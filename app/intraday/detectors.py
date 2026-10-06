"""
detectors.py — the market concepts, implemented once for every timeframe.

Each detector reads a TimeframeContext (or the base candles) and returns plain
dicts that always say WHEN things became known:

    occurred_at    when the event happened (start of the candle where it began)
    confirmed_at   the candle CLOSE at which the definition could first be checked
    available_from / valid_until   for price levels

Rules (all from concept_library definitions; nothing hidden):
    FVG           three CONTIGUOUS candles of the same trading day, none suspect;
                  exists at candle 3's close; states tracked on its own timeframe.
    PDH / PDL     previous trading day's high/low (full Globex day or RTH);
                  usable from the start of the next day; only from complete, clean days.
    ONH / ONL     18:00 → 09:30 ET high/low; final (usable) at 09:30 ET.
    Swings        left/right candle comparison; confirmed only after the right candles close.
    Equal H/L     ≥ N confirmed swings within a tick tolerance; confirmed with the last one.
    Sweeps        trade ≥ X ticks beyond a level that was ALREADY known and that price was
                  on the other side of; confirmed at the close that reclaims it within Y candles.

Data quality: suspect candles never create or confirm anything; contract rolls
("regimes") end every object, because prices on either side aren't comparable.
"""

from . import sessions
from .engine import regime

# ---------------------------------------------------------------- FVG

def _gap_ok(size, params, close_price, atr_value, tick):
    value, unit = float(params.get("min_gap") or 0), params.get("min_gap_unit", "points")
    if not value:
        return True
    if unit == "points":
        return size >= value
    if unit == "ticks":
        return size >= value * tick
    if unit == "percent":
        return size / close_price * 100 >= value
    if unit == "ATR":
        return atr_value is not None and size >= value * atr_value
    return True


def detect_fvgs(ctx, params, tick=0.25):
    s = ctx.series
    n, step = len(s), s.step
    direction = params.get("direction", "either")
    atr_period = int(params.get("atr_period", 14))
    atr = ctx.atr(atr_period)
    out, skipped = [], 0
    for c3 in range(2, n):
        c1, c2 = c3 - 2, c3 - 1
        if not (s.ts[c2] - s.ts[c1] == step and s.ts[c3] - s.ts[c2] == step and s.day[c1] == s.day[c3]):
            continue
        bull = s.high[c1] < s.low[c3]
        bear = s.low[c1] > s.high[c3]
        if not (bull or bear):
            continue
        kind = "bullish" if bull else "bearish"
        if direction != "either" and kind != direction:
            continue
        if any("suspect" in s.flags[i] or "after_gap" in s.flags[i] for i in (c1, c2, c3)):
            skipped += 1
            continue
        top, bottom = (s.low[c3], s.high[c1]) if bull else (s.low[c1], s.high[c3])
        size = top - bottom
        if not _gap_ok(size, params, s.close[c2], atr[c2], tick):
            continue
        if params.get("require_displacement"):
            rng = s.high[c2] - s.low[c2]
            body = abs(s.close[c2] - s.open[c2])
            if not rng or body / rng < float(params.get("min_body_ratio", 0.6)):
                continue
            if atr[c2 - 1 if c2 else c2] is None or rng < float(params.get("displacement_atr", 1.0)) * atr[c2 - 1]:
                continue
        fvg = {"id": f"fvg_{s.interval}_{s.ts[c1]}", "tf": s.interval, "direction": kind, "top": top, "bottom": bottom,
               "mid": (top + bottom) / 2, "size": size, "c1": s.ts[c1], "occurred_at": s.ts[c1],
               "created_at": ctx.close_times[c3], "confirmed_at": ctx.close_times[c3], "day": str(s.day[c3]),
               "regime": regime(s, s.ts[c3]), "states": [["CREATED", ctx.close_times[c3]]],
               "invalidation_rule": params.get("invalidation_rule", "close_beyond_far")}
        _track_fvg(ctx, fvg, c3, params)
        out.append(fvg)
    return out, skipped


