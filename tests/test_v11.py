"""
Tests for the simplification / Invest overhaul: one-call testing (auto_test), execution relaxations
in the ladder, the Invest lenses (dependency / thematic / macro kept separate), fund look-through,
the summary and blind spots, and the measured-sensitivity helper. Offline: prices for the market
proxies are stubbed.

Run:  python3 -m unittest discover tests -v
"""

import random
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import invest as INV                            # noqa: E402
import research_projects as R                   # noqa: E402
from intraday import engine, ladder             # noqa: E402
from test_intraday import TUE, random_days, series_of   # noqa: E402
from test_v9 import fake_profile                # noqa: E402

DATES = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(300)]


def walk(steps):
    v, out = 100.0, []
    for s in steps:
        v *= 1 + s
        out.append(v)
    return {"dates": DATES, "values": out}


class LadderExecutionTests(unittest.TestCase):
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
        return R.raw(pid)

    def dataset(self):
        from intraday.dataset import Dataset
        s, report = series_of(random_days([date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)], seed=3))
        return Dataset("NQ", "1m", "test", s, report, engine.build_contexts(s, ["5m"]), None, None)

    def test_limit_entry_has_an_execution_relaxation(self):
        p = self.project()
        cands = {c["id"]: c for c in ladder.candidates(p)}
        self.assertIn("entry", cands)
        self.assertEqual(cands["entry"]["class"], "execution")
        v = ladder.apply(p, ["entry"])
        self.assertEqual(v["entries"][0]["kind"], "validation_close")
        self.assertEqual(p["entries"][0]["kind"], "fvg_50")                       # the project itself is unchanged
        self.assertEqual(v["ladder_relaxations"][0]["class"], "execution")

    def test_similar_relaxes_execution_before_secondary_and_never_core(self):
        p = self.project()
        out = ladder.build(p, self.dataset())
        sim = next((t for t in out["tiers"] if t["tier"] == "similar"), None)
        self.assertIsNotNone(sim)
        classes = [r["class"] for r in sim["relaxations"]]
        self.assertEqual(classes[0], "execution")                                 # execution comes first
        core = {c["id"] for c in p["hypothesis"]["conditions"] if c["importance"] == "core"}
        self.assertFalse(any(rid.split(":", 1)[-1] in core for rid in sim["relax_ids"] if rid.startswith("drop:")))

    def test_auto_test_labels_the_tier_it_shows(self):
        p = self.project()
        L, result = ladder.auto_test(p, self.dataset())
        self.assertIn(L["auto"]["tier"], ("exact", "similar", "broader", None))
        order = [t["tier"] for t in L["tiers"]]
        shown = order.index(L["auto"]["tier"]) if L["auto"]["tier"] else len(order) - 1
        for t in L["tiers"][:shown + 1]:                                       # every tier up to the one shown was run and counted
            if t.get("setups"):
                self.assertIn("trades", t)
        if result:
            self.assertEqual(result["tier"]["name"], L["auto"]["tier"])
            if L["auto"]["tier"] != "exact":
                self.assertTrue(result["tier"]["relaxations"])                   # a relaxed result always lists what changed


class InvestLensTests(unittest.TestCase):
    def setUp(self):
        self.saved = (INV.profile, INV._proxy_returns)
        rng = random.Random(4)
        common = [rng.gauss(0, .012) for _ in range(300)]
        ai = [("theme:ai", "AI demand", "theme"), ("geo:Taiwan", "Taiwan", "geography"), ("macro:Interest rates", "Interest rates", "macro")]
        self.profiles = {
            "AAA": fake_profile("AAA", ai, walk([c + rng.gauss(0, .004) for c in common])),
            "BBB": fake_profile("BBB", ai, walk([c + rng.gauss(0, .004) for c in common])),
            "CCC": fake_profile("CCC", ai[:1], walk([rng.gauss(0, .01) for _ in range(300)]), industry="Software"),
            "FND": dict(fake_profile("FND", [], walk(common), industry=None), fund=True, sector=None,
                        fund_holdings=[{"ticker": "AAA", "name": "AAA", "weight": 0.09}, {"ticker": "ZZZ", "name": "Z", "weight": 0.05}],
                        fund_sectors={"technology": 0.8, "healthcare": 0.2}),
        }
        for p in self.profiles.values():
            p.setdefault("fund_holdings", [])
            p.setdefault("fund_sectors", {})
            p.setdefault("sector", "Technology")
        INV.profile = lambda sym: self.profiles[sym]
        INV._proxy_returns = lambda sym, n: {}                                    # offline: no market proxies

    def tearDown(self):
        INV.profile, INV._proxy_returns = self.saved

    def analyze(self):
        return INV.analyze({"holdings": [{"ticker": t} for t in self.profiles], "horizon": "24m", "text": ""})

    def test_lenses_keep_dependency_theme_and_macro_apart(self):
        a = self.analyze()
        dep = {x["key"] for x in a["lenses"]["dependency"]}
        thm = {x["key"] for x in a["lenses"]["thematic"]}
        mac = {x["key"] for x in a["lenses"]["macro"]}
        self.assertIn("geo:Taiwan", dep)
        self.assertIn("theme:ai", thm)
        self.assertNotIn("theme:ai", dep)
        self.assertFalse(dep & mac)
        self.assertIn("macro:Interest rates", mac)

    def test_fund_look_through(self):
        a = self.analyze()
        self.assertEqual([(l["fund"], l["ticker"]) for l in a["lookthrough"]], [("FND", "AAA")])      # ZZZ isn't in the basket
        ai = next(f for f in a["factors"] if f["key"] == "theme:ai")
        self.assertIn(("FND", "look-through"), [(h["ticker"], h["strength"]) for h in ai["holders"]])
        self.assertAlmostEqual(a["effective_weights"]["AAA"], 25 + 25 * 0.09, places=3)
        self.assertTrue(any(b["kind"] == "overlap" for b in a["blind_spots"]))

    def test_summary_first_and_blind_spots_are_descriptive(self):
        a = self.analyze()
        self.assertTrue(a["summary"])
        self.assertTrue(any("AI demand" in s["text"] for s in a["summary"]))
        texts = " ".join(b["text"] for b in a["blind_spots"]).lower()
        for word in ("buy", "sell", "should", "recommend"):
            self.assertNotIn(word, texts)

    def test_correlation_alone_is_not_dependency(self):
        a = self.analyze()
        pair = next(p for p in a["correlation"]["pairs"] if {p["a"], p["b"]} == {"AAA", "FND"})
        self.assertEqual(pair["kind"], "market")                                # correlated, but no shared filing dependency

    def test_sensitivities_are_measured_from_prices(self):
        rets = {"X": {d: (i % 7 - 3) / 100 for i, d in enumerate(DATES[:120])}}
        INV._proxy_returns = lambda sym, n: rets["X"] if sym == "SPY" else {}
        out = INV._sensitivities(rets, 120)
        self.assertAlmostEqual(out["X"]["SPY"], 1.0, places=6)
        self.assertAlmostEqual(out["X"]["beta"], 1.0, places=6)
        self.assertIsNone(out["X"]["TLT"])


if __name__ == "__main__":
    unittest.main()
