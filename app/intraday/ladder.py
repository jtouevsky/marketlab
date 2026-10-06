"""
ladder.py — the MATCH LADDER: what to test when the exact strategy is rare or untestable.

    EXACT          every rule as described
    VERY SIMILAR   the core thesis kept; EXECUTION details relaxed first (entry order type, entry
                   time window), then the specific liquidity level, then secondary conditions one
                   at a time until there's a usable sample (plus any condition that can't be
                   measured at all, which is always named)
    BROADER        the core thesis kept; every execution and secondary constraint relaxed

Rules
    * Components are CORE (the thesis: context, the first liquidity sweep, the final trigger),
      SECONDARY (confirmations and filters: SMT, displacement, structure breaks, specific levels) or
      EXECUTION (how the trade is entered and when). Execution is relaxed first, then secondary.
      CORE conditions are never relaxed, and stops / targets are never changed.
    * Every relaxation is listed as  original → relaxed interpretation  with the reason.
    * Tiers are never mixed: a run is labelled EXACT, SIMILAR or BROADER.
    * Nothing is optimized for profit. "Very similar" adds relaxations in a fixed order and, among
      secondary conditions, the one that keeps the most SETUPS (sample size), never the best
      returns; every alternative is shown with its count.
    * Zero exact matches are diagnosed: required data unavailable / concept not detectable yet /
      the pattern simply didn't occur (with the most restrictive rule and the data window).
"""

import copy

from . import strategy as S

LIMIT_ENTRIES = ("fvg_50", "fvg_first_touch")
SIMILAR_TARGET = 15          # "very similar" stops adding relaxations once this many setups exist
MIN_TRADES = 5               # the automatic test shows the most specific tier with at least this many trades

BROAD_LEVELS = {
    "sell-side": ["pdl", "onl", "pwl", "lonl", "asial", "nyaml", "swingl"],
    "buy-side": ["pdh", "onh", "pwh", "lonh", "asiah", "nyamh", "swingh"],
}
BROAD_LEVELS["both"] = BROAD_LEVELS["sell-side"] + BROAD_LEVELS["buy-side"]
LEVEL_NAMES = {"pdl": "previous-day low", "pdh": "previous-day high", "onl": "overnight low", "onh": "overnight high",
               "pwl": "previous-week low", "pwh": "previous-week high", "sessl": "session low", "sessh": "session high",
               "eql": "equal lows", "eqh": "equal highs", "swingl": "swing low", "swingh": "swing high",
               "lonl": "London low", "lonh": "London high", "asial": "Asia low", "asiah": "Asia high",
               "nyaml": "NY-morning low", "nyamh": "NY-morning high"}


def importance(project):
    """{condition id: "core" | "secondary"} (stored by Describe My Trade; inferred for hand-made projects)."""
    conds = project["hypothesis"]["conditions"]
    defs = {d["id"]: d for d in project["definitions"]}
    first_sweep = next((c["id"] for c in conds if defs.get(c["definition_id"], {}).get("concept") == "liquidity_sweep"), None)
    out = {}
    for i, c in enumerate(conds):
        if c.get("importance") in ("core", "secondary"):
            out[c["id"]] = c["importance"]
            continue
        concept = defs.get(c["definition_id"], {}).get("concept")
        core = c.get("role") == "context" or c["id"] == first_sweep or (i == len(conds) - 1 and concept != "unsupported")
        out[c["id"]] = "core" if core else "secondary"
    return out


def _describe(d, c):
    try:
        import formalize
        return formalize.plain(d, c)
    except Exception:                           # noqa: BLE001
        return d["name"]


