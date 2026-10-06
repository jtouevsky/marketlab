"""
formalize.py — "Describe My Trade": a trade described in normal language → an explicit,
testable strategy. Deterministic (no AI): the same words always give the same structure.

    DESCRIPTION  →  mentions (concept, timeframe, direction, phrase, position)
                 →  definitions (Concept Library or the user's saved definitions)
                 →  ordered conditions with roles (context / prerequisite / setup / confirmation)
                 →  entry, stop, target, time rules
                 →  TRADE RECIPE + TIMELINE (human-readable)
                 →  AMBIGUITIES (only questions whose answer changes detection or results)
                 →  UNSUPPORTED (recognised but not measurable; never silently dropped)
                 →  STATUS (READY only when everything is deterministic)

The result uses exactly the structures Manual mode edits (definitions, hypothesis.conditions,
entries, stops, exits, settings, timeframes), so both workflows run the same backtester.

Principles
    * Never add a trading rule the description doesn't contain. Library definitions of concepts
      (what an FVG is) are reused and shown; they are definitions, not new rules.
    * Every open choice has a SEVERITY:
          low     a safe default exists → applied automatically, listed under "Assumptions used"
          medium  several reasonable defaults → the best default is applied and visibly noted
          high    the answer materially changes the strategy → asked before testing
      (parse(..., auto_resolve=False) leaves every choice open, as Advanced mode shows it.)
    * Every condition is CORE (the thesis), SECONDARY (refines it) or part of EXECUTION; the
      match ladder (strategy.ladder) only ever relaxes secondary conditions, transparently.
    * A concept that can't be measured stays in the strategy as an untestable condition: the
      exact test reports it, the "similar" test says what it replaced it with.
    * Every interpretation records the phrase it came from.
"""

import hashlib
import re

import concept_library as C

TF_ORDER = C.TIMEFRAMES
TF_WORDS = [
    (r"\b1\s*-?\s*(?:m|min|minute)s?\b|\bone[\s-]*minute\b", "1m"),
    (r"\b5\s*-?\s*(?:m|min|minute)s?\b|\bfive[\s-]*minute\b", "5m"),
    (r"\b15\s*-?\s*(?:m|min|minute)s?\b|\bfifteen[\s-]*minute\b", "15m"),
    (r"\b30\s*-?\s*(?:m|min|minute)s?\b|\bthirty[\s-]*minute\b", "30m"),
    (r"\b1\s*-?\s*(?:h|hr|hour)s?\b|\bone[\s-]*hour\b|\bhourly\b|\b60\s*-?\s*(?:m|min)\b", "1h"),
    (r"\b4\s*-?\s*(?:h|hr|hour)s?\b|\bfour[\s-]*hour\b", "4h"),
    (r"\bdaily\b|\b1\s*-?\s*d\b", "1d"),
]
TF_ANY = "|".join(f"(?:{p})" for p, _ in TF_WORDS)

# Concepts as they appear in trade descriptions. Order matters only for overlapping phrases.
MENTIONS = [
    ("fvg", r"fair[\s-]*value[\s-]*gaps?|\bfvgs?\b|\bimbalances?\b"),
    ("market_structure_break", r"market[\s-]*structure[\s-]*(?:breaks?|shifts?)|\bstructure[\s-]*(?:breaks?|shifts?)\b|\bbreaks? of structure\b|"
                               r"\bbos\b|\bmss\b|\bchoch\b|\bchange of character\b|\bshift in (?:market )?structure\b"),
    ("displacement", r"\bdisplacement\b|\bimpulsive (?:move|candle|leg|push)s?\b|\bexpansion candles?\b"),
    ("liquidity_sweep", r"\bsweep(?:s|ed|ing)?\b|\bswept\b|\braid(?:s|ed|ing)?\b|\bgrab(?:s|bed)?\b|\bstop[\s-]*(?:runs?|hunts?)\b|"
                        r"\btook out\b|\btakes? out\b|\btaken(?: out)?\b|\bruns? (?:the )?(?:stops|lows|highs)\b"),
    ("equal_highs", r"\bequal[\s-]*highs\b|\beqhs?\b|\bdouble[\s-]*tops?\b"),
    ("equal_lows", r"\bequal[\s-]*lows\b|\beqls?\b|\bdouble[\s-]*bottoms?\b"),
    ("vwap", r"\bvwap\b"),
    ("opening_range", r"\bopening[\s-]*range\b|\borb\b"),
    ("volume_spike", r"\bvolume (?:spike|expansion|surge)s?\b|\bhigh volume\b|\bheavy volume\b"),
    ("volatility_expansion", r"\bvolatility expansion\b|\brange expansion\b|\batr expansion\b"),
    ("candle", r"\bengulfing\b|\brejection (?:candle|wick)s?\b|\bpin[\s-]*bars?\b"),
    ("directional_move", r"\bmomentum\b"),
    ("smt_divergence", r"\bsmt(?:\s+divergence)?\b|\bdivergence\s+(?:against|with|vs\.?|versus)\s+(?:es|ym|nq|rty|mes|mnq)\b|\bsmt\b"),
]
LEVEL_WORDS = [
    ("pdl", r"\bpdl\b|previous[\s-]*day(?:'s)?[\s-]*lows?|yesterday'?s lows?|prior day(?:'s)? lows?"),
    ("pdh", r"\bpdh\b|previous[\s-]*day(?:'s)?[\s-]*highs?|yesterday'?s highs?|prior day(?:'s)? highs?"),
    ("onl", r"\bonl\b|overnight[\s-]*lows?"),
    ("onh", r"\bonh\b|overnight[\s-]*highs?"),
    ("pwl", r"\bpwl\b|previous[\s-]*week(?:'s)?[\s-]*lows?|last week'?s lows?"),
    ("pwh", r"\bpwh\b|previous[\s-]*week(?:'s)?[\s-]*highs?|last week'?s highs?"),
    ("lonl", r"london(?:\s+session)?(?:'s)?\s+lows?"),
    ("lonh", r"london(?:\s+session)?(?:'s)?\s+highs?"),
    ("asial", r"asia(?:n)?(?:\s+session)?(?:'s)?\s+lows?"),
    ("asiah", r"asia(?:n)?(?:\s+session)?(?:'s)?\s+highs?"),
    ("nyaml", r"(?:ny|new york)\s+(?:am|morning)(?:\s+session)?\s+lows?"),
    ("nyamh", r"(?:ny|new york)\s+(?:am|morning)(?:\s+session)?\s+highs?"),
    ("sessl", r"(?<!london )(?<!asia )(?<!asian )session lows?|low of (?:the )?day|\blod\b"),
    ("sessh", r"(?<!london )(?<!asia )(?<!asian )session highs?|high of (?:the )?day|\bhod\b"),
    ("eql", r"equal[\s-]*lows|\beqls?\b"),
    ("eqh", r"equal[\s-]*highs|\beqhs?\b"),
    ("swingl", r"swing lows?|(?:recent|internal|external) lows?"),
    ("swingh", r"swing highs?|(?:recent|internal|external) highs?"),
]
SELL_LEVELS = {"pdl", "onl", "pwl", "sessl", "eql", "swingl", "lonl", "asial", "nyaml"}
BUY_LEVELS = {"pdh", "onh", "pwh", "sessh", "eqh", "swingh", "lonh", "asiah", "nyamh"}
LEVEL_CONCEPT = {"pdh": "pdh", "pdl": "pdl", "onh": "overnight_high", "onl": "overnight_low",
                 "pwh": "previous_week_high", "pwl": "previous_week_low", "sessh": "session_high", "sessl": "session_low"}
POOL_OF_LEVEL = {"pdh": "pdhl", "pdl": "pdhl", "onh": "onhl", "onl": "onhl", "pwh": "pwhl", "pwl": "pwhl",
                 "sessh": "session", "sessl": "session", "swingh": "swing", "swingl": "swing", "eqh": "eqhl", "eql": "eqhl",
                 "lonh": "sessions", "lonl": "sessions", "asiah": "sessions", "asial": "sessions", "nyamh": "sessions", "nyaml": "sessions"}
