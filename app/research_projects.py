"""
research_projects.py — Research Projects: living notebooks for complex
(mostly intraday) trading hypotheses.

Quick Test (the bubble builder) answers "has this simple condition been
followed by X?". A Research Project holds an idea while it is turned from
trading language into rules a computer can test without hindsight:

    OBSERVE → WRITE THE IDEA → DEFINE THE TERMS → FORMALIZE → TEST → INSPECT
    → CHANGE THE RULES → TEST AGAIN → CHALLENGE → SAVE

Storage: one JSON file per project in research/ next to the app (plain text,
readable, easy to back up). Works entirely without AI.

A project:
{
  "id": "rp_20261001_ab12cd", "name": "NQ — HTF FVG continuation",
  "instrument": {"symbol": "NQ", "asset_type": "future", "universe": []},
  "timeframes": {"primary": "1h", "execution": ["5m", "1m"]},
  "observation": {
      "original": "I think when…",    THE RESEARCHER'S WORDS. Set once, never changed by the app.
      "saved_at": "...",
      "revisions": [{"id", "text", "saved_at"}]   later rewordings, added alongside (never replacing)
  },
  "terms":       [{key, phrase, concept, kind, label, why, status: open|defined|not_needed, definition_id}]
  "definitions": [{id, name, concept, params, explanation[], term_key, notes}]
  "hypothesis":  {"conditions": [{id, letter, definition_id, requirement, timing, within, notes}],
                  "sequence": "ordered"|"any", "outcome": "free text"}
  "entries" / "exits" / "stops": [{id, letter, kind, params, text}]     ideas to COMPARE, not one answer
  "settings":    {session, max_holding_minutes, contract, costs{...}}
  "notes":       [{id, text, created_at, updated_at}]
  "questions":   [{id, text, status: open|testing|answered|parked, answer}]
  "examples":    [{id, kind: successful|failed|interesting, date, time, timeframe, setup, entry, exit, result_r, notes}]
  "experiments": []                  filled once intraday data + the engine exist (Phase 2–4)
  "versions":    [{id, label, parent_id, note, snapshot, saved_at}]   the strategy version tree
  "search_log":  {"experiments": 0, "variants": 0}   for multiple-testing warnings later
  "parent_project_id": null | "rp_…"  set on forks
}
"""

import copy
import json
import secrets
import threading
from datetime import datetime

import concept_library as C
from config import BASE_DIR
from data import now_iso

RESEARCH_DIR = BASE_DIR / "research"
_lock = threading.Lock()

MAX_TEXT = 8000
MAX_ITEMS = 200
MAX_VERSIONS = 100
COLLECTIONS = ("definitions", "notes", "questions", "examples", "entries", "exits", "stops", "conditions")

ENTRY_KINDS = {
    "validation_close": "Enter at the close of the trigger candle (the last confirmation, e.g. the FVG validation)",
    "fvg_50": "Enter at a 50% retracement into the FVG (limit order)",
    "fvg_first_touch": "Enter on the first touch of the FVG (limit at its near edge)",
    "break_validation": "Enter on a break of the validation candle's high (long) / low (short)",
    "custom": "Custom rule",
}
EXIT_KINDS = {
    "fixed_points": "Fixed target in points",
    "r_multiple": "Risk/reward multiple (R)",
    "structure": "Structure target",
    "time": "Time exit",
    "scale_out": "Partial exit, then a final target",
    "custom": "Custom rule",
}
STRUCTURE_TARGETS = ["nearest opposing liquidity", "previous day high/low", "session high/low", "opposite edge of the higher-timeframe FVG"]
TARGET_POOLS = {"pdhl": "previous day high/low", "onhl": "overnight high/low", "pwhl": "previous week high/low",
                "sessions": "London / Asia / NY-morning session highs/lows",
                "session": "session high/low so far", "swing": "same-day confirmed swing highs/lows", "eqhl": "equal highs/lows"}
STOP_KINDS = {
    "fvg_invalidation": "FVG invalidates (per its definition)",
    "sweep_extreme": "Beyond the sweep's extreme",
    "fixed_points": "Fixed stop in points",
    "atr": "ATR-based stop",
    "displacement_extreme": "Beyond the displacement's extreme",
    "structure_extreme": "Beyond the structure-break candle's extreme",
    "swing_extreme": "Beyond the most recent confirmed swing (same day)",
    "time": "Time expiration",
    "custom": "Custom rule",
}
REQUIREMENTS = {
    "fvg": ["exists (CREATED)", "ACTIVE", "CREATED (event)", "ENTERED", "PARTIALLY_FILLED", "HALF_FILLED", "VALIDATED", "not INVALIDATED"],
    "liquidity_sweep": ["confirmed sweep"],
    "swing_high": ["confirmed"], "swing_low": ["confirmed"],
    "pdh": ["price above it", "price below it"], "pdl": ["price above it", "price below it"],
    "overnight_high": ["price above it", "price below it"], "overnight_low": ["price above it", "price below it"],
    "session_high": ["price above it", "price below it"], "session_low": ["price above it", "price below it"],
    "time_window": ["setup inside the window"],
    "equal_highs": ["confirmed"], "equal_lows": ["confirmed"],
    "previous_week_high": ["price above it", "price below it"], "previous_week_low": ["price above it", "price below it"],
    "vwap": ["price above it", "price below it", "crosses above", "crosses below"],
    "opening_range": ["breakout (close beyond)", "price above it", "price below it", "price inside it"],
    "displacement": ["occurs"], "market_structure_break": ["confirmed break"],
    "smt_divergence": ["confirmed divergence"], "unsupported": ["occurs"],
    "volume_spike": ["occurs"], "volatility_expansion": ["occurs"], "candle": ["occurs"], "directional_move": ["occurs"],
    "_default": ["occurs", "is true"],
}
ROLES = ("context", "setup", "confirmation", "execution")
CONDITION_ROLES = ("context", "prerequisite", "setup", "confirmation", "filter")
IMPORTANCE = ("core", "secondary")
DATA_BASES = ("auto", "1m", "5m", "15m", "1h")
DIRECTION_RULES = ("same", "ignore")
TIMINGS = ["at the setup candle", "any time earlier in the same session", "within the last N minutes", "since 18:00 ET", "before the next condition"]
QUESTION_STATUSES = ("open", "testing", "answered", "parked")
EXAMPLE_KINDS = ("successful", "failed", "interesting")
DEFAULT_COSTS = {"commission_per_side": 0.0, "exchange_fees_per_side": 0.0, "slippage_ticks": 1, "spread_ticks": 0}

TEMPLATES = {
    "nq_htf_fvg": {
        "name": "NQ — HTF FVG continuation",
        "instrument": "NQ",
        "observation": ("I think that when NQ has a directional 1-hour fair value gap, after liquidity has already been swept "
                        "in the opposite direction, validation of a 5-minute fair value gap in the direction of the 1-hour "
                        "imbalance may be associated with continuation in that direction."),
        "entries": [("validation_close", {}), ("fvg_50", {}), ("fvg_first_touch", {}), ("break_validation", {})],
        "exits": [("r_multiple", {"r": 1}), ("r_multiple", {"r": 2}), ("structure", {"target": "nearest opposing liquidity"}),
                  ("time", {"minutes": 60})],
        "stops": [("fvg_invalidation", {}), ("sweep_extreme", {})],
        "questions": ["Does this only work before 11:00 ET?", "Is 5m validation better than 1m?",
                      "Does overnight liquidity matter more than previous-day liquidity?",
                      "Does the 1H FVG need to be fresh?", "Does it behave differently on CPI / FOMC days?"],
    },
}


