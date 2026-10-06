"""
concept_library.py — the Concept Library for intraday research.

A CONCEPT is a market event with an explicit, configurable definition.
Discretionary words ("fair value gap", "liquidity sweep", "validation") have
no single accepted meaning, so MarketLab never hides one. Every concept:

  * has parameters you can change (the definition is data, not code);
  * renders its exact algorithm as plain sentences (explain());
  * states WHEN it is known, split into
        occurrence    - the moment the event happens
        confirmation  - the moment the definition can be checked with data
                        that existed then (never earlier)
        eligible      - the earliest moment a strategy may act on it
    This split is what keeps look-ahead bias out of the engine. A swing high
    "occurs" at its peak but is only CONFIRMED once its right-hand bars have
    closed; nothing may use it before that.

Detection: every concept with "detection": True is found in candles by
intraday/detectors.py or intraday/concepts.py, look-ahead safe. Concepts that
still can't be measured (economic events: no free calendar source yet) say
exactly what is missing and name the nearest measurable alternative.

This module also does deterministic "formalization" (analyze_observation):
it reads a natural-language observation and lists the vague terms that need
a definition. No AI involved; AI can later propose definitions on top.
"""

import re

# --- Instruments ---------------------------------------------------------------
# One market, different contract economics. Signals are computed on the market;
# P&L uses the contract's point value. Source: CME Group contract specifications.
INSTRUMENTS = {
    "NQ":  {"label": "E-mini Nasdaq-100 futures", "market": "NASDAQ100_FUT", "exchange": "CME Globex",
            "tick_size": 0.25, "point_value": 20.0, "tick_value": 5.0, "available": True},
    "MNQ": {"label": "Micro E-mini Nasdaq-100 futures", "market": "NASDAQ100_FUT", "exchange": "CME Globex",
            "tick_size": 0.25, "point_value": 2.0, "tick_value": 0.5, "available": True},
    "ES":  {"label": "E-mini S&P 500 futures", "market": "SP500_FUT", "exchange": "CME Globex",
            "tick_size": 0.25, "point_value": 50.0, "tick_value": 12.5, "available": False},
    "MES": {"label": "Micro E-mini S&P 500 futures", "market": "SP500_FUT", "exchange": "CME Globex",
            "tick_size": 0.25, "point_value": 5.0, "tick_value": 1.25, "available": False},
    "RTY": {"label": "E-mini Russell 2000 futures", "market": "R2K_FUT", "exchange": "CME Globex",
            "tick_size": 0.1, "point_value": 50.0, "tick_value": 5.0, "available": False},
    "M2K": {"label": "Micro E-mini Russell 2000 futures", "market": "R2K_FUT", "exchange": "CME Globex",
            "tick_size": 0.1, "point_value": 5.0, "tick_value": 0.5, "available": False},
    "YM":  {"label": "E-mini Dow futures", "market": "DJIA_FUT", "exchange": "CBOT",
            "tick_size": 1.0, "point_value": 5.0, "tick_value": 5.0, "available": False},
}

TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
TIMEFRAME_LABELS = {"1m": "1-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute",
                    "1h": "1-hour", "4h": "4-hour", "1d": "daily"}

# --- Sessions (US Eastern time; DST handled by the timezone, not by fixed UTC offsets) ---
# CME Globex equity-index futures trade Sunday–Friday 18:00–17:00 ET with a daily
# 17:00–18:00 halt. Holidays and early closes come from an exchange calendar (Phase 2);
# no day is assumed to have identical hours.
SESSION_TZ = "America/New_York"
SESSIONS = {
    "globex_day":  {"label": "Full trading day", "start": "18:00", "end": "17:00", "note": "Starts the previous evening"},
    "overnight":   {"label": "Overnight", "start": "18:00", "end": "09:30", "note": "Previous evening to the NY cash open"},
    "premarket":   {"label": "Pre-market", "start": "04:00", "end": "09:30", "note": ""},
    "ny_open":     {"label": "New York open", "start": "09:30", "end": "10:00", "note": ""},
    "rth":         {"label": "Regular trading hours", "start": "09:30", "end": "16:00", "note": "Cash-equity hours"},
    "morning":     {"label": "NY morning", "start": "09:30", "end": "12:00", "note": ""},
    "lunch":       {"label": "Lunch", "start": "12:00", "end": "13:30", "note": ""},
    "afternoon":   {"label": "Afternoon", "start": "13:30", "end": "16:00", "note": ""},
    "asia":        {"label": "Asia session", "start": "20:00", "end": "00:00", "note": "MarketLab default (ICT-style Asia range)"},
    "london":      {"label": "London session", "start": "02:00", "end": "05:00", "note": "MarketLab default (London open range)"},
    "custom":      {"label": "Custom", "start": None, "end": None, "note": "Your own window"},
}
# Named session RANGES used as liquidity levels (high/low of the window, known when the window ends).
SESSION_LEVELS = {"asia": ("asiah", "asial", "20:00", "00:00"), "london": ("lonh", "lonl", "02:00", "05:00"),
                  "ny_am": ("nyamh", "nyaml", "09:30", "12:00")}


# --- Parameter helpers -----------------------------------------------------------
def choice(options, default, label, help=""):
    return {"kind": "choice", "options": options, "default": default, "label": label, "help": help}

def number(default, label, unit="", minimum=0, maximum=None, step=1, help=""):
    return {"kind": "number", "default": default, "label": label, "unit": unit,
            "min": minimum, "max": maximum, "step": step, "help": help}

def flag(default, label, help=""):
    return {"kind": "bool", "default": default, "label": label, "help": help}


TF = choice(TIMEFRAMES, "5m", "Timeframe")


def _tf(p):
    return TIMEFRAME_LABELS.get(p.get("timeframe"), p.get("timeframe") or "chosen")


