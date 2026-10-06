"""
assistant.py — "Ask MarketLab", the research assistant.

The key idea: the AI does NOT answer from memory. For every question we
hand it MarketLab's own retrieved data (a "context pack"), with every fact
labelled by type (reported / estimate / market / calculated) and by source
([S1], [S2], ...), plus an explicit list of what MarketLab has NOT
retrieved yet. The rules in SYSTEM_RULES tell it to cite sources, to admit
gaps, and never to give buy/sell advice.

    build_context(ticker)  -> gathers data from the tab builders, as facts
    context_to_text(ctx)   -> turns those facts into the text the AI reads
    answer_stream(...)     -> streams the AI's answer back to the browser
    suggested_questions()  -> company-specific starter questions (rule-based)

Adding a new data source later (news, price history, filings text) means
adding facts/documents in build_context() and removing the matching line
from NOT_YET_AVAILABLE. Nothing else has to change.
"""

import time
from datetime import date

import config
import describe
from data import now_iso
from provenance import SOURCES, fact
from sources import llm, sec, yahoo
import tab_earnings
import tab_news
import tab_risk
from tab_overview import get_company_info
from tab_profile import get_profile

# What MarketLab can't see yet. The AI is told this list explicitly,
# so it says "MarketLab hasn't retrieved that" instead of guessing.
NOT_YET_AVAILABLE = [
    "the full text of news articles (only headlines, sources, dates and short snippets)",
    "earnings call transcripts and company guidance",
    "revenue broken down by segment or geography",
    "the text of SEC filings (only the list of recent filings, with links)",
    "named customers, suppliers, competitors, and litigation history",
]

SYSTEM_RULES = """You are Ask MarketLab, the research assistant inside MarketLab, a stock research app for intelligent people who are not finance professionals. Today is {today}.

The user is currently looking at {name} ({ticker}). Every question is about this company unless the user clearly says otherwise. Never ask which company they mean.

GROUNDING
- MarketLab's retrieved data is inside <marketlab_data>. It is your primary source. After each claim that uses it, cite the source tag, e.g. [S2].
- Facts marked ESTIMATE are analyst forecasts, not results. Say so when you use them.
- You may add widely known background from general knowledge (what a technology is, long-established history, definitions). Mark it with "(general knowledge, not from MarketLab's data)". Never present general knowledge as current.
- For anything recent or time-sensitive (price moves, news, deals, partnerships, lawsuits, the latest earnings, guidance), rely only on <marketlab_data>. If it isn't there, say plainly that MarketLab hasn't retrieved that yet, and point to where to look (e.g. a recent 8-K or 10-Q link listed in the data). Do not guess.
- Never invent numbers, dates, customers, partners or quotes.
- News is headlines only (you have not read the articles). Attribute them ("a September 30 headline from Barron's reports…"), don't present a headline as established fact, and never infer motives.
- Price-based risk figures (drawdowns, volatility, holding-period returns) describe the past. Say so; never turn them into a forecast or a risk score.

EXPLAINING
- Lead with a direct answer in 1-2 sentences, then the supporting detail.
- Plain English. The first time you use a finance term (P/E, EPS, free cash flow, beta, dilution...), explain it in a short clause.
- Describe what numbers mean factually ("investors are paying about 39 times last year's profit") rather than labelling them good, bad, cheap, expensive or safe. Any comparison needs a stated basis.
- Keep answers under about 250 words unless the user asks for depth. Short paragraphs, compact bullet lists, ### headings only for multi-part answers. No tables.

INVESTMENT DECISIONS
- Never tell the user to buy, sell or hold. Never give price targets, stop-losses, position sizes or expected returns.
- When asked whether a stock suits a short- or long-term approach, structure the answer by time horizon:
  ### Short term (days to weeks): momentum, volatility, upcoming catalysts such as earnings, recent news.
  ### Medium term (months): earnings cycles, revenue growth, guidance, valuation, product launches, sector conditions.
  ### Long term (years): business model, competitive position, growth, profitability, balance sheet, dilution, market size, long-term risks.
  Under each, say what MarketLab's data shows (with citations) and what the user would still need to research.
- When asked when to sell, explain "thesis-breakers": measurable developments that would weaken an investment case, tied to this company's actual figures (e.g. revenue growth falling well below its recent rate, cash burn rising, debt growing).
- When the user asks for a decision, you may note once, briefly, that this isn't personalised financial advice. Don't repeat disclaimers.

<marketlab_data>
{context}
</marketlab_data>"""