class ResearchError(ValueError):
    """Bad input or unknown project."""


# --- storage ---------------------------------------------------------------------
def _path(project_id):
    if not (isinstance(project_id, str) and project_id.startswith("rp_") and project_id.replace("_", "").isalnum()):
        raise ResearchError("That research project doesn't exist.")
    return RESEARCH_DIR / f"{project_id}.json"


def _load(project_id):
    path = _path(project_id)
    if not path.exists():
        raise ResearchError("That research project doesn't exist.")
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ResearchError("That project file couldn't be read; it has been left untouched.") from error
    return _migrate(project)


OLD_DATA_DEFAULT = {"provider": "yahoo", "base": "1m"}


def _migrate(project):
    """Older projects: the untouched old data default (or none) becomes the automatic free sources."""
    settings = project.setdefault("settings", {})
    if not project.get("data_choice_made") and settings.get("data") in (None, OLD_DATA_DEFAULT):
        settings["data"] = {"provider": "auto", "base": "auto"}
    settings.setdefault("flat_time", None)
    return project


def _save(project):
    RESEARCH_DIR.mkdir(exist_ok=True)
    project["updated_at"] = now_iso()
    path = _path(project["id"])
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)            # atomic: never a half-written file
    return project


def _text(value, limit=MAX_TEXT):
    return str(value or "").strip()[:limit]


def _id(prefix):
    return f"{prefix}_{secrets.token_hex(4)}"


def _letter(items):
    used = {i.get("letter") for i in items}
    for code in range(26):
        letter = chr(65 + code)
        if letter not in used:
            return letter
    return f"#{len(items) + 1}"


# --- validation helpers -------------------------------------------------------------
def _instrument(raw):
    raw = raw if isinstance(raw, dict) else {"symbol": raw}
    symbol = _text(raw.get("symbol"), 12).upper() or "NQ"
    asset_type = "future" if symbol in C.INSTRUMENTS else _text(raw.get("asset_type"), 20) or "equity"
    universe = [_text(s, 12).upper() for s in (raw.get("universe") or []) if _text(s, 12)][:50]
    return {"symbol": symbol, "asset_type": asset_type, "universe": universe}


def _timeframes(raw):
    """
    Timeframe ROLES: context (e.g. 1h), setup (5m), confirmation (5m / 1m), execution (1m).
    "primary"/"execution" are kept for older projects and derived from the roles.
    """
    raw = raw or {}
    tfs = C.TIMEFRAMES
    order = lambda xs: sorted(set(xs), key=tfs.index)
    roles_in = raw.get("roles") if isinstance(raw.get("roles"), dict) else None
    if roles_in is None:
        primary = raw.get("primary") if raw.get("primary") in tfs else None
        lower = order(tf for tf in (raw.get("execution") or []) if tf in tfs and tf != primary)
        roles_in = {"context": primary, "setup": lower[-1] if lower else None, "confirmation": lower,
                    "execution": lower[0] if lower else None}
    one = lambda v: v if v in tfs else None
    roles = {"context": one(roles_in.get("context")), "setup": one(roles_in.get("setup")),
             "confirmation": order(tf for tf in (roles_in.get("confirmation") or []) if tf in tfs)[:4],
             "execution": one(roles_in.get("execution"))}
    lower = [tf for tf in order(x for x in [roles["setup"], roles["execution"], *roles["confirmation"]] if x)
             if tf != roles["context"]]
    return {"primary": roles["context"], "execution": lower, "roles": roles}


def _params(concept_id, params):
    """Keep only known parameters with sane types; fill the rest from the library default."""
    spec = C.CONCEPTS_BY_ID[concept_id]["params"]
    out = C.default_params(concept_id)
    for name, value in (params or {}).items():
        if name not in spec:
            continue
        kind = spec[name]["kind"]
        if kind == "choice" and value in spec[name]["options"]:
            out[name] = value
        elif kind == "number":
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            lo, hi = spec[name].get("min"), spec[name].get("max")
            if (lo is None or number >= lo) and (hi is None or number <= hi):
                out[name] = int(number) if float(number).is_integer() and spec[name].get("step", 1) >= 1 else number
        elif kind == "bool":
            out[name] = bool(value)
        elif kind == "multi" and isinstance(value, list):
            out[name] = [v for v in value if v in spec[name]["options"]]
        elif kind == "time" and isinstance(value, str) and len(value) == 5 and value[2] == ":":
            out[name] = value
        elif kind == "text" and isinstance(value, str):
            out[name] = value[:400]
    return out


def _idea(kind_map, raw, items):
    kind = raw.get("kind") if raw.get("kind") in kind_map else "custom"
    params = {}
    for name in ("points", "r", "minutes", "atr_mult", "atr_period", "fraction", "rest_r"):
        if name in (raw.get("params") or {}):
            try:
                params[name] = max(0.0, float(raw["params"][name]))
            except (TypeError, ValueError):
                pass
    target = (raw.get("params") or {}).get("target")
    if target in STRUCTURE_TARGETS:
        params["target"] = target
    pools = (raw.get("params") or {}).get("pools")
    if isinstance(pools, list) and pools:
        params["pools"] = [x for x in pools if x in TARGET_POOLS]
    rest = (raw.get("params") or {}).get("rest")
    if rest in ("r", "liquidity"):
        params["rest"] = rest
    if "fraction" in params:
        params["fraction"] = min(0.95, max(0.05, params["fraction"]))
    tf = (raw.get("params") or {}).get("timeframe")
    if tf in C.TIMEFRAMES:
        params["timeframe"] = tf
    return {"id": raw.get("id") or _id("i"), "letter": raw.get("letter") or _letter(items), "kind": kind,
            "params": params, "text": _text(raw.get("text"), 600)}


def idea_text(collection, idea):
    """Plain-language version of an entry/exit/stop idea."""
    p, kind = idea.get("params", {}), idea.get("kind")
    if kind == "custom":
        return idea.get("text") or "Custom rule (describe it)"
    if collection == "exits":
        if kind == "fixed_points":
            return f"Target +{p.get('points', 20):g} points"
        if kind == "r_multiple":
            return f"Target {p.get('r', 2):g}R (R = distance from entry to stop)"
        if kind == "structure":
            pools = p.get("pools")
            extra = f" ({', '.join(TARGET_POOLS[x] for x in pools)})" if pools and p.get("target", STRUCTURE_TARGETS[0]) == STRUCTURE_TARGETS[0] else ""
            return f"Target: {p.get('target', STRUCTURE_TARGETS[0])}{extra}"
        if kind == "time":
            return f"Exit after {p.get('minutes', 60):g} minutes"
        if kind == "scale_out":
            first = f"{p.get('fraction', 0.5):.0%} at {p.get('r', 2):g}R"
            if p.get("rest") == "liquidity":
                pools = p.get("pools")
                if not pools:
                    return f"{first}; the rest at the nearest major liquidity (which levels count is still open)"
                return f"{first}; the rest at the nearest liquidity ({', '.join(TARGET_POOLS.get(x, x) for x in pools)})"
            return f"{first}; the rest at {p.get('rest_r', 4):g}R"
    if collection == "stops":
        if kind == "fixed_points":
            return f"Stop {p.get('points', 20):g} points from entry"
        if kind == "atr":
            return f"Stop {p.get('atr_mult', 1.5):g} × ATR({int(p.get('atr_period', 14))}) from entry"
        if kind == "time":
            return f"Close the trade after {p.get('minutes', 60):g} minutes if still open"
        return STOP_KINDS[kind]
    return ENTRY_KINDS.get(kind, kind)


