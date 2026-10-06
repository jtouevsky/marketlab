"""
Tests for the completed Invest tabs: dependencies (shared first, provenance kept), themes (explicit
level rules, no invented percentages), macro (fundamental and measured kept apart) and the
configurable correlation analysis (matrix, extremes, clusters, insufficient-history state).
Offline: profiles and market proxies are stubbed.

Run:  python3 -m unittest discover tests -v
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import invest as INV                            # noqa: E402

DATES = [f"20{20 + i // 336:02d}-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}" for i in range(900)]


def series(steps):
    v, out = 100.0, []
    for s in steps:
        v *= 1 + s
        out.append(v)
    return {"dates": DATES[-len(out):], "values": out}          # every series ends on the same (latest) date


def prof(sym, factors, closes, industry="Semiconductors", sector="Technology"):
    return {"ticker": sym, "name": sym, "sector": sector, "industry": industry, "fund": False, "errors": [], "closes": closes,
            "fund_holdings": [], "fund_sectors": {}, "fundamentals": {}, "exposure_state": {"state": "ok"}, "competitors": [],
            "factors": {k: {"key": k, "label": label, "kind": kind, "strength": strength,
                            "evidence": [{"text": f"{sym} says {label}", "source": "10-K filed 2026-02-25", "url": "https://sec.example/x",
                                          "date": "2026-02-25", "type": "Annual report (SEC EDGAR)", **({"mentions": m} if m else {})}]}
                        for k, label, kind, strength, m in factors}}


class InvestTabsTests(unittest.TestCase):
    def setUp(self):
        rng = random.Random(9)
        common = [rng.gauss(0, .012) for _ in range(900)]
        tsmc = ("entity:TSMC", "TSMC (supplier / manufacturer)", "supplier", "emphasized", None)
        ai = ("theme:ai", "AI demand", "theme", "emphasized", 40)
        ai_low = ("theme:ai", "AI demand", "theme", "mentioned", 7)
        rates = ("macro:Interest rates", "Interest rates", "macro", "emphasized", None)
        self.profiles = {
            "AAA": prof("AAA", [tsmc, ai, rates], series([c + rng.gauss(0, .003) for c in common])),
            "BBB": prof("BBB", [tsmc, ai_low], series([c + rng.gauss(0, .003) for c in common])),
            "CCC": prof("CCC", [], series([rng.gauss(0, .01) for _ in range(900)]), industry="Utilities", sector="Utilities"),
            "NEW": prof("NEW", [ai], series([rng.gauss(0, .01) for _ in range(30)]), industry="Software"),
        }
        self.saved = (INV.profile, INV._proxy_returns)
        INV.profile = lambda s: self.profiles[s]
        INV._proxy_returns = lambda sym, n: {}
        INV._corr_cache.clear()

    def tearDown(self):
        INV.profile, INV._proxy_returns = self.saved

    def analyze(self, syms=("AAA", "BBB", "CCC", "NEW")):
        return INV.analyze({"holdings": [{"ticker": t} for t in syms], "horizon": "12m", "text": ""})

    def test_dependencies_shared_first_with_provenance(self):
        d = self.analyze()["dependencies"]
        first = d["shared"][0]
        self.assertEqual(first["key"], "entity:TSMC")
        self.assertEqual(first["category"], "Suppliers & manufacturing")
        self.assertEqual({h["ticker"] for h in first["holders"]}, {"AAA", "BBB"})
        ev = first["holders"][0]["evidence"][0]
        self.assertEqual((ev["type"], ev["date"]), ("Annual report (SEC EDGAR)", "2026-02-25"))
        self.assertEqual(d["per_holding"]["CCC"], [])                     # nothing invented for a holding without data
        self.assertTrue(d["available"])

    def test_theme_levels_follow_the_rules(self):
        t = next(x for x in self.analyze()["themes"]["themes"] if x["key"] == "theme:ai")
        levels = {m["ticker"]: m["level"] for m in t["members"]}
        self.assertEqual(levels, {"AAA": "high", "BBB": "moderate", "NEW": "high"})
        self.assertTrue(t["shared"])
        self.assertNotIn("%", "".join(m["level"] for m in t["members"]))

    def test_macro_keeps_fundamental_and_measured_apart(self):
        m = self.analyze()["macro"]
        rates = next(v for v in m["variables"] if v["id"] == "rates")
        self.assertEqual([h["ticker"] for h in rates["fundamental"]["holders"]], ["AAA"])
        self.assertIsNone(rates["measured"])                               # no proxy data offline → no invented sensitivity

    def test_correlation_windows_clusters_and_missing_history(self):
        a = self.analyze()
        c = a["corr"]
        self.assertEqual(c["window"], "1y")
        top = c["highest"][0]
        self.assertEqual({top["a"], top["b"]}, {"AAA", "BBB"})
        self.assertIn(["AAA", "BBB"], c["clusters"])
        self.assertIn("Hidden dependencies can still exist", c["note"])
        three = INV.correlation_for(a["id"], "3y")
        self.assertEqual(three["window_days"], 756)
        self.assertGreaterEqual(three["range"]["max_pair_days"], 700)
        self.assertIn("NEW", three["short_history"])                          # flagged, not silently mixed in
        with self.assertRaises(INV.InvestError):
            INV.correlation_for(a["id"], "10y")
        short = self.analyze(("NEW", "CCC"))
        one_m = INV.correlation_for(short["id"], "1m")
        self.assertTrue(one_m["enough"])
        self.assertEqual(self.analyze(("AAA",))["corr"]["enough"], False)   # a single holding: nothing to correlate

    def test_overview_counts_are_factual(self):
        o = self.analyze()["overview"]
        self.assertEqual(o["n"], 4)
        self.assertGreaterEqual(o["shared_themes"], 1)
        self.assertIn(o["largest"], ("AI demand", "TSMC (supplier / manufacturer)"))
        self.assertGreaterEqual(o["largest_count"], 2)


if __name__ == "__main__":
    unittest.main()