# =============================================================================
# 1. Formatting numbers for the AI
# =============================================================================

def money(value, currency="USD"):
    if value is None:
        return "N/A"
    sign = "-" if value < 0 else ""
    value = abs(value)
    prefix, suffix = ("$", "") if currency in (None, "USD") else ("", f" {currency}")
    for limit, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= limit:
            return f"{sign}{prefix}{value / limit:.2f}{unit}{suffix}"
    return f"{sign}{prefix}{value:,.2f}{suffix}"


def pct(fraction, signed=True):
    if fraction is None:
        return "N/A"
    return f"{fraction * 100:+.1f}%" if signed else f"{fraction * 100:.1f}%"


def multiple(value):
    return "N/A" if value is None else f"{value:.1f}x"


# =============================================================================
# 2. Building the context pack
# =============================================================================

class SourceList:
    """Gives each distinct source a short tag (S1, S2, ...) the AI can cite."""

    def __init__(self):
        self.items = []

    def add(self, label, kind, url=None, retrieved_at=None):
        for item in self.items:
            # Same link = same document, even if we describe it differently.
            if (url and item["url"] == url) or (item["label"] == label and item["url"] == url):
                return item["id"]
        item = {"id": f"S{len(self.items) + 1}", "label": label, "kind": kind,
                "url": url, "retrieved_at": retrieved_at}
        self.items.append(item)
        return item["id"]


_context_cache = {}  # ticker -> (time built, context)
CONTEXT_SECONDS = 10 * 60


def get_context(ticker):
    cached = _context_cache.get(ticker)
    if cached and time.time() - cached[0] < CONTEXT_SECONDS:
        return cached[1]
    context = build_context(ticker)
    _context_cache[ticker] = (time.time(), context)
    return context