# --- derived state -----------------------------------------------------------------
def _decorate(project):
    """Add computed fields for the UI (never stored)."""
    p = copy.deepcopy(project)
    defs = {d["id"]: d for d in p["definitions"]}
    for collection in ("entries", "exits", "stops"):
        for idea in p[collection]:
            idea["description"] = idea_text(collection, idea)
    for cond in p["hypothesis"]["conditions"]:
        d = defs.get(cond.get("definition_id"))
        cond["definition_name"] = d["name"] if d else None
    open_terms = [t for t in p["terms"] if t.get("status", "open") == "open"]
    concept_terms = [t for t in p["terms"] if t["kind"] == "concept"]
    f = p.get("formalization")
    p["mode"] = project.get("mode") or ("quick" if f else "manual")
    current_hash = _rules_hash(project)
    if f:
        f["diverged"] = current_hash != f.get("applied_rules_hash")
        f["description_changed"] = _latest_description(project) != f.get("text")
        open_q = sum(1 for a in f.get("ambiguities", []) if not a.get("answer"))
        open_u = sum(1 for u in f.get("unsupported", []) if not u.get("answer"))
        first = {"label": "Description turned into rules" + (f" — {open_q + open_u} question{'s' if open_q + open_u != 1 else ''} open" if open_q + open_u else ""),
                 "ok": not open_q and not open_u}
    else:
        first = {"label": "Every concept defined", "ok": bool(concept_terms) and all(t.get("status") != "open" for t in concept_terms)}
    checks = [
        {"label": "Idea written", "ok": bool(p["observation"]["original"] or (p.get("trade_description") or {}).get("original"))},
        first,
        {"label": "Conditions combined into a hypothesis", "ok": bool(p["hypothesis"]["conditions"])},
        {"label": "At least one entry idea", "ok": bool(p["entries"])},
        {"label": "At least one stop / invalidation idea", "ok": bool(p["stops"])},
        {"label": "At least one exit / target idea", "ok": bool(p["exits"])},
        {"label": "Intraday data available", "ok": p["instrument"]["symbol"] in ("NQ", "MNQ", "ES", "MES", "RTY", "M2K"),
         "note": "Free Yahoo Finance candles (10-minute delayed, about 30 days of 1-minute history)."},
    ]
    p["readiness"] = {"checks": checks, "open_terms": len(open_terms), "terms_total": len(p["terms"])}
    p["version_count"] = len(p["versions"])
    p["rules_hash"] = current_hash
    p["status"] = project_status(project, checks, current_hash)
    p["test_counts"] = {"tests": len(project.get("experiments") or []),
                        "rule_sets": len({x["rules_hash"] for x in project.get("experiments") or []}),
                        "variations": (project.get("search_log") or {}).get("variations", 0)}
    return p


def project_status(project, checks, current_hash):
    """IDEA → DEFINING → READY → TESTED → REFINING."""
    tested = {x["rules_hash"] for x in project.get("experiments") or []}
    has_rules = bool(project["hypothesis"]["conditions"] or project["definitions"])
    if not has_rules:
        return "IDEA"
    if not all(c["ok"] for c in checks):
        return "DEFINING" if not tested else "REFINING"
    if len(tested) > 1:
        return "REFINING"
    if current_hash in tested:
        return "TESTED"
    return "READY" if not tested else "REFINING"


def _blank(name, instrument, timeframes, observation, parent=None):
    now = now_iso()
    analysis = C.analyze_observation(observation)
    if not instrument.get("symbol") and analysis["instrument"]:
        instrument["symbol"] = analysis["instrument"]
    if not timeframes["primary"] and analysis["timeframes"]["primary"]:
        timeframes = _timeframes(analysis["timeframes"])
    return {
        "id": f"rp_{datetime.now():%Y%m%d}_{secrets.token_hex(3)}",
        "name": name, "instrument": instrument, "timeframes": timeframes,
        "observation": {"original": observation, "saved_at": now if observation else None, "revisions": []},
        "terms": [{**t, "status": "open", "definition_id": None} for t in analysis["terms"]],
        "definitions": [], "hypothesis": {"conditions": [], "sequence": "ordered", "outcome": "", "direction_rule": "same"},
        "entries": [], "exits": [], "stops": [],
        "settings": {"session": None, "max_holding_minutes": None,
                     "contract": instrument["symbol"] if instrument["symbol"] in C.INSTRUMENTS else None,
                     "costs": dict(DEFAULT_COSTS), "data": {"provider": "auto", "base": "auto"}, "flat_time": None},
        "notes": [], "questions": [], "examples": [], "experiments": [], "versions": [],
        "search_log": {"experiments": 0, "variants": 0},
        "parent_project_id": parent, "created_at": now, "updated_at": now,
    }


# --- public API ----------------------------------------------------------------------
def list_projects():
    if not RESEARCH_DIR.exists():
        return []
    out = []
    for path in RESEARCH_DIR.glob("rp_*.json"):
        try:
            p = _migrate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        out.append({"id": p["id"], "name": p["name"], "instrument": p["instrument"], "timeframes": p["timeframes"],
                    "observation": (p["observation"]["original"] or "")[:280], "updated_at": p["updated_at"],
                    "created_at": p["created_at"], "parent_project_id": p.get("parent_project_id"),
                    "counts": {k: len(p.get(k) or []) for k in ("definitions", "questions", "examples", "versions", "notes")},
                    "open_terms": sum(1 for t in p["terms"] if t.get("status", "open") == "open"),
                    "kind": p.get("kind", "research"), "mode": p.get("mode"), "actual_trade": p.get("actual_trade"),
                    "last_luck": next((x.get("luck") for x in reversed(p.get("experiments") or []) if x.get("luck")), None),
                    "description": ((p.get("trade_description") or {}).get("original") or "")[:280],
                    "status": _quick_status(p), "tests": len(p.get("experiments") or [])})
    return sorted(out, key=lambda p: p["updated_at"], reverse=True)


def _quick_status(p):
    try:
        return _decorate(p)["status"]
    except Exception:
        return None


def get(project_id):
    with _lock:
        return _decorate(_load(project_id))


def create(body):
    body = body or {}
    template = TEMPLATES.get(body.get("template"))
    observation = _text(body.get("observation") if not template else template["observation"])
    instrument = _instrument(body.get("instrument") or (template and template["instrument"]) or "NQ")
    name = _text(body.get("name"), 120) or (template and template["name"]) or "Untitled research project"
    project = _blank(name, instrument, _timeframes(body.get("timeframes")), observation)
    if body.get("kind") in ("trade_check", "idea"):
        project["kind"] = body["kind"]
    if template:
        for collection in ("entries", "exits", "stops"):
            kinds = {"entries": ENTRY_KINDS, "exits": EXIT_KINDS, "stops": STOP_KINDS}[collection]
            for kind, params in template[collection]:
                project[collection].append(_idea(kinds, {"kind": kind, "params": params}, project[collection]))
        for question in template["questions"]:
            project["questions"].append({"id": _id("q"), "text": question, "status": "open", "answer": "", "created_at": now_iso()})
    with _lock:
        _save(project)
    return _decorate(project)


