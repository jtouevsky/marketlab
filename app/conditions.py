"""
conditions.py — the building blocks of an experiment.

A condition answers one yes/no question for every bar of data:
"Did CAT fall at least 5% over the last 5 days?"  "Was volume above 1.5x its
20-day average?"  "Was this the first session after an earnings report?"

THE NO-LOOK-AHEAD RULE (the most important rule in MarketLab)
    compute() decides bar t using ONLY data available at the close of bar t.
    Moving averages, RSI and volume averages include bar t at most. Earnings
    reports count only once they have been announced (an after-close report
    is "known" at the NEXT session). This is TRIGGER DETECTION.
    What happens AFTER a trigger (forward returns) is OUTCOME MEASUREMENT and
    lives in experiments.py, never in here.
    Because every condition obeys this rule, the same code can later run bar
    by bar in Replay or on live data and give exactly the same answers.

A CONDITION in an experiment definition (plain JSON):
    {"id": "c1", "type": "price_move", "params": {...},
     "instrument": null,     # null = the experiment's instrument (later: "SPY", "NQ=F"...)
     "timeframe": null}      # null = the experiment's timeframe (later: "5m"...)

Each registered type provides:
    id, category, label        names used by the UI and saved experiments
    params                     adjustable parts, with kind/default/limits (drives the UI)
    sentence                   how it reads in the Lab's sentence builder
    warmup(params)             bars of history needed before it can answer
    compute(bars, params, ctx) -> [True/False/None per bar]   (None = can't tell yet)
    measure(bars, params, t, ctx) -> what the condition saw at bar t (episode inspector)
    neighbors                  which numeric parameters "Challenge → nearby parameters" varies

ctx gives access to extra data a condition may need (e.g. earnings dates),
loaded lazily so price-only experiments never touch other providers.

Combining conditions: see combine(). Today the Lab uses AND; the logic
structure already supports OR trees so a future editor doesn't need an
engine rewrite.

FUTURE CONDITIONS (architecture notes, not implemented)
    Intraday/futures conditions register here exactly like the ones below,
    with bars that carry intraday timestamps and a `session` label:
      fvg_created / fvg_filled / fvg_invalidated   3-bar gap between bar t-2 high and bar t low (or inverse);
                                                    tracked as open gaps in ctx state, all known at bar t close
      liquidity_sweep(level)       high of bar t > level and close back below it; levels = previous-day
                                   high/low, overnight high/low, equal highs (computed from bars < t)
      vwap_reclaim / vwap_loss     close crosses session VWAP (cumulative from session open to t)
      opening_range_breakout(n)    close beyond the high/low of the first n minutes of the session
      displacement(k)              bar range > k x average true range
      session(name)                bar timestamp inside "NY open", "London", ...
      economic_event(type, mins)   within N minutes after a scheduled release (calendar provider)
      news_event(category)         a structured NewsEvent of that category published at or before t
"""

import indicators
from sources import yahoo

REGISTRY = {}

CATEGORIES = [
    {"id": "PRICE", "label": "Price", "available": True},
    {"id": "VOLUME", "label": "Volume", "available": True},
    {"id": "TECHNICAL", "label": "Technical", "available": True},
    {"id": "EARNINGS", "label": "Earnings", "available": True},
    {"id": "FUNDAMENTAL", "label": "Fundamentals", "available": False},
    {"id": "MARKET", "label": "Market", "available": False},
    {"id": "EVENT", "label": "Events", "available": False},
    {"id": "SESSION", "label": "Session", "available": False},
    {"id": "NEWS", "label": "News", "available": False},
]


def register(id, category, label, params, sentence, warmup, measure=None, neighbors=()):
    """Decorator that adds a condition type to the registry."""
    def wrap(compute):
        REGISTRY[id] = {"id": id, "category": category, "label": label, "params": params,
                        "sentence": sentence, "warmup": warmup, "compute": compute,
                        "measure": measure, "neighbors": list(neighbors)}
        return compute
    return wrap


def describe_registry():
    """Everything the UI needs to build pills (no functions)."""
    return {
        "categories": CATEGORIES,
        "conditions": [{k: v for k, v in c.items() if k not in ("compute", "warmup", "measure")}
                       for c in REGISTRY.values()],
    }