SMT_DEFAULT_PEER = {"NQ": "ES", "MNQ": "ES", "ES": "NQ", "MES": "NQ", "YM": "ES", "MYM": "ES", "RTY": "ES", "M2K": "ES"}
UNSUPPORTED = [
    (r"\border[\s-]*blocks?\b|\bobs?\b(?= )", "Order block", "a definition of an order block (the last opposite candle before a displacement) — not built yet",
     "Displacement that leaves an FVG (Displacement → ‘Must leave a fair value gap’)"),
    (r"\bbreaker(?: blocks?)?\b", "Breaker block", "a breaker-block detector — not built yet", "A CHoCH / MSS in the trade's direction"),
    (r"\binverse (?:fvg|fair value gap)s?\b|\bifvgs?\b", "Inverse FVG", "an inverse-FVG detector (an FVG invalidated, then used from the other side) — not built yet",
     "An FVG of the opposite direction that is INVALIDATED (close beyond its far edge)"),
    (r"\bote\b|\bfib(?:onacci)?\b|\b(?:61\.8|62|70\.5|79)\s*%", "Fibonacci / OTE", "Fibonacci retracement levels — not built yet",
     "Entry at a 50% retracement into the FVG"),
    (r"\bpremium\b|\bdiscount\b", "Premium / discount", "a dealing-range definition — not built yet", "Price below the session's VWAP"),
    (r"\bcpi\b|\bfomc\b|\bnfp\b|\bppi\b|\bnews\b|\bnon[\s-]*farm\b", "Economic news", "an economic-calendar data source — none is connected yet",
     "A time-window condition around the release (e.g. 08:30–09:00 ET)"),
    (r"\bkill[\s-]*zones?\b", "Killzone", "an exact time window (killzone times differ between traders)", "A time window you choose (e.g. 09:30–11:00 ET)"),
    (r"\bsilver bullet\b", "Silver bullet", "an exact time window", "A time window you choose (e.g. 10:00–11:00 ET)"),
]
SESSION_WORDS = [
    (r"\bny\s*(?:am|morning)\b|\bnew york (?:am|morning)\b|\bam session\b|\bmorning session\b|\bduring the morning\b|\bin the morning\b", "morning"),
    (r"\bny\s*open\b|\bnew york open\b|\bat the open\b|\bopening bell\b|\bcash open\b", "ny_open"),
    (r"\brth\b|\bregular (?:trading )?hours\b|\bcash session\b", "rth"),
    (r"\blunch\b", "lunch"),
    (r"\bny\s*pm\b|\bafternoon\b|\bpm session\b", "afternoon"),
    (r"\bovernight session\b|\bglobex session\b", "overnight"),
    (r"\bpre[\s-]*market\b", "premarket"),
]
TIME = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?:\s*(?:et|est|edt|ny time|new york time))?"


def _clock(h, m, ap):
    h, m = int(h), int(m or 0)
    ap = (ap or "").replace(".", "").lower()
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 7:
        h += 12                    # "no entries after 2" during the day = 14:00
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def _sentences(text):
    """(start, end) spans of sentences/clauses separated by . ; ! ? or new lines."""
    spans, start = [], 0
    for m in re.finditer(r"[.;!?\n]+(?=\s|$)|\n", text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _sentence_of(spans, pos):
    for a, b in spans:
        if a <= pos < b:
            return a, b
    return 0, 0


def _clause(text, spans, pos):
    """The comma-separated part of the sentence containing pos."""
    a, b = _sentence_of(spans, pos)
    seg = text[a:b]
    cuts = [0] + [m.end() for m in re.finditer(r",|\bthen\b|\band then\b|\bbut\b", seg)] + [len(seg)]
    rel = pos - a
    for x, y in zip(cuts, cuts[1:]):
        if x <= rel < y:
            return a + x, a + y
    return a, b


def _tf_near(text, start, end):
    """Timeframe written right before ("5m FVG", "1-hour bullish FVG") or right after ("FVG on the 5m")."""
    before = text[max(0, start - 45):start]
    lead = re.search(rf"((?:{TF_ANY})(?:\s*(?:or|and|/|,)\s*(?:{TF_ANY}))*)\s*(?:(?:bullish|bearish|bull|bear|directional|fresh|valid|new|the|a|an|clean)\s+)*$",
                     before, re.I)
    if lead:
        found = [tf for p, tf in TF_WORDS if re.search(p, lead.group(1), re.I)]
        if found:
            return sorted(found, key=TF_ORDER.index)
    after = text[end:end + 30]
    trail = re.match(rf"\s*(?:on|in|from)\s+(?:the\s+)?((?:{TF_ANY}))(?:\s*(?:chart|timeframe|tf))?", after, re.I)
    if trail:
        return [tf for p, tf in TF_WORDS if re.search(p, trail.group(1), re.I)][:1]
    return []


def _direction_near(text, start, end, window=30):
    before = text[max(0, start - window):start].lower()
    after = text[end:end + 12].lower()
    words = re.findall(r"\b(bullish|bearish|bull|bear|upside|downside|up|down|long|short|buy[\s-]*side|sell[\s-]*side)\b", before)
    if words:
        w = words[-1]
        if w in ("bullish", "bull", "upside", "up", "long"):
            return "bullish"
        if w in ("bearish", "bear", "downside", "down", "short"):
            return "bearish"
    if re.match(r"\s*(?:to the )?(?:upside|up)\b", after):
        return "bullish"
    if re.match(r"\s*(?:to the )?(?:downside|down)\b", after):
        return "bearish"
    return None


def _overall_direction(text):
    t = text.lower()
    longs = len(re.findall(r"\b(?:go |going |get |enter |take a |take the |a )?long\b|\bbuy(?:ing)?\b(?!-?\s*side)|\bbullish\b", t))
    shorts = len(re.findall(r"\b(?:go |going |get |enter |take a |take the |a )?short\b|\bsell(?:ing)?\b(?!-?\s*side)|\bbearish\b", t))
    if re.search(r"\bboth directions\b|\blong or short\b|\bshort or long\b|\beither direction\b|\bboth ways\b", t):
        return "both"
    if re.search(r"\benter long\b|\bgo long\b|\bbuy\b(?!-?\s*side)|\blongs?\b(?! wick)", t) and not re.search(r"\benter short\b|\bgo short\b|\bshorts?\b", t):
        return "long"
    if re.search(r"\benter short\b|\bgo short\b|\bshorts?\b|\bsell\b(?!-?\s*side)", t) and not re.search(r"\benter long\b|\bgo long\b", t):
        return "short"
    if longs and not shorts:
        return "long"
    if shorts and not longs:
        return "short"
    return None


def _context_kind(text, spans, pos):
    """'stop', 'target' or None: whether a mention sits in a stop / target phrase (then it's not a condition)."""
    a, b = _clause(text, spans, pos)
    before = text[a:pos].lower()
    if re.search(r"\b(?:stop(?:[\s-]*loss)?|sl|invalidat\w*|risk)\b(?!.*\b(?:target|tp)\b)", before):
        return "stop"
    if re.search(r"\b(?:target(?:ing)?|tp|take[\s-]*profits?|aim(?:ing)? for|objective|draw on liquidity|dol|exit at|towards?|to the)\b", before):
        return "target"
    return None


def _uid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:8]


# ----------------------------------------------------------------------------- main

SEVERITY = {"direction": "high", "stop": "high", "target": "high", "target_pools": "medium",
            "lookback": "low", "sweep_levels": "medium", "tf": "medium", "tf_default": "medium", "ctx": "medium",
            "validation": "medium", "fvgreq": "medium", "req": "medium", "smt_peer": "low"}