def _size(p):
    unit = p.get("min_gap_unit", "points")
    value = p.get("min_gap", 0)
    if not value:
        return "any size"
    if unit == "ATR":
        return f"at least {value:g} × ATR({p.get('atr_period', 14):g})"
    if unit == "percent":
        return f"at least {value:g}% of price"
    return f"at least {value:g} {unit}"


# --- FVG ---------------------------------------------------------------------------
FVG_STATES = [
    {"id": "CREATED", "rule": "Candle 3 has closed and the gap meets the size rules.",
     "known": "At the close of candle 3."},
    {"id": "ACTIVE", "rule": "Created, and not yet fully filled, invalidated or expired.",
     "known": "Re-checked at every candle close."},
    {"id": "ENTERED", "rule": "A later candle trades into the gap (bullish: its low ≤ gap top; bearish: its high ≥ gap bottom).",
     "known": "During the candle that first touches it; confirmed at that candle's close."},
    {"id": "PARTIALLY_FILLED", "rule": "Fill % > 0. Fill % = deepest penetration since creation ÷ gap size.",
     "known": "At each candle close."},
    {"id": "HALF_FILLED", "rule": "Fill % ≥ 50 (price reached the gap's midpoint).",
     "known": "At the close of the candle that reached the midpoint."},
    {"id": "FULLY_FILLED", "rule": "Price traded through the far edge (bullish: low ≤ gap bottom).",
     "known": "At the close of the candle that reached the far edge."},
    {"id": "VALIDATED", "rule": "Set by the validation rule (below). Only possible after ENTERED.",
     "known": "At the close of the validating candle — never earlier."},
    {"id": "INVALIDATED", "rule": "Set by the invalidation rule (below).",
     "known": "At the close of the invalidating candle."},
    {"id": "EXPIRED", "rule": "Neither validated nor invalidated within the age limit, or the session ended.",
     "known": "When the limit is reached."},
]


def explain_fvg(p):
    tf = _tf(p)
    direction = p.get("direction", "either")
    lines = []
    if direction in ("bullish", "either"):
        lines.append(f"Bullish FVG ({tf}): three consecutive candles where candle 1's HIGH is below candle 3's LOW. "
                     "The gap is the price range between candle 1's high (bottom) and candle 3's low (top).")
    if direction in ("bearish", "either"):
        lines.append(f"Bearish FVG ({tf}): candle 1's LOW is above candle 3's HIGH. "
                     "The gap is between candle 3's high (bottom) and candle 1's low (top).")
    lines.append(f"Size: the gap must be {_size(p)}.")
    if p.get("require_displacement"):
        lines.append(f"Displacement: candle 2's body must be at least {p.get('min_body_ratio', 0.6):.0%} of its full range"
                     f" and its range at least {p.get('displacement_atr', 1.0):g} × ATR({p.get('atr_period', 14):g}).")
    lines.append("The FVG does not exist until candle 3 has CLOSED.")
    validation = {
        "close_beyond": "after price has entered the gap, a candle closes back beyond it in the gap's direction "
                        "(bullish: close above the gap top) without first closing beyond the far edge",
        "hold_midpoint": "after price has entered the gap, no candle closes beyond its midpoint, and a candle then closes back beyond the gap",
        "reaction_candle": "after price has entered the gap, a candle closes in the gap's direction (bullish: close > open) "
                           "with its close outside the gap",
        "close_beyond_mid": "after price has entered the gap, a candle closes beyond the gap's midpoint in the gap's direction "
                            "(bullish: close above the midpoint)",
    }[p.get("validation_rule", "close_beyond")]
    invalidation = {
        "close_beyond_far": "a candle CLOSES beyond the far edge (bullish: close below the gap bottom)",
        "trade_beyond_far": "price TRADES beyond the far edge, even intrabar",
        "close_beyond_mid": "a candle closes beyond the midpoint",
    }[p.get("invalidation_rule", "close_beyond_far")]
    lines.append(f"Validated: {validation}.")
    lines.append(f"Invalidated when {invalidation}.")
    ends = " or at the end of its trading day (17:00 ET) if earlier" if p.get("expire_at_session_end", True) else ""
    lines.append(f"Expires after {p.get('max_age_bars', 48):g} {tf} candles{ends}. States are checked on {tf} candle closes; "
                 "candles flagged by the data-quality layer end tracking.")
    return lines


# --- Liquidity -----------------------------------------------------------------------
LEVELS = {
    "pdh": "previous day high", "pdl": "previous day low",
    "pwh": "previous week high", "pwl": "previous week low",
    "onh": "overnight high", "onl": "overnight low",
    "sessh": "session high so far", "sessl": "session low so far",
    "lonh": "London session high", "lonl": "London session low", "asiah": "Asia session high", "asial": "Asia session low",
    "nyamh": "NY-morning high", "nyaml": "NY-morning low",
    "swingh": "most recent confirmed swing high", "swingl": "most recent confirmed swing low",
    "eqh": "equal highs", "eql": "equal lows",
}


def explain_sweep(p):
    if p.get("side") == "both":
        sell = explain_sweep({**p, "side": "sell-side"})
        buy = explain_sweep({**p, "side": "buy-side"})
        return [sell[0], buy[0], "A sell-side sweep counts as BULLISH, a buy-side sweep as BEARISH."] + sell[1:]
    tf = _tf(p)
    side = p.get("side", "sell-side")
    levels = p.get("levels") or (["pdl", "onl", "swingl"] if side == "sell-side" else ["pdh", "onh", "swingh"])
    names = ", ".join(LEVELS.get(level, level) for level in levels)
    beyond = "below" if side == "sell-side" else "above"
    back = "above" if side == "sell-side" else "below"
    lines = [
        f"{side.capitalize()} liquidity sweep ({tf}): price trades at least {p.get('penetration_ticks', 2)} ticks {beyond} "
        f"a reference level ({names}), then a {tf} candle CLOSES back {back} that level within {p.get('reclaim_bars', 3)} candles.",
        "Occurrence: the moment price first trades beyond the level.",
        "Confirmation: the close of the candle that returns back through the level. Before that close it is only a break, not a sweep.",
        "Reference levels are only used once they exist (the overnight low is final at 09:30 ET; a swing low only after its confirmation bars), "
        "only if price was still on the near side of the level at that moment, and only the first interaction with each level counts.",
        f"Swing levels use {p.get('swing_left', 3)} candles before / {p.get('swing_right', 3)} after on the {tf} chart and stay usable until the end of the next trading day.",
    ]
    if p.get("max_penetration_ticks"):
        lines.append(f"If price goes more than {p['max_penetration_ticks']} ticks beyond the level, it counts as a breakout, not a sweep.")
    return lines


