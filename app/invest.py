"""
invest.py — the Investment Lab: long-horizon thesis research on a basket of holdings.

    THESIS (plain language)  →  tickers, themes, horizon, concerns          parse_thesis()
    BASKET (stocks/ETFs/cash, weights)                                         chosen by the user
    ANALYSIS                                                                   analyze()
        per holding     price behaviour, valuation, growth, balance sheet, dilution
                        (Yahoo Finance + SEC), and what its latest annual report says it
                        depends on (risk_intel.exposures: suppliers, customers, geographies,
                        policy topics, theme language)
        HIDDEN EXPOSURES  shared dependencies across holdings ("4 of 5 depend on AI capex")
        CORRELATION ≠ DIVERSIFICATION  price correlation and dependency overlap side by side
        STRESS TEST     what must be true / what could break, each line tied to evidence
        SCENARIOS       which holdings an event reaches, and through which dependency
                        (exposure pathways only: no return forecasts)

Principles
    * Research, not recommendations: suggested tickers are "candidates to research".
    * Correlation alone is never treated as dependency, and dependency is never inferred from
      model knowledge: only what filings say (with the sentence), classifications and numbers.
    * The horizon changes what is shown first (short: momentum, valuation, catalysts;
      long: growth, balance sheet, dependencies, competition, structural risks).
"""

import json
import math
import re
import secrets
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import marketdata
import risk_intel
from config import BASE_DIR
from data import now_iso, to_number
from sources import sec, yahoo

THESES_DIR = BASE_DIR / "research" / "theses"
_lock = threading.Lock()
_profiles = {}
_analyses = {}
PROFILE_SECONDS = 6 * 3600

HORIZONS = {"3m": ("3 months", 3), "6m": ("6 months", 6), "12m": ("12 months", 12), "24m": ("24 months", 24),
            "5y": ("5 years", 60), "custom": ("Custom", None)}
EMPHASIS = {
    "short": {"label": "Short horizon: price behaviour, valuation and near-term catalysts come first",
              "order": ["momentum", "valuation", "catalysts", "exposures", "growth", "balance"]},
    "medium": {"label": "Medium horizon: growth, valuation and dependencies come first",
               "order": ["growth", "valuation", "exposures", "balance", "momentum", "catalysts"]},
    "long": {"label": "Long horizon: business growth, balance sheet, dependencies and competition come first",
             "order": ["growth", "balance", "exposures", "competition", "valuation", "momentum"]},
}

THEMES = {
    "quantum": {"label": "Quantum computing", "patterns": [r"\bquantum\b"],
                "candidates": ["IONQ", "RGTI", "QBTS", "QUBT", "IBM", "GOOGL", "HON"],
                "must": ["The quantum computing market grows enough to support several companies",
                         "Commercial (not only research / government) adoption increases",
                         "Technology milestones arrive roughly on the companies' timelines",
                         "Pure-play companies keep enough cash, or raise it without severe dilution"]},
    "ai_infra": {"label": "AI infrastructure", "patterns": [r"\bai\b.{0,30}(?:infra|capex|spend|data ?cent|chips?|compute)", r"\bai infrastructure\b",
                                                           r"artificial intelligence", r"\bgpus?\b", r"data ?cent(?:er|re)s?"],
                 "candidates": ["NVDA", "AMD", "AVGO", "TSM", "MU", "ANET", "VRT", "SMCI", "ASML", "MRVL"],
                 "must": ["Hyperscaler and enterprise AI capital spending stays strong over the horizon",
                          "Supply (advanced foundry capacity, memory, power) keeps up",
                          "Export rules don't cut off major markets",
                          "Valuations don't already price in more growth than arrives"]},
    "semis": {"label": "Semiconductors", "patterns": [r"semiconductor", r"\bchips?\b(?! and)"],
              "candidates": ["NVDA", "AMD", "AVGO", "TSM", "ASML", "QCOM", "INTC", "MU", "TXN", "AMAT"],
              "must": ["The semiconductor cycle doesn't turn down hard within the horizon", "Taiwan / Asian manufacturing isn't disrupted"]},
    "cloud": {"label": "Cloud & software", "patterns": [r"\bcloud\b", r"\bsaas\b", r"\bsoftware\b"],
              "candidates": ["MSFT", "AMZN", "GOOGL", "ORCL", "CRM", "NOW", "SNOW"],
              "must": ["Enterprise IT budgets keep growing", "AI features add revenue rather than only cost"]},
    "cyber": {"label": "Cybersecurity", "patterns": [r"cyber ?security", r"\bcyber\b"],
              "candidates": ["CRWD", "PANW", "ZS", "FTNT", "NET", "S"],
              "must": ["Security budgets stay a priority", "Pricing holds as vendors consolidate"]},
    "nuclear": {"label": "Nuclear & uranium", "patterns": [r"\bnuclear\b", r"\buranium\b", r"\bsmrs?\b"],
                "candidates": ["CCJ", "LEU", "SMR", "OKLO", "BWXT", "CEG"],
                "must": ["Regulators approve new reactors on reasonable timelines", "Power demand (incl. data centres) keeps rising",
                         "Projects get financed without severe dilution"]},
    "space": {"label": "Space", "patterns": [r"\bspace\b", r"satellite", r"\blaunch\b"],
              "candidates": ["RKLB", "ASTS", "LUNR", "PL", "IRDM"],
              "must": ["Launch and satellite demand grows", "Government contracts continue", "Companies fund development without excessive dilution"]},
    "obesity": {"label": "GLP-1 / obesity drugs", "patterns": [r"glp-?1", r"obesity", r"weight[- ]loss"],
                "candidates": ["LLY", "NVO", "AMGN", "VKTX"],
                "must": ["Demand and reimbursement keep expanding", "Manufacturing capacity keeps up", "Pricing pressure stays limited"]},
    "ev": {"label": "Electric vehicles & batteries", "patterns": [r"electric vehicles?", r"\bevs?\b", r"batter(?:y|ies)", r"lithium"],
           "candidates": ["TSLA", "RIVN", "ALB", "QS", "ENPH"],
           "must": ["EV adoption keeps rising", "Battery input costs stay manageable", "Subsidy and tariff policy stays supportive"]},
}
STOPWORDS = {"I", "A", "AI", "US", "USA", "CEO", "CFO", "IPO", "EV", "EVS", "ETF", "ETFS", "GDP", "AND", "OR", "THE", "TO", "IN", "OF", "IT",
             "IS", "MY", "ME", "SO", "IF", "ON", "AT", "BY", "FOR", "NOT", "DON", "T", "ALL", "ONE", "TWO", "BUT", "WITH", "NEXT", "YEARS",
             "YEAR", "SMR", "CPU", "GPU", "GPUS", "TAM", "R", "AND", "PE", "PS", "EPS", "SEC", "FED", "UK", "EU", "OK"}
KNOWN_FUNDS = {"SPY", "QQQ", "VOO", "VTI", "IWM", "DIA", "SMH", "SOXX", "QTUM", "ARKK", "ARKQ", "VGT", "XLK", "XLF", "XLE", "TLT", "GLD", "BOTZ", "URA", "NLR"}
HYPERSCALERS = {"Microsoft", "Amazon / AWS", "Alphabet / Google", "Meta", "Oracle"}
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10, "twelve": 12, "eighteen": 18}

SCENARIOS = [
    {"id": "ai_capex", "label": "AI infrastructure spending slows 30%", "factors": ["theme:ai", "customer:hyperscalers", "theme:data_center"],
     "why": "Holdings whose filings emphasise AI / data-centre demand or name hyperscalers as customers."},
    {"id": "rates", "label": "Rates stay high for longer", "factors": ["fund:burning_cash", "fund:high_valuation", "fund:high_debt"],
     "why": "Companies that need outside funding, carry high debt or trade on far-future growth are the usual transmission channels."},
    {"id": "export", "label": "Export controls tighten", "factors": ["policy:Export controls", "geo:China"],
     "why": "Holdings whose filings discuss export controls or meaningful China exposure."},
    {"id": "tsmc", "label": "TSMC / Taiwan supply is disrupted", "factors": ["entity:TSMC", "geo:Taiwan"],
     "why": "Holdings that name TSMC as a manufacturer/supplier or discuss Taiwan for manufacturing."},
    {"id": "china", "label": "China demand falls / trade conflict escalates", "factors": ["geo:China", "policy:Tariffs & trade"],
     "why": "Holdings with China sales or supply discussed in filings, or tariff exposure."},
    {"id": "gov", "label": "Government contract funding is cut", "factors": ["customer:us_gov", "policy:Government contracts & procurement", "policy:Subsidies & incentives"],
     "why": "Holdings that disclose government customers, contracts or subsidies."},
    {"id": "funding", "label": "Equity markets close to new issuance", "factors": ["fund:burning_cash", "fund:dilution"],
     "why": "Companies burning cash or already diluting shareholders depend on raising money."},
    {"id": "quantum", "label": "Quantum milestones slip by years", "factors": ["theme:quantum"],
     "why": "Holdings whose filings centre on quantum computing."},
]
SCENARIO_WORDS = [(r"\bai\b|artificial intelligence|capex|data ?cent|hyperscal", ["theme:ai", "customer:hyperscalers", "theme:data_center"]),
                  (r"\brates?\b|interest|fed\b|yields?", ["fund:burning_cash", "fund:high_valuation", "fund:high_debt"]),
                  (r"export|entity list", ["policy:Export controls"]), (r"china|chinese|beijing", ["geo:China"]),
                  (r"taiwan|tsmc", ["entity:TSMC", "geo:Taiwan"]), (r"tariff|trade war", ["policy:Tariffs & trade"]),
                  (r"government|defen[cs]e|contract", ["customer:us_gov", "policy:Government contracts & procurement"]),
                  (r"recession|consumer|slowdown", ["macro:Consumer spending", "macro:Economic cycle"]),
                  (r"dilut|funding|raise capital|issuance", ["fund:burning_cash", "fund:dilution"]),
                  (r"quantum", ["theme:quantum"]), (r"samsung", ["entity:Samsung"]), (r"memory|hbm|micron|sk hynix", ["entity:SK hynix", "entity:Micron"])]