def validate(condition, index=0):
    """Fill defaults, clamp to limits, reject unknown types. Returns a clean copy."""
    kind = REGISTRY.get(condition.get("type"))
    if not kind:
        raise ValueError(f"Unknown condition type: {condition.get('type')!r}")
    clean = {"id": str(condition.get("id") or f"c{index + 1}")[:20], "type": kind["id"],
             "category": kind["category"], "params": {},
             "instrument": condition.get("instrument") or None, "timeframe": condition.get("timeframe") or None}
    given = condition.get("params") or {}
    for name, spec in kind["params"].items():
        value = given.get(name, spec["default"])
        if spec["kind"] == "choice":
            if value not in spec["options"]:
                raise ValueError(f"{kind['label']}: {name} must be one of {spec['options']}")
        else:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{kind['label']}: {name} must be a number")
            if not spec["min"] <= value <= spec["max"]:
                raise ValueError(f"{kind['label']}: {name} must be between {spec['min']} and {spec['max']}")
            if spec["kind"] == "int":
                value = int(round(value))
        clean["params"][name] = value
    return clean


def warmup(condition):
    """How many bars of history this condition needs before it can answer."""
    return REGISTRY[condition["type"]]["warmup"](condition["params"])


class Context:
    """Lazily loaded extra data for conditions (one per experiment run)."""

    def __init__(self, symbol):
        self.symbol = symbol
        self._earnings = None

    def earnings(self):
        if self._earnings is None:
            self._earnings = yahoo.earnings_history(self.symbol)
        return self._earnings


def compute(condition, bars, ctx=None):
    return REGISTRY[condition["type"]]["compute"](bars, condition["params"], ctx or Context(bars.get("symbol")))


def measure(condition, bars, t, ctx=None):
    """What the condition saw at bar t: {"label", "value", "format"} (format: pct / x / num / text)."""
    fn = REGISTRY[condition["type"]]["measure"]
    if not fn:
        return None
    try:
        return fn(bars, condition["params"], t, ctx or Context(bars.get("symbol")))
    except (IndexError, ZeroDivisionError, TypeError):
        return None


def combine(logic, series_by_id, length):
    """
    Combine per-condition series with a logic tree:
        {"op": "AND", "items": ["c1", "c2", {"op": "OR", "items": ["c3", "c4"]}]}
    A bar is True only if the tree is True; None (can't tell yet) counts as not True.
    """
    def evaluate(node, t):
        if isinstance(node, str):
            return series_by_id[node][t] is True
        values = (evaluate(item, t) for item in node["items"])
        return all(values) if node["op"] == "AND" else any(values)
    return [evaluate(logic, t) for t in range(length)]


def validate_logic(logic, ids):
    """Default AND over all conditions; otherwise check the tree only uses AND/OR and known ids."""
    if not logic:
        return {"op": "AND", "items": list(ids)}

    def check(node):
        if isinstance(node, str):
            if node not in ids:
                raise ValueError(f"Logic refers to unknown condition {node!r}")
            return node
        if node.get("op") not in ("AND", "OR") or not node.get("items"):
            raise ValueError("Logic nodes must be AND/OR with at least one item")
        return {"op": node["op"], "items": [check(i) for i in node["items"]]}
    tree = check(logic)
    used = set()

    def collect(node):
        if isinstance(node, str):
            used.add(node)
        else:
            for item in node["items"]:
                collect(item)
    collect(tree)
    missing = [i for i in ids if i not in used]
    if missing:   # a condition that isn't in the logic would be silently ignored: refuse
        raise ValueError(f"Conditions {missing} are not used by the logic")
    return tree


# ---------------------------------------------------------------------------
# PRICE
# ---------------------------------------------------------------------------
def _price_move_measure(bars, p, t, ctx):
    return {"label": f"{p['window']}-day move", "value": bars["close"][t] / bars["close"][t - p["window"]] - 1, "format": "pct"}