def explain_swing(p, high=True):
    left, right = p.get("left_bars", 3), p.get("right_bars", 3)
    word = "high" if high else "low"
    comp = "higher" if high else "lower"
    return [
        f"Swing {word} ({_tf(p)}): a candle whose {word} is {comp} than the {left} candles before it and the {right} candles after it.",
        f"Occurrence: the swing candle itself. Confirmation: only after the {right} later candles have closed — "
        "it cannot be used before then (this is the classic source of look-ahead bias).",
    ]


def explain_equal(p, high=True):
    word = "highs" if high else "lows"
    return [f"Equal {word} ({_tf(p)}): at least {p.get('touches', 2)} confirmed swing {word} within "
            f"{p.get('tolerance_ticks', 2)} ticks of each other in the last {p.get('lookback_bars', 50)} candles.",
            "Known once the last of those swings is confirmed."]


def explain_level(name, p):
    day = "the full Globex trading day (18:00–17:00 ET)" if p.get("day", "globex") == "globex" else "regular trading hours (09:30–16:00 ET)"
    return {
        "pdh": [f"Previous day high: the highest price of the previous trading day, using {day}.", "Known from the start of the next trading day."],
        "pdl": [f"Previous day low: the lowest price of the previous trading day, using {day}.", "Known from the start of the next trading day."],
        "onh": ["Overnight high: highest price from 18:00 ET (previous evening) to 09:30 ET.", "Final at 09:30 ET; before then it is only the overnight high so far."],
        "onl": ["Overnight low: lowest price from 18:00 ET (previous evening) to 09:30 ET.", "Final at 09:30 ET; before then it is only the overnight low so far."],
        "sessh": ["Session high: highest price since the session start, up to and including the current candle.", "Changes as the session runs."],
        "sessl": ["Session low: lowest price since the session start, up to and including the current candle.", "Changes as the session runs."],
        "pwh": ["Previous week high: the highest price of the previous calendar week's trading days (complete, clean days only; at least 3).",
                "Known from the start of the next week's first trading day; usable through that week."],
        "pwl": ["Previous week low: the lowest price of the previous calendar week's trading days (complete, clean days only; at least 3).",
                "Known from the start of the next week's first trading day; usable through that week."],
    }[name]


def explain_displacement(p):
    k = int(p.get("consecutive", 1))
    direction = p.get("direction", "either")
    who = (f"A {direction} candle" if direction != "either" else "A candle") if k == 1 else \
        f"{k} consecutive {direction if direction != 'either' else 'same-direction'} candles"
    size = "Its range" if k == 1 else "Their combined range"
    lines = [f"Displacement ({_tf(p)}): {who}. {size} is at least {p.get('atr_mult', 1.5):g} × ATR({p.get('atr_period', 14):g}) of the candles "
             f"BEFORE the move; the body (open → close) is at least {p.get('min_body_ratio', 0.6):.0%} of that range; it closes within "
             f"{p.get('close_pct', 25):g}% of its extreme (bullish: near the high)"
             + (f"; it moves at least {p.get('min_points'):g} points" if p.get("min_points") else "") + "."]
    if p.get("volume_mult"):
        lines.append(f"Volume of the move must be at least {p.get('volume_mult'):g}× the average of the previous {p.get('volume_lookback', 20):g} candles.")
    if p.get("require_fvg"):
        lines.append("It must leave a fair value gap; it is then confirmed one candle later, when that gap exists.")
    lines.append("Occurrence: the first candle's open. Confirmation: the close of the last candle needed. "
                 "Never spans missing data, a new trading day or a contract roll.")
    return lines


def explain_window(p):
    s = SESSIONS.get(p.get("session", "morning"), SESSIONS["morning"])
    start, end = (p.get("start"), p.get("end")) if p.get("session") == "custom" else (s["start"], s["end"])
    return [f"Time window: {s['label']}, {start or '?'}–{end or '?'} US Eastern time (adjusts automatically for daylight saving).",
            "Uses the exchange calendar: holidays and early closes are not treated as normal days."]


