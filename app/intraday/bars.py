"""
bars.py — generic intervals and the normalized bar series.

A Series holds parallel lists (fast for tens of thousands of candles):
    ts      bar OPEN time, Unix seconds (UTC)
    open, high, low, close, volume
    flags   per-bar quality flags (set by quality.py / engine.py):
              "suspect"  the bar is inside a contaminated period; detectors skip it
              "partial"  a resampled bar built from fewer base candles than expected
              "after_gap" the bar follows missing data
    contracts per-bar contract code when the source identifies it (None otherwise)
    sources   per-bar source key, for provenance
plus metadata: symbol, interval, provider, contract.

A bar is CLOSED (and therefore visible to strategies) at ts + interval.
Intervals are generic: second-level data ("1s", "5s"…) uses the same code.
"""

INTERVAL_SECONDS = {
    "1s": 1, "5s": 5, "15s": 15, "30s": 30,
    "1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}
ORDER = ["1s", "5s", "15s", "30s", "1m", "2m", "5m", "15m", "30m", "1h", "4h", "1d"]


def seconds(interval):
    if interval not in INTERVAL_SECONDS:
        raise ValueError(f"Unknown interval {interval!r}")
    return INTERVAL_SECONDS[interval]


def finer_or_equal(a, b):
    return seconds(a) <= seconds(b)


class Series:
    __slots__ = ("symbol", "interval", "provider", "contract", "ts", "open", "high", "low", "close",
                 "volume", "flags", "count", "expected", "day", "meta", "contracts", "sources")

    def __init__(self, symbol, interval, provider, contract=None):
        self.symbol, self.interval, self.provider, self.contract = symbol, interval, provider, contract
        self.ts, self.open, self.high, self.low, self.close, self.volume = [], [], [], [], [], []
        self.flags = []          # list of sets
        self.count = []          # base candles inside (resampled series)
        self.expected = []       # base candles expected
        self.day = []            # trading day (datetime.date), filled by quality.py / engine.py
        self.contracts = []      # per-bar contract code ("NQZ26") or None when the source doesn't say
        self.sources = []        # per-bar source key ("yahoo", "firstrate", "local")
        self.meta = {}

    def append(self, ts, o, h, l, c, v, flags=None, count=1, expected=1, contract=None, source=None):
        self.ts.append(ts); self.open.append(o); self.high.append(h); self.low.append(l)
        self.close.append(c); self.volume.append(v); self.flags.append(set(flags or ()))
        self.count.append(count); self.expected.append(expected)
        self.contracts.append(contract); self.sources.append(source)

    def __len__(self):
        return len(self.ts)

    @property
    def step(self):
        return seconds(self.interval)

    def close_time(self, i):
        return self.ts[i] + self.step

    def suspect(self, i):
        return "suspect" in self.flags[i]

    def rows(self, start=0, end=None):
        end = len(self) if end is None else end
        return [{"t": self.ts[i], "o": self.open[i], "h": self.high[i], "l": self.low[i], "c": self.close[i],
                 "v": self.volume[i], "flags": sorted(self.flags[i]),
                 "contract": self.contracts[i] if self.contracts else None} for i in range(start, end)]


def normalize_row(raw, symbol, interval, provider, contract=None):
    """
    Turn one provider row into the normalized shape:
    {timestamp, open, high, low, close, volume, symbol, interval, provider, contract}
    Returns None for unusable rows (missing prices, high < low, non-positive prices).
    """
    try:
        ts = int(raw["timestamp"])
        o, h, l, c = (float(raw[k]) for k in ("open", "high", "low", "close"))
    except (KeyError, TypeError, ValueError):
        return None
    if any(x != x or x <= 0 for x in (o, h, l, c)) or h < l:
        return None
    volume = raw.get("volume")
    try:
        volume = float(volume) if volume is not None and volume == volume else 0.0
    except (TypeError, ValueError):
        volume = 0.0
    # A high/low that doesn't contain open/close is repaired to contain them (common provider rounding).
    h, l = max(h, o, c), min(l, o, c)
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": volume,
            "symbol": symbol, "interval": interval, "provider": provider, "contract": raw.get("contract", contract)}