def update(project_id, body):
    """Edit top-level fields. The original observation can be SET once, never changed."""
    body = body or {}
    with _lock:
        p = _load(project_id)
        if "name" in body:
            p["name"] = _text(body["name"], 120) or p["name"]
        if "instrument" in body:
            p["instrument"] = _instrument(body["instrument"])
        if "timeframes" in body:
            p["timeframes"] = _timeframes(body["timeframes"])
        if "observation" in body:
            text = _text(body["observation"])
            if p["observation"]["original"] and text != p["observation"]["original"]:
                raise ResearchError("The original observation is kept exactly as written. Add a revised wording instead.")
            if text and not p["observation"]["original"]:
                p["observation"].update(original=text, saved_at=now_iso())
                _merge_terms(p, C.analyze_observation(text)["terms"])
        if "hypothesis" in body and isinstance(body["hypothesis"], dict):
            h = body["hypothesis"]
            if h.get("sequence") in ("ordered", "any"):
                p["hypothesis"]["sequence"] = h["sequence"]
            if "outcome" in h:
                p["hypothesis"]["outcome"] = _text(h["outcome"], 1200)
            if h.get("direction_rule") in DIRECTION_RULES:
                p["hypothesis"]["direction_rule"] = h["direction_rule"]
        if "settings" in body and isinstance(body["settings"], dict):
            s = body["settings"]
            if "session" in s:
                p["settings"]["session"] = _params("time_window", s["session"]) if s["session"] else None
            if "max_holding_minutes" in s:
                try:
                    p["settings"]["max_holding_minutes"] = max(1, int(s["max_holding_minutes"])) if s["max_holding_minutes"] else None
                except (TypeError, ValueError):
                    pass
            if s.get("contract") in C.INSTRUMENTS:
                p["settings"]["contract"] = s["contract"]
            if "flat_time" in s:
                v = s["flat_time"]
                p["settings"]["flat_time"] = v if isinstance(v, str) and len(v) == 5 and v[2] == ":" else None
            if isinstance(s.get("data"), dict):
                p["data_choice_made"] = True          # an explicit choice is never migrated again
                data = p["settings"].setdefault("data", {"provider": "auto", "base": "auto"})
                if s["data"].get("provider") in ("auto", "yahoo", "firstrate", "local"):
                    data["provider"] = s["data"]["provider"]
                if s["data"].get("base") in DATA_BASES:
                    data["base"] = s["data"]["base"]
            for name, value in (s.get("costs") or {}).items():
                if name in DEFAULT_COSTS:
                    try:
                        p["settings"]["costs"][name] = max(0.0, float(value))
                    except (TypeError, ValueError):
                        pass
        if "term" in body and isinstance(body["term"], dict):
            _update_term(p, body["term"])
        _save(p)
    return _decorate(p)


def _update_term(p, change):
    """
    open / not_needed for any term; a concept term becomes "defined" only through a
    definition (new, or an existing one via definition_id); any other term becomes
    "defined" with a written rule (rule_text).
    """
    term = next((t for t in p["terms"] if t["key"] == change.get("key")), None)
    if not term:
        raise ResearchError("That term is no longer in the list.")
    status = change.get("status")
    if status in ("open", "not_needed"):
        term.update(status=status, definition_id=None)
        if status == "open":
            term.pop("rule_text", None)
    elif status == "defined" and term["kind"] == "concept":
        if change.get("definition_id") not in {d["id"] for d in p["definitions"]}:
            raise ResearchError("Pick one of your definitions for this term.")
        term.update(status="defined", definition_id=change["definition_id"])
    elif status == "defined":
        rule = _text(change.get("rule_text"), 1200)
        if not rule:
            raise ResearchError("Write the rule in a sentence first.")
        term.update(status="defined", rule_text=rule)
    else:
        raise ResearchError("Unknown term status.")


def _merge_terms(p, found):
    known = {t["key"] for t in p["terms"]}
    for term in found:
        if term["key"] not in known:
            p["terms"].append({**term, "status": "open", "definition_id": None})


def add_revision(project_id, text):
    """A reworded observation is stored beside the original, never over it."""
    text = _text(text)
    if not text:
        raise ResearchError("Write the revised wording first.")
    with _lock:
        p = _load(project_id)
        if not p["observation"]["original"]:
            p["observation"].update(original=text, saved_at=now_iso())
        else:
            p["observation"]["revisions"].append({"id": _id("r"), "text": text, "saved_at": now_iso()})
        _merge_terms(p, C.analyze_observation(text)["terms"])
        _save(p)
    return _decorate(p)


def rescan(project_id):
    with _lock:
        p = _load(project_id)
        texts = [p["observation"]["original"]] + [r["text"] for r in p["observation"]["revisions"]]
        for text in texts:
            _merge_terms(p, C.analyze_observation(text)["terms"])
        _save(p)
    return _decorate(p)


def _clean_item(collection, raw, p, existing=None):
    raw = raw or {}
    now = now_iso()
    base = dict(existing or {})
    if collection == "definitions":
        concept = raw.get("concept", base.get("concept"))
        if concept not in C.CONCEPTS_BY_ID:
            raise ResearchError("Pick a concept from the library.")
        params = _params(concept, {**base.get("params", {}), **(raw.get("params") or {})})
        base.update(concept=concept, params=params,
                    name=_text(raw.get("name", base.get("name")), 120) or C.CONCEPTS_BY_ID[concept]["label"],
                    notes=_text(raw.get("notes", base.get("notes")), 2000),
                    term_key=raw.get("term_key", base.get("term_key")),
                    explanation=C.explain(concept, params), source=base.get("source", "manual"))
    elif collection in ("entries", "exits", "stops"):
        kinds = {"entries": ENTRY_KINDS, "exits": EXIT_KINDS, "stops": STOP_KINDS}[collection]
        merged = {**base, **raw, "params": {**base.get("params", {}), **(raw.get("params") or {})}}
        base.update(_idea(kinds, merged, [i for i in p[collection] if i["id"] != base.get("id")]))
    elif collection == "conditions":
        definition_id = raw.get("definition_id", base.get("definition_id"))
        if definition_id not in {d["id"] for d in p["definitions"]}:
            raise ResearchError("Conditions are built from your definitions. Define the concept first.")
        concept = next(d["concept"] for d in p["definitions"] if d["id"] == definition_id)
        options = REQUIREMENTS.get(concept, REQUIREMENTS["_default"])
        requirement = raw.get("requirement", base.get("requirement"))
        timing = raw.get("timing", base.get("timing"))
        try:
            within = int(raw.get("within", base.get("within")) or 0)
        except (TypeError, ValueError):
            within = 0
        within = within if within > 0 else None
        role = raw.get("role", base.get("role"))
        base.update(role=role if role in CONDITION_ROLES else None)
        importance = raw.get("importance", base.get("importance"))
        base.update(importance=importance if importance in IMPORTANCE else None)
        base.update(definition_id=definition_id, requirement=requirement if requirement in options else options[0],
                    timing=timing if timing in TIMINGS else TIMINGS[0], within=within,
                    notes=_text(raw.get("notes", base.get("notes")), 600),
                    letter=base.get("letter") or _letter(p["hypothesis"]["conditions"]))
    elif collection == "notes":
        text = _text(raw.get("text", base.get("text")))
        if not text:
            raise ResearchError("Notes can't be empty.")
        base.update(text=text)
    elif collection == "questions":
        text = _text(raw.get("text", base.get("text")), 600)
        if not text:
            raise ResearchError("Write the question first.")
        status = raw.get("status", base.get("status", "open"))
        base.update(text=text, status=status if status in QUESTION_STATUSES else "open",
                    answer=_text(raw.get("answer", base.get("answer")), 2000))
    elif collection == "examples":
        kind = raw.get("kind", base.get("kind"))
        if kind not in EXAMPLE_KINDS:
            raise ResearchError("Choose successful, failed or interesting.")
        result = raw.get("result_r", base.get("result_r"))
        try:
            result = float(result) if result not in (None, "") else None
        except (TypeError, ValueError):
            result = None
        date = _text(raw.get("date", base.get("date")), 10)
        if date:
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError as error:
                raise ResearchError("Use a date like 2026-09-29.") from error
        base.update(kind=kind, date=date, time=_text(raw.get("time", base.get("time")), 5),
                    timeframe=raw.get("timeframe", base.get("timeframe")) if raw.get("timeframe", base.get("timeframe")) in C.TIMEFRAMES else None,
                    setup=_text(raw.get("setup", base.get("setup")), 1200), entry=_text(raw.get("entry", base.get("entry")), 300),
                    exit=_text(raw.get("exit", base.get("exit")), 300), result_r=result,
                    notes=_text(raw.get("notes", base.get("notes")), 2000),
                    source=base.get("source", "manual"))
    base.setdefault("id", _id(collection[:3]))
    base.setdefault("created_at", now)
    base["updated_at"] = now
    return base