CONCEPTS = [
    {"id": "fvg", "label": "Fair Value Gap", "category": "Structure", "aliases": [r"fair[\s-]*value[\s-]*gaps?", r"\bfvgs?\b", r"imbalances?"],
     "summary": "A three-candle price gap that the middle candle moved through without overlap.",
     "params": {"direction": choice(["bullish", "bearish", "either"], "either", "Direction"), "timeframe": TF,
                "min_gap": number(0, "Minimum gap size", step=0.25), "min_gap_unit": choice(["points", "ticks", "percent", "ATR"], "points", "Size unit"),
                "atr_period": number(14, "ATR period", "candles", 2, 200),
                "require_displacement": flag(False, "Require displacement in candle 2"),
                "min_body_ratio": number(0.6, "Min. candle-2 body ÷ range", "", 0, 1, 0.05),
                "displacement_atr": number(1.0, "Min. candle-2 range", "× ATR", 0, 10, 0.1),
                "validation_rule": choice(["close_beyond", "hold_midpoint", "reaction_candle", "close_beyond_mid"], "close_beyond", "Validation rule"),
                "invalidation_rule": choice(["close_beyond_far", "trade_beyond_far", "close_beyond_mid"], "close_beyond_far", "Invalidation rule"),
                "max_age_bars": number(48, "Expires after", "candles", 1, 2000),
                "expire_at_session_end": flag(True, "Also expires at the end of its trading day")},
     "states": FVG_STATES, "explain": explain_fvg, "detection": True},
    {"id": "liquidity_sweep", "label": "Liquidity sweep", "category": "Liquidity",
     "aliases": [r"liquidity", r"\bsweep(?:s|ed|ing)?\b", r"\bswept\b", r"stop[\s-]*(?:run|hunt)s?", r"\braid(?:s|ed)?\b", r"\bgrab(?:s|bed)?\b"],
     "summary": "Price briefly trades beyond a known level, then closes back through it.",
     "params": {"side": choice(["sell-side", "buy-side", "both"], "sell-side", "Side",
                               "Sell-side = below lows (bullish); buy-side = above highs (bearish); both = either, direction from the side"),
                "timeframe": TF, "levels": {"kind": "multi", "options": list(LEVELS), "labels": LEVELS, "default": [], "label": "Reference levels"},
                "penetration_ticks": number(2, "Min. distance beyond the level", "ticks", 1, 400),
                "max_penetration_ticks": number(0, "Max. distance (0 = no limit)", "ticks", 0, 2000),
                "reclaim_bars": number(3, "Must close back within", "candles", 1, 100),
                "day": choice(["globex", "rth"], "globex", "Previous-day levels from"),
                "swing_left": number(3, "Swing levels: candles before", "", 1, 50),
                "swing_right": number(3, "Swing levels: candles after", "", 1, 50)},
     "explain": explain_sweep, "detection": True},
    {"id": "swing_high", "label": "Swing high", "category": "Structure", "aliases": [r"swing[\s-]*highs?"],
     "params": {"timeframe": TF, "left_bars": number(3, "Candles before", "", 1, 50), "right_bars": number(3, "Candles after (confirmation)", "", 1, 50)},
     "summary": "A local peak, confirmed only after later candles.", "explain": lambda p: explain_swing(p, True), "detection": True},
    {"id": "swing_low", "label": "Swing low", "category": "Structure", "aliases": [r"swing[\s-]*lows?"],
     "params": {"timeframe": TF, "left_bars": number(3, "Candles before", "", 1, 50), "right_bars": number(3, "Candles after (confirmation)", "", 1, 50)},
     "summary": "A local trough, confirmed only after later candles.", "explain": lambda p: explain_swing(p, False), "detection": True},
    {"id": "equal_highs", "label": "Equal highs", "category": "Liquidity", "aliases": [r"equal[\s-]*highs?", r"\beqh\b", r"double[\s-]*top"],
     "params": {"timeframe": TF, "touches": number(2, "Swings", "", 2, 10), "tolerance_ticks": number(2, "Within", "ticks", 0, 100),
                "lookback_bars": number(50, "Look back", "candles", 5, 2000),
                "swing_left": number(3, "Swing: candles before", "", 1, 50), "swing_right": number(3, "Swing: candles after", "", 1, 50)},
     "summary": "Several swing highs at about the same price.", "explain": lambda p: explain_equal(p, True), "detection": True},
    {"id": "equal_lows", "label": "Equal lows", "category": "Liquidity", "aliases": [r"equal[\s-]*lows?", r"\beql\b", r"double[\s-]*bottom"],
     "params": {"timeframe": TF, "touches": number(2, "Swings", "", 2, 10), "tolerance_ticks": number(2, "Within", "ticks", 0, 100),
                "lookback_bars": number(50, "Look back", "candles", 5, 2000),
                "swing_left": number(3, "Swing: candles before", "", 1, 50), "swing_right": number(3, "Swing: candles after", "", 1, 50)},
     "summary": "Several swing lows at about the same price.", "explain": lambda p: explain_equal(p, False), "detection": True},
    {"id": "pdh", "label": "Previous day high", "category": "Levels", "aliases": [r"previous[\s-]*day[\s-]*high", r"\bpdh\b", r"yesterday'?s high"],
     "params": {"day": choice(["globex", "rth"], "globex", "Day definition")}, "summary": "", "explain": lambda p: explain_level("pdh", p), "detection": True},
    {"id": "pdl", "label": "Previous day low", "category": "Levels", "aliases": [r"previous[\s-]*day[\s-]*low", r"\bpdl\b", r"yesterday'?s low"],
     "params": {"day": choice(["globex", "rth"], "globex", "Day definition")}, "summary": "", "explain": lambda p: explain_level("pdl", p), "detection": True},
    {"id": "overnight_high", "label": "Overnight high", "category": "Levels", "aliases": [r"overnight[\s-]*high", r"\bonh\b"],
     "params": {}, "summary": "", "explain": lambda p: explain_level("onh", p), "detection": True},
    {"id": "overnight_low", "label": "Overnight low", "category": "Levels", "aliases": [r"overnight[\s-]*low", r"\bonl\b"],
     "params": {}, "summary": "", "explain": lambda p: explain_level("onl", p), "detection": True},
    {"id": "session_high", "label": "Session high", "category": "Levels", "aliases": [r"session[\s-]*high", r"high of (?:the )?day", r"\bhod\b"],
     "params": {}, "summary": "", "explain": lambda p: explain_level("sessh", p), "detection": True},
    {"id": "session_low", "label": "Session low", "category": "Levels", "aliases": [r"session[\s-]*low", r"low of (?:the )?day", r"\blod\b"],
     "params": {}, "summary": "", "explain": lambda p: explain_level("sessl", p), "detection": True},
    {"id": "previous_week_high", "label": "Previous week high", "category": "Levels", "aliases": [r"previous[\s-]*week(?:'s)?[\s-]*high", r"\bpwh\b", r"last week'?s high"],
     "params": {}, "summary": "", "explain": lambda p: explain_level("pwh", p), "detection": True, "kind": "level"},
    {"id": "previous_week_low", "label": "Previous week low", "category": "Levels", "aliases": [r"previous[\s-]*week(?:'s)?[\s-]*low", r"\bpwl\b", r"last week'?s low"],
     "params": {}, "summary": "", "explain": lambda p: explain_level("pwl", p), "detection": True, "kind": "level"},
    {"id": "vwap", "label": "VWAP", "category": "Levels", "aliases": [r"\bvwap\b"],
     "params": {"anchor": choice(["globex", "rth"], "rth", "Anchored at", "Globex 18:00 ET or RTH 09:30 ET"), "timeframe": TF},
     "summary": "Volume-weighted average price of the session so far.", "detection": True, "kind": "level",
     "explain": lambda p: [f"VWAP: cumulative (typical price × volume) ÷ cumulative volume since {'18:00 ET' if p.get('anchor') == 'globex' else '09:30 ET'}, "
                           "using completed candles only (typical price = (high + low + close) ÷ 3). Candles flagged by the data-quality layer are left out.",
                           f"“Price above/below VWAP” compares the latest closed candle with VWAP at that moment. "
                           f"“Crosses” = the first {_tf(p)} close on the other side; known at that close.",
                           "Outside the anchored session there is no VWAP (the condition is false)."]},
    {"id": "opening_range", "label": "Opening range", "category": "Levels", "aliases": [r"opening[\s-]*range", r"\borb\b"],
     "params": {"minutes": choice([5, 15, 30, 60], 15, "Length (minutes)"), "timeframe": TF}, "summary": "", "detection": True, "kind": "level",
     "explain": lambda p: [f"Opening range: high and low from 09:30 ET to 09:30 + {p.get('minutes', 15)} minutes, from complete, clean candles.",
                           "Known only once the range has finished; nothing may use it earlier.",
                           f"Breakout: the first {_tf(p)} candle that CLOSES above the high (bullish) or below the low (bearish) afterwards, "
                           "until 16:00 ET. Known at that close."]},
    {"id": "displacement", "label": "Displacement", "category": "Structure",
     "aliases": [r"displacement", r"\bimpulsive?\b", r"strong (?:move|candle)s?", r"expansion candle"],
     "params": {"timeframe": TF, "direction": choice(["bullish", "bearish", "either"], "either", "Direction"),
                "atr_mult": number(1.5, "Range at least", "× ATR", 0.1, 10, 0.1), "atr_period": number(14, "ATR period", "candles", 2, 200),
                "min_body_ratio": number(0.6, "Body at least", "of the range", 0, 1, 0.05),
                "close_pct": number(25, "Closes within", "% of the extreme", 0, 100, 5),
                "consecutive": number(1, "Candles in the move", "", 1, 10),
                "min_points": number(0, "Minimum move", "points", 0, 2000, 0.25),
                "require_fvg": flag(False, "Must leave a fair value gap"),
                "volume_mult": number(0, "Volume at least (0 = off)", "× average", 0, 20, 0.1),
                "volume_lookback": number(20, "Volume average of", "candles", 2, 500)},
     "summary": "An unusually large, decisive candle (or short run of candles).", "detection": True, "kind": "event",
     "explain": lambda p: explain_displacement(p)},
    {"id": "market_structure_break", "label": "Market structure (BOS / CHoCH / MSS)", "category": "Structure",
     "aliases": [r"market[\s-]*structure[\s-]*(?:break|shift)", r"structure[\s-]*(?:break|shift)", r"\bbos\b", r"\bmss\b", r"\bchoch\b",
                 r"break of structure", r"change of character", r"market structure", r"\bshift in (?:market )?structure\b"],
     "params": {"timeframe": TF, "type": choice(["any", "bos", "choch", "mss"], "any", "Break type",
                                                "BOS = with the trend, CHoCH = first break against it, MSS = CHoCH made by a displacement candle"),
                "direction": choice(["bullish", "bearish", "either"], "either", "Direction"),
                "by": choice(["close", "wick"], "close", "Break by"),
                "swing_left": number(3, "Swing: candles before", "", 1, 50), "swing_right": number(3, "Swing: candles after (confirmation)", "", 1, 50),
                "max_swing_age": number(200, "Swings older than this are ignored", "candles", 5, 5000),
                "mss_atr_mult": number(1.0, "MSS candle range at least", "× ATR(14)", 0, 10, 0.1),
                "mss_body_ratio": number(0.5, "MSS candle body at least", "of its range", 0, 1, 0.05)},
     "summary": "A candle closing beyond the most recent confirmed swing.", "detection": True, "kind": "event",
     "explain": lambda p: [f"Swings ({_tf(p)}): a high (low) above (below) the {p.get('swing_left', 3)} candles before and {p.get('swing_right', 3)} after. "
                           f"A swing exists only after its {p.get('swing_right', 3)} later candles have CLOSED; until then nothing can break it.",
                           f"Break: a candle {'CLOSES' if p.get('by', 'close') == 'close' else 'TRADES'} beyond the most recent confirmed, unbroken swing high "
                           "(bullish) or swing low (bearish). Each swing can be broken once; swings older than "
                           f"{p.get('max_swing_age', 200)} candles are ignored.",
                           "Trend = direction of the last break. BOS = a break in the trend's direction; CHoCH = the first break against it; "
                           f"MSS = a CHoCH whose breaking candle has range ≥ {p.get('mss_atr_mult', 1.0):g} × ATR(14) of earlier candles and body ≥ "
                           f"{p.get('mss_body_ratio', 0.5):.0%} of its range. After a contract roll the trend is unknown again (first break = 'break').",
                           f"This condition uses: {dict(any='any break', bos='BOS only', choch='CHoCH or MSS', mss='MSS only')[p.get('type', 'any')]}"
                           + (f", {p.get('direction')} only" if p.get("direction", "either") != "either" else "") + ". Known at the breaking candle's close."]},
    {"id": "volume_spike", "label": "Volume expansion", "category": "Volume", "aliases": [r"volume[\s-]*(?:spikes?|expansion|surge)", r"high volume", r"heavy volume"],
     "params": {"timeframe": TF, "multiple": number(2.0, "Volume at least", "× average", 1, 20, 0.1),
                "lookback": number(20, "Average of previous", "candles", 2, 500),
                "same_time": flag(False, "Compare with the same time of day on previous days")},
     "summary": "", "detection": True, "kind": "event",
     "explain": lambda p: [f"Volume expansion ({_tf(p)}): candle volume ≥ {p.get('multiple', 2.0)} × the average of the previous "
                           f"{p.get('lookback', 20)} {'same-time-of-day candles' if p.get('same_time') else 'candles'} (this candle excluded). "
                           "Its direction is the candle's colour.",
                           "Known at the candle's close. Yahoo's futures volume can be incomplete; check the Dataset panel."]},
    {"id": "volatility_expansion", "label": "Volatility expansion (ATR)", "category": "Volatility",
     "aliases": [r"volatility[\s-]*expan\w*", r"\batr expansion\b", r"range expansion", r"wide[\s-]*range candle"],
     "params": {"timeframe": TF, "mode": choice(["range_vs_atr", "atr_ratio"], "range_vs_atr", "Measure"),
                "multiple": number(1.5, "At least", "×", 1, 20, 0.1), "atr_period": number(14, "ATR period", "candles", 2, 200),
                "fast": number(5, "Fast ATR", "candles", 2, 100), "slow": number(50, "Slow ATR", "candles", 5, 1000)},
     "summary": "", "detection": True, "kind": "event",
     "explain": lambda p: [f"Volatility expansion ({_tf(p)}): " + (
         f"a candle whose range is at least {p.get('multiple', 1.5):g} × ATR({p.get('atr_period', 14):g}) of the candles before it."
         if p.get("mode", "range_vs_atr") == "range_vs_atr" else
         f"ATR({p.get('fast', 5):g}) rises above {p.get('multiple', 1.5):g} × ATR({p.get('slow', 50):g}) (the first candle where that becomes true)."),
         "Known at the candle's close."]},
    {"id": "candle", "label": "Candle condition", "category": "Candles",
     "aliases": [r"engulfing", r"rejection candle", r"bullish candle", r"bearish candle", r"pin bar", r"close (?:strong|near the (?:high|low))"],
     "params": {"timeframe": TF, "direction": choice(["bullish", "bearish", "either"], "either", "Direction"),
                "min_body_ratio": number(0, "Body at least", "of range", 0, 1, 0.05), "min_range_points": number(0, "Range at least", "points", 0, 2000, 0.25),
                "min_range_atr": number(0, "Range at least (0 = off)", "× ATR", 0, 10, 0.1), "atr_period": number(14, "ATR period", "candles", 2, 200),
                "close_location_pct": number(0, "Closes within (0 = off)", "% of its extreme", 0, 100, 5),
                "engulfing": flag(False, "Body engulfs the previous candle's body (opposite colour)")},
     "summary": "A single candle with explicit shape rules.", "detection": True, "kind": "event",
     "explain": lambda p: [f"Candle ({_tf(p)}): a {p.get('direction', 'either') if p.get('direction', 'either') != 'either' else 'bullish or bearish'} candle"
                           + (f", body ≥ {p.get('min_body_ratio'):.0%} of its range" if p.get("min_body_ratio") else "")
                           + (f", range ≥ {p.get('min_range_points'):g} points" if p.get("min_range_points") else "")
                           + (f", range ≥ {p.get('min_range_atr'):g} × ATR({p.get('atr_period', 14):g})" if p.get("min_range_atr") else "")
                           + (f", closing within {p.get('close_location_pct'):g}% of its high (bullish) / low (bearish)" if p.get("close_location_pct") else "")
                           + (", whose body engulfs the previous opposite-colour candle's body" if p.get("engulfing") else "") + ".",
                           "Known at the candle's close."]},
    {"id": "directional_move", "label": "Directional move (momentum)", "category": "Momentum",
     "aliases": [r"momentum", r"directional move", r"strong (?:push|rally|selloff|sell-off)", r"trend(?:ing)? (?:up|down)"],
     "params": {"timeframe": TF, "direction": choice(["bullish", "bearish", "either"], "either", "Direction"),
                "lookback": number(6, "Over the last", "candles", 1, 500), "min_points": number(0, "At least", "points", 0, 5000, 0.25),
                "min_atr": number(2.0, "At least", "× ATR", 0, 50, 0.1), "atr_period": number(14, "ATR period", "candles", 2, 200)},
     "summary": "Price has moved far in one direction recently.", "detection": True, "kind": "event",
     "explain": lambda p: [f"Directional move ({_tf(p)}): the close is at least max({p.get('min_points', 0):g} points, {p.get('min_atr', 2.0):g} × ATR) "
                           f"away from the close {p.get('lookback', 6):g} candles earlier. Only the first candle of each run counts.",
                           "Known at the candle's close."]},
    {"id": "smt_divergence", "label": "SMT divergence", "category": "Intermarket",
     "aliases": [r"\bsmt\b", r"divergence (?:against|with|vs) (?:es|ym|nq)"],
     "params": {"timeframe": TF, "compare_with": choice(["ES", "NQ", "YM"], "ES", "Compare with"),
                "direction": choice(["bullish", "bearish", "either"], "either", "Direction"),
                "swing_left": number(3, "Swing: candles before", "", 1, 50), "swing_right": number(3, "Swing: candles after (confirmation)", "", 1, 50),
                "lookback_bars": number(40, "Previous swing within", "candles", 3, 2000),
                "max_lag_bars": number(3, "Max timing difference", "candles", 0, 50),
                "min_ticks": number(2, "Each market's swing difference at least", "ticks", 0, 400)},
     "summary": "One market makes a new extreme that a correlated market fails to confirm.", "detection": True, "kind": "event",
     "explain": lambda p: [f"Bullish SMT ({_tf(p)}): this market makes a LOWER confirmed swing low than its previous swing low "
                           f"(within {p.get('lookback_bars', 40)} candles), while {p.get('compare_with', 'ES')}'s lowest price around the same two moments "
                           f"(± {p.get('max_lag_bars', 3)} candles) does NOT make a lower low (or the other way round). Both differences must be at least "
                           f"{p.get('min_ticks', 2):g} ticks, so noise isn't counted. Bearish SMT is the mirror image with highs.",
                           f"Swings use {p.get('swing_left', 3)} candles before / {p.get('swing_right', 3)} after; the divergence is known only when the "
                           "newer swing is confirmed and the comparison window has closed.",
                           f"Needs {p.get('compare_with', 'ES')} candles at the same timestamps; candles flagged by data quality in either market block it."]},
    {"id": "unsupported", "label": "Not measurable yet", "category": "Other", "aliases": [], "hidden": True,
     "params": {"label": {"kind": "text", "default": "", "label": "Concept"}, "needs": {"kind": "text", "default": "", "label": "Needs"},
                "nearest": {"kind": "text", "default": "", "label": "Nearest measurable"}},
     "summary": "A concept MarketLab recognised but can't detect yet.", "detection": False, "kind": "event",
     "missing": "a detector that doesn't exist yet", "nearest": None,
     "explain": lambda p: [f"{p.get('label') or 'This concept'} can't be measured yet: it needs {p.get('needs') or 'a detector'}.",
                           ("Nearest measurable version: " + p["nearest"] + ".") if p.get("nearest") else "There is no close measurable version yet."]},
    {"id": "time_window", "label": "Time window / session", "category": "Time",
     "aliases": [r"\bny\b|new[\s-]*york", r"\bsession\b", r"\bmorning\b", r"\blunch\b", r"\bafternoon\b", r"\bopen\b", r"\blondon\b", r"\basia\b", r"before \d{1,2}(?::\d\d)?"],
     "params": {"session": choice(list(SESSIONS), "morning", "Session"), "start": {"kind": "time", "default": "09:30", "label": "Start (ET)"},
                "end": {"kind": "time", "default": "11:00", "label": "End (ET)"}},
     "summary": "Which part of the trading day a setup may happen in.", "explain": explain_window, "detection": True},
    {"id": "economic_event", "label": "Economic event", "category": "Events",
     "aliases": [r"\bcpi\b", r"\bfomc\b", r"\bnfp\b", r"\bppi\b", r"non[\s-]*farm", r"fed (?:speech|meeting|decision)", r"news (?:day|event)s?", r"earnings"],
     "params": {"events": {"kind": "multi", "options": ["CPI", "FOMC", "NFP", "PPI", "Fed speech", "Mega-cap tech earnings"],
                           "default": ["CPI", "FOMC", "NFP"], "label": "Events"},
                "mode": choice(["only_event_days", "exclude_event_days", "compare"], "compare", "Use")},
     "summary": "Scheduled macro releases.", "detection": False,
     "missing": "an economic-calendar data source (none is connected yet)",
     "nearest": "a time-window condition around the release time (e.g. 08:30–09:00 ET), or keep it as a note",
     "explain": lambda p: [f"Scheduled event days: {', '.join(p.get('events') or [])}.",
                           "Needs an economic-calendar source (planned); sentiment is never inferred from headlines."]},
]
CONCEPTS_BY_ID = {c["id"]: c for c in CONCEPTS}


