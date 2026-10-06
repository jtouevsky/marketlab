"""
tab_news.py — turns raw articles from many providers into NEWS EVENTS.

    providers (sources/news.py)          SEC, company newsroom, government, Bing, Google,
                                         GDELT, Nasdaq, publications, Yahoo, optional APIs
        ↓ NORMALIZE      one article shape, UTC times, original URLs where available
        ↓ FILTER         last 30 days; must actually be about the company; no future dates
        ↓ DEDUPLICATE    same URL / same headline = one article
        ↓ CLUSTER        articles about the same development (similar headlines within
                         72 hours) become ONE event with all its coverage
        ↓ CATEGORIZE     keyword rules; SEC item codes for filings (exact, not guessed)
        ↓ RANK           relevance & materiality, with the reasons recorded
        ↓ EVENTS         the News tab, the timeline, chart markers (later) and the Lab (later)

NewsEvent (what the rest of MarketLab consumes):
    {"id", "ticker", "headline", "summary", "category", "categories", "published",
     "bucket": today|week|month, "sources": [{name, type, url, published, title}],
     "source_count", "primary": {name, url, type} | null, "relevance", "reasons",
     "government": {kind, mentions_trump} | null, "earnings_related", "material", "retrieved_at"}

Everything is deterministic (no AI). Events are also kept in a small local
store (cache/news_events/<TICKER>.json) so historical lookups become possible
later (e.g. "what was happening around this Lab episode?").
"""

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone

import config
from data import now_iso
from sources import news, yahoo

STORE_DIR = config.CACHE_DIR / "news_events"
WINDOW_DAYS = 30
CLUSTER_HOURS = 72
CLUSTER_SIMILARITY = 0.5      # IDF-weighted share of the shorter headline's words

# ---------------------------------------------------------------------------
# Categories (first match wins for the primary category; all matches kept)
# ---------------------------------------------------------------------------
OWNERSHIP = re.compile(r"((acquires|buys|sells|purchases|sold|bought|acquired|trims|adds) [\d,.]+ shares|shares (acquired|sold|bought|purchased) by|"
                       r"(cuts|raises|boosts|lowers|trims|increases|decreases|reduces|grows|lifts) (its )?(stake|position|holdings)|position in|holdings in|stake in|"
                       r"\b13f\b|institutional (investor|ownership))", re.I)
LISTICLE = re.compile(r"(\b\d+ (stocks|reasons|things)\b|stocks? to (buy|watch|own)|should you buy|buy (now|today)|"
                      r"is it (a buy|too late)|better buy|top picks|millionaire|trending stock|facts to know|what you should know|"
                      r"(dips|rises|falls) more than broader market)", re.I)

CATEGORY_RULES = [
    ("Earnings", r"\b(earnings|quarterly results|results|revenue|eps|profit|guidance|beats?|miss(es|ed)?|outlook|q[1-4]|fiscal (year|quarter))\b"),
    ("M&A", r"\b(acquires|acquisition|merger|merge|buyout|takeover|deal to buy|to acquire|divest(s|iture)?|spin[- ]?off)\b"),
    ("Legal", r"\b(lawsuit|sued|sues|class action|settle(ment|s)?|verdict|jury|litigation|patent (suit|infringement))\b"),
    ("Regulatory", r"\b(regulator|antitrust|ftc|doj|sec (charges|probe|investigation)|probe|investigation|fine[ds]?|fda|fcc)\b"),
    ("Government & Policy", r"\b(tariffs?|export (controls?|restrictions?|ban)|white house|administration|executive order|congress|senate|lawmakers|"
                            r"subsid(y|ies)|chips act|sanctions?|government|pentagon|department of (defense|commerce|energy|war)|federal|trump|commerce department|darpa)\b"),
    ("Contracts", r"\b(contract|awarded|wins? (deal|order)|order from|purchase order)\b"),
    ("Cybersecurity", r"\b(cyber(security|attack)?|breach|hack(ed|ers)?|ransomware|data leak)\b"),
    ("Management", r"\b(ceo|cfo|coo|chief executive|resign(s|ed|ation)?|appoint(s|ed|ment)?|steps down|board of directors)\b"),
    ("Partnerships", r"\b(partner(s|ship)?|collaborat(e|es|ion)|teams up|alliance|joint venture|agreement with|signs (agreement|deal))\b"),
    ("Customers", r"\b(customer|selects|deploys|adopts|chosen by)\b"),
    ("Products", r"\b(launch(es|ed)?|unveil(s|ed)?|introduc(es|ed)|new (chip|product|model|service|system|processor|platform)|announces? new)\b"),
    ("Analyst Activity", r"\b(upgrade[sd]?|downgrade[sd]?|price target|analysts?|overweight|underweight|outperform|initiat(es|ed) coverage)\b"),
    ("Operations", r"\b(layoffs?|job cuts|restructuring|plant|factory|facility|production|supply chain|recall|capacity|offering|raises \$)\b"),
    ("Competition", r"\b(rival|competitor|competition|versus|vs\.|market share)\b"),
    ("Macroeconomic", r"\b(fed|interest rates?|inflation|recession|jobs report|treasury yields?|cpi|gdp)\b"),
    ("Industry", r"\b(industry|sector|chipmakers|quantum stocks|semiconductor stocks|peers)\b"),
]
SEC_ITEM_CATEGORY = {"2.02": "Earnings", "1.01": "Contracts", "1.02": "Contracts", "2.01": "M&A", "5.02": "Management",
                     "1.05": "Cybersecurity", "2.05": "Operations", "2.06": "Operations", "4.02": "Regulatory",
                     "3.01": "Regulatory", "1.03": "Operations", "3.02": "Operations", "2.03": "Operations",
                     "5.07": "Management", "8.01": "Other", "7.01": "Other"}
