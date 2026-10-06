"""
quality.py — inspect normalized rows before anything is detected on them.

Detects (each with time range, size and a plain explanation):
    OUT-OF-ORDER DATA        rows not in time order (they are sorted, and counted)
    DUPLICATE CANDLE         the same timestamp twice (first kept; conflicting copies flagged)
    MISSING DATA             candles absent inside the span a trading day actually traded
    POSSIBLE ROLL ARTIFACT   a price jump near a quarterly futures roll
    ABNORMAL GAP             a jump between consecutive candles far beyond normal,
                             or a candle whose range is far beyond normal (bad print)
    STALE DATA               long runs of identical, zero-volume candles
    CONTRACT ROLL            the series switches contract (explicit, from contract-labelled sources)
    SOURCE DISAGREEMENT      two sources report different prices for the same candle
    CROSS-CHECK MISMATCH     NQ and MNQ (same market) disagree by more than normal
    SHORT SESSION            a day with far fewer candles than usual (holiday / early close?);
                             informational, not contamination

Nothing is interpolated. Bars inside suspicious periods get the "suspect" flag and
the detectors refuse to build events from them; bars right after missing data
get "after_gap" so three-candle patterns can't span a hole.
"""

import statistics

from . import sessions
from .bars import Series, seconds

MAX_LISTED = 300
GAP_SUSPECT_BARS = 3        # a hole of this many candles or more makes the next candle suspect
STALE_RUN = 15


def _iso(ts):
    return sessions.to_et(ts).strftime("%Y-%m-%d %H:%M ET")


