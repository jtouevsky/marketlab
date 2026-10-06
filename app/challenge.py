"""
challenge.py — "Challenge this result": deterministic attempts to break an
apparent pattern.

Each test asks one question a skeptic would ask, and reports the numbers.
There is NO overall score. The summary lists what survived and what should
make you cautious, each with the explicit rule and the calculation behind it,
so every line can be checked by hand.

    outliers      Does it survive without the most extreme outcomes?
    time_split    Earlier vs later half of the episodes (equal counts, not equal years)
    recency       Full history vs the last 5 and 3 years
    neighbors     Small changes to the arbitrary parameters (3 x 3 grid)
    summary       What survived / what should make you cautious

Comparisons are made against the NORMAL return of the same period wherever
possible ("difference" = average after condition − normal average), because a
positive average can still be worse than doing nothing special.
"""

import statistics

import conditions as C
import experiments

MIN_PART = 8          # fewer episodes than this in a sub-sample: shown, but not judged
MIN_GRID_CELL = 10


def _stats(values):
    if not values:
        return None
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "positive_rate": sum(1 for v in values if v > 0) / len(values)}


def _baseline_between(prep, horizon, start_date, end_date):
    """Normal H-day returns starting on days within [start_date, end_date]."""
    dates, closes = prep["bars"]["date"], prep["bars"]["close"]
    out = []
    for t in range(prep["first"], prep["last"] - horizon + 1):
        if start_date <= dates[t] <= end_date and not experiments._has_gap(prep, max(0, t - prep["warm"]), t + horizon):
            out.append(closes[t + horizon] / closes[t] - 1)
    return out


def _part(prep, horizon, events, label, start, end):
    values = [e["forward_return"] for e in events]
    s = _stats(values)
    base = _stats(_baseline_between(prep, horizon, start, end))
    return {"label": label, "from": start, "to": end, "stats": s, "baseline": base,
            "difference": (s["mean"] - base["mean"]) if (s and base) else None,
            "enough": bool(s and s["n"] >= MIN_PART)}


# ---------------------------------------------------------------------------
def outliers(values, rb):
    s = _stats(values)
    rows = [
        {"label": "All episodes", "value": s["mean"], "n": s["n"]},
        {"label": "Median", "value": s["median"], "n": s["n"]},
        {"label": f"Trimmed mean ({rb['trim_fraction']:.0%} each side)", "value": rb["trimmed_mean"],
         "n": s["n"] - 2 * (rb["trimmed_each_side"] or 0)},
        {"label": "Without the largest absolute outcome", "value": rb["mean_without_extreme"], "n": s["n"] - 1},
        {"label": "Without the best and the worst outcome", "value": rb["mean_without_both_extremes"], "n": s["n"] - 2},
    ]
    return {"rows": rows, "most_extreme": rb["most_extreme"], "contribution": rb["contribution"]}


def time_split(prep, horizon, completed):
    """Equal numbers of episodes in each half, so neither half is starved of data."""
    if len(completed) < 2 * MIN_PART:
        return {"available": False, "reason": f"Needs at least {2 * MIN_PART} episodes (has {len(completed)})."}
    half = len(completed) // 2
    early, late = completed[:half], completed[half:]
    dates = prep["bars"]["date"]
    split_date = late[0]["trigger_date"]
    early_end = dates[max(prep["first"], dates.index(split_date) - 1)]
    return {"available": True, "parts": [
        _part(prep, horizon, early, "Earlier half", dates[prep["first"]], early_end),
        _part(prep, horizon, late, "Later half", split_date, dates[prep["last"]]),
    ]}


def recency(prep, horizon, completed):
    dates = prep["bars"]["date"]
    last = dates[prep["last"]]
    year = int(last[:4])
    parts = [_part(prep, horizon, completed, "Full history", dates[prep["first"]], last)]
    for years in (5, 3):
        cutoff = f"{year - years}{last[4:]}"
        if cutoff <= dates[prep["first"]]:
            continue
        subset = [e for e in completed if e["trigger_date"] >= cutoff]
        if len(subset) == len(completed):
            continue                       # the whole history is shorter than this window
        parts.append(_part(prep, horizon, subset, f"Last {years} years", cutoff, last))
    return {"available": len(parts) > 1, "parts": parts}


# ---------------------------------------------------------------------------
# Parameter neighbors
# ---------------------------------------------------------------------------
def _step(name, value, spec):
    """A 'small change' for each kind of parameter."""
    if name == "threshold":
        delta = 1.0 if value <= 6 else max(1.0, round(value * 0.2 * 2) / 2)
    elif name == "window":
        delta = 1 if value <= 10 else max(2, round(value * 0.2))
    elif name == "multiple":
        delta = 0.25
    elif name == "lookback":
        delta = 5
    elif name == "level":
        delta = 5
    elif name == "period":
        delta = 2 if value <= 30 else max(5, round(value * 0.1 / 5) * 5)
    else:
        delta = spec.get("step", 1)
    return delta