def _track_fvg(ctx, f, c3, params):
    s = ctx.series
    bull = f["direction"] == "bullish"
    top, bottom, mid, size = f["top"], f["bottom"], f["mid"], f["size"]
    max_age = int(params.get("max_age_bars", 48))
    session_end = bool(params.get("expire_at_session_end", True))
    validation = params.get("validation_rule", "close_beyond")
    invalidation = params.get("invalidation_rule", "close_beyond_far")
    entered = validated = False
    closed_beyond_mid = False
    deepest = top if bull else bottom
    marks = {"entered_at": None, "partial_at": None, "half_at": None, "full_at": None,
             "validated_at": None, "invalidated_at": None, "expired_at": None}
    end, reason = None, None
    last_close = ctx.close_times[c3]
    for j in range(c3 + 1, len(s)):
        t = ctx.close_times[j]
        if session_end and s.day[j] != s.day[c3]:
            end, reason = last_close, "session ended"
            marks["expired_at"] = last_close
            break
        if j - c3 > max_age:
            end, reason = last_close, f"age limit ({max_age} candles)"
            marks["expired_at"] = last_close
            break
        if regime(s, s.ts[j]) != f["regime"]:
            end, reason = last_close, "contract roll"
            break
        if "suspect" in s.flags[j]:
            end, reason = last_close, "suspect data"
            break
        last_close = t
        hi, lo, cl, op = s.high[j], s.low[j], s.close[j], s.open[j]
        touch = lo <= top if bull else hi >= bottom
        if touch and not entered:
            entered = True
            marks["entered_at"] = t
        if entered:
            deepest = min(deepest, lo) if bull else max(deepest, hi)
            fill = (top - deepest) / size if bull else (deepest - bottom) / size
            if fill > 0 and not marks["partial_at"]:
                marks["partial_at"] = t
            if fill >= 0.5 and not marks["half_at"]:
                marks["half_at"] = t
            if fill >= 1 and not marks["full_at"]:
                marks["full_at"] = t
            if (cl < mid) if bull else (cl > mid):
                closed_beyond_mid = True
        # invalidation first (conservative when a candle could do both)
        inval = {"close_beyond_far": (cl < bottom) if bull else (cl > top),
                 "trade_beyond_far": (lo < bottom) if bull else (hi > top),
                 "close_beyond_mid": (cl < mid) if bull else (cl > mid)}[invalidation]
        if inval:
            marks["invalidated_at"] = t
            f["invalidation_price"] = cl if invalidation != "trade_beyond_far" else bottom if bull else top
            end, reason = t, "invalidated"
            break
        if entered and not validated:
            beyond = cl > top if bull else cl < bottom
            ok = {"close_beyond": beyond,
                  "hold_midpoint": beyond and not closed_beyond_mid,
                  "reaction_candle": beyond and ((cl > op) if bull else (cl < op)),
                  "close_beyond_mid": (cl > mid) if bull else (cl < mid)}[validation]
            if ok:
                validated = True
                marks["validated_at"] = t
                f["validation_bar"] = {"t": s.ts[j], "o": op, "h": hi, "l": lo, "c": cl}
    else:
        end, reason = None, "data ended"
    f.update(marks)
    f["end_at"], f["end_reason"] = end, reason
    names = [("ENTERED", "entered_at"), ("PARTIALLY_FILLED", "partial_at"), ("HALF_FILLED", "half_at"),
             ("FULLY_FILLED", "full_at"), ("VALIDATED", "validated_at"), ("INVALIDATED", "invalidated_at"),
             ("EXPIRED", "expired_at")]
    f["states"] += sorted(([name, marks[key]] for name, key in names if marks[key]), key=lambda x: x[1])