WEIGHT = {"Earnings": 3, "M&A": 3, "Legal": 2, "Regulatory": 2, "Government & Policy": 2, "Contracts": 2,
          "Cybersecurity": 2, "Management": 2, "Partnerships": 2, "Customers": 1.5, "Products": 1.5, "Operations": 1.5,
          "Analyst Activity": 1, "Competition": 0.5, "Industry": 0, "Macroeconomic": 0, "Other": 0, "Ownership filings": -4}

# Government wording -> what kind of development it is (proposals are not enacted policy)
GOV_KINDS = [
    ("Court ruling", r"\b(court rules?|ruled|judge (rules|blocks|orders)|ruling|verdict|appeals court|supreme court)\b"),
    ("Legislation passed", r"\b(passes|passed|signs into law|signed into law|enacted)\b"),
    ("Investigation", r"\b(probe|investigat(es|ion|ing)|subpoena|inquiry)\b"),
    ("Agency action", r"\b(fined|fines|imposes|imposed|blocks|blocked|approves|approved|awards|awarded|ordered|charged|sues|sued|bans|banned|revokes)\b"),
    ("Award or contract", r"\b(wins|won|secures|secured|finaliz(es|ed)|receives|received)\b.*\b(award|funding|grant|contract|incentive)s?\b"),
    ("Implemented policy", r"\b(takes effect|went into effect|now in effect|begins? enforcing|implemented)\b"),
    ("Proposal", r"\b(propos(e|es|ed|al)|consider(s|ing)?|weigh(s|ing)?|plans?|planning|threaten(s|ed)?|bill would|draft|could|may)\b"),
    ("Statement", r"\b(says|said|warns|calls for|urges|comments?|criticiz(es|ed)|praises)\b"),
]
MAJOR_OUTLETS = {"reuters.com", "apnews.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com", "nytimes.com",
                 "barrons.com", "marketwatch.com", "economist.com", "washingtonpost.com", "theinformation.com",
                 "axios.com", "fortune.com", "forbes.com", "businessinsider.com", "theverge.com", "techcrunch.com"}
SOURCE_RANK = {"primary_company": 0, "company": 0, "primary_regulatory": 1, "primary_government": 1, "reporting": 2, "aggregator": 3}
STOP = set("a an the of to in on for and or with by at as is are be its it from after over says say said new this that "
           "stock stocks shares share inc corp co ltd will has have just what why how here".split())


