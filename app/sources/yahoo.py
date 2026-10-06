"""
sources/yahoo.py — Yahoo Finance, via the yfinance library.

Authoritative in MarketLab for: prices, market cap, trading volume, beta,
valuation multiples and analyst estimates (our only source for those today).
Also used as a FALLBACK for financial statements when SEC data is missing.

Caveats: unofficial (yfinance reads Yahoo's website data), prices delayed,
Yahoo can temporarily rate-limit heavy use.
"""

import logging
import time

import pandas as pd
import yfinance as yf

from data import DataSourceError, TickerNotFoundError

# yfinance prints its own warnings straight to the terminal. We handle
# errors ourselves, so we silence its internal logger.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


# --- Cache -------------------------------------------------------------------
# yfinance stores what it downloads inside each Ticker object. Keeping the
# object for 10 minutes means opening several tabs doesn't download the
# same data again (and keeps us under Yahoo's rate limits).

CACHE_SECONDS = 10 * 60
_ticker_cache = {}  # "AAPL" -> (time created, yf.Ticker object)


def get_ticker(ticker):
    now = time.time()
    cached = _ticker_cache.get(ticker)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    ticker_object = yf.Ticker(ticker)
    _ticker_cache[ticker] = (now, ticker_object)
    return ticker_object


def get_info(ticker):
    """
    Yahoo's big 'info' dictionary for a ticker (~150 fields).
    Raises TickerNotFoundError / DataSourceError when appropriate.
    """
    try:
        info = get_ticker(ticker).info
    except Exception as error:
        _ticker_cache.pop(ticker, None)  # don't keep a broken object around
        raise DataSourceError(str(error)) from error

    # For a fake ticker, Yahoo returns an (almost) empty dictionary.
    # A real, tradable security always has a name and a price.
    name = info.get("longName") or info.get("shortName")
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not name or price is None:
        _ticker_cache.pop(ticker, None)
        raise TickerNotFoundError(ticker)

    return info


def load_statement(ticker, kind, frequency):
    """
    A financial statement as a table (rows = line items like "TotalRevenue",
    columns = period end dates). kind: "income", "cash_flow" or "balance".
    Returns an empty table on failure, so one missing statement never
    breaks a whole tab.
    """
    ticker_object = get_ticker(ticker)
    loaders = {
        "income": ticker_object.get_income_stmt,
        "cash_flow": ticker_object.get_cash_flow,
        "balance": ticker_object.get_balance_sheet,
    }
    try:
        # pretty=False gives exact Yahoo field names like "TotalRevenue"
        table = loaders[kind](pretty=False, freq=frequency)
    except Exception:
        return pd.DataFrame()
    return table if isinstance(table, pd.DataFrame) else pd.DataFrame()


def load_estimates(ticker, kind):
    """Analyst consensus table: kind "revenue" or "eps". Empty table on failure."""
    attribute = {"revenue": "revenue_estimate", "eps": "earnings_estimate"}[kind]
    try:
        table = getattr(get_ticker(ticker), attribute)
    except Exception:
        return pd.DataFrame()
    return table if isinstance(table, pd.DataFrame) else pd.DataFrame()


def quote_url(ticker):
    return f"https://finance.yahoo.com/quote/{ticker}"


# --- Daily price history (used by Strategy Lab) --------------------------------

HISTORY_CACHE_SECONDS = 60 * 60
_history_cache = {}  # "NVDA" -> (time fetched, list of (date_text, adjusted_close))


def get_price_history(ticker):
    """
    Every daily closing price Yahoo has for `ticker`, oldest first, as
    [("1999-01-22", 0.0376), ...].

    Prices are ADJUSTED (auto_adjust=True): past prices are scaled down for
    every stock split and dividend, so a 4-for-1 split doesn't look like a
    75% crash and returns include dividends. Cleaning applied:
      * rows with a missing, zero or negative close are dropped
      * duplicated dates are dropped (keeping the last)
      * sorted oldest -> newest
    Raises DataSourceError if Yahoo can't be reached.
    """
    now = time.time()
    cached = _history_cache.get(ticker)
    if cached and now - cached[0] < HISTORY_CACHE_SECONDS:
        return cached[1]

    try:
        table = get_ticker(ticker).history(period="max", auto_adjust=True, actions=False)
    except Exception as error:
        raise DataSourceError(str(error)) from error

    if table is None or table.empty or "Close" not in table:
        return []

    closes = table["Close"]
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    rows = []
    for timestamp, close in closes.items():
        if close is not None and close == close and close > 0:   # close == close is False for NaN
            rows.append((timestamp.strftime("%Y-%m-%d"), float(close)))

    _history_cache[ticker] = (now, rows)
    return rows


# --- Earnings announcement history (used by Lab's earnings conditions) --------

EARNINGS_CACHE_SECONDS = 6 * 3600
_earnings_cache = {}


def earnings_history(ticker):
    """
    Past earnings announcements, oldest first:
    [{"date": "2026-08-26", "timing": "after_close"|"before_open"|"during_market"|"unknown",
      "eps_estimate", "eps_actual", "surprise_pct"}]
    Up to ~25 years for large companies. Upcoming (unreported) dates are dropped.
    Duplicate rows for the same day are merged (Yahoo sometimes repeats a date).
    """
    now = time.time()
    cached = _earnings_cache.get(ticker)
    if cached and now - cached[0] < EARNINGS_CACHE_SECONDS:
        return cached[1]
    try:
        table = get_ticker(ticker).get_earnings_dates(limit=100)
    except Exception as error:
        raise DataSourceError(f"Earnings dates unavailable: {error}") from error
    rows = {}
    if table is not None and not table.empty:
        for ts, row in table.sort_index().iterrows():
            def num(key):
                value = row.get(key)
                return float(value) if value is not None and value == value else None
            actual, surprise = num("Reported EPS"), num("Surprise(%)")
            if actual is None and surprise is None:
                continue                                   # not reported yet
            minutes = ts.hour * 60 + ts.minute
            timing = ("unknown" if minutes == 0 else "before_open" if minutes < 570
                      else "after_close" if minutes >= 960 else "during_market")
            day = ts.strftime("%Y-%m-%d")
            if day not in rows:
                rows[day] = {"date": day, "timing": timing, "eps_estimate": num("EPS Estimate"),
                             "eps_actual": actual, "surprise_pct": surprise}
    result = sorted(rows.values(), key=lambda r: r["date"])
    _earnings_cache[ticker] = (now, result)
    return result