@register(
    "price_move", "PRICE", "Price move",
    params={
        "direction": {"kind": "choice", "options": ["falls", "rises"], "default": "falls"},
        "threshold": {"kind": "number", "min": 0.5, "max": 95, "default": 5, "unit": "%",
                      "choices": [2, 3, 5, 7.5, 10, 15, 20, 30], "step": 0.5},
        "window": {"kind": "int", "min": 1, "max": 252, "default": 5, "unit": "trading days",
                   "choices": [1, 3, 5, 10, 21, 63], "step": 1},
    },
    sentence="{symbol} {direction} at least {threshold}% within {window}",
    warmup=lambda p: p["window"],
    measure=_price_move_measure,
    neighbors=["threshold", "window"],
)
def price_move(bars, p, ctx):
    closes, w, th = bars["close"], p["window"], p["threshold"] / 100
    out = [None] * len(closes)
    for t in range(w, len(closes)):
        move = closes[t] / closes[t - w] - 1
        out[t] = move <= -th if p["direction"] == "falls" else move >= th
    return out


def price_move_value(bars, p, t):
    """The actual move at bar t (shown in the event explorer)."""
    return bars["close"][t] / bars["close"][t - p["window"]] - 1


def _drawdown_measure(bars, p, t, ctx):
    lb = p["lookback"]
    peak = max(c for c in bars["close"][t - lb + 1:t + 1] if c is not None)
    return {"label": f"Below the {lb}-day high", "value": bars["close"][t] / peak - 1, "format": "pct"}


@register(
    "drawdown_from_high", "PRICE", "Drawdown from high",
    params={
        "threshold": {"kind": "number", "min": 1, "max": 95, "default": 20, "unit": "%",
                      "choices": [5, 10, 15, 20, 30, 40, 50], "step": 1},
        "lookback": {"kind": "int", "min": 20, "max": 756, "default": 252, "unit": "-day high",
                     "choices": [63, 126, 252, 504], "step": 21},
    },
    sentence="{symbol} closes at least {threshold}% below its {lookback}-day high",
    warmup=lambda p: p["lookback"],
    measure=_drawdown_measure,
    neighbors=["threshold", "lookback"],
)
def drawdown_from_high(bars, p, ctx):
    """True while the close is at least X% under the highest close of the last N sessions (today included)."""
    closes, lb, th = bars["close"], p["lookback"], p["threshold"] / 100
    out = [None] * len(closes)
    for t in range(lb - 1, len(closes)):
        window = [c for c in closes[t - lb + 1:t + 1] if c is not None]
        if window and closes[t] is not None:
            out[t] = closes[t] <= max(window) * (1 - th)
    return out


def _extreme_measure(bars, p, t, ctx):
    lb = p["lookback"]
    prior = [c for c in bars["close"][t - lb:t] if c is not None]
    ref = max(prior) if p["kind"] == "high" else min(prior)
    return {"label": f"Close vs previous {lb}-day {p['kind']}", "value": bars["close"][t] / ref - 1, "format": "pct"}


@register(
    "new_extreme", "PRICE", "New high / low",
    params={
        "kind": {"kind": "choice", "options": ["high", "low"], "default": "high"},
        "lookback": {"kind": "int", "min": 5, "max": 756, "default": 252, "unit": "-day",
                     "choices": [20, 63, 126, 252], "step": 1},
    },
    sentence="{symbol} closes at a new {lookback}-day {kind}",
    warmup=lambda p: p["lookback"] + 1,
    measure=_extreme_measure,
    neighbors=["lookback"],
)
def new_extreme(bars, p, ctx):
    """True on a close above the highest (below the lowest) close of the previous N sessions."""
    closes, lb = bars["close"], p["lookback"]
    out = [None] * len(closes)
    for t in range(lb, len(closes)):
        prior = [c for c in closes[t - lb:t] if c is not None]
        if prior and closes[t] is not None:
            out[t] = closes[t] > max(prior) if p["kind"] == "high" else closes[t] < min(prior)
    return out


def _gap_measure(bars, p, t, ctx):
    return {"label": "Opening gap", "value": bars["open"][t] / bars["close"][t - 1] - 1, "format": "pct"}