class InvestError(ValueError):
    pass


# ---------------------------------------------------------------- thesis parsing

def _known_ticker(sym):
    if sym in KNOWN_FUNDS or any(sym in t["candidates"] for t in THEMES.values()):
        return True
    if not sec.is_configured():
        return False
    try:
        sec.cik_for_ticker(sym)
        return True
    except Exception:                           # noqa: BLE001
        return False


def parse_thesis(text):
    """Holdings only from resolver.resolve (names, $TICK, listed non-word tickers); questions for the rest."""
    text = (text or "").strip()
    low = text.lower()
    import resolver
    extra = set(KNOWN_FUNDS) | {c for t in THEMES.values() for c in t["candidates"]}
    res = resolver.resolve(text, extra)
    tickers = [h["ticker"] for h in res["holdings"]]
    themes = [{"id": k, "label": t["label"]} for k, t in THEMES.items() if any(re.search(p, low) for p in t["patterns"])]
    months = None
    m = re.search(r"(\d+|one|two|three|four|five|six|ten|twelve|eighteen)\s*(?:-|\s)?(years?|months?)", low)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else WORDS[m.group(1)]
        months = n * 12 if m.group(2).startswith("year") else n
    horizon = None
    if months:
        exact = {3: "3m", 6: "6m", 12: "12m", 24: "24m", 60: "5y"}
        horizon = {"id": exact.get(months, "custom"), "months": months}
    concerns = []
    if re.search(r"concentrat|all my risk|one company|single company|too much in|overweight", low):
        concerns.append({"id": "concentration", "label": "Not too much risk in one company"})
    if re.search(r"hidden|depend|same (?:supplier|customer)|exposure", low):
        concerns.append({"id": "dependency", "label": "No single hidden dependency"})
    if re.search(r"drawdown|volatil|crash|lose", low):
        concerns.append({"id": "drawdown", "label": "Limit large drawdowns"})
    candidates = []
    for t in themes:
        for sym in THEMES[t["id"]]["candidates"]:
            if sym not in tickers and sym not in candidates:
                candidates.append(sym)
    return {"tickers": tickers, "detected": res["holdings"], "suggestions": res["suggestions"], "ignored": res["ignored"],
            "themes": themes, "horizon": horizon, "concerns": concerns, "candidates": candidates[:14],
            "candidate_note": "Candidates to research, from MarketLab's theme list. Not recommendations."}


# ---------------------------------------------------------------- per-holding profile

def _strength(mentions, emphasized=3):
    return "emphasized" if mentions >= emphasized else "mentioned"


def _factors(exp, fund, info):
    out = {}

    def add(key, label, kind, strength, evidence=None):
        cur = out.get(key)
        rank = {"disclosed": 3, "emphasized": 2, "classification": 2, "measured": 2, "mentioned": 1}
        if cur and rank[cur["strength"]] >= rank[strength]:
            if evidence:
                cur["evidence"] = (cur["evidence"] + evidence)[:3]
            return
        out[key] = {"key": key, "label": label, "kind": kind, "strength": strength, "evidence": (evidence or [])[:3]}

    if info.get("industry"):
        add(f"industry:{info['industry']}", info["industry"], "industry", "classification", [{"text": "Yahoo Finance industry classification", "type": "Classification (Yahoo Finance)"}])
    dep = (exp or {}).get("dependencies") or {}
    srcd = (exp or {}).get("source") or {}
    src = srcd.get("name")
    prov = {"url": srcd.get("url"), "date": srcd.get("date"), "type": "Annual report (SEC EDGAR)"}
    for e in dep.get("entities", []):
        ev = [{"text": x["text"], "source": src, **prov} for x in e.get("evidence", [])]
        rel = e["relationship"]
        if rel == "Competitor":
            continue
        if rel == "Customer" and e["name"] in HYPERSCALERS:
            add("customer:hyperscalers", "Hyperscaler spending (Microsoft, Amazon, Google, Meta, Oracle)", "customer", _strength(e["mentions"]), ev)
        elif e["name"] == "U.S. government":
            add("customer:us_gov", "U.S. government as customer / funder", "customer", _strength(e["mentions"]), ev)
        elif rel in ("Manufacturer / supplier", "Cloud / infrastructure"):
            add(f"entity:{e['name']}", f"{e['name']} ({'supplier / manufacturer' if rel.startswith('Manu') else 'cloud / infrastructure'})", "supplier",
                _strength(e["mentions"]), ev)
        elif rel in ("Customer", "Distributor / channel", "Partner"):
            add(f"entity:{e['name']}", f"{e['name']} ({rel.lower()})", "customer" if rel == "Customer" else "partner", _strength(e["mentions"]), ev)
    for q in dep.get("quantitative", []):
        subj = q["subject"]
        if re.search(r"customer|distributor|largest", subj, re.I) and q["of"].startswith(("revenue", "net sales", "sales")):
            add("concentration:customer", "A few large customers (disclosed concentration)", "customer", "disclosed",
                [{"text": f"{subj}: {q['percent']:g}% of {q['of']}. " + q["evidence"][:240], "source": src, **prov}])
        for geo in ("China", "Taiwan"):
            if geo.lower() in subj.lower():
                add(f"geo:{geo}", geo, "geography", "disclosed", [{"text": f"{q['percent']:g}% of {q['of']}: " + q["evidence"][:220], "source": src, **prov}])
    for g in dep.get("geographies", []):
        add(f"geo:{g['name']}", f"{g['name']} ({', '.join(g['why'])})", "geography", _strength(g["mentions"], 4),
            [{"text": x["text"], "source": src, **prov} for x in g.get("evidence", [])])
    for mac in dep.get("macro", []):
        if mac["mentions"] >= 3:
            add(f"macro:{mac['name']}", mac["name"], "macro", "emphasized", [{"text": x["text"], "source": src, **prov} for x in mac.get("evidence", [])])
    for pol in (exp or {}).get("policy", []):
        add(f"policy:{pol['topic']}", pol["topic"], "policy", _strength(pol["mentions"]),
            [{"text": x["text"], "source": src, **prov} for x in pol.get("evidence", [])])
    themes = (exp or {}).get("themes") or {}
    labels = {"ai": "AI demand", "data_center": "Data-centre build-out", "quantum": "Quantum computing", "crypto": "Crypto / digital assets",
              "ev": "Electric vehicles", "defense": "Defence spending", "nuclear": "Nuclear", "hyperscale": "Hyperscale cloud"}
    for k, n in themes.items():
        if k in labels and n >= 5:
            add(f"theme:{k}", labels[k], "theme", "emphasized" if n >= 15 else "mentioned",
                [{"text": f"The annual report mentions this {n} times in its business and risk sections.", "source": src, **prov,
                  "mentions": n}])
    if fund:
        if fund.get("burning_cash"):
            runway = fund.get("runway_years")
            add("fund:burning_cash", "Needs outside funding (negative free cash flow)", "financial", "measured",
                [{"type": "Reported financials (SEC / Yahoo)", "text": f"Free cash flow {fund['free_cash_flow'] / 1e6:,.0f}M" + (f"; cash covers ≈ {runway:.1f} years at that rate" if runway else "")}])
        dte = to_number(fund.get("debt_to_equity"))
        if dte is not None and dte > 1.5:
            add("fund:high_debt", "High debt (debt ÷ equity above 1.5)", "financial", "measured", [{"type": "Reported financials (SEC / Yahoo)", "text": f"Debt ÷ equity {dte:.2f}"}])
        one = ((fund.get("dilution") or {}).get("one_year") or {}).get("value")
        if one is not None and one > 0.1:
            add("fund:dilution", "Diluting shareholders (shares +10% or more in a year)", "financial", "measured",
                [{"type": "SEC cover pages", "text": f"Shares outstanding {one:+.0%} over one year (SEC cover pages)"}])
    ps, fpe = to_number(info.get("priceToSalesTrailing12Months")), to_number(info.get("forwardPE"))
    if (ps and ps > 15) or (fpe and fpe > 50):
        add("fund:high_valuation", "Valuation depends on far-future growth (P/S > 15 or forward P/E > 50)", "financial", "measured",
            [{"type": "Market data (Yahoo Finance)", "text": f"P/S {ps:.1f}" if ps else f"Forward P/E {fpe:.0f}"}])
    return out


# ---- request de-duplication: concurrent callers of the same work share one computation
_inflight = {}
_inflight_lock = threading.Lock()


def _single_flight(key, fn):
    with _inflight_lock:
        entry = _inflight.get(key)
        owner = entry is None
        if owner:
            entry = {"event": threading.Event(), "result": None, "error": None}
            _inflight[key] = entry
    if not owner:
        entry["event"].wait(120)
        if entry["error"]:
            raise entry["error"]
        return entry["result"]
    try:
        entry["result"] = fn()
        return entry["result"]
    except Exception as error:                  # noqa: BLE001
        entry["error"] = error
        raise
    finally:
        entry["event"].set()
        with _inflight_lock:
            _inflight.pop(key, None)


_basics = {}
BASIC_SECONDS = 30 * 60


def basic(ticker):
    """Fast facts for one holding: Yahoo info + daily prices (cached 30 min, shared by concurrent requests)."""
    with _lock:
        hit = _basics.get(ticker)
        if hit and time.time() - hit[0] < BASIC_SECONDS:
            return hit[1]
    return _single_flight(("basic", ticker), lambda: _basic(ticker))