def build_context(ticker):
    """
    Collect everything MarketLab knows about `ticker` as provenance-tagged
    facts. Each part is optional: if one source fails, the rest still work,
    and `available` records what the AI can and can't see.
    """
    sources = SourceList()
    facts = []
    documents = []      # longer texts (descriptions)
    filings = []
    available = {}

    # ---- Market data (required: it also proves the ticker exists) ----
    overview = get_company_info(ticker)
    currency = overview["currency"]
    market = sources.add("Yahoo Finance: quote and market statistics (delayed)", SOURCES["yahoo"]["kind"],
                         yahoo.quote_url(ticker), overview["fetched_at"])
    g = overview["glance"]

    def add_market(label, value, display):
        facts.append(fact(label, value, display, "market", market, retrieved_at=overview["fetched_at"]))

    add_market("Price", overview["price"], f"{overview['price']} {currency}")
    if overview["change_pct"] is not None:
        add_market("Change today", overview["change_pct"], f"{overview['change_pct']:+.2f}%")
    add_market("Market capitalisation", overview["market_cap"], money(overview["market_cap"], currency))
    if g["week52_low"] is not None and g["week52_high"] is not None:
        add_market("52-week low / high", g["week52_high"], f"{g['week52_low']} / {g['week52_high']} {currency}")
    add_market("P/E ratio (trailing 12 months)", g["pe"], multiple(g["pe"]))
    facts.append(fact("Forward P/E (based on analyst EPS estimates)", g["forward_pe"], multiple(g["forward_pe"]),
                      "estimate", market))
    add_market("EPS, trailing 12 months", g["eps_ttm"], f"{g['eps_ttm']}")
    add_market("Dividend yield (forward dividend / price)", g["dividend_yield"], pct(g["dividend_yield"], False))
    add_market("Beta (5-year, monthly, vs S&P 500)", g["beta"], f"{g['beta']}")
    add_market("Average daily volume (3 months)", g["avg_volume"], f"{g['avg_volume']:,.0f}" if g["avg_volume"] else "N/A")
    available["market data"] = True

    # ---- Company description ----
    try:
        summary = describe.company_summary(ticker)
        if summary.get("source_text"):
            desc = sources.add("Yahoo Finance: company description", "Company description (third-party)",
                               yahoo.quote_url(ticker) + "/profile")
            documents.append({"title": "Company description", "source": desc, "text": summary["source_text"]})
        available["company description"] = bool(summary.get("source_text"))
    except Exception:
        available["company description"] = False

    # ---- Profile: financials, valuation, balance sheet, filings ----
    try:
        profile = get_profile(ticker)
        fin_currency = profile["statement_currency"]
        f = profile["facts"]
        identity = []
        for label, key in (("Legal name", "legal_name"), ("CEO", "ceo"), ("Headquarters", "headquarters"),
                           ("Employees", "employees"), ("Founded", "founded"), ("Sector (Yahoo)", "sector"),
                           ("Industry (Yahoo)", "industry"), ("SEC industry code", "sec_industry"),
                           ("Fiscal year ends", "fiscal_year_end")):
            if f.get(key):
                identity.append(f"{label}: {f[key]:,.0f}" if isinstance(f[key], float) else f"{label}: {f[key]}")
        documents.append({"title": "Company facts", "source": market, "text": "; ".join(identity)})

        # Annual history: every point keeps its own source (SEC filing or Yahoo)
        names = {"revenue": "Revenue", "operating_income": "Operating income", "net_income": "Net income",
                 "free_cash_flow": "Free cash flow", "gross_margin": "Gross margin",
                 "operating_margin": "Operating margin", "eps": "Diluted EPS"}
        for key, label in names.items():
            m = profile["snapshot"][key]
            for point in m["points"]:
                if point["source"] == "sec":
                    tag = sources.add(f"SEC filing: {point['detail']}", SOURCES["sec"]["kind"], point.get("url"))
                    kind = "reported"
                elif point["source"] == "yahoo":
                    tag = sources.add("Yahoo Finance: annual financial statements", SOURCES["yahoo"]["kind"],
                                      yahoo.quote_url(ticker) + "/financials")
                    kind = "reported"
                else:
                    tag, kind = "MarketLab calculation", "calculated"
                if m["kind"] == "percent":
                    display = pct(point["value"], False)
                elif m["kind"] == "per_share":
                    display = f"{point['value']:.2f} {fin_currency}"
                else:
                    display = money(point["value"], fin_currency)
                facts.append(fact(f"{label} (annual)", point["value"], display, kind, tag,
                                  period=f"fiscal year ending {point['date']}"))

        gr = profile["growth"]
        calc = "MarketLab calculation"
        facts += [
            fact("Revenue growth, latest fiscal year", gr["revenue_yoy"], pct(gr["revenue_yoy"]), "calculated", calc),
            fact("Revenue growth, year before", gr["revenue_yoy_prior"], pct(gr["revenue_yoy_prior"]), "calculated", calc),
            fact("Revenue growth, 3-year compound annual", gr["revenue_cagr_3y"], pct(gr["revenue_cagr_3y"]), "calculated", calc),
            fact("Latest quarter revenue vs same quarter last year", gr["quarterly_revenue_yoy"],
                 pct(gr["quarterly_revenue_yoy"]), "reported", market),
        ]
        for name, est in gr["estimates"].items():
            for period, e in est.items():
                if e and e.get("avg") is not None:
                    shown = money(e["avg"], fin_currency) if name == "revenue" else f"{e['avg']:.2f}"
                    facts.append(fact(f"Analyst consensus {name.upper()} ({period.replace('_', ' ')})", e["avg"],
                                      f"{shown} (implied growth {pct(e['growth'])}, {e['analysts']:.0f} analysts)"
                                      if e.get("analysts") else shown, "estimate", market))

        v = profile["valuation"]
        for label, key in (("Price / sales (TTM)", "price_to_sales"), ("Price / book", "price_to_book"),
                           ("EV / EBITDA", "ev_to_ebitda"), ("PEG ratio", "peg")):
            facts.append(fact(label, v[key], multiple(v[key]), "market", market))
        facts.append(fact("Free cash flow yield (latest FY FCF / market cap)", v["fcf_yield"],
                          pct(v["fcf_yield"], False), "calculated", calc))

        h = profile["health"]
        period = f"quarter ending {h['as_of']}" if h["as_of"] else None
        balance = sources.add("Yahoo Finance: quarterly balance sheet", SOURCES["yahoo"]["kind"],
                              yahoo.quote_url(ticker) + "/balance-sheet")
        facts += [
            fact("Cash and short-term investments", h["cash"], money(h["cash"], fin_currency), "reported", balance, period),
            fact("Total debt", h["total_debt"], money(h["total_debt"], fin_currency), "reported", balance, period),
            fact("Net cash (cash minus debt; negative = net debt)", h["net_cash"], money(h["net_cash"], fin_currency),
                 "calculated", calc, period),
            fact("Current ratio", h["current_ratio"], multiple(h["current_ratio"]), "calculated", calc, period),
            fact("Debt / equity", h["debt_to_equity"], multiple(h["debt_to_equity"]), "calculated", calc, period),
            fact("Interest coverage (EBIT / interest)", h["interest_coverage"], multiple(h["interest_coverage"]),
                 "calculated", calc),
        ]
        available["financial statements"] = True

        if profile["filings"]:
            edgar = sources.add("SEC EDGAR: list of recent filings", SOURCES["sec"]["kind"], profile.get("edgar_url"))
            for item in profile["filings"][:8]:
                filings.append(f"{item['form']} ({item['description']}) filed {item['filed']}"
                               f"{', period ' + item['period'] if item['period'] else ''}: {item['url']} [{edgar}]")
        available["SEC filings list"] = bool(profile["filings"])
    except Exception:
        available["financial statements"] = False
        available["SEC filings list"] = False
        profile = None

    # ---- Earnings: estimates vs results, and the stock's move afterwards ----
    try:
        e = tab_earnings.get_earnings(ticker)
        est_src = sources.add("Yahoo Finance: earnings calendar and analyst estimates", SOURCES["yahoo"]["kind"],
                              yahoo.quote_url(ticker) + "/analysis")
        calc = "MarketLab calculation"
        if e["next"]:
            n = e["next"]
            facts.append(fact("Next earnings report date", n["date"], f"{n['date']} ({n['timing'].replace('_', ' ')})",
                              "estimate", est_src))
            facts.append(fact("Next quarter EPS consensus", n["eps_estimate"],
                              f"{n['eps_estimate']:.2f}" if n["eps_estimate"] is not None else "N/A", "estimate", est_src))
            facts.append(fact("Next quarter revenue consensus", n["revenue_estimate"],
                              money(n["revenue_estimate"], e["currency"]), "estimate", est_src))
        for h in e["history"][:6]:
            r = h.get("reaction") or {}
            rev = h.get("revenue") or {}
            text = (f"EPS {h['eps_actual']} vs estimate {h['eps_estimate']} (surprise {h['surprise_pct']}%)"
                    + (f"; quarterly revenue {money(rev.get('value'), e['currency'])}, {pct(rev.get('yoy'))} year over year" if rev else "")
                    + (f"; stock moved {pct(r.get('one_day'))} the next session and {pct(r.get('five_day'))} over 5 sessions" if r else ""))
            facts.append(fact(f"Earnings report {h['date']}", h["eps_actual"], text, "reported", est_src,
                              period=f"reported {h['date']}"))
        documents.append({"title": "Earnings notes", "source": calc,
                          "text": " ".join(e["notes"].values())})
        available["earnings history"] = bool(e["history"])
    except Exception:
        available["earnings history"] = False

    # ---- News: de-duplicated headlines (text of articles is NOT read) ----
    try:
        news = tab_news.build_news(ticker)
        heads = []
        for e in sorted(news["events"], key=lambda e: e["relevance"], reverse=True)[:25]:
            lead = e["sources"][0]
            tag = sources.add(f"{lead['name']}: {e['headline'][:90]}", "News (headline only)", lead["url"], e["published"])
            primary = f" | primary source: {e['primary']['name']}" if e.get("primary") else ""
            policy = f" | government/policy: {e['government']['kind']}" if e.get("government") else ""
            heads.append(f"{e['published'][:10]} | {e['headline']} | {e['category']} | {e['source_count']} publisher(s){primary}{policy} [{tag}]")
        if heads:
            documents.append({"title": "Recent news events (last 30 days, most relevant first; article text not read)",
                              "source": "see each line", "text": "\n".join(heads)})
        available["news headlines"] = bool(heads)
    except Exception:
        available["news headlines"] = False

    # ---- Price-based risk measurements ----
    try:
        r = tab_risk.get_risk(ticker)
        calc = "MarketLab calculation"
        dd, vol, tail = r["drawdowns"], r["volatility"], r["tail"]
        facts.append(fact("Current drawdown from the previous high", dd["current"], pct(dd["current"]), "calculated", calc,
                          period=f"high on {dd['current_peak']}"))
        if dd["max"]:
            facts.append(fact("Deepest historical drawdown", dd["max"]["depth"],
                              f"{pct(dd['max']['depth'])} ({dd['max']['peak']} to {dd['max']['trough']})", "calculated", calc))
        facts.append(fact("Annualised volatility, last year", vol["one_year"], pct(vol["one_year"], False), "calculated", calc))
        for h in r["horizons"]:
            st = h["stats"]
            if st and h["label"] in ("1M", "1Y"):
                facts.append(fact(f"Historical {h['label']} holding-period returns", st["median"],
                                  f"median {pct(st['median'])}, 5th percentile {pct(st['p5'])}, 95th {pct(st['p95'])}, "
                                  f"{pct(st['p_negative'], False)} of periods negative (overlapping windows since {r['tested_from']})",
                                  "calculated", calc))
        for m in r["market"]:
            if m.get("y1"):
                facts.append(fact(f"Beta / correlation vs {m['symbol']} (1 year, daily)", m["y1"]["beta"],
                                  f"beta {m['y1']['beta']:.2f}, correlation {m['y1']['correlation']:.2f}", "calculated", calc))
        if tail:
            facts.append(fact("Historical 1-day 95% VaR / expected shortfall (5 years)", tail["var95"],
                              f"{pct(tail['var95'])} / {pct(tail['es95'])}", "calculated", calc))
        si = r["asymmetry"]["short_interest"]
        if si["percent_of_float"] is not None:
            facts.append(fact("Short interest (% of float)", si["percent_of_float"], pct(si["percent_of_float"], False),
                              "market", market, period=f"as of {si['as_of']}"))
        available["price history and risk"] = True
    except Exception:
        available["price history and risk"] = False

    return {
        "ticker": ticker,
        "name": overview["name"],
        "currency": currency,
        "built_at": now_iso(),
        "facts": facts,
        "documents": documents,
        "filings": filings,
        "sources": sources.items,
        "available": available,
        "not_available": NOT_YET_AVAILABLE,
        "overview": overview,
        "profile": profile,
    }


