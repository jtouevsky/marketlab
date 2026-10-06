"""
sessions.py — CME equity-index futures sessions (NQ, MNQ, ES, MES…) in US Eastern time.

All timestamps are stored in UTC; sessions are defined in America/New_York so
daylight-saving changes are handled by the timezone database, never by fixed
offsets.

Standard Globex hours: Sunday 18:00 ET → Friday 17:00 ET, with a daily halt
17:00–18:00 ET. A TRADING DAY runs from 18:00 ET the previous evening to
17:00 ET (Sunday evening belongs to Monday).

Holidays and early closes are NOT assumed to look like normal days: the
quality layer only counts candles as missing inside the span a day actually
traded, and flags days that are much shorter than normal ("short session").
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DAY_START = time(18, 0)     # previous evening
DAY_END = time(17, 0)


def to_et(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)


def from_et(day, clock):
    """UTC seconds for a wall-clock time on a calendar date in New York."""
    return int(datetime.combine(day, clock, tzinfo=ET).timestamp())


def trading_day(ts):
    """The trading day a timestamp belongs to (18:00 ET onwards counts toward the next weekday)."""
    local = to_et(ts)
    day = local.date()
    if local.time() >= DAY_START:
        day += timedelta(days=1)
    while day.weekday() >= 5:          # Saturday/Sunday evening → Monday
        day += timedelta(days=1)
    return day


def day_bounds(day):
    """(start, end) UTC seconds of a trading day: 18:00 ET the previous evening → 17:00 ET."""
    previous = day - timedelta(days=1)  # Monday's session opens Sunday 18:00
    return from_et(previous, DAY_START), from_et(day, DAY_END)


def standard_open(ts):
    """True if Globex is normally open at this instant (ignores holidays)."""
    local = to_et(ts)
    wd, t = local.weekday(), local.time()
    if wd == 5:                                   # Saturday
        return False
    if wd == 6:                                   # Sunday: opens 18:00
        return t >= DAY_START
    if wd == 4 and t >= DAY_END:                  # Friday after 17:00
        return False
    return not (DAY_END <= t < DAY_START)         # daily halt


def parse_clock(text):
    hh, mm = text.split(":")
    return time(int(hh), int(mm))


def in_window(ts, start, end):
    """Is ts inside a wall-clock window (ET)? Windows may wrap midnight (e.g. 18:00 → 09:30)."""
    t = to_et(ts).time()
    a, b = parse_clock(start), parse_clock(end)
    return a <= t < b if a < b else (t >= a or t < b)


def window_bounds(day, start, end):
    """UTC (start, end) of a named window on a trading day; wrapping windows start the previous evening."""
    a, b = parse_clock(start), parse_clock(end)
    day_start, _ = day_bounds(day)
    if a < b and a >= DAY_START:                  # entirely in the evening before
        prev = to_et(day_start).date()
        return from_et(prev, a), from_et(prev, b)
    if a < b:
        return from_et(day, a), from_et(day, b)
    prev = to_et(day_start).date()
    return from_et(prev, a), from_et(day, b)


def quarterly_expiries(years):
    """Third Friday of Mar/Jun/Sep/Dec: equity-index futures expiry days (for roll checks)."""
    out = []
    for year in years:
        for month in (3, 6, 9, 12):
            first = date(year, month, 1)
            friday = first + timedelta(days=(4 - first.weekday()) % 7)
            out.append(friday + timedelta(days=14))
    return out


def roll_window(day):
    """True within the usual roll period: from 8 days before expiry through expiry day."""
    for expiry in quarterly_expiries({day.year}):
        if expiry - timedelta(days=8) <= day <= expiry + timedelta(days=3):
            return True
    return False


# ---------------------------------------------------------------- contracts
MONTH_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}
CODE_MONTHS = {v: k for k, v in MONTH_CODES.items()}


def contract_code(root, expiry):
    """NQ + expiry 2026-12-18 → 'NQZ26'."""
    return f"{root}{MONTH_CODES[expiry.month]}{expiry.year % 100:02d}"


def contract_expiry(code):
    """'NQZ26' → date(2026, 12, 18) (third Friday of the contract month), or None."""
    import re
    m = re.match(r"^([A-Z0-9]+?)([HMUZ])(\d{2})$", code or "")
    if not m:
        return None
    year = 2000 + int(m.group(3))
    month = CODE_MONTHS[m.group(2)]
    return next(e for e in quarterly_expiries({year}) if e.month == month)


def contracts_between(root, first_day, last_day, ahead=1):
    """Quarterly contracts that were front month at some point in [first_day, last_day], plus `ahead` later ones."""
    expiries = sorted(quarterly_expiries({first_day.year - 1, first_day.year, last_day.year, last_day.year + 1}))
    live = [e for e in expiries if e >= first_day]
    out = [e for e in live if e <= last_day]
    later = [e for e in live if e > last_day][:1 + ahead]
    return [contract_code(root, e) for e in out + later]