def fvg_state_at(f, t):
    """The FVG's state as known at time t (None before it exists)."""
    if t < f["created_at"]:
        return None
    current = None
    for name, at in f["states"]:
        if at <= t:
            current = name
    if f["end_at"] is not None and t >= f["end_at"] and current not in ("INVALIDATED", "EXPIRED"):
        return "ENDED"
    return current


# ---------------------------------------------------------------- reference levels

def daily_levels(base, rth=False):
    """
    Per trading day: high/low over the whole Globex day (or 09:30–16:00 ET), from CLEAN candles only.
    usable = clean candles cover ≥ 90% of the expected minutes, the day starts on time,
             and no contract roll happens inside it.
    """
    days = {}
    step = base.step
    for i, t in enumerate(base.ts):
        d = base.day[i]
        if rth and not sessions.in_window(t, "09:30", "16:00"):
            continue
        row = days.setdefault(d, {"high": None, "low": None, "count": 0, "first": t, "last": t,
                                  "regime": regime(base, t), "roll": False})
        row["last"] = t
        row["roll"] |= regime(base, t) != row["regime"]
        if "suspect" in base.flags[i]:
            continue
        row["high"] = base.high[i] if row["high"] is None else max(row["high"], base.high[i])
        row["low"] = base.low[i] if row["low"] is None else min(row["low"], base.low[i])
        row["count"] += 1
    for d, row in days.items():
        start, end = sessions.day_bounds(d)
        if rth:
            start, end = sessions.from_et(d, sessions.parse_clock("09:30")), sessions.from_et(d, sessions.parse_clock("16:00"))
        expected = sum(1 for p in range(start, end, step) if sessions.standard_open(p))
        row["coverage"] = row["count"] / expected if expected else 0
        row["complete"] = (row["high"] is not None and row["coverage"] >= 0.9 and row["first"] - start <= 30 * 60
                           and not row["roll"])
    return days


def reference_levels(base, pd_definition="globex"):
    """Every PDH/PDL/ONH/ONL level instance with the time it becomes usable."""
    out = []
    days = daily_levels(base, rth=pd_definition == "rth")
    ordered = sorted(days)
    for prev, d in zip(ordered, ordered[1:]):
        p = days[prev]
        start, end = sessions.day_bounds(d)
        if p["complete"] and regime(base, start) == p["regime"]:
            known = start if pd_definition == "globex" else sessions.from_et(prev, sessions.parse_clock("16:00"))
            for kind, price in (("pdh", p["high"]), ("pdl", p["low"])):
                out.append({"kind": kind, "price": price, "day": str(d), "available_from": max(known, start),
                            "valid_until": end, "source_day": str(prev)})
    # overnight
    by_day = {}
    for i, t in enumerate(base.ts):
        if sessions.in_window(t, "18:00", "09:30"):
            by_day.setdefault(base.day[i], []).append(i)
    for d, idx in by_day.items():
        start, end = sessions.day_bounds(d)
        final = sessions.from_et(d, sessions.parse_clock("09:30"))
        expected = sum(1 for p in range(start, final, base.step) if sessions.standard_open(p))
        clean = [i for i in idx if not base.suspect(i)]
        if expected == 0 or len(clean) < 0.9 * expected or base.ts[idx[0]] - start > 30 * 60:
            continue
        if regime(base, base.ts[idx[0]]) != regime(base, final):
            continue
        out.append({"kind": "onh", "price": max(base.high[i] for i in clean), "day": str(d), "available_from": final, "valid_until": end})
        out.append({"kind": "onl", "price": min(base.low[i] for i in clean), "day": str(d), "available_from": final, "valid_until": end})
    return out