def context_to_text(context):
    """Turn the context pack into compact text for the AI."""
    lines = [f"COMPANY: {context['name']} ({context['ticker']}), trading currency {context['currency']}",
             f"DATA RETRIEVED: {context['built_at']}", ""]

    lines.append("SOURCES")
    for s in context["sources"]:
        lines.append(f"[{s['id']}] {s['label']} | {s['kind']}" + (f" | {s['url']}" if s["url"] else ""))
    lines.append("")

    for doc in context["documents"]:
        lines.append(f"{doc['title'].upper()} [{doc['source']}]")
        lines.append(doc["text"])
        lines.append("")

    lines.append("FACTS (type: market = market data; reported = company-reported result; "
                 "calculated = computed by MarketLab; ESTIMATE = analyst forecast)")
    for f in context["facts"]:
        if f["value"] is None:   # missing data is simply left out (never shown as "None")
            continue
        kind = "ESTIMATE" if f["type"] == "estimate" else f["type"]
        period = f" | {f['period']}" if f["period"] else ""
        lines.append(f"- {f['label']}: {f['display']} | {kind}{period} [{f['source']}]")
    lines.append("")

    if context["filings"]:
        lines.append("RECENT SEC FILINGS (titles and links only; their text has not been read)")
        lines += [f"- {item}" for item in context["filings"]]
        lines.append("")

    lines.append("NOT AVAILABLE TO YOU YET (say so if asked about these):")
    lines += [f"- {item}" for item in context["not_available"]]
    return "\n".join(lines)


