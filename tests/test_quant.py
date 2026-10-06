"""
Tests for MarketLab's quantitative core. No network: every test uses small
synthetic price series whose correct answers can be worked out by hand.

Run from the marketlab folder:
    python3 -m unittest discover tests -v
"""

import os
import statistics
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import challenge          # noqa: E402
import conditions         # noqa: E402
import dataquality        # noqa: E402
import experiments        # noqa: E402
import indicators         # noqa: E402
import marketdata         # noqa: E402
import robust             # noqa: E402


def trading_days(n, start=date(2020, 1, 6)):
    """n weekdays as ISO strings (no gaps longer than a weekend)."""
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_bars(closes, volumes=None, symbol="TEST"):
    n = len(closes)
    bars = {"date": trading_days(n), "open": list(closes), "high": list(closes), "low": list(closes),
            "close": list(closes), "volume": list(volumes or [1000] * n)}
    clean, report = dataquality.check_daily(bars, {})
    clean.update(symbol=symbol, interval="1d", adjusted=True, provider="synthetic", retrieved_at="2026-01-01T00:00:00Z",
                 quality=report, splits={})
    return clean


class FakeData:
    """Swap marketdata.daily_bars for synthetic series during a test."""

    def __init__(self, series):
        self.series = series

    def __enter__(self):
        self.original = marketdata.daily_bars
        marketdata.daily_bars = lambda symbol: self.series[symbol]
        experiments._series_cache.clear()
        return self

    def __exit__(self, *exc):
        marketdata.daily_bars = self.original
        experiments._series_cache.clear()


def definition(conds, horizon=5, symbol="TEST", benchmark=None):
    return {"instrument": {"symbol": symbol}, "conditions": conds, "outcome": {"horizon": horizon},
            "benchmark": benchmark, "settings": {"trim": 0.05}}