def swings(ctx, left=3, right=3):
    s = ctx.series
    out = []
    for i in range(left, len(s) - right):
        window = range(i - left, i + right + 1)
        if any("suspect" in s.flags[k] for k in window) or s.day[i - left] != s.day[i + right]:
            continue
        if regime(s, s.ts[i - left]) != regime(s, s.ts[i + right]):
            continue
        h, l = s.high[i], s.low[i]
        if all(h > s.high[k] for k in window if k != i):
            out.append({"kind": "swingh", "price": h, "occurred_at": s.ts[i], "confirmed_at": ctx.close_times[i + right],
                        "index": i, "tf": s.interval, "day": str(s.day[i])})
        if all(l < s.low[k] for k in window if k != i):
            out.append({"kind": "swingl", "price": l, "occurred_at": s.ts[i], "confirmed_at": ctx.close_times[i + right],
                        "index": i, "tf": s.interval, "day": str(s.day[i])})
    return out


def equal_levels(swing_list, tick, tolerance_ticks=2, lookback_bars=50, touches=2):
    out = []
    for kind, label in (("swingh", "eqh"), ("swingl", "eql")):
        points = [p for p in swing_list if p["kind"] == kind]
        for k, p in enumerate(points):
            group = [q for q in points[:k + 1] if p["index"] - q["index"] <= lookback_bars
                     and abs(q["price"] - p["price"]) <= tolerance_ticks * tick]
            if len(group) >= touches:
                price = max(q["price"] for q in group) if label == "eqh" else min(q["price"] for q in group)
                out.append({"kind": label, "price": price, "occurred_at": group[0]["occurred_at"],
                            "confirmed_at": p["confirmed_at"], "touches": len(group), "tf": p["tf"], "day": p["day"]})
    return out


# ---------------------------------------------------------------- sweeps

SELL_SIDE = {"pdl", "onl", "sessl", "swingl", "eql", "pwl", "lonl", "asial", "nyaml"}
BUY_SIDE = {"pdh", "onh", "sessh", "swingh", "eqh", "pwh", "lonh", "asiah", "nyamh"}


def sweep_levels(base, ctx, params, tick):
    """Level instances usable by the sweep definition (each knows when it became available)."""
    side = params.get("side", "sell-side")
    defaults = {"sell-side": ["pdl", "onl", "swingl"], "buy-side": ["pdh", "onh", "swingh"],
                "both": ["pdl", "onl", "swingl", "pdh", "onh", "swingh"]}[side]
    wanted = set(params.get("levels") or defaults)
    wanted &= {"sell-side": SELL_SIDE, "buy-side": BUY_SIDE, "both": SELL_SIDE | BUY_SIDE}[side]
    levels = [l for l in reference_levels(base, params.get("day", "globex")) if l["kind"] in wanted]
    if wanted & {"pwh", "pwl"}:
        from .concepts import weekly_levels
        levels += [l for l in weekly_levels(base) if l["kind"] in wanted]
    if wanted & {"lonh", "lonl", "asiah", "asial", "nyamh", "nyaml"}:
        from .concepts import session_range_levels
        levels += [l for l in session_range_levels(base) if l["kind"] in wanted]
    if wanted & {"swingh", "swingl", "eqh", "eql"}:
        sw = swings(ctx, int(params.get("swing_left", 3)), int(params.get("swing_right", 3)))
        pool = [x for x in sw if x["kind"] in wanted]
        if wanted & {"eqh", "eql"}:
            pool += [x for x in equal_levels(sw, tick) if x["kind"] in wanted]
        for x in pool:            # a swing level stays usable until the end of the next trading day
            day = sessions.trading_day(x["confirmed_at"])
            next_end = sessions.day_bounds(day)[1] + 86400 * (3 if day.weekday() == 4 else 1)
            levels.append({**x, "available_from": x["confirmed_at"], "valid_until": next_end})
    return levels, wanted