def candidates(project):
    """Every relaxation the ladder may use, each with original → relaxed text."""
    imp = importance(project)
    defs = {d["id"]: d for d in project["definitions"]}
    out = []
    for c in project["hypothesis"]["conditions"]:
        d = defs.get(c["definition_id"])
        if not d:
            continue
        label = f"{c.get('letter')}: {_describe(d, c)}"
        if d["concept"] == "liquidity_sweep":
            levels = d["params"].get("levels") or []
            side = d["params"].get("side", "both")
            broad = BROAD_LEVELS.get(side, BROAD_LEVELS["both"])
            if levels and not set(broad) <= set(levels):
                out.append({"id": f"levels:{c['id']}", "kind": "levels", "condition_id": c["id"],
                            "original": f"{label} (specifically: {', '.join(LEVEL_NAMES.get(k, k) for k in levels)})",
                            "relaxed": f"Any major {side if side != 'both' else ''} liquidity sweep: "
                                       + ", ".join(LEVEL_NAMES[k] for k in broad),
                            "why": "Keeps the liquidity-sweep thesis but not the specific level."})
        if imp.get(c["id"]) != "secondary":
            continue
        if d["concept"] == "unsupported":
            nearest = d["params"].get("nearest")
            import formalize
            build = formalize.NEAREST_BUILD.get(d["params"].get("label"))
            if build:
                out.append({"id": f"nearest:{c['id']}", "kind": "nearest", "condition_id": c["id"], "original": label,
                            "relaxed": f"Nearest measurable version: {nearest}", "why": "The original concept has no detector yet."})
                continue
            out.append({"id": f"drop:{c['id']}", "kind": "drop", "condition_id": c["id"], "original": label,
                        "relaxed": "Omitted (can't be measured yet)", "why": d["params"].get("needs") or "No detector yet."})
            continue
        out.append({"id": f"drop:{c['id']}", "kind": "drop", "condition_id": c["id"], "original": label,
                    "relaxed": "Omitted", "why": "Secondary to the core thesis."})
    window = S._session_window(project.get("settings"))
    if window:
        out.append({"id": "session", "kind": "session", "class": "execution", "original": f"Entries only {window[0]}–{window[1]} ET",
                    "relaxed": "Any time of day", "why": "When to enter is execution, not the thesis."})
    entries = project.get("entries") or []
    if entries and entries[0].get("kind") in LIMIT_ENTRIES:
        import research_projects as R
        out.append({"id": "entry", "kind": "entry", "class": "execution", "original": R.ENTRY_KINDS.get(entries[0]["kind"], entries[0]["kind"]),
                    "relaxed": "Enter at the close of the confirming candle (no limit order to fill)",
                    "why": "A limit order often isn't filled, or price passes the stop first: an execution detail."})
    for c in out:
        c.setdefault("class", "secondary")
    return out


def apply(project, relax_ids):
    """A copy of the project with the chosen relaxations applied (the project itself is never changed)."""
    v = copy.deepcopy(project)
    cands = {c["id"]: c for c in candidates(project)}
    applied = []
    defs = {d["id"]: d for d in v["definitions"]}
    for rid in relax_ids:
        c = cands.get(rid)
        if not c:
            raise S.StrategyError(f"Unknown relaxation {rid!r}.")
        applied.append({k: c.get(k) for k in ("id", "original", "relaxed", "why", "class")})
        if c["kind"] == "drop":
            v["hypothesis"]["conditions"] = [x for x in v["hypothesis"]["conditions"] if x["id"] != c["condition_id"]]
        elif c["kind"] == "levels":
            cond = next(x for x in v["hypothesis"]["conditions"] if x["id"] == c["condition_id"])
            d = defs[cond["definition_id"]]
            d["params"]["levels"] = BROAD_LEVELS.get(d["params"].get("side", "both"), BROAD_LEVELS["both"])
        elif c["kind"] == "session":
            v["settings"]["session"] = None
        elif c["kind"] == "entry":
            v["entries"] = [{**v["entries"][0], "kind": "validation_close", "params": {}}]
        elif c["kind"] == "nearest":
            import formalize
            cond = next(x for x in v["hypothesis"]["conditions"] if x["id"] == c["condition_id"])
            d = defs[cond["definition_id"]]
            bias = next((x["params"].get("direction") for x in v["definitions"] if x["params"].get("direction") in ("bullish", "bearish")), None)
            tf = next((x["params"].get("timeframe") for x in v["definitions"] if x["params"].get("timeframe")), "5m")
            nd, nc = formalize.NEAREST_BUILD[d["params"]["label"]](bias, tf)
            d.update(concept=nd["concept"], params=nd["params"], name=nd["name"])
            cond.update(requirement=nc["requirement"], timing=nc["timing"])
    for i, c in enumerate(v["hypothesis"]["conditions"]):
        c["letter"] = chr(65 + i)
    v["ladder_relaxations"] = applied
    return v


def count(project, ds, facts):
    """(setups, problems, funnel) without simulating trades."""
    try:
        setups, info = S.find_setups(project, ds, facts)
        return len(setups), None, info["funnel"], info
    except S.UntestableError as error:
        return None, error.problems, None, None
    except S.StrategyError as error:
        return None, [{"text": str(error), "kind": "concept", "condition_id": None}], None, None


