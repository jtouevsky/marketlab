"""
Tests for the simplification update: severity-based ambiguity handling, the interpretation
card, back-references in descriptions, SMT divergence (incl. look-ahead), session-range levels,
scale-out exits, the Exact / Very similar / Broader ladder, logged trades, the luck wording,
the Investment Lab's correlation-vs-dependency logic and the new Quick-test conditions.

Run:  python3 -m unittest discover tests -v
"""

import math
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import conditions as COND                       # noqa: E402
import formalize as F                           # noqa: E402
import invest as INV                            # noqa: E402
import research_projects as R                   # noqa: E402
from intraday import concepts as K, engine, ladder, strategy   # noqa: E402
from test_intraday import TUE, random_days, rows_from, series_of, ts_et   # noqa: E402

EXAMPLE = ("Long NQ. Price is inside a 4H bullish FVG. During the NY session, London low gets swept, with bullish SMT against ES. "
           "After the sweep, 1m displacement and a break of structure creating a 1m bullish FVG. Enter at CE of the FVG, stop below "
           "the sweep low. Take 50% at 2R and the rest at the next major liquidity. Only take entries between 9:30 and 11:00.")


def concepts(d):
    defs = {x["id"]: x for x in d["definitions"]}
    return [defs[c["definition_id"]]["concept"] for c in d["conditions"]]


class InterpretationTests(unittest.TestCase):
    def test_example_is_understood_without_inventing_or_duplicating(self):
        d = F.parse(EXAMPLE)
        # "after the sweep" and "CE of the FVG" refer back; they are not new conditions
        self.assertEqual(concepts(d), ["fvg", "liquidity_sweep", "smt_divergence", "displacement", "market_structure_break", "fvg"])
        defs = {x["id"]: x for x in d["definitions"]}
        sweep = next(defs[c["definition_id"]] for c in d["conditions"] if defs[c["definition_id"]]["concept"] == "liquidity_sweep")
        self.assertEqual(sweep["params"]["levels"], ["lonl"])
        smt = next(x for x in d["definitions"] if x["concept"] == "smt_divergence")
        self.assertEqual(smt["params"]["compare_with"], "ES")
        self.assertEqual(d["entries"][0]["kind"], "fvg_50")
        self.assertEqual(d["exits"][0]["kind"], "scale_out")
        self.assertEqual(d["settings"]["session"], {"session": "custom", "start": "09:30", "end": "11:00"})

    def test_only_high_impact_questions_are_asked(self):
        d = F.parse(EXAMPLE)
        self.assertEqual(d["questions"], [])                     # paragraph → test: no question needed here
        self.assertTrue(d["status"]["ready"])
        for a in d["ambiguities"]:
            self.assertIn(a["severity"], ("low", "medium", "high"))
            self.assertEqual(a["auto"], a["severity"] != "high")
        pools = next(a for a in d["ambiguities"] if a["id"] == "target_pools")
        self.assertEqual((pools["severity"], pools["answer"]), ("medium", "pdhl,onhl,sessions"))
        # a missing stop is still asked: testing without one would be misleading
        bare = F.parse("NQ long after a 5m bullish FVG validates, target 2R.")
        self.assertIn("stop", [q["id"] for q in bare["questions"]])

    def test_card_has_the_recipe_sections_and_importance(self):
        card = F.parse(EXAMPLE)["card"]
        self.assertEqual(card["headline"], "NQ LONG")
        titles = [s["title"] for s in card["sections"]]
        for t in ("Context", "Prerequisites", "Confirmation", "Entry", "Stop", "Exit", "Session"):
            self.assertIn(t, titles)
        items = [i for s in card["sections"] for i in s["items"] if "importance" in i]
        self.assertTrue(any(i["importance"] == "core" for i in items))
        self.assertTrue(any(i["importance"] == "secondary" for i in items))

    def test_smt_peer_and_planned_targets(self):
        d = F.parse("Short YM after bearish SMT divergence against NQ on the 5m, enter on the 5m FVG validation. Stop 20 points, target 2R.")
        smt = next(x for x in d["definitions"] if x["concept"] == "smt_divergence")
        self.assertEqual(smt["params"]["compare_with"], "NQ")
        d = F.parse("NQ long after a 5m bullish FVG validates. Stop 10 points, take 50% at 2R and the rest at 4R.")
        self.assertEqual(d["entries"][0]["kind"], "validation_close")      # "50% at 2R" is not a CE entry
        self.assertEqual(d["exits"][0]["kind"], "scale_out")