def parse(text, answers=None, personal=None, instrument=None, auto_resolve=True):
    """Description → draft strategy (see module docstring). Pure function."""
    text = (text or "").strip()
    answers = dict(answers or {})
    personal = list(personal or [])
    low = text.lower()
    spans = _sentences(text)
    notes, ambiguities, unsupported, missing = [], [], [], []

    def ask(aid, label, question, options, phrase=None, why=None, suggested=None, multi=False):
        if any(a["id"] == aid for a in ambiguities):
            return answers.get(aid)
        severity = SEVERITY.get(aid.split(":")[0], "medium")
        answer, auto = answers.get(aid), False
        if answer is None and auto_resolve and severity != "high" and suggested is not None:
            answer, auto = suggested, True            # a safe/reasonable default: applied, and listed as an assumption
        item = {"id": aid, "label": label, "question": question, "options": options, "phrase": phrase, "why": why,
                "suggested": suggested, "multi": multi, "answer": answer, "auto": auto, "severity": severity}
        ambiguities.append(item)
        return item["answer"]

    # ---- instrument
    sym = None
    for s in sorted(C.INSTRUMENTS, key=len, reverse=True):
        if re.search(rf"\b{s}\b", text, re.I):
            sym = s
            break
    if not sym and re.search(r"\bnasdaq\b|\bnas100\b|\bnq\b|\bus100\b|\bustec\b", low):
        sym = "NQ"
    instrument_source = "description" if sym else "project"
    sym = sym or instrument or "NQ"

    # ---- direction
    direction = _overall_direction(text)
    if direction is None:
        direction = ask("direction", "Direction", "Which direction does this trade take?",
                        [{"value": "long", "label": "Long only"}, {"value": "short", "label": "Short only"},
                         {"value": "both", "label": "Both (each setup's own direction)"}],
                        why="The description doesn't say long or short; it decides which setups count.")
    bias = {"long": "bullish", "short": "bearish"}.get(direction)

    # ---- unsupported concepts (recognised, not measurable)
    for pattern, label, needs, nearest in UNSUPPORTED:
        m = re.search(pattern, text, re.I)
        if m:
            key = f"unsupported:{label}"
            unsupported.append({"id": key, "label": label, "phrase": m.group(0), "needs": needs, "nearest": nearest,
                                "start": m.start(), "answer": "kept", "buildable": label in NEAREST_BUILD, "options": [],
                                "handling": "Kept in the strategy as a condition MarketLab can't measure yet: the exact test reports it; "
                                            "the closest test " + ("uses the nearest measurable version." if label in NEAREST_BUILD else "leaves it out and says so.")})

    # ---- concept mentions
    mentions = []
    taken = []
    for concept, pattern in MENTIONS:
        for m in re.finditer(pattern, text, re.I):
            if any(a <= m.start() < b for a, b in taken):
                continue
            if concept == "fvg" and re.search(r"\binverse\s+$", text[max(0, m.start() - 9):m.start()], re.I):
                continue
            if concept == "liquidity_sweep" and re.match(r"taken", m.group(0), re.I) and not re.search(
                    r"\b(?:liquidity|low|high|pdl|pdh|onl|onh|lows|highs|stops)\b", text[max(0, m.start() - 40):m.end() + 25], re.I):
                continue
            # "after the sweep", "CE of the FVG", "that gap": a reference back to one already described, not a new condition
            if any(x["concept"] == concept and x["start"] < m.start() for x in mentions) and (
                    re.search(r"\b(?:the|that|this|same|its|said)\s+(?:(?:bullish|bearish|first|new)\s+)?$",
                              text[max(0, m.start() - 24):m.start()], re.I)):
                taken.append((m.start(), m.end()))
                continue
            kind = _context_kind(text, spans, m.start())
            mentions.append({"concept": concept, "phrase": m.group(0), "start": m.start(), "end": m.end(),
                             "tfs": _tf_near(text, m.start(), m.end()), "dir": _direction_near(text, m.start(), m.end()),
                             "context_kind": kind})
            taken.append((m.start(), m.end()))
    # a bare "liquidity ... swept" phrase: liquidity mention near a sweep verb counts as one sweep
    for m in re.finditer(r"\b(?:sell[\s-]*side|buy[\s-]*side|ssl|bsl)?\s*liquidity\b", text, re.I):
        near = text[m.end():m.end() + 60]
        if re.search(r"\b(?:swept|sweep|taken|grabbed|raided|run|cleared)\b", near, re.I) and not any(
                x["concept"] == "liquidity_sweep" and abs(x["start"] - m.end()) < 70 for x in mentions):
            mentions.append({"concept": "liquidity_sweep", "phrase": m.group(0).strip(), "start": m.start(), "end": m.end(),
                             "tfs": _tf_near(text, m.start(), m.end()), "dir": None, "context_kind": _context_kind(text, spans, m.start())})
    mentions.sort(key=lambda x: x["start"])
    level_hits = []
    for kind, pattern in LEVEL_WORDS:
        for m in re.finditer(pattern, text, re.I):
            level_hits.append({"kind": kind, "phrase": m.group(0), "start": m.start(), "end": m.end(),
                               "context_kind": _context_kind(text, spans, m.start())})

    conditions = [x for x in mentions if x["context_kind"] is None]
    # sweeps: attach levels named in the same sentence; side from words or direction
    sweeps = [x for x in conditions if x["concept"] == "liquidity_sweep"]
    merged = []
    for sw in sweeps:            # several sweep verbs in one sentence describe one sweep
        if merged and _sentence_of(spans, merged[-1]["start"]) == _sentence_of(spans, sw["start"]):
            conditions.remove(sw)
            continue
        merged.append(sw)
    for sw in merged:
        a, b = _sentence_of(spans, sw["start"])
        seg = text[a:b].lower()
        named = [h["kind"] for h in level_hits if a <= h["start"] < b and h["context_kind"] is None]
        side = None
        if re.search(r"sell[\s-]*side|\bssl\b|\blows?\b|\bpdl\b|\bonl\b", seg):
            side = "sell-side"
        if re.search(r"buy[\s-]*side|\bbsl\b|\bhighs?\b|\bpdh\b|\bonh\b", seg):
            side = "both" if side == "sell-side" and not named else side or "buy-side"
        if named:
            sides = {"sell-side" if k in SELL_LEVELS else "buy-side" for k in named}
            side = sides.pop() if len(sides) == 1 else "both"
        if side is None and bias:
            side = "sell-side" if bias == "bullish" else "buy-side"
            notes.append(f"“{sw['phrase']}”: {side} liquidity, because the trade is {direction} (a {bias} setup sweeps "
                         f"{'lows' if bias == 'bullish' else 'highs'}).")
        sw["side"] = side or "both"
        sw["phrase"] = re.sub(r"\s+", " ", text[a:b].strip())[:70] + ("…" if len(text[a:b].strip()) > 70 else "")
        sw["levels"] = sorted(set(named), key=[k for k, _ in LEVEL_WORDS].index) if named else None
        if not named:
            choice = ask(f"sweep_levels:{_uid(sw['start'])}", "Which liquidity counts",
                         f"“{sw['phrase']}”: which levels count as {sw['side'] if sw['side'] != 'both' else ''} liquidity?".replace("  ", " "),
                         [{"value": v, "label": l} for v, l in (
                             ("pdl,onl,swingl,pdh,onh,swingh", "Previous-day, overnight and same-day swing levels"),
                             ("pdl,onl,pdh,onh", "Previous-day and overnight levels only"),
                             ("pdl,onl,pwl,eql,swingl,pdh,onh,pwh,eqh,swingh", "All: previous day/week, overnight, equal and swing levels"),
                             ("swingl,swingh", "Swing highs/lows only"),
                             ("sessl,sessh", "The session's running high/low"))],
                         phrase=sw["phrase"], suggested="pdl,onl,swingl,pdh,onh,swingh",
                         why="The description doesn't name the levels; this decides which sweeps are found.")
            if choice:
                wanted = set(choice.split(","))
                keep = SELL_LEVELS if sw["side"] == "sell-side" else BUY_LEVELS if sw["side"] == "buy-side" else SELL_LEVELS | BUY_LEVELS
                sw["levels"] = sorted(wanted & keep, key=[k for k, _ in LEVEL_WORDS].index)

    # level checks ("price above PDH", "holding above VWAP" is handled as vwap concept)
    for h in level_hits:
        if h["context_kind"] is not None or h["kind"] not in LEVEL_CONCEPT:
            continue
        before = text[max(0, h["start"] - 25):h["start"]].lower()
        rel = re.search(r"\b(above|below|over|under)\s+(?:the\s+)?$", before)
        if rel and not any(sw["start"] <= h["start"] <= sw["start"] + 200 and _sentence_of(spans, sw["start"]) == _sentence_of(spans, h["start"])
                           for sw in merged):
            conditions.append({"concept": LEVEL_CONCEPT[h["kind"]], "phrase": text[h["start"] - len(rel.group(0)):h["end"]].strip(),
                               "start": h["start"], "end": h["end"], "tfs": [], "dir": None, "context_kind": None,
                               "requirement": "price above it" if rel.group(1) in ("above", "over") else "price below it"})
    for u in unsupported:              # recognised but unmeasurable: stays in the strategy, visibly
        conditions.append({"concept": "unsupported", "phrase": u["phrase"], "start": u["start"], "end": u["start"] + len(u["phrase"]),
                           "tfs": [], "dir": None, "context_kind": None, "unsupported": u})
    conditions.sort(key=lambda x: x["start"])

    # ---- timeframes and roles
    mentioned_tfs = sorted({tf for x in conditions for tf in x["tfs"]}, key=TF_ORDER.index)
    untimed = []
    for x in conditions:
        spec = C.CONCEPTS_BY_ID[x["concept"]]
        if "timeframe" not in spec["params"]:
            x["tf"] = None
            continue
        if len(x["tfs"]) > 1:
            choice = ask(f"tf:{_uid(x['start'])}", "Which timeframe", f"“{x['phrase']}” is mentioned on {' or '.join(x['tfs'])}. Which should this version test?",
                         [{"value": tf, "label": tf} for tf in x["tfs"]], phrase=x["phrase"], suggested=x["tfs"][0],
                         why="Different timeframes find different setups. The other one can be tested as a variation.")
            x["tf"] = choice or x["tfs"][0]
        elif x["tfs"]:
            x["tf"] = x["tfs"][0]
        else:
            untimed.append(x)
    if untimed:
        lower = [t for t in mentioned_tfs if TF_ORDER.index(t) <= TF_ORDER.index("15m")]
        guess = lower[0] if lower else "5m"
        names = ", ".join(f"“{x['phrase']}”" for x in untimed)
        choice = ask("tf_default", "Timeframe of the untimed steps", f"{names}: which timeframe are these measured on?",
                     [{"value": tf, "label": tf} for tf in ("1m", "5m", "15m", "1h")], phrase=names, suggested=guess,
                     why="No timeframe is written next to them, and the timeframe changes what is detected.")
        for x in untimed:
            x["tf"] = choice or guess
    tf_set = sorted({x["tf"] for x in conditions if x.get("tf")}, key=TF_ORDER.index)
    lowest = tf_set[0] if tf_set else None
    for x in conditions:
        tf = x.get("tf")
        htf_words = re.search(r"\b(?:htf|higher[\s-]*time[\s-]*frame|context|bias|draw)\b", text[max(0, x["start"] - 40):x["start"]], re.I)
        if tf and lowest and (TF_ORDER.index(tf) >= TF_ORDER.index("30m") and tf != lowest or htf_words) and x["concept"] in ("fvg", "displacement", "market_structure_break"):
            x["role"] = "context"
        elif x["concept"] in ("liquidity_sweep", "smt_divergence"):
            x["role"] = "prerequisite"
        elif x["concept"] in LEVEL_CONCEPT.values() or x["concept"] in ("vwap",) and x.get("requirement"):
            x["role"] = "filter"
        elif x["concept"] in ("displacement", "market_structure_break", "volume_spike", "volatility_expansion", "directional_move", "candle",
                              "equal_highs", "equal_lows", "opening_range", "vwap", "unsupported"):
            x["role"] = "setup"
        else:
            x["role"] = "confirmation"
        if re.search(r"\b(?:first|already|prior|before that|beforehand)\b", text[x["start"]:_sentence_of(spans, x["start"])[1]][:80], re.I) and x["role"] != "context":
            x["first"] = True
    # order: context first; "first"/"already" next; then as written, except "X after Y" puts Y before X
    for x in conditions:
        x["order"] = x["start"]
    for x in conditions:
        a, b = _sentence_of(spans, x["start"])
        seg = text[a:b]
        m = re.search(r"\b(?:after|once|following)\b", seg[x["end"] - a:], re.I)
        if m:
            after_pos = x["end"] + m.start()
            for y in conditions:
                if y is not x and a <= y["start"] < b and y["start"] > after_pos and y["role"] != "context":
                    y["order"] = min(y["order"], x["order"] - 0.5 - (y["start"] - after_pos) / 10000)
    conditions.sort(key=lambda x: (0 if x["role"] == "context" else 1, 0 if x.get("first") else 1, x["order"]))
    if conditions and conditions[-1]["role"] not in ("context",) and conditions[-1]["concept"] == "fvg":
        conditions[-1]["role"] = "confirmation"

    # ---- definitions (library or the user's saved ones) and requirements
    definitions, conds = [], []
    used_personal = []
    for i, x in enumerate(conditions):
        concept = x["concept"]
        spec = C.CONCEPTS_BY_ID[concept]
        params = C.default_params(concept)
        source = "library"
        mine = _personal_for(personal, concept, low)
        if mine:
            params.update(mine["params"])
            source = f"saved: {mine['name']}"
            used_personal.append(mine["name"])
        if x.get("tf") and "timeframe" in spec["params"]:
            params["timeframe"] = x["tf"]
        dir_ = x["dir"] or bias
        if "direction" in spec["params"] and dir_:
            params["direction"] = dir_
        requirement = x.get("requirement")
        timing = "at the setup candle"
        within = None
        name = f"{x['tf'] + ' ' if x.get('tf') else ''}{dir_ + ' ' if dir_ and 'direction' in spec['params'] else ''}{spec['label']}"
        if concept == "liquidity_sweep":
            params["side"] = x["side"]
            if x.get("levels"):
                params["levels"] = x["levels"]
            requirement = "confirmed sweep"
            name = f"{x['tf']} {x['side']} sweep" + (f" ({', '.join(k.upper() for k in x['levels'])})" if x.get("levels") else "")
        elif concept == "market_structure_break":
            p = x["phrase"].lower()
            kind = "mss" if re.search(r"\bmss\b|shift", p) else "choch" if re.search(r"choch|change of character", p) else \
                "bos" if re.search(r"\bbos\b|\bbreaks? of structure\b", p) else "any"
            params["type"] = kind
            requirement = "confirmed break"
            name = f"{x['tf']} {dir_ + ' ' if dir_ else ''}{dict(any='structure break', bos='BOS', choch='CHoCH', mss='MSS')[kind]}"
        elif concept == "fvg":
            requirement = _fvg_requirement(text, spans, x, ask, answers, params)
        elif concept == "smt_divergence":
            a, b = _sentence_of(spans, x["start"])
            peer = re.search(r"(?:against|with|vs\.?|versus|and)\s+(ES|YM|NQ|RTY|MES|MNQ)\b", text[a:b], re.I)
            if peer:
                params["compare_with"] = peer.group(1).upper().replace("MES", "ES").replace("MNQ", "NQ")
            else:
                default = SMT_DEFAULT_PEER.get(sym, "ES")
                params["compare_with"] = ask(f"smt_peer:{_uid(x['start'])}", "SMT comparison market",
                                             f"“{x['phrase']}”: compare {sym} with which market?",
                                             [{"value": v, "label": v} for v in ("ES", "NQ", "YM") if v != sym],
                                             phrase=x["phrase"], suggested=default,
                                             why="SMT needs a second, correlated market.") or default
            requirement = "confirmed divergence"
            name = f"{x['tf']} {dir_ + ' ' if dir_ else ''}SMT vs {params['compare_with']}"
        elif concept == "unsupported":
            u = x["unsupported"]
            params = {"label": u["label"], "needs": u["needs"], "nearest": u["nearest"] or ""}
            requirement = "occurs"
            name = f"{u['label']} (not measurable yet)"
        elif concept in ("vwap", "opening_range"):
            seg = text[x["end"]:x["end"] + 40].lower() + " " + text[max(0, x["start"] - 25):x["start"]].lower()
            if concept == "vwap":
                requirement = "crosses above" if re.search(r"reclaim|cross(?:es)? above|back above", seg) else \
                    "crosses below" if re.search(r"lose|cross(?:es)? below|back below", seg) else \
                    "price below it" if re.search(r"below|under", seg) else "price above it" if re.search(r"above|over", seg) else None
                if requirement is None:
                    requirement = ask(f"req:{_uid(x['start'])}", "VWAP rule", f"What must price do relative to VWAP (“{x['phrase']}”)?",
                                      [{"value": v, "label": v} for v in ("price above it", "price below it", "crosses above", "crosses below")],
                                      phrase=x["phrase"], suggested="price above it",
                                      why="VWAP can be a filter (above/below) or an event (cross).") or "price above it"
            else:
                requirement = "breakout (close beyond)" if re.search(r"break", seg) else "price above it" if "above" in seg else \
                    "price below it" if "below" in seg else "breakout (close beyond)"
        elif requirement is None:
            requirement = _default_requirement(concept)
        if x["role"] == "context":
            timing = "at the setup candle"
        elif i < len(conditions) - 1:
            timing, within = _lookback(text, x, ask, answers, low)
        did = f"d_{_uid(concept, x['start'], x.get('tf'))}"
        definitions.append({"id": did, "concept": concept, "name": name.strip(), "params": params, "source": source,
                            "phrase": x["phrase"]})
        conds.append({"id": f"c_{_uid(did)}", "letter": chr(65 + i), "definition_id": did, "requirement": requirement,
                      "timing": timing, "within": within, "role": x["role"], "phrase": x["phrase"]})
    # ---- core vs secondary: the thesis is the context, the (first) liquidity sweep and the final trigger
    first_sweep = next((c for c, d in zip(conds, definitions) if d["concept"] == "liquidity_sweep"), None)
    for i, (c, d) in enumerate(zip(conds, definitions)):
        core = c["role"] == "context" or c is first_sweep or (i == len(conds) - 1 and d["concept"] != "unsupported")
        c["importance"] = "core" if core else "secondary"

    # ---- time rules
    settings = {"session": None, "max_holding_minutes": None, "flat_time": None, "contract": sym if sym in C.INSTRUMENTS else None}
    session_key = None
    for pattern, key in SESSION_WORDS:
        m = re.search(pattern, text, re.I)
        if m:
            session_key, session_phrase = key, m.group(0)
            break
    start = end = None
    if session_key:
        start, end = C.SESSIONS[session_key]["start"], C.SESSIONS[session_key]["end"]
    m = re.search(rf"\bbetween\s+{TIME}\s+(?:and|-|–|to)\s+{TIME}", text, re.I)
    if m:
        start, end = _clock(*m.group(1, 2, 3)), _clock(*m.group(4, 5, 6))
    cut = re.search(rf"\b(?:no (?:new )?(?:entries|entry|trades?)|don'?t (?:enter|trade)|stop (?:trading|taking trades))\s+(?:after|past|beyond)\s+{TIME}", text, re.I) or \
        re.search(rf"\b(?:only )?(?:enter|entries|trade|take (?:the )?trades?)\b[^.]{{0,25}}?\b(?:before|until|by)\s+{TIME}", text, re.I) or \
        re.search(rf"\b(?:before|until)\s+{TIME}\s*(?:et|ny)?\b(?!\s*(?:the|a)\b)", text, re.I)
    if cut:
        end = _clock(*cut.group(1, 2, 3))
        start = start or "18:00"
    begin = re.search(rf"\b(?:not before|no (?:entries|trades) before|(?:only )?after|from)\s+{TIME}\b", text, re.I)
    if begin and not (cut and begin.start() == cut.start()) and not re.search(r"no (?:new )?(?:entries|entry|trades?)\s+after", text[begin.start() - 20:begin.end()], re.I):
        start = _clock(*begin.group(1, 2, 3))
        end = end or "17:00"
    if start and end:
        if session_key and (start, end) == (C.SESSIONS[session_key]["start"], C.SESSIONS[session_key]["end"]):
            settings["session"] = {"session": session_key, "start": start, "end": end}
        else:
            settings["session"] = {"session": "custom", "start": start, "end": end}
    flat = re.search(rf"\b(?:flat|out|close (?:all|everything|the trade|the position|positions?)|exit (?:all|everything|the trade)?)\s*(?:by|at|before)\s+{TIME}", text, re.I)
    if flat:
        settings["flat_time"] = _clock(*flat.group(1, 2, 3))
    hold = re.search(r"\b(?:hold(?:ing)?(?: time)?|max(?:imum)? hold(?:ing)?(?: time)?|for)\s*(?:of\s*)?(?:max(?:imum)?|up to|at most|no more than)?\s*(\d+)\s*(min(?:ute)?s?|m\b|hours?|hrs?|h\b)", text, re.I)
    if hold and re.search(r"hold|max|up to|at most|no more", hold.group(0), re.I):
        n = int(hold.group(1))
        settings["max_holding_minutes"] = n * 60 if hold.group(2).lower().startswith("h") else n

    # ---- entry / stop / target
    entry = _entry(text, conds, definitions)
    if entry and entry["kind"] in ("fvg_50", "fvg_first_touch"):
        # a limit entry inside the FVG: the FVG only has to exist; the entry itself waits for the retracement
        for c, d in zip(reversed(conds), reversed(definitions)):
            if d["concept"] == "fvg" and c["role"] != "context":
                if c["requirement"] in ("HALF_FILLED", "ENTERED", "PARTIALLY_FILLED"):
                    c["requirement"] = "CREATED (event)"
                    ambiguities[:] = [a for a in ambiguities if not a["id"].startswith(("fvgreq", "validation"))]
                notes.append(f"Entry: a limit order at the {'50% midpoint (consequent encroachment)' if entry['kind'] == 'fvg_50' else 'near edge'} "
                             f"of the {d['params'].get('timeframe')} FVG once it has formed.")
                break
    stop, stop_missing = _stop(text, conds, definitions, ask, bias)
    exit_, exit_missing = _target(text, ask, bias, level_hits)
    entries = [entry] if entry else []
    stops = [stop] if stop else []
    exits = [exit_] if exit_ else []

    # ---- timeframe roles
    ctx_tf = next((d["params"].get("timeframe") for d, c in zip(definitions, conds) if c["role"] == "context"), None)
    trig_tf = definitions[len(conds) - 1]["params"].get("timeframe") if conds and len(definitions) >= len(conds) else None
    setup_tfs = [d["params"].get("timeframe") for d, c in zip(definitions, conds) if c["role"] in ("setup", "prerequisite") and d["params"].get("timeframe")]
    finest = min([t for t in tf_set] or ["1m"], key=TF_ORDER.index)
    roles = {"context": ctx_tf, "setup": setup_tfs[-1] if setup_tfs else trig_tf, "confirmation": [trig_tf] if trig_tf else [],
             "execution": "1m" if finest != "1h" else finest}
    if re.search(r"\bexecute\b|\bexecution\b|\benter on the\b", low):
        e = re.search(rf"(?:execute|execution|enter)\s+(?:on|from)\s+(?:the\s+)?({TF_ANY})", text, re.I)
        if e:
            roles["execution"] = [tf for p, tf in TF_WORDS if re.search(p, e.group(1), re.I)][0]

    # ---- recipe, timeline, status
    by_role = lambda role: [(d, c) for d, c in zip(definitions, conds) if c["role"] == role]
    describe = lambda pairs: [f"{d['name']} — {c['requirement']}" for d, c in pairs]
    window = settings["session"]
    time_text = []
    if window:
        time_text.append(f"Entries {window['start']}–{window['end']} ET")
    if settings["flat_time"]:
        time_text.append(f"Flat by {settings['flat_time']} ET")
    if settings["max_holding_minutes"]:
        time_text.append(f"Hold at most {settings['max_holding_minutes']} min")
    from research_projects import idea_text
    recipe = [
        {"key": "MARKET", "values": [f"{sym}" + (" (from the project)" if instrument_source == "project" else "")]},
        {"key": "DIRECTION", "values": [{"long": "Long", "short": "Short", "both": "Both directions"}.get(direction, "?")],
         "pending": direction is None},
        {"key": "CONTEXT", "values": describe(by_role("context"))},
        {"key": "PREREQUISITE", "values": describe(by_role("prerequisite"))},
        {"key": "SETUP", "values": describe(by_role("setup"))},
        {"key": "FILTERS", "values": describe(by_role("filter"))},
        {"key": "CONFIRMATION", "values": describe(by_role("confirmation"))},
        {"key": "ENTRY", "values": [idea_text("entries", e) for e in entries], "pending": not entries},
        {"key": "STOP", "values": [idea_text("stops", s) for s in stops], "pending": not stops},
        {"key": "TARGET", "values": [idea_text("exits", x) for x in exits], "pending": not exits},
        {"key": "TIME", "values": time_text or ["No time restriction in the description"]},
    ]
    timeline = []
    step = 1
    for role, label in (("context", "context"), ("prerequisite", "prerequisite"), ("filter", "filter"), ("setup", "setup"), ("confirmation", "confirmation")):
        for d, c in by_role(role):
            timeline.append({"step": step, "role": label, "text": f"{d['name']} — {c['requirement']}",
                             "known": _known_text(d, c), "letter": c["letter"]})
            step += 1
    timeline.append({"step": step, "role": "entry", "text": "ENTRY ELIGIBLE — " + (idea_text("entries", entries[0]) if entries else "entry rule missing")})
    timeline.append({"step": step + 1, "role": "exit", "text": " · ".join(
        [idea_text("stops", s) for s in stops] + [idea_text("exits", x) for x in exits] + time_text) or "Exit rules missing"})
    if stop_missing:
        missing.append({"key": "stop", "label": "Stop / invalidation"})
    if exit_missing:
        missing.append({"key": "target", "label": "Target / exit"})
    if not conds:
        missing.append({"key": "setup", "label": "Setup conditions (no known concept was recognised)"})
    open_q = [a for a in ambiguities if not a["answer"]]
    open_u = [u for u in unsupported if not u["answer"]]
    trigger_ok = any(c["role"] != "context" for c in conds) or (conds and conds[-1]["requirement"] not in ("ACTIVE", "exists (CREATED)"))
    checks = [
        {"key": "instrument", "label": "Instrument", "ok": True},
        {"key": "direction", "label": "Direction", "ok": direction is not None},
        {"key": "setup", "label": "Setup", "ok": bool(conds) and trigger_ok},
        {"key": "entry", "label": "Entry", "ok": bool(entries)},
        {"key": "stop", "label": "Stop", "ok": bool(stops)},
        {"key": "exit", "label": "Exit", "ok": bool(exits)},
    ]
    ready = all(c["ok"] for c in checks) and not open_q
    card = _card(sym, direction, definitions, conds, entries, stops, exits, settings, session_key)
    assumptions = [{"id": a["id"], "label": a["label"], "severity": a["severity"], "question": a["question"],
                    "value": next((o["label"] for o in a["options"] if o["value"] == a["answer"]), a["answer"]),
                    "options": a["options"], "answer": a["answer"]} for a in ambiguities if a.get("auto")]
    questions = [a for a in ambiguities if not a["answer"]]
    return {
        "card": card, "assumptions": assumptions, "questions": questions,
        "text_hash": hashlib.sha1(text.encode()).hexdigest()[:12],
        "instrument": sym, "direction": direction,
        "definitions": definitions, "conditions": conds, "sequence": "any" if re.search(r"\bin any order\b", low) else "ordered",
        "direction_rule": "same", "entries": entries, "stops": stops, "exits": exits, "settings": settings,
        "roles": roles, "recipe": recipe, "timeline": timeline, "ambiguities": ambiguities, "unsupported": unsupported,
        "missing": missing, "notes": notes, "personal_used": used_personal,
        "status": {"checks": checks, "open_questions": len(open_q), "open_unsupported": len(open_u), "ready": ready},
    }