def categorise(article):
    if article["provider"] == "sec":
        cats = [SEC_ITEM_CATEGORY[i] for i in article["extra"].get("sec_items", []) if i in SEC_ITEM_CATEGORY]
        cats = [c for c in cats if c != "Other"] or ["Other"]
        return list(dict.fromkeys(cats))
    if article["source_type"] == "primary_government":
        return ["Government & Policy"]
    title = article["title"]
    if OWNERSHIP.search(title):
        return ["Ownership filings"]
    # Headline first: snippets often mention earnings or Washington in passing.
    found = [label for label, pattern in CATEGORY_RULES if re.search(pattern, title, re.I)]
    if not found and article.get("summary"):
        found = [label for label, pattern in CATEGORY_RULES
                 if label not in ("Government & Policy", "Industry", "Competition", "Earnings")
                 and re.search(pattern, article["summary"], re.I)]
    return found or ["Other"]


def gov_kind(text, article):
    if article["extra"].get("gov_kind"):
        return article["extra"]["gov_kind"]
    for kind, pattern in GOV_KINDS:
        if re.search(pattern, text, re.I):
            return kind
    return "Report"


def tokens(title, company):
    words = re.findall(r"[a-z0-9]+", title.lower())
    drop = set(" ".join(news.name_variants(company)).split()) | {company["ticker"].lower()}
    return {w for w in words if w not in STOP and w not in drop and len(w) > 2}


def _canonical(url):
    return re.sub(r"[?#].*$", "", (url or "").lower()).rstrip("/")


# ---------------------------------------------------------------------------
def company_for(ticker):
    info = yahoo.get_info(ticker)
    name = info.get("longName") or info.get("shortName") or ticker
    short = re.sub(r",?\s+(Inc\.?|Corporation|Corp\.?|Ltd\.?|plc|Holdings|Co\.)$", "", info.get("shortName") or name).strip()
    return {"ticker": ticker, "name": re.sub(r",?\s+(Inc\.?|Corporation|Corp\.?|Ltd\.?|plc|N\.V\.|S\.A\.)$", "", name).strip(),
            "full_name": name, "short_name": short, "website": info.get("website")}