def neighbor_values(name, value, spec):
    """[value − step, value, value + step], clipped to the parameter's limits (deduplicated)."""
    delta = _step(name, value, spec)
    out = []
    for v in (value - delta, value, value + delta):
        v = min(spec["max"], max(spec["min"], v))
        v = int(round(v)) if spec["kind"] == "int" else round(v, 4)
        if v not in out:
            out.append(v)
    return out


def neighbor_axes(definition):
    """
    Which two things to vary: the first condition's two numeric 'neighbor' parameters,
    or one parameter and the forward period.
    """
    for i, cond in enumerate(definition["conditions"]):
        spec = C.REGISTRY[cond["type"]]
        names = [n for n in spec["neighbors"] if spec["params"][n]["kind"] != "choice"]
        if not names:
            continue
        axes = [{"path": f"conditions.{i}.{n}", "name": n, "condition": i,
                 "values": neighbor_values(n, cond["params"][n], spec["params"][n]),
                 "unit": spec["params"][n].get("unit", "")} for n in names[:2]]
        if len(axes) == 1:
            h = definition["outcome"]["horizon"]
            hs = {"kind": "int", "min": 1, "max": 252}
            delta = max(1, round(h * 0.2))
            axes.append({"path": "horizon", "name": "forward period", "condition": None,
                         "values": sorted({min(252, max(1, x)) for x in (h - delta, h, h + delta)}), "unit": "trading days"})
        return axes
    return []


def neighbors(definition):
    axes = neighbor_axes(definition)
    if len(axes) < 2:
        return {"available": False, "reason": "No adjustable numeric parameters to vary."}
    rows = []
    for a in axes[0]["values"]:
        row = []
        for b in axes[1]["values"]:
            variant = experiments.with_param(experiments.with_param(definition, axes[0]["path"], a), axes[1]["path"], b)
            variant = experiments.normalize(variant)
            horizon = variant["outcome"]["horizon"]
            prep = experiments.prepare(variant)
            episodes, _, _ = experiments.detect_triggers(prep, horizon)
            events = [e for e in experiments.measure_outcomes(prep, episodes, horizon, with_details=False)
                      if e["status"] == "complete"]
            s = _stats([e["forward_return"] for e in events])
            base = _stats(experiments.baseline_returns(prep, horizon))
            row.append({"a": a, "b": b, "stats": s, "baseline_mean": base["mean"] if base else None,
                        "difference": (s["mean"] - base["mean"]) if (s and base) else None,
                        "center": a == _center(axes[0], definition) and b == _center(axes[1], definition)})
        rows.append(row)
    return {"available": True, "axes": axes, "rows": rows}


def _center(axis, definition):
    if axis["path"] == "horizon":
        return definition["outcome"]["horizon"]
    _, i, name = axis["path"].split(".")
    return definition["conditions"][int(i)]["params"][name]