# =============================================================================
# 3. Answering
# =============================================================================

MAX_TURNS = 12
MAX_CHARS = 4000


def clean_history(messages):
    """Keep only well-formed user/assistant turns, the latest 12, ending with the user."""
    cleaned = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role, content = message.get("role"), message.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            cleaned.append({"role": role, "content": content.strip()[:MAX_CHARS]})
    cleaned = cleaned[-MAX_TURNS:]
    while cleaned and cleaned[0]["role"] != "user":   # the API expects the user to speak first
        cleaned.pop(0)
    if not cleaned or cleaned[-1]["role"] != "user":
        return []
    return cleaned


def system_prompt(context, focus=None):
    prompt = SYSTEM_RULES.format(
        today=date.today().isoformat(), name=context["name"], ticker=context["ticker"],
        context=context_to_text(context),
    )
    # "Explain this": the user clicked something in the interface.
    if focus and isinstance(focus, dict) and focus.get("label"):
        prompt += (f"\n\nThe user clicked this item in the MarketLab interface: "
                   f"\"{str(focus.get('label'))[:80]}\" = \"{str(focus.get('value', ''))[:80]}\" "
                   f"(section: {str(focus.get('section', ''))[:40]}). Explain briefly what it means in general, "
                   f"then what it shows for {context['ticker']} specifically, using the data.")
    return prompt