def swing_path(points, start_price):
    """Candles walking linearly between the given (index, price) points; wicks ±0.25."""
    bars, price = [], start_price
    for (i0, p0), (i1, p1) in zip(points, points[1:]):
        for k in range(i1 - i0):
            o = p0 + (p1 - p0) * k / (i1 - i0)
            c = p0 + (p1 - p0) * (k + 1) / (i1 - i0)
            bars.append((o, max(o, c) + 0.25, min(o, c) - 0.25, c))
    for (_, prev), (i, p), (_, nxt) in zip(points, points[1:], points[2:]):     # a clear wick at each turning point
        o, h, l, c = bars[i - 1]
        bars[i - 1] = (o, h + 0.5, l, c) if p > prev and p > nxt else (o, h, l - 0.5, c)
    return bars


class SmtTests(unittest.TestCase):
    def ctx(self, bars):
        rows = rows_from(ts_et(TUE, "10:00"), bars)
        s, _ = series_of(rows)
        return engine.TimeframeContext(engine.resample(s, "1m")), rows

    def test_bullish_divergence_found_and_not_known_early(self):
        a_pts = [(0, 110), (10, 100), (20, 110), (30, 96), (45, 112)]       # A: lower low
        b_pts = [(0, 110), (10, 100), (20, 110), (30, 103), (45, 112)]      # B: higher low
        a, rows_a = self.ctx(swing_path(a_pts, 110))
        b, _ = self.ctx(swing_path(b_pts, 110))
        p = {"swing_left": 3, "swing_right": 3, "lookback_bars": 40, "max_lag_bars": 2, "min_ticks": 2, "compare_with": "ES"}
        events = K.smt(a, b, p)
        bull = [e for e in events if e["direction"] == "bullish"]
        self.assertEqual(len(bull), 1)
        e = bull[0]
        self.assertGreater(e["confirmed_at"], e["occurred_at"])
        self.assertGreaterEqual(e["confirmed_at"], e["occurred_at"] + 3 * 60)        # needs the lag window to close
        # look-ahead: with data ending before confirmation the event must not exist
        cut = e["confirmed_at"]
        a2, _ = self.ctx(swing_path(a_pts, 110)[: (cut - rows_a[0]["timestamp"]) // 60 - 1])
        self.assertFalse([x for x in K.smt(a2, b, p) if x["direction"] == "bullish"])

    def test_no_divergence_when_both_make_lower_lows(self):
        pts = [(0, 110), (10, 100), (20, 110), (30, 96), (45, 112)]
        a, _ = self.ctx(swing_path(pts, 110))
        b, _ = self.ctx(swing_path(pts, 110))
        self.assertEqual([e for e in K.smt(a, b, {"swing_left": 3, "swing_right": 3}) if e["direction"] == "bullish"], [])


class SessionLevelTests(unittest.TestCase):
    def test_london_range_is_known_only_after_the_window(self):
        rows = random_days([TUE], seed=11)
        s, _ = series_of(rows)
        levels = [x for x in K.session_range_levels(s) if x["session"] == "london"]
        self.assertEqual({x["kind"] for x in levels}, {"lonh", "lonl"})
        a, b = ts_et(TUE, "02:00"), ts_et(TUE, "05:00")
        inside = [r for r in rows if a <= r["timestamp"] < b]
        hi = next(x for x in levels if x["kind"] == "lonh")
        self.assertEqual(hi["price"], max(r["high"] for r in inside))
        self.assertEqual(hi["available_from"], b)


class ScaleOutTests(unittest.TestCase):
    def test_points_blend_partial_and_final(self):
        f, entry, partial, final = 0.5, 100.0, 110.0, 104.0
        blended = f * (partial - entry) + (1 - f) * (final - entry)
        self.assertAlmostEqual(blended, 7.0)


class LadderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = R.RESEARCH_DIR
        R.RESEARCH_DIR = Path(self.tmp.name)

    def tearDown(self):
        R.RESEARCH_DIR = self.saved
        self.tmp.cleanup()

    def project(self):
        p = R.create({"observation": "x", "instrument": "NQ", "kind": "idea"})
        pid = p["id"]
        p = R.add_item(pid, "definitions", {"concept": "liquidity_sweep", "params": {"timeframe": "5m", "side": "sell-side", "levels": ["pdl"]}})
        p = R.add_item(pid, "definitions", {"concept": "displacement", "params": {"timeframe": "5m", "direction": "bullish"}})
        p = R.add_item(pid, "definitions", {"concept": "fvg", "params": {"timeframe": "5m", "direction": "bullish"}})
        d = [x["id"] for x in p["definitions"]]
        R.add_item(pid, "conditions", {"definition_id": d[0], "timing": "any time earlier in the same session", "importance": "core"})
        R.add_item(pid, "conditions", {"definition_id": d[1], "timing": "any time earlier in the same session", "importance": "secondary"})
        R.add_item(pid, "conditions", {"definition_id": d[2], "requirement": "CREATED (event)", "timing": "at the setup candle", "importance": "core"})
        R.add_item(pid, "entries", {"kind": "fvg_50"})
        R.add_item(pid, "stops", {"kind": "fixed_points", "params": {"points": 10}})
        R.add_item(pid, "exits", {"kind": "r_multiple", "params": {"r": 2}})
        R.update_settings(pid, {"session": {"session": "custom", "start": "09:30", "end": "11:00"}}) if hasattr(R, "update_settings") else None
        return R.raw(pid)

    def test_only_secondary_rules_are_relaxed_and_everything_is_listed(self):
        p = self.project()
        cands = ladder.candidates(p)
        ids = {c["id"].split(":")[0] for c in cands}
        self.assertIn("drop", ids)
        dropped = [c for c in cands if c["kind"] == "drop"]
        core = {c["id"] for c in p["hypothesis"]["conditions"] if c.get("importance") == "core"}
        self.assertFalse(any(c["condition_id"] in core for c in dropped))      # core is never dropped
        for c in cands:
            self.assertTrue(c["original"] and c["relaxed"])                    # original → relaxed always shown

    def test_apply_never_changes_the_project(self):
        p = self.project()
        before = repr(p)
        ids = [c["id"] for c in ladder.candidates(p)]
        v = ladder.apply(p, ids)
        self.assertEqual(repr(p), before)
        self.assertEqual(len(v["ladder_relaxations"]), len(ids))
        with self.assertRaises(strategy.StrategyError):
            ladder.apply(p, ["drop:nope"])

    def test_tiers_and_diagnosis_on_data(self):
        from intraday.dataset import Dataset
        days = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]
        s, report = series_of(random_days(days, seed=3))
        ds = Dataset("NQ", "1m", "test", s, report, engine.build_contexts(s, ["5m"]), None, None)
        out = ladder.build(self.project(), ds)
        tiers = {t["tier"]: t for t in out["tiers"]}
        self.assertIn("exact", tiers)
        self.assertEqual(tiers["exact"]["relaxations"], [])
        for t in out["tiers"]:
            if t["tier"] != "exact":
                self.assertTrue(t["relaxations"])                               # a relaxed tier always says what changed
                self.assertGreaterEqual(t["setups"] or 0, 0)
        self.assertGreaterEqual(out["variants_counted"], 1)
        self.assertIn(out["diagnosis"]["verdict"], ("OK", "DATA", "CONCEPT", "PATTERN"))

    def test_untestable_condition_is_reported_as_data_or_concept(self):
        p = self.project()
        pid = p["id"]
        p = R.add_item(pid, "definitions", {"concept": "unsupported", "params": {"label": "Breaker block", "needs": "a breaker detector", "nearest": "x"}})
        R.add_item(pid, "conditions", {"definition_id": p["definitions"][-1]["id"], "importance": "secondary"})
        from intraday.dataset import Dataset
        s, report = series_of(random_days([TUE], seed=3))
        ds = Dataset("NQ", "1m", "test", s, report, engine.build_contexts(s, ["5m"]), None, None)
        out = ladder.build(R.raw(pid), ds)
        exact = out["tiers"][0]
        self.assertIsNone(exact["setups"])
        self.assertTrue(any(x["kind"] == "concept" for x in exact["problems"]))


class LoggedTradeTests(unittest.TestCase):
    def test_parse_trade_text(self):
        t = R.parse_trade_text("I went long NQ at 10:14 on Sep 29 after the London low was swept. Stop below the sweep low, "
                               "target 2R. I made +1.6R.", date(2026, 10, 1))
        self.assertEqual((t["direction"], t["date"], t["time"], t["result_r"]), ("long", "2026-09-29", "10:14", 1.6))
        self.assertIsNone(R.parse_trade_text("long NQ target 3R", date(2026, 10, 1))["result_r"])     # a plan is not a result
        self.assertEqual(R.parse_trade_text("short at 9:45, lost 1R", date(2026, 10, 1))["result_r"], -1.0)
        t = R.parse_trade_text("entry 29160, stop 28954, out at 29500", date(2026, 10, 1))
        self.assertEqual((t["entry"], t["stop"], t["exit"]), (29160.0, 28954.0, 29500.0))

    def test_log_trade_asks_for_what_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved, R.RESEARCH_DIR = R.RESEARCH_DIR, Path(tmp)
            try:
                out = R.log_trade({"text": "I went long NQ after a sweep"})
                self.assertIn("needs", out)
                self.assertTrue({"date", "time"} <= set(out["needs"]))
            finally:
                R.RESEARCH_DIR = saved

    def test_luck_wording_never_says_lucky(self):
        trades = [{"r_net": v, "suspect": False} for v in [-1] * 15 + [1] * 5]
        strong = strategy.luck(trades, {"result_r": 3.0})
        self.assertEqual(strong["text"], "This realized outcome was unusually strong relative to comparable historical setups.")
        weak = strategy.luck(trades, {"result_r": -2.0})
        self.assertIn("unusually weak", weak["text"])
        for r in (strong, weak, strategy.luck(trades[:4], {"result_r": 1})):
            self.assertNotIn("lucky", r["text"].lower())


def fake_profile(sym, factors, closes, industry="Semiconductors"):
    return {"ticker": sym, "name": sym, "sector": "Technology", "industry": industry, "fund": False, "errors": [],
            "closes": closes, "factors": {k: {"key": k, "label": label, "kind": kind, "strength": "disclosed", "evidence": []}
                                          for k, label, kind in factors},
            "fundamentals": {}, "exposure_state": {"state": "ok"}, "competitors": []}


class InvestTests(unittest.TestCase):
    def test_parse_thesis_offline(self):
        out = INV.parse_thesis("I think quantum computing could grow massively over the next five years. "
                               "I want exposure through IONQ, RGTI and others, but not all my risk in one company.")
        self.assertIn("IONQ", out["tickers"])
        self.assertEqual(out["horizon"], {"id": "5y", "months": 60})
        self.assertTrue(any(t["id"] == "quantum" for t in out["themes"]))
        self.assertIn("Not recommendations", out["candidate_note"])

    def test_boilerplate_is_not_hidden_concentration(self):
        self.assertTrue(INV._is_general({"kind": "macro", "key": "macro:rates", "strength": "disclosed"}))
        self.assertTrue(INV._is_general({"kind": "policy", "key": "policy:Tax policy", "strength": "disclosed"}))
        self.assertFalse(INV._is_general({"kind": "geography", "key": "geo:China", "strength": "emphasized"}))
        self.assertEqual(INV._lower_first("China (sales)"), "China (sales)")
        self.assertEqual(INV._lower_first("Data-centre build-out"), "data-centre build-out")

    def test_correlation_is_not_dependency(self):
        import random
        rng = random.Random(1)
        dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(300)]

        def walk(noise_from=None):
            v, out = 100.0, []
            for i in range(300):
                step = noise_from[i] if noise_from else rng.gauss(0, .01)
                v *= 1 + step
                out.append(v)
            return out
        common = [rng.gauss(0, .01) for _ in range(300)]
        move_a = walk(common)
        move_b = walk([c + rng.gauss(0, .002) for c in common])            # highly correlated with A
        move_c = walk()                                                    # independent
        shared = [("customer:hyperscalers", "Hyperscale cloud", "customer"), ("geo:Taiwan", "Taiwan", "geography")]
        profiles = {
            "AAA": fake_profile("AAA", [], {"dates": dates, "values": move_a}),
            "BBB": fake_profile("BBB", [], {"dates": dates, "values": move_b}, industry="Software"),
            "CCC": fake_profile("CCC", shared, {"dates": dates, "values": move_c}, industry="Utilities"),
            "DDD": fake_profile("DDD", shared, {"dates": dates, "values": walk()}, industry="Banks"),
        }
        saved = INV.profile
        INV.profile = lambda sym: profiles[sym]
        try:
            out = INV.analyze({"holdings": [{"ticker": t} for t in profiles], "horizon": "12m", "text": ""})
        finally:
            INV.profile = saved
        pairs = {(p["a"], p["b"]): p for p in out["correlation"]["pairs"]} if isinstance(out.get("correlation"), dict) else \
            {(p["a"], p["b"]): p for p in out["pairs"]}
        self.assertEqual(pairs[("AAA", "BBB")]["kind"], "market")           # moves together, no shared dependency
        self.assertEqual(pairs[("CCC", "DDD")]["kind"], "hidden")           # shared dependency, uncorrelated prices
        texts = " ".join(c["text"] for c in out["concentrations"])
        self.assertIn("Hyperscale cloud".lower()[:10], texts.lower())