# ---------------------------------------------------------------------------
# Summary: explicit rules, no score
# ---------------------------------------------------------------------------
def pct(x):
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def summarize_checks(result, out):
    s, rb, base = result["stats"], result["robust"], result["baseline"]
    good, caution = [], []

    def add(ok, text, calc):
        (good if ok else caution).append({"text": text, "calc": calc})

    n = s["n"]
    add(n >= 30, f"{n} historical episodes" if n >= 30 else f"Only {n} historical episodes",
        f"Rule: fewer than 30 episodes is treated as a small sample. n = {n}.")

    same = (s["mean"] > 0) == (s["median"] > 0)
    add(same, "Mean and median point the same way" if same else "Mean and median point in opposite directions",
        f"mean {pct(s['mean'])}, median {pct(s['median'])}")

    if rb["trimmed_mean"] is not None:
        keeps = (rb["trimmed_mean"] > 0) == (s["mean"] > 0)
        close = abs(rb["trimmed_mean"] - s["mean"]) <= max(0.25 * abs(s["mean"]), 0.002)
        add(keeps and close, "Result is similar after trimming extremes" if keeps and close
            else "Trimming the extremes changes the result materially",
            f"Rule: same sign and within 25% of the mean (or 0.2 pts). mean {pct(s['mean'])}, trimmed {pct(rb['trimmed_mean'])}.")

    top = next((c for c in rb["contribution"]["gains"] if c["k"] == 3), None)
    if top and s["mean"] > 0:
        add(top["share"] < 0.5, f"The 3 largest gains are {top['share']:.0%} of all gains",
            f"Rule: caution when the top 3 of {rb['contribution']['n_gains']} gains supply half or more of the total gain. "
            f"Sum of top 3 / sum of all gains = {top['share']:.1%}.")

    if s["ci_low"] is not None:
        crosses = s["ci_low"] <= 0 <= s["ci_high"]
        add(not crosses, "95% interval for the average excludes zero" if not crosses else "95% interval for the average crosses zero",
            f"t-interval {pct(s['ci_low'])} to {pct(s['ci_high'])}; bootstrap {pct(rb['bootstrap_mean_ci'][0])} to {pct(rb['bootstrap_mean_ci'][1])}.")

    if base:
        diff = s["mean"] - base["mean"]
        inside = result.get("baseline_inside_ci")
        add(inside is False, "Average differs from normal by more than the sampling noise" if inside is False
            else "Average is within sampling noise of a normal period",
            f"after condition {pct(s['mean'])} vs normal {pct(base['mean'])} (difference {diff * 100:+.2f} pts); "
            f"normal lies {'outside' if inside is False else 'inside'} the 95% interval.")
        if diff < 0 < s["mean"]:
            caution.append({"text": "Positive on average, but below a normal period",
                            "calc": f"{pct(s['mean'])} after the condition vs {pct(base['mean'])} for any {result['definition']['outcome']['horizon']}-day period."})

    ts = out.get("time_split") or {}
    if ts.get("available") and all(p["enough"] and p["difference"] is not None for p in ts["parts"]):
        a, b = ts["parts"]
        same_dir = (a["difference"] > 0) == (b["difference"] > 0)
        small, large = sorted([abs(a["difference"]), abs(b["difference"])])
        similar_size = large == 0 or small / large >= 1 / 3
        if same_dir and similar_size:
            text = "Earlier and later episodes differ from normal in the same direction, by a similar amount"
        elif same_dir:
            text = "Earlier and later episodes point the same way, but by very different amounts"
        else:
            text = "Earlier and later episodes behave differently"
        add(same_dir and similar_size, text,
            "Rule: same direction vs normal, and the smaller difference is at least a third of the larger. "
            f"{a['from'][:4]}–{a['to'][:4]}: {pct(a['stats']['mean'])} vs normal {pct(a['baseline']['mean'])} (n={a['stats']['n']}); "
            f"{b['from'][:4]}–{b['to'][:4]}: {pct(b['stats']['mean'])} vs normal {pct(b['baseline']['mean'])} (n={b['stats']['n']}).")

    rc = out.get("recency") or {}
    recent = [p for p in rc.get("parts", [])[1:] if p["enough"] and p["difference"] is not None]
    full = rc.get("parts", [None])[0] if rc.get("parts") else None
    if recent and full and full["difference"] is not None:
        p = recent[0]
        same_dir = (p["difference"] > 0) == (full["difference"] > 0)
        add(same_dir, f"{p['label']} look like the full history" if same_dir else f"{p['label']} behave differently from the full history",
            f"full: {full['difference'] * 100:+.2f} pts vs normal (n={full['stats']['n']}); "
            f"{p['label'].lower()}: {p['difference'] * 100:+.2f} pts (n={p['stats']['n']}).")

    nb = out.get("neighbors") or {}
    if nb.get("available"):
        cells = [c for row in nb["rows"] for c in row]
        center = next((c for c in cells if c["center"]), None)
        judged = [c for c in cells if c["stats"] and c["stats"]["n"] >= MIN_GRID_CELL and c["difference"] is not None]
        if center and center["difference"] is not None and len(judged) >= 4:
            agree = sum(1 for c in judged if (c["difference"] > 0) == (center["difference"] > 0))
            ok = agree / len(judged) >= 0.75
            means = [c["stats"]["mean"] for c in judged]
            add(ok, f"Nearby parameter settings agree in direction ({agree} of {len(judged)})" if ok
                else f"Nearby parameter settings disagree (only {agree} of {len(judged)} point the same way)",
                f"Rule: at least 75% of the neighbouring settings (with ≥ {MIN_GRID_CELL} episodes) must differ from normal in the same direction "
                f"as the chosen setting. {agree} of {len(judged)} do. Averages across the grid range from {pct(min(means))} to {pct(max(means))}.")

    return {"survived": good, "caution": caution}


def challenge(raw_definition):
    definition = experiments.normalize(raw_definition)
    result = experiments.run(definition)
    if not result["stats"] or result["stats"]["n"] < 3:
        return {"available": False, "reason": "Too few episodes to challenge.", "definition": definition}
    horizon = definition["outcome"]["horizon"]
    prep = experiments.prepare(definition)
    completed = sorted((e for e in result["events"] if e["status"] == "complete"), key=lambda e: e["trigger_date"])
    values = [e["forward_return"] for e in completed]
    out = {
        "available": True,
        "definition": definition,
        "result_summary": experiments.summary_for_notebook(result),
        "outliers": outliers(values, result["robust"]),
        "time_split": time_split(prep, horizon, completed),
        "recency": recency(prep, horizon, completed),
        "neighbors": neighbors(definition),
    }
    out["summary"] = summarize_checks(result, out)
    return out
