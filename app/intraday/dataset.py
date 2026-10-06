"""
dataset.py — assemble a research dataset and describe it honestly.

build(symbol, base="1m", provider="yahoo")
    1. provider.get_historical_bars()   (normalized rows)
    2. quality.inspect()                (flags, anomalies; nothing interpolated)
    3. NQ ↔ MNQ cross-check             (same market, different contracts)
    4. engine.build_contexts()          (every coarser timeframe from the same base)

inspector(ds) returns what the Dataset panel shows: provider, instrument, intervals
(with unavailable ones disabled and the reason), first/last candle, candle count,
missing candles, anomalies, freshness and the data STATUS:

    LIVE               real-time provider and the newest candle is current
    DELAYED            delayed provider (Yahoo: 10 min) and the newest candle is as fresh as that delay allows
    LATEST AVAILABLE   the market is closed now and this is the newest candle there is
    HISTORICAL         older data (or a file); not current
"""

import threading
import time

from . import composite, engine, quality, sessions
from .bars import ORDER, seconds
from .providers import NotSupported, catalog, get_provider, record_catalog

CACHE_SECONDS = 300
_cache = {}
_lock = threading.Lock()
SIBLINGS = {"NQ": "MNQ", "MNQ": "NQ", "ES": "MES", "MES": "ES", "RTY": "M2K", "M2K": "RTY"}
DESCRIPTIONS = {
    "auto": "MarketLab's own continuous series: Yahoo's per-contract series + Yahoo continuous + the FirstRate sample, "
            "joined with an explicit volume roll rule (unadjusted). Every candle keeps its contract and source",
    "yahoo": "Continuous front-month series from Yahoo (unadjusted; contract months and roll dates are not provided)",
    "firstrate": "FirstRate Data public sample (about two recent weeks, unadjusted front month)",
    "local": "Local file (methodology as documented by the file's source)",
}


class Dataset:
    def __init__(self, symbol, base, provider, series, report, contexts, cross, fetched_at, provenance=None):
        self.symbol, self.base, self.provider = symbol, base, provider
        self.series, self.report, self.contexts = series, report, contexts
        self.cross, self.fetched_at, self.built_at = cross, fetched_at, time.time()
        self.provenance = provenance

    def context(self, interval):
        if interval not in self.contexts:
            if seconds(interval) < seconds(self.base):
                raise NotSupported(f"{interval} needs finer data than this dataset's {self.base} candles.")
            self.contexts[interval] = engine.build_contexts(self.series, [interval])[interval]
        return self.contexts[interval]


def build(symbol, base="1m", provider="yahoo", refresh=False, intervals=("5m", "15m", "1h")):
    key = (symbol, base, provider)
    with _lock:
        cached = _cache.get(key)
        if cached and not refresh and time.time() - cached.built_at < CACHE_SECONDS:
            return cached
    source = get_provider(provider)
    kwargs = {"refresh": refresh} if provider in ("yahoo", "auto", "firstrate") else {}
    rows = source.get_historical_bars(symbol, base, **kwargs)
    provenance = source.provenance(symbol, base) if provider == "auto" else None
    series, report = quality.inspect(rows, base, symbol=symbol, provider=source.provider_name())
    cross = None
    sibling = SIBLINGS.get(symbol)
    if sibling and provider in ("yahoo", "auto"):
        try:
            other_rows = source.get_historical_bars(sibling, base)
            other, _ = quality.inspect(other_rows, base, symbol=sibling)
            flagged, cross = quality.cross_check(series, other)
            if cross:
                cross["sibling"] = sibling
                for period in cross.pop("periods"):
                    report["counts"][period["type"]] = report["counts"].get(period["type"], 0) + 1
                    report["anomalies"].append(period)
                report["suspect_bars"] = sum(1 for f in series.flags if "suspect" in f)
        except Exception:
            cross = None
    contexts = engine.build_contexts(series, [iv for iv in intervals if seconds(iv) >= seconds(base)])
    fetched = source.fetched_at(symbol, base) if hasattr(source, "fetched_at") else None
    ds = Dataset(symbol, base, provider, series, report, contexts, cross, fetched, provenance)
    try:
        record_catalog(f"research_{provider}_{symbol}_{base}", provider=source.provider_name(), provider_key=provider,
                       instrument=symbol, contract="continuous", resolution=base,
                       first=series.ts[0] if len(series) else None, last=series.ts[-1] if len(series) else None,
                       bars=len(series), downloaded_at=fetched, built_at=time.time(),
                       quality={"suspect_bars": report.get("suspect_bars", 0), "missing_bars": report.get("missing_bars", 0),
                                "anomalies": report.get("counts", {})})
    except OSError:
        pass
    with _lock:
        _cache[key] = ds
    return ds