def build_news(ticker, refresh=False):
    company = company_for(ticker)
    raw, status = news.fetch_all(company, refresh=refresh)
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=WINDOW_DAYS)

    # 1. FILTER: dated, recent, not from the future, actually about the company
    seen_urls, seen_titles, articles = set(), set(), []
    dropped = {"undated": 0, "old_or_future": 0, "not_about_company": 0, "duplicate": 0}
    for a in raw:
        if not a["published"]:
            dropped["undated"] += 1
            continue
        published = datetime.fromisoformat(a["published"])
        if published < oldest or published > now + timedelta(hours=3):
            dropped["old_or_future"] += 1
            continue
        in_title = news.mentions(a["title"], company)
        in_text = in_title or news.mentions(a.get("summary") or "", company)
        primary_own = a["provider"] in ("sec", "company_newsroom")    # the company's own record: always about it
        if not (in_text or primary_own or (a["ticker_specific"] and a["provider"] in ("finnhub", "polygon", "alphavantage"))):
            dropped["not_about_company"] += 1
            continue
        url_key, title_key = _canonical(a["url"]), re.sub(r"\W+", " ", a["title"].lower()).strip()
        if url_key in seen_urls or (title_key, a["source_name"].lower()) in seen_titles:
            dropped["duplicate"] += 1
            continue
        seen_urls.add(url_key)
        seen_titles.add((title_key, a["source_name"].lower()))
        articles.append({**a, "_time": published, "_tokens": tokens(a["title"], company), "_in_title": in_title, "_own": primary_own,
                         "_cats": categorise(a)})

    # 2. CLUSTER: similar headlines within 72 hours = one development.
    #    Words are weighted by rarity across today's articles (IDF), so "$100 million CHIPS award"
    #    links stories while common words like "quantum" or "stock" barely count.
    articles.sort(key=lambda a: a["_time"])
    df = {}
    for a in articles:
        for w in a["_tokens"]:
            df[w] = df.get(w, 0) + 1
    idf = {w: math.log((1 + len(articles)) / (1 + c)) + 1 for w, c in df.items()}

    def similarity(x, y):
        shared = x["_tokens"] & y["_tokens"]
        if len(shared) < 2:
            return 0.0
        smaller = min(sum(idf[w] for w in x["_tokens"]), sum(idf[w] for w in y["_tokens"]))
        return sum(idf[w] for w in shared) / smaller if smaller else 0.0

    clusters = []
    for a in articles:
        best, best_sim = None, 0.0
        for cluster in clusters[-250:]:
            if a["provider"] == "sec" or cluster[0]["provider"] == "sec":     # a filing is its own record
                continue
            if abs((a["_time"] - cluster[0]["_time"]).total_seconds()) > CLUSTER_HOURS * 3600:
                continue
            if min(len(a["_tokens"]), len(cluster[0]["_tokens"])) < 3:
                continue
            sim = max(similarity(a, member) for member in cluster[:6])
            if sim > best_sim:
                best, best_sim = cluster, sim
        if best is not None and best_sim >= CLUSTER_SIMILARITY:
            best.append(a)
        else:
            clusters.append([a])

    # 2b. MERGE PASS: coverage of one development often uses different wording
    #     ("DOJ probes Nvidia-Groq deal" / "Regulators investigating Nvidia's Groq licensing deal").
    #     Merge clusters whose combined vocabularies overlap strongly on rare words.
    def vocab(cluster):
        words = {}
        for member in cluster:
            for w in member["_tokens"]:
                words[w] = words.get(w, 0) + 1
        return {w for w, c in words.items() if c >= max(1, len(cluster) // 3)}

    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                a, b = clusters[i], clusters[j]
                if "sec" in (a[0]["provider"], b[0]["provider"]):
                    continue
                if abs((a[0]["_time"] - b[0]["_time"]).total_seconds()) > CLUSTER_HOURS * 3600:
                    continue
                va, vb = vocab(a), vocab(b)
                shared = va & vb
                rare = [w for w in shared if idf[w] >= 2.5]
                if len(shared) < 3 or len(rare) < 2:
                    continue
                smaller = min(sum(idf[w] for w in va), sum(idf[w] for w in vb))
                if smaller and sum(idf[w] for w in shared) / smaller >= 0.45:
                    clusters[i] = a + b
                    del clusters[j]
                    merged = True
                    break
            if merged:
                break

    # 3. EVENTS
    events = [make_event(cluster, company, now) for cluster in clusters]
    events.sort(key=lambda e: e["published"], reverse=True)

    def top(days):
        window = [e for e in events if (now - datetime.fromisoformat(e["published"])).total_seconds() <= days * 86400]
        return sorted(window, key=lambda e: (e["relevance"], e["published"]), reverse=True)

    store_events(ticker, events)
    categories = {}
    for e in events:
        for c in e["categories"]:
            categories[c] = categories.get(c, 0) + 1
    publishers = sorted({s["name"] for e in events for s in e["sources"]})
    return {
        "ticker": ticker, "name": company["full_name"], "retrieved_at": now_iso(),
        "events": events,
        "top": {"today": [e["id"] for e in top(1)[:5]], "week": [e["id"] for e in top(7)[:8]],
                "month": [e["id"] for e in top(30)[:10]]},
        "timeline": [e["id"] for e in events if e["material"]],
        "policy": [e["id"] for e in events if e["government"]],
        "chart_events": [{"id": e["id"], "date": e["published"][:10], "category": e["category"], "headline": e["headline"]}
                         for e in events if e["material"] and e["relevance"] >= 6],
        "counts": {b: sum(1 for e in events if e["bucket"] == b) for b in ("today", "week", "month")},
        "categories": categories,
        "providers": status,
        "stats": {"raw": len(raw), "articles": len(articles), "events": len(events), "dropped": dropped,
                  "publishers": len(publishers)},
        "method": ("Collected from every connected source, filtered to articles naming the company in the last 30 days, "
                   "duplicates removed, similar headlines within 72 hours grouped into one event, categories assigned by "
                   "keyword rules (SEC filings by their item codes), and ranked by relevance and materiality."),
    }


def make_event(cluster, company, now):
    def rank(a):
        major = 0 if (a["source_domain"] or "") in MAJOR_OUTLETS else 1
        return (SOURCE_RANK.get(a["source_type"], 3), major, a["_time"])
    ordered = sorted(cluster, key=rank)
    lead = ordered[0]
    first_time = min(a["_time"] for a in cluster)
    cats = list(dict.fromkeys(c for a in ordered for c in a["_cats"]))
    if len(cats) > 1 and "Other" in cats:
        cats.remove("Other")
    primary_cat = lead["_cats"][0] if lead["_cats"] and lead["_cats"][0] != "Other" else cats[0]
    primary = next((a for a in ordered if a["source_type"] in ("primary_company", "company", "primary_regulatory", "primary_government")), None)
    text = " ".join(f"{a['title']} {a.get('summary') or ''}" for a in cluster)
    is_gov = "Government & Policy" in cats or any(a["source_type"] == "primary_government" for a in cluster)
    publishers = {a["source_name"].lower() for a in cluster}

    # Relevance: every point is recorded with its reason
    reasons, score = [], 0.0
    if any(a["_in_title"] for a in cluster):
        score += 4
        reasons.append("names the company in the headline (+4)")
    elif any(a["_own"] for a in cluster):
        score += 2
        reasons.append("the company's own announcement or filing (+2)")
    else:
        score -= 2
        reasons.append("mentions the company only in passing (−2)")
    if primary:
        score += 3
        reasons.append(f"primary source: {primary['source_name']} (+3)")
    weights = [WEIGHT.get(c, 0) for c in cats]
    weight = min(weights) if min(weights, default=0) < 0 else max(weights, default=0)
    score += weight
    if weight:
        reasons.append(f"{primary_cat.lower()} ({weight:+g})")
    if len(publishers) > 1:
        bonus = min(math.log2(len(publishers)) * 1.5, 4)
        score += bonus
        reasons.append(f"covered by {len(publishers)} publishers (+{bonus:.1f})")
    if any((a["source_domain"] or "") in MAJOR_OUTLETS for a in cluster):
        score += 1
        reasons.append("major-outlet coverage (+1)")
    if LISTICLE.search(lead["title"]):
        score -= 2
        reasons.append("opinion / listicle headline (−2)")
    age_h = (now - first_time).total_seconds() / 3600
    score += 1 if age_h <= 24 else 0.5 if age_h <= 24 * 7 else 0

    sources = [{"name": a["source_name"], "type": a["source_type"], "url": a["url"], "title": a["title"],
                "published": a["_time"].isoformat(timespec="seconds"), "provider": a["provider"],
                "via": a["extra"].get("via"), "paywall_possible": bool(a["extra"].get("paywall_possible"))} for a in ordered]
    event_id = hashlib.sha1(f"{company['ticker']}|{_canonical(lead['url'])}".encode()).hexdigest()[:12]
    summary = next((a["summary"] for a in ordered if a.get("summary")), None)
    return {
        "id": event_id, "ticker": company["ticker"], "headline": lead["title"], "summary": summary,
        "category": primary_cat, "categories": cats,
        "published": first_time.isoformat(timespec="seconds"),
        "bucket": "today" if age_h <= 24 else "week" if age_h <= 24 * 7 else "month",
        "sources": sources, "source_count": len(publishers),
        "primary": {"name": primary["source_name"], "url": primary["url"], "type": primary["source_type"]} if primary else None,
        "relevance": round(score, 1), "reasons": reasons,
        "government": {"kind": gov_kind(text, lead), "mentions_trump": bool(re.search(r"\btrump\b", text, re.I))} if is_gov else None,
        "earnings_related": "Earnings" in cats,
        "material": score >= 5 and "Ownership filings" not in cats,
        "retrieved_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# Local event store (foundation for historical lookups)
# ---------------------------------------------------------------------------
def store_events(ticker, events):
    """Keep every material event seen for this ticker (by id), so history accumulates across visits."""
    path = STORE_DIR / f"{ticker}.json"
    try:
        stored = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        stored = {}
    for e in events:
        if e["material"]:
            stored[e["id"]] = {**{k: e[k] for k in ("id", "headline", "category", "categories", "published", "relevance",
                                                    "primary", "government", "earnings_related")},
                               "sources": [{"name": s["name"], "url": s["url"]} for s in e["sources"][:5]]}
    try:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stored))
    except OSError:
        pass


def events_between(ticker, start, end):
    """Stored events for ticker with start <= date <= end (ISO dates). For the Lab's episode inspector later."""
    path = STORE_DIR / f"{ticker}.json"
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return sorted((e for e in stored.values() if start <= e["published"][:10] <= end), key=lambda e: e["published"])