LEVEL_NAMES = {"pdl": "previous-day low", "pdh": "previous-day high", "onl": "overnight low", "onh": "overnight high",
               "pwl": "previous-week low", "pwh": "previous-week high", "sessl": "session low", "sessh": "session high",
               "eql": "equal lows", "eqh": "equal highs", "swingl": "swing low", "swingh": "swing high",
               "lonl": "London low", "lonh": "London high", "asial": "Asia low", "asiah": "Asia high",
               "nyaml": "NY-morning low", "nyamh": "NY-morning high"}
_FVG_REQ_WORDS = {"VALIDATED": "validates", "CREATED (event)": "forms", "ENTERED": "is entered", "ACTIVE": "(active)",
                  "not INVALIDATED": "(not invalidated)", "HALF_FILLED": "is half filled", "exists (CREATED)": "exists",
                  "PARTIALLY_FILLED": "is partly filled"}


def plain(d, c):
    """A short, human description of one condition (the interpretation card's wording)."""
    p, concept = d["params"], d["concept"]
    raw_tf = p.get("timeframe") or ""
    tf = raw_tf if raw_tf.endswith("m") else raw_tf.upper()
    dir_ = (p.get("direction") or "").replace("either", "")
    cap = lambda x: x[:1].upper() + x[1:] if x else x
    if concept == "fvg":
        return f"{tf} {dir_} FVG {_FVG_REQ_WORDS.get(c['requirement'], c['requirement'].lower())}".replace("  ", " ").strip()
    if concept == "liquidity_sweep":
        levels = p.get("levels") or []
        if levels:
            names = [LEVEL_NAMES.get(k, k) for k in levels]
            return (" or ".join(names) + " swept")[:1].upper() + (" or ".join(names) + " swept")[1:]
        return f"{cap(p.get('side', ''))} liquidity swept"
    if concept == "smt_divergence":
        return f"{cap(dir_)} SMT vs {p.get('compare_with')}".strip()
    if concept == "displacement":
        return f"{cap(dir_) or 'A'} displacement".strip() + (f" ({tf})" if tf else "")
    if concept == "market_structure_break":
        kind = {"any": "structure break", "bos": "BOS", "choch": "CHoCH", "mss": "MSS"}[p.get("type", "any")]
        return f"{cap(dir_)} {kind}".strip() + (f" ({tf})" if tf else "")
    if concept == "unsupported":
        return f"{p.get('label')} — not measurable yet"
    if c["requirement"].startswith("price"):
        return f"Price {c['requirement'][6:].replace(' it', '')} the {d['name'].lower()}"
    return d["name"]