def _basic(ticker):
    out = {"ticker": ticker, "errors": []}
    try:
        info = yahoo.get_info(ticker)
    except Exception as error:                  # noqa: BLE001
        raise InvestError(f"Could not resolve {ticker} ({type(error).__name__}).") from error
    is_fund = (info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND")
    out["statement_currency"] = info.get("financialCurrency") or "USD"
    out.update(name=info.get("longName") or info.get("shortName") or ticker, sector=info.get("sector") or ("Fund" if is_fund else None),
               industry=info.get("industry") or (info.get("category") if is_fund else None), fund=is_fund,
               price=to_number(info.get("currentPrice") or info.get("regularMarketPrice")),
               market_cap=to_number(info.get("marketCap")), revenue_growth=to_number(info.get("revenueGrowth")),
               earnings_growth=to_number(info.get("earningsGrowth")), gross_margin=to_number(info.get("grossMargins")),
               operating_margin=to_number(info.get("operatingMargins")), profit_margin=to_number(info.get("profitMargins")),
               pe=to_number(info.get("trailingPE")), forward_pe=to_number(info.get("forwardPE")),
               ps=to_number(info.get("priceToSalesTrailing12Months")), beta=to_number(info.get("beta")),
               high_52w=to_number(info.get("fiftyTwoWeekHigh")))
    ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
    out["next_earnings"] = datetime.fromtimestamp(ts).date().isoformat() if ts and ts > time.time() else None
    splits = {}
    try:
        bars = marketdata.daily_bars(ticker)
        dates, closes = bars["date"][-2600:], bars["close"][-2600:]
        out["closes"] = {"dates": dates, "values": closes}
        last = closes[-1]
        for label, n in (("ret_3m", 63), ("ret_6m", 126), ("ret_12m", 252)):
            out[label] = last / closes[-n - 1] - 1 if len(closes) > n else None
        rets = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 252), len(closes))]
        out["vol_1y"] = statistics.pstdev(rets) * math.sqrt(252) if len(rets) > 20 else None
        out["from_high"] = last / max(closes[-252:]) - 1
        splits = bars.get("splits") or {}
    except Exception as error:                  # noqa: BLE001
        out["errors"].append(f"Price history unavailable ({type(error).__name__}).")
        out["closes"] = None
    out["_info"], out["_splits"] = info, splits
    with _lock:
        _basics[ticker] = (time.time(), out)
    return out


def forget(tickers):
    """Explicit refresh: drop cached facts for these tickers so the next analysis fetches them again."""
    with _lock:
        for t in tickers:
            _basics.pop(t, None)
            _profiles.pop(t, None)
        _corr_cache.clear()
    marketdata._cache.clear() if hasattr(marketdata, "_cache") else None


def profile(ticker):
    """Everything the Investment Lab needs for one holding (cached for 6 hours, shared by concurrent requests)."""
    with _lock:
        hit = _profiles.get(ticker)
        if hit and time.time() - hit[0] < PROFILE_SECONDS:
            return hit[1]
    return _single_flight(("profile", ticker), lambda: _profile(ticker))


def _profile(ticker):
    b = basic(ticker)
    out = {k: v for k, v in b.items() if not k.startswith("_")}
    out["errors"] = list(b["errors"])
    info, splits, is_fund = b["_info"], b["_splits"], b["fund"]
    fund = None
    if not is_fund:
        try:
            import tab_risk
            fund, _ = tab_risk._fundamental(ticker, info, splits)
        except Exception as error:              # noqa: BLE001
            out["errors"].append(f"Financial statements unavailable ({type(error).__name__}).")
    out["fundamentals"] = {k: (fund or {}).get(k) for k in ("cash", "total_debt", "net_cash", "free_cash_flow", "runway_years",
                                                            "burning_cash", "debt_to_equity", "operating_margin", "gross_margin")}
    out["dilution_1y"] = (((fund or {}).get("dilution") or {}).get("one_year") or {}).get("value")
    out["fund_holdings"], out["fund_sectors"] = (_fund_lookthrough(ticker) if is_fund else ([], {}))
    exp = risk_intel.exposures(ticker)
    if is_fund and out["fund_holdings"]:
        exp = dict(exp, message="A fund: MarketLab looks through its largest holdings (Yahoo Finance) to find overlap with the rest of the basket.")
    out["exposure_state"] = {"state": exp.get("state"), "message": exp.get("message"), "source": exp.get("source")}
    dep = exp.get("dependencies") or {}
    out["competitors"] = [e["name"] for e in dep.get("entities", []) if e["relationship"] == "Competitor"][:8]
    out["factors"] = _factors(exp, fund, info)
    if info.get("sector") and not is_fund:
        out["factors"][f"sector:{info['sector']}"] = {"key": f"sector:{info['sector']}", "label": f"{info['sector']} sector", "kind": "sector",
                                                      "strength": "classification", "evidence": [{"text": "Yahoo Finance sector classification"}]}
    for sec_name, w in (out["fund_sectors"] or {}).items():
        if w >= 0.25:
            label = SECTOR_LABELS.get(sec_name, sec_name.replace("_", " ").title())
            out["factors"][f"sector:{label}"] = {"key": f"sector:{label}", "label": f"{label} sector", "kind": "sector", "strength": "measured",
                                                 "evidence": [{"text": f"{w:.0%} of the fund (Yahoo Finance sector weightings)"}]}
    with _lock:
        _profiles[ticker] = (time.time(), out)
    return out


SECTOR_LABELS = {"technology": "Technology", "communication_services": "Communication Services", "consumer_cyclical": "Consumer Cyclical",
                 "consumer_defensive": "Consumer Defensive", "financial_services": "Financial Services", "healthcare": "Healthcare",
                 "industrials": "Industrials", "energy": "Energy", "utilities": "Utilities", "realestate": "Real Estate",
                 "basic_materials": "Basic Materials"}
ALL_SECTORS = list(SECTOR_LABELS.values())


def _fund_lookthrough(ticker):
    """A fund's largest holdings and sector weights (Yahoo Finance); empty when unavailable."""
    try:
        fd = yahoo.get_ticker(ticker).funds_data
        top = fd.top_holdings
        holdings = [{"ticker": str(sym).upper(), "name": str(row.get("Name") or sym), "weight": float(row.get("Holding Percent") or 0)}
                    for sym, row in top.iterrows()][:25]
        sectors = {k: float(v) for k, v in (fd.sector_weightings or {}).items() if v}
        return holdings, sectors
    except Exception:                           # noqa: BLE001
        return [], {}


# Market proxies for MEASURED macro sensitivity (price behaviour, never called a dependency)
MACRO_PROXIES = {
    "SPY": {"label": "Broad US market", "kind": "market"},
    "QQQ": {"label": "Tech & growth valuations (Nasdaq-100)", "kind": "growth", "threshold": 0.6},
    "SOXX": {"label": "Semiconductor cycle", "kind": "semis", "threshold": 0.6},
    "TLT": {"label": "Interest rates (long-term Treasury prices)", "kind": "rates", "threshold": 0.2},
    "UUP": {"label": "US dollar", "kind": "usd", "threshold": 0.25},
    "USO": {"label": "Oil prices", "kind": "oil", "threshold": 0.35},
}
_proxy_cache = {}


def _proxy_returns(sym, n):
    key = (sym, n)
    hit = _proxy_cache.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        bars = marketdata.daily_bars(sym)
        out = _returns({"dates": bars["date"][-n - 2:], "values": bars["close"][-n - 2:]}, n)
    except Exception:                           # noqa: BLE001
        out = {}
    _proxy_cache[key] = (time.time(), out)
    return out


def _sensitivities(rets, window):
    """Per holding: correlation with each proxy, and beta to the S&P 500. Measured from daily returns."""
    proxies = {sym: _proxy_returns(sym, window) for sym in MACRO_PROXIES}
    out = {}
    for h, r in rets.items():
        row = {}
        for sym, pr in proxies.items():
            c = _corr(r, pr)
            row[sym] = c
        common = sorted(set(r) & set(proxies["SPY"]))
        if len(common) >= 40:
            x, y = [proxies["SPY"][k] for k in common], [r[k] for k in common]
            vx = statistics.pvariance(x)
            mx, my = statistics.fmean(x), statistics.fmean(y)
            row["beta"] = (statistics.fmean([(a - mx) * (b - my) for a, b in zip(x, y)]) / vx) if vx else None
        else:
            row["beta"] = None
        out[h] = row
    return out


# ---------------------------------------------------------------- analysis

MATERIAL = {"disclosed", "emphasized", "measured", "classification"}
MATERIAL_LT = MATERIAL | {"look-through"}        # a fund's 5%+ position in a company that discloses the dependency
# Topics nearly every annual report lists: shown separately as "common macro exposures", never as hidden concentration.
GENERAL_POLICY = {"policy:Tax policy", "policy:Sector regulation", "policy:Antitrust & competition law"}
SPECIFIC_GEOS = {"China", "Taiwan", "Hong Kong", "Russia / Ukraine", "Israel", "Middle East", "South Korea"}


def _is_general(f):
    if f["kind"] == "macro" or f["key"] in GENERAL_POLICY:
        return True
    if f["kind"] == "geography":
        return f["key"].split(":", 1)[1] not in SPECIFIC_GEOS and f["strength"] != "disclosed"
    return False


GENERIC_FIRST = {"A", "Data-centre", "Defence", "Tariffs", "Export", "Government", "Subsidies", "Defense", "Needs", "Valuation", "Customer",
                 "Hyperscale", "Hyperscaler", "Quantum", "Electric", "Nuclear", "Crypto", "Diluting", "High", "Single"}


def _lower_first(label):
    first = label.split(" ", 1)[0]
    return label[:1].lower() + label[1:] if first in GENERIC_FIRST else label


def _group(months):
    return "short" if months <= 6 else "medium" if months <= 24 else "long"


def _returns(closes, n):
    if not closes:
        return {}
    d, v = closes["dates"][-n - 1:], closes["values"][-n - 1:]
    return {d[i]: v[i] / v[i - 1] - 1 for i in range(1, len(d))}


