"""
engine.py — multi-timeframe synchronization.

Higher timeframes are BUILT from the base candles (1m → 5m, 15m, 30m, 1h, 4h, 1d),
so every timeframe describes exactly the same trades and gaps.

    5m … 1h   aligned to the clock (a 1h candle covers 10:00–11:00 ET)
    4h        aligned to the trading-day start (18:00, 22:00, 02:00, 06:00, 10:00, 14:00 ET)
    1d        one candle per trading day (18:00 ET → 17:00 ET)

A higher-timeframe candle is CLOSED, and therefore visible, only at its end
(bucket start + interval). For the last 4h bucket and the daily candle that is
18:00 ET, an hour after trading stops: conservative, never early.

Coverage: a resampled candle built from fewer base candles than expected is
"partial"; below 80% coverage it is also "suspect" (its high/low may be wrong).
Any suspect base candle makes the candle containing it suspect.
"""

from bisect import bisect_right

from . import sessions
from .bars import ORDER, Series, seconds

MIN_COVERAGE = 0.8


def _bucket(ts, step, interval):
    if interval == "1d":
        start, _ = sessions.day_bounds(sessions.trading_day(ts))
        return start
    if interval == "4h":
        start, _ = sessions.day_bounds(sessions.trading_day(ts))
        return start + (ts - start) // step * step
    return ts - ts % step


def resample(base, interval):
    if interval == base.interval:
        return base
    step, base_step = seconds(interval), base.step
    if step < base_step:
        raise ValueError(f"Can't build {interval} candles from {base.interval} data.")
    out = Series(base.symbol, interval, base.provider, base.contract)
    groups = {}
    order = []
    for i, t in enumerate(base.ts):
        b = _bucket(t, step, interval)
        if b not in groups:
            groups[b] = []
            order.append(b)
        groups[b].append(i)
    for b in order:
        idx = groups[b]
        if interval == "1d":
            day = sessions.trading_day(b + 3600)
            s, e = sessions.day_bounds(day)
            expected = sum(1 for p in range(s, e, base_step) if sessions.standard_open(p)) or len(idx)
        else:
            expected = sum(1 for p in range(b, b + step, base_step) if sessions.standard_open(p)) or len(idx)
        flags = set()
        if any(base.suspect(i) for i in idx):
            flags.add("suspect")
        if len(idx) < expected:
            flags.add("partial")
            if len(idx) < MIN_COVERAGE * expected:
                flags.add("suspect")
        contracts = {base.contracts[i] for i in idx} if base.contracts else {None}
        sources = {base.sources[i] for i in idx} if base.sources else {None}
        out.append(b, base.open[idx[0]], max(base.high[i] for i in idx), min(base.low[i] for i in idx),
                   base.close[idx[-1]], sum(base.volume[i] for i in idx), flags, len(idx), expected,
                   contracts.pop() if len(contracts) == 1 else "mixed", sources.pop() if len(sources) == 1 else "mixed")
    out.day = [sessions.trading_day(t + (3600 if interval == "1d" else 0)) for t in out.ts]
    out.meta = dict(base.meta)
    return out


def regime(series, t):
    """Which contract regime a time belongs to (increments at each detected roll). Levels never cross regimes."""
    return sum(1 for b in series.meta.get("breaks", ()) if b <= t)


def atr(series, period):
    """Simple-average true range; value i uses candles up to and including i (never later)."""
    n = len(series)
    tr = []
    for i in range(n):
        h, l = series.high[i], series.low[i]
        if i and series.day[i] == series.day[i - 1]:
            pc = series.close[i - 1]
            tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        else:
            tr.append(h - l)
    out, total = [None] * n, 0.0
    for i in range(n):
        total += tr[i]
        if i >= period:
            total -= tr[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


class TimeframeContext:
    """One timeframe's candles; answers 'which candles had closed by time t?'"""

    def __init__(self, series):
        self.series = series
        self.close_times = [t + series.step for t in series.ts]
        self._atr = {}

    def last_closed(self, t):
        """Index of the newest candle closed at or before t (-1 if none)."""
        return bisect_right(self.close_times, t) - 1

    def atr(self, period):
        if period not in self._atr:
            self._atr[period] = atr(self.series, period)
        return self._atr[period]

    def index_of_close(self, t):
        i = self.last_closed(t)
        return i if i >= 0 and self.close_times[i] == t else None


def build_contexts(base, intervals):
    """{interval: TimeframeContext} for the base and every coarser interval requested."""
    out = {}
    for interval in sorted(set(intervals) | {base.interval}, key=ORDER.index):
        out[interval] = TimeframeContext(resample(base, interval))
    return out