def _card(sym, direction, definitions, conds, entries, stops, exits, settings, session_key):
    from research_projects import idea_text
    pairs = list(zip(definitions, conds))
    item = lambda d, c: {"text": plain(d, c), "importance": c.get("importance"), "untestable": d["concept"] == "unsupported",
                         "letter": c["letter"]}
    section = lambda title, roles: {"title": title, "items": [item(d, c) for d, c in pairs if c["role"] in roles]}
    w = settings.get("session")
    session = []
    if w:
        label = C.SESSIONS.get(w.get("session"), {}).get("label") if w.get("session") != "custom" else None
        session.append(label + f" ({w['start']}–{w['end']} ET)" if label else f"Entries {w['start']}–{w['end']} ET")
    if session_key and w and w.get("session") == "custom":
        session.insert(0, C.SESSIONS[session_key]["label"])
    if settings.get("flat_time"):
        session.append(f"Flat by {settings['flat_time']} ET")
    if settings.get("max_holding_minutes"):
        session.append(f"Hold at most {settings['max_holding_minutes']} minutes")
    return {"headline": f"{sym} {({'long': 'LONG', 'short': 'SHORT', 'both': 'LONG / SHORT'}).get(direction, '?')}",
            "sections": [s for s in [
                section("Context", ("context",)), section("Prerequisites", ("prerequisite",)),
                section("Confirmation", ("setup", "filter", "confirmation")),
                {"title": "Entry", "items": [{"text": idea_text("entries", e), "importance": "execution"} for e in entries]},
                {"title": "Stop", "items": [{"text": idea_text("stops", x), "importance": "execution"} for x in stops]},
                {"title": "Exit", "items": [{"text": idea_text("exits", x), "importance": "execution"} for x in exits]},
                {"title": "Session", "items": [{"text": t, "importance": "execution"} for t in session]},
            ] if s["items"] or s["title"] in ("Entry", "Stop", "Exit")]}