def default_params(concept_id):
    concept = CONCEPTS_BY_ID[concept_id]
    return {name: spec["default"] for name, spec in concept["params"].items()}


def explain(concept_id, params=None):
    """The exact, current definition as plain sentences."""
    concept = CONCEPTS_BY_ID.get(concept_id)
    if not concept:
        raise KeyError(concept_id)
    merged = {**default_params(concept_id), **(params or {})}
    return concept["explain"](merged)


def library():
    """The library as JSON (no functions), for the UI."""
    out = []
    for c in CONCEPTS:
        if c.get("hidden"):
            continue
        out.append({"id": c["id"], "label": c["label"], "category": c["category"], "summary": c.get("summary", ""),
                    "params": c["params"], "param_order": list(c["params"]), "states": c.get("states"), "detection": c["detection"],
                    "default_explanation": explain(c["id"])})
    return {"concepts": out, "instruments": INSTRUMENTS, "timeframes": TIMEFRAMES,
            "timeframe_labels": TIMEFRAME_LABELS, "sessions": SESSIONS, "session_tz": SESSION_TZ}


# --- Formalization (deterministic) ------------------------------------------------
# Words that are not market events but still hide a decision.
RELATIONS = [
    {"key": "timing", "label": "“Earlier” / timing", "patterns": [r"\bearlier\b", r"\bbefore\b", r"\bafter\b", r"\balready\b", r"\bfirst\b", r"\bwithin\b", r"\bthen\b"],
     "why": "How long before the setup may this happen? Same session, last N candles, since 18:00 ET?"},
    {"key": "direction", "label": "“Directional”", "patterns": [r"\bdirectional\b", r"\bin the direction\b", r"\bopposite\b", r"\bsame direction\b"],
     "why": "Which direction counts, and what is 'opposite'? Usually the FVG's bullish/bearish side."},
    {"key": "validation", "label": "“Validation” / confirmation", "patterns": [r"\bvalidat\w*", r"\bconfirm\w*", r"\brespect\w*"],
     "why": "Which exact candle event counts as validated? (See the FVG validation rule.)"},
    {"key": "freshness", "label": "“Fresh” / unmitigated", "patterns": [r"\bfresh\b", r"\bunmitigated\b", r"\buntested\b", r"\bnew\b"],
     "why": "How old may it be, and may it have been touched already?"},
]
# Every testable strategy needs these, whether or not the observation mentions them.
REQUIRED = [
    {"key": "outcome", "label": "Outcome (what “works” means)", "patterns": [r"\bcontinu\w*", r"\breversal\b|\breverses?\b", r"\bmoves?\b", r"\bworks?\b"],
     "why": "Continuation of how much, measured how, by when? This becomes the trade's target/exit or a measured outcome."},
    {"key": "entry", "label": "Entry", "patterns": [r"\benter\w*", r"\bentry\b", r"\bbuy\b", r"\bsell\b", r"\blong\b", r"\bshort\b"],
     "why": "The exact price and moment a trade would start."},
    {"key": "invalidation", "label": "Invalidation / stop", "patterns": [r"\bstop\b", r"\binvalidat\w*", r"\bwrong\b"],
     "why": "What proves the idea wrong for this trade?"},
    {"key": "target", "label": "Target", "patterns": [r"\btarget\w*", r"\btake[\s-]*profit\b", r"\btp\b"],
     "why": "Where or when the trade is closed in profit."},
    {"key": "session", "label": "Session", "patterns": [], "why": "When during the day setups may occur. Leaving it open is also a choice."},
    {"key": "holding", "label": "Maximum holding time", "patterns": [r"\bhold(?:ing)?\b", r"\bfor (?:up to )?\d+ ?(?:minutes|hours|candles|bars)\b", r"\bby (?:the )?(?:close|end of)\b"],
     "why": "When does a trade end if neither target nor stop is hit?"},
]