# ---------------------------------------------------------------------------
class TestIndicators(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(indicators.sma([1, 2, 3, 4, 5], 3), [None, None, 2.0, 3.0, 4.0])

    def test_trailing_mean_excludes_today(self):
        # average of the 2 values BEFORE each day
        self.assertEqual(indicators.trailing_mean([10, 20, 30, 40], 2), [None, None, 15.0, 25.0])

    def test_rsi_all_gains_is_100(self):
        values = indicators.rsi(list(range(1, 30)), 14)
        self.assertIsNone(values[13])
        self.assertEqual(values[14], 100.0)
        self.assertEqual(values[-1], 100.0)

    def test_rsi_wilder_hand_computed(self):
        # +1, -1 alternating: avg gain = avg loss after the first period -> RSI 50
        closes = [10, 11] * 20
        values = indicators.rsi(closes, 2)
        self.assertAlmostEqual(values[2], 50.0)

    def test_rsi_uses_no_future(self):
        closes = [10 + (i % 7) for i in range(60)]
        full = indicators.rsi(closes, 14)
        for t in range(20, 60, 7):
            self.assertAlmostEqual(indicators.rsi(closes[:t + 1], 14)[t], full[t])


# ---------------------------------------------------------------------------
class TestConditions(unittest.TestCase):
    def test_price_move(self):
        bars = make_bars([100, 100, 100, 94, 100, 100])
        c = conditions.validate({"type": "price_move", "params": {"direction": "falls", "threshold": 5, "window": 2}})
        self.assertEqual(conditions.compute(c, bars), [None, None, False, True, False, False])

    def test_relative_volume_compares_with_previous_days(self):
        bars = make_bars([100] * 8, volumes=[100, 100, 100, 100, 100, 300, 100, 100])
        c = conditions.validate({"type": "relative_volume", "params": {"op": "above", "multiple": 2, "lookback": 5}})
        # day 5: 300 vs the average of days 0-4 (100) = 3x; day 6 compares with days 1-5 (140) = 0.7x
        self.assertEqual(conditions.compute(c, bars), [None] * 5 + [True, False, False])

    def test_ma_position_and_cross(self):
        closes = [10, 10, 10, 9, 9, 12, 12]
        bars = make_bars(closes)
        pos = conditions.validate({"type": "ma_position", "params": {"op": "above", "period": 5}})
        cross = conditions.validate({"type": "ma_cross", "params": {"op": "above", "period": 5}})
        # SMA5 at t=4: 9.6 (close 9 below); t=5: (10+10+9+9+12)/5 = 10.0, close 12 above -> cross
        self.assertEqual(conditions.compute(pos, bars)[4:], [False, True, True])
        self.assertEqual(conditions.compute(cross, bars)[4:], [None, True, False])   # t=4 needs yesterday's SMA (none yet)

    def test_and_or_logic(self):
        s = {"a": [True, True, False, None], "b": [True, False, True, True]}
        self.assertEqual(conditions.combine({"op": "AND", "items": ["a", "b"]}, s, 4), [True, False, False, False])
        self.assertEqual(conditions.combine({"op": "OR", "items": ["a", "b"]}, s, 4), [True, True, True, True])

    def test_logic_must_use_every_condition(self):
        with self.assertRaises(ValueError):
            conditions.validate_logic({"op": "AND", "items": ["c1"]}, ["c1", "c2"])
        self.assertEqual(conditions.validate_logic(None, ["c1", "c2"]), {"op": "AND", "items": ["c1", "c2"]})

    def test_earnings_session_timing(self):
        bars = make_bars([100] * 6)
        d = bars["date"]
        reports = [{"date": d[1], "timing": "after_close"}, {"date": d[3], "timing": "before_open"}]
        sessions = conditions.earnings_sessions(bars, reports)
        self.assertEqual(sorted(sessions), [2, 3])     # after close -> next session; before open -> same day

    def test_validate_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            conditions.validate({"type": "price_move", "params": {"threshold": 500}})


# ---------------------------------------------------------------------------
class TestDataQuality(unittest.TestCase):
    def test_unadjusted_split_is_corrected(self):
        closes = [400.0] * 10 + [100.0] * 10            # a 4-for-1 split the provider didn't adjust
        bars = {"date": trading_days(20), "open": closes[:], "high": closes[:], "low": closes[:], "close": closes[:], "volume": [1000] * 20}
        clean, report = dataquality.check_daily(bars, {bars["date"][10]: 4.0})
        self.assertAlmostEqual(clean["close"][9], 100.0)
        self.assertTrue(any(x["severity"] == dataquality.LIKELY_ERROR for x in report["issues"]))

    def test_bad_print_removed_real_move_kept(self):
        closes = [100.0] * 5 + [10.0] + [100.0] * 5 + [160.0] * 5      # one-day bad print, then a real +60% move
        bars = {"date": trading_days(16), "open": closes[:], "high": closes[:], "low": closes[:], "close": closes[:], "volume": [1000] * 16}
        clean, report = dataquality.check_daily(bars, {})
        self.assertNotIn(10.0, clean["close"])
        self.assertIn(160.0, clean["close"])                         # the real move survives
        severities = {x["check"]: x["severity"] for x in report["issues"] if x["date"]}
        self.assertIn(dataquality.INFO, severities.values())


# ---------------------------------------------------------------------------
class TestExperimentEngine(unittest.TestCase):
    def series(self):
        # flat at 100, a 10% drop on day 30, recovering to 105 ten days later; another drop on day 33
        closes = [100.0] * 80
        for t in range(30, 80):
            closes[t] = 90.0
        for t in range(40, 80):
            closes[t] = 105.0
        return closes

    def test_forward_return_and_non_overlap(self):
        closes = self.series()
        with FakeData({"TEST": make_bars(closes)}):
            r = experiments.run(definition([{"type": "price_move", "params": {"direction": "falls", "threshold": 5, "window": 1}}], horizon=10))
        complete = [e for e in r["events"] if e["status"] == "complete"]
        self.assertEqual(len(complete), 1)
        e = complete[0]
        self.assertEqual(e["entry_price"], 90.0)
        self.assertAlmostEqual(e["forward_return"], 105.0 / 90.0 - 1)
        self.assertEqual(r["qualifying_days"], 1)

    def test_episodes_never_overlap(self):
        closes = [100 * (0.97 ** (i % 3)) for i in range(200)]       # a qualifying drop every few days
        with FakeData({"TEST": make_bars(closes)}):
            r = experiments.run(definition([{"type": "price_move", "params": {"direction": "falls", "threshold": 2, "window": 1}}], horizon=7))
        idx = [r["data"]["first_date"]]  # noqa: F841
        dates = make_bars(closes)["date"]
        starts = [dates.index(e["trigger_date"]) for e in r["events"]]
        for a, b in zip(starts, starts[1:]):
            self.assertGreaterEqual(b - a, 8)                       # next start >= t + H + 1

    def test_pending_episode_excluded(self):
        closes = [100.0] * 60 + [90.0] * 3                           # drop 2 days before the end
        with FakeData({"TEST": make_bars(closes)}):
            r = experiments.run(definition([{"type": "price_move", "params": {"direction": "falls", "threshold": 5, "window": 1}}], horizon=10))
        self.assertEqual(r["events"][-1]["status"], "pending")
        self.assertIsNone(r["stats"])

    def test_no_look_ahead(self):
        """Changing prices AFTER a day must not change whether that day triggered."""
        closes = self.series()
        changed = closes[:45] + [c * 3 for c in closes[45:]]
        c = [{"type": "price_move", "params": {"direction": "falls", "threshold": 5, "window": 1}},
             {"type": "rsi", "params": {"op": "below", "level": 50, "period": 5}}]
        with FakeData({"TEST": make_bars(closes)}):
            a = experiments.prepare(experiments.normalize(definition(c, 5)))["combined"][:45]
        with FakeData({"TEST": make_bars(changed)}):
            b = experiments.prepare(experiments.normalize(definition(c, 5)))["combined"][:45]
        self.assertEqual(a, b)

    def test_baseline_is_every_window(self):
        closes = [100.0 + i for i in range(50)]
        with FakeData({"TEST": make_bars(closes)}):
            d = experiments.normalize(definition([{"type": "price_move", "params": {"direction": "rises", "threshold": 0.5, "window": 1}}], horizon=5))
            prep = experiments.prepare(d)
            base = experiments.baseline_returns(prep, 5)
        first = prep["first"]
        self.assertEqual(len(base), len(closes) - 5 - first)
        self.assertAlmostEqual(base[0], closes[first + 5] / closes[first] - 1)

    def test_split_adjusted_series_gives_economic_return(self):
        closes = [400.0] * 30 + [100.0] * 30                         # unadjusted 4:1 split = no real move
        bars = {"date": trading_days(60), "open": closes[:], "high": closes[:], "low": closes[:], "close": closes[:], "volume": [1000] * 60}
        clean, report = dataquality.check_daily(bars, {bars["date"][30]: 4.0})
        clean.update(symbol="TEST", interval="1d", adjusted=True, provider="synthetic", retrieved_at="x", quality=report, splits={})
        with FakeData({"TEST": clean}):
            r = experiments.run(definition([{"type": "price_move", "params": {"direction": "falls", "threshold": 20, "window": 1}}], horizon=5))
        self.assertIsNone(r["stats"])                                # no fake -75% crash

    def test_preview_shrinks_with_conditions(self):
        closes = [100 + 10 * ((i * 7919) % 13) / 13 for i in range(400)]
        vols = [1000 + 500 * ((i * 104729) % 5) for i in range(400)]
        with FakeData({"TEST": make_bars(closes, vols)}):
            p = experiments.preview(definition([
                {"type": "price_move", "params": {"direction": "falls", "threshold": 2, "window": 1}},
                {"type": "relative_volume", "params": {"op": "above", "multiple": 1.2, "lookback": 10}}], horizon=5))
        a, b = p["steps"]
        self.assertGreaterEqual(a["qualifying_days"], b["qualifying_days"])


# ---------------------------------------------------------------------------
class TestRobust(unittest.TestCase):
    def test_trimmed_and_winsorized(self):
        values = list(range(1, 20)) + [1000]                         # n = 20, 5% -> 1 each side
        trimmed, k = robust.trimmed_mean(values, 0.05)
        self.assertEqual(k, 1)
        self.assertAlmostEqual(trimmed, statistics.fmean(range(2, 20)))
        wins, _ = robust.winsorized_mean(values, 0.05)
        self.assertAlmostEqual(wins, statistics.fmean([2] + list(range(2, 20)) + [19]))

    def test_bootstrap_is_deterministic_and_brackets_mean(self):
        values = [0.01 * ((i * 37) % 11 - 5) for i in range(40)]
        a = robust.bootstrap_ci(values, statistics.fmean)
        b = robust.bootstrap_ci(values, statistics.fmean)
        self.assertEqual(a, b)
        self.assertLess(a[0], statistics.fmean(values))
        self.assertGreater(a[1], statistics.fmean(values))

    def test_contribution_shares(self):
        c = robust.contribution([10, 5, 3, 1, 1, -2, -1], ks=(1, 3))
        self.assertAlmostEqual(c["gains"][0]["share"], 10 / 20)
        self.assertAlmostEqual(c["gains"][1]["share"], 18 / 20)
        self.assertEqual(c["losses"], [{"k": 1, "share": 2 / 3}])  # only reported when more than k losses

    def test_ci_contains_mean(self):
        s = robust.summarize([0.01, 0.02, -0.01, 0.03, 0.0])
        self.assertLess(s["ci_low"], s["mean"])
        self.assertGreater(s["ci_high"], s["mean"])

    def test_outlier_sensitivity(self):
        values = [0.01] * 19 + [1.0]
        rb = robust.robust_stats(values)
        self.assertAlmostEqual(rb["mean_without_extreme"], 0.01)
        self.assertEqual(rb["most_extreme"]["value"], 1.0)


# ---------------------------------------------------------------------------
class TestChallenge(unittest.TestCase):
    def test_neighbor_values_respect_limits(self):
        spec = conditions.REGISTRY["price_move"]["params"]
        self.assertEqual(challenge.neighbor_values("threshold", 5, spec["threshold"]), [4.0, 5.0, 6.0])
        self.assertEqual(challenge.neighbor_values("window", 1, spec["window"]), [1, 2])     # clipped at the minimum
        self.assertEqual(challenge.neighbor_values("threshold", 20, spec["threshold"]), [16.0, 20.0, 24.0])

    def test_parameter_grid(self):
        d = experiments.normalize(definition([{"type": "price_move", "params": {}}]))
        grid = experiments.parameter_grid(d, {"conditions.0.threshold": [4, 5], "horizon": [5, 10]})
        self.assertEqual(len(grid), 4)
        labels, variant = grid[-1]
        self.assertEqual(variant["conditions"][0]["params"]["threshold"], 5)
        self.assertEqual(variant["outcome"]["horizon"], 10)

    def test_time_split_equal_counts(self):
        closes = [100 * (0.96 if (i // 9) % 2 else 1.0) for i in range(600)]
        with FakeData({"TEST": make_bars(closes)}):
            d = experiments.normalize(definition([{"type": "price_move", "params": {"direction": "falls", "threshold": 3, "window": 1}}], horizon=3))
            prep = experiments.prepare(d)
            eps, _, _ = experiments.detect_triggers(prep, 3)
            events = [e for e in experiments.measure_outcomes(prep, eps, 3) if e["status"] == "complete"]
            split = challenge.time_split(prep, 3, events)
        self.assertTrue(split["available"])
        a, b = split["parts"]
        self.assertLessEqual(abs(a["stats"]["n"] - b["stats"]["n"]), 1)
        self.assertLess(a["to"], b["from"])


if __name__ == "__main__":
    unittest.main()
