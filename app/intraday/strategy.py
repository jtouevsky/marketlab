"""
strategy.py — turn a Research Project into setups, trades and results.

1. FACTS. Each hypothesis condition is evaluated from its definition:
     event   happens at one moment (an FVG VALIDATED, a sweep confirmed, a swing confirmed)
     state   holds over an interval (an FVG ACTIVE from creation until filled/invalidated/expired)
     check   a test made at the setup moment (price above PDH, setup inside 09:30–11:00 ET)
   Every time used is a CONFIRMATION time (a candle close), never earlier.

2. SETUPS. The trigger is the last event condition (in hypothesis order). At each trigger
   time t, every other condition must be satisfied using only facts confirmed at or before t,
   with its timing rule, the hypothesis order, and (by default) the same direction.
   t is when the setup becomes ENTRY ELIGIBLE.

3. TRADES. Every entry × stop × exit combination is simulated on the execution timeframe,
   strictly AFTER t: one position at a time, costs applied, conservative when a candle
   touches both stop and target (stop first), forced exit at the holding limit and at the
   end of the trading day. Trades that touch suspect data are excluded and counted.

4. RESULTS. Sample size first; R-based metrics when a stop defines risk; gross and net.
   Nothing is optimized or ranked; every combination is reported.
"""

import copy
import hashlib
import json
import statistics
from datetime import datetime

import concept_library as C
import robust

from . import concepts as K
from . import detectors as D
from . import sessions
from .bars import seconds

INF = float("inf")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
EVENT_REQUIREMENTS = {"CREATED (event)": "created_at", "ENTERED": "entered_at", "PARTIALLY_FILLED": "partial_at", "HALF_FILLED": "half_at",
                      "VALIDATED": "validated_at"}
LEVEL_CONCEPTS = {"pdh": "pdh", "pdl": "pdl", "overnight_high": "onh", "overnight_low": "onl",
                  "previous_week_high": "pwh", "previous_week_low": "pwl"}
EVENT_CONCEPTS = {"displacement": K.displacement, "market_structure_break": K.structure, "candle": K.candle,
                  "directional_move": K.directional_move, "volume_spike": K.volume_spike,
                  "volatility_expansion": K.volatility_expansion}
VOLUME_CONCEPTS = {"volume_spike", "vwap"}
MAX_SETUPS_LISTED = 300
MAX_TRADES_LISTED = 400
MIN_RISK_TICKS = 4          # risk smaller than 1 NQ point makes R meaningless; such trades are counted, not simulated


class StrategyError(ValueError):
    pass


class Problem(str):
    """Why a condition can't be tested. kind: "data" (required data unavailable) or "concept" (no detector yet)."""

    def __new__(cls, text, kind="concept"):
        obj = str.__new__(cls, text)
        obj.kind = kind
        return obj


class UntestableError(StrategyError):
    """Raised when conditions can't be evaluated; .problems says which, and why (data vs concept)."""

    def __init__(self, message, problems):
        super().__init__(message)
        self.problems = problems


def _fmt(t):
    return sessions.to_et(t).strftime("%a %b %d %H:%M ET")


# ---------------------------------------------------------------- facts