def _corr(a, b, min_points=40):
    common = sorted(set(a) & set(b))
    if len(common) < min_points:
        return None
    x, y = [a[k] for k in common], [b[k] for k in common]
    try:
        return statistics.correlation(x, y)
    except statistics.StatisticsError:
        return None


def _inputs(body):
    """Holdings and weights from the request: blank weights share what's left; everything scales to 100% with cash."""
    body = body or {}
    text = str(body.get("text") or "")[:6000]
    horizon_id = body.get("horizon") if body.get("horizon") in HORIZONS else "12m"
    months = int(body.get("months") or HORIZONS[horizon_id][1] or 12)
    rows = []
    for h in (body.get("holdings") or [])[:20]:
        sym = re.sub(r"[^A-Z0-9.\-]", "", str(h.get("ticker", "")).upper())[:12]
        if not sym or sym in [r["ticker"] for r in rows]:
            continue
        try:
            w = float(h.get("weight")) if h.get("weight") not in (None, "") else None
        except (TypeError, ValueError):
            w = None
        rows.append({"ticker": sym, "weight": w})
    if not rows:
        raise InvestError("Add at least one holding (a ticker).")
    cash = max(0.0, float(body.get("cash") or 0))
    given = [r["weight"] for r in rows if r["weight"] is not None]
    if not given:
        for r in rows:
            r["weight"] = (100 - cash) / len(rows)
    else:
        missing = [r for r in rows if r["weight"] is None]
        rest = max(0.0, 100 - cash - sum(given))
        for r in missing:
            r["weight"] = rest / len(missing) if missing else 0
    total = sum(r["weight"] for r in rows) + cash
    for r in rows:
        r["weight"] = r["weight"] / total * 100 if total else 0
    cash = cash / total * 100 if total else 0
    if body.get("refresh"):
        forget([r["ticker"] for r in rows])
    return text, horizon_id, months, rows, cash


def _renormalize(rows, cash):
    """After unresolvable tickers are dropped, the rest keep their proportions and fill the non-cash part again."""
    tot = sum(r["weight"] for r in rows)
    if rows and tot:
        for r in rows:
            r["weight"] = r["weight"] / tot * (100 - cash)
    return rows


def quick(body):
    """The fast first stage: names, weights, sectors and price correlation (no filings). Seconds, not tens of seconds."""
    text, horizon_id, months, rows, cash = _inputs(body)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {r["ticker"]: pool.submit(basic, r["ticker"]) for r in rows}
    basics, problems = {}, []
    for sym, fut in futures.items():
        try:
            basics[sym] = fut.result()
        except Exception as error:              # noqa: BLE001
            problems.append(str(error) if "Could not resolve" in str(error) else f"Could not resolve {sym}.")
    rows = _renormalize([r for r in rows if r["ticker"] in basics], cash)
    if not rows:
        raise InvestError("None of the holdings could be loaded. " + " ".join(problems))
    weight = {r["ticker"]: r["weight"] for r in rows}
    profiles = {s_: {**{k: v for k, v in b.items() if not k.startswith("_")}, "factors": {}} for s_, b in basics.items()}
    corr = correlation_view(profiles, weight, {}, list(weight), "1y")
    sectors = {}
    for r in rows:
        sec_name = basics[r["ticker"]].get("sector") or "Other"
        sectors[sec_name] = sectors.get(sec_name, 0) + r["weight"]
    analysis_id = secrets.token_hex(6)
    with _lock:
        _analyses[analysis_id] = (profiles, weight, {})
        while len(_analyses) > 60:
            _analyses.pop(next(iter(_analyses)))
    return {"id": analysis_id, "stage": "quick", "generated_at": now_iso(),
            "holdings": [{"ticker": r["ticker"], "weight": r["weight"], "name": basics[r["ticker"]]["name"],
                          "sector": basics[r["ticker"]].get("sector"), "industry": basics[r["ticker"]].get("industry"),
                          "fund": basics[r["ticker"]].get("fund"), "price": basics[r["ticker"]].get("price"),
                          "ret_12m": basics[r["ticker"]].get("ret_12m"), "vol_1y": basics[r["ticker"]].get("vol_1y")} for r in rows],
            "cash": cash, "corr": corr, "sectors": dict(sorted(sectors.items(), key=lambda x: -x[1])), "problems": problems}


