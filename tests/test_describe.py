"""
Tests for Describe My Trade (formalize.py) and its project integration:
description → strategy, ambiguity detection, no invented conditions, readiness,
original-description immutability, re-parse safety, saved personal definitions,
variations / forks, the luck comparison, and compatibility with the existing backtester.

Run:  python3 -m unittest discover tests -v
"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import formalize as F                           # noqa: E402
import research_projects as R                   # noqa: E402
from intraday import engine, strategy          # noqa: E402
from test_intraday import random_days, series_of   # noqa: E402

SPEC = ("On NQ, I'm looking for a bullish 1H FVG. I want sell-side liquidity such as ONL or PDL swept first. "
        "During NY morning, after bullish displacement and a market structure break, if a 5m FVG forms with the 1H "
        "direction and price enters/rejects it, enter long. Stop below the sweep low, target nearest major buy-side "
        "liquidity, no entries after 11:30 ET.")


def concepts(draft):
    defs = {d["id"]: d for d in draft["definitions"]}
    return [(defs[c["definition_id"]]["concept"], c["role"]) for c in draft["conditions"]]


class ParserTests(unittest.TestCase):
    def test_spec_example_structure(self):
        d = F.parse(SPEC)
        self.assertEqual(d["instrument"], "NQ")
        self.assertEqual(d["direction"], "long")
        self.assertEqual(concepts(d), [("fvg", "context"), ("liquidity_sweep", "prerequisite"), ("displacement", "setup"),
                                       ("market_structure_break", "setup"), ("fvg", "confirmation")])
        defs = {x["id"]: x for x in d["definitions"]}
        sweep = defs[d["conditions"][1]["definition_id"]]["params"]
        self.assertEqual((sweep["side"], sorted(sweep["levels"])), ("sell-side", ["onl", "pdl"]))
        self.assertEqual(defs[d["conditions"][0]["definition_id"]]["params"]["timeframe"], "1h")
        self.assertEqual(defs[d["conditions"][-1]["definition_id"]]["params"]["timeframe"], "5m")
        self.assertEqual(d["conditions"][-1]["requirement"], "VALIDATED")
        self.assertEqual(d["settings"]["session"], {"session": "custom", "start": "09:30", "end": "11:30"})
        self.assertEqual(d["stops"][0]["kind"], "sweep_extreme")
        self.assertEqual(d["entries"][0]["kind"], "validation_close")
        self.assertEqual(d["roles"]["context"], "1h")

    def test_ambiguities_are_the_material_ones_only(self):
        d = F.parse(SPEC)
        ids = {a["id"].split(":")[0] for a in d["ambiguities"]}
        self.assertEqual(ids, {"tf_default", "ctx", "lookback", "validation", "target_pools"})
        self.assertTrue(d["status"]["ready"])          # nothing here is high-impact: defaults are used and listed
        for a in d["ambiguities"]:
            self.assertGreaterEqual(len(a["options"]), 2)
            # low / medium: best default applied and visibly marked; high: always asked, never guessed
            if a["severity"] == "high":
                self.assertIsNone(a["answer"])
                self.assertFalse(a["auto"])
            else:
                self.assertEqual(a["answer"], a["suggested"])
                self.assertTrue(a["auto"])
        self.assertEqual(d["questions"], [])
        manual = F.parse(SPEC, auto_resolve=False)
        self.assertTrue(all(a["answer"] is None for a in manual["ambiguities"]))

    def test_answers_make_it_ready_and_change_the_structure(self):
        d = F.parse(SPEC)
        answers = {a["id"]: a["suggested"] for a in d["ambiguities"]}
        ready = F.parse(SPEC, answers)
        self.assertTrue(ready["status"]["ready"])
        defs = {x["id"]: x for x in ready["definitions"]}
        self.assertEqual(defs[ready["conditions"][-1]["definition_id"]]["params"]["validation_rule"], "close_beyond_mid")
        other = dict(answers, **{next(a["id"] for a in d["ambiguities"] if a["id"].startswith("validation")): "reaction_candle"})
        alt = F.parse(SPEC, other)
        defs = {x["id"]: x for x in alt["definitions"]}
        self.assertEqual(defs[alt["conditions"][-1]["definition_id"]]["params"]["validation_rule"], "reaction_candle")

    def test_no_invented_conditions(self):
        d = F.parse("NQ long when a 5m bullish FVG is validated with a close above it. Stop 20 points, target 2R.")
        self.assertEqual(concepts(d), [("fvg", "confirmation")])
        self.assertIsNone(d["settings"]["session"])            # no time rule was written, none is added
        self.assertIsNone(d["settings"]["max_holding_minutes"])
        self.assertEqual(d["stops"][0], {**d["stops"][0], "kind": "fixed_points", "params": {"points": 20.0}})
        self.assertEqual(d["exits"][0]["params"], {"r": 2.0})
        self.assertTrue(d["status"]["ready"])

    def test_missing_stop_and_target_are_asked_not_guessed(self):
        d = F.parse("NQ: go long after a 5m bullish displacement.")
        ids = {a["id"] for a in d["ambiguities"]}
        self.assertTrue({"stop", "target"} <= ids)
        self.assertEqual(d["stops"], [])
        self.assertEqual(d["exits"], [])
        self.assertFalse(d["status"]["ready"])

    def test_unsupported_concepts_are_never_dropped_silently(self):
        text = "NQ long after an order block on the 5m and SMT divergence against ES, enter on the 5m FVG validation. Stop 10 points, target 2R."
        d = F.parse(text)
        labels = {u["label"] for u in d["unsupported"]}
        self.assertEqual(labels, {"Order block"})                 # SMT is measurable now
        # the order block stays in the strategy as a condition MarketLab reports it can't measure
        self.assertEqual(concepts(d), [("unsupported", "setup"), ("smt_divergence", "prerequisite"), ("fvg", "confirmation")])
        defs = {x["id"]: x for x in d["definitions"]}
        smt = next(defs[c["definition_id"]] for c in d["conditions"] if defs[c["definition_id"]]["concept"] == "smt_divergence")
        self.assertEqual(smt["params"]["compare_with"], "ES")
        self.assertTrue(d["status"]["ready"])                     # testable; the exact test will say what it couldn't measure

    def test_structure_types_and_time_words(self):
        d = F.parse("Short NQ. After a 15m bearish MSS, then a 15m BOS, enter. Stop above the structure, target 1:3. Flat by 15:55, hold max 90 minutes, no entries after 2pm.")
        defs = {x["id"]: x for x in d["definitions"]}
        types = [defs[c["definition_id"]]["params"].get("type") for c in d["conditions"]]
        self.assertEqual(types, ["mss", "bos"])
        self.assertEqual(d["direction"], "short")
        self.assertEqual(d["exits"][0]["params"]["r"], 3.0)
        self.assertEqual(d["settings"]["flat_time"], "15:55")
        self.assertEqual(d["settings"]["max_holding_minutes"], 90)
        self.assertEqual(d["settings"]["session"]["end"], "14:00")
        self.assertEqual(d["stops"][0]["kind"], "structure_extreme")

    def test_times_are_not_read_as_risk_reward(self):
        d = F.parse("NQ long on a 5m FVG validation, stop 10 points, target the overnight high, no entries after 11:30.")
        self.assertEqual(d["exits"][0]["kind"], "structure")
        self.assertEqual(d["exits"][0]["params"]["pools"], ["onhl"])

    def test_deterministic(self):
        self.assertEqual(F.parse(SPEC), F.parse(SPEC))


class ProjectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = R.RESEARCH_DIR
        R.RESEARCH_DIR = Path(self.tmp.name)

    def tearDown(self):
        R.RESEARCH_DIR = self.saved
        self.tmp.cleanup()

    def described(self, text=SPEC):
        p = R.create({"name": "t", "instrument": "NQ"})
        return R.describe(p["id"], {"text": text})

    def test_original_description_is_immutable(self):
        p = self.described()
        p = R.describe(p["id"], {"text": SPEC.replace("11:30", "11:00")})
        self.assertEqual(p["trade_description"]["original"], SPEC)
        self.assertEqual(len(p["trade_description"]["revisions"]), 1)
        self.assertEqual(p["formalization"]["text"], SPEC.replace("11:30", "11:00"))
        self.assertEqual(p["settings"]["session"]["end"], "11:00")

    def test_same_structure_as_manual_mode_and_readiness(self):
        p = self.described()
        self.assertEqual(p["mode"], "quick")
        self.assertEqual(p["status"], "READY")         # testable immediately; defaults can still be changed
        p = R.answer(p["id"], {"use_suggested": True})
        self.assertTrue(p["formalization"]["status"]["ready"])
        self.assertEqual(p["status"], "READY")
        self.assertTrue(all(c["ok"] for c in p["readiness"]["checks"]))
        # the rules live in the ordinary manual-mode fields
        self.assertEqual(len(p["hypothesis"]["conditions"]), 5)
        self.assertTrue(all(c["definition_id"] in {d["id"] for d in p["definitions"]} for c in p["hypothesis"]["conditions"]))
        manual = R.set_mode(p["id"], "manual")
        self.assertEqual(manual["hypothesis"], p["hypothesis"])

    def test_hand_edits_mark_divergence_and_reparse_keeps_a_version(self):
        p = R.answer(self.described()["id"], {"use_suggested": True})
        cond = p["hypothesis"]["conditions"][1]
        p = R.update_item(p["id"], "conditions", cond["id"], {"timing": "since 18:00 ET"})
        self.assertTrue(p["formalization"]["diverged"])
        versions = len(p["versions"])
        p = R.reparse(p["id"])
        self.assertEqual(len(p["versions"]), versions + 1)
        self.assertIn("before re-parse", p["versions"][-1]["label"])
        snap = p["versions"][-1]["snapshot"]["hypothesis"]["conditions"]
        self.assertEqual(next(c for c in snap if c["id"] == cond["id"])["timing"], "since 18:00 ET")
        self.assertFalse(p["formalization"]["diverged"])

    def test_saved_definitions_are_reused(self):
        R.save_personal_definition({"name": "My Displacement", "concept": "displacement",
                                    "params": {"atr_mult": 2.5, "consecutive": 2}, "auto": True})
        d = F.parse("NQ long after a 5m bullish displacement, enter on the close. Stop 10 points, target 2R.",
                    personal=R.personal_definitions())
        disp = next(x for x in d["definitions"] if x["concept"] == "displacement")
        self.assertEqual((disp["params"]["atr_mult"], disp["params"]["consecutive"]), (2.5, 2))
        self.assertEqual(disp["params"]["timeframe"], "5m")        # the description's timeframe still applies
        self.assertEqual(d["personal_used"], ["My Displacement"])
        R.save_personal_definition({"name": "Other", "concept": "displacement", "params": {"atr_mult": 1.0}, "auto": True})
        autos = [x for x in R.personal_definitions() if x["concept"] == "displacement" and x["auto"]]
        self.assertEqual([x["name"] for x in autos], ["Other"])    # one automatic definition per concept
        named = F.parse("NQ long after My Displacement on the 5m, stop 10 points, target 2R.", personal=R.personal_definitions())
        self.assertEqual(next(x for x in named["definitions"] if x["concept"] == "displacement")["params"]["atr_mult"], 2.5)

    def test_variations_fork_instead_of_overwriting(self):
        p = R.answer(self.described()["id"], {"use_suggested": True})
        fvg5 = next(d for d in p["definitions"] if d["concept"] == "fvg" and d["params"]["timeframe"] == "5m")
        p = R.make_variation(p["id"], {"kind": "timeframe", "definition_id": fvg5["id"], "value": "1m"})
        self.assertEqual(len(p["versions"]), 2)
        base, var = p["versions"]
        self.assertEqual(var["parent_id"], base["id"])
        before = next(d for d in base["snapshot"]["definitions"] if d["id"] == fvg5["id"])
        after = next(d for d in var["snapshot"]["definitions"] if d["id"] == fvg5["id"])
        self.assertEqual((before["params"]["timeframe"], after["params"]["timeframe"]), ("5m", "1m"))
        self.assertEqual(p["test_counts"]["variations"], 1)
        p = R.make_variation(p["id"], {"kind": "cutoff", "value": "11:00"})
        self.assertEqual(p["settings"]["session"]["end"], "11:00")
        self.assertEqual(p["versions"][-1]["parent_id"], var["id"])
        with self.assertRaises(R.ResearchError):
            R.make_variation(p["id"], {"kind": "timeframe", "definition_id": fvg5["id"], "value": "1m"})   # unchanged

    def test_duplicate_keeps_description_and_rules(self):
        p = R.answer(self.described()["id"], {"use_suggested": True})
        copy = R.fork(p["id"], {"duplicate": True})
        self.assertTrue(copy["name"].endswith("(copy)"))
        self.assertEqual(copy["trade_description"]["original"], SPEC)
        self.assertEqual(copy["hypothesis"], p["hypothesis"])

    def test_old_projects_move_to_automatic_data_unless_chosen(self):
        import json
        p = R.create({"name": "old"})
        path = R.RESEARCH_DIR / f"{p['id']}.json"
        raw = json.loads(path.read_text())
        raw["settings"].pop("data")
        path.write_text(json.dumps(raw))
        self.assertEqual(R.raw(p["id"])["settings"]["data"], {"provider": "auto", "base": "auto"})
        raw["settings"]["data"] = {"provider": "yahoo", "base": "1m"}
        path.write_text(json.dumps(raw))
        self.assertEqual(R.raw(p["id"])["settings"]["data"]["provider"], "auto")
        R.update(p["id"], {"settings": {"data": {"provider": "yahoo", "base": "1m"}}})      # an explicit choice sticks
        self.assertEqual(R.raw(p["id"])["settings"]["data"], {"provider": "yahoo", "base": "1m"})

    def test_actual_trade_r(self):
        p = self.described()
        p = R.set_actual_trade(p["id"], {"date": "2026-09-30", "time": "10:05", "direction": "long",
                                         "entry": 100, "stop": 90, "exit": 125})
        self.assertEqual(p["actual_trade"]["result_r"], 2.5)
        with self.assertRaises(R.ResearchError):
            R.set_actual_trade(p["id"], {"date": "2026-09-30", "time": "10:05", "direction": "long", "entry": 100, "stop": 110, "exit": 125})

    def test_described_strategy_runs_on_the_existing_backtester(self):
        text = ("NQ long. After a 5m bullish displacement and a 5m market structure break, enter when a 5m bullish FVG "
                "validates with a close above it. Stop below the displacement, target 2R.")
        p = R.answer(self.described(text)["id"], {"use_suggested": True})
        self.assertTrue(p["formalization"]["status"]["ready"], p["formalization"])
        rows = random_days([date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)], seed=5)
        from intraday.dataset import Dataset
        s, report = series_of(rows)
        ds = Dataset("NQ", "1m", "auto", s, report, engine.build_contexts(s, ["5m"]), None, None)
        result = strategy.run(R.raw(p["id"]), ds)
        self.assertIn("funnel", result)
        self.assertEqual(result["funnel"][0]["remaining"], result["triggers"])
        self.assertEqual(result["funnel"][-1]["remaining"], result["setups"])
        for st in result["setup_list"]:
            self.assertTrue(st["why"])
            self.assertTrue(all(w["time"] <= st["time"] for w in st["why"]))      # everything known by entry eligibility
        for t in result["primary"]["trades"]:
            self.assertGreaterEqual(t["entry_time"], t["setup_time"])
        w = strategy.why_not(R.raw(p["id"]), ds, result["setup_list"][0]["time"]) if result["setup_list"] else None
        if w:
            self.assertTrue(w["matched"])

    def test_luck_wording(self):
        trades = [{"r_net": v, "suspect": False} for v in [-1, -1, -0.5, 0.2, 0.5, 1, 1, -1, 0.3, -0.2, 0.1, -1]]
        strong = strategy.luck(trades, {"result_r": 3.0})
        self.assertIn("unusually strong", strong["text"])
        self.assertNotIn("luck", strong["text"].lower().replace("unusually", ""))
        self.assertIn("within the usual range", strategy.luck(trades, {"result_r": 0.1})["text"])
        self.assertIn("too few", strategy.luck(trades[:4], {"result_r": 3.0})["text"])


if __name__ == "__main__":
    unittest.main()
