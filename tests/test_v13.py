"""
Tests for the Invest performance + workflow fix: ticker resolution (no ordinary words as tickers, company
names, questions for ambiguous ones), thesis parsing, weight inputs and renormalisation, the fast "quick"
stage with per-holding error isolation, request de-duplication (single flight) and the correlation cache.
Offline: the SEC list and Yahoo are not used (basic() is stubbed).

Run:  python3 -m unittest discover tests -v
"""

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import invest as INV                            # noqa: E402
import resolver                                 # noqa: E402

DATES = [f"20{20 + i // 336:02d}-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}" for i in range(400)]


def closes(seed, n=400):
    import random
    rnd = random.Random(seed)
    v, out = 100.0, []
    for _ in range(n):
        v *= 1 + rnd.gauss(0, 0.015)
        out.append(v)
    return {"dates": DATES[-n:], "values": out}


class ResolverTests(unittest.TestCase):
    def setUp(self):
        resolver._names = None                  # offline list: KNOWN + aliases (+ SEC when configured)

    def tickers(self, text):
        return [h["ticker"] for h in resolver.resolve(text)["holdings"]]

    def test_ordinary_words_are_never_holdings(self):
        res = resolver.resolve("AI and IT stocks are ON fire, ALL of them. F it, I think A lot of them CAN run.")
        self.assertEqual(res["holdings"], [])
        self.assertEqual(res["suggestions"], [])
        for word in ("AI", "IT", "ON", "ALL"):
            self.assertNotIn(word, self.tickers(f"I like {word} a lot"))

    def test_company_names_resolve(self):
        self.assertEqual(self.tickers("Nvidia and Apple"), ["NVDA", "AAPL"])
        self.assertEqual(self.tickers("I hold taiwan semiconductor"), ["TSM"])
        self.assertIn("NVDA", self.tickers("NVDA"))

    def test_dollar_prefix_is_explicit(self):
        self.assertIn("NVDA", self.tickers("$NVDA is my biggest"))

    def test_ambiguous_ticker_becomes_a_question(self):
        res = resolver.resolve("thinking about (F) here")
        self.assertEqual(res["holdings"], [])
        self.assertEqual([s["ticker"] for s in res["suggestions"]], ["F"])
        self.assertIn("Did you mean", res["suggestions"][0]["question"])
        self.assertIn("(F)", res["suggestions"][0]["question"])

    def test_company_name_beats_ambiguity(self):
        self.assertEqual(self.tickers("Ford F"), ["F"])

    def test_spec_prose(self):
        res = resolver.resolve("I think Ford F and AI and IT stocks are ON fire, ALL of them. Nvidia and Apple.")
        self.assertEqual(sorted(h["ticker"] for h in res["holdings"]), ["AAPL", "F", "NVDA"])
        self.assertTrue({"AI", "IT", "ON", "ALL"} <= set(res["ignored"]))


class ParseTests(unittest.TestCase):
    def test_parse_returns_detected_suggestions_and_ignored(self):
        out = INV.parse_thesis("Nvidia, AMD and QQQ for five years; AI and IT are ON fire")
        self.assertEqual(out["tickers"][:1], ["NVDA"])
        self.assertIn("AMD", out["tickers"])
        self.assertIn("QQQ", out["tickers"])
        self.assertNotIn("AI", out["tickers"])
        self.assertNotIn("IT", out["tickers"])
        self.assertEqual(out["horizon"], {"id": "5y", "months": 60})
        self.assertIn("suggestions", out)
        self.assertIn("ignored", out)


class WeightTests(unittest.TestCase):
    def rows(self, holdings, cash=0):
        return INV._inputs({"holdings": holdings, "cash": cash})[3:]

    def test_blank_weights_share_the_rest(self):
        rows, cash = self.rows([{"ticker": "NVDA", "weight": 50}, {"ticker": "AMD", "weight": None}, {"ticker": "TSM", "weight": ""}])
        self.assertEqual([round(r["weight"], 2) for r in rows], [50, 25, 25])

    def test_decimal_and_zero_and_off_total(self):
        rows, cash = self.rows([{"ticker": "NVDA", "weight": 12.5}, {"ticker": "AMD", "weight": 0}, {"ticker": "TSM", "weight": 75}])
        self.assertAlmostEqual(sum(r["weight"] for r in rows), 100)
        self.assertAlmostEqual(rows[1]["weight"], 0)
        self.assertAlmostEqual(rows[0]["weight"], 12.5 / 87.5 * 100)

    def test_all_blank_is_equal_with_cash(self):
        rows, cash = self.rows([{"ticker": "A1"}, {"ticker": "B1"}], cash=10)
        self.assertEqual([round(r["weight"], 2) for r in rows], [45, 45])
        self.assertEqual(cash, 10)

    def test_duplicates_and_junk_dropped(self):
        rows, _ = self.rows([{"ticker": "nvda"}, {"ticker": "NVDA"}, {"ticker": "!!"}])
        self.assertEqual([r["ticker"] for r in rows], ["NVDA"])

    def test_renormalize_after_dropping(self):
        rows = INV._renormalize([{"ticker": "NVDA", "weight": 30}, {"ticker": "AMD", "weight": 30}], 10)
        self.assertEqual([round(r["weight"], 2) for r in rows], [45, 45])