def _known_text(d, c):
    concept = d["concept"]
    tf = d["params"].get("timeframe")
    return {"fvg": f"known at the {tf} candle close where it is {c['requirement'].lower()}",
            "liquidity_sweep": f"confirmed at the {tf} close back through the level",
            "displacement": f"known at the close of the last {tf} candle of the move",
            "market_structure_break": f"known at the breaking {tf} candle's close (swings only once confirmed)"}.get(concept, "known at the candle close")


def _default_requirement(concept):
    from research_projects import REQUIREMENTS
    return REQUIREMENTS.get(concept, REQUIREMENTS["_default"])[0]


def _personal_for(personal, concept, low):
    """A saved definition named in the text ("my displacement") wins; otherwise the newest one set to auto-apply."""
    named = [p for p in personal if p.get("concept") == concept and p.get("name") and p["name"].lower() in low]
    if named:
        return named[-1]
    auto = [p for p in personal if p.get("concept") == concept and p.get("auto", True)]
    return auto[-1] if auto else None


VALIDATION_OPTIONS = [
    ("close_beyond_mid", "A candle closes beyond the FVG's midpoint (in the trade's direction)"),
    ("close_beyond", "A candle closes beyond the entire FVG"),
    ("reaction_candle", "A candle in the trade's direction closes outside the FVG after entering it"),
    ("hold_midpoint", "No close through the midpoint, then a close beyond the FVG"),
]