@register(
    "gap", "PRICE", "Opening gap",
    params={
        "direction": {"kind": "choice", "options": ["falls", "rises"], "default": "rises"},
        "threshold": {"kind": "number", "min": 0.5, "max": 50, "default": 3, "unit": "%",
                      "choices": [1, 2, 3, 5, 7.5, 10], "step": 0.5},
    },
    sentence="{symbol} opens with a gap that {direction} at least {threshold}% from the previous close",
    warmup=lambda p: 1,
    measure=_gap_measure,
    neighbors=["threshold"],
)
def gap_condition(bars, p, ctx):
    """Open vs the previous close (known at the open, so also at that session's close)."""
    opens, closes, th = bars.get("open") or [], bars["close"], p["threshold"] / 100
    out = [None] * len(closes)
    for t in range(1, min(len(opens), len(closes))):
        if opens[t] is None or closes[t - 1] is None:
            continue
        g = opens[t] / closes[t - 1] - 1
        out[t] = g >= th if p["direction"] == "rises" else g <= -th
    return out


# ---------------------------------------------------------------------------
# VOLUME
# ---------------------------------------------------------------------------
def _volume_measure(bars, p, t, ctx):
    volumes = bars["volume"]
    window = [v or 0 for v in volumes[t - p["lookback"]:t]]
    avg = sum(window) / len(window)
    return {"label": f"Volume vs {p['lookback']}-day average", "value": (volumes[t] or 0) / avg if avg else None, "format": "x"}


@register(
    "relative_volume", "VOLUME", "Relative volume",
    params={
        "op": {"kind": "choice", "options": ["above", "below"], "default": "above"},
        "multiple": {"kind": "number", "min": 0.1, "max": 20, "default": 1.5, "unit": "×",
                     "choices": [0.5, 1, 1.5, 2, 3, 5], "step": 0.25},
        "lookback": {"kind": "int", "min": 5, "max": 252, "default": 20, "unit": "day average",
                     "choices": [10, 20, 50, 100], "step": 5},
    },
    sentence="volume is {op} {multiple}× its {lookback}-day average",
    warmup=lambda p: p["lookback"],
    measure=_volume_measure,
    neighbors=["multiple", "lookback"],
)
def relative_volume(bars, p, ctx):
    volumes = [v or 0 for v in bars["volume"]]
    average = indicators.trailing_mean(volumes, p["lookback"])   # the days BEFORE today
    out = [None] * len(volumes)
    for t, avg in enumerate(average):
        if avg:
            ratio = volumes[t] / avg
            out[t] = ratio > p["multiple"] if p["op"] == "above" else ratio < p["multiple"]
    return out


# ---------------------------------------------------------------------------
# TECHNICAL
# ---------------------------------------------------------------------------
def _rsi_measure(bars, p, t, ctx):
    return {"label": f"{p['period']}-day RSI", "value": indicators.rsi(bars["close"][:t + 1], p["period"])[t], "format": "num"}


@register(
    "rsi", "TECHNICAL", "RSI",
    params={
        "op": {"kind": "choice", "options": ["below", "above"], "default": "below"},
        "level": {"kind": "number", "min": 1, "max": 99, "default": 30, "unit": "",
                  "choices": [20, 25, 30, 35, 65, 70, 75, 80], "step": 1},
        "period": {"kind": "int", "min": 2, "max": 50, "default": 14, "unit": "-day",
                   "choices": [7, 14, 21], "step": 1},
    },
    sentence="{period}-day RSI is {op} {level}",
    warmup=lambda p: p["period"] * 3,   # Wilder smoothing needs time to settle
    measure=_rsi_measure,
    neighbors=["level", "period"],
)
def rsi_condition(bars, p, ctx):
    values = indicators.rsi(bars["close"], p["period"])
    warm = p["period"] * 3
    return [None if (v is None or t < warm) else (v < p["level"] if p["op"] == "below" else v > p["level"])
            for t, v in enumerate(values)]


def _ma_measure(bars, p, t, ctx):
    window = bars["close"][t - p["period"] + 1:t + 1]
    avg = sum(window) / len(window)
    return {"label": f"Price vs {p['period']}-day average", "value": bars["close"][t] / avg - 1, "format": "pct"}


@register(
    "ma_position", "TECHNICAL", "Moving average",
    params={
        "op": {"kind": "choice", "options": ["above", "below"], "default": "above"},
        "period": {"kind": "int", "min": 5, "max": 300, "default": 200, "unit": "-day moving average",
                   "choices": [20, 50, 100, 200], "step": 5},
    },
    sentence="price is {op} its {period}-day moving average",
    warmup=lambda p: p["period"],
    measure=_ma_measure,
    neighbors=["period"],
)
def ma_position(bars, p, ctx):
    average = indicators.sma(bars["close"], p["period"])
    return [None if a is None else (c > a if p["op"] == "above" else c < a)
            for c, a in zip(bars["close"], average)]