def build(project, ds, facts=None):
    facts = facts or S.Facts(ds)
    cands = candidates(project)
    imp = importance(project)
    n_exact, problems, funnel, info = count(project, ds, facts)
    tested = 1
    required = []
    blocked = []
    for p in problems or []:
        cid = p.get("condition_id")
        fix = next((c for c in cands if c.get("condition_id") == cid and c["kind"] in ("drop", "nearest")), None)
        if fix:
            required.append(fix["id"])
        else:
            blocked.append(p)
    tiers = [{"tier": "exact", "label": "Exact setup", "setups": n_exact, "relaxations": [], "relax_ids": [],
              "problems": problems or [], "testable": n_exact is not None}]
    # VERY SIMILAR: required fixes → execution → level generalisation → secondary conditions (greedy by SETUPS)
    optional = [c for c in cands if c["id"] not in required]
    alternatives = []
    similar_ids = list(required)
    if not blocked:
        steps = [c for c in optional if c["class"] == "execution"] + [c for c in optional if c["kind"] == "levels"]
        n = count(apply(project, required), ds, facts)[0] if required else n_exact
        for c in steps:
            similar_ids.append(c["id"])
        if steps:
            n = count(apply(project, similar_ids), ds, facts)[0]
            tested += 1
        drops = [c for c in optional if c not in steps]
        while drops and (n or 0) < SIMILAR_TARGET:
            trial = []
            for c in drops:
                k = count(apply(project, similar_ids + [c["id"]]), ds, facts)[0]
                tested += 1
                trial.append((k or 0, c))
                alternatives.append({"id": c["id"], "original": c["original"], "relaxed": c["relaxed"], "setups": k})
            best_n, best = max(trial, key=lambda x: x[0])
            if best_n <= (n or 0):
                break
            similar_ids.append(best["id"])
            drops.remove(best)
            n = best_n
            if len(similar_ids) - len(required) >= len(steps) + 2:      # very similar: at most two secondary conditions relaxed
                break
        seen = set()
        alternatives = [a for a in alternatives if a["id"] not in similar_ids and not (a["id"] in seen or seen.add(a["id"]))]
    sim_n = count(apply(project, similar_ids), ds, facts)[0] if similar_ids and not blocked else None
    if similar_ids and not blocked and similar_ids != []:
        tiers.append({"tier": "similar", "label": "Very similar setups", "setups": sim_n, "relax_ids": similar_ids,
                      "relaxations": apply(project, similar_ids)["ladder_relaxations"], "testable": sim_n is not None,
                      "alternatives": alternatives})
    broad_ids = required + [c["id"] for c in optional]
    dropped = {i.split(":", 1)[1] for i in broad_ids if i.startswith(("drop:", "nearest:"))}
    broad_ids = [i for i in broad_ids if not (i.startswith("levels:") and i.split(":", 1)[1] in dropped)]  # omitted beats generalised
    if broad_ids and set(broad_ids) != set(similar_ids) and not blocked:
        b_n = count(apply(project, broad_ids), ds, facts)[0]
        tested += 1
        tiers.append({"tier": "broader", "label": "Broader thesis", "setups": b_n, "relax_ids": broad_ids,
                      "relaxations": apply(project, broad_ids)["ladder_relaxations"], "testable": b_n is not None})
    defs = {d["id"]: d for d in project["definitions"]}
    core = [_describe(defs[c["definition_id"]], c) for c in project["hypothesis"]["conditions"]
            if imp.get(c["id"]) == "core" and c["definition_id"] in defs]
    for t in tiers:
        t["summary"] = _summary(t, core)
    return {"tiers": tiers, "core": core, "diagnosis": diagnose(n_exact, problems, funnel, info, ds, project, blocked),
            "variants_counted": tested}


def _summary(tier, core):
    if tier["tier"] == "exact":
        return "Every rule exactly as described."
    ex = sum(1 for r in tier["relaxations"] if r.get("class") == "execution")
    sec = len(tier["relaxations"]) - ex
    parts = [f"{ex} execution detail{'' if ex == 1 else 's'}" if ex else "", f"{sec} secondary rule{'' if sec == 1 else 's'}" if sec else ""]
    return f"Keeps the core: {' + '.join(core)}. Relaxed: {' and '.join(p for p in parts if p)}."


