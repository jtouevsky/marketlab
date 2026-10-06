"""
concepts.py — detectors for the concepts beyond FVGs, sweeps and swings.

Every detector reads candles of ONE timeframe (a TimeframeContext) and returns events:

    {id, concept, direction ("bullish"/"bearish"/None), occurred_at, confirmed_at, tf, ...details}

    occurred_at    the open of the first candle involved
    confirmed_at   the CLOSE of the last candle the definition needs (never earlier)

Shared rules (the same as detectors.py):
    * suspect candles (bad data, rolls, source disagreement) never create or confirm anything;
    * a pattern never spans missing data, two trading days (where the definition is intraday)
      or a contract roll;
    * ATR / averages use only candles BEFORE the candle being judged.

Terms such as "displacement", "BOS", "MSS" and "CHoCH" have no single universal definition.
These are MarketLab's explicit, configurable versions (see concept_library.py for the sentences).
"""

from bisect import bisect_right

from . import sessions
from .detectors import daily_levels, swings
from .engine import regime


def _bad(s, i):
    return "suspect" in s.flags[i]


def _contiguous(s, a, b):
    """Candles a..b are consecutive, same trading day, same contract regime, none suspect or after a hole."""
    step = s.step
    for k in range(a, b + 1):
        if _bad(s, k) or (k > a and ("after_gap" in s.flags[k] or s.ts[k] - s.ts[k - 1] != step or s.day[k] != s.day[a])):
            return False
    return regime(s, s.ts[a]) == regime(s, s.ts[b])


def _event(concept, s, ctx, start, end, direction, **extra):
    return {"id": f"{concept}_{s.interval}_{s.ts[start]}_{direction or ''}", "concept": concept, "direction": direction,
            "occurred_at": s.ts[start], "confirmed_at": ctx.close_times[end], "tf": s.interval, "day": str(s.day[end]), **extra}