@register(
    "ma_cross", "TECHNICAL", "Moving-average cross",
    params={
        "op": {"kind": "choice", "options": ["above", "below"], "default": "above"},
        "period": {"kind": "int", "min": 5, "max": 300, "default": 50, "unit": "-day moving average",
                   "choices": [20, 50, 100, 200], "step": 5},
    },
    sentence="price crosses {op} its {period}-day moving average",
    warmup=lambda p: p["period"] + 1,
    measure=_ma_measure,
    neighbors=["period"],
)
def ma_cross(bars, p, ctx):
    """True on the day the close moves from one side of the average to the other (yesterday vs today)."""
    closes = bars["close"]
    average = indicators.sma(closes, p["period"])
    out = [None] * len(closes)
    for t in range(1, len(closes)):
        if average[t] is None or average[t - 1] is None:
            continue
        if p["op"] == "above":
            out[t] = closes[t - 1] <= average[t - 1] and closes[t] > average[t]
        else:
            out[t] = closes[t - 1] >= average[t - 1] and closes[t] < average[t]
    return out


# ---------------------------------------------------------------------------
# EARNINGS
# ---------------------------------------------------------------------------
def earnings_sessions(bars, reports):
    """
    {bar index of the first session that could react to a report: report}.
    After-close (and unknown-time) reports react the NEXT session; before-open and
    intraday reports react the same session. That session is when the report is
    first known at the close, so using it is not look-ahead.
    """
    dates = bars["date"]
    import bisect
    out = {}
    for r in reports:
        if r["timing"] in ("before_open", "during_market"):
            i = bisect.bisect_left(dates, r["date"])
        else:
            i = bisect.bisect_right(dates, r["date"])
        if 0 < i < len(dates) and (i not in out):
            out[i] = r
    return out


def _earnings_measure(bars, p, t, ctx):
    sessions = earnings_sessions(bars, ctx.earnings())
    r = sessions.get(t)
    if not r:
        return None
    move = bars["close"][t] / bars["close"][t - 1] - 1
    text = f"report {r['date']}"
    if r.get("surprise_pct") is not None:
        text += f", EPS surprise {r['surprise_pct']:+.1f}%"
    return {"label": "Earnings reaction day", "value": move, "format": "pct", "detail": text}


@register(
    "earnings", "EARNINGS", "After earnings",
    params={
        "result": {"kind": "choice", "options": ["any result", "an EPS beat", "an EPS miss"], "default": "any result"},
        "reaction": {"kind": "choice", "options": ["moves any amount", "rises", "falls"], "default": "moves any amount"},
        "threshold": {"kind": "number", "min": 0, "max": 50, "default": 0, "unit": "%",
                      "choices": [0, 2, 3, 5, 7.5, 10], "step": 0.5},
    },
    sentence="{symbol} reports earnings with {result} and the stock {reaction} at least {threshold}% that session",
    warmup=lambda p: 2,
    measure=_earnings_measure,
    neighbors=["threshold"],
)
def earnings_condition(bars, p, ctx):
    """
    True on the first session that reacts to an earnings report, optionally
    filtered by EPS surprise (beat = reported above the consensus estimate) and
    by the stock's move that session (close vs previous close).
    """
    closes = bars["close"]
    out = [False] * len(closes)
    out[0] = None
    reports = ctx.earnings()
    if not reports:
        return [None] * len(closes)
    first = earnings_sessions(bars, reports[:1])
    start = min(first) if first else 0
    for i in range(start):
        out[i] = None                       # before the first known report we can't say "no report"
    th = p["threshold"] / 100
    for t, r in earnings_sessions(bars, reports).items():
        surprise = r.get("surprise_pct")
        if p["result"] == "an EPS beat" and not (surprise is not None and surprise > 0):
            continue
        if p["result"] == "an EPS miss" and not (surprise is not None and surprise < 0):
            continue
        move = closes[t] / closes[t - 1] - 1
        if p["reaction"] == "rises" and move < th:
            continue
        if p["reaction"] == "falls" and move > -th:
            continue
        if p["reaction"] == "moves any amount" and abs(move) < th:
            continue
        out[t] = True
    return out