def diagnose(n_exact, problems, funnel, info, ds, project, blocked):
    """Why the exact setup has zero matches: DATA unavailable, CONCEPT not detectable, or the PATTERN didn't occur."""
    days = len(set(ds.series.day))
    history = f"{days} trading days of {ds.symbol} {ds.base} data"
    reasons = []
    if problems:
        data = [p for p in problems if p.get("kind") == "data"]
        concept = [p for p in problems if p.get("kind") != "data"]
        for p in data:
            reasons.append({"kind": "data", "text": p["text"]})
        for p in concept:
            reasons.append({"kind": "concept", "text": p["text"]})
        verdict = "DATA" if data else "CONCEPT"
        headline = ("0 matches because required data is unavailable" if data else
                    "0 matches because a concept can't be detected yet")
        return {"verdict": verdict, "headline": headline, "reasons": reasons, "history": history, "blocked": bool(blocked)}
    if n_exact:
        return {"verdict": "OK", "headline": f"{n_exact} exact match{'es' if n_exact != 1 else ''} in {history}.",
                "reasons": [], "history": history}
    if funnel:
        steps = [(funnel[i - 1], funnel[i]) for i in range(1, len(funnel))]
        drops = sorted(steps, key=lambda ab: ab[0]["remaining"] - ab[1]["remaining"], reverse=True)
        for a, b in drops[:2]:
            if a["remaining"] - b["remaining"] > 0:
                reasons.append({"kind": "pattern", "text": f"“{b['name']}”{(' — ' + b['requirement']) if b['requirement'] else ''} removed "
                                                            f"{a['remaining'] - b['remaining']} of {a['remaining']} remaining candidates."})
        if funnel[0]["remaining"] == 0:
            reasons.append({"kind": "pattern", "text": f"The trigger (“{funnel[0]['name']}”) never happened in {history}."})
    conds = project["hypothesis"]["conditions"]
    if len(conds) >= 5:
        reasons.append({"kind": "pattern", "text": f"{len(conds)} conditions must all line up in order: each one multiplies the rarity."})
    if days < 60:
        reasons.append({"kind": "history", "text": f"Only {history} (free data). A rare setup may simply not have happened yet in this window."})
    _session_level_coverage(project, ds, reasons)
    return {"verdict": "PATTERN", "headline": f"0 matches: the exact pattern didn't occur in {history}.", "reasons": reasons,
            "history": history}


def _session_level_coverage(project, ds, reasons):
    from . import concepts as K
    defs = {d["id"]: d for d in project["definitions"]}
    wanted = set()
    for c in project["hypothesis"]["conditions"]:
        d = defs.get(c["definition_id"])
        if d and d["concept"] == "liquidity_sweep":
            wanted |= set(d["params"].get("levels") or []) & {"lonl", "lonh", "asial", "asiah", "nyaml", "nyamh"}
    if not wanted:
        return
    days = len(set(ds.series.day)) or 1
    have = {}
    for lvl in K.session_range_levels(ds.series):
        if lvl["kind"] in wanted:
            have[lvl["kind"]] = have.get(lvl["kind"], 0) + 1
    for k in sorted(wanted):
        n = have.get(k, 0)
        if n < 0.6 * days:
            reasons.append({"kind": "data", "text": f"{LEVEL_NAMES[k]}: complete session data on only {n} of {days} days "
                                                    "(missing or suspect candles in that window)."})


def run_tier(project, ds, relax_ids, combo=None):
    """Run a ladder tier: the relaxed copy goes through the normal backtester and is labelled with its tier."""
    variant = apply(project, relax_ids) if relax_ids else project
    result = S.run(variant, ds, combo)
    result = dict(result)
    result["tier"] = {"relax_ids": list(relax_ids), "relaxations": variant.get("ladder_relaxations", [])}
    return result


def auto_test(project, ds, facts=None):
    """
    One call for "paragraph → results": build the ladder, then run tiers from the most specific
    (exact → similar → broader) and show the first one with at least MIN_TRADES completed trades
    (or, if none reaches it, the one with the most). Every tier that was run reports its trade count,
    and the chosen tier is labelled. Selection uses sample size only, never returns.
    """
    facts = facts or S.Facts(ds)
    L = build(project, ds, facts)
    chosen, best = None, None
    for t in L["tiers"]:
        if not t.get("setups"):
            t["trades"] = 0 if t.get("setups") == 0 else None
            continue
        r = run_tier(project, ds, t.get("relax_ids") or [])
        r["tier"]["name"] = t["tier"]
        n = ((r.get("primary") or {}).get("metrics") or {}).get("n") or 0
        t["trades"] = n
        if best is None or n > best[0]:
            best = (n, t["tier"], r)
        if n >= MIN_TRADES:
            chosen = (n, t["tier"], r)
            break
    pick = chosen or best
    L["auto"] = {"tier": pick[1] if pick else None, "min_trades": MIN_TRADES,
                 "reason": (None if not pick else
                            "Every rule exactly as described." if pick[1] == "exact" and chosen else
                            f"The exact rules gave fewer than {MIN_TRADES} completed trades, so the closest tier with a usable sample is shown — "
                            "every change is listed. You can switch tiers at any time.")}
    return L, (pick[2] if pick else None)