def detect_sweeps(base, ctx, params, tick=0.25):
    s = ctx.series
    pen = float(params.get("penetration_ticks", 2)) * tick
    max_pen = float(params.get("max_penetration_ticks", 0) or 0) * tick
    reclaim = int(params.get("reclaim_bars", 3))
    levels, wanted = sweep_levels(base, ctx, params, tick)
    out = []
    n = len(s)
    for level in sorted(levels, key=lambda x: x["available_from"]):
        sell = level["kind"] in SELL_SIDE
        side = "sell-side" if sell else "buy-side"
        price = level["price"]
        start = ctx.last_closed(level["available_from"]) + 1     # first candle that opens after the level is known
        if start <= 0 or start >= n:
            continue
        reg = regime(s, level["available_from"])
        prior_close = s.close[start - 1]
        if (prior_close <= price) if sell else (prior_close >= price):
            continue                       # price was already through the level when it became known
        for j in range(start, n):
            if ctx.close_times[j] > level["valid_until"] or regime(s, s.ts[j]) != reg:
                break
            beyond = (s.low[j] <= price - pen) if sell else (s.high[j] >= price + pen)
            if not beyond:
                continue
            if "suspect" in s.flags[j]:
                break
            if max_pen and ((price - s.low[j]) if sell else (s.high[j] - price)) > max_pen:
                break                      # a breakout, not a sweep
            extreme = s.low[j] if sell else s.high[j]
            for k in range(j, min(j + reclaim, n)):
                if "suspect" in s.flags[k] or regime(s, s.ts[k]) != reg:
                    break
                extreme = min(extreme, s.low[k]) if sell else max(extreme, s.high[k])
                if max_pen and ((price - extreme) if sell else (extreme - price)) > max_pen:
                    break
                if (s.close[k] > price) if sell else (s.close[k] < price):
                    out.append({"id": f"sweep_{level['kind']}_{s.ts[j]}", "side": side,
                                "direction": "bullish" if sell else "bearish",
                                "level_kind": level["kind"], "level_price": price, "level_known_at": level["available_from"],
                                "occurred_at": s.ts[j], "confirmed_at": ctx.close_times[k], "extreme": extreme,
                                "penetration": round(abs(extreme - price) / tick), "tf": s.interval,
                                "day": str(s.day[k])})
                    break
            break                          # only the first interaction with a level counts
    # running session high/low (dynamic levels)
    if "sessl" in wanted:
        out += _session_extreme_sweeps(ctx, True, pen, max_pen, reclaim, "sell-side")
    if "sessh" in wanted:
        out += _session_extreme_sweeps(ctx, False, pen, max_pen, reclaim, "buy-side")
    out.sort(key=lambda x: x["confirmed_at"])
    return out


def _session_extreme_sweeps(ctx, sell, pen, max_pen, reclaim, side, warmup=30):
    s, out = ctx.series, []
    i, n = 0, len(s)
    while i < n:
        day = s.day[i]
        j = i
        level = None
        count = 0
        while j < n and s.day[j] == day:
            if level is not None and "suspect" not in s.flags[j] and count >= warmup:
                beyond = s.low[j] <= level - pen if sell else s.high[j] >= level + pen
                if beyond and not (max_pen and abs((s.low[j] if sell else s.high[j]) - level) > max_pen):
                    for k in range(j, min(j + reclaim, n)):
                        if s.day[k] != day or "suspect" in s.flags[k]:
                            break
                        if (s.close[k] > level) if sell else (s.close[k] < level):
                            out.append({"id": f"sweep_sess_{s.ts[j]}", "side": side, "direction": "bullish" if sell else "bearish",
                                        "level_kind": "sessl" if sell else "sessh", "level_price": level,
                                        "level_known_at": ctx.close_times[j - 1], "occurred_at": s.ts[j],
                                        "confirmed_at": ctx.close_times[k], "penetration": None, "tf": s.interval,
                                        "extreme": min(s.low[j:k + 1]) if sell else max(s.high[j:k + 1]), "day": str(day)})
                            break
            if level is None:
                level = s.low[j] if sell else s.high[j]
            else:
                level = min(level, s.low[j]) if sell else max(level, s.high[j])
            count += 1
            j += 1
        i = j
    return out
