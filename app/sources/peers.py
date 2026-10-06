"""
sources/peers.py — suggestions for "Test elsewhere".

Nothing here is a hard-coded universe. Suggestions come from data about the
company, grouped by WHY they're suggested, so the user can judge relevance:

    similar      Yahoo's "similar symbols" (stocks people research together;
                 behaviour-based, often the most intuitive peers for small caps)
    industry     the largest companies in the same Yahoo industry
    sector       the SPDR sector ETF for the company's sector
    market       broad benchmarks (SPY, QQQ)

Cached for 6 hours. Any failing source is simply left out.
"""

import time

from sources import yahoo

CACHE_SECONDS = 6 * 3600
_cache = {}

SECTOR_ETFS = {
    "technology": ("XLK", "Technology Select Sector SPDR"),
    "financial-services": ("XLF", "Financial Select Sector SPDR"),
    "healthcare": ("XLV", "Health Care Select Sector SPDR"),
    "industrials": ("XLI", "Industrial Select Sector SPDR"),
    "consumer-cyclical": ("XLY", "Consumer Discretionary Select Sector SPDR"),
    "consumer-defensive": ("XLP", "Consumer Staples Select Sector SPDR"),
    "energy": ("XLE", "Energy Select Sector SPDR"),
    "utilities": ("XLU", "Utilities Select Sector SPDR"),
    "real-estate": ("XLRE", "Real Estate Select Sector SPDR"),
    "basic-materials": ("XLB", "Materials Select Sector SPDR"),
    "communication-services": ("XLC", "Communication Services Select Sector SPDR"),
}


def _similar(symbol):
    from yfinance.data import YfData
    response = YfData().get(f"https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/{symbol}")
    result = (response.json().get("finance") or {}).get("result") or []
    return [r["symbol"] for r in (result[0].get("recommendedSymbols") if result else []) or []]


def _industry(key):
    import yfinance as yf
    table = yf.Industry(key).top_companies
    return list(table.index) if table is not None else []


def suggestions(symbol):
    cached = _cache.get(symbol)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        info = yahoo.get_info(symbol)
    except Exception:
        info = {}
    groups = []
    try:
        similar = [s for s in _similar(symbol) if s != symbol][:5]
        if similar:
            groups.append({"id": "similar", "label": "Often researched together", "source": "Yahoo Finance similar symbols", "symbols": similar})
    except Exception:
        pass
    if info.get("industryKey"):
        try:
            peers = [s for s in _industry(info["industryKey"]) if s != symbol and "." not in s][:5]
            if peers:
                groups.append({"id": "industry", "label": f"Largest in {info.get('industry') or 'the same industry'}",
                               "source": "Yahoo Finance industry classification", "symbols": peers})
        except Exception:
            pass
    etf = SECTOR_ETFS.get(info.get("sectorKey") or "")
    if etf and etf[0] != symbol:
        groups.append({"id": "sector", "label": f"{info.get('sector')} sector", "source": etf[1], "symbols": [etf[0]]})
    groups.append({"id": "market", "label": "Broad market", "source": "S&P 500 and Nasdaq-100 ETFs",
                   "symbols": [s for s in ("SPY", "QQQ") if s != symbol]})
    payload = {"symbol": symbol, "groups": groups}
    _cache[symbol] = (time.time(), payload)
    return payload