def analyze(body):
    text, horizon_id, months, rows, cash = _inputs(body)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {r["ticker"]: pool.submit(profile, r["ticker"]) for r in rows}
    profiles, problems = {}, []
    for sym, fut in futures.items():
        try:
            profiles[sym] = fut.result()
        except Exception as error:              # noqa: BLE001
            problems.append(str(error))
    rows = _renormalize([r for r in rows if r["ticker"] in profiles], cash)
    if not rows:
        raise InvestError("None of the holdings could be loaded. " + " ".join(problems))
    weight = {r["ticker"]: r["weight"] for r in rows}
    n = len(rows)
    group = _group(months)

    # hidden exposures: factor → holders
    factors = {}
    for sym, prof in profiles.items():
        for f in prof["factors"].values():
            row = factors.setdefault(f["key"], {"key": f["key"], "label": f["label"], "kind": f["kind"], "holders": [], "weight": 0.0,
                                                "material_weight": 0.0, "general": _is_general(f)})
            row["holders"].append({"ticker": sym, "strength": f["strength"], "evidence": f["evidence"]})
            row["weight"] += weight[sym]
            if f["strength"] in MATERIAL:
                row["material_weight"] += weight[sym]
    # ---- fund look-through overlap
    lookthrough = []
    direct = {r["ticker"]: r["weight"] for r in rows}
    for r in rows:
        prof = profiles[r["ticker"]]
        for h in prof.get("fund_holdings") or []:
            if h["ticker"] in direct and h["ticker"] != r["ticker"] and h["weight"] >= 0.02:
                lookthrough.append({"fund": r["ticker"], "ticker": h["ticker"], "fund_weight": h["weight"],
                                    "portfolio_weight": r["weight"] * h["weight"]})
                # the fund inherits the direct holding's material dependencies (labelled as look-through)
                for k, f in profiles[h["ticker"]]["factors"].items():
                    if f["strength"] in MATERIAL and not _is_general(f) and k in factors and not k.startswith(("fund:", "industry:", "sector:")):
                        row = factors[k]
                        if not any(x["ticker"] == r["ticker"] for x in row["holders"]):
                            strong = h["weight"] >= 0.05          # a 5%+ position in the fund counts as material look-through
                            row["holders"].append({"ticker": r["ticker"], "strength": "look-through" if strong else "mentioned",
                                                   "evidence": [{"text": f"{r['ticker']} holds {h['ticker']} ({h['weight']:.1%} of the fund)"}]})
                            row["weight"] += r["weight"]
                            if strong:
                                row["material_weight"] += r["weight"]
    effective = {}
    for lt in lookthrough:
        effective.setdefault(lt["ticker"], direct[lt["ticker"]])
        effective[lt["ticker"]] += lt["portfolio_weight"]

    concentrations, common = [], []
    for f in factors.values():
        mat = [h for h in f["holders"] if h["strength"] in MATERIAL_LT]
        if len(mat) < 2 or not (f["material_weight"] >= 50 or (n >= 3 and len(mat) >= n - 1)):
            continue
        if f["general"]:
            common.append(f["label"])
            continue
        if f["kind"] == "industry":
            text = f"{len(mat)} of your {n} holdings are in the same industry ({f['label']}): one industry cycle drives {f['material_weight']:.0f}% of the portfolio."
        elif f["kind"] == "financial":
            text = f"{len(mat)} of your {n} holdings share this trait: {_lower_first(f['label'])} ({f['material_weight']:.0f}% of the portfolio)."
        else:
            text = (f"You hold {n} {'investments' if n != 1 else 'investment'}, but {len(mat)} depend materially on {_lower_first(f['label'])} "
                    f"({f['material_weight']:.0f}% of the portfolio).")
        concentrations.append({"key": f["key"], "label": f["label"], "kind": f["kind"], "count": len(mat), "weight": f["material_weight"],
                               "holders": [h["ticker"] for h in mat], "text": text})
    rank = {"theme": 0, "customer": 0, "supplier": 0, "geography": 1, "policy": 1, "partner": 2, "industry": 2, "financial": 3}
    concentrations.sort(key=lambda c: (rank.get(c["kind"], 4), -c["weight"], -c["count"]))
    graph_factors = sorted([f for f in factors.values() if not f["general"] and (len(f["holders"]) >= 2 or f["material_weight"] >= 30)],
                           key=lambda f: (-f["material_weight"], -len(f["holders"])))[:14]
    graph = {"holdings": [{"ticker": r["ticker"], "weight": r["weight"], "name": profiles[r["ticker"]]["name"]} for r in rows],
             "cash": cash,
             "factors": [{"key": f["key"], "label": f["label"], "kind": f["kind"], "weight": f["material_weight"],
                          "links": [{"ticker": h["ticker"], "strength": h["strength"]} for h in f["holders"]]} for f in graph_factors]}

    # correlation vs dependency overlap
    window = {"short": 126, "medium": 252, "long": 756}[group]
    rets = {sym: _returns(prof.get("closes"), window) for sym, prof in profiles.items()}
    material = {sym: {k for k, f in prof["factors"].items() if f["strength"] in MATERIAL and not k.startswith("industry:")
                      and not _is_general(f)} for sym, prof in profiles.items()}
    pairs = []
    syms = [r["ticker"] for r in rows]
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            a, b = syms[i], syms[j]
            c = _corr(rets[a], rets[b])
            union = material[a] | material[b]
            shared = material[a] & material[b]
            overlap = len(shared) / len(union) if union else 0.0
            same_industry = profiles[a].get("industry") and profiles[a].get("industry") == profiles[b].get("industry")
            high = c is not None and c >= 0.6
            if overlap >= 0.3:
                kind, text = ("same_bet", "Move together AND share business dependencies: effectively the same bet.") if high else \
                             ("hidden", "Share business dependencies without a strong price link: diversified on a chart, not in the business.")
            else:
                kind, text = ("market", "Move together without shared stated dependencies (market / style / sentiment).") if high else \
                             ("independent", "Relatively independent on both measures.")
            pairs.append({"a": a, "b": b, "correlation": c, "overlap": overlap, "same_industry": bool(same_industry),
                          "shared": sorted(factors[k]["label"] for k in shared if k in factors), "kind": kind, "text": text})
    corr_vals = [p["correlation"] for p in pairs if p["correlation"] is not None]
    vols = {s: profiles[s].get("vol_1y") for s in syms}
    div_ratio = None
    if all(vols.values()) and len(syms) > 1 and all(p["correlation"] is not None for p in pairs):
        w = {s: weight[s] / 100 for s in syms}
        var = sum(w[s] ** 2 * vols[s] ** 2 for s in syms)
        for p in pairs:
            var += 2 * w[p["a"]] * w[p["b"]] * vols[p["a"]] * vols[p["b"]] * p["correlation"]
        if var > 0:
            div_ratio = sum(w[s] * vols[s] for s in syms) / math.sqrt(var)
    correlation = {"window_days": window, "pairs": pairs, "average": statistics.fmean(corr_vals) if corr_vals else None,
                   "diversification_ratio": div_ratio, "matrix": {f"{p['a']}|{p['b']}": p["correlation"] for p in pairs}}

    # ---- measured macro sensitivity (price behaviour vs market proxies)
    sens = _sensitivities(rets, window)
    macro_measured = []
    for psym, meta_p in MACRO_PROXIES.items():
        if psym == "SPY":
            continue
        th = meta_p["threshold"]
        hits = [(s, sens[s][psym]) for s in syms if sens.get(s, {}).get(psym) is not None and
                (abs(sens[s][psym]) >= th if meta_p["kind"] == "usd" else sens[s][psym] >= th)]
        if hits:
            macro_measured.append({"key": f"measured:{meta_p['kind']}", "label": meta_p["label"], "kind": "measured", "proxy": psym,
                                   "holders": [{"ticker": s, "strength": "measured", "value": v} for s, v in hits],
                                   "weight": sum(weight[s] for s, _ in hits), "count": len(hits),
                                   "text": f"{len(hits)} of {n} move closely with {meta_p['label'][0].lower() + meta_p['label'][1:]} ({psym}; correlation of daily returns ≥ {th:g})"})
    rate_channel = [s for s in syms if any(k in profiles[s]["factors"] for k in ("fund:high_valuation", "fund:burning_cash", "fund:high_debt"))]

    # ---- four lenses: dependency / thematic / macro / market correlation
    def lens_items(kinds, include_general=False):
        items = []
        for f in factors.values():
            if f["kind"] not in kinds or (f["general"] and not include_general):
                continue
            mat = [h for h in f["holders"] if h["strength"] in MATERIAL_LT]
            if len(mat) < 2:
                continue
            items.append({"key": f["key"], "label": f["label"], "kind": f["kind"], "count": len(mat), "weight": sum(weight[h["ticker"]] for h in mat),
                          "holders": [{"ticker": h["ticker"], "strength": h["strength"]} for h in f["holders"]], "general": f["general"]})
        return sorted(items, key=lambda x: (-x["count"], -x["weight"]))
    lenses = {
        "dependency": lens_items(("supplier", "customer", "partner", "geography", "policy")),
        "thematic": lens_items(("theme", "industry", "sector")),
        "macro": macro_measured + [dict(x, label=x["label"]) for x in lens_items(("financial", "macro", "policy"), include_general=True)
                                   if x["kind"] != "policy" or x["general"]],
    }

    # ---- summary first: "5 holdings, but …"
    summary = []
    merged = []
    for c in concentrations:                    # identical holder sets read as one line ("AI demand and data-centre build-out")
        twin = next((m for m in merged if set(m["holders"]) == set(c["holders"]) and m["kind"] not in ("industry", "financial")
                     and c["kind"] not in ("industry", "financial")), None)
        if twin:
            twin["labels"].append(c["label"])
        else:
            merged.append({**c, "labels": [c["label"]]})
    def join_names(labels):
        names = [_lower_first(x.split(" (")[0]) if not x.startswith("A few") else "a few large customers" for x in labels[:3]]
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    for c in merged[:3]:
        verb = "share a trait:" if c["kind"] == "financial" else "are in" if c["kind"] in ("industry", "sector") else "depend materially on"
        summary.append({"count": c["count"], "of": n, "kind": c["kind"], "key": c["key"], "holders": c["holders"],
                        "text": f"{verb[0].upper() + verb[1:]} {join_names(c['labels'])} — {c['weight']:.0f}% of the basket.", "long": c["text"]})
    for m in sorted(macro_measured, key=lambda m: -m["count"]):
        if m["count"] >= max(2, n - 1) and len(summary) < 5:
            summary.append({"count": m["count"], "of": n, "kind": "measured", "key": m["key"], "long": m["text"] + ".",
                            "text": f"Move closely with {m['label'][0].lower() + m['label'][1:]} — measured from prices, not a business dependency."})
    if len(rate_channel) >= 2 and len(summary) < 5:
        summary.append({"count": len(rate_channel), "of": n, "kind": "financial", "key": "rates_channel",
                        "text": "Have a direct rates channel: priced on far-future growth, burning cash or carrying high debt."})
    for t, eff in sorted(effective.items(), key=lambda x: -x[1])[:1]:
        funds = [lt for lt in lookthrough if lt["ticker"] == t]
        if eff - direct[t] < 1.5:
            continue
        summary.append({"count": None, "of": n, "kind": "lookthrough", "key": f"lt:{t}",
                        "text": f"{t} is really ≈{eff:.1f}% of the basket: {direct[t]:.0f}% directly plus "
                                + ", ".join(f"{lt['portfolio_weight']:.1f}% through {lt['fund']}" for lt in funds) + "."})

    # ---- blind spots (descriptive, never prescriptive)
    blind = []
    sector_w = {}
    for r in rows:
        prof = profiles[r["ticker"]]
        if prof.get("fund_sectors"):
            for k, v in prof["fund_sectors"].items():
                lab = SECTOR_LABELS.get(k, k)
                sector_w[lab] = sector_w.get(lab, 0) + r["weight"] * v
        elif prof.get("sector"):
            sector_w[prof["sector"]] = sector_w.get(prof["sector"], 0) + r["weight"]
    if sector_w and n >= 2:
        top_sector, top_w = max(sector_w.items(), key=lambda x: x[1])
        if top_w >= 60:
            blind.append({"kind": "sector", "text": f"About {top_w:.0f}% of the basket (including what the funds hold) sits in one sector: {top_sector}."})
        missing = [x for x in ALL_SECTORS if sector_w.get(x, 0) < 1]
        if len(missing) >= 5:
            blind.append({"kind": "coverage", "text": f"Little or no exposure to: {', '.join(missing)}."})
    theme_groups = {}
    for c in concentrations:
        if c["kind"] == "theme" and c["count"] >= 3:
            theme_groups.setdefault(tuple(sorted(c["holders"])), []).append(c["label"])
    for holders, labels in theme_groups.items():
        blind.append({"kind": "theme", "text": f"{len(holders)} holdings derive substantial value from the same theme ({join_names(labels)})."})
    for c in concentrations:
        if c["kind"] == "geography" and c["count"] >= 2:
            blind.append({"kind": "geography", "text": f"{c['count']} holdings depend on the same geography ({c['label'].split(' (')[0]}) in their own filings."})
        if c["key"] == "concentration:customer" and c["count"] >= 2:
            blind.append({"kind": "counterparty", "text": f"{c['count']} holdings disclose that a few customers make up a large share of revenue."})
        elif c["kind"] in ("supplier", "customer") and c["count"] >= 2:
            blind.append({"kind": "counterparty", "text": f"{c['count']} holdings rely on the same counterparty or customer group ({_lower_first(c['label'])})."})
    avg = statistics.fmean(corr_vals) if corr_vals else None
    if avg is not None and avg >= 0.5 and n >= 3:
        blind.append({"kind": "regime", "text": f"Average price correlation between holdings is {avg:.2f}: most of the apparent diversification sits within one market regime."})
    betas = [sens[s].get("beta") for s in syms if sens.get(s, {}).get("beta") is not None]
    if len(betas) >= 2 and min(betas) > 1.1:
        blind.append({"kind": "beta", "text": f"Every holding has an above-market beta (lowest {min(betas):.2f}): market drawdowns are likely to hit all of them."})
    unprofitable = [s for s in syms if (profiles[s].get("operating_margin") or 0) < 0]
    if len(unprofitable) >= 2 and len(unprofitable) >= n - 1:
        blind.append({"kind": "funding", "text": f"{len(unprofitable)} of {n} holdings aren't profitable yet: the basket depends on capital markets staying open."})
    if lookthrough:
        blind.append({"kind": "overlap", "text": "Funds in the basket already hold " + ", ".join(sorted({lt['ticker'] for lt in lookthrough}))
                      + ": owning them directly as well adds to the same position."})

    stress = _stress(text, rows, profiles, factors, group)
    deps_view = dependencies_view(profiles, weight, factors, syms)
    themes_v = themes_view(profiles, weight, factors, syms, text)
    macro_v = macro_view(profiles, weight, factors, syms, rets, sens, window)
    corr_default = correlation_view(profiles, weight, factors, syms, "1y")
    scenario_results = [scenario(s, profiles, weight, factors) for s in SCENARIOS]
    scenario_results = [s for s in scenario_results if s["exposed"]] + [s for s in scenario_results if not s["exposed"]]
    holdings = []
    for r in rows:
        p = profiles[r["ticker"]]
        holdings.append({k: v for k, v in p.items() if k not in ("closes", "factors", "fund_holdings")} | {"fund_top": (p.get("fund_holdings") or [])[:8],
            "weight": r["weight"], "factor_count": sum(1 for f in p["factors"].values() if f["strength"] in MATERIAL)})
    analysis_id = secrets.token_hex(6)
    out = {"id": analysis_id, "generated_at": now_iso(), "horizon": {"id": horizon_id, "months": months, "group": group,
                                                                       "label": HORIZONS[horizon_id][0] if horizon_id != "custom" else f"{months} months"},
           "emphasis": EMPHASIS[group], "holdings": holdings, "cash": cash, "concentrations": concentrations, "graph": graph,
           "common_macro": sorted(set(common)),
           "factors": sorted(factors.values(), key=lambda f: -f["material_weight"]), "correlation": correlation, "stress": stress,
           "scenarios": scenario_results, "problems": problems, "thesis": parse_thesis(text) if text else None,
           "dependencies": deps_view, "themes": themes_v, "macro": macro_v, "corr": corr_default,
           "overview": {"n": n, "shared_themes": sum(1 for t in themes_v["themes"] if t["shared"]),
                        "shared_dependencies": sum(1 for d in deps_view["shared"] if d["major"]),
                        "average_correlation": corr_default.get("average"),
                        "largest": (concentrations[0]["label"] if concentrations else None),
                        "largest_count": (concentrations[0]["count"] if concentrations else None)},
           "summary": summary, "lenses": lenses, "blind_spots": blind, "sensitivities": sens, "lookthrough": lookthrough,
           "effective_weights": effective, "sectors": dict(sorted(sector_w.items(), key=lambda x: -x[1])),
           "note": "Research, not advice. Dependencies come from each company's latest annual report (quoted), classifications and "
                   "reported numbers; nothing is inferred from general knowledge. Scenarios show exposure pathways, not predicted returns."}
    with _lock:
        _analyses[analysis_id] = (profiles, weight, factors)
        _corr_cache.clear() if len(_corr_cache) > 200 else None
        while len(_analyses) > 60:
            _analyses.pop(next(iter(_analyses)))
    return out


