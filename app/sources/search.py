"""
sources/search.py — find a security by company name OR ticker.

Primary: Yahoo Finance's search (understands partial names like "nvid",
covers stocks, ETFs, indexes and futures worldwide).
Fallback: the SEC's list of ~10,000 US-listed companies, searched locally,
used if Yahoo is unreachable or rate-limited.

Results are ranked: exact ticker match first, then US listings, then the rest.
"""

import time

from sources import sec

CACHE_SECONDS = 10 * 60
_cache = {}
US_EXCHANGES = {"NASDAQ", "NYSE", "NYSEArca", "NYSE American", "NYSE MKT", "BATS", "Cboe US", "NasdaqGS", "NasdaqGM", "NasdaqCM"}
ALLOWED_TYPES = {"EQUITY", "ETF", "INDEX", "FUTURE", "MUTUALFUND"}   # no crypto "tokens" named after companies


def _yahoo(query):
    import yfinance as yf
    found = yf.Search(query, max_results=10, news_count=0, lists_count=0, enable_fuzzy_query=True).quotes
    results = []
    for q in found:
        kind = q.get("quoteType")
        if kind not in ALLOWED_TYPES or not q.get("symbol"):
            continue
        results.append({
            "symbol": q["symbol"],
            "name": q.get("longname") or q.get("shortname") or q["symbol"],
            "exchange": q.get("exchDisp") or q.get("exchange") or "",
            "type": kind,
        })
    return results


def _sec(query):
    """Local fallback over the SEC ticker list (US companies only)."""
    table = sec._get_json("https://www.sec.gov/files/company_tickers.json", 24 * 3600)
    q = query.lower()
    scored = []
    for row in table.values():
        ticker, title = str(row.get("ticker", "")), str(row.get("title", ""))
        name = title.lower()
        if ticker.lower() == q:
            score = 0
        elif ticker.lower().startswith(q):
            score = 1
        elif name.startswith(q):
            score = 2
        elif q in name:
            score = 3
        else:
            continue
        scored.append((score, len(title), {"symbol": ticker, "name": title.title(), "exchange": "US", "type": "EQUITY"}))
    return [item for _, _, item in sorted(scored, key=lambda x: (x[0], x[1]))[:8]]


def search(query):
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "source": None}
    key = query.lower()
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]

    source = "Yahoo Finance"
    try:
        results = _yahoo(query)
    except Exception:
        results = []
    if not results:
        try:
            results = _sec(query)
            source = "SEC company list"
        except Exception:
            results = []

    q = query.upper()
    results.sort(key=lambda r: (r["symbol"] != q, r["exchange"] not in US_EXCHANGES, r["type"] != "EQUITY"))
    payload = {"query": query, "results": results[:8], "source": source if results else None}
    _cache[key] = (time.time(), payload)
    return payload
