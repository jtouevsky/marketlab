"""
marketdata.py — price bars (open/high/low/close/volume) for any instrument.

Everything that needs prices (Chart, Risk, Earnings reactions, Strategy
Lab) gets them from here, so every feature sees the SAME validated data.

    daily_bars(symbol)              full daily history, split/dividend
                                    adjusted, run through dataquality.py
    intraday_bars(symbol, period)   recent 5- or 15-minute bars for charts

Each result is a dict of equal-length lists plus provenance:
    {"date": [...], "open": [...], "high": [...], "low": [...],
     "close": [...], "volume": [...],
     "symbol", "interval", "adjusted", "provider", "retrieved_at", "quality"}

The interval is part of the data, not an assumption, so intraday futures
(NQ, MNQ, ES...) can later come through the same shape with "5m" bars.
"""

import time

import dataquality
from data import DataSourceError, TickerNotFoundError, now_iso
from sources import yahoo

DAILY_CACHE_SECONDS = 60 * 60
INTRADAY_CACHE_SECONDS = 120
_cache = {}


def _from_table(table, date_format):
    """yfinance table -> dict of plain lists (oldest first, duplicates dropped)."""
    table = table[~table.index.duplicated(keep="last")].sort_index()
    out = {"date": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
    for timestamp, row in table.iterrows():
        out["date"].append(timestamp.strftime(date_format))
        for key, column in (("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close"), ("volume", "Volume")):
            value = row.get(column)
            out[key].append(float(value) if value == value and value is not None else None)
    return out


def daily_bars(symbol):
    key = ("daily", symbol)
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < DAILY_CACHE_SECONDS:
        return cached[1]

    try:
        table = yahoo.get_ticker(symbol).history(period="max", interval="1d", auto_adjust=True, actions=True)
    except Exception as error:
        raise DataSourceError(str(error)) from error
    if table is None or table.empty:
        raise TickerNotFoundError(symbol)

    # Corporate actions on record (used to recognise unadjusted splits)
    splits = {}
    if "Stock Splits" in table:
        for timestamp, ratio in table["Stock Splits"].items():
            if ratio and ratio == ratio and ratio != 0:
                splits[timestamp.strftime("%Y-%m-%d")] = float(ratio)

    bars, report = dataquality.check_daily(_from_table(table, "%Y-%m-%d"), splits)
    bars.update({
        "symbol": symbol, "interval": "1d", "adjusted": True,
        "provider": "Yahoo Finance (daily, adjusted for splits and dividends)",
        "retrieved_at": now_iso(), "quality": report, "splits": splits,
    })
    _cache[key] = (time.time(), bars)
    return bars


def intraday_bars(symbol, period="1d"):
    """Recent intraday bars. period "1d" -> 5-minute bars, "5d" -> 15-minute bars.
    Times are exchange-local (America/New_York for US listings), not adjusted."""
    interval = {"1d": "5m", "5d": "15m"}[period]
    key = ("intraday", symbol, period)
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < INTRADAY_CACHE_SECONDS:
        return cached[1]
    try:
        table = yahoo.get_ticker(symbol).history(period=period, interval=interval, prepost=False, auto_adjust=False)
    except Exception as error:
        raise DataSourceError(str(error)) from error
    if table is None or table.empty:
        raise TickerNotFoundError(symbol)
    bars = _from_table(table, "%Y-%m-%dT%H:%M")
    bars.update({"symbol": symbol, "interval": interval, "adjusted": False,
                 "provider": f"Yahoo Finance ({interval} bars, exchange time)", "retrieved_at": now_iso()})
    _cache[key] = (time.time(), bars)
    return bars


def closes_by_date(symbol):
    """{date: adjusted close}, handy for lining up two instruments (beta, correlation)."""
    bars = daily_bars(symbol)
    return dict(zip(bars["date"], bars["close"]))