def inspect(rows, interval, symbol=None, provider=None, contract=None):
    """rows: normalized rows (any order). Returns (Series, report)."""
    step = seconds(interval)
    anomalies, counts = [], {}

    def add(kind, start, end, bars, detail, severity="warning"):
        counts[kind] = counts.get(kind, 0) + 1
        if len(anomalies) < MAX_LISTED:
            anomalies.append({"type": kind, "start": start, "end": end, "start_label": _iso(start),
                              "end_label": _iso(end), "bars": bars, "detail": detail, "severity": severity})

    # 1. order
    out_of_order = sum(1 for a, b in zip(rows, rows[1:]) if b["timestamp"] < a["timestamp"])
    if out_of_order:
        add("OUT-OF-ORDER DATA", rows[0]["timestamp"], rows[-1]["timestamp"], out_of_order,
            f"{out_of_order} candles arrived out of time order; they were sorted.", "info")

    # 2. duplicates
    seen, unique = {}, []
    for row in rows:
        first = seen.get(row["timestamp"])
        if first is None:
            seen[row["timestamp"]] = row
            unique.append(row)
            continue
        same = all(abs(first[k] - row[k]) < 1e-9 for k in ("open", "high", "low", "close"))
        add("DUPLICATE CANDLE", row["timestamp"], row["timestamp"], 1,
            "Identical duplicate removed." if same else "Two different candles share this timestamp; the first was kept and the minute is marked suspect.",
            "info" if same else "warning")
        if not same:
            first.setdefault("_conflict", True)
    unique.sort(key=lambda r: r["timestamp"])

    series = Series(symbol or (unique[0]["symbol"] if unique else None), interval,
                    provider or (unique[0]["provider"] if unique else None), contract)
    for row in unique:
        series.append(row["timestamp"], row["open"], row["high"], row["low"], row["close"], row["volume"],
                      {"suspect"} if row.get("_conflict") or row.get("_disagree") else None,
                      contract=row.get("contract"), source=row.get("source"))
    series.day = [sessions.trading_day(t) for t in series.ts]
    n = len(series)
    series.meta["breaks"] = []
    if n < 3:
        return series, {"anomalies": anomalies, "counts": counts, "missing_bars": 0, "suspect_bars": 0}

    # 0. explicit contract changes (sources that label contracts): a documented roll, not an artifact.
    explicit = set()
    for i in range(1, n):
        if series.contracts[i] != series.contracts[i - 1]:
            explicit.add(i)
            series.meta["breaks"].append(series.ts[i])
            a, b = series.contracts[i - 1] or "unlabelled front month", series.contracts[i] or "unlabelled front month"
            add("CONTRACT ROLL", series.ts[i - 1], series.ts[i] + step, 1,
                f"The series switches from {a} to {b} here (roll rule below). Prices on either side aren't comparable, "
                "so no level, pattern or trade is carried across.", "info")
    # 0b. bars where two sources disagree (marked by the composite builder)
    run = []
    disagree = [i for i, row in enumerate(unique) if row.get("_disagree")]
    for i in disagree + [None]:
        if i is not None and (not run or series.ts[i] - series.ts[run[-1]] <= 30 * step):
            run.append(i)
            continue
        if run:
            detail = unique[run[0]]["_disagree"]
            add("SOURCE DISAGREEMENT", series.ts[run[0]], series.ts[run[-1]] + step, len(run),
                f"{len(run)} candle{'s' if len(run) != 1 else ''} where {detail}. Excluded from detection (we can't tell which source is right).")
        run = [i] if i is not None else []

    intraday = step < 86400
    missing_total = 0
    if intraday:
        # 3. missing candles inside each day's traded span
        for i in range(1, n):
            gap = series.ts[i] - series.ts[i - 1]
            if gap <= step or series.day[i] != series.day[i - 1]:
                continue
            positions = [p for p in range(series.ts[i - 1] + step, series.ts[i], step) if sessions.standard_open(p)]
            if not positions:
                continue
            missing_total += len(positions)
            series.flags[i].add("after_gap")
            if len(positions) >= GAP_SUSPECT_BARS:
                series.flags[i].add("suspect")
            add("MISSING DATA", positions[0], positions[-1] + step, len(positions),
                f"{len(positions)} {interval} candle{'s' if len(positions) != 1 else ''} missing.", "warning" if len(positions) >= GAP_SUSPECT_BARS else "info")

    # reference sizes for "abnormal"
    moves = [abs(series.close[i] / series.close[i - 1] - 1) for i in range(1, n) if series.day[i] == series.day[i - 1]]
    ranges = [(series.high[i] - series.low[i]) / series.close[i] for i in range(n)]
    typical_move = statistics.median(moves) if moves else 0
    typical_range = statistics.median(ranges) if ranges else 0
    gap_limit = max(0.004, 12 * typical_move)
    range_limit = max(0.01, 25 * typical_range)
    # Roll: Yahoo switches its continuous series to the next contract without adjustment, which shows up as
    # a jump of roughly the calendar basis. Inside each roll window, a jump in the MIDDLE of a session is the
    # roll (real news gaps usually happen at the reopen); a reopen gap is only blamed when no mid-session jump exists.
    roll_limit = max(0.006, 15 * typical_move)
    candidates = {}
    for i in range(1, n):
        if not sessions.roll_window(series.day[i]) or i in explicit:
            continue
        if series.contracts[i] is not None or series.contracts[i - 1] is not None:
            continue              # contract-labelled data: rolls are explicit (above), never guessed
        jump = abs(series.open[i] / series.close[i - 1] - 1)
        if jump <= roll_limit:
            continue
        expiry = min(sessions.quarterly_expiries({series.day[i].year}), key=lambda e: abs((e - series.day[i]).days))
        mid = series.day[i] == series.day[i - 1] and series.ts[i] - series.ts[i - 1] == step
        best = candidates.get(expiry)
        if best is None or (mid, jump) > (best[1], best[2]):
            candidates[expiry] = (i, mid, jump)
    roll_days, roll_bars = set(), set()
    series.meta["breaks"] = sorted(set(series.meta["breaks"]) | {series.ts[i] for i, _, _ in candidates.values()})
    for i, mid, jump in candidates.values():
        roll_days.add(series.day[i])
        roll_bars.add(i)
        add("POSSIBLE ROLL ARTIFACT", series.ts[i - 1], series.ts[i] + step, 1,
            f"Jump of {jump:.2%} {'in the middle of a session' if mid else 'at a session reopen'} near a quarterly futures roll, "
            "in a continuous series without roll adjustment. The whole trading day is excluded from detection.")
    for i in range(1, n):
        if i in roll_bars or i in explicit:
            continue
        jump = abs(series.open[i] / series.close[i - 1] - 1)
        if series.day[i] == series.day[i - 1] and series.ts[i] - series.ts[i - 1] == step and jump > gap_limit:
            series.flags[i].add("suspect")
            add("ABNORMAL GAP", series.ts[i - 1], series.ts[i] + step, 1,
                f"Open {jump:.2%} away from the previous close (normal is about {typical_move:.3%}).")
    for i in range(n):
        if ranges[i] > range_limit:
            series.flags[i].add("suspect")
            add("ABNORMAL GAP", series.ts[i], series.ts[i] + step, 1,
                f"Candle range {ranges[i]:.2%} of price (normal is about {typical_range:.3%}); possible bad print.")
    if roll_days:
        for i in range(n):
            if series.day[i] in roll_days:
                series.flags[i].add("suspect")

    # 5. stale runs
    run_start = None
    for i in range(1, n + 1):
        flat = i < n and series.volume[i] == 0 and series.high[i] == series.low[i] == series.close[i - 1]
        if flat and run_start is None:
            run_start = i
        elif not flat and run_start is not None:
            if i - run_start >= STALE_RUN:
                for j in range(run_start, i):
                    series.flags[j].add("suspect")
                add("STALE DATA", series.ts[run_start], series.ts[i - 1] + step, i - run_start,
                    f"{i - run_start} identical zero-volume candles in a row.")
            run_start = None

    # 6. short sessions (informational)
    if intraday:
        per_day = {}
        for d in series.day:
            per_day[d] = per_day.get(d, 0) + 1
        typical = statistics.median(per_day.values()) if per_day else 0
        days = sorted(per_day)
        for d in days[1:-1]:
            if typical and per_day[d] < 0.6 * typical:
                start, end = sessions.day_bounds(d)
                add("SHORT SESSION", start, end, per_day[d],
                    f"{per_day[d]} candles vs a typical {int(typical)}: probably a holiday or early close. Not treated as missing.", "info")

    report = {"anomalies": anomalies, "counts": counts, "missing_bars": missing_total,
              "suspect_bars": sum(1 for f in series.flags if "suspect" in f),
              "thresholds": {"abnormal_gap": gap_limit, "abnormal_range": range_limit, "missing_suspect_bars": GAP_SUSPECT_BARS,
                             "stale_run": STALE_RUN}}
    return series, report