def _collection(p, collection):
    if collection not in COLLECTIONS:
        raise ResearchError("Unknown section.")
    return p["hypothesis"]["conditions"] if collection == "conditions" else p[collection]


def add_item(project_id, collection, raw):
    with _lock:
        p = _load(project_id)
        items = _collection(p, collection)
        if len(items) >= MAX_ITEMS:
            raise ResearchError("That section is full.")
        item = _clean_item(collection, raw, p)
        items.append(item)
        if collection == "definitions" and item.get("term_key"):
            for term in p["terms"]:
                if term["key"] == item["term_key"]:
                    term.update(status="defined", definition_id=item["id"])
        _save(p)
    return _decorate(p)


def update_item(project_id, collection, item_id, raw):
    with _lock:
        p = _load(project_id)
        items = _collection(p, collection)
        for index, item in enumerate(items):
            if item["id"] == item_id:
                if collection == "conditions" and raw.get("move") in (-1, 1):
                    # Reorder only. Letters stay with their conditions (A is always the same condition).
                    target = index + raw["move"]
                    if 0 <= target < len(items):
                        items[index], items[target] = items[target], items[index]
                    break
                items[index] = _clean_item(collection, raw, p, existing=item)
                break
        else:
            raise ResearchError("That item no longer exists.")
        _save(p)
    return _decorate(p)


def delete_item(project_id, collection, item_id):
    with _lock:
        p = _load(project_id)
        items = _collection(p, collection)
        if collection == "definitions" and any(c.get("definition_id") == item_id for c in p["hypothesis"]["conditions"]):
            raise ResearchError("A condition in the hypothesis uses this definition. Remove the condition first.")
        remaining = [i for i in items if i["id"] != item_id]
        if len(remaining) == len(items):
            raise ResearchError("That item no longer exists.")
        items[:] = remaining
        if collection == "definitions":
            for term in p["terms"]:
                if term.get("definition_id") == item_id:
                    term.update(status="open", definition_id=None)
        _save(p)
    return _decorate(p)


# --- versions and forks -----------------------------------------------------------
SNAPSHOT_KEYS = ("timeframes", "definitions", "hypothesis", "entries", "exits", "stops", "settings")


def save_version(project_id, body):
    """Freeze the current rules as a version. Versions form a tree through parent_id."""
    body = body or {}
    with _lock:
        p = _load(project_id)
        if len(p["versions"]) >= MAX_VERSIONS:
            raise ResearchError("This project has the maximum number of versions.")
        ids = {v["id"] for v in p["versions"]}
        parent = body.get("parent_id") if body.get("parent_id") in ids else p.get("current_version")
        version = {"id": _id("v"), "label": _text(body.get("label"), 60) or f"v{len(p['versions']) + 1}",
                   "note": _text(body.get("note"), 600), "parent_id": parent, "saved_at": now_iso(),
                   "snapshot": {k: copy.deepcopy(p[k]) for k in SNAPSHOT_KEYS}}
        p["versions"].append(version)
        p["current_version"] = version["id"]
        _save(p)
    return _decorate(p)


def restore_version(project_id, version_id):
    """Load a version's rules into the working copy. Notes, questions and examples are kept."""
    with _lock:
        p = _load(project_id)
        version = next((v for v in p["versions"] if v["id"] == version_id), None)
        if not version:
            raise ResearchError("That version no longer exists.")
        for key in SNAPSHOT_KEYS:
            p[key] = copy.deepcopy(version["snapshot"][key])
        p["current_version"] = version_id
        _save(p)
    return _decorate(p)


def fork(project_id, body=None):
    """A new project that starts from this one's rules and remembers where it came from."""
    body = body or {}
    with _lock:
        source = _load(project_id)
    suffix = "(copy)" if body.get("duplicate") else "(fork)"
    new = _blank(_text(body.get("name"), 120) or f"{source['name']} {suffix}", copy.deepcopy(source["instrument"]),
                 copy.deepcopy(source["timeframes"]), source["observation"]["original"], parent=project_id)
    new["observation"] = copy.deepcopy(source["observation"])
    for key in ("terms", "definitions", "hypothesis", "entries", "exits", "stops", "settings", "questions",
                "trade_description", "formalization", "mode", "kind", "actual_trade"):
        if key in source:
            new[key] = copy.deepcopy(source[key])
    new["notes"] = [{"id": _id("not"), "text": f"{'Duplicated' if body.get('duplicate') else 'Forked'} from “{source['name']}”.",
                     "created_at": now_iso(), "updated_at": now_iso()}]
    with _lock:
        _save(new)
    return _decorate(new)


def delete(project_id):
    with _lock:
        path = _path(project_id)
        if not path.exists():
            raise ResearchError("That research project doesn't exist.")
        path.unlink()
    return {"deleted": project_id}


def meta():
    """Everything the UI needs to build pickers."""
    return {"entry_kinds": ENTRY_KINDS, "exit_kinds": EXIT_KINDS, "stop_kinds": STOP_KINDS,
            "structure_targets": STRUCTURE_TARGETS, "requirements": REQUIREMENTS, "timings": TIMINGS,
            "question_statuses": QUESTION_STATUSES, "example_kinds": EXAMPLE_KINDS, "default_costs": DEFAULT_COSTS,
            "roles": ROLES, "data_bases": DATA_BASES, "direction_rules": DIRECTION_RULES, "target_pools": TARGET_POOLS,
            "templates": {k: {"name": v["name"], "observation": v["observation"]} for k, v in TEMPLATES.items()}}


# --- data status ------------------------------------------------------------------------
DATA_STATUS = {
    "connected": True,
    "provider": "Automatic: Yahoo Finance contract + continuous series, FirstRate Data public sample (all free, no account)",
    "summary": "MarketLab builds its own NQ/MNQ research series from every free source that needs no account, login or key: "
               "Yahoo's per-contract series (e.g. NQZ26), Yahoo's continuous NQ=F and FirstRate Data's public sample. "
               "Candles keep their contract and source, rolls follow an explicit volume rule, two sources cross-check each other, "
               "and everything downloaded is kept locally so history grows over time.",
    "yahoo_findings": [
        "Tick and second-level NQ/MNQ data: no free anonymous source exists (CME real-time and tick data need a licensed feed). "
        "Seconds are shown as unavailable and are never built from minute candles.",
        "1-minute: about 29 days (Yahoo), with ~2 recent weeks also from the FirstRate sample. 5-minute: about 60 days (about 40 trading days). "
        "1-hour: about 2 years. A strategy whose finest timeframe is 5m automatically uses the deeper 5-minute history.",
        "Yahoo lists each quarterly contract separately while it trades (NQZ26.CME); expired contracts disappear, so MarketLab "
        "archives them while they are available. Older days come from the continuous series, where the contract can't be identified.",
        "Rolls: one contract per trading day, switching when the next contract trades more volume; every roll is a break nothing crosses.",
        "Where Yahoo and FirstRate both have a candle and disagree, it is flagged SOURCE DISAGREEMENT and excluded.",
        "Yahoo data is delayed about 10 minutes and is labelled DELAYED, never LIVE.",
    ],
    "verdict": "Good for developing and checking definitions on recent weeks. Not enough history to establish a durable edge; "
               "the local archive grows every time you use it.",
    "upgrade": "For deeper or second-level data later, put CSV files in data/intraday/ (Local files provider); the engine doesn't change.",
}