# ---------------------------------------------------------------- the four lenses, in full

DEP_CATEGORY = [("concentration:", "Customer concentration"), ("customer:hyperscalers", "Customers"), ("customer:us_gov", "Government contracts"),
                ("policy:Government contracts", "Government contracts"), ("geo:", "Geography"), ("policy:", "Regulation & policy")]
STRENGTH_RANK = {"disclosed": 4, "emphasized": 3, "measured": 3, "classification": 2, "look-through": 2, "mentioned": 1}


def _dep_category(key, f):
    for prefix, cat in DEP_CATEGORY:
        if key.startswith(prefix):
            return cat
    if f["kind"] == "supplier":
        return "Cloud & infrastructure" if "cloud" in f["label"].lower() else "Suppliers & manufacturing"
    if f["kind"] == "customer":
        return "Customers"
    if f["kind"] == "partner":
        return "Partners & distribution"
    return "Other"


DEP_WHY = {
    "Suppliers & manufacturing": "named as a supplier or manufacturer: a capacity, pricing or disruption problem there reaches every holding listed.",
    "Cloud & infrastructure": "named as cloud / infrastructure the business runs on.",
    "Customers": "named as a customer: these holdings sell into the same demand.",
    "Customer concentration": "disclosed as a large share of revenue from a few customers: losing or pricing pressure from one customer moves results.",
    "Partners & distribution": "named as a partner or sales channel.",
    "Geography": "discussed for sales, customers or manufacturing in this region: local demand, trade rules or disruption affect all of them.",
    "Government contracts": "government funding or contracts are part of the business.",
    "Regulation & policy": "a policy topic the filings discuss repeatedly: a rule change here touches each of them.",
}


def dependencies_view(profiles, weight, factors, syms):
    """Shared dependencies first (2+ holdings), then each holding's own list — all from filings, with provenance."""
    kinds = ("supplier", "customer", "partner", "geography", "policy")
    shared, per = [], {s: [] for s in syms}
    for key, f in factors.items():
        if f["kind"] not in kinds:
            continue
        cat = _dep_category(key, f)
        holders = sorted(f["holders"], key=lambda h: -STRENGTH_RANK.get(h["strength"], 0))
        for h in holders:
            per.setdefault(h["ticker"], []).append({"key": key, "label": f["label"], "category": cat, "strength": h["strength"],
                                                    "evidence": h.get("evidence", [])[:3], "general": f["general"]})
        if len({h["ticker"] for h in holders}) >= 2:
            mat = [h for h in holders if h["strength"] in MATERIAL_LT]
            shared.append({"key": key, "label": f["label"], "category": cat, "general": f["general"], "major": len(mat) >= 2 and not f["general"],
                           "holders": [{"ticker": h["ticker"], "strength": h["strength"], "evidence": h.get("evidence", [])[:2]} for h in holders],
                           "material_count": len(mat), "weight": sum(weight[h["ticker"]] for h in mat),
                           "why": (f"{len(holders)} holdings disclose that a few customers make up a large share of revenue: losing one, or its pricing pressure, moves results."
                                   if cat == "Customer concentration" else
                                   f"{len(holders)} holdings — {f['label'].split(' (')[0]} is " + DEP_WHY.get(cat, "shared."))})
    cat_order = ["Suppliers & manufacturing", "Customers", "Customer concentration", "Cloud & infrastructure", "Partners & distribution",
                 "Geography", "Government contracts", "Regulation & policy", "Other"]
    shared.sort(key=lambda d: (d["general"], not d["major"], cat_order.index(d["category"]), -d["material_count"], -d["weight"]))
    for s_ in per:
        per[s_].sort(key=lambda d: (d["general"], -STRENGTH_RANK.get(d["strength"], 0)))
    states = {s_: profiles[s_].get("exposure_state") or {} for s_ in syms}
    return {"shared": shared, "per_holding": per, "states": states,
            "available": any(per[s_] for s_ in syms),
            "note": "From each company's latest annual report (SEC EDGAR), quoted. Funds inherit a holding's dependencies only through 5%+ positions (look-through)."}


THEME_LEVEL_RULES = {
    "high": "HIGH — the annual report discusses it 15+ times, or it is the company's industry / a 25%+ fund sector",
    "moderate": "MODERATE — discussed 5–14 times in the annual report, or the sector classification",
    "indirect": "INDIRECT — only through a fund's 5%+ position in a company that discloses it",
}


def themes_view(profiles, weight, factors, syms, text):
    out = []
    for key, f in factors.items():
        if f["kind"] not in ("theme", "industry", "sector"):
            continue
        members = []
        for h in f["holders"]:
            ev = h.get("evidence") or []
            n_ment = next((e.get("mentions") for e in ev if e.get("mentions")), None)
            if h["strength"] == "look-through":
                level = "indirect"
            elif f["kind"] == "industry" or (n_ment and n_ment >= 15) or (f["kind"] == "sector" and h["strength"] == "measured"):
                level = "high"
            elif f["kind"] == "sector" or (n_ment and n_ment >= 5):
                level = "moderate"
            else:
                level = "indirect" if h["strength"] == "mentioned" and not n_ment else "moderate"
            members.append({"ticker": h["ticker"], "level": level, "evidence": ev[:2], "mentions": n_ment})
        if not members:
            continue
        direct = [m for m in members if m["level"] in ("high", "moderate")]
        out.append({"key": key, "label": f["label"], "kind": f["kind"], "members": sorted(members, key=lambda m: ["high", "moderate", "indirect"].index(m["level"])),
                    "shared": len(direct) >= 2 and f["kind"] != "sector", "count": len(direct),
                    "weight": sum(weight[m["ticker"]] for m in direct),
                    "concentration": (f"{len(direct)} of {len(syms)} holdings ({sum(weight[m['ticker']] for m in direct):.0f}% of the basket) express this theme."
                                      if len(direct) >= 2 else None)})
    order = {"theme": 0, "industry": 1, "sector": 2}
    out.sort(key=lambda t: (not t["shared"], order[t["kind"]], -t["count"], -t["weight"]))
    thesis = parse_thesis(text)["themes"] if text else []
    return {"themes": out, "rules": THEME_LEVEL_RULES, "thesis_themes": thesis,
            "note": "Themes come from how often each annual report discusses them, plus Yahoo Finance industry / sector classifications and fund sector weights. Levels follow the rules shown; no percentages are invented."}