def cross_check(series, other, point_limit=8.0):
    """
    NQ and MNQ track the same market. Where both have the same candle and the closes
    disagree by more than max(point_limit, 6 × the typical difference), mark it suspect
    in `series` (we can't tell which feed is wrong). Returns the number flagged.
    """
    index = {t: i for i, t in enumerate(other.ts)}
    diffs = []
    pairs = []
    month = lambda code: code[-3:] if code and code != "mixed" else code
    for i, t in enumerate(series.ts):
        j = index.get(t)
        if j is not None and series.contracts and other.contracts and month(series.contracts[i]) != month(other.contracts[j]):
            continue              # different contract months (each labelled): not comparable
        if j is not None:
            d = abs(series.close[i] - other.close[j])
            diffs.append(d)
            pairs.append((i, d))
    if not diffs:
        return 0, None
    limit = max(point_limit, 6 * statistics.median(diffs))
    flagged = [(i, d) for i, d in pairs if d > limit]
    for i, _ in flagged:
        series.flags[i].add("suspect")
    # Group consecutive flagged candles into periods and say what they look like.
    periods, run = [], []
    for i, d in flagged:
        if run and i - run[-1][0] > 30:
            periods.append(run)
            run = []
        run.append((i, d))
    if run:
        periods.append(run)
    step = series.step
    described = []
    for run in periods:
        first, last = run[0][0], run[-1][0]
        typical = statistics.median(d for _, d in run)
        roll = typical > 0.005 * series.close[first] and len(run) >= 10
        described.append({
            "type": "ROLL MISMATCH" if roll else "CROSS-CHECK MISMATCH",
            "start": series.ts[first], "end": series.ts[last] + step, "start_label": _iso(series.ts[first]),
            "end_label": _iso(series.ts[last] + step), "bars": len(run), "severity": "warning",
            "detail": (f"{series.symbol} and {other.symbol} were on different contract months (difference ≈ {typical:.0f} points): "
                       "Yahoo rolled the two continuous series at different times. Excluded from detection.") if roll else
                      (f"{series.symbol} and {other.symbol} disagree by about {typical:.1f} points (normal ≈ {statistics.median(diffs):.2f}). "
                       "One of the feeds has a bad print; excluded."),
        })
    return len(flagged), {"compared": len(diffs), "median_diff": statistics.median(diffs), "limit": limit,
                          "flagged": len(flagged), "periods": described}