def record_ladder(project_id, variants):
    """The ladder counts setups for several relaxed variants: they count toward the number of things tried."""
    with _lock:
        p = _load(project_id)
        log = p.setdefault("search_log", {"experiments": 0, "variants": 0})
        log["ladder_variants"] = log.get("ladder_variants", 0) + int(variants or 0)
        _save(p)
    return log


def record_experiment(project_id, result, combo):
    """Store a compact record of a test run (rules fingerprint + headline numbers). Counts toward multiple testing."""
    m = result["primary"]["metrics"]
    entry = {"id": _id("x"), "ran_at": now_iso(), "rules_hash": result["rules_hash"], "combo": result["primary"]["cell"]["label"],
             "setups": result["setups"], "trades": m.get("n", 0), "unit": m.get("unit"), "win_rate": m.get("win_rate"),
             "expectancy": m.get("expectancy"), "data": result["data"], "version": None, "combos": result["combos"],
             "tier": (result.get("tier") or {}).get("name", "exact"),
             "luck": ({k: result["luck"].get(k) for k in ("n", "percentile", "your_r", "text")} if result.get("luck") else None),
             "relaxations": [r["original"] + " → " + r["relaxed"] for r in (result.get("tier") or {}).get("relaxations", [])]}
    with _lock:
        p = _load(project_id)
        entry["version"] = p.get("current_version")
        p.setdefault("experiments", []).append(entry)
        p["experiments"] = p["experiments"][-100:]
        log = p.setdefault("search_log", {"experiments": 0, "variants": 0})
        known = {x["rules_hash"] for x in p["experiments"][:-1]}
        if result["rules_hash"] not in known:
            log["experiments"] += 1
            log["variants"] += result["combos"]
        _save(p)
    return entry, log


def raw(project_id):
    with _lock:
        return _load(project_id)


# --- Describe My Trade (quick formalize) -------------------------------------------------
# The ORIGINAL description is stored once and never changed. Later wordings are revisions.
# Parsing produces the same structures Manual mode edits; every apply first snapshots the
# current rules as a version when they differ, so nothing is silently destroyed.
MODES = ("quick", "manual")


def _personal_path():
    return RESEARCH_DIR / "personal_definitions.json"