def _fvg_requirement(text, spans, x, ask, answers, params):
    a, b = _sentence_of(spans, x["start"])
    after = text[x["end"]:b].lower()[:140]
    before = text[a:x["start"]].lower()[-60:]
    if x["role"] == "context":
        if re.search(r"\b(?:fresh|unmitigated|untested|unfilled|active)\b", before + " " + after):
            return "ACTIVE"
        if re.search(r"\b(?:inside|within|trading (?:in|into)|tapped|in the)\b", after[:40]):
            return "ENTERED"
        choice = ask(f"ctx:{_uid(x['start'])}", "Context condition", f"What must be true of the “{x['phrase']}” ({x.get('tf')}) when you enter?",
                     [{"value": "ACTIVE", "label": "It exists and isn't filled, invalidated or expired (ACTIVE)"},
                      {"value": "not INVALIDATED", "label": "It exists and hasn't been invalidated (filling is fine)"},
                      {"value": "ENTERED", "label": "Price has already traded into it"},
                      {"value": "VALIDATED", "label": "It has been validated"}],
                     phrase=x["phrase"], suggested="ACTIVE", why="Each reading finds a different set of trades.")
        return choice or "ACTIVE"
    rule = None
    if re.search(r"close[sd]?\s+(?:back\s+)?(?:above|below|through|beyond)\s+(?:the\s+)?(?:midpoint|50|ce|consequent|middle)", after):
        rule = "close_beyond_mid"
    elif re.search(r"close[sd]?\s+(?:back\s+)?(?:above|below|out of|beyond)\s+(?:it|the (?:fvg|gap|top|bottom|whole|entire))", after):
        rule = "close_beyond"
    elif re.search(r"(?:bullish|bearish) (?:candle|close|reaction)|engulf", after):
        rule = "reaction_candle"
    if rule or re.search(r"valid|reject|respect|hold|bounce|react|confirm", after):
        if not rule:
            word = re.search(r"valid\w*|reject\w*|respect\w*|hold\w*|bounc\w*|react\w*|confirm\w*", after).group(0)
            choice = ask(f"validation:{_uid(x['start'])}", "What counts as rejection / validation",
                         f"“{x['phrase']}” … “{word}”: what exactly must happen?",
                         [{"value": v, "label": l} for v, l in VALIDATION_OPTIONS], phrase=x["phrase"], suggested="close_beyond_mid",
                         why="Rejection has no single definition; each rule finds different entries.")
            rule = choice
        if rule:
            params["validation_rule"] = rule
        return "VALIDATED"
    if re.search(r"\b(?:enter(?:s|ed)?|tap(?:s|ped)?|touch(?:es|ed)?|trades? into|retrace(?:s|d)? into|fills?|into it)\b", after):
        return "ENTERED"
    if re.search(r"\bhalf|50\s*%|\bce\b", after):
        return "HALF_FILLED"
    if re.search(r"\b(?:form(?:s|ed)?|creat\w*|print(?:s|ed)?|appear\w*|leaves?|left)\b", after + " " + before):
        return "CREATED (event)"
    choice = ask(f"fvgreq:{_uid(x['start'])}", "FVG event", f"What must the “{x['phrase']}” ({x.get('tf')}) do?",
                 [{"value": "CREATED (event)", "label": "Just form (at candle 3's close)"},
                  {"value": "ENTERED", "label": "Price trades back into it"},
                  {"value": "VALIDATED", "label": "Price enters it and then validates it (choose the rule in the definition)"}],
                 phrase=x["phrase"], suggested="CREATED (event)", why="The description doesn't say what the FVG must do.")
    return choice or "CREATED (event)"


def _lookback(text, x, ask, answers, low):
    m = re.search(r"\bwithin (?:the (?:last|past) )?(\d+)\s*(min(?:ute)?s?|m\b|hours?|h\b|candles?|bars?)", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        minutes = n * 60 if unit.startswith("h") else n * 5 if unit.startswith(("candle", "bar")) else n
        return "within the last N minutes", minutes
    if re.search(r"\bsame (?:session|day)\b|\btoday\b|\bthis session\b", low):
        return "any time earlier in the same session", None
    if re.search(r"\bsince (?:the )?(?:globex )?open\b|since 18:00|overnight", low):
        return "since 18:00 ET", None
    choice = ask("lookback", "How long before entry",
                 "How long before entry may the earlier steps (sweep, displacement, structure…) have happened?",
                 [{"value": "session", "label": "Any time earlier in the same trading session"},
                  {"value": "60", "label": "Within the last 60 minutes"},
                  {"value": "120", "label": "Within the last 2 hours"},
                  {"value": "30", "label": "Within the last 30 minutes"}],
                 suggested="session", why="The description gives the order but not how far apart the steps may be.")
    if choice in (None, "session"):
        return "any time earlier in the same session", None
    return "within the last N minutes", int(choice)


def _idea(kind, params=None, letter="A", text=""):
    return {"id": f"i_{_uid(kind, params, letter)}", "letter": letter, "kind": kind, "params": params or {}, "text": text}


def _entry(text, conds, definitions):
    low = text.lower()
    has_fvg = any(d["concept"] == "fvg" for d in definitions)
    ce = re.search(r"(?:retrac\w*|pull\w* back|limit|enter\w*|tap\w*)[^.;]{0,40}(?:50\s*%|\bhalf\b|midpoint|\bce\b|consequent encroachment)"
                   r"|(?:50\s*%|midpoint|\bce\b)[^.;]{0,30}(?:of (?:the |its )?)?(?:fvg|gap|imbalance)|consequent encroachment|middle of the (?:fvg|gap)", low)
    if has_fvg and ce:
        return _idea("fvg_50")
    if has_fvg and re.search(r"\b(?:first touch|on the (?:retest|tap|touch)|at the (?:fvg|gap)|limit at the (?:top|bottom|edge))\b", low):
        return _idea("fvg_first_touch")
    if re.search(r"\bbreak of (?:the )?(?:candle|validation candle)'?s? (?:high|low)\b|\bstop order above\b|\bbuy stop\b|\bsell stop\b", low):
        return _idea("break_validation")
    if re.search(r"\benter|\bentry\b|\bgo (?:long|short)\b|\bbuy\b|\bsell\b|\btake (?:the|a) (?:trade|long|short)\b|\blong\b|\bshort\b", low):
        return _idea("validation_close")
    return None


def _stop(text, conds, definitions, ask, bias):
    low = text.lower()
    m = re.search(r"\b(?:stop(?:[\s-]*loss)?|sl|invalidat\w*)\b(?!\s+(?:trading|taking))[^.;,]{0,90}", low)
    seg = m.group(0) if m else ""
    concepts = {d["concept"] for d in definitions}
    if seg:
        pts = re.search(r"(\d+(?:\.\d+)?)\s*(?:points?|pts|handles?)", seg)
        ticks = re.search(r"(\d+)\s*ticks?", seg)
        atr = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|×)?\s*atr", seg)
        if re.search(r"sweep|wick|swept|liquidity|raid|stop[\s-]*run", seg) and "liquidity_sweep" in concepts:
            return _idea("sweep_extreme"), False
        if re.search(r"displacement", seg) and "displacement" in concepts:
            return _idea("displacement_extreme"), False
        if re.search(r"\b(?:mss|bos|choch|structure|break(?:ing)? candle)\b", seg) and "market_structure_break" in concepts:
            return _idea("structure_extreme"), False
        if re.search(r"\b(?:fvg|gap|imbalance)\b", seg) and "fvg" in concepts:
            return _idea("fvg_invalidation"), False
        if re.search(r"swing (?:low|high)|structure (?:low|high)|recent (?:low|high)", seg):
            return _idea("swing_extreme"), False
        if pts:
            return _idea("fixed_points", {"points": float(pts.group(1))}), False
        if ticks:
            return _idea("fixed_points", {"points": int(ticks.group(1)) * 0.25}), False
        if atr:
            return _idea("atr", {"atr_mult": float(atr.group(1)), "atr_period": 14}), False
    options = []
    if "liquidity_sweep" in concepts:
        options.append({"value": "sweep_extreme", "label": "Beyond the sweep's extreme (wick)"})
    if "fvg" in concepts:
        options.append({"value": "fvg_invalidation", "label": "When the FVG is invalidated (its definition)"})
    if "displacement" in concepts:
        options.append({"value": "displacement_extreme", "label": "Beyond the displacement's extreme"})
    if "market_structure_break" in concepts:
        options.append({"value": "structure_extreme", "label": "Beyond the structure-break candle's extreme"})
    options += [{"value": "swing_extreme", "label": "Beyond the most recent confirmed swing"},
                {"value": "fixed_points:20", "label": "Fixed 20 points"}, {"value": "atr:1.5", "label": "1.5 × ATR(14)"}]
    choice = ask("stop", "Stop / invalidation", "The description doesn't say where the stop goes. Which stop should be tested?", options,
                 why="Results in R depend entirely on the stop.")
    if not choice:
        return None, True
    if choice.startswith("fixed_points:"):
        return _idea("fixed_points", {"points": float(choice.split(":")[1])}), False
    if choice.startswith("atr:"):
        return _idea("atr", {"atr_mult": float(choice.split(":")[1]), "atr_period": 14}), False
    return _idea(choice), False