class Facts:
    """Detector output for one dataset, computed once per definition (memoized)."""

    def __init__(self, ds, peer_loader=None):
        self.ds = ds
        self._cache = {}
        self.tick = C.INSTRUMENTS.get(ds.symbol, {}).get("tick_size", 0.25)
        self.peer_loader = peer_loader or getattr(ds, "peer_loader", None)

    def peer(self, symbol):
        """A second market's dataset at the same base resolution (for SMT). Raises when it can't be loaded."""
        def build():
            if self.peer_loader:
                return self.peer_loader(symbol)
            from .dataset import build as build_dataset
            return build_dataset(symbol, self.ds.base, self.ds.provider)
        key = ("peer", symbol)
        if key not in self._cache:
            try:
                self._cache[key] = (build(), None)
            except Exception as error:          # noqa: BLE001 - reported as a data problem
                self._cache[key] = (None, error)
        value, error = self._cache[key]
        if error:
            raise error
        return value

    def get(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    def fvgs(self, params):
        key = ("fvg", json.dumps(params, sort_keys=True))
        return self.get(key, lambda: D.detect_fvgs(self.ds.context(params["timeframe"]), params, self.tick)[0])

    def sweeps(self, params):
        key = ("sweep", json.dumps(params, sort_keys=True))
        return self.get(key, lambda: D.detect_sweeps(self.ds.series, self.ds.context(params["timeframe"]), params, self.tick))

    def swings(self, tf, left, right):
        return self.get(("swing", tf, left, right), lambda: D.swings(self.ds.context(tf), left, right))

    def levels(self, day_def="globex"):
        return self.get(("levels", day_def), lambda: D.reference_levels(self.ds.series, day_def)
                        + K.weekly_levels(self.ds.series) + K.session_range_levels(self.ds.series))

    def events(self, concept, params):
        """Events of any event-type concept (displacement, structure, candle, …) for one definition."""
        key = ("events", concept, json.dumps(params, sort_keys=True))

        def build():
            ctx = self.ds.context(params.get("timeframe") or self.ds.base)
            if concept == "displacement":
                return K.displacement(ctx, params, self.tick)
            if concept in EVENT_CONCEPTS:
                return EVENT_CONCEPTS[concept](ctx, params)
            if concept in ("equal_highs", "equal_lows"):
                return K.equal_level_events(ctx, params, self.tick, concept == "equal_highs")
            if concept == "opening_range":
                return K.opening_range_breakouts(self.ds.series, ctx, int(params.get("minutes", 15)))
            if concept == "vwap":
                return K.vwap_crosses(self.ds.series, ctx, params.get("anchor", "rth"))
            return []
        return self.get(key, build)

    def vwap(self, anchor):
        return self.get(("vwap", anchor), lambda: K.Vwap(self.ds.series, anchor))

    def opening_ranges(self, minutes):
        return self.get(("or", minutes), lambda: K.opening_ranges(self.ds.series, minutes))

    def has_volume(self):
        return self.get(("has_volume",), lambda: sum(1 for v in self.ds.series.volume if v) > 0.5 * len(self.ds.series))

    def session_extreme(self, t, high):
        """Session high/low from base candles that closed at or before t."""
        s = self.ds.series
        day = sessions.trading_day(t - 1)
        start, _ = sessions.day_bounds(day)
        ctx = self.ds.context(s.interval)
        hi_idx = ctx.last_closed(t)
        values = [(s.high[i] if high else s.low[i]) for i in range(hi_idx, -1, -1)
                  if s.ts[i] >= start and "suspect" not in s.flags[i]] if hi_idx >= 0 else []
        # walk back only within the day
        return (max(values) if high else min(values)) if values else None


def condition_items(cond, definition, facts, data_end):
    """Turn one hypothesis condition into a list of facts (event/state/check items)."""
    concept, params = definition["concept"], dict(definition["params"])
    req = cond.get("requirement")
    name = definition["name"]
    items = []
    if concept == "fvg":
        for f in facts.fvgs(params):
            base = {"direction": f["direction"], "obj": f, "objtype": "fvg"}
            end = f["end_at"] if f["end_at"] is not None else data_end
            if req in ("exists (CREATED)", "ACTIVE"):
                stop = min(end, f["full_at"] or INF) if req == "ACTIVE" else end
                items.append({**base, "kind": "state", "start": f["created_at"], "end": stop})
            elif req == "not INVALIDATED":
                items.append({**base, "kind": "state", "start": f["created_at"], "end": f["invalidated_at"] or end})
            elif req in EVENT_REQUIREMENTS and f.get(EVENT_REQUIREMENTS[req]):
                items.append({**base, "kind": "event", "time": f[EVENT_REQUIREMENTS[req]]})
        return items, None
    if concept == "liquidity_sweep":
        return [{"kind": "event", "time": x["confirmed_at"], "direction": x["direction"], "obj": x, "objtype": "sweep"}
                for x in facts.sweeps(params)], None
    if concept in ("swing_high", "swing_low"):
        kind = "swingh" if concept == "swing_high" else "swingl"
        sw = facts.swings(params.get("timeframe", "5m"), int(params.get("left_bars", 3)), int(params.get("right_bars", 3)))
        return [{"kind": "event", "time": x["confirmed_at"], "direction": None, "obj": x, "objtype": "swing"}
                for x in sw if x["kind"] == kind], None
    if concept in LEVEL_CONCEPTS:
        kind = LEVEL_CONCEPTS[concept]
        levels = [l for l in facts.levels(params.get("day", "globex")) if l["kind"] == kind]
        above = req != "price below it"

        def check(t, levels=levels, above=above):
            usable = [l for l in levels if l["available_from"] <= t < l["valid_until"]]
            if not usable:
                return None
            close = _close_at(facts.ds, t)
            if close is None:
                return None
            level = usable[-1]
            return level if (close > level["price"]) == above else None
        return [{"kind": "check", "fn": check, "direction": None, "objtype": "level"}], None
    if concept in ("session_high", "session_low"):
        high = concept == "session_high"

        def check(t, high=high):
            level = facts.session_extreme(t - facts.ds.series.step, high)
            close = _close_at(facts.ds, t)
            if level is None or close is None:
                return None
            ok = close > level if req != "price below it" else close < level
            return {"kind": "sessh" if high else "sessl", "price": level} if ok else None
        return [{"kind": "check", "fn": check, "direction": None, "objtype": "level"}], None
    if concept == "time_window":
        p = C.default_params("time_window") | params
        start, end = (p["start"], p["end"]) if p.get("session") == "custom" else (C.SESSIONS[p["session"]]["start"], C.SESSIONS[p["session"]]["end"])

        def check(t, start=start, end=end):
            return {"window": f"{start}–{end}"} if sessions.in_window(t - 1, start, end) else None
        return [{"kind": "check", "fn": check, "direction": None, "objtype": "window"}], None
    if concept in VOLUME_CONCEPTS or (concept == "displacement" and params.get("volume_mult")):
        if not facts.has_volume():
            return [], Problem(f"“{name}” needs trading volume, and this dataset has volume for less than half of its candles. "
                               "Nearest measurable: the same rule without its volume part.", "data")
    if concept == "smt_divergence":
        peer_symbol = params.get("compare_with") or "ES"
        if peer_symbol == facts.ds.symbol:
            return [], Problem(f"“{name}” compares {peer_symbol} with itself; choose another market.", "concept")
        try:
            peer = facts.peer(peer_symbol)
        except Exception as error:          # noqa: BLE001
            return [], Problem(f"“{name}” needs {peer_symbol} candles at the same times as {facts.ds.symbol}; they couldn't be loaded "
                               f"({str(error)[:120] or type(error).__name__}).", "data")
        tf = params.get("timeframe") or facts.ds.base
        try:
            a, b = facts.ds.context(tf), peer.context(tf)
        except Exception as error:          # noqa: BLE001
            return [], Problem(f"“{name}”: {peer_symbol} has no {tf} candles ({error}).", "data")
        shared = len(set(a.series.ts) & set(b.series.ts))
        if shared < 0.5 * max(1, len(a.series)):
            return [], Problem(f"“{name}”: {peer_symbol} data covers only {shared} of {len(a.series)} {facts.ds.symbol} {tf} candles.", "data")
        tick_b = C.INSTRUMENTS.get(peer_symbol, {}).get("tick_size", 0.25)
        events = facts.get(("smt", json.dumps(params, sort_keys=True)), lambda: K.smt(a, b, params, facts.tick, tick_b))
        return [{"kind": "event", "time": e["confirmed_at"], "direction": e["direction"], "obj": e, "objtype": "event"}
                for e in events], None
    if concept in EVENT_CONCEPTS or concept in ("equal_highs", "equal_lows"):
        return [{"kind": "event", "time": e["confirmed_at"], "direction": e["direction"], "obj": e, "objtype": "event"}
                for e in facts.events(concept, params)], None
    if concept == "vwap":
        if req in ("crosses above", "crosses below"):
            want = "bullish" if req == "crosses above" else "bearish"
            return [{"kind": "event", "time": e["confirmed_at"], "direction": e["direction"], "obj": e, "objtype": "event"}
                    for e in facts.events(concept, params) if e["direction"] == want], None
        vwap = facts.vwap(params.get("anchor", "rth"))
        above = req != "price below it"

        def check(t, vwap=vwap, above=above):
            value, close = vwap.at(t), _close_at(facts.ds, t)
            if value is None or close is None:
                return None
            return {"kind": "vwap", "price": value} if (close > value) == above else None
        return [{"kind": "check", "fn": check, "direction": None, "objtype": "level"}], None
    if concept == "opening_range":
        minutes = int(params.get("minutes", 15))
        if req == "breakout (close beyond)":
            return [{"kind": "event", "time": e["confirmed_at"], "direction": e["direction"], "obj": e, "objtype": "event"}
                    for e in facts.events(concept, params)], None
        ranges = facts.opening_ranges(minutes)

        def check(t, ranges=ranges, req=req):
            usable = [r for r in ranges if r["available_from"] <= t < r["valid_until"]]
            close = _close_at(facts.ds, t)
            if not usable or close is None:
                return None
            r = usable[-1]
            ok = {"price above it": close > r["high"], "price below it": close < r["low"],
                  "price inside it": r["low"] <= close <= r["high"]}.get(req, False)
            return {"kind": "or", "price": r["high"] if req == "price above it" else r["low"], "range": r} if ok else None
        return [{"kind": "check", "fn": check, "direction": None, "objtype": "level"}], None
    spec = C.CONCEPTS_BY_ID[concept]
    if concept == "unsupported":
        return [], Problem(f"“{params.get('label')}” can't be measured yet: it needs {params.get('needs') or 'a detector'}."
                           + (f" Nearest measurable version: {params['nearest']}." if params.get("nearest") else ""), "concept")
    missing = spec.get("missing", "a detector")
    nearest = spec.get("nearest")
    kind = "data" if "data source" in missing else "concept"
    return [], Problem(f"“{name}” ({spec['label']}) can't be measured: it needs {missing}."
                       + (f" Nearest measurable alternative: {nearest}." if nearest else ""), kind)


def _close_at(ds, t):
    ctx = ds.context(ds.series.interval)
    i = ctx.last_closed(t)
    return ds.series.close[i] if i >= 0 else None


# ---------------------------------------------------------------- matching

def _direction_ok(item, direction, rule):
    return rule == "ignore" or direction is None or item.get("direction") is None or item["direction"] == direction


def _pick(items, cond, t, upper, direction, rule):
    """Latest item of this condition satisfying its timing, known by t, not after `upper`."""
    timing = cond.get("timing")
    within = (cond.get("within") or 30) * 60
    day = sessions.trading_day(t - 1)
    best = None
    for it in items:
        if not _direction_ok(it, direction, rule):
            continue
        if it["kind"] == "event":
            x = it["time"]
            if x > t or x > upper:
                continue
            if timing == "at the setup candle" and x != t:
                continue
            if timing in ("any time earlier in the same session", "since 18:00 ET") and sessions.trading_day(x - 1) != day:
                continue
            if timing == "within the last N minutes" and x < t - within:
                continue
            if best is None or x > best[1]:
                best = (it, x)
        elif it["kind"] == "state":
            if it["start"] > min(t, upper):
                continue
            if timing == "within the last N minutes":
                if it["end"] <= t - within:
                    continue
            elif not (it["start"] <= t < it["end"]):
                continue
            if best is None or it["start"] > best[1]:
                best = (it, it["start"])
    return best


def _prepare(project, ds, facts):
    defs = {d["id"]: d for d in project["definitions"]}
    hyp = project["hypothesis"]
    conds = hyp["conditions"]
    if not conds:
        raise StrategyError("The hypothesis has no conditions yet.")
    rule = hyp.get("direction_rule", "same")
    data_end = ds.series.ts[-1] + ds.series.step
    per_cond, untestable = [], []
    for c in conds:
        d = defs.get(c["definition_id"])
        if not d:
            raise StrategyError(f"Condition {c.get('letter')} refers to a deleted definition.")
        tf = d["params"].get("timeframe")
        if tf and seconds(tf) < seconds(ds.base):
            items, problem = [], Problem(f"“{d['name']}” uses {tf} candles, but this dataset starts at {ds.base}.", "data")
        else:
            items, problem = condition_items(c, d, facts, data_end)
        if problem:
            untestable.append({"condition_id": c["id"], "letter": c.get("letter"), "name": d["name"], "text": str(problem),
                               "kind": getattr(problem, "kind", "concept")})
        per_cond.append(items)
    if untestable:
        raise UntestableError(" ".join(u["text"] for u in untestable)
                              + " Change or remove that condition to test the rest; nothing was dropped silently.", untestable)
    kinds = [{it["kind"] for it in items} or {"none"} for items in per_cond]
    trigger = max((i for i, k in enumerate(kinds) if k == {"event"}), default=None)
    if trigger is None:
        raise StrategyError("Add at least one event condition (for example a VALIDATED FVG, a sweep, a displacement or a structure break). "
                            "It decides the moment a setup happens.")
    ordered = hyp.get("sequence", "ordered") == "ordered"
    window = _session_window(project.get("settings"))
    return defs, conds, per_cond, kinds, trigger, rule, ordered, window


def find_setups(project, ds, facts=None):
    facts = facts or Facts(ds)
    defs, conds, per_cond, kinds, trigger, rule, ordered, window = _prepare(project, ds, facts)
    setups, outside = [], 0
    fails = [0] * len(conds)
    for trig in sorted(per_cond[trigger], key=lambda x: x["time"]):
        t = trig["time"]
        if window and not sessions.in_window(t - 1, *window):
            outside += 1
            continue
        direction = trig.get("direction") if rule == "same" else None
        matched = {trigger: (trig, t)}
        ok = True
        upper = t
        # conditions before the trigger: backwards, each no later than the next one (if ordered)
        for i in range(trigger - 1, -1, -1):
            context = conds[i].get("role") == "context"      # context only has to hold at the setup, in any order
            pick = _pick(per_cond[i], conds[i], t, upper if ordered and not context else t, direction, rule) \
                if kinds[i] != {"check"} else _check(per_cond[i], t)
            if not pick:
                ok = False
                fails[i] += 1
                break
            matched[i] = pick
            if direction is None and rule == "same":
                direction = pick[0].get("direction") or direction
            if ordered and kinds[i] != {"check"} and not context:
                upper = pick[1]
        if not ok:
            continue
        for i in range(trigger + 1, len(conds)):        # conditions listed after the trigger must hold at t
            pick = _pick(per_cond[i], conds[i], t, t, direction, rule) if kinds[i] != {"check"} else _check(per_cond[i], t)
            if not pick:
                ok = False
                fails[i] += 1
                break
            matched[i] = pick
        if not ok:
            continue
        direction = direction or next((m[0].get("direction") for m in matched.values() if m[0].get("direction")), None)
        record = _setup_record(t, direction, matched, conds, defs)
        if window:
            record["why"].append({"letter": None, "ok": True, "time": t, "time_label": _clock(t),
                                  "text": f"Inside the allowed window {window[0]}–{window[1]} ET"})
        setups.append(record)
    # funnel: how many trigger candidates survive each check, in the order they are checked
    order = list(range(trigger - 1, -1, -1)) + list(range(trigger + 1, len(conds)))
    remaining = len(per_cond[trigger])
    funnel = [{"letter": conds[trigger].get("letter"), "name": defs[conds[trigger]["definition_id"]]["name"],
               "requirement": conds[trigger].get("requirement"), "remaining": remaining, "trigger": True}]
    if window:
        remaining -= outside
        funnel.append({"letter": None, "name": f"Inside {window[0]}–{window[1]} ET", "requirement": "", "remaining": remaining})
    for i in order:
        remaining -= fails[i]
        funnel.append({"letter": conds[i].get("letter"), "name": defs[conds[i]["definition_id"]]["name"],
                       "requirement": conds[i].get("requirement"), "remaining": remaining})
    return setups, {"triggers": len(per_cond[trigger]), "outside_session": outside, "trigger_letter": conds[trigger].get("letter"),
                    "facts": facts, "funnel": funnel}


def _check(items, t):
    for it in items:
        result = it["fn"](t)
        if result:
            return ({**it, "obj": result}, t)
    return None


LEVEL_LABELS = {"pdl": "previous-day low", "pdh": "previous-day high", "onl": "overnight low", "onh": "overnight high",
                "pwl": "previous-week low", "pwh": "previous-week high", "sessl": "session low", "sessh": "session high",
                "eql": "equal lows", "eqh": "equal highs", "swingl": "swing low", "swingh": "swing high",
                "lonl": "London low", "lonh": "London high", "asial": "Asia low", "asiah": "Asia high",
                "nyaml": "NY-morning low", "nyamh": "NY-morning high"}


def _clock(t):
    return sessions.to_et(t).strftime("%H:%M")


def _why_text(d, c, item, obj):
    """One plain line saying what satisfied this condition."""
    name = d["name"]
    obj = obj or {}
    if item.get("objtype") == "fvg":
        return f"{name}: {obj['tf']} {obj['direction']} FVG {obj['bottom']:.2f}–{obj['top']:.2f} ({c.get('requirement', '').lower()})"
    if item.get("objtype") == "sweep":
        level = LEVEL_LABELS.get(obj["level_kind"], obj["level_kind"].upper())
        return f"{name}: {level} {obj['level_price']:.2f} swept at {_clock(obj['occurred_at'])}, reclaimed by {_clock(obj['confirmed_at'])}"
    if item.get("objtype") == "event":
        return f"{name}: {obj.get('detail', obj.get('concept'))}"
    if item.get("objtype") == "swing":
        return f"{name}: swing {obj['price']:.2f} confirmed"
    if item.get("objtype") == "window":
        return f"{name}: inside {obj.get('window')}"
    if item.get("objtype") == "level" and isinstance(obj, dict) and obj.get("price") is not None:
        return f"{name}: {c.get('requirement', '')} ({obj.get('kind', '').upper()} {obj['price']:.2f})"
    return f"{name}: {c.get('requirement', '')}"


def _setup_record(t, direction, matched, conds, defs):
    timeline = []
    objects = {}
    why = []
    for i, (item, when) in sorted(matched.items()):
        c = conds[i]
        d = defs[c["definition_id"]]
        obj = item.get("obj")
        label = f"{c.get('letter')}: {d['name']} — {c.get('requirement')}"
        timeline.append({"letter": c.get("letter"), "time": when, "label": label, "kind": item["kind"]})
        if item.get("objtype") == "fvg":
            objects.setdefault("fvgs", []).append(obj)
            for state, at in obj["states"]:
                if at <= t:
                    timeline.append({"letter": c.get("letter"), "time": at, "detail": True,
                                     "label": f"{obj['tf']} {obj['direction']} FVG {obj['bottom']:.2f}–{obj['top']:.2f}: {state.replace('_', ' ').lower()}"})
        elif item.get("objtype") == "sweep":
            objects.setdefault("sweeps", []).append(obj)
            timeline.append({"letter": c.get("letter"), "time": obj["level_known_at"], "detail": True,
                             "label": f"{obj['level_kind'].upper()} {obj['level_price']:.2f} known"})
            timeline.append({"letter": c.get("letter"), "time": obj["occurred_at"], "detail": True,
                             "label": f"Price trades through {obj['level_kind'].upper()} (sweep begins)"})
        elif item.get("objtype") == "event":
            objects.setdefault("events", []).append(obj)
            if obj.get("swing_known_at"):
                timeline.append({"letter": c.get("letter"), "time": obj["swing_known_at"], "detail": True,
                                 "label": f"Swing {obj['level']:.2f} confirmed (now breakable)"})
            if obj.get("occurred_at") and obj["occurred_at"] < when:
                timeline.append({"letter": c.get("letter"), "time": obj["occurred_at"], "detail": True,
                                 "label": f"{obj['tf']} {obj.get('detail', obj['concept'])} begins"})
        elif item.get("objtype") == "level" and isinstance(obj, dict):
            objects.setdefault("levels", []).append(obj)
        why.append({"letter": c.get("letter"), "ok": True, "time": when, "time_label": _clock(when),
                    "text": _why_text(d, c, item, obj)})
    timeline.append({"letter": None, "time": t, "label": "ENTRY ELIGIBLE", "kind": "eligible"})
    seen = set()
    clean = []
    for row in sorted(timeline, key=lambda r: (r["time"], r.get("detail", False))):
        key = (row["time"], row["label"])
        if key in seen:
            continue
        seen.add(key)
        clean.append({**row, "time_label": _fmt(row["time"])})
    return {"id": f"s{t}", "time": t, "time_label": _fmt(t), "direction": direction, "timeline": clean, "objects": objects,
            "day": str(sessions.trading_day(t - 1)), "why": sorted(why, key=lambda w: w["time"])}


# ---------------------------------------------------------------- trade simulation

def _pending_limit(ctx, start, level, sign, until):
    s = ctx.series
    for k in range(start, len(s)):
        if ctx.close_times[k] > until:
            return None
        if sign > 0 and s.low[k] <= level:
            return k, min(s.open[k], level)
        if sign < 0 and s.high[k] >= level:
            return k, max(s.open[k], level)
    return None


def _pending_stop(ctx, start, level, sign, until):
    s = ctx.series
    for k in range(start, len(s)):
        if ctx.close_times[k] > until:
            return None
        if sign > 0 and s.high[k] >= level:
            return k, max(s.open[k], level)
        if sign < 0 and s.low[k] <= level:
            return k, min(s.open[k], level)
    return None


TARGET_POOLS = {"pdhl": "previous day high/low", "onhl": "overnight high/low", "pwhl": "previous week high/low",
                "sessions": "London / Asia / NY-morning session highs/lows",
                "session": "session high/low so far", "swing": "same-day confirmed swing highs/lows", "eqhl": "equal highs/lows"}
DEFAULT_POOLS = ["pdhl", "onhl", "session", "swing"]


def _target_structure(kind, setup, facts, ds, entry_time, entry, sign, pools=None):
    if kind == "opposite edge of the higher-timeframe FVG":
        fvgs = setup["objects"].get("fvgs") or []
        if not fvgs:
            return None, "no FVG"
        htf = max(fvgs, key=lambda f: seconds(f["tf"]))
        edge = htf["top"] if sign > 0 else htf["bottom"]
        if (edge > entry) if sign > 0 else (edge < entry):
            return edge, None
        return None, "no level beyond entry"
    pools = set(pools or DEFAULT_POOLS) if kind == "nearest opposing liquidity" else \
        {"previous day high/low": {"pdhl"}, "session high/low": {"session"}}.get(kind, set(DEFAULT_POOLS))
    candidates = []
    want = set()
    if "pdhl" in pools:
        want.add("pdh" if sign > 0 else "pdl")
    if "onhl" in pools:
        want.add("onh" if sign > 0 else "onl")
    if "pwhl" in pools:
        want.add("pwh" if sign > 0 else "pwl")
    if "sessions" in pools:
        want |= {"lonh", "asiah", "nyamh"} if sign > 0 else {"lonl", "asial", "nyaml"}
    if want:
        candidates += [l["price"] for l in facts.levels() if l["kind"] in want and l["available_from"] <= entry_time < l["valid_until"]]
    if "session" in pools:
        level = facts.session_extreme(entry_time, sign > 0)
        if level is not None:
            candidates.append(level)
    if "eqhl" in pools:
        tf = "5m" if seconds(ds.base) <= 300 else ds.base
        day = sessions.trading_day(entry_time - 1)
        for e in facts.events("equal_highs" if sign > 0 else "equal_lows", {"timeframe": tf}):
            if e["confirmed_at"] <= entry_time and sessions.trading_day(e["confirmed_at"] - 1) == day:
                candidates.append(e["price"])
    if "swing" in pools:
        tf = "5m" if seconds(ds.base) <= 300 else ds.base
        day = sessions.trading_day(entry_time - 1)
        for sw in facts.swings(tf, 3, 3):
            if sw["kind"] == ("swingh" if sign > 0 else "swingl") and sw["confirmed_at"] <= entry_time \
                    and sessions.trading_day(sw["confirmed_at"] - 1) == day:
                candidates.append(sw["price"])
    beyond = [p for p in candidates if (p > entry if sign > 0 else p < entry)]
    if not beyond:
        return None, "no level beyond entry"
    return (min(beyond) if sign > 0 else max(beyond)), None


def _session_window(settings):
    session = (settings or {}).get("session")
    if not session:
        return None
    if session.get("session") == "custom":
        return session.get("start"), session.get("end")
    spec = C.SESSIONS.get(session.get("session"))
    return (spec["start"], spec["end"]) if spec else None


def _window_end(settings, day, t):
    """UTC end of the allowed entry window on this trading day (None without a window)."""
    w = _session_window(settings)
    if not w or not w[0] or not w[1]:
        return None
    _, end = sessions.window_bounds(day, *w)
    return end if end > t - 1 else None


def _flat_at(settings, day):
    clock = (settings or {}).get("flat_time")
    if not clock:
        return None
    return sessions.from_et(day, sessions.parse_clock(clock))


def simulate(setups, project, ds, facts, entry, stop, exit_, execution_tf):
    ctx = ds.context(execution_tf)
    s = ctx.series
    settings = project.get("settings") or {}
    contract = C.INSTRUMENTS.get(settings.get("contract") or ds.symbol) or C.INSTRUMENTS["NQ"]
    tick, pv = contract["tick_size"], contract["point_value"]
    costs = settings.get("costs") or {}
    round_trip = (2 * float(costs.get("commission_per_side", 0)) + 2 * float(costs.get("exchange_fees_per_side", 0))
                  + 2 * float(costs.get("slippage_ticks", 0)) * contract["tick_value"]
                  + float(costs.get("spread_ticks", 0)) * contract["tick_value"])
    max_hold = settings.get("max_holding_minutes")
    trades, stats = [], {"no_fill": 0, "skipped_overlap": 0, "untestable": None, "no_direction": 0,
                         "bad_stop": 0, "tiny_risk": 0, "no_target": 0, "open_at_end": 0, "ambiguous_bars": 0}
    busy_until = -1
    for setup in setups:
        t = setup["time"]
        if t < busy_until:
            stats["skipped_overlap"] += 1
            continue
        if setup["direction"] not in ("bullish", "bearish"):
            stats["no_direction"] += 1
            continue
        sign = 1 if setup["direction"] == "bullish" else -1
        fvg = (setup["objects"].get("fvgs") or [None])[-1]
        sweep = (setup["objects"].get("sweeps") or [None])[-1]
        day = setup["day"]
        _, day_end = sessions.day_bounds(datetime.fromisoformat(day).date())
        k0 = ctx.last_closed(t) + 1
        if k0 >= len(s):
            stats["open_at_end"] += 1
            continue
        wait_until = min(day_end, t + 60 * (max_hold or 120))
        cutoff = _window_end(settings, datetime.fromisoformat(day).date(), t)
        if cutoff:
            wait_until = min(wait_until, cutoff)        # no entries after the session window closes
        # ---- entry
        ek = entry["kind"]
        fill = None
        if ek == "validation_close":
            i = ctx.index_of_close(t)
            if i is None:
                stats["untestable"] = f"The execution timeframe ({execution_tf}) doesn't close at the trigger time; use a finer execution timeframe."
                break
            fill = (k0, s.close[i], t)
        elif ek in ("fvg_50", "fvg_first_touch"):
            if not fvg:
                stats["untestable"] = "FVG entries need an FVG condition in the hypothesis."
                break
            level = fvg["mid"] if ek == "fvg_50" else (fvg["top"] if sign > 0 else fvg["bottom"])
            got = _pending_limit(ctx, k0, level, sign, wait_until)
            fill = (got[0], got[1], ctx.series.ts[got[0]]) if got else None
        elif ek == "break_validation":
            i = ctx.index_of_close(t)
            bar = (fvg or {}).get("validation_bar") if fvg and fvg.get("validated_at") == t else None
            hi = bar["h"] if bar else (s.high[i] if i is not None else None)
            lo = bar["l"] if bar else (s.low[i] if i is not None else None)
            if hi is None:
                stats["untestable"] = "Break entries need the trigger candle on the execution timeframe."
                break
            level = hi + tick if sign > 0 else lo - tick
            got = _pending_stop(ctx, k0, level, sign, wait_until)
            fill = (got[0], got[1], ctx.series.ts[got[0]]) if got else None
        else:
            stats["untestable"] = "Custom entry rules can't be simulated yet; describe them with a built-in kind."
            break
        if not fill:
            stats["no_fill"] += 1
            continue
        kf, price, fill_time = fill
        # ---- stop
        sk, sp = stop["kind"], stop.get("params", {})
        stop_price, close_stop, risk = None, None, None
        if sk == "fvg_invalidation":
            if not fvg:
                stats["untestable"] = "An FVG-invalidation stop needs an FVG condition."
                break
            rule = fvg.get("invalidation_rule", "close_beyond_far")
            far = fvg["bottom"] if sign > 0 else fvg["top"]
            if rule == "trade_beyond_far":
                stop_price = far - sign * tick
            else:                       # close-based: only a candle CLOSE on the FVG's timeframe stops the trade
                level = far if rule == "close_beyond_far" else fvg["mid"]
                close_stop = {"ctx": ds.context(fvg["tf"]), "level": level}
                if (level >= price) if sign > 0 else (level <= price):
                    stats["bad_stop"] += 1
                    continue
            risk = abs(price - far)
        elif sk == "sweep_extreme":
            if not sweep:
                stats["untestable"] = "A sweep-extreme stop needs a liquidity-sweep condition."
                break
            stop_price = sweep["extreme"] - sign * tick
        elif sk in ("displacement_extreme", "structure_extreme"):
            concept = "displacement" if sk == "displacement_extreme" else "market_structure_break"
            ev = [e for e in setup["objects"].get("events", []) if e.get("concept") == concept]
            if not ev:
                stats["untestable"] = f"This stop needs a {'displacement' if concept == 'displacement' else 'market-structure'} condition in the hypothesis."
                break
            e = ev[-1]
            extreme = e.get("extreme", e.get("low") if sign > 0 else e.get("high"))
            if concept == "displacement":
                extreme = e["low"] if sign > 0 else e["high"]
            stop_price = extreme - sign * tick
        elif sk == "swing_extreme":
            tf = sp.get("timeframe") or ((project.get("timeframes") or {}).get("roles") or {}).get("setup") or execution_tf
            day_now = sessions.trading_day(fill_time - 1)
            pts = [w for w in facts.swings(tf, 3, 3) if w["kind"] == ("swingl" if sign > 0 else "swingh")
                   and w["confirmed_at"] <= fill_time and sessions.trading_day(w["confirmed_at"] - 1) == day_now
                   and ((w["price"] < price) if sign > 0 else (w["price"] > price))]
            if not pts:
                stats["bad_stop"] += 1
                continue
            stop_price = pts[-1]["price"] - sign * tick
        elif sk == "fixed_points":
            stop_price = price - sign * float(sp.get("points", 20))
        elif sk == "atr":
            a = ctx.atr(int(sp.get("atr_period", 14)))[max(0, kf - 1)]
            if a is None:
                stats["no_fill"] += 1
                continue
            stop_price = price - sign * float(sp.get("atr_mult", 1.5)) * a
        elif sk == "time":
            stop_price = None
        else:
            stats["untestable"] = f"“{stop.get('description', sk)}” stops can't be simulated yet."
            break
        if stop_price is not None:
            if (stop_price >= price) if sign > 0 else (stop_price <= price):
                stats["bad_stop"] += 1
                continue
            risk = risk if risk else abs(price - stop_price)
        if risk is not None and risk < MIN_RISK_TICKS * tick:
            stats["tiny_risk"] += 1
            continue
        stop_text = (f"{stop_price:.2f}" if stop_price is not None else
                     f"close beyond {close_stop['level']:.2f} on {fvg['tf']}" if close_stop else "time only")
        # ---- target / exits
        xk, xp = exit_["kind"], exit_.get("params", {})
        target, time_exit = None, None
        if xk == "fixed_points":
            target = price + sign * float(xp.get("points", 20))
        elif xk == "r_multiple":
            if not risk:
                stats["untestable"] = "R-multiple targets need a price stop."
                break
            target = price + sign * float(xp.get("r", 2)) * risk
        elif xk == "structure":
            target, problem = _target_structure(xp.get("target", "nearest opposing liquidity"), setup, facts, ds, fill_time, price, sign,
                                                xp.get("pools"))
            if problem == "not testable yet":
                stats["untestable"] = "“Opposite edge of the higher-timeframe FVG” targets can't be simulated yet."
                break
            if target is None:
                stats["no_target"] += 1
        elif xk == "time":
            time_exit = float(xp.get("minutes", 60)) * 60
        elif xk == "scale_out":
            if not risk:
                stats["untestable"] = "Partial exits in R need a price stop."
                break
            fraction = min(0.95, max(0.05, float(xp.get("fraction", 0.5))))
            target = price + sign * float(xp.get("r", 2)) * risk
            if xp.get("rest") == "liquidity":
                final, _ = _target_structure("nearest opposing liquidity", setup, facts, ds, fill_time, price, sign,
                                             xp.get("pools") or DEFAULT_POOLS)
                if final is None:
                    stats["no_target"] += 1
            else:
                final = price + sign * float(xp.get("rest_r", 4)) * risk
            if final is not None and ((final <= target) if sign > 0 else (final >= target)):
                final = target                    # the final level is inside the first target: everything exits there
            scale = {"fraction": fraction, "final": final}
        else:
            stats["untestable"] = "Custom exits can't be simulated yet."
            break
        if xk != "scale_out":
            scale = None
        partial = None
        if sk == "time":
            time_exit = min(time_exit or INF, float(sp.get("minutes", 60)) * 60)
        hold_limit = max_hold * 60 if max_hold else None
        flat_at = _flat_at(settings, datetime.fromisoformat(day).date())
        # ---- walk forward
        mfe = mae = 0.0
        exit_price = exit_time = reason = None
        suspect = False
        for k in range(kf, len(s)):
            ct = ctx.close_times[k]
            if s.day[k] != s.day[kf] and k > kf:
                prev = k - 1
                exit_price, exit_time, reason = s.close[prev], ctx.close_times[prev], "end of trading day"
                break
            suspect |= "suspect" in s.flags[k]
            hi, lo, op = s.high[k], s.low[k], s.open[k]
            first = k == kf and ek != "validation_close"
            fav = (hi - price) if sign > 0 else (price - lo)
            adv = (price - lo) if sign > 0 else (hi - price)
            mfe, mae = max(mfe, fav), max(mae, adv)
            hit_stop = stop_price is not None and ((lo <= stop_price) if sign > 0 else (hi >= stop_price))
            hit_target = (not first) and target is not None and ((hi >= target) if sign > 0 else (lo <= target))
            if hit_stop and hit_target:
                stats["ambiguous_bars"] += 1
            if hit_stop:
                gap = (op <= stop_price) if sign > 0 else (op >= stop_price)
                exit_price, exit_time, reason = (op if gap and not first else stop_price), ct, "stop"
                break
            if hit_target and scale and partial is None and scale["final"] != target:
                gap = (op >= target) if sign > 0 else (op <= target)
                partial = {"price": op if gap else target, "time": ct, "fraction": scale["fraction"]}
                target = scale["final"]               # the rest runs to the final target (checked from the next candle)
                continue
            if hit_target:
                gap = (op >= target) if sign > 0 else (op <= target)
                exit_price, exit_time, reason = (op if gap else target), ct, "target"
                break
            if close_stop:
                j = close_stop["ctx"].index_of_close(ct)
                if j is not None:
                    c = close_stop["ctx"].series.close[j]
                    if (c < close_stop["level"]) if sign > 0 else (c > close_stop["level"]):
                        exit_price, exit_time, reason = c, ct, "FVG invalidated (close)"
                        break
            if time_exit and ct - fill_time >= time_exit:
                exit_price, exit_time, reason = s.close[k], ct, "time exit"
                break
            if hold_limit and ct - fill_time >= hold_limit:
                exit_price, exit_time, reason = s.close[k], ct, "max holding time"
                break
            if flat_at and ct >= flat_at:
                exit_price, exit_time, reason = s.close[k], ct, "flat time"
                break
        if exit_price is None:
            stats["open_at_end"] += 1
            continue
        points = (exit_price - price) * sign
        if partial:
            f = partial["fraction"]
            points = f * (partial["price"] - price) * sign + (1 - f) * points
            reason = f"{f:.0%} at first target, rest: {reason}"
        gross = points * pv
        net = gross - round_trip
        trade = {"setup": setup["id"], "setup_time": t, "direction": setup["direction"], "entry_time": fill_time,
                 "entry_label": _fmt(fill_time), "entry": round(price, 2),
                 "stop": round(stop_price, 2) if stop_price is not None else None, "stop_text": stop_text,
                 "target": round(target, 2) if target is not None else None,
                 "exit": round(exit_price, 2), "exit_time": exit_time, "exit_label": _fmt(exit_time), "reason": reason,
                 "points": round(points, 2), "gross": round(gross, 2), "net": round(net, 2),
                 "risk_points": round(risk, 2) if risk else None,
                 "r_gross": round(points / risk, 3) if risk else None,
                 "r_net": round(net / (risk * pv), 3) if risk else None,
                 "mfe_points": round(mfe, 2), "mae_points": round(mae, 2),
                 "mfe_r": round(mfe / risk, 3) if risk else None, "mae_r": round(mae / risk, 3) if risk else None,
                 "minutes": round((exit_time - fill_time) / 60, 1), "suspect": suspect,
                 "hour": sessions.to_et(fill_time).hour, "weekday": sessions.to_et(fill_time).weekday(),
                 "day": setup["day"], "contract": s.contracts[kf] if s.contracts else None,
                 "why": setup.get("why", []), "partial": partial}
        trades.append(trade)
        busy_until = exit_time
    stats["costs_round_trip"] = round(round_trip, 2)
    stats["point_value"] = pv
    return trades, stats


# ---------------------------------------------------------------- metrics

def _streaks(values):
    best_w = best_l = cur_w = cur_l = 0
    for v in values:
        if v > 0:
            cur_w, cur_l = cur_w + 1, 0
        elif v < 0:
            cur_l, cur_w = cur_l + 1, 0
        else:
            cur_w = cur_l = 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return best_w, best_l


def _drawdown(values):
    peak = equity = dd = 0.0
    for v in values:
        equity += v
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return dd


def metrics(trades, use_r=True):
    clean = [t for t in trades if not t["suspect"]]
    unit = "R" if use_r and clean and all(t["r_net"] is not None for t in clean) else "$"
    key, gross_key = ("r_net", "r_gross") if unit == "R" else ("net", "gross")
    values = [t[key] for t in clean]
    n = len(values)
    out = {"n": n, "excluded_suspect": len(trades) - n, "unit": unit}
    if not n:
        return out
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    gross = [t[gross_key] for t in clean]
    out.update({
        "wins": len(wins), "losses": len(losses), "breakeven": n - len(wins) - len(losses),
        "win_rate": len(wins) / n, "loss_rate": len(losses) / n,
        "mean": statistics.fmean(values), "median": statistics.median(values),
        "gross_mean": statistics.fmean(gross),
        "avg_win": statistics.fmean(wins) if wins else None, "avg_loss": statistics.fmean(losses) if losses else None,
        "median_win": statistics.median(wins) if wins else None, "median_loss": statistics.median(losses) if losses else None,
        "expectancy": statistics.fmean(values),
        "expectancy_dollars": statistics.fmean(t["net"] for t in clean),
        "gross_expectancy_dollars": statistics.fmean(t["gross"] for t in clean),
        "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
        "std": statistics.stdev(values) if n > 1 else None,
        "max_drawdown": _drawdown(values),
        "holding_mean": statistics.fmean(t["minutes"] for t in clean),
        "holding_median": statistics.median(t["minutes"] for t in clean),
        "mfe_mean": statistics.fmean(t["mfe_r"] if unit == "R" else t["mfe_points"] for t in clean),
        "mae_mean": statistics.fmean(t["mae_r"] if unit == "R" else t["mae_points"] for t in clean),
        "mfe_unit": "R" if unit == "R" else "points",
    })
    out["max_consec_wins"], out["max_consec_losses"] = _streaks(values)
    if n >= 10 and out["std"]:
        out["sharpe_like"] = out["mean"] / out["std"]
        down = [min(v, 0) ** 2 for v in values]
        dd = (sum(down) / n) ** 0.5
        out["sortino_like"] = out["mean"] / dd if dd else None
    lo, hi = robust.bootstrap_ci(values, statistics.fmean)
    out["ci95"] = [lo, hi] if lo is not None else None
    out["by_hour"] = _group(clean, "hour", key)
    out["by_weekday"] = {WEEKDAYS[k]: v for k, v in _group(clean, "weekday", key).items()}
    out["by_reason"] = _group(clean, "reason", key)
    return out


def _group(trades, field, key):
    groups = {}
    for t in trades:
        groups.setdefault(t[field], []).append(t[key])
    return {k: {"n": len(v), "mean": statistics.fmean(v), "win_rate": sum(1 for x in v if x > 0) / len(v)}
            for k, v in sorted(groups.items())}


# ---------------------------------------------------------------- the whole run

def rules_hash(project):
    keep = copy.deepcopy({k: project.get(k) for k in ("definitions", "hypothesis", "entries", "exits", "stops", "settings", "timeframes")})
    for d in keep["definitions"] or []:
        d.pop("explanation", None)
    return hashlib.sha1(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:12]


_RUNS = {}


def run(project, ds, combo=None):
    key = (id(ds), ds.built_at, rules_hash(project), json.dumps(combo or {}, sort_keys=True))
    if key in _RUNS:
        return _RUNS[key]
    result = _run(project, ds, combo)
    if len(_RUNS) > 20:
        _RUNS.clear()
    _RUNS[key] = result
    return result


def _run(project, ds, combo=None):
    roles = (project.get("timeframes") or {}).get("roles") or {}
    execution = roles.get("execution") or ds.base
    if seconds(execution) < seconds(ds.base):
        raise StrategyError(f"The execution timeframe ({execution}) is finer than the data ({ds.base}).")
    if not project["entries"] or not project["stops"] or not project["exits"]:
        raise StrategyError("Add at least one entry, one stop and one exit idea to test.")
    setups, info = find_setups(project, ds)
    facts = info.pop("facts")
    matrix = []
    primary = None
    wanted = combo or {}
    for e in project["entries"]:
        for st in project["stops"]:
            for x in project["exits"]:
                trades, stats = simulate(setups, project, ds, facts, e, st, x, execution)
                m = metrics(trades)
                cell = {"entry": e["id"], "stop": st["id"], "exit": x["id"],
                        "label": f"{e['letter']}/{st['letter']}/{x['letter']}", "stats": stats,
                        "n": m.get("n", 0), "unit": m.get("unit"), "win_rate": m.get("win_rate"),
                        "expectancy": m.get("expectancy"), "median": m.get("median"), "profit_factor": m.get("profit_factor")}
                matrix.append(cell)
                is_wanted = (wanted.get("entry"), wanted.get("stop"), wanted.get("exit")) == (e["id"], st["id"], x["id"])
                if primary is None or is_wanted:
                    primary = {"cell": cell, "trades": trades, "metrics": m, "stats": stats}
    trades = primary["trades"]
    clean = [t for t in trades if not t["suspect"]]
    key = "r_net" if primary["metrics"].get("unit") == "R" else "net"
    worst = sorted(clean, key=lambda t: t[key])[:5]
    days = len(set(ds.series.day))
    n = primary["metrics"].get("n", 0)
    warnings = [f"{n} trades from {days} trading days of data. "
                + ("That is far too few to tell an edge from luck." if n < 30 else
                   "Still a short window: one market regime, a few weeks.")]
    if info["outside_session"]:
        warnings.append(f"{info['outside_session']} triggers fell outside the allowed session and were ignored.")
    if primary["stats"]["ambiguous_bars"]:
        warnings.append(f"{primary['stats']['ambiguous_bars']} candles touched both stop and target; the stop was assumed first.")
    combos = len(matrix)
    actual = project.get("actual_trade")
    luck_info = None
    if actual:
        t_actual = sessions.from_et(datetime.strptime(actual["date"], "%Y-%m-%d").date(), sessions.parse_clock(actual["time"]))
        near = [x for x in setups if abs(x["time"] - t_actual) <= 30 * 60]
        luck_info = luck(trades, actual)
        if luck_info is not None:
            luck_info["matched_setup"] = ({"time": near[0]["time"], "time_label": near[0]["time_label"]} if near else None)
            luck_info["actual"] = actual
            luck_info["in_data"] = ds.series.ts[0] <= t_actual <= ds.series.ts[-1] + ds.series.step
    return {
        "rules_hash": rules_hash(project), "ran_at": datetime.now().isoformat(timespec="seconds"),
        "execution": execution, "setups": len(setups), "triggers": info["triggers"], "trigger_letter": info["trigger_letter"],
        "funnel": info["funnel"],
        "setup_list": [{k: v for k, v in s.items() if k != "objects"} for s in setups[:MAX_SETUPS_LISTED]],
        "matrix": matrix, "combos": combos, "primary": {"cell": primary["cell"], "metrics": primary["metrics"],
                                                        "stats": primary["stats"], "trades": trades[:MAX_TRADES_LISTED]},
        "counterexamples": worst, "warnings": warnings, "luck": luck_info,
        "data": {"symbol": ds.symbol, "base": ds.base, "provider": ds.provider, "candles": len(ds.series),
                 "first": ds.series.ts[0], "last": ds.series.ts[-1], "trading_days": days},
    }


def replay(project, ds, setup_time, combo=None, before_minutes=180, after_minutes=90):
    """Everything Replay needs for one setup: candles, and WHEN each fact became known."""
    result = run(project, ds, combo)
    setup = next((s for s in result["setup_list"] if s["time"] == setup_time), None)
    if not setup:
        raise StrategyError("That setup isn't in the current results; run the test again.")
    trade = next((t for t in result["primary"]["trades"] if t["setup_time"] == setup_time), None)
    execution = result["execution"]
    ctx = ds.context(execution)
    s = ctx.series
    end = (trade["exit_time"] if trade else setup_time) + after_minutes * 60
    start = setup_time - before_minutes * 60
    first_known = min(r["time"] for r in setup["timeline"])
    start = min(start, first_known - 30 * 60)
    idx = [i for i in range(len(s)) if start <= s.ts[i] and ctx.close_times[i] <= end]
    idx = idx[-900:]
    facts = Facts(ds)
    levels = [l for l in facts.levels() if l["available_from"] <= end and l["valid_until"] >= start]
    setups, _ = find_setups(project, ds, facts)
    full = next((x for x in setups if x["time"] == setup_time), None)
    zones = []
    for f in (full or {}).get("objects", {}).get("fvgs", []):
        zones.append({"tf": f["tf"], "direction": f["direction"], "top": f["top"], "bottom": f["bottom"],
                      "created_at": f["created_at"], "end_at": f["end_at"], "states": f["states"]})
    sweeps = [{"level_kind": x["level_kind"], "level_price": x["level_price"], "known_at": x["level_known_at"],
               "occurred_at": x["occurred_at"], "confirmed_at": x["confirmed_at"], "extreme": x["extreme"]}
              for x in (full or {}).get("objects", {}).get("sweeps", [])]
    et = lambda ts: int(ts + sessions.to_et(ts).utcoffset().total_seconds())   # chart time axis in New York wall-clock
    return {"interval": execution, "timezone": "America/New_York",
            "candles": [{"t": s.ts[i], "x": et(s.ts[i]), "close_t": ctx.close_times[i], "o": s.open[i], "h": s.high[i],
                         "l": s.low[i], "c": s.close[i], "suspect": "suspect" in s.flags[i]} for i in idx],
            "setup": setup, "trade": trade, "levels": levels, "zones": zones, "sweeps": sweeps,
            "conditions": [{"letter": r["letter"], "label": r["label"], "known_at": r["time"]}
                           for r in setup["timeline"] if not r.get("detail")]}


# ---------------------------------------------------------------- explainability

def why_not(project, ds, t):
    """
    Was there a setup at (or just before) time t? For every condition: what was found, or why
    nothing qualified, using only what was known at that moment.
    """
    facts = Facts(ds)
    defs, conds, per_cond, kinds, trigger, rule, ordered, window = _prepare(project, ds, facts)
    trig_items = [it for it in per_cond[trigger] if t - 3600 <= it["time"] <= t]
    rows = []
    at = t
    direction = None
    if trig_items:
        best = trig_items[-1]
        at = best["time"]
        direction = best.get("direction") if rule == "same" else None
    out_window = window and not sessions.in_window(at - 1, *window)
    upper = at
    for i, c in enumerate(conds):
        d = defs[c["definition_id"]]
        if i == trigger:
            if trig_items:
                rows.append({"letter": c.get("letter"), "ok": True, "text": _why_text(d, c, best, best.get("obj")) + f" at {_clock(at)} (the trigger)"})
            else:
                latest = [it for it in per_cond[i] if it["time"] <= t]
                rows.append({"letter": c.get("letter"), "ok": False,
                             "text": f"{d['name']} — {c.get('requirement')}: none in the hour before {_clock(t)}"
                                     + (f" (latest: {_fmt(latest[-1]['time'])})" if latest else "")})
            continue
        if kinds[i] == {"check"}:
            pick = _check(per_cond[i], at)
        else:
            context = c.get("role") == "context"
            lim = at if (i > trigger or context or not ordered) else upper
            pick = _pick(per_cond[i], c, at, lim, direction, rule)
        if pick:
            rows.append({"letter": c.get("letter"), "ok": True, "text": _why_text(d, c, pick[0], pick[0].get("obj")) + f" (known {_clock(pick[1])})"})
            if direction is None and rule == "same":
                direction = pick[0].get("direction") or direction
        else:
            known = [it for it in per_cond[i] if it["kind"] != "check" and (it.get("time") or it.get("start") or 0) <= at]
            hint = ""
            if known:
                last = max(known, key=lambda it: it.get("time") or it.get("start"))
                when = last.get("time") or last.get("start")
                wrong_dir = direction and last.get("direction") and last["direction"] != direction
                hint = f" Latest one: {_fmt(when)}" + (f", {last['direction']} (wrong direction)" if wrong_dir else "") + \
                       (f"; it doesn't meet the timing “{c.get('timing')}”" if not wrong_dir else "") + "."
            rows.append({"letter": c.get("letter"), "ok": False,
                         "text": f"{d['name']} — {c.get('requirement')}: not satisfied when the setup would be eligible ({_clock(at)}).{hint}"})
    if window:
        rows.append({"letter": None, "ok": not out_window, "text": f"Allowed window {window[0]}–{window[1]} ET"
                     + (" — outside it" if out_window else "")})
    matched = all(r["ok"] for r in rows)
    return {"time": t, "time_label": _fmt(t), "evaluated_at": at, "evaluated_label": _fmt(at), "matched": matched, "rows": rows}


def luck(trades, actual, unit="R"):
    """Where a realized trade's R falls among historically similar setups (no judgement words beyond the data)."""
    values = sorted(x["r_net"] for x in trades if not x["suspect"] and x.get("r_net") is not None)
    if actual is None or actual.get("result_r") is None:
        return None
    r = actual["result_r"]
    n = len(values)
    if not n:
        return {"n": 0, "your_r": r, "text": "No historical matches to compare with."}
    below = sum(1 for v in values if v < r)
    equal = sum(1 for v in values if v == r)
    percentile = (below + 0.5 * equal) / n
    if n < 10:
        verdict = (f"Only {n} historical match{'es' if n != 1 else ''}: too few to say whether your outcome was unusual.")
    elif percentile >= 0.9:
        verdict = "This realized outcome was unusually strong relative to comparable historical setups."
    elif percentile <= 0.1:
        verdict = "This realized outcome was unusually weak relative to comparable historical setups."
    else:
        verdict = "This realized outcome was within the usual range of comparable historical setups."
    hist = {}
    for v in values:
        b = max(-3, min(5, round(v * 2) / 2))
        hist[b] = hist.get(b, 0) + 1
    return {"n": n, "your_r": r, "percentile": percentile, "text": verdict,
            "mean": statistics.fmean(values), "median": statistics.median(values),
            "win_rate": sum(1 for v in values if v > 0) / n, "histogram": sorted(hist.items()),
            "better_than": below}