def personal_definitions():
    try:
        return json.loads(_personal_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_personal_definition(body):
    body = body or {}
    concept = body.get("concept")
    if concept not in C.CONCEPTS_BY_ID:
        raise ResearchError("Pick a concept from the library.")
    name = _text(body.get("name"), 60)
    if not name:
        raise ResearchError("Give the definition a name, e.g. “My Displacement”.")
    params = _params(concept, body.get("params") or {})
    with _lock:
        items = [d for d in personal_definitions() if d["name"].lower() != name.lower()]
        item = {"id": _id("pd"), "name": name, "concept": concept, "params": params, "auto": bool(body.get("auto", True)),
                "explanation": C.explain(concept, params), "saved_at": now_iso()}
        if item["auto"]:                 # only one auto-applied definition per concept
            for d in items:
                if d["concept"] == concept:
                    d["auto"] = False
        items.append(item)
        RESEARCH_DIR.mkdir(exist_ok=True)
        temp = _personal_path().with_suffix(".tmp")
        temp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(_personal_path())
    return {"personal": items}


def delete_personal_definition(pid):
    with _lock:
        items = personal_definitions()
        rest = [d for d in items if d["id"] != pid]
        if len(rest) == len(items):
            raise ResearchError("That saved definition no longer exists.")
        temp = _personal_path().with_suffix(".tmp")
        temp.write_text(json.dumps(rest, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(_personal_path())
    return {"personal": rest}


def _rules_hash(p):
    from intraday.strategy import rules_hash
    return rules_hash(p)


def _apply_draft(p, draft, text):
    """Write a parsed draft into the project's rules (the same fields Manual mode edits)."""
    current = _rules_hash(p)
    has_rules = bool(p["hypothesis"]["conditions"] or p["entries"] or p["stops"] or p["exits"])
    last = (p.get("formalization") or {}).get("applied_rules_hash")
    if has_rules and current != last and len(p["versions"]) < MAX_VERSIONS:
        p["versions"].append({"id": _id("v"), "label": f"v{len(p['versions']) + 1} (before re-parse)",
                              "note": "Saved automatically before Describe My Trade replaced the rules.",
                              "parent_id": p.get("current_version"), "saved_at": now_iso(),
                              "snapshot": {k: copy.deepcopy(p[k]) for k in SNAPSHOT_KEYS}})
        p["current_version"] = p["versions"][-1]["id"]
    now = now_iso()
    keep = [d for d in p["definitions"] if d.get("source") == "manual"]        # hand-made definitions stay
    p["definitions"] = keep + [{**{k: v for k, v in d.items() if k != "phrase"}, "explanation": C.explain(d["concept"], d["params"]),
                                "term_key": None, "notes": f"From your description: “{d.get('phrase', '')}”", "created_at": now,
                                "updated_at": now, "params": _params(d["concept"], d["params"])} for d in draft["definitions"]]
    p["hypothesis"]["conditions"] = [{**c, "notes": f"“{c.get('phrase', '')}”", "created_at": now, "updated_at": now}
                                     for c in draft["conditions"]]
    p["hypothesis"]["sequence"] = draft["sequence"]
    p["hypothesis"]["direction_rule"] = draft["direction_rule"]
    for collection in ("entries", "stops", "exits"):
        kinds = {"entries": ENTRY_KINDS, "exits": EXIT_KINDS, "stops": STOP_KINDS}[collection]
        p[collection] = [_idea(kinds, i, []) for i in draft[collection]]
    s = draft["settings"]
    p["settings"]["session"] = _params("time_window", s["session"]) if s.get("session") else None
    p["settings"]["max_holding_minutes"] = s.get("max_holding_minutes")
    p["settings"]["flat_time"] = s.get("flat_time")
    if s.get("contract"):
        p["settings"]["contract"] = s["contract"]
    p["instrument"]["symbol"] = draft["instrument"]
    p["timeframes"] = _timeframes({"roles": draft["roles"]})
    p["formalization"] = {"text": text, "text_hash": draft["text_hash"], "answers": (p.get("formalization") or {}).get("answers", {}),
                          "recipe": draft["recipe"], "timeline": draft["timeline"], "ambiguities": draft["ambiguities"],
                          "unsupported": draft["unsupported"], "missing": draft["missing"], "notes": draft["notes"],
                          "status": draft["status"], "personal_used": draft["personal_used"], "direction": draft["direction"],
                          "card": draft.get("card"),
                          "parsed_at": now_iso()}
    p["formalization"]["applied_rules_hash"] = _rules_hash(p)


def _latest_description(p):
    td = p.get("trade_description") or {}
    revs = td.get("revisions") or []
    return revs[-1]["text"] if revs else td.get("original") or ""


def describe(project_id, body):
    """
    Save the description (the first one is kept forever as the ORIGINAL; later ones are revisions)
    and parse it into the project's rules.
    """
    body = body or {}
    text = _text(body.get("text"), 12000)
    if not text:
        raise ResearchError("Describe the trade first.")
    with _lock:
        p = _load(project_id)
        td = p.setdefault("trade_description", {"original": None, "saved_at": None, "revisions": []})
        if not td.get("original"):
            td.update(original=text, saved_at=now_iso())
        elif text != _latest_description(p):
            td["revisions"].append({"id": _id("r"), "text": text, "saved_at": now_iso()})
        if not p["observation"]["original"]:
            p["observation"].update(original=text, saved_at=now_iso())
        answers = (p.get("formalization") or {}).get("answers", {}) if body.get("keep_answers", True) else {}
        import formalize
        draft = formalize.parse(text, answers, personal_definitions(), p["instrument"]["symbol"])
        p.setdefault("formalization", {})["answers"] = answers
        _apply_draft(p, draft, text)
        p["mode"] = "quick"
        _save(p)
    return _decorate(p)


def answer(project_id, body):
    """Answer ambiguity questions (or choose what to do with unsupported concepts) and re-apply the parse."""
    body = body or {}
    with _lock:
        p = _load(project_id)
        f = p.get("formalization")
        if not f:
            raise ResearchError("Describe the trade first.")
        answers = dict(f.get("answers") or {})
        for key, value in (body.get("answers") or {}).items():
            if value in (None, ""):
                answers.pop(str(key)[:80], None)
            else:
                answers[str(key)[:80]] = str(value)[:200]
        if body.get("use_suggested"):
            for a in f.get("ambiguities", []):
                if not answers.get(a["id"]) and a.get("suggested"):
                    answers[a["id"]] = a["suggested"]
        import formalize
        text = f.get("text") or _latest_description(p)
        draft = formalize.parse(text, answers, personal_definitions(), p["instrument"]["symbol"])
        f["answers"] = answers
        _apply_draft(p, draft, text)
        _save(p)
    return _decorate(p)


def reparse(project_id):
    """Parse the latest wording again (after the description was revised)."""
    with _lock:
        p = _load(project_id)
        text = _latest_description(p)
        if not text:
            raise ResearchError("There's no description to parse.")
        import formalize
        answers = (p.get("formalization") or {}).get("answers", {})
        draft = formalize.parse(text, answers, personal_definitions(), p["instrument"]["symbol"])
        _apply_draft(p, draft, text)
        _save(p)
    return _decorate(p)


def set_mode(project_id, mode):
    if mode not in MODES:
        raise ResearchError("Unknown mode.")
    with _lock:
        p = _load(project_id)
        p["mode"] = mode
        _save(p)
    return _decorate(p)


# --- Was my trade just luck? -------------------------------------------------------------
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_trade_text(text, today=None):
    """
    Pull the facts of a real trade out of a sentence like
    "I went long NQ at 10:14 on Sep 30 after the London low was swept; entry 29160, stop 28954, out at 29500 (+1.6R)".
    Only what is written is extracted; anything missing stays None for the form to ask.
    """
    import re
    from datetime import date as _date, timedelta
    t = (text or "").lower()
    today = today or datetime.now().date()
    out = {"date": None, "time": None, "direction": None, "entry": None, "stop": None, "exit": None, "result_r": None, "pnl": None}
    if re.search(r"\b(?:went|go|going|got|was|i'?m)\s+long\b|\bbought\b|\blong (?:nq|mnq|es|mes|ym|at)\b|\bbuy(?:ing)?\b(?!-?\s*side)", t):
        out["direction"] = "long"
    elif re.search(r"\b(?:went|go|going|got|was|i'?m)\s+short\b|\bsold\b|\bshort (?:nq|mnq|es|mes|ym|at)\b|\bshorted\b", t):
        out["direction"] = "short"
    m = re.search(r"\b(?:at|@|around|entered at)\s+(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        ap = (m.group(3) or "").replace(".", "")
        h = h + 12 if ap == "pm" and h < 12 else 0 if ap == "am" and h == 12 else h
        if 0 <= h < 24:
            out["time"] = f"{h:02d}:{mi:02d}"
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t)
    if m:
        out["date"] = m.group(0)
    else:
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d\d))?", t)
        if m:
            year = int(m.group(3) or today.year)
            try:
                d = _date(year, MONTHS[m.group(1)], int(m.group(2)))
                if d > today and not m.group(3):
                    d = d.replace(year=year - 1)
                out["date"] = d.isoformat()
            except ValueError:
                pass
        elif re.search(r"\byesterday\b", t):
            d = today - timedelta(days=1)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            out["date"] = d.isoformat()
        elif re.search(r"\btoday\b|\bthis morning\b", t):
            out["date"] = today.isoformat()
    num = r"(\d{2,6}(?:\.\d{1,2})?)"
    for key, pattern in (("entry", rf"\b(?:entry|entered|in|filled)\s*(?:at|@|was|:)?\s*{num}"),
                         ("stop", rf"\b(?:stop(?:\s*loss)?|sl)\s*(?:at|@|was|:)?\s*{num}"),
                         ("exit", rf"\b(?:exit(?:ed)?|out|closed|took profit|tp|sold|covered)\s*(?:at|@|was|:)?\s*{num}")):
        m = re.search(pattern, t)
        if m:
            value = float(m.group(1))
            if value > 100:                 # an index price, not a time or a count
                out[key] = value
    # the realised R: a signed number, or one after made/got/took/lost/result…; never a planned target ("target 2R", "50% at 2R")
    best = None
    for m in re.finditer(r"([+\-−]?)\s*(\d+(?:\.\d+)?)\s*r\b", t):
        before = t[max(0, m.start() - 28):m.start()]
        if re.search(r"(?:target(?:ing)?|tp|aim(?:ing)?(?: for)?|at|for a|planned|rr|r:r|risk[\s-]*reward)\s*$", before) and not m.group(1):
            continue
        sign = -1 if m.group(1) in ("-", "−") or re.search(r"\b(?:lost|loss|down|minus)\b[^.]*$", before) else 1
        score = 2 if m.group(1) or re.search(r"\b(?:made|got|took|won|lost|result|closed|netted|banked|for|up|down)\b[^.]*$", before) else 1
        if not best or score > best[0]:
            best = (score, sign * float(m.group(2)))
    if best:
        out["result_r"] = best[1]
    m = re.search(r"([+\-−])\s*\$\s*(\d[\d,]*(?:\.\d+)?)", t)
    if m:
        out["pnl"] = float(m.group(2).replace(",", "")) * (-1 if m.group(1) in "-−" else 1)
    return out


def set_actual_trade(project_id, body):
    body = body or {}
    trade = {}
    try:
        trade["date"] = datetime.strptime(_text(body.get("date"), 10), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as error:
        raise ResearchError("Use a date like 2026-09-29.") from error
    clock = _text(body.get("time"), 5)
    if not (len(clock) == 5 and clock[2] == ":"):
        raise ResearchError("Use a 24-hour entry time like 09:47 (New York time).")
    trade["time"] = clock
    direction = body.get("direction")
    if direction not in ("long", "short"):
        raise ResearchError("Choose long or short.")
    trade["direction"] = direction
    number = lambda key: float(body[key]) if body.get(key) not in (None, "") else None
    try:
        entry, stop, exit_, result_r, pnl = (number(k) for k in ("entry", "stop", "exit", "result_r", "pnl"))
    except (TypeError, ValueError) as error:
        raise ResearchError("Prices and results must be numbers.") from error
    trade.update(entry=entry, stop=stop, exit=exit_, pnl=pnl)
    sign = 1 if direction == "long" else -1
    if entry is not None and stop is not None and exit_ is not None:
        risk = (entry - stop) * sign
        if risk <= 0:
            raise ResearchError("The stop must be below the entry for a long (above it for a short).")
        trade["risk_points"] = round(risk, 2)
        trade["result_r"] = round((exit_ - entry) * sign / risk, 3)
    elif result_r is not None:
        trade["result_r"] = round(result_r, 3)
    else:
        raise ResearchError("Give the result in R (e.g. +2.5), or the entry, stop and exit prices.")
    trade["notes"] = _text(body.get("notes"), 1200)
    with _lock:
        p = _load(project_id)
        p["actual_trade"] = trade
        p["kind"] = "trade_check"
        _save(p)
    return _decorate(p)


def log_trade(body):
    """'Log a trade': one sentence (plus optional numbers) → a project that tests the trade's thesis and places the result."""
    body = body or {}
    text = _text(body.get("text"), 12000)
    if not text:
        raise ResearchError("Describe the trade you took.")
    facts = parse_trade_text(text)
    for key in ("date", "time", "direction", "entry", "stop", "exit", "result_r", "pnl"):
        if body.get(key) not in (None, ""):
            facts[key] = body[key]
    missing = [k for k in ("date", "time", "direction") if not facts.get(k)]
    if facts.get("result_r") is None and not all(facts.get(k) is not None for k in ("entry", "stop", "exit")):
        missing.append("result")
    if missing:
        return {"needs": missing, "extracted": facts}
    instrument = body.get("instrument") or "NQ"
    name = f"{instrument} {facts['direction']} · {facts['date']} {facts['time']}"
    p = create({"name": name, "instrument": instrument, "kind": "trade_check"})
    describe(p["id"], {"text": text})
    return set_actual_trade(p["id"], facts)


# --- variations ------------------------------------------------------------------------------
VARIATION_KINDS = ("timeframe", "param", "stop", "exit", "cutoff", "flat_time", "execution", "requirement")


def make_variation(project_id, body):
    """
    Test a variation without losing the original: the current rules are saved as a version (if
    unsaved), the change is applied, and the result is saved as a NEW version branching from it.
    """
    body = body or {}
    kind = body.get("kind")
    if kind not in VARIATION_KINDS:
        raise ResearchError("Unknown variation.")
    with _lock:
        p = _load(project_id)
        if len(p["versions"]) + 2 > MAX_VERSIONS:
            raise ResearchError("This project has the maximum number of versions.")
        current = _rules_hash(p)
        base_version = next((v for v in p["versions"] if v["id"] == p.get("current_version")), None)
        if not base_version or _rules_hash({**p, **base_version["snapshot"]}) != current:
            base_version = {"id": _id("v"), "label": f"v{len(p['versions']) + 1}", "note": "Saved before testing a variation.",
                            "parent_id": p.get("current_version"), "saved_at": now_iso(),
                            "snapshot": {k: copy.deepcopy(p[k]) for k in SNAPSHOT_KEYS}}
            p["versions"].append(base_version)
        label = _apply_variation(p, kind, body)
        version = {"id": _id("v"), "label": f"v{len(p['versions']) + 1}: {label}"[:60], "note": f"Variation of {base_version['label']}: {label}",
                   "parent_id": base_version["id"], "saved_at": now_iso(), "variation": label,
                   "snapshot": {k: copy.deepcopy(p[k]) for k in SNAPSHOT_KEYS}}
        p["versions"].append(version)
        p["current_version"] = version["id"]
        log = p.setdefault("search_log", {"experiments": 0, "variants": 0})
        log["variations"] = log.get("variations", 0) + 1
        _save(p)
    return _decorate(p)


def _apply_variation(p, kind, body):
    defs = {d["id"]: d for d in p["definitions"]}
    if kind in ("timeframe", "param"):
        d = defs.get(body.get("definition_id"))
        if not d:
            raise ResearchError("Pick one of the strategy's definitions.")
        name, value = ("timeframe", body.get("value")) if kind == "timeframe" else (body.get("param"), body.get("value"))
        if name not in C.CONCEPTS_BY_ID[d["concept"]]["params"]:
            raise ResearchError("That setting doesn't exist for this concept.")
        before = d["params"].get(name)
        d["params"] = _params(d["concept"], {**d["params"], name: value})
        if d["params"].get(name) == before:
            raise ResearchError("That value is the same as now (or outside the allowed range).")
        d["explanation"] = C.explain(d["concept"], d["params"])
        if kind == "timeframe" and d.get("name"):
            d["name"] = d["name"].replace(str(before), str(d["params"]["timeframe"]), 1)
            roles = p["timeframes"].get("roles") or {}
            if before in (roles.get("confirmation") or []):
                roles["confirmation"] = [d["params"]["timeframe"] if tf == before else tf for tf in roles["confirmation"]]
            p["timeframes"] = _timeframes({"roles": roles})
        spec = C.CONCEPTS_BY_ID[d["concept"]]["params"][name]
        return f"{d['name']}: {spec.get('label', name)} {before} → {d['params'][name]}"
    if kind in ("stop", "exit"):
        collection = "stops" if kind == "stop" else "exits"
        kinds = STOP_KINDS if kind == "stop" else EXIT_KINDS
        idea = _idea(kinds, body.get("idea") or {}, [])
        if idea["kind"] == "custom":
            raise ResearchError("Pick a built-in kind for the variation.")
        idea["letter"] = "A"
        p[collection] = [idea]
        return f"{'stop' if kind == 'stop' else 'exit'}: {idea_text(collection, idea)}"
    if kind == "execution":
        value = body.get("value")
        if value not in C.TIMEFRAMES:
            raise ResearchError("Pick a timeframe.")
        roles = dict(p["timeframes"].get("roles") or {})
        before = roles.get("execution")
        roles["execution"] = value
        p["timeframes"] = _timeframes({"roles": roles})
        return f"execution {before} → {value}"
    if kind == "requirement":
        cond = next((c for c in p["hypothesis"]["conditions"] if c["id"] == body.get("condition_id")), None)
        if not cond:
            raise ResearchError("Pick one of the conditions.")
        d = defs.get(cond["definition_id"])
        options = REQUIREMENTS.get(d["concept"], REQUIREMENTS["_default"]) if d else []
        value = body.get("value")
        if value in options:
            before = cond["requirement"]
            cond["requirement"] = value
            return f"{cond['letter']}: {before} → {value}"
        if value in TIMINGS or str(value).isdigit():
            before = cond["timing"]
            if str(value).isdigit():
                cond["timing"], cond["within"] = "within the last N minutes", int(value)
            else:
                cond["timing"], cond["within"] = value, None
            return f"{cond['letter']} timing → " + (f"last {cond['within']} min" if cond["within"] else cond["timing"].replace("any time earlier in the same session", "same session"))
        raise ResearchError("That isn't an option for this condition.")
    if kind == "cutoff":
        value = body.get("value")
        if not (isinstance(value, str) and len(value) == 5 and value[2] == ":"):
            raise ResearchError("Use a time like 11:00.")
        session = p["settings"].get("session") or {"session": "custom", "start": "18:00", "end": "17:00"}
        start = session.get("start") or C.SESSIONS.get(session.get("session"), {}).get("start") or "18:00"
        p["settings"]["session"] = _params("time_window", {"session": "custom", "start": start, "end": value})
        return f"no entries after {value}"
    value = body.get("value")
    p["settings"]["flat_time"] = value if isinstance(value, str) and len(value) == 5 else None
    return f"flat by {value}" if p["settings"]["flat_time"] else "no flat time"