class QuickStageTests(unittest.TestCase):
    def setUp(self):
        self.orig = INV.basic
        self.calls = []

        def fake(sym):
            self.calls.append(sym)
            if sym == "ZZZZQ":
                raise INV.InvestError("Could not resolve ZZZZQ (no price data).")
            return {"ticker": sym, "name": f"{sym} Inc", "sector": "Technology", "industry": "Semiconductors", "fund": False,
                    "price": 100, "ret_12m": 0.1, "vol_1y": 0.3, "closes": closes(hash(sym) % 1000), "errors": []}
        INV.basic = fake
        INV._corr_cache.clear()

    def tearDown(self):
        INV.basic = self.orig

    def test_quick_isolates_bad_holdings(self):
        out = INV.quick({"holdings": [{"ticker": "NVDA"}, {"ticker": "AMD"}, {"ticker": "ZZZZQ"}]})
        self.assertEqual(out["stage"], "quick")
        self.assertEqual([h["ticker"] for h in out["holdings"]], ["NVDA", "AMD"])
        self.assertAlmostEqual(sum(h["weight"] for h in out["holdings"]), 100)
        self.assertTrue(any("Could not resolve ZZZZQ" in p for p in out["problems"]))
        self.assertIn("average", out["corr"])
        self.assertEqual(out["sectors"], {"Technology": 100})
        # the quick result can serve other correlation windows without a full analysis
        self.assertIn(out["id"], INV._analyses)

    def test_quick_all_bad_is_an_error(self):
        with self.assertRaises(INV.InvestError):
            INV.quick({"holdings": [{"ticker": "ZZZZQ"}]})

    def test_correlation_is_cached(self):
        out = INV.quick({"holdings": [{"ticker": "NVDA"}, {"ticker": "AMD"}]})
        n = len(INV._corr_cache)
        INV.correlation_for(out["id"], "1y")
        INV.correlation_for(out["id"], "1y")
        self.assertLessEqual(len(INV._corr_cache), n + 1)


class SingleFlightTests(unittest.TestCase):
    def test_concurrent_callers_share_one_computation(self):
        runs = []

        def slow():
            runs.append(1)
            time.sleep(0.2)
            return 42
        results = []
        threads = [threading.Thread(target=lambda: results.append(INV._single_flight(("t", "X"), slow))) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [42] * 5)
        self.assertEqual(len(runs), 1)

    def test_errors_propagate_and_do_not_stick(self):
        def bad():
            raise ValueError("boom")
        with self.assertRaises(ValueError):
            INV._single_flight(("t", "Y"), bad)
        self.assertEqual(INV._single_flight(("t", "Y"), lambda: 7), 7)


if __name__ == "__main__":
    unittest.main()


class ScenarioRebuildTests(unittest.TestCase):
    """v14: a Trace after a server restart (analysis id unknown) rebuilds from the same inputs instead of failing."""

    def test_expired_analysis_is_rebuilt_from_inputs(self):
        calls = []
        orig = INV.analyze

        def fake_analyze(body):
            calls.append(body)
            with INV._lock:
                INV._analyses["rebuilt"] = ({"NVDA": {"ticker": "NVDA"}}, {"NVDA": 100.0}, {})
            return {"id": "rebuilt"}
        INV.analyze = fake_analyze
        try:
            out = INV.custom_scenario("gone", "something unrelated", {"holdings": [{"ticker": "NVDA"}]})
        finally:
            INV.analyze = orig
            INV._analyses.pop("rebuilt", None)
        self.assertEqual(len(calls), 1)
        self.assertIn("why", out)

    def test_expired_without_inputs_still_explains(self):
        with self.assertRaises(INV.InvestError):
            INV.custom_scenario("gone-too", "rates stay high", None)