def status(ds, now=None):
    now = now or time.time()
    source = get_provider(ds.provider)
    if not len(ds.series):
        return {"label": "HISTORICAL", "why": "No candles."}
    last_close = ds.series.ts[-1] + ds.series.step
    age = now - last_close
    open_now = sessions.standard_open(now)
    delay = source.data_delay() or 0
    if source.is_realtime() and age < 2 * ds.series.step + 5:
        return {"label": "LIVE", "why": "Real-time provider; newest candle is current."}
    if open_now and delay and age <= delay + 3 * ds.series.step + 120:
        return {"label": "DELAYED", "why": f"{source.provider_name()} data is delayed about {delay // 60} minutes. Not live."}
    if not open_now and age < 3 * 86400:
        return {"label": "LATEST AVAILABLE", "why": "The market is closed; this is the newest candle there is."}
    return {"label": "HISTORICAL", "why": "Recorded history; not current. Refresh to update."}


def inspector(ds):
    source = get_provider(ds.provider)
    s = ds.series
    supported = set(source.supported_intervals()) | {"4h"}
    intervals = []
    intervals.append({"interval": "tick", "available": False,
                      "why": "Requires a tick data source. No free anonymous NQ/MNQ tick feed exists."})
    for iv in ["1s", "5s", "15s", "30s", "1m", "5m", "15m", "30m", "1h", "4h", "1d"]:
        if seconds(iv) < 60 and iv not in source.supported_intervals():
            intervals.append({"interval": iv, "available": False,
                              "why": "Requires second-level data source. None is free without an account; never built from minute candles."})
        elif seconds(iv) < seconds(ds.base):
            intervals.append({"interval": iv, "available": False, "why": f"Needs {iv} data; this dataset starts at {ds.base}."})
        else:
            intervals.append({"interval": iv, "available": True,
                              "why": "Base data" if iv == ds.base else f"Built from {ds.base} candles"})
    days = sorted(set(s.day))
    first = s.ts[0] if len(s) else None
    last = s.ts[-1] if len(s) else None
    return {
        "provider": source.provider_name(), "provider_key": ds.provider,
        "instrument": ds.symbol, "methodology": DESCRIPTIONS.get(ds.provider),
        "base": ds.base, "intervals": intervals,
        "first": first, "last": last,
        "first_label": sessions.to_et(first).strftime("%a %Y-%m-%d %H:%M ET") if first else None,
        "last_label": sessions.to_et(last).strftime("%a %Y-%m-%d %H:%M ET") if last else None,
        "candles": len(s), "trading_days": len(days),
        "missing_bars": ds.report.get("missing_bars", 0),
        "missing_ranges": ds.report["counts"].get("MISSING DATA", 0),
        "suspect_bars": ds.report.get("suspect_bars", 0),
        "anomaly_counts": ds.report["counts"],
        "anomalies": ds.report["anomalies"][:120],
        "thresholds": ds.report.get("thresholds"),
        "cross_check": ds.cross,
        "fetched_at": ds.fetched_at,
        "status": status(ds),
        "realtime": source.is_realtime(), "delay_seconds": source.data_delay(),
        "history_note": source.history_available(ds.base).get("note"),
        "provenance": _provenance(ds),
        "base_reason": getattr(ds, "base_reason", None),
        "contract_status": _contract_status(ds),
        "depth": RESOLUTION_DEPTH,
        "cache": [dict(v, name=k) for k, v in catalog().items() if (v.get("instrument") or "").startswith(ds.symbol)
                  and (v.get("instrument") == ds.symbol or (v.get("instrument") or "")[len(ds.symbol):][:1] in "HMUZ")],
    }