_TF_PATTERNS = [
    (r"\b1[\s-]*(?:m|min|minute)s?\b|\bone[\s-]*minute\b", "1m"),
    (r"\b5[\s-]*(?:m|min|minute)s?\b|\bfive[\s-]*minute\b", "5m"),
    (r"\b15[\s-]*(?:m|min|minute)s?\b|\bfifteen[\s-]*minute\b", "15m"),
    (r"\b30[\s-]*(?:m|min|minute)s?\b|\bthirty[\s-]*minute\b", "30m"),
    (r"\b1[\s-]*(?:h|hr|hour)s?\b|\bone[\s-]*hour\b|\bhourly\b|\b60[\s-]*(?:m|min)\b", "1h"),
    (r"\b4[\s-]*(?:h|hr|hour)s?\b|\bfour[\s-]*hour\b", "4h"),
    (r"\bdaily\b|\b1[\s-]*d\b|\bdaily chart\b", "1d"),
]

_TF_ANY = "|".join(f"(?:{pattern})" for pattern, _ in _TF_PATTERNS)


def _find(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def analyze_observation(text):
    """
    Find what in a natural-language observation still needs a precise definition.
    Pure pattern matching: it never rewrites the text and never guesses a definition.
    """
    text = text or ""
    instrument = None
    for symbol in sorted(INSTRUMENTS, key=len, reverse=True):
        if re.search(rf"\b{symbol}\b", text, re.IGNORECASE):
            instrument = symbol
            break
    if not instrument and re.search(r"nasdaq|\bnas\b|\bnq\b", text, re.IGNORECASE):
        instrument = "NQ"

    mentioned = []
    for pattern, tf in _TF_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE) and tf not in mentioned:
            mentioned.append(tf)
    ordered = sorted(mentioned, key=TIMEFRAMES.index)
    primary = ordered[-1] if ordered else None
    execution = ordered[:-1] if len(ordered) > 1 else []

    terms = []
    for concept in CONCEPTS:
        # The same concept on different timeframes is a different term ("1H FVG" vs "5m FVG").
        seen = {}
        for alias in concept["aliases"]:
            for match in re.finditer(alias, text, re.IGNORECASE):
                tfs = []
                if "timeframe" in concept["params"]:
                    # Only a timeframe written directly in front counts ("5-minute or 1-minute bullish FVG").
                    before = text[max(0, match.start() - 40):match.start()]
                    lead = re.search(rf"((?:{_TF_ANY})(?:\s*(?:or|and|/|,)\s*(?:{_TF_ANY}))*)\s*(?:(?:bullish|bearish|directional|fresh|valid|new|the|a|an)\s+)*$",
                                     before, re.IGNORECASE)
                    if lead:
                        tfs = [tf for pattern, tf in _TF_PATTERNS if re.search(pattern, lead.group(1), re.IGNORECASE)]
                key = "+".join(sorted(tfs, key=TIMEFRAMES.index))
                if key not in seen:
                    seen[key] = match.group(0)
        if "" in seen and len(seen) > 1:      # a bare mention ("the FVG") of one already named with a timeframe
            seen.pop("")
        for key, phrase in seen.items():
            tfs = key.split("+") if key else []
            prefix = " / ".join(TIMEFRAME_LABELS[tf] for tf in tfs)
            terms.append({"key": f"{concept['id']}@{key}" if key else concept["id"], "phrase": phrase, "concept": concept["id"],
                          "timeframes": tfs, "kind": "concept",
                          "label": f"{prefix} {concept['label']}".strip() if prefix else concept["label"],
                          "why": "Needs an exact definition: " + (concept.get("summary") or concept["label"])})
    # A specific level ("overnight low") already implies the generic liquidity mention is about it.
    for relation in RELATIONS:
        phrase = _find(relation["patterns"], text)
        if phrase:
            terms.append({"key": relation["key"], "phrase": phrase, "concept": None, "kind": "relation",
                          "label": relation["label"], "why": relation["why"]})
    for rule in REQUIRED:
        phrase = _find(rule["patterns"], text) if rule["patterns"] else None
        if rule["key"] == "session":
            phrase = _find(CONCEPTS_BY_ID["time_window"]["aliases"], text)
        terms.append({"key": rule["key"], "phrase": phrase, "concept": "time_window" if rule["key"] == "session" else None,
                      "kind": "rule", "label": rule["label"], "why": rule["why"], "mentioned": bool(phrase)})
    # Session is listed as a rule; drop the duplicate concept entry.
    terms = [t for t in terms if not (t["kind"] == "concept" and t["key"] == "time_window")]
    return {"instrument": instrument, "timeframes": {"mentioned": ordered, "primary": primary, "execution": execution},
            "terms": terms}