def _target(text, ask, bias, level_hits):
    low = text.lower()
    scale = re.search(r"\b(?:take|close|scale(?: out)?|book|bank|sell|cover)\s+(?:off\s+)?(\d{1,2})\s*%\s*(?:of (?:the |my )?(?:position|size)\s*)?(?:at|@)\s*(\d+(?:\.\d+)?)\s*r\b", low) or \
        re.search(r"\b(?:half|partials?)\s+(?:off\s+)?(?:at|@)\s*(\d+(?:\.\d+)?)\s*r\b", low)
    if scale:
        fraction = int(scale.group(1)) / 100 if scale.lastindex and scale.lastindex >= 2 else 0.5
        first_r = float(scale.group(2) if scale.lastindex and scale.lastindex >= 2 else scale.group(1))
        rest_text = low[scale.end():scale.end() + 160]
        rest_r = re.search(r"(\d+(?:\.\d+)?)\s*r\b", rest_text)
        params = {"fraction": fraction, "r": first_r}
        if rest_r and not re.search(r"liquidity|\bbsl\b|\bssl\b|high|low", rest_text[:rest_r.start()]):
            params.update(rest="r", rest_r=float(rest_r.group(1)))
        else:
            named = [h["kind"] for h in level_hits if h["context_kind"] == "target" and h["start"] > scale.end() - 5]
            if named:
                params.update(rest="liquidity", pools=sorted({POOL_OF_LEVEL[k] for k in named}))
            elif re.search(r"liquidity|\bbsl\b|\bssl\b|highs|lows|draw", rest_text):
                choice = ask("target_pools", "Which liquidity is the final target",
                             "For the rest of the position: which levels count as the target liquidity? (The nearest one beyond entry is used.)",
                             _POOL_OPTIONS, phrase=rest_text.strip()[:80], suggested="pdhl,onhl,sessions",
                             why="‘Major liquidity’ depends on which levels count, and it decides where most of the profit is taken.")
                params["rest"] = "liquidity"
                if choice:
                    params["pools"] = choice.split(",")
            else:
                params.update(rest="r", rest_r=max(first_r * 2, first_r + 1))
                return _idea("scale_out", params), False
        return _idea("scale_out", params), False
    m = re.search(r"\b(?:target(?:ing)?|tp|take[\s-]*profits?|aim(?:ing)? for|objective|exit (?:at|when|after)|draw on liquidity)\b[^.;,]{0,100}", low)
    seg = m.group(0) if m else ""
    rr = re.search(r"(?<![\d:.])(\d+(?:\.\d+)?)\s*(?:r\b|rr\b|r:r\b|x (?:my )?risk|times (?:my )?risk)|(?<![\d:])1\s*:\s*([1-9](?:\.\d+)?)\b(?!\s*(?:am|pm|et)\b)", seg or low)
    if rr:
        return _idea("r_multiple", {"r": float(rr.group(1) or rr.group(2))}), False
    if seg:
        pts = re.search(r"(\d+(?:\.\d+)?)\s*(?:points?|pts|handles?)", seg)
        mins = re.search(r"(\d+)\s*(?:min(?:ute)?s?|m\b)", seg)
        if pts:
            return _idea("fixed_points", {"points": float(pts.group(1))}), False
        if re.search(r"opposite (?:end|edge|side)|other (?:end|side)|(?:top|bottom) of the (?:1h|4h|htf|hourly|higher)", seg):
            return _idea("structure", {"target": "opposite edge of the higher-timeframe FVG"}), False
        named = [h["kind"] for h in level_hits if h["context_kind"] == "target"]
        if named:
            pools = sorted({POOL_OF_LEVEL[k] for k in named})
            return _idea("structure", {"target": "nearest opposing liquidity", "pools": pools}), False
        if re.search(r"liquidity|\bbsl\b|\bssl\b|highs|lows|draw", seg):
            choice = ask("target_pools", "Which liquidity is the target",
                         f"“{seg.strip()[:80]}”: which levels count as the target liquidity? (The nearest one beyond entry is used.)",
                         _POOL_OPTIONS,
                         phrase=seg.strip()[:80], suggested="pdhl,onhl,sessions", why="‘Nearest major liquidity’ depends on which levels count.")
            if not choice:
                return None, True
            return _idea("structure", {"target": "nearest opposing liquidity", "pools": choice.split(",")}), False
        if mins:
            return _idea("time", {"minutes": float(mins.group(1))}), False
    choice = ask("target", "Target / exit", "The description doesn't say how the trade is closed in profit. Which exit should be tested?",
                 [{"value": "r:2", "label": "2R"}, {"value": "r:1", "label": "1R"},
                  {"value": "structure", "label": "Nearest opposing liquidity (previous-day / overnight levels)"},
                  {"value": "time:60", "label": "Exit after 60 minutes"}], why="Without an exit nothing can be measured.")
    if not choice:
        return None, True
    if choice.startswith("r:"):
        return _idea("r_multiple", {"r": float(choice[2:])}), False
    if choice.startswith("time:"):
        return _idea("time", {"minutes": float(choice[5:])}), False
    return _idea("structure", {"target": "nearest opposing liquidity", "pools": ["pdhl", "onhl"]}), False


_POOL_OPTIONS = [{"value": "pdhl,onhl", "label": "Previous-day and overnight highs/lows"},
                 {"value": "pdhl,onhl,sessions", "label": "…plus London / Asia / NY-morning session highs/lows"},
                 {"value": "pdhl,onhl,session,swing", "label": "…plus the running session high/low and same-day swings"},
                 {"value": "pdhl,onhl,pwhl,sessions,session,swing,eqhl", "label": "All enabled liquidity (incl. previous week, equal highs/lows)"},
                 {"value": "swing", "label": "Same-day swing highs/lows only"}]


def _nearest_ob(bias, tf):
    p = {**C.default_params("displacement"), "timeframe": tf, "require_fvg": True}
    if bias:
        p["direction"] = bias
    return ({"concept": "displacement", "name": f"{tf} displacement leaving an FVG (order-block proxy)", "params": p, "source": "nearest", "phrase": "order block"},
            {"requirement": "occurs", "timing": "any time earlier in the same session", "within": None, "role": "setup", "phrase": "order block"})


def _nearest_breaker(bias, tf):
    p = {**C.default_params("market_structure_break"), "timeframe": tf, "type": "choch"}
    if bias:
        p["direction"] = bias
    return ({"concept": "market_structure_break", "name": f"{tf} CHoCH (breaker proxy)", "params": p, "source": "nearest", "phrase": "breaker"},
            {"requirement": "confirmed break", "timing": "any time earlier in the same session", "within": None, "role": "setup", "phrase": "breaker"})


NEAREST_BUILD = {"Order block": _nearest_ob, "Breaker block": _nearest_breaker}
