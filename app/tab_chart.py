"""
tab_chart.py — price bars + overlays for the Overview quick chart and the Chart tab.

Ranges
    1D  -> 5-minute bars (today's / last session)      intraday, not adjusted
    5D  -> 15-minute bars                              intraday, not adjusted
    1M, 3M, 6M, YTD, 1Y, 5Y -> daily bars              split/dividend adjusted
    MAX -> weekly bars (daily history aggregated)      adjusted

Overlays are computed on the FULL daily history and then cut to the range,
so a 200-day average is correct on the first day shown (not "warming up").
VWAP (volume-weighted average price) only exists for intraday bars; it
restarts every session.

Times: daily bars use "YYYY-MM-DD"; intraday bars use exchange-local time
(e.g. New York) expressed as seconds, so the chart shows market hours.
"""

from datetime import datetime, timedelta

import indicators
import marketdata

DAILY_RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "5Y": 1827}
RANGES = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"]


def _epoch(local_text):
    """'2026-09-30T09:35' (exchange time) -> seconds, treated as if UTC so the chart shows exchange time."""
    return int((datetime.fromisoformat(local_text) - datetime(1970, 1, 1)).total_seconds())


def _weekly(bars):
    """Aggregate daily bars into weeks (Monday-based), keeping OHLCV meaning."""
    out = {"date": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
    current = None
    for i, day in enumerate(bars["date"]):
        week = (datetime.fromisoformat(day) - timedelta(days=datetime.fromisoformat(day).weekday())).strftime("%Y-%m-%d")
        if week != current:
            current = week
            out["date"].append(day)
            out["open"].append(bars["open"][i]); out["high"].append(bars["high"][i])
            out["low"].append(bars["low"][i]); out["close"].append(bars["close"][i])
            out["volume"].append(bars["volume"][i] or 0)
        else:
            out["high"][-1] = max(out["high"][-1] or 0, bars["high"][i] or 0)
            out["low"][-1] = min(x for x in (out["low"][-1], bars["low"][i]) if x is not None)
            out["close"][-1] = bars["close"][i]
            out["volume"][-1] += bars["volume"][i] or 0
    return out


def get_chart(ticker, range_key="1Y"):
    range_key = range_key.upper()
    if range_key not in RANGES:
        range_key = "1Y"

    if range_key in ("1D", "5D"):
        bars = marketdata.intraday_bars(ticker, range_key.lower())
        if range_key == "1D" and bars["date"]:                 # keep only the latest session
            last_day = bars["date"][-1][:10]
            keep = [i for i, d in enumerate(bars["date"]) if d.startswith(last_day)]
            bars = {**bars, **{k: [bars[k][i] for i in keep] for k in ("date", "open", "high", "low", "close", "volume")}}
        times = [_epoch(d) for d in bars["date"]]
        vwap, cum_pv, cum_v, session = [], 0.0, 0.0, None
        for i, d in enumerate(bars["date"]):
            if d[:10] != session:
                session, cum_pv, cum_v = d[:10], 0.0, 0.0
            typical = ((bars["high"][i] or 0) + (bars["low"][i] or 0) + (bars["close"][i] or 0)) / 3
            cum_pv += typical * (bars["volume"][i] or 0)
            cum_v += bars["volume"][i] or 0
            vwap.append(cum_pv / cum_v if cum_v else None)
        sma20 = indicators.sma([c or 0 for c in bars["close"]], 20)
        overlays = {"sma20": sma20, "sma50": [None] * len(times), "sma200": [None] * len(times), "vwap": vwap}
        start_index, interval = 0, bars["interval"]
        quality = None
    else:
        full = marketdata.daily_bars(ticker)
        quality = full["quality"]["summary"]
        source = full
        overlays_full = {f"sma{p}": indicators.sma(full["close"], p) for p in (20, 50, 200)}
        if range_key == "MAX" and len(full["date"]) > 2600:
            source = _weekly(full)
            overlays_full = {f"sma{p}": indicators.sma(source["close"], p) for p in (20, 50, 200)}   # weekly bars: weeks, not days
            interval = "1wk"
        else:
            interval = "1d"
        if range_key == "MAX":
            start_day = source["date"][0]
        elif range_key == "YTD":
            start_day = f"{source['date'][-1][:4]}-01-01"
        else:
            start_day = (datetime.fromisoformat(source["date"][-1]) - timedelta(days=DAILY_RANGES[range_key])).strftime("%Y-%m-%d")
        start_index = next((i for i, d in enumerate(source["date"]) if d >= start_day), 0)
        bars = {k: source[k][start_index:] for k in ("date", "open", "high", "low", "close", "volume")}
        bars.update(provider=full["provider"], retrieved_at=full["retrieved_at"])
        times = bars["date"]
        overlays = {k: v[start_index:] for k, v in overlays_full.items()}
        overlays["vwap"] = None

    closes = [c for c in bars["close"] if c is not None]
    first, last = (closes[0], closes[-1]) if closes else (None, None)
    return {
        "ticker": ticker, "range": range_key, "interval": interval,
        "time": times, "open": bars["open"], "high": bars["high"], "low": bars["low"],
        "close": bars["close"], "volume": bars["volume"], "overlays": overlays,
        "summary": {
            "first": first, "last": last,
            "change": (last / first - 1) if first else None,
            "high": max((h for h in bars["high"] if h is not None), default=None),
            "low": min((l for l in bars["low"] if l is not None), default=None),
            "volume": sum(v or 0 for v in bars["volume"]),
        },
        "adjusted": interval not in ("5m", "15m"),
        "provider": bars.get("provider"), "retrieved_at": bars.get("retrieved_at"),
        "quality": quality,
        "sma_unit": "weeks" if interval == "1wk" else "bars" if interval in ("5m", "15m") else "days",
    }
