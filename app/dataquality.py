"""
dataquality.py — checks every price series BEFORE any statistics use it.

Why this exists: a single >50% daily move can be
    A. bad data from the provider (a wrong print),
    B. a stock split the provider failed to adjust for, or
    C. a real market event.
These need opposite treatment. A and B are not economic returns and must
be corrected; C is real and must be KEPT (deleting real crashes would make
history look safer than it was). So we classify, act only on strong
evidence, and report everything so the user can inspect it.

Severity levels
    INFO          worth knowing, nothing changed (e.g. a real extreme move)
    WARNING       could affect results, kept as-is (e.g. a long data gap)
    LIKELY_ERROR  strong evidence of bad data, corrected or removed

Every issue records: date, check, severity, detail, and the action taken.
"""

from datetime import date
from statistics import median

INFO, WARNING, LIKELY_ERROR = "info", "warning", "likely_error"

EXTREME_MOVE = 0.40          # |daily return| above this gets classified
REVERT_TOLERANCE = 0.12      # spike-and-revert: next close within 12% of the day before
SPLIT_TOLERANCE = 0.06       # price jump within 6% of a split ratio
GAP_DAYS = 10                # calendar days between rows that count as a data gap
STALE_RUN = 5                # identical closes with zero volume, in a row
VOLUME_SPIKE = 10            # volume above 10x its 50-day median
# Whole-number splits only: an unrecorded split moves the price by at least -50% or +100%.
# (3-for-2 style ratios are rare and would flag ordinary +50% rallies.)
COMMON_SPLIT_RATIOS = [2, 3, 4, 5, 8, 10, 15, 20, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 8, 1 / 10, 1 / 15, 1 / 20]


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def check_daily(bars, splits=None):
    """
    bars: dict of equal-length lists: date, open, high, low, close, volume (oldest first).
    splits: {date_text: ratio} from the provider's corporate-action records (e.g. 4.0 = 4-for-1).
    Returns (cleaned_bars, report). The input is not modified.
    """
    splits = splits or {}
    b = {key: list(values) for key, values in bars.items()}
    issues = []

    def issue(day, check, severity, detail, action="kept", value=None):
        issues.append({"date": day, "check": check, "severity": severity, "detail": detail,
                       "action": action, "value": value})

    # 1. Missing / zero / negative closes -> removed
    keep = [i for i, c in enumerate(b["close"]) if c is not None and c == c and c > 0]
    removed = len(b["close"]) - len(keep)
    if removed:
        issue(None, "invalid_price", LIKELY_ERROR, f"{removed} rows with a missing, zero or negative close", "removed")
        b = {key: [values[i] for i in keep] for key, values in b.items()}

    n = len(b["close"])
    closes = b["close"]

    # 2. Extreme daily moves: split? bad print? real?
    drop = set()
    i = 1
    while i < n:
        move = closes[i] / closes[i - 1] - 1
        if abs(move) <= EXTREME_MOVE:
            i += 1
            continue
        day = b["date"][i]
        jump = closes[i] / closes[i - 1]            # e.g. 0.25 after an unadjusted 4-for-1 split
        recorded = [r for d, r in splits.items() if abs(_days(d, day)) <= 4]
        matches_recorded = any(abs(jump * r - 1) < SPLIT_TOLERANCE for r in recorded)
        reverts = i + 1 < n and abs(closes[i + 1] / closes[i - 1] - 1) < REVERT_TOLERANCE

        if matches_recorded:
            # Unadjusted split: rescale everything BEFORE this day so the series is continuous.
            factor = jump
            for j in range(i):
                for key in ("open", "high", "low", "close"):
                    if b[key][j] is not None:
                        b[key][j] *= factor
                if b["volume"][j] is not None and factor:
                    b["volume"][j] /= factor
            issue(day, "unadjusted_split", LIKELY_ERROR,
                  f"{move:+.1%} jump matches a recorded {recorded[0]:g}-for-1 split that wasn't applied to earlier prices",
                  "corrected: earlier prices rescaled", move)
        elif reverts:
            drop.add(i)
            issue(day, "spike_and_revert", LIKELY_ERROR,
                  f"{move:+.1%} one-day spike fully reversed the next day: typical of a bad price print",
                  "removed this day", move)
            i += 1
        elif any(abs(jump * r - 1) < 0.03 for r in COMMON_SPLIT_RATIOS) and abs(move) > 0.45:
            issue(day, "possible_split", WARNING,
                  f"{move:+.1%} jump is close to a common split ratio but no split is on record. Kept; check before relying on it.",
                  "kept", move)
        else:
            issue(day, "extreme_move", INFO,
                  f"{move:+.1%} in one day. Consistent with surrounding prices, so treated as a real market move.",
                  "kept", move)
        i += 1

    if drop:
        b = {key: [values[k] for k in range(n) if k not in drop] for key, values in b.items()}
        n = len(b["close"])
        closes = b["close"]

    # 3. Gaps in the data
    for k in range(1, n):
        gap = _days(b["date"][k - 1], b["date"][k])
        if gap > GAP_DAYS:
            issue(b["date"][k], "data_gap", WARNING, f"{gap} calendar days with no prices before this date")

    # 4. Stale quotes: the same close for several days with no volume
    run = 1
    for k in range(1, n):
        same = closes[k] == closes[k - 1] and not b["volume"][k]
        run = run + 1 if same else 1
        if run == STALE_RUN:
            issue(b["date"][k], "stale_quotes", WARNING, f"{STALE_RUN}+ days with an unchanged price and no volume")

    # 5. Abnormal volume (informational, the 10 largest only)
    spikes = []
    volumes = b["volume"]
    for k in range(50, n):
        window = [v for v in volumes[k - 50:k] if v]
        if len(window) >= 30 and volumes[k]:
            base = median(window)
            if base > 0 and volumes[k] / base > VOLUME_SPIKE:
                spikes.append((volumes[k] / base, b["date"][k]))
    for ratio, day in sorted(spikes, reverse=True)[:10]:
        issue(day, "volume_spike", INFO, f"volume {ratio:.0f}x its 50-day median")

    # 6. OHLC consistency (counted, not changed)
    bad_ohlc = sum(1 for k in range(n)
                   if None not in (b["high"][k], b["low"][k], b["open"][k])
                   and (b["high"][k] < max(b["open"][k], closes[k]) * 0.999 or b["low"][k] > min(b["open"][k], closes[k]) * 1.001))
    if bad_ohlc:
        issue(None, "ohlc_inconsistent", INFO, f"{bad_ohlc} days where high/low don't contain open/close (provider rounding)")

    summary = {sev: sum(1 for x in issues if x["severity"] == sev) for sev in (INFO, WARNING, LIKELY_ERROR)}
    report = {
        "checks": ["invalid prices", "extreme moves (split / bad print / real)", "data gaps", "stale quotes",
                   "abnormal volume", "OHLC consistency"],
        "issues": issues,
        "summary": summary,
        "corrections": sum(1 for x in issues if x["action"] != "kept"),
        "rows": n,
    }
    return b, report


def issues_between(report, start_date, end_date):
    """Issues inside a date range (plus undated, series-wide ones)."""
    return [x for x in report["issues"] if x["date"] is None or start_date <= x["date"] <= end_date]