RESOLUTION_DEPTH = [
    {"resolution": "tick", "free": False, "depth": None, "note": "No free anonymous source"},
    {"resolution": "1s–30s", "free": False, "depth": None, "note": "No free anonymous source; never built from minute data"},
    {"resolution": "1m", "free": True, "depth": "≈ 29 days, about 20 trading days (+ local archive)", "note": "Yahoo contract + continuous series, FirstRate sample"},
    {"resolution": "5m", "free": True, "depth": "≈ 60 days (about 40 trading days)", "note": "Used automatically when a strategy's finest timeframe is 5m or coarser"},
    {"resolution": "15m", "free": True, "depth": "≈ 60 days", "note": ""},
    {"resolution": "1h", "free": True, "depth": "≈ 2 years", "note": "Older years: contract not identified (Yahoo continuous)"},
]


def _provenance(ds):
    p = ds.provenance
    if not p:
        return None
    label = lambda day: day
    return {"segments": [{**seg, "from_label": label(seg["from_day"]), "to_label": label(seg["to_day"])} for seg in p["segments"]],
            "rolls": p["rolls"], "roll_rule": p["roll_rule"], "sources": p.get("sources", []),
            "rows_by_source": p.get("rows_by_source", {}), "limitations": p.get("limitations", []),
            "notes": p.get("notes", []), "adjustment": p.get("adjustment")}


def _contract_status(ds):
    s = ds.series
    labelled = [c for c in (s.contracts or []) if c]
    n = len(s) or 1
    contracts = sorted({c for c in labelled if c != "mixed"}, key=lambda c: (sessions.contract_expiry(c) or 0, c))
    if not labelled:
        return {"label": "Continuous, contract not identified", "share": 0, "contracts": [],
                "why": "This source gives one continuous series without contract months; rolls are detected heuristically."}
    share = len(labelled) / n
    return {"label": "Contract-labelled continuous series" if share > 0.999 else "Partly contract-labelled continuous series",
            "share": share, "contracts": contracts,
            "why": f"{share:.0%} of candles carry their exact contract; the rest come from a front month that is no longer downloadable."}


def auto_base(project, available=("1m", "5m", "15m", "1h")):
    """
    The coarsest base resolution that still builds every timeframe the strategy uses (a base must divide
    each timeframe exactly). Coarser bases reach further back for free (5m ≈ 60 days (about 40 trading days) vs 1m ≈ 29).
    Returns (base, reason).
    """
    tfs = set()
    for d in project.get("definitions") or []:
        tf = (d.get("params") or {}).get("timeframe")
        if tf:
            tfs.add(tf)
    roles = (project.get("timeframes") or {}).get("roles") or {}
    for key in ("context", "setup", "execution"):
        if roles.get(key):
            tfs.add(roles[key])
    tfs |= set(roles.get("confirmation") or [])
    tfs = {tf for tf in tfs if tf in ORDER}
    if not tfs:
        return "1m", "No timeframes chosen yet; using the finest free data (1m)."
    finest = min(tfs, key=seconds)
    for base in sorted(available, key=seconds, reverse=True):
        if seconds(base) <= seconds(finest) and all(seconds(tf) % seconds(base) == 0 for tf in tfs):
            if base == "1m":
                return base, f"Your finest timeframe is {finest}, so 1-minute data is needed (≈ 29 days free)."
            return base, (f"Your finest timeframe is {finest}, so {base} data is enough; it reaches further back "
                          f"(≈ {'60 days' if base in ('5m', '15m') else '2 years'}) than 1-minute data.")
    return "1m", "Using 1-minute data."