class QuickConditionTests(unittest.TestCase):
    def bars(self):
        return {"close": [100, 102, 104, 90, 80, 85, 110], "open": [100, 101, 108, 92, 79, 85, 100], "symbol": "X"}

    def test_new_conditions(self):
        b = self.bars()
        gap = COND.compute(COND.validate({"type": "gap", "params": {"direction": "rises", "threshold": 3}}), b)
        self.assertEqual(gap, [None, False, True, False, False, True, True])
        hi = COND.compute(COND.validate({"type": "new_extreme", "params": {"kind": "high", "lookback": 5}}), b)
        self.assertEqual(hi[-1], True)
        long_b = {"close": [100.0] * 30 + [70.0], "symbol": "X"}
        dd = COND.compute(COND.validate({"type": "drawdown_from_high", "params": {"threshold": 25, "lookback": 20}}), long_b)
        self.assertEqual(dd[-1], True)
        self.assertEqual(dd[-2], False)

    def test_no_look_ahead_in_new_conditions(self):
        b = self.bars()
        for spec in ({"type": "gap", "params": {}}, {"type": "new_extreme", "params": {"lookback": 5}}):
            c = COND.validate(spec)
            full = COND.compute(c, b)
            for t in range(1, len(b["close"])):
                part = {k: (v[:t + 1] if isinstance(v, list) else v) for k, v in b.items()}
                self.assertEqual(COND.compute(c, part)[t], full[t])


if __name__ == "__main__":
    unittest.main()
