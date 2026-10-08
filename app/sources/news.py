"""
sources/news.py — the PROVIDER layer of MarketLab News.

Every provider returns a list of RawArticle dicts in ONE shape, so the rest
of MarketLab never depends on any provider's format:

    {"title", "summary", "url", "source_name", "source_domain",
     "source_type",      # primary_company | primary_regulatory | primary_government | company | reporting | aggregator
     "provider",         # which provider found it (id below)
     "published",        # ISO 8601, UTC
     "ticker_specific",  # True if the provider returns items for this ticker only
     "extra": {...}}     # provider-specific facts (e.g. SEC item codes, government document type)

Provider registry (PROVIDERS): each entry has id, label, kind, needs, terms
and a fetch(company) function. company = {"ticker", "name", "short_name",
"website"}. Providers run in parallel; a failing provider never breaks
News (its status is recorded and shown in "Sources").

What we never do: scrape paywalled sites, bypass access controls, or show
article text. We keep headline, a short provider-supplied snippet, source,
time and the link to the original publisher.
"""

import email.utils
import html
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone

import requests

import config
from sources import sec, yahoo

HEADERS = {"User-Agent": "Mozilla/5.0 (MarketLab personal research app)"}
TIMEOUT = 6
CACHE_SECONDS = 15 * 60
FAIL_CACHE_SECONDS = 3 * 60
_cache = {}                 # (provider id, ticker) -> (time, articles or error text)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip() or None


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    try:
        return iso(email.utils.parsedate_to_datetime(text))
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M"):
        try:
            return iso(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return iso(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
    except ValueError:
        return None


def domain(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return None


def article(title, url, source_name, source_type, provider, published, summary=None, ticker_specific=False, **extra):
    title = clean(title)
    if not title or not url:
        return None
    summary = clean(summary)
    if summary and (summary.lower().startswith(title.lower()[:40]) or len(summary) < 30):
        summary = None
    return {"title": title, "summary": summary[:400] if summary else None, "url": url.strip(),
            "source_name": (source_name or "").strip() or domain(url) or "Unknown", "source_domain": domain(url),
            "source_type": source_type, "provider": provider, "published": published,
            "ticker_specific": ticker_specific, "extra": extra}


def get(url, **kwargs):
    response = requests.get(url, headers=kwargs.pop("headers", HEADERS), timeout=kwargs.pop("timeout", TIMEOUT), **kwargs)
    response.raise_for_status()
    return response


ATOM = "{http://www.w3.org/2005/Atom}"


def rss_items(url, **kwargs):
    root = ET.fromstring(get(url, **kwargs).content)
    yield from root.iter("item")
    yield from root.iter(f"{ATOM}entry")         # Atom feeds


def text_of(item, *names):
    for name in names:
        value = item.findtext(name)
        if value:
            return value
    return None


def link_of(item):
    link = item.findtext("link")
    if link:
        return link
    atom = item.find(f"{ATOM}link")
    return atom.get("href") if atom is not None else None


# ---------------------------------------------------------------------------
# PROVIDERS
# ---------------------------------------------------------------------------
def p_sec(company):
    """Company-filed current reports: the legal record of material events (primary source)."""
    if not sec.is_configured():
        raise RuntimeError("SEC is off (add SEC_USER_AGENT to .env)")
    out = []
    for f in sec.recent_current_reports(company["ticker"]):
        names = f["item_names"] or ["Current report"]
        published = parse_date(f["accepted"]) or parse_date(f["filed"])
        out.append(article(f"{company['short_name']} files {f['form']}: {'; '.join(names)}", f["url"],
                           "SEC EDGAR", "primary_regulatory", "sec", published,
                           summary=f"Form {f['form']} filed {f['filed']} by {f['company']}. Item codes: {', '.join(f['items']) or 'none listed'}.",
                           ticker_specific=True, sec_items=f["items"]))
    return out


def p_yahoo(company):
    out = []
    for raw in yahoo.get_ticker(company["ticker"]).news or []:
        c = raw.get("content") or raw
        url = ((c.get("clickThroughUrl") or {}).get("url") or (c.get("canonicalUrl") or {}).get("url"))
        out.append(article(c.get("title"), url, (c.get("provider") or {}).get("displayName") or "Yahoo Finance",
                           "aggregator" if (domain(url or "") or "").endswith("yahoo.com") else "reporting", "yahoo",
                           parse_date(c.get("pubDate")), c.get("summary") or c.get("description"), ticker_specific=True))
    return out


def p_yahoo_rss(company):
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={urllib.parse.quote(company['ticker'])}&region=US&lang=en-US"
    return [article(text_of(i, "title"), link_of(i), "Yahoo Finance", "aggregator", "yahoo_rss",
                    parse_date(text_of(i, "pubDate")), text_of(i, "description"), ticker_specific=True)
            for i in rss_items(url)]


def _google(query, provider):
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    out = []
    for i in rss_items(url):
        source_el = i.find("source")
        source = clean(source_el.text) if source_el is not None else None
        title = text_of(i, "title") or ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        a = article(title, link_of(i), source, "reporting", provider, parse_date(text_of(i, "pubDate")))
        if a and source_el is not None and source_el.get("url"):
            a["source_domain"] = domain(source_el.get("url"))     # the publisher, not news.google.com
            a["extra"]["via"] = "Google News link (redirects to the publisher)"
        out.append(a)
    return out


POLICY_TERMS = ('tariff OR "export controls" OR regulators OR antitrust OR "executive order" OR "White House" OR '
                'Congress OR Senate OR FTC OR DOJ OR subsidy OR "government contract" OR Pentagon OR lawsuit OR court OR probe')


def p_google(company):
    return _google(f'"{company["name"]}" when:30d', "google")


def p_google_policy(company):
    return _google(f'"{company["name"]}" ({POLICY_TERMS}) when:30d', "google_policy")


def p_bing(company):
    """Bing News RSS. Its links carry the ORIGINAL publisher URL, which we extract."""
    url = f"https://www.bing.com/news/search?q={urllib.parse.quote(chr(34) + company['name'] + chr(34))}&format=rss&count=50"
    out = []
    for i in rss_items(url):
        link = link_of(i) or ""
        original = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("url", [None])[0] or link
        source = next((clean(child.text) for child in i if child.tag.endswith("Source")), None)
        out.append(article(text_of(i, "title"), original, source, "reporting", "bing",
                           parse_date(text_of(i, "pubDate")), text_of(i, "description")))
    return out


def p_nasdaq(company):
    url = f"https://www.nasdaq.com/feed/rssoutbound?symbol={urllib.parse.quote(company['ticker'])}"
    out = []
    for i in rss_items(url):
        creator = text_of(i, "{http://purl.org/dc/elements/1.1/}creator")
        out.append(article(text_of(i, "title"), link_of(i), creator or "Nasdaq", "reporting", "nasdaq",
                           parse_date(text_of(i, "pubDate")), text_of(i, "description"), ticker_specific=True))
    return out


def p_seeking_alpha(company):
    """Per-ticker headlines (personal, non-commercial use per Seeking Alpha's feed terms). Many articles are paywalled."""
    url = f"https://seekingalpha.com/api/sa/combined/{urllib.parse.quote(company['ticker'])}.xml"
    return [article(text_of(i, "title"), link_of(i), "Seeking Alpha", "reporting", "seeking_alpha",
                    parse_date(text_of(i, "pubDate")), None, ticker_specific=True, paywall_possible=True)
            for i in rss_items(url)]


def p_gdelt(company):
    """GDELT: worldwide news index with original publisher URLs (free; max one request per 5 seconds)."""
    q = urllib.parse.quote(f'"{company["name"]}" sourcelang:english')
    data = get(f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&format=json&maxrecords=60&timespan=30d&sort=datedesc").json()
    return [article(a.get("title"), a.get("url"), a.get("domain"), "reporting", "gdelt", parse_date(a.get("seendate")))
            for a in data.get("articles", [])]


def p_company_newsroom(company):
    """The company's own newsroom / investor-relations RSS feed, when one can be found (company source)."""
    feed = discover_company_feed(company)
    if not feed:
        raise RuntimeError("No public newsroom feed found for this company")
    return [article(text_of(i, "title", f"{ATOM}title"), link_of(i), f"{company['short_name']} newsroom", "company",
                    "company_newsroom", parse_date(text_of(i, "pubDate", f"{ATOM}updated", f"{ATOM}published")),
                    text_of(i, "description", f"{ATOM}summary"), ticker_specific=True, feed=feed)
            for i in rss_items(feed)]


def p_globenewswire(company):
    """Press releases distributed through GlobeNewswire (company-issued; many small caps use it)."""
    url = f"https://www.globenewswire.com/RssFeed/keyword/{urllib.parse.quote(company['name'])}"
    return [article(text_of(i, "title"), link_of(i), "GlobeNewswire (press release)", "company",
                    "globenewswire", parse_date(text_of(i, "pubDate")), text_of(i, "description"))
            for i in rss_items(url)]


FR_TYPES = {"Presidential Document": "Executive action", "Rule": "Agency action (final rule)",
            "Proposed Rule": "Proposal (proposed rule)", "Notice": "Agency notice"}


def p_federal_register(company):
    """Official U.S. government documents (rules, proposed rules, notices, presidential documents) naming the company."""
    since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    params = {"conditions[term]": f'"{company["name"]}"', "conditions[publication_date][gte]": since,
              "order": "newest", "per_page": 20,
              "fields[]": ["title", "abstract", "html_url", "publication_date", "type", "agencies"]}
    data = get("https://www.federalregister.gov/api/v1/documents.json", params=params).json()
    out = []
    for d in data.get("results", []):
        agencies = ", ".join(a.get("name") or a.get("raw_name", "") for a in d.get("agencies") or []) or "Federal Register"
        out.append(article(d.get("title"), d.get("html_url"), agencies, "primary_government", "federal_register",
                           parse_date(d.get("publication_date")), d.get("abstract"),
                           gov_kind=FR_TYPES.get(d.get("type"), d.get("type"))))
    return out


AGENCY_FEEDS = [("Federal Trade Commission", "https://www.ftc.gov/feeds/press-release.xml"),
                ("U.S. Department of Justice", "https://www.justice.gov/news/rss?type=press_release")]


def p_agencies(company):
    """Agency press releases that name the company (FTC, DOJ)."""
    out, errors = [], 0
    for name, url in AGENCY_FEEDS:
        try:
            for i in rss_items(url):
                text = f"{text_of(i, 'title') or ''} {text_of(i, 'description') or ''}"
                if mentions(text, company):
                    out.append(article(text_of(i, "title"), link_of(i), name, "primary_government", "agencies",
                                       parse_date(text_of(i, "pubDate")), text_of(i, "description"), gov_kind="Agency press release"))
        except Exception:
            errors += 1
    if errors == len(AGENCY_FEEDS):
        raise RuntimeError("Agency feeds unreachable")
    return out


PUBLICATION_FEEDS = [("The Wall Street Journal", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
                     ("The Wall Street Journal", "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
                     ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
                     ("Investing.com", "https://www.investing.com/rss/news_25.rss")]


def p_publications(company):
    """Major publications' public RSS feeds (headlines only), kept when they name the company."""
    out, errors = [], 0
    for name, url in PUBLICATION_FEEDS:
        try:
            for i in rss_items(url):
                text = f"{text_of(i, 'title') or ''} {text_of(i, 'description') or ''}"
                if mentions(text, company):
                    out.append(article(text_of(i, "title"), link_of(i), name, "reporting", "publications",
                                       parse_date(text_of(i, "pubDate")), text_of(i, "description")))
        except Exception:
            errors += 1
    if errors == len(PUBLICATION_FEEDS):
        raise RuntimeError("Publication feeds unreachable")
    return out


def p_finnhub(company):
    today = datetime.now(timezone.utc).date()
    data = get("https://finnhub.io/api/v1/company-news", params={
        "symbol": company["ticker"], "from": (today - timedelta(days=30)).isoformat(), "to": today.isoformat(),
        "token": config.FINNHUB_API_KEY}).json()
    return [article(a.get("headline"), a.get("url"), a.get("source"), "reporting", "finnhub",
                    iso(datetime.fromtimestamp(a["datetime"], timezone.utc)) if a.get("datetime") else None,
                    a.get("summary"), ticker_specific=True) for a in data[:100]]


def p_alphavantage(company):
    data = get("https://www.alphavantage.co/query", params={"function": "NEWS_SENTIMENT", "tickers": company["ticker"],
                                                           "limit": 50, "apikey": config.ALPHAVANTAGE_API_KEY}).json()
    if "feed" not in data:
        raise RuntimeError(data.get("Information") or data.get("Note") or "No feed returned")
    out = []
    for a in data["feed"]:
        rel = next((t for t in a.get("ticker_sentiment", []) if t.get("ticker") == company["ticker"]), {})
        out.append(article(a.get("title"), a.get("url"), a.get("source"), "reporting", "alphavantage",
                           parse_date(a.get("time_published")), a.get("summary"), ticker_specific=True,
                           provider_relevance=float(rel.get("relevance_score") or 0)))
    return out


def p_polygon(company):
    data = get("https://api.polygon.io/v2/reference/news", params={"ticker": company["ticker"], "limit": 50,
                                                                   "apiKey": config.POLYGON_API_KEY}).json()
    return [article(a.get("title"), a.get("article_url"), (a.get("publisher") or {}).get("name"), "reporting", "polygon",
                    parse_date(a.get("published_utc")), a.get("description"), ticker_specific=True)
            for a in data.get("results", [])]


# --- Articles found by Ask MarketLab's web search -------------------------------------------------------------
# Dated, non-social results are kept here (cache/web_articles/<TICKER>.json, 30 days) and read back as a normal
# provider, so they go through the same filter / dedupe / cluster / rank pipeline as every other article and show up
# in News, Risk, Charts, Ask MarketLab and Invest like any other event. No separate silo.
WEB_STORE = config.CACHE_DIR / "web_articles"
WEB_KEEP_DAYS = 30


def save_web_articles(ticker, articles):
    path = WEB_STORE / f"{ticker}.json"
    try:
        kept = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        kept = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WEB_KEEP_DAYS)).isoformat()
    for a in articles:
        if a.get("published") and a.get("url"):
            kept[a["url"]] = a
    kept = {u: a for u, a in kept.items() if (a.get("published") or "") >= cutoff}
    try:
        WEB_STORE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(kept))
    except OSError:
        return 0
    _cache.pop(("web_search", ticker), None)           # the News pipeline sees the new articles immediately
    return len(articles)


def p_web_search(company):
    path = WEB_STORE / f"{company['ticker']}.json"
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [article(a["title"], a["url"], a.get("source_name"), a.get("source_type", "reporting"), "web_search",
                    a.get("published"), a.get("summary"), via="Ask MarketLab web search")
            for a in stored.values()]


PROVIDERS = [
    {"id": "sec", "label": "SEC EDGAR (8-K / 6-K filings)", "kind": "Primary · regulatory filings", "fetch": p_sec,
     "needs": "SEC_USER_AGENT", "terms": "Public U.S. government data."},
    {"id": "company_newsroom", "label": "Company newsroom", "kind": "Company source", "fetch": p_company_newsroom,
     "needs": None, "terms": "The company's own public RSS feed, when one can be found."},
    {"id": "globenewswire", "label": "GlobeNewswire press releases", "kind": "Company source", "fetch": p_globenewswire,
     "needs": None, "terms": "Public RSS."},
    {"id": "federal_register", "label": "Federal Register", "kind": "Primary · government", "fetch": p_federal_register,
     "needs": None, "terms": "Official U.S. government API."},
    {"id": "agencies", "label": "FTC and DOJ press releases", "kind": "Primary · government", "fetch": p_agencies,
     "needs": None, "terms": "Public agency RSS."},
    {"id": "bing", "label": "Bing News", "kind": "News index · original links", "fetch": p_bing, "needs": None,
     "terms": "Public RSS search, personal use."},
    {"id": "google", "label": "Google News", "kind": "News index", "fetch": p_google, "needs": None,
     "terms": "Public RSS search; links go through Google to the publisher."},
    {"id": "google_policy", "label": "Google News (policy search)", "kind": "News index", "fetch": p_google_policy,
     "needs": None, "terms": "Public RSS search."},
    {"id": "gdelt", "label": "GDELT news index", "kind": "News index · original links", "fetch": p_gdelt, "needs": None,
     "terms": "Free; limited to one request every 5 seconds."},
    {"id": "nasdaq", "label": "Nasdaq", "kind": "Financial news", "fetch": p_nasdaq, "needs": None, "terms": "Public RSS."},
    {"id": "publications", "label": "WSJ, MarketWatch, Investing.com feeds", "kind": "Major publications",
     "fetch": p_publications, "needs": None, "terms": "Public RSS headlines; articles may be paywalled."},
    {"id": "seeking_alpha", "label": "Seeking Alpha", "kind": "Financial analysis", "fetch": p_seeking_alpha,
     "needs": None, "terms": "Public RSS for personal, non-commercial use; many articles are paywalled."},
    {"id": "web_search", "label": "Web search (found by Ask MarketLab)", "kind": "Official web search · original links",
     "fetch": p_web_search, "needs": None, "terms": "Only articles Ask MarketLab found while answering a current-events question."},
    {"id": "yahoo", "label": "Yahoo Finance", "kind": "Aggregator", "fetch": p_yahoo, "needs": None, "terms": "Via yfinance."},
    {"id": "yahoo_rss", "label": "Yahoo Finance RSS", "kind": "Aggregator", "fetch": p_yahoo_rss, "needs": None,
     "terms": "Public RSS."},
    {"id": "finnhub", "label": "Finnhub", "kind": "News API", "fetch": p_finnhub, "needs": "FINNHUB_API_KEY",
     "terms": "Free key: 60 requests/minute."},
    {"id": "alphavantage", "label": "Alpha Vantage News", "kind": "News API", "fetch": p_alphavantage,
     "needs": "ALPHAVANTAGE_API_KEY", "terms": "Free key: 25 requests/day."},
    {"id": "polygon", "label": "Polygon.io News", "kind": "News API", "fetch": p_polygon, "needs": "POLYGON_API_KEY",
     "terms": "Free key: 5 requests/minute."},
]


def configured(provider):
    need = provider["needs"]
    return True if not need else bool(getattr(config, need, ""))


# ---------------------------------------------------------------------------
# Company feed discovery (cached 7 days on disk)
# ---------------------------------------------------------------------------
_FEED_FILE = config.CACHE_DIR / "company_feeds.json"
KNOWN_FEEDS = {   # verified public newsroom feeds; discovery handles everything else
    "AAPL": "https://www.apple.com/newsroom/rss-feed.rss",
    "NVDA": "https://nvidianews.nvidia.com/releases.xml",
}


def discover_company_feed(company):
    ticker = company["ticker"]
    if ticker in KNOWN_FEEDS:
        return KNOWN_FEEDS[ticker]
    try:
        store = json.loads(_FEED_FILE.read_text())
    except (OSError, ValueError):
        store = {}
    hit = store.get(ticker)
    if hit and time.time() - hit["checked"] < 7 * 86400:
        return hit["feed"]
    feed = None
    website = (company.get("website") or "").rstrip("/")
    if website:
        base = domain(website) or ""
        candidates = []
        try:   # 1) feeds the homepage advertises
            page = get(website, timeout=6).text[:300000]
            for m in re.finditer(r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*>', page, re.I):
                href = re.search(r'href="([^"]+)"', m.group(0))
                if href:
                    candidates.append(urllib.parse.urljoin(website + "/", href.group(1)))
        except Exception:
            pass
        candidates += [f"{website}/newsroom/rss-feed.rss", f"{website}/news/rss", f"{website}/feed",
                       f"https://investors.{base}/rss/news-releases.xml", f"https://ir.{base}/rss/news-releases.xml",
                       f"https://investor.{base}/rss/news-releases.xml"]
        for url in candidates[:8]:
            try:
                r = get(url, timeout=5)
                if b"<item" in r.content[:200000] or b"<entry" in r.content[:200000]:
                    feed = url
                    break
            except Exception:
                continue
    store[ticker] = {"feed": feed, "checked": time.time()}
    try:
        _FEED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _FEED_FILE.write_text(json.dumps(store))
    except OSError:
        pass
    return feed


# ---------------------------------------------------------------------------
# Relevance helpers shared with tab_news
# ---------------------------------------------------------------------------
def name_variants(company):
    """'NVIDIA Corporation' -> {'nvidia'}; 'Rigetti Computing, Inc.' -> {'rigetti computing', 'rigetti'}."""
    core = re.sub(r"\b(inc|corp|corporation|co|ltd|plc|holdings|group|company|the|class [a-c]|n\.v|s\.a|ag|se)\b\.?", "",
                  (company.get("name") or "").lower())
    words = re.sub(r"[^a-z0-9& ]", " ", core).split()
    variants = set()
    if words:
        variants.add(" ".join(words[:2]))
        if len(words[0]) >= 4 or len(words) == 1:
            variants.add(words[0])
    return {v for v in variants if len(v) >= 3}


def mentions(text, company):
    text = (text or "").lower()
    if any(re.search(rf"\b{re.escape(v)}\b", text) for v in name_variants(company)):
        return True
    return bool(re.search(rf"(\(|\$|nasdaq:\s?|nyse:\s?){re.escape(company['ticker'].lower())}\b", text))


# ---------------------------------------------------------------------------
# Fetch everything (parallel, cached per provider)
# ---------------------------------------------------------------------------
def fetch_all(company, refresh=False):
    """Returns (articles, status). status[provider id] = {ok, count, error?, label, kind, configured, cached}."""
    ticker = company["ticker"]
    status, articles, todo = {}, [], []
    for p in PROVIDERS:
        base = {"label": p["label"], "kind": p["kind"], "terms": p["terms"], "configured": configured(p)}
        if not base["configured"]:
            status[p["id"]] = {**base, "ok": False, "error": f"Add {p['needs']} to .env to enable", "count": 0}
            continue
        hit = _cache.get((p["id"], ticker))
        if hit and not refresh:
            age = time.time() - hit[0]
            if isinstance(hit[1], list) and age < CACHE_SECONDS:
                articles += hit[1]
                status[p["id"]] = {**base, "ok": True, "count": len(hit[1]), "cached": True}
                continue
            if isinstance(hit[1], str) and age < FAIL_CACHE_SECONDS:
                status[p["id"]] = {**base, "ok": False, "error": hit[1], "count": 0, "cached": True}
                continue
        todo.append((p, base))

    def run(p):
        return [a for a in p["fetch"](company) if a]

    pool = ThreadPoolExecutor(max_workers=8)
    futures = {pool.submit(run, p): (p, base) for p, base in todo}
    done, pending = wait(futures, timeout=20)
    for future, (p, base) in futures.items():
        if future in pending:
            status[p["id"]] = {**base, "ok": False, "error": "Timed out", "count": 0}
            _cache[(p["id"], ticker)] = (time.time(), "Timed out")
            continue
        try:
            found = future.result()
            _cache[(p["id"], ticker)] = (time.time(), found)
            articles += found
            status[p["id"]] = {**base, "ok": True, "count": len(found)}
        except Exception as error:
            message = (str(error) or error.__class__.__name__)[:140]
            _cache[(p["id"], ticker)] = (time.time(), message)
            status[p["id"]] = {**base, "ok": False, "error": message, "count": 0}
    pool.shutdown(wait=False, cancel_futures=True)     # a slow provider never holds the page
    return articles, status