MACRO_VARS = [
    {"id": "rates", "label": "Interest rates & yields", "filing": ["macro:Interest rates"], "traits": ["fund:high_valuation", "fund:burning_cash", "fund:high_debt"],
     "proxy": "TLT", "proxy_label": "long-term Treasury prices (TLT)",
     "fundamental": "Long-duration growth valuations, cash-burning companies and indebted companies are the usual transmission channels.",
     "measured_note": "Positive correlation with TLT means the holding tended to fall when long yields rose."},
    {"id": "usd", "label": "US dollar", "filing": ["macro:Currency / U.S. dollar"], "traits": [], "proxy": "UUP", "proxy_label": "the US dollar index fund (UUP)",
     "fundamental": "Companies with large non-US sales report currency translation risk.", "measured_note": "Negative correlation: the holding tended to fall when the dollar rose."},
    {"id": "growth", "label": "Economic cycle", "filing": ["macro:Economic cycle", "macro:Industry cyclicality"], "traits": [], "proxy": "SPY", "proxy_label": "the S&P 500 (beta)",
     "fundamental": "Filings that discuss recession risk or cyclicality.", "measured_note": "Beta above 1: moved more than the market."},
    {"id": "capex", "label": "Enterprise & AI capex", "filing": ["macro:Business / IT spending"], "themes": ["theme:ai", "theme:data_center", "customer:hyperscalers"],
     "traits": [], "proxy": "QQQ", "proxy_label": "tech & growth stocks (QQQ)",
     "fundamental": "Revenue depends on companies' technology / data-centre spending.", "measured_note": "Correlation with the Nasdaq-100."},
    {"id": "semis", "label": "Semiconductor cycle", "filing": [], "industries": ["Semiconductors", "Semiconductor Equipment & Materials"], "traits": [], "proxy": "SOXX",
     "proxy_label": "semiconductor stocks (SOXX)", "fundamental": "Chip companies and their suppliers move with industry inventory and capex cycles.",
     "measured_note": "Correlation with the semiconductor index."},
    {"id": "consumer", "label": "Consumer spending", "filing": ["macro:Consumer spending"], "traits": [], "proxy": None,
     "fundamental": "Filings that tie demand to consumer spending or confidence.", "measured_note": None},
    {"id": "inflation", "label": "Inflation", "filing": ["macro:Inflation"], "traits": [], "proxy": None,
     "fundamental": "Filings that discuss input-cost or wage inflation.", "measured_note": None},
    {"id": "commodities", "label": "Oil & commodities", "filing": ["macro:Commodities / energy"], "traits": [], "proxy": "USO", "proxy_label": "oil prices (USO)",
     "fundamental": "Filings that discuss commodity or energy costs.", "measured_note": "Correlation with oil prices."},
    {"id": "credit", "label": "Credit & funding", "filing": ["macro:Credit / financing"], "traits": ["fund:burning_cash", "fund:dilution"], "proxy": None,
     "fundamental": "Companies that need outside capital depend on open credit and equity markets.", "measured_note": None},
]


def _portfolio_returns(rets, weight, syms):
    common = None
    for s_ in syms:
        common = set(rets.get(s_) or {}) if common is None else common & set(rets.get(s_) or {})
    if not common:
        return {}
    tot = sum(weight[s_] for s_ in syms) or 1
    return {d: sum(weight[s_] / tot * rets[s_][d] for s_ in syms) for d in common}


def macro_view(profiles, weight, factors, syms, rets, sens, window):
    port = _portfolio_returns(rets, weight, syms)
    out = []
    for v in MACRO_VARS:
        fund = []
        for s_ in syms:
            pf = profiles[s_]["factors"]
            basis = []
            for k in v["filing"]:
                if k in pf:
                    ev = pf[k]["evidence"][:1]
                    basis.append({"text": f"{pf[k]['label']} discussed in the annual report", "evidence": ev})
            for k in v.get("traits", []):
                if k in pf:
                    basis.append({"text": pf[k]["label"], "evidence": pf[k]["evidence"][:1]})
            for k in v.get("themes", []):
                if k in pf and pf[k]["strength"] in MATERIAL:
                    basis.append({"text": pf[k]["label"], "evidence": pf[k]["evidence"][:1]})
            if profiles[s_].get("industry") in v.get("industries", []):
                basis.append({"text": f"Industry: {profiles[s_]['industry']}", "evidence": [{"text": "Yahoo Finance industry classification", "type": "Classification (Yahoo Finance)"}]})
            if basis:
                fund.append({"ticker": s_, "basis": basis})
        measured = None
        if v.get("proxy"):
            per = {s_: (sens.get(s_) or {}).get(v["proxy"] if v["id"] != "growth" else "beta") for s_ in syms}
            per = {k: x for k, x in per.items() if x is not None}
            pr = _proxy_returns(v["proxy"], window)
            pc = _corr(port, pr) if port and pr else None
            pbeta = None
            common = sorted(set(port) & set(pr))
            if len(common) >= 40:
                x, y = [pr[d] for d in common], [port[d] for d in common]
                vx = statistics.pvariance(x)
                mx, my = statistics.fmean(x), statistics.fmean(y)
                pbeta = statistics.fmean([(a - mx) * (b - my) for a, b in zip(x, y)]) / vx if vx else None
            if per or pc is not None:
                ranked = sorted(per.items(), key=lambda kv: -abs(kv[1]) if v["id"] != "usd" else kv[1])
                meaningful = (lambda x: x > 1.2) if v["id"] == "growth" else (lambda x: abs(x) >= 0.2)
                ranked = [kv for kv in ranked if meaningful(kv[1])]
                measured = {"proxy": v["proxy"], "proxy_label": v.get("proxy_label"), "metric": "beta" if v["id"] == "growth" else "correlation",
                            "per_holding": per, "portfolio_correlation": pc, "portfolio_beta": pbeta, "window_days": window,
                            "most_sensitive": [k for k, _ in ranked[:3]], "note": v.get("measured_note"),
                            "threshold": "beta above 1.2" if v["id"] == "growth" else "|correlation| ≥ 0.2"}
        if not fund and not measured:
            continue
        out.append({"id": v["id"], "label": v["label"], "fundamental": {"text": v["fundamental"], "holders": fund},
                    "measured": measured, "shared": len(fund) >= 2,
                    "count": len(fund)})
    out.sort(key=lambda m: (not m["shared"], -m["count"]))
    return {"variables": out, "window_days": window,
            "note": "Fundamental exposure comes from filings and reported financials. Measured sensitivity is the correlation (or beta) of daily returns "
                    "with a market proxy — how prices moved together, not proof of cause."}


CORR_WINDOWS = {"1m": 21, "3m": 63, "6m": 126, "1y": 252, "3y": 756, "max": 2600}
_corr_cache = {}


def correlation_view(profiles, weight, factors, syms, window_id="1y"):
    n_days = CORR_WINDOWS.get(window_id, 252)
    key = (tuple(sorted(syms)), window_id, tuple(profiles[s_]["closes"]["dates"][-1] if profiles[s_].get("closes") else "" for s_ in sorted(syms)))
    if key in _corr_cache:
        return _corr_cache[key]
    rets = {s_: _returns(profiles[s_].get("closes"), n_days) for s_ in syms}
    missing = [s_ for s_ in syms if not rets[s_]]
    usable = [s_ for s_ in syms if rets[s_]]
    matrix, pairs = {}, []
    common_all = None
    for s_ in usable:
        common_all = set(rets[s_]) if common_all is None else common_all & set(rets[s_])
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            a, b = usable[i], usable[j]
            c = _corr(rets[a], rets[b], min_points=min(40, max(15, int(n_days * 0.7))))
            n_common = len(set(rets[a]) & set(rets[b]))
            matrix[f"{a}|{b}"] = c
            if c is not None:
                pa, pb = profiles[a]["factors"], profiles[b]["factors"]
                shared = sorted(pa[k]["label"] for k in set(pa) & set(pb)
                                if pa[k]["strength"] in MATERIAL and pb[k]["strength"] in MATERIAL and not _is_general(pa[k])
                                and not k.startswith(("industry:", "sector:")))
                pairs.append({"a": a, "b": b, "correlation": c, "days": n_common, "shared": shared[:6]})
    vals = [p["correlation"] for p in pairs]
    # clusters: holdings linked by correlation ≥ 0.7 (single linkage)
    parent = {s_: s_ for s_ in usable}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x
    for p in pairs:
        if p["correlation"] >= 0.7:
            parent[find(p["a"])] = find(p["b"])
    groups = {}
    for s_ in usable:
        groups.setdefault(find(s_), []).append(s_)
    clusters = [sorted(g) for g in groups.values() if len(g) >= 2]
    all_dates = sorted(set().union(*[set(rets[s_]) for s_ in usable])) if usable else []
    short = [s_ for s_ in usable if len(rets[s_]) < 0.8 * min(n_days, max(len(r) for r in rets.values() if r))]
    out = {"window": window_id, "window_days": n_days, "matrix": matrix, "symbols": usable, "missing": missing, "short_history": short,
           "average": statistics.fmean(vals) if vals else None,
           "highest": sorted(pairs, key=lambda p: -p["correlation"])[:5], "lowest": sorted(pairs, key=lambda p: p["correlation"])[:5],
           "clusters": clusters, "pairs": pairs,
           "range": {"from": all_dates[0] if all_dates else None, "to": all_dates[-1] if all_dates else None,
                     "days": len(common_all) if common_all else 0, "max_pair_days": max((p["days"] for p in pairs), default=0)},
           "enough": len(pairs) > 0,
           "source": "Yahoo Finance daily closes (adjusted for splits and dividends)", "frequency": "daily returns",
           "note": "Market correlation measures how prices moved historically. Hidden dependencies can still exist even when correlation is low."}
    _corr_cache[key] = out
    return out


def correlation_for(analysis_id, window_id):
    with _lock:
        stored = _analyses.get(analysis_id)
    if not stored:
        raise InvestError("Run the analysis again first (it expired).")
    profiles, weight, factors = stored
    if window_id not in CORR_WINDOWS:
        raise InvestError("Unknown lookback.")
    return correlation_view(profiles, weight, factors, list(weight), window_id)


def scenario(spec, profiles, weight, factors):
    keys = spec["factors"]
    exposed = []
    for sym, prof in profiles.items():
        hits = [prof["factors"][k] for k in keys if k in prof["factors"]]
        if hits:
            strongest = max(hits, key=lambda f: {"disclosed": 3, "emphasized": 2, "measured": 2, "classification": 2, "mentioned": 1}[f["strength"]])
            exposed.append({"ticker": sym, "weight": weight[sym], "pathways": [{"label": f["label"], "strength": f["strength"],
                                                                                "evidence": f["evidence"][:1]} for f in hits],
                            "strength": strongest["strength"]})
    exposed.sort(key=lambda e: -e["weight"])
    material = [e for e in exposed if e["strength"] in MATERIAL]
    return {"id": spec.get("id"), "label": spec["label"], "why": spec.get("why"), "exposed": exposed,
            "material_weight": sum(e["weight"] for e in material), "any_weight": sum(e["weight"] for e in exposed),
            "unexposed": [s for s in profiles if s not in {e["ticker"] for e in exposed}]}