def answer_stream(context, messages, focus=None):
    return llm.assistant_stream(system_prompt(context, focus), messages, model=config.AI_MODEL_ASSISTANT, max_tokens=1200)


# =============================================================================
# 4. Suggested questions (rule-based: built from the company's own numbers)
# =============================================================================

def suggested_questions(context):
    """
    3-5 starter questions chosen from the company's actual data. Rule-based
    on purpose: free, instant, and every suggestion is justified by a number
    MarketLab really has.
    """
    t = context["ticker"]
    overview = context["overview"]
    profile = context.get("profile") or {}
    snapshot = profile.get("snapshot", {})
    growth = profile.get("growth", {})
    health = profile.get("health", {})
    glance = overview["glance"]

    net_income = (snapshot.get("net_income") or {}).get("latest")
    fcf = (snapshot.get("free_cash_flow") or {}).get("latest")
    revenue_growth = growth.get("revenue_yoy")
    est = (growth.get("estimates") or {}).get("revenue", {}).get("current_fy") or {}

    questions = [f"I've never heard of {t}. Explain what they actually do and how they make money."]

    if net_income is not None and net_income < 0:
        questions.append(f"{t} is losing money. What would need to happen for it to become profitable?")
    if fcf is not None and fcf < 0 and health.get("cash"):
        questions.append(f"How quickly is {t} burning cash, and how long could its current cash last?")
    if revenue_growth is not None and revenue_growth >= 0.25:
        questions.append(f"What's behind {t}'s fast revenue growth, and how sustainable does it look?")
    elif revenue_growth is not None and revenue_growth < 0:
        questions.append(f"Why has {t}'s revenue been shrinking?")
    if est.get("growth") is not None and revenue_growth is not None and est["growth"] > revenue_growth + 0.1:
        questions.append(f"Analysts expect {t}'s revenue growth to speed up. What are they assuming?")
    if glance.get("dividend_yield"):
        questions.append(f"How well does {t}'s free cash flow cover its dividend?")
    if (health.get("debt_to_equity") or 0) > 1:
        questions.append(f"How much debt does {t} carry, and how comfortably can it handle it?")
    if glance.get("pe") and glance.get("forward_pe") and glance["forward_pe"] < glance["pe"] * 0.8:
        questions.append(f"Explain {t}'s P/E versus its forward P/E. What does the gap imply?")
    if glance.get("pe") is None and net_income is not None and net_income < 0:
        questions.append(f"Explain P/E to me, and why {t} doesn't have one.")
    if (glance.get("beta") or 0) >= 1.5:
        questions.append(f"Why has {t} been so much more volatile than the overall market?")

    questions.append(f"Is {t} more of a short-term trade or a long-term research idea? Walk me through each time horizon.")
    questions.append(f"What are the biggest risks I should research for {t}?")

    unique = list(dict.fromkeys(questions))
    # Intro + up to 2 data-driven questions + time horizon + risks = at most 5.
    data_driven = unique[1:-2][:2]
    return [unique[0], *data_driven, *unique[-2:]]
