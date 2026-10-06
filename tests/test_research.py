"""
Tests for Research Projects and the Concept Library.

Run:  python3 -m unittest discover tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import concept_library as C          # noqa: E402
import research_projects as R        # noqa: E402

EXAMPLE = ("I think when NQ has a directional 1-hour fair value gap, and liquidity in the opposite direction has "
           "already been swept earlier in the session, then if a 5-minute or 1-minute fair value gap validates in "
           "the direction of the 1-hour FVG, price is more likely to continue in that direction.")


class TempStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = R.RESEARCH_DIR
        R.RESEARCH_DIR = Path(self.tmp.name)

    def tearDown(self):
        R.RESEARCH_DIR = self.saved
        self.tmp.cleanup()


class ConceptLibraryTests(unittest.TestCase):
    def test_formalization_finds_concepts_per_timeframe(self):
        a = C.analyze_observation(EXAMPLE)
        self.assertEqual(a["instrument"], "NQ")
        self.assertEqual(a["timeframes"]["primary"], "1h")
        self.assertEqual(a["timeframes"]["execution"], ["1m", "5m"])
        keys = {t["key"] for t in a["terms"]}
        self.assertIn("fvg@1h", keys)
        self.assertIn("fvg@1m+5m", keys)                 # the lower-timeframe FVG is a separate term
        self.assertIn("liquidity_sweep", keys)            # not wrongly bound to "1-hour"
        for needed in ("timing", "direction", "validation", "entry", "invalidation", "target", "session", "holding"):
            self.assertIn(needed, keys)
        rules = {t["key"]: t for t in a["terms"] if t["kind"] == "rule"}
        self.assertFalse(rules["entry"]["mentioned"])
        self.assertFalse(rules["holding"]["mentioned"])   # "1-minute" is a timeframe, not a holding time

    def test_every_concept_explains_itself(self):
        for concept in C.CONCEPTS:
            lines = C.explain(concept["id"])
            self.assertTrue(lines and all(isinstance(line, str) and line for line in lines), concept["id"])

    def test_fvg_definition_is_explicit_and_configurable(self):
        text = " ".join(C.explain("fvg", {"direction": "bullish", "timeframe": "1h", "min_gap": 3, "min_gap_unit": "ATR"}))
        self.assertIn("candle 1's HIGH is below candle 3's LOW", text)
        self.assertIn("3 × ATR", text)
        self.assertIn("does not exist until candle 3 has CLOSED", text)
        self.assertNotIn("Bearish", text)

    def test_fvg_states_say_when_they_are_known(self):
        states = {s["id"]: s for s in C.CONCEPTS_BY_ID["fvg"]["states"]}
        for name in ("CREATED", "ACTIVE", "ENTERED", "PARTIALLY_FILLED", "HALF_FILLED", "FULLY_FILLED",
                     "VALIDATED", "INVALIDATED", "EXPIRED"):
            self.assertIn(name, states)
            self.assertTrue(states[name]["known"])

    def test_sweep_separates_occurrence_from_confirmation(self):
        text = " ".join(C.explain("liquidity_sweep", {"side": "buy-side", "levels": ["pdh"]}))
        self.assertIn("Occurrence", text)
        self.assertIn("Confirmation", text)
        self.assertIn("previous day high", text)

    def test_contracts_share_market_but_not_economics(self):
        self.assertEqual(C.INSTRUMENTS["NQ"]["market"], C.INSTRUMENTS["MNQ"]["market"])
        self.assertEqual(C.INSTRUMENTS["NQ"]["point_value"], 10 * C.INSTRUMENTS["MNQ"]["point_value"])


class ProjectTests(TempStore):
    def test_original_observation_is_never_overwritten(self):
        p = R.create({"name": "Test", "observation": EXAMPLE})
        self.assertEqual(p["observation"]["original"], EXAMPLE)
        with self.assertRaises(R.ResearchError):
            R.update(p["id"], {"observation": "something else"})
        p = R.add_revision(p["id"], "Reworded: 1H FVG + sweep + 5m validation → continuation.")
        self.assertEqual(p["observation"]["original"], EXAMPLE)
        self.assertEqual(len(p["observation"]["revisions"]), 1)

    def test_defining_a_term_marks_it_defined_and_unlinks_on_delete(self):
        p = R.create({"observation": EXAMPLE})
        p = R.add_item(p["id"], "definitions", {"concept": "fvg", "term_key": "fvg@1h", "params": {"timeframe": "1h", "direction": "bullish"}})
        d = p["definitions"][0]
        self.assertEqual(d["params"]["timeframe"], "1h")
        self.assertTrue(d["explanation"])
        term = next(t for t in p["terms"] if t["key"] == "fvg@1h")
        self.assertEqual(term["status"], "defined")
        p = R.delete_item(p["id"], "definitions", d["id"])
        term = next(t for t in p["terms"] if t["key"] == "fvg@1h")
        self.assertEqual(term["status"], "open")

    def test_terms_resolve_by_rule_or_definition_only(self):
        p = R.create({"observation": EXAMPLE})
        with self.assertRaises(R.ResearchError):     # a concept can't be "defined" with words alone
            R.update(p["id"], {"term": {"key": "fvg@1h", "status": "defined", "rule_text": "a gap"}})
        with self.assertRaises(R.ResearchError):     # a rule needs actual text
            R.update(p["id"], {"term": {"key": "timing", "status": "defined", "rule_text": "  "}})
        p = R.update(p["id"], {"term": {"key": "timing", "status": "defined", "rule_text": "Sweep happens earlier in the same session (since 18:00 ET)."}})
        self.assertEqual(next(t for t in p["terms"] if t["key"] == "timing")["status"], "defined")
        p = R.add_item(p["id"], "definitions", {"concept": "fvg"})
        p = R.update(p["id"], {"term": {"key": "fvg@1h", "status": "defined", "definition_id": p["definitions"][0]["id"]}})
        self.assertEqual(next(t for t in p["terms"] if t["key"] == "fvg@1h")["definition_id"], p["definitions"][0]["id"])

    def test_bad_parameters_fall_back_to_defaults(self):
        p = R.create({"observation": "x"})
        p = R.add_item(p["id"], "definitions", {"concept": "fvg", "params": {"timeframe": "7m", "min_gap": -5, "evil": 1}})
        params = p["definitions"][0]["params"]
        self.assertEqual(params["timeframe"], "5m")
        self.assertEqual(params["min_gap"], 0)
        self.assertNotIn("evil", params)

    def test_conditions_need_a_definition(self):
        p = R.create({"observation": EXAMPLE})
        with self.assertRaises(R.ResearchError):
            R.add_item(p["id"], "conditions", {"definition_id": "nope"})
        p = R.add_item(p["id"], "definitions", {"concept": "fvg"})
        p = R.add_item(p["id"], "conditions", {"definition_id": p["definitions"][0]["id"], "requirement": "VALIDATED"})
        self.assertEqual(p["hypothesis"]["conditions"][0]["letter"], "A")
        p = R.add_item(p["id"], "conditions", {"definition_id": p["definitions"][0]["id"]})
        first, second = (c["id"] for c in p["hypothesis"]["conditions"])
        p = R.update_item(p["id"], "conditions", second, {"move": -1})
        self.assertEqual([c["letter"] for c in p["hypothesis"]["conditions"]], ["B", "A"])
        with self.assertRaises(R.ResearchError):          # can't delete a definition still in use
            R.delete_item(p["id"], "definitions", p["definitions"][0]["id"])

    def test_template_starts_with_ideas_but_no_definitions(self):
        p = R.create({"template": "nq_htf_fvg"})
        self.assertEqual(p["instrument"]["symbol"], "NQ")
        self.assertEqual([e["letter"] for e in p["entries"]], ["A", "B", "C", "D"])
        self.assertEqual(p["definitions"], [])             # the researcher defines the terms
        self.assertTrue(p["questions"])
        self.assertTrue(p["readiness"]["checks"][-1]["ok"])   # free NQ data is available now
        self.assertEqual(p["settings"]["data"], {"provider": "auto", "base": "auto"})
        self.assertEqual(p["timeframes"]["roles"]["context"], "1h")
        self.assertEqual(p["timeframes"]["roles"]["setup"], "5m")

    def test_versions_form_a_tree_and_restore(self):
        p = R.create({"observation": EXAMPLE})
        p = R.add_item(p["id"], "entries", {"kind": "validation_close"})
        p = R.save_version(p["id"], {"label": "v1"})
        v1 = p["versions"][0]["id"]
        p = R.add_item(p["id"], "entries", {"kind": "fvg_50"})
        p = R.save_version(p["id"], {"label": "v2"})
        self.assertEqual(p["versions"][1]["parent_id"], v1)
        p = R.restore_version(p["id"], v1)
        self.assertEqual(len(p["entries"]), 1)
        p = R.save_version(p["id"], {"label": "v2b"})       # a branch from v1
        self.assertEqual(p["versions"][2]["parent_id"], v1)

    def test_fork_keeps_observation_and_parent(self):
        p = R.create({"observation": EXAMPLE, "name": "Parent"})
        p = R.add_item(p["id"], "notes", {"text": "private note"})
        f = R.fork(p["id"])
        self.assertEqual(f["parent_project_id"], p["id"])
        self.assertEqual(f["observation"]["original"], EXAMPLE)
        self.assertNotIn("private note", [n["text"] for n in f["notes"]])
        self.assertEqual(len(R.list_projects()), 2)

    def test_examples_keep_failures(self):
        p = R.create({"observation": EXAMPLE})
        p = R.add_item(p["id"], "examples", {"kind": "failed", "date": "2026-09-29", "time": "10:25", "result_r": "-1"})
        self.assertEqual(p["examples"][0]["result_r"], -1.0)
        with self.assertRaises(R.ResearchError):
            R.add_item(p["id"], "examples", {"kind": "winner"})
        with self.assertRaises(R.ResearchError):
            R.add_item(p["id"], "examples", {"kind": "failed", "date": "29/09/2026"})

    def test_timeframe_roles_round_trip(self):
        p = R.create({"observation": EXAMPLE})
        p = R.update(p["id"], {"timeframes": {"roles": {"context": "1h", "setup": "5m", "confirmation": ["5m", "1m"],
                                                        "execution": "1m", "bogus": "x"}}})
        roles = p["timeframes"]["roles"]
        self.assertEqual(roles, {"context": "1h", "setup": "5m", "confirmation": ["1m", "5m"], "execution": "1m"})
        self.assertEqual(p["timeframes"]["primary"], "1h")
        self.assertEqual(p["timeframes"]["execution"], ["1m", "5m"])
        p = R.update(p["id"], {"timeframes": {"roles": {"context": "2h"}}})
        self.assertIsNone(p["timeframes"]["roles"]["context"])

    def test_ids_cannot_escape_the_folder(self):
        with self.assertRaises(R.ResearchError):
            R.get("../../etc/passwd")
        with self.assertRaises(R.ResearchError):
            R.get("rp_../x")


if __name__ == "__main__":
    unittest.main()