def custom_scenario(analysis_id, text, body=None):
    with _lock:
        stored = _analyses.get(analysis_id)
    if not stored and isinstance(body, dict) and body.get("holdings"):
        # The server restarted or the analysis aged out: rebuild it from the same inputs (profiles are cached).
        analysis_id = analyze(body)["id"]
        with _lock:
            stored = _analyses.get(analysis_id)
    if not stored:
        raise InvestError("Run the analysis again first (it expired).")
    profiles, weight, factors = stored
    keys = []
    for pattern, ks in SCENARIO_WORDS:
        if re.search(pattern, (text or "").lower()):
            keys += [k for k in ks if k not in keys]
    for k, f in factors.items():                 # any factor named directly ("what if Samsung…")
        name = f["label"].split(" (")[0].lower()
        if len(name) > 3 and name in (text or "").lower() and k not in keys:
            keys.append(k)
    if not keys:
        return {"label": text, "exposed": [], "unexposed": list(profiles), "material_weight": 0, "any_weight": 0,
                "why": "MarketLab couldn't link this scenario to any dependency it tracks (suppliers, customers, geographies, policy, "
                       "funding, themes). Try naming a company, country, policy or theme."}
    out = scenario({"id": "custom", "label": text, "factors": keys}, profiles, weight, factors)
    out["why"] = "Matched to: " + ", ".join(factors[k]["label"] if k in factors else k.split(":", 1)[1] for k in keys)
    return out


def _stress(text, rows, profiles, factors, group):
    must, breaks = [], []
    parsed = parse_thesis(text) if text else {"themes": []}
    if not parsed["themes"]:                    # no thesis written: use the themes the holdings' own filings share
        shared = {k for k, f in factors.items() if k.startswith("theme:") and sum(1 for h in f["holders"] if h["strength"] in MATERIAL_LT) >= 2}
        mapping = {"theme:ai": "ai_infra", "theme:data_center": "ai_infra", "theme:hyperscale": "cloud", "theme:quantum": "quantum",
                   "theme:nuclear": "nuclear", "theme:ev": "ev"}
        ids = []
        for k in sorted(shared):
            if mapping.get(k) and mapping[k] not in ids:
                ids.append(mapping[k])
        parsed = {"themes": [{"id": i, "label": THEMES[i]["label"]} for i in ids]}
    for t in parsed["themes"]:
        for line in THEMES[t["id"]]["must"]:
            must.append({"text": line, "basis": f"{t['label']} thesis", "evidence": []})
    burners = [s for s, p in profiles.items() if p["fundamentals"].get("burning_cash")]
    if burners:
        ev = []
        for s in burners:
            f = profiles[s]["fundamentals"]
            ev.append(f"{s}: free cash flow {f['free_cash_flow'] / 1e6:,.0f}M" + (f", ≈ {f['runway_years']:.1f} years of cash at that rate" if f.get("runway_years") else ""))
        must.append({"text": "Cash-burning holdings can fund themselves until they turn profitable", "basis": "reported cash flow", "evidence": ev})
        short = [s for s in burners if (profiles[s]["fundamentals"].get("runway_years") or 99) < 2]
        breaks.append({"kind": "capital", "label": "Capital requirements", "holdings": burners,
                       "text": "Negative free cash flow" + (f"; under 2 years of cash at the current rate: {', '.join(short)}" if short else ""), "evidence": ev})
    dil = [(s, p["dilution_1y"]) for s, p in profiles.items() if p.get("dilution_1y") and p["dilution_1y"] > 0.1]
    if dil:
        must.append({"text": "Dilution stays manageable", "basis": "SEC share counts", "evidence": [f"{s}: shares {v:+.0%} in a year" for s, v in dil]})
        breaks.append({"kind": "dilution", "label": "Dilution", "holdings": [s for s, _ in dil],
                       "text": "Share count already rising more than 10% a year", "evidence": [f"{s}: {v:+.0%}" for s, v in dil]})
    growth = [s for s, p in profiles.items() if (p.get("revenue_growth") or 0) > 0.2]
    if growth:
        must.append({"text": "Revenue keeps growing fast enough to support today's valuations", "basis": "reported growth & valuation",
                     "evidence": [f"{s}: revenue growth {profiles[s]['revenue_growth']:+.0%} (latest year-over-year quarter)" for s in growth]})
    margins = [(s, p["gross_margin"]) for s, p in profiles.items() if p.get("gross_margin") is not None and not p.get("fund")]
    if margins:
        must.append({"text": "Margins hold up as competition and supply costs change", "basis": "reported margins",
                     "evidence": [f"{s}: gross margin {m:.0%}" for s, m in margins]})
    supply = [s for s, p in profiles.items() if "entity:TSMC" in p["factors"] or "geo:Taiwan" in p["factors"]]
    if supply:
        must.append({"text": "Advanced manufacturing supply stays available", "basis": "suppliers named in filings",
                     "evidence": [f"{s}: {', '.join(p['factors'][k]['label'] for k in ('entity:TSMC', 'geo:Taiwan') if k in p['factors'])}"
                                  for s, p in profiles.items() if s in supply]})
    comp = [(s, p["competitors"]) for s, p in profiles.items() if p.get("competitors")]
    if comp:
        breaks.append({"kind": "competition", "label": "Competition", "holdings": [s for s, _ in comp],
                       "text": "Competitors named in the companies' own filings",
                       "evidence": [f"{s}: {', '.join(c[:5])}" for s, c in comp]})
    cust = factors.get("concentration:customer")
    if cust:
        breaks.append({"kind": "customers", "label": "Customer concentration", "holdings": [h["ticker"] for h in cust["holders"]],
                       "text": "A few customers account for a large share of revenue (disclosed)",
                       "evidence": [e["text"] for h in cust["holders"] for e in h["evidence"][:1]]})
    for key, label in (("entity:TSMC", "Single manufacturing partner (TSMC)"), ("geo:Taiwan", "Taiwan"), ("geo:China", "China"),
                       ("policy:Export controls", "Export controls"), ("customer:us_gov", "Government funding")):
        f = factors.get(key)
        if f and any(h["strength"] in MATERIAL for h in f["holders"]):
            holders = [h["ticker"] for h in f["holders"] if h["strength"] in MATERIAL]
            breaks.append({"kind": "dependency", "label": label, "holdings": holders, "text": f"{label}: emphasised or disclosed in filings",
                           "evidence": [h["evidence"][0]["text"] for h in f["holders"] if h["evidence"]][:2]})
    pols = [(s, [f["label"] for k, f in p["factors"].items() if k.startswith("policy:") and f["strength"] in MATERIAL]) for s, p in profiles.items()]
    pols = [(s, l) for s, l in pols if l]
    if pols:
        breaks.append({"kind": "regulation", "label": "Regulatory shifts", "holdings": [s for s, _ in pols],
                       "text": "Policy topics the companies discuss repeatedly", "evidence": [f"{s}: {', '.join(l[:4])}" for s, l in pols]})
    if group == "short":
        rich = [s for s, p in profiles.items() if (p.get("ps") or 0) > 15 or (p.get("forward_pe") or 0) > 50]
        if rich:
            breaks.append({"kind": "valuation", "label": "Valuation reset", "holdings": rich,
                           "text": "Priced on far-future growth: sensitive to disappointments and rates",
                           "evidence": [f"{s}: P/S {profiles[s].get('ps') or 0:.1f}, forward P/E {profiles[s].get('forward_pe') or 0:.0f}" for s in rich]})
        soon = [s for s, p in profiles.items() if p.get("next_earnings")]
        if soon:
            breaks.append({"kind": "catalyst", "label": "Earnings inside the horizon", "holdings": soon, "text": "Scheduled reports",
                           "evidence": [f"{s}: {profiles[s]['next_earnings']}" for s in soon]})
    losses = [s for s, p in profiles.items() if (p.get("operating_margin") or 0) < 0]
    if losses:
        breaks.append({"kind": "profitability", "label": "Not yet profitable", "holdings": losses, "text": "Negative operating margin",
                       "evidence": [f"{s}: operating margin {profiles[s]['operating_margin']:.0%}" for s in losses]})
    return {"must_be_true": must, "could_break": breaks}


# ---------------------------------------------------------------- saved theses

def _thesis_path(tid):
    if not re.fullmatch(r"th_[0-9a-f]{8}", tid or ""):
        raise InvestError("That thesis doesn't exist.")
    return THESES_DIR / f"{tid}.json"


def save_thesis(body):
    body = body or {}
    tid = body.get("id") or f"th_{secrets.token_hex(4)}"
    path = _thesis_path(tid)
    record = {"id": tid, "name": str(body.get("name") or "")[:120] or (str(body.get("text") or "")[:60] or "Untitled thesis"),
              "text": str(body.get("text") or "")[:6000], "horizon": body.get("horizon") if body.get("horizon") in HORIZONS else "12m",
              "months": body.get("months"), "holdings": body.get("holdings") or [], "cash": body.get("cash") or 0,
              "updated_at": now_iso()}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            record["created_at"] = old.get("created_at")
        except (OSError, ValueError):
            pass
    record.setdefault("created_at", now_iso())
    record["created_at"] = record["created_at"] or now_iso()
    THESES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(path)
    return record


def list_theses():
    if not THESES_DIR.exists():
        return []
    out = []
    for p in THESES_DIR.glob("th_*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda t: t.get("updated_at", ""), reverse=True)


def delete_thesis(tid):
    path = _thesis_path(tid)
    if path.exists():
        path.unlink()
    return {"deleted": tid}


def meta():
    return {"horizons": {k: v[0] for k, v in HORIZONS.items()}, "themes": {k: {"label": t["label"], "candidates": t["candidates"]} for k, t in THEMES.items()},
            "scenarios": [{"id": s["id"], "label": s["label"]} for s in SCENARIOS]}