def _avg_volume(s, i, lookback):
    prev = [s.volume[k] for k in range(max(0, i - lookback), i) if s.volume[k]]
    return sum(prev) / len(prev) if len(prev) >= max(2, lookback // 2) else None


# ---------------------------------------------------------------- displacement

def displacement(ctx, p, tick=0.25):
    """
    A run of `consecutive` same-direction candles that together:
      range (run high − run low) ≥ atr_mult × ATR(atr_period) of the candles BEFORE the run,
      body (first open → last close) ≥ min_body_ratio × range,
      close within close_pct % of the run's extreme (bullish: near the high),
      move ≥ min_points,
      optional: volume ≥ volume_mult × average volume of the previous volume_lookback candles,
      optional: leaves a fair value gap (then confirmed one candle later, when that gap exists).
    """
    s = ctx.series
    k = max(1, int(p.get("consecutive", 1)))
    atr = ctx.atr(int(p.get("atr_period", 14)))
    want = p.get("direction", "either")
    mult, body_min = float(p.get("atr_mult", 1.5)), float(p.get("min_body_ratio", 0.6))
    close_pct, min_pts = float(p.get("close_pct", 25)) / 100, float(p.get("min_points", 0) or 0)
    vol_mult, vol_look = float(p.get("volume_mult", 0) or 0), int(p.get("volume_lookback", 20))
    need_fvg = bool(p.get("require_fvg"))
    out, last_end = [], -1
    for i in range(k - 1, len(s)):
        a = i - k + 1
        if a <= last_end or a == 0 or atr[a - 1] is None or not _contiguous(s, a, i):
            continue
        ups = all(s.close[j] > s.open[j] for j in range(a, i + 1))
        downs = all(s.close[j] < s.open[j] for j in range(a, i + 1))
        if not (ups or downs):
            continue
        direction = "bullish" if ups else "bearish"
        if want not in ("either", direction):
            continue
        hi, lo = max(s.high[a:i + 1]), min(s.low[a:i + 1])
        rng = hi - lo
        if rng <= 0 or rng < mult * atr[a - 1] or rng < min_pts:
            continue
        if abs(s.close[i] - s.open[a]) < body_min * rng:
            continue
        if (hi - s.close[i] if ups else s.close[i] - lo) > close_pct * rng:
            continue
        if vol_mult:
            avg = _avg_volume(s, a, vol_look)
            if avg is None or sum(s.volume[a:i + 1]) < vol_mult * avg * k:
                continue
        end = i
        gap = None
        if need_fvg:            # one of the run's candles is the middle candle of a fair value gap
            if i + 1 >= len(s) or not _contiguous(s, a - 1, i + 1):
                continue
            for c2 in range(a, i + 1):
                c1, c3 = c2 - 1, c2 + 1
                if ups and s.high[c1] < s.low[c3]:
                    gap = (s.high[c1], s.low[c3])
                elif downs and s.low[c1] > s.high[c3]:
                    gap = (s.high[c3], s.low[c1])
                if gap:
                    end = max(c3, i)
                    break
            if not gap:
                continue
        out.append(_event("displacement", s, ctx, a, end, direction, high=hi, low=lo, size=rng,
                          atr=atr[a - 1], candles=k, fvg=gap,
                          detail=f"{direction} displacement {rng:.2f} pts ({rng / atr[a - 1]:.1f}× ATR)"))
        last_end = i
    return out


def is_displacement_candle(s, atr, i, atr_mult, body_ratio):
    rng = s.high[i] - s.low[i]
    return (i > 0 and atr[i - 1] is not None and rng > 0 and rng >= atr_mult * atr[i - 1]
            and abs(s.close[i] - s.open[i]) >= body_ratio * rng)


# ---------------------------------------------------------------- market structure

def structure(ctx, p):
    """
    Swing points: a high (low) above (below) the `swing_left` candles before and `swing_right` after,
    usable only once confirmed (after the right-hand candles have closed) and only for
    `max_swing_age` candles. A candle BREAKS structure when it closes (or trades, by="wick")
    beyond the most recent confirmed, unbroken swing high (bullish) or swing low (bearish).
    Each swing can be broken once. Trend = direction of the last break:
        BOS     a break in the same direction as the trend (continuation)
        CHoCH   the first break against the trend (change of character)
        MSS     a CHoCH whose breaking candle is a displacement candle
                (range ≥ mss_atr_mult × ATR(14) of earlier candles, body ≥ mss_body_ratio × range)
        first break of a new contract regime: trend not known yet → type "break" (counts only for "any")
    """
    s = ctx.series
    left, right = int(p.get("swing_left", 3)), int(p.get("swing_right", 3))
    by_close = p.get("by", "close") == "close"
    max_age = int(p.get("max_swing_age", 200))
    want_type, want_dir = p.get("type", "any"), p.get("direction", "either")
    atr = ctx.atr(14)
    mss_mult, mss_body = float(p.get("mss_atr_mult", 1.0)), float(p.get("mss_body_ratio", 0.5))
    points = sorted(swings(ctx, left, right), key=lambda x: x["confirmed_at"])
    highs, lows = [], []               # confirmed, unbroken: (occurred_index, price, swing)
    pi, trend, reg = 0, None, None
    out = []
    for j in range(len(s)):
        r = regime(s, s.ts[j])
        if r != reg:                     # a roll: forget every swing and the trend
            reg, trend, highs, lows = r, None, [], []
        while pi < len(points) and points[pi]["confirmed_at"] <= s.ts[j]:      # known before candle j opens
            sw = points[pi]
            if regime(s, sw["occurred_at"]) == reg:
                (highs if sw["kind"] == "swingh" else lows).append((sw["index"], sw["price"], sw))
            pi += 1
        highs = [h for h in highs if j - h[0] <= max_age]
        lows = [l for l in lows if j - l[0] <= max_age]
        if _bad(s, j):
            continue
        for side in ("bullish", "bearish"):
            pool = highs if side == "bullish" else lows
            if not pool:
                continue
            ref = max(pool, key=lambda x: x[0])          # most recent swing
            value = (s.close[j] if by_close else s.high[j]) if side == "bullish" else (s.close[j] if by_close else s.low[j])
            broke = value > ref[1] if side == "bullish" else value < ref[1]
            if not broke:
                continue
            kind = "break" if trend is None else ("BOS" if trend == side else "CHoCH")
            disp = is_displacement_candle(s, atr, j, mss_mult, mss_body)
            if kind == "CHoCH" and disp:
                kind = "MSS"
            trend = side
            if side == "bullish":
                highs = [h for h in highs if h[1] >= value]
            else:
                lows = [l for l in lows if l[1] <= value]
            types = {"any": True, "bos": kind == "BOS", "choch": kind in ("CHoCH", "MSS"), "mss": kind == "MSS"}
            if types.get(want_type, False) and want_dir in ("either", side):
                sw = ref[2]
                out.append(_event("market_structure_break", s, ctx, j, j, side, type=kind, level=ref[1],
                                  swing_at=sw["occurred_at"], swing_known_at=sw["confirmed_at"], displacement=disp,
                                  extreme=s.low[j] if side == "bullish" else s.high[j],
                                  detail=f"{kind} {side}: {'close' if by_close else 'trade'} beyond swing "
                                         f"{'high' if side == 'bullish' else 'low'} {ref[1]:.2f}"))
    return out


# ---------------------------------------------------------------- candle-based events

def candle(ctx, p):
    """A single candle meeting shape rules (direction, body share, range, close location, engulfing)."""
    s = ctx.series
    atr = ctx.atr(int(p.get("atr_period", 14)))
    want = p.get("direction", "either")
    body_min, rng_pts = float(p.get("min_body_ratio", 0) or 0), float(p.get("min_range_points", 0) or 0)
    rng_atr, loc = float(p.get("min_range_atr", 0) or 0), float(p.get("close_location_pct", 0) or 0) / 100
    engulf = bool(p.get("engulfing"))
    out = []
    for i in range(1, len(s)):
        if _bad(s, i):
            continue
        o, h, l, c = s.open[i], s.high[i], s.low[i], s.close[i]
        rng = h - l
        if rng <= 0 or c == o:
            continue
        direction = "bullish" if c > o else "bearish"
        if want not in ("either", direction):
            continue
        if abs(c - o) < body_min * rng or rng < rng_pts:
            continue
        if rng_atr and (atr[i - 1] is None or rng < rng_atr * atr[i - 1]):
            continue
        if loc and ((h - c) if direction == "bullish" else (c - l)) > loc * rng:
            continue
        if engulf:
            if not _contiguous(s, i - 1, i):
                continue
            po, pc = s.open[i - 1], s.close[i - 1]
            if not (min(o, c) <= min(po, pc) and max(o, c) >= max(po, pc) and (pc - po) * (c - o) < 0):
                continue
        out.append(_event("candle", s, ctx, i, i, direction, high=h, low=l, extreme=l if direction == "bullish" else h,
                          detail=f"{direction} candle, range {rng:.2f}"))
    return out


def directional_move(ctx, p):
    """Close-to-close move over the last `lookback` candles of at least min_points or min_atr × ATR (first candle of each run)."""
    s = ctx.series
    n_back = max(1, int(p.get("lookback", 6)))
    atr = ctx.atr(int(p.get("atr_period", 14)))
    pts, mult = float(p.get("min_points", 0) or 0), float(p.get("min_atr", 2.0) or 0)
    want = p.get("direction", "either")
    out, active = [], {"bullish": False, "bearish": False}
    for i in range(n_back, len(s)):
        a = i - n_back
        if not _contiguous(s, a, i) or atr[a] is None:
            active = {"bullish": False, "bearish": False}
            continue
        move = s.close[i] - s.close[a]
        need = max(pts, mult * atr[a])
        for side, ok in (("bullish", move >= need), ("bearish", -move >= need)):
            if ok and not active[side] and want in ("either", side):
                out.append(_event("directional_move", s, ctx, a + 1, i, side, move=move, extreme=min(s.low[a:i + 1]) if side == "bullish" else max(s.high[a:i + 1]),
                                  detail=f"{side} move {abs(move):.2f} pts in {n_back} candles"))
            active[side] = ok
    return out


def volume_spike(ctx, p):
    """Candle volume ≥ multiple × the average of the previous `lookback` candles (or the same time on previous days)."""
    s = ctx.series
    mult, look = float(p.get("multiple", 2.0)), int(p.get("lookback", 20))
    same_time = bool(p.get("same_time"))
    out = []
    by_clock = {}
    for i in range(len(s)):
        clock = sessions.to_et(s.ts[i]).strftime("%H:%M") if same_time else None
        if same_time:
            history = by_clock.setdefault(clock, [])
            avg = sum(history[-look:]) / len(history[-look:]) if len(history) >= 3 else None
        else:
            avg = _avg_volume(s, i, look)
        v = s.volume[i]
        if not _bad(s, i) and avg and v >= mult * avg and s.close[i] != s.open[i]:
            direction = "bullish" if s.close[i] > s.open[i] else "bearish"
            out.append(_event("volume_spike", s, ctx, i, i, direction, ratio=v / avg,
                              detail=f"volume {v:,.0f} = {v / avg:.1f}× average"))
        if same_time and v:
            by_clock[clock].append(v)
    return out


def volatility_expansion(ctx, p):
    """range_vs_atr: candle range ≥ multiple × ATR of earlier candles. atr_ratio: ATR(fast) crosses above multiple × ATR(slow)."""
    s = ctx.series
    mult = float(p.get("multiple", 1.5))
    out = []
    if p.get("mode", "range_vs_atr") == "range_vs_atr":
        atr = ctx.atr(int(p.get("atr_period", 14)))
        for i in range(1, len(s)):
            if _bad(s, i) or atr[i - 1] is None or s.close[i] == s.open[i]:
                continue
            rng = s.high[i] - s.low[i]
            if rng >= mult * atr[i - 1]:
                direction = "bullish" if s.close[i] > s.open[i] else "bearish"
                out.append(_event("volatility_expansion", s, ctx, i, i, direction, ratio=rng / atr[i - 1],
                                  detail=f"range {rng:.2f} = {rng / atr[i - 1]:.1f}× ATR"))
        return out
    fast, slow = ctx.atr(int(p.get("fast", 5))), ctx.atr(int(p.get("slow", 50)))
    above = False
    for i in range(len(s)):
        if fast[i] is None or slow[i] is None or _bad(s, i):
            continue
        now = fast[i] >= mult * slow[i]
        if now and not above:
            out.append(_event("volatility_expansion", s, ctx, i, i, None, ratio=fast[i] / slow[i],
                              detail=f"ATR({p.get('fast', 5)}) = {fast[i] / slow[i]:.1f}× ATR({p.get('slow', 50)})"))
        above = now
    return out


def equal_level_events(ctx, p, tick, high):
    from .detectors import equal_levels
    sw = swings(ctx, int(p.get("swing_left", 3)), int(p.get("swing_right", 3)))
    kind = "eqh" if high else "eql"
    out = []
    for x in equal_levels(sw, tick, float(p.get("tolerance_ticks", 2)), int(p.get("lookback_bars", 50)), int(p.get("touches", 2))):
        if x["kind"] == kind:
            out.append({"id": f"{kind}_{x['confirmed_at']}", "concept": "equal_highs" if high else "equal_lows",
                        "direction": None, "occurred_at": x["occurred_at"], "confirmed_at": x["confirmed_at"],
                        "tf": x["tf"], "day": x["day"], "price": x["price"],
                        "detail": f"{x['touches']} equal {'highs' if high else 'lows'} at {x['price']:.2f}"})
    return out


# ---------------------------------------------------------------- levels

def weekly_levels(base):
    """
    Previous week high/low: from complete, clean trading days (as for PDH/PDL) of the previous
    calendar week; every trading day in that week must be complete and at least 3 must exist.
    Usable from the start of the first trading day of the next week until that week ends.
    """
    days = daily_levels(base)
    weeks = {}
    for d, row in days.items():
        weeks.setdefault(d.isocalendar()[:2], []).append((d, row))
    out = []
    ordered = sorted(weeks)
    for wk, nxt in zip(ordered, ordered[1:]):
        rows = weeks[wk]
        if len(rows) < 3 or not all(r["complete"] for _, r in rows) or len({r["regime"] for _, r in rows}) != 1:
            continue
        first_next = min(d for d, _ in weeks[nxt])
        start, _ = sessions.day_bounds(first_next)
        last_next = max(d for d, _ in weeks[nxt])
        _, end = sessions.day_bounds(last_next)
        if regime(base, start) != rows[0][1]["regime"]:
            continue
        hi, lo = max(r["high"] for _, r in rows), min(r["low"] for _, r in rows)
        label = f"{min(d for d, _ in rows)}–{max(d for d, _ in rows)}"
        out.append({"kind": "pwh", "price": hi, "day": str(first_next), "available_from": start, "valid_until": end, "source_day": label})
        out.append({"kind": "pwl", "price": lo, "day": str(first_next), "available_from": start, "valid_until": end, "source_day": label})
    return out


def opening_ranges(base, minutes):
    """High/low of 09:30 ET → 09:30 + minutes, from clean base candles; known when the range has finished."""
    by_day = {}
    for i, t in enumerate(base.ts):
        if sessions.in_window(t, "09:30", "16:00"):
            by_day.setdefault(base.day[i], []).append(i)
    out = []
    for d, idx in by_day.items():
        start = sessions.from_et(d, sessions.parse_clock("09:30"))
        end = start + minutes * 60
        inside = [i for i in idx if start <= base.ts[i] < end]
        expected = minutes * 60 // base.step
        if len(inside) < expected or any(base.suspect(i) for i in inside):
            continue
        out.append({"kind": "or", "day": str(d), "high": max(base.high[i] for i in inside), "low": min(base.low[i] for i in inside),
                    "available_from": end, "valid_until": sessions.from_et(d, sessions.parse_clock("16:00"))})
    return out


def opening_range_breakouts(base, ctx, minutes):
    """First close beyond the opening range on the chosen timeframe after the range is known."""
    s = ctx.series
    out = []
    for r in opening_ranges(base, minutes):
        j = ctx.last_closed(r["available_from"]) + 1
        while j < len(s) and ctx.close_times[j] <= r["valid_until"]:
            if not _bad(s, j):
                if s.close[j] > r["high"] or s.close[j] < r["low"]:
                    up = s.close[j] > r["high"]
                    out.append({"id": f"orb_{s.ts[j]}", "concept": "opening_range", "direction": "bullish" if up else "bearish",
                                "occurred_at": s.ts[j], "confirmed_at": ctx.close_times[j], "tf": s.interval, "day": r["day"],
                                "level": r["high"] if up else r["low"], "range": r,
                                "detail": f"close {'above' if up else 'below'} the {minutes}-minute opening range"})
                    break
            j += 1
    return out


class Vwap:
    """Session VWAP from completed base candles: Σ(typical price × volume) ÷ Σ volume since the anchor."""

    def __init__(self, base, anchor="rth"):
        self.base, self.anchor = base, anchor
        self.close_times = [t + base.step for t in base.ts]
        self.cum = []
        pv = vol = 0.0
        key = None
        for i, t in enumerate(base.ts):
            k = self._session(t, base.day[i])
            if k != key:
                key, pv, vol = k, 0.0, 0.0
            if k is not None and not base.suspect(i) and base.volume[i]:
                tp = (base.high[i] + base.low[i] + base.close[i]) / 3
                pv += tp * base.volume[i]
                vol += base.volume[i]
            self.cum.append((k, pv / vol if vol else None))

    def _session(self, t, day):
        if self.anchor == "globex":
            return day
        return day if sessions.in_window(t, "09:30", "16:00") else None

    def at(self, t):
        """VWAP as known at time t (candles closed at or before t), or None outside the anchored session."""
        i = bisect_right(self.close_times, t) - 1
        if i < 0:
            return None
        key, value = self.cum[i]
        if key is None or key != self._session(t - 1, sessions.trading_day(t - 1)):
            return None
        return value


def vwap_crosses(base, ctx, anchor):
    v = Vwap(base, anchor)
    s = ctx.series
    out, prev = [], None
    for j in range(len(s)):
        value = v.at(ctx.close_times[j])
        if value is None or _bad(s, j):
            prev = None
            continue
        side = s.close[j] > value
        if prev is not None and side != prev:
            out.append({"id": f"vwap_{s.ts[j]}", "concept": "vwap", "direction": "bullish" if side else "bearish",
                        "occurred_at": s.ts[j], "confirmed_at": ctx.close_times[j], "tf": s.interval, "day": str(s.day[j]),
                        "level": value, "detail": f"close {'above' if side else 'below'} VWAP {value:.2f}"})
        prev = side
    return out


# ---------------------------------------------------------------- session ranges (London / Asia / NY morning)

def session_range_levels(base):
    """
    High and low of each named session window (concept_library.SESSION_LEVELS), per trading day,
    from clean base candles covering ≥ 90 % of the window. Known when the window ends; usable
    until the end of that trading day. A window that crosses a contract roll is skipped.
    """
    import concept_library as C
    by_day = {}
    for i, d in enumerate(base.day):
        by_day.setdefault(d, []).append(i)
    out = []
    for d, idx in by_day.items():
        _, day_end = sessions.day_bounds(d)
        for name, (hi_kind, lo_kind, start, end) in C.SESSION_LEVELS.items():
            a, b = sessions.window_bounds(d, start, end)
            inside = [i for i in idx if a <= base.ts[i] < b]
            expected = sum(1 for t in range(a, b, base.step) if sessions.standard_open(t))
            if not expected or len(inside) < 0.9 * expected or any(base.suspect(i) for i in inside):
                continue
            if regime(base, base.ts[inside[0]]) != regime(base, base.ts[inside[-1]]):
                continue
            hi, lo = max(base.high[i] for i in inside), min(base.low[i] for i in inside)
            for kind, price in ((hi_kind, hi), (lo_kind, lo)):
                out.append({"kind": kind, "price": price, "day": str(d), "available_from": b, "valid_until": day_end,
                            "session": name})
    return out


# ---------------------------------------------------------------- SMT divergence

def smt(ctx_a, ctx_b, p, tick_a=0.25, tick_b=0.25):
    """
    Two consecutive confirmed swing lows (highs) of market A, at most `lookback_bars` apart. Market B's
    extreme is taken over the same moments ± `max_lag_bars`. The swings DIVERGE when one market makes a
    lower low (higher high) and the other does not:
        lows  → bullish SMT        highs → bearish SMT
    Known when A's newer swing is confirmed AND B's comparison window has closed. Windows with missing
    or suspect candles in either market, or a contract roll between the swings, are skipped.
    """
    from .detectors import swings
    a, b = ctx_a.series, ctx_b.series
    if not len(a) or not len(b):
        return []
    step = a.step
    left, right = int(p.get("swing_left", 3)), int(p.get("swing_right", 3))
    look, lag = int(p.get("lookback_bars", 40)), int(p.get("max_lag_bars", 3))
    min_ticks = float(p.get("min_ticks", 2))
    want = p.get("direction", "either")
    index_b = {t: i for i, t in enumerate(b.ts)}
    points = swings(ctx_a, left, right)

    def window_extreme(t, low):
        vals = []
        for k in range(-lag, lag + 1):
            j = index_b.get(t + k * step)
            if j is None or "suspect" in b.flags[j]:
                return None
            vals.append(b.low[j] if low else b.high[j])
        return min(vals) if low else max(vals)

    out = []
    for kind, low, direction in (("swingl", True, "bullish"), ("swingh", False, "bearish")):
        if want not in ("either", direction):
            continue
        pts = [x for x in points if x["kind"] == kind]
        for prev, cur in zip(pts, pts[1:]):
            if cur["index"] - prev["index"] > look or regime(a, prev["occurred_at"]) != regime(a, cur["occurred_at"]):
                continue
            eb_prev, eb_cur = window_extreme(prev["occurred_at"], low), window_extreme(cur["occurred_at"], low)
            if eb_prev is None or eb_cur is None or regime(b, prev["occurred_at"]) != regime(b, cur["occurred_at"]):
                continue
            da, db = cur["price"] - prev["price"], eb_cur - eb_prev
            if (da > 0) == (db > 0) or abs(da) < min_ticks * tick_a or abs(db) < min_ticks * tick_b:
                continue                       # no divergence, or too small to mean anything
            confirmed = max(cur["confirmed_at"], cur["occurred_at"] + (lag + 1) * step)
            leader = "this market" if (da < 0 if low else da > 0) else "the comparison market"
            out.append({"id": f"smt_{a.interval}_{cur['occurred_at']}_{direction}", "concept": "smt_divergence",
                        "direction": direction, "occurred_at": cur["occurred_at"], "confirmed_at": confirmed, "tf": a.interval,
                        "day": cur["day"], "extreme": cur["price"], "compare": p.get("compare_with"),
                        "detail": f"{direction} SMT vs {p.get('compare_with')}: {leader} made a "
                                  f"{'lower low' if low else 'higher high'} the other didn't "
                                  f"({prev['price']:.2f} → {cur['price']:.2f} vs {eb_prev:.2f} → {eb_cur:.2f})"})
    out.sort(key=lambda e: e["confirmed_at"])
    return out
