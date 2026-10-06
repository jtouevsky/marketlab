"""
Tests for the intraday engine: providers' normalization, data quality, sessions,
multi-timeframe synchronization, FVG detection and states, levels, swings,
sweeps, strategy matching, and — most importantly — look-ahead prevention.

Run:  python3 -m unittest discover tests -v
"""

import random
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import concept_library as C                     # noqa: E402
import research_projects as R                   # noqa: E402
from intraday import detectors as D             # noqa: E402
from intraday import engine, quality, sessions, strategy   # noqa: E402
from intraday.bars import normalize_row         # noqa: E402

TUE = date(2026, 9, 29)       # an ordinary Tuesday, outside any roll window


def ts_et(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    from datetime import time
    return sessions.from_et(day, time(h, m))


def rows_from(start, bars, step=60):
    """bars: list of (o, h, l, c) → normalized rows, one per minute from `start`."""
    return [normalize_row({"timestamp": start + i * step, "open": o, "high": h, "low": l, "close": c, "volume": 10},
                          "NQ", "1m", "test") for i, (o, h, l, c) in enumerate(bars)]


def flat(n, price=100.0):
    return [(price, price + 0.25, price - 0.25, price)] * n


def random_days(days, seed=7, start_price=20000.0):
    """Realistic-looking 1m candles over whole trading days (18:00 → 17:00 ET), halts respected."""
    rng = random.Random(seed)
    rows, price = [], start_price
    for d in days:
        start, end = sessions.day_bounds(d)
        for t in range(start, end, 60):
            if not sessions.standard_open(t):
                continue
            o = price
            c = round((o + rng.gauss(0, 4)) * 4) / 4
            h = max(o, c) + round(abs(rng.gauss(0, 2)) * 4) / 4
            l = min(o, c) - round(abs(rng.gauss(0, 2)) * 4) / 4
            rows.append({"timestamp": t, "open": o, "high": h, "low": l, "close": c, "volume": 5})
            price = c
    return [normalize_row(r, "NQ", "1m", "test") for r in rows]


def series_of(rows, interval="1m"):
    s, report = quality.inspect(rows, interval, symbol="NQ", provider="test")
    return s, report


class NormalizationTests(unittest.TestCase):
    def test_normalize_row(self):
        ok = normalize_row({"timestamp": 1, "open": 10, "high": 9, "low": 8, "close": 11, "volume": None}, "NQ", "1m", "p")
        self.assertEqual((ok["high"], ok["low"], ok["volume"]), (11, 8, 0.0))      # high/low repaired to contain o/c
        self.assertEqual(set(ok), {"timestamp", "open", "high", "low", "close", "volume", "symbol", "interval", "provider", "contract"})
        self.assertIsNone(normalize_row({"timestamp": 1, "open": 10, "high": 8, "low": 9, "close": 9}, "NQ", "1m", "p"))
        self.assertIsNone(normalize_row({"timestamp": 1, "open": float("nan"), "high": 8, "low": 7, "close": 7}, "NQ", "1m", "p"))
        self.assertIsNone(normalize_row({"timestamp": 1, "open": 0, "high": 1, "low": 0, "close": 1}, "NQ", "1m", "p"))


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.start = ts_et(TUE, "10:00")

    def test_duplicates_and_out_of_order(self):
        rows = rows_from(self.start, flat(10))
        dup_same = dict(rows[3])
        dup_diff = dict(rows[5], close=105.0, high=105.0)
        shuffled = [rows[1], rows[0]] + rows[2:] + [dup_same, dup_diff]
        s, report = series_of(shuffled)
        self.assertEqual(len(s), 10)
        self.assertEqual(s.ts, sorted(s.ts))
        self.assertEqual(report["counts"]["DUPLICATE CANDLE"], 2)
        self.assertIn("OUT-OF-ORDER DATA", report["counts"])
        self.assertIn("suspect", s.flags[5])          # conflicting copies make the minute suspect
        self.assertNotIn("suspect", s.flags[3])

    def test_missing_candles_are_reported_not_filled(self):
        rows = rows_from(self.start, flat(30))
        del rows[10:15]                               # a 5-minute hole
        s, report = series_of(rows)
        self.assertEqual(len(s), 25)                  # nothing interpolated
        self.assertEqual(report["missing_bars"], 5)
        i = s.ts.index(self.start + 15 * 60)
        self.assertIn("after_gap", s.flags[i])
        self.assertIn("suspect", s.flags[i])

    def test_daily_halt_is_not_missing(self):
        rows = rows_from(ts_et(TUE, "16:50"), flat(10)) + rows_from(ts_et(TUE, "18:00"), flat(10))
        _, report = series_of(rows)
        self.assertEqual(report["missing_bars"], 0)

    def test_stale_and_abnormal(self):
        bars = [(100 + i * 0.25, 100 + i * 0.25 + 0.5, 100 + i * 0.25 - 0.5, 100 + (i + 1) * 0.25) for i in range(40)]
        bars += [(110, 110, 110, 110)] * 20                       # stale: flat, zero volume
        bars += [(150, 151, 149, 150)]                            # abnormal jump
        rows = rows_from(self.start, bars)
        for r in rows[40:60]:
            r["volume"] = 0
        rows[40]["open"] = rows[40]["high"] = rows[40]["low"] = rows[40]["close"] = rows[39]["close"]
        for r in rows[41:60]:
            r["open"] = r["high"] = r["low"] = r["close"] = rows[39]["close"]
        s, report = series_of(rows)
        self.assertIn("STALE DATA", report["counts"])
        self.assertIn("ABNORMAL GAP", report["counts"])
        self.assertIn("suspect", s.flags[-1])

    def test_cross_check_flags_disagreeing_feeds(self):
        a, _ = series_of(rows_from(self.start, flat(20, 20000)))
        rows = rows_from(self.start, flat(20, 20000))
        for r in rows[5:8]:
            r["close"] = r["high"] = r["open"] = 20040.0
        b, _ = series_of(rows)
        flagged, info = quality.cross_check(a, b)
        self.assertEqual(flagged, 3)
        self.assertTrue(all("suspect" in a.flags[i] for i in (5, 6, 7)))


class SessionTests(unittest.TestCase):
    def test_trading_day_and_halts(self):
        sunday_evening = ts_et(date(2026, 9, 27), "18:30")
        self.assertEqual(sessions.trading_day(sunday_evening), date(2026, 9, 28))
        self.assertEqual(sessions.trading_day(ts_et(TUE, "16:00")), TUE)
        self.assertEqual(sessions.trading_day(ts_et(TUE, "18:00")), date(2026, 9, 30))
        self.assertFalse(sessions.standard_open(ts_et(TUE, "17:30")))
        self.assertFalse(sessions.standard_open(ts_et(date(2026, 9, 19), "12:00")))   # Saturday
        self.assertTrue(sessions.standard_open(ts_et(TUE, "03:00")))

    def test_dst_is_handled_by_the_timezone(self):
        summer = ts_et(date(2026, 7, 7), "09:30") - ts_et(date(2026, 7, 7), "00:00")
        winter = ts_et(date(2026, 12, 8), "09:30") - ts_et(date(2026, 12, 8), "00:00")
        self.assertEqual(summer, winter)              # same wall-clock distance
        utc_summer = sessions.to_et(ts_et(date(2026, 7, 7), "09:30")).utcoffset().total_seconds()
        utc_winter = sessions.to_et(ts_et(date(2026, 12, 8), "09:30")).utcoffset().total_seconds()
        self.assertEqual(utc_summer - utc_winter, 3600)

    def test_windows(self):
        self.assertTrue(sessions.in_window(ts_et(TUE, "10:15"), "09:30", "11:00"))
        self.assertFalse(sessions.in_window(ts_et(TUE, "11:00"), "09:30", "11:00"))
        self.assertTrue(sessions.in_window(ts_et(TUE, "02:00"), "18:00", "09:30"))   # wraps midnight


class TimeframeTests(unittest.TestCase):
    def test_resample_and_visibility(self):
        start = ts_et(TUE, "10:00")
        bars = [(100 + i, 100.5 + i, 99.5 + i, 100.25 + i) for i in range(10)]
        s, _ = series_of(rows_from(start, bars))
        five = engine.resample(s, "5m")
        self.assertEqual(len(five), 2)
        self.assertEqual((five.open[0], five.high[0], five.low[0], five.close[0]), (100, 104.5, 99.5, 104.25))
        ctx = engine.TimeframeContext(five)
        self.assertEqual(ctx.last_closed(start + 299), -1)        # first 5m candle not closed yet
        self.assertEqual(ctx.last_closed(start + 300), 0)
        hour = engine.TimeframeContext(engine.resample(s, "1h"))
        self.assertIn("partial", hour.series.flags[0])            # 10 of 60 minutes
        self.assertIn("suspect", hour.series.flags[0])

    def test_one_engine_any_interval(self):
        start = ts_et(TUE, "10:00")
        s, _ = quality.inspect([dict(r, interval="5s") for r in rows_from(start, flat(24), step=5)], "5s", symbol="NQ")
        self.assertEqual(len(engine.resample(s, "1m")), 2)       # second-level data uses the same code


def fvg_bars():
    """A clean bullish FVG (candle 1 high 101 < candle 3 low 103), then entry, half fill, validation."""
    bars = flat(5, 100)
    bars += [(100, 101, 99.5, 100.5), (100.5, 104, 100.5, 103.75), (103.75, 105, 103, 104.5)]   # c1, c2, c3 → gap 101–103
    bars += [(104.5, 104.75, 102.5, 103.5)]      # enters (low 102.5 ≤ 103), 25% filled, closes back above? 103.5 > 103 → validates
    bars += [(103.5, 103.75, 101.75, 101.9)]     # half filled (low 101.75 ≤ 102), closes just under the midpoint
    bars += [(101.9, 102.25, 100.5, 100.75)]     # trades and closes below 101 → fully filled + invalidated (close rule)
    bars += flat(5, 100.75)
    return bars


class FvgTests(unittest.TestCase):
    def setUp(self):
        self.start = ts_et(TUE, "10:00")
        s, _ = series_of(rows_from(self.start, fvg_bars()))
        self.ctx = engine.TimeframeContext(s)
        self.params = C.default_params("fvg") | {"timeframe": "1m"}

    def test_detects_with_exact_bounds_and_timing(self):
        fvgs, _ = D.detect_fvgs(self.ctx, self.params)
        bull = [f for f in fvgs if f["direction"] == "bullish" and f["bottom"] == 101]
        self.assertEqual(len(bull), 1)
        f = bull[0]
        self.assertEqual((f["bottom"], f["top"], f["size"]), (101, 103, 2))
        self.assertEqual(f["created_at"], self.start + 8 * 60)         # close of candle 3 (index 7)

    def test_state_sequence(self):
        f = next(f for f in D.detect_fvgs(self.ctx, self.params)[0] if f["bottom"] == 101)
        names = [s for s, _ in f["states"]]
        for state in ("CREATED", "ENTERED", "PARTIALLY_FILLED", "VALIDATED", "HALF_FILLED", "FULLY_FILLED", "INVALIDATED"):
            self.assertIn(state, names)
        self.assertEqual(f["entered_at"], self.start + 9 * 60)
        self.assertEqual(f["validated_at"], self.start + 9 * 60)       # entered and closed back above in the same candle
        self.assertEqual(f["half_at"], self.start + 10 * 60)
        self.assertEqual(f["invalidated_at"], self.start + 11 * 60)
        self.assertIsNone(f["expired_at"])
        self.assertEqual(D.fvg_state_at(f, self.start + 8 * 60), "CREATED")
        self.assertIsNone(D.fvg_state_at(f, self.start + 8 * 60 - 1))  # not before candle 3 closes

    def test_minimum_size_and_rules(self):
        big = D.detect_fvgs(self.ctx, self.params | {"min_gap": 3})[0]
        self.assertFalse([f for f in big if f["bottom"] == 101])
        mid = next(f for f in D.detect_fvgs(self.ctx, self.params | {"invalidation_rule": "close_beyond_mid"})[0] if f["bottom"] == 101)
        self.assertEqual(mid["invalidated_at"], self.start + 10 * 60)  # the 101.9 close is beyond the midpoint (102)
        trade = next(f for f in D.detect_fvgs(self.ctx, self.params | {"invalidation_rule": "trade_beyond_far"})[0] if f["bottom"] == 101)
        self.assertEqual(trade["invalidated_at"], self.start + 11 * 60)

    def test_age_expiry(self):
        bars = fvg_bars()[:8] + flat(10, 104.5)        # never comes back into the gap
        s, _ = series_of(rows_from(self.start, bars))
        f = next(f for f in D.detect_fvgs(engine.TimeframeContext(s), self.params | {"max_age_bars": 3})[0] if f["bottom"] == 101)
        self.assertEqual(f["expired_at"], self.start + 11 * 60)
        self.assertIn("age", f["end_reason"])

    def test_no_fvg_across_missing_data(self):
        rows = rows_from(self.start, fvg_bars())
        del rows[6]                                   # remove candle 2: candles 1 and 3 are no longer contiguous
        s, _ = series_of(rows)
        self.assertFalse([f for f in D.detect_fvgs(engine.TimeframeContext(s), self.params)[0]
                          if f["direction"] == "bullish" and (f["bottom"], f["top"]) == (101, 103)])


class LevelTests(unittest.TestCase):
    def setUp(self):
        self.days = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]
        self.rows = random_days(self.days)
        self.s, _ = series_of(self.rows)

    def test_pdh_pdl_from_previous_day_usable_next_day(self):
        levels = D.reference_levels(self.s)
        day1 = [r for r in self.rows if sessions.trading_day(r["timestamp"]) == self.days[0]]
        pdh = next(l for l in levels if l["kind"] == "pdh" and l["day"] == str(self.days[1]))
        self.assertEqual(pdh["price"], max(r["high"] for r in day1))
        self.assertEqual(pdh["available_from"], sessions.day_bounds(self.days[1])[0])
        self.assertFalse([l for l in levels if l["day"] == str(self.days[0]) and l["kind"] in ("pdh", "pdl")])   # no earlier day in data

    def test_overnight_levels_final_at_0930(self):
        levels = D.reference_levels(self.s)
        onl = next(l for l in levels if l["kind"] == "onl" and l["day"] == str(self.days[1]))
        start, _ = sessions.day_bounds(self.days[1])
        overnight = [r for r in self.rows if start <= r["timestamp"] < ts_et(self.days[1], "09:30")]
        self.assertEqual(onl["price"], min(r["low"] for r in overnight))
        self.assertEqual(onl["available_from"], ts_et(self.days[1], "09:30"))

    def test_swings_are_confirmed_after_right_candles(self):
        ctx = engine.TimeframeContext(engine.resample(self.s, "5m"))
        for sw in D.swings(ctx, 3, 3)[:20]:
            self.assertEqual(sw["confirmed_at"], ctx.close_times[sw["index"] + 3])
            self.assertGreater(sw["confirmed_at"], sw["occurred_at"])


class SweepTests(unittest.TestCase):
    def build(self, after):
        """Previous day with low 100, today's overnight above it, then `after` candles from 09:30."""
        d1, d2 = date(2026, 9, 29), date(2026, 9, 30)
        rows = []
        for d, base in ((d1, 101.0), (d2, 102.0)):
            start, end = sessions.day_bounds(d)
            for t in range(start, end, 60):
                if sessions.standard_open(t):
                    rows.append({"timestamp": t, "open": base, "high": base + 0.5, "low": base - 0.5, "close": base, "volume": 1})
        rows[0]["low"] = 100.0                         # day 1 low = 100 → PDL for day 2
        t0 = ts_et(d2, "10:00")
        idx = next(i for i, r in enumerate(rows) if r["timestamp"] == t0)
        for k, (o, h, l, c) in enumerate(after):
            rows[idx + k].update(open=o, high=h, low=l, close=c)
        s, _ = series_of([normalize_row(r, "NQ", "1m", "test") for r in rows])
        return s, t0

    def test_sweep_confirmed_on_reclaim_close(self):
        s, t0 = self.build([(102, 102, 99.25, 99.5), (99.5, 100.75, 99.5, 100.5)])   # breaks 100 by 3 ticks, reclaims next candle
        ctx = engine.TimeframeContext(s)
        sweeps = D.detect_sweeps(s, ctx, {"side": "sell-side", "levels": ["pdl"], "penetration_ticks": 2, "reclaim_bars": 3,
                                         "timeframe": "1m"})
        self.assertEqual(len(sweeps), 1)
        sw = sweeps[0]
        self.assertEqual((sw["level_kind"], sw["level_price"], sw["direction"]), ("pdl", 100.0, "bullish"))
        self.assertEqual(sw["occurred_at"], t0)
        self.assertEqual(sw["confirmed_at"], t0 + 120)        # close of the reclaim candle, not before
        self.assertEqual(sw["extreme"], 99.25)

    def test_no_reclaim_is_not_a_sweep(self):
        s, _ = self.build([(102, 102, 99.25, 99.5)] + [(99.5, 99.75, 99.0, 99.25)] * 5)
        sweeps = D.detect_sweeps(s, engine.TimeframeContext(s), {"side": "sell-side", "levels": ["pdl"], "penetration_ticks": 2,
                                                                  "reclaim_bars": 3, "timeframe": "1m"})
        self.assertEqual(sweeps, [])

    def test_too_shallow_or_too_deep(self):
        s, _ = self.build([(102, 102, 99.9, 100.5)])           # only 0.1 below: under 2 ticks
        self.assertEqual(D.detect_sweeps(s, engine.TimeframeContext(s), {"side": "sell-side", "levels": ["pdl"], "penetration_ticks": 2,
                                                                          "reclaim_bars": 3, "timeframe": "1m"}), [])
        s, _ = self.build([(102, 102, 95, 100.5)])             # 5 points below with a 10-tick maximum: breakout
        self.assertEqual(D.detect_sweeps(s, engine.TimeframeContext(s), {"side": "sell-side", "levels": ["pdl"], "penetration_ticks": 2,
                                                                          "max_penetration_ticks": 10, "reclaim_bars": 3, "timeframe": "1m"}), [])


class LookAheadTests(unittest.TestCase):
    """Cutting the data at time T must not change anything that was known at T."""

    def setUp(self):
        self.days = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]
        self.rows = random_days(self.days, seed=11)

    def truncated(self, cut):
        s, _ = series_of([r for r in self.rows if r["timestamp"] + 60 <= cut])
        return s

    def known(self, events, cut, key="confirmed_at"):
        return sorted((e[key], round(e.get("top", e.get("price", e.get("level_price", 0))), 2)) for e in events if e[key] <= cut)

    def test_detectors_unchanged_by_future_data(self):
        full, _ = series_of(self.rows)
        for cut in (ts_et(self.days[1], "10:37"), ts_et(self.days[2], "03:11"), ts_et(self.days[2], "13:44")):
            part = self.truncated(cut)
            for tf in ("1m", "5m", "1h"):
                p = C.default_params("fvg") | {"timeframe": tf}
                a = D.detect_fvgs(engine.TimeframeContext(engine.resample(full, tf)), p)[0]
                b = D.detect_fvgs(engine.TimeframeContext(engine.resample(part, tf)), p)[0]
                self.assertEqual(self.known(a, cut, "created_at"), self.known(b, cut, "created_at"), (tf, cut))
                # every state transition known by `cut` is identical
                sa = sorted((f["created_at"], st, at) for f in a for st, at in f["states"] if at <= cut)
                sb = sorted((f["created_at"], st, at) for f in b for st, at in f["states"] if at <= cut)
                self.assertEqual(sa, sb, (tf, cut))
            ctx_a = engine.TimeframeContext(engine.resample(full, "5m"))
            ctx_b = engine.TimeframeContext(engine.resample(part, "5m"))
            self.assertEqual(self.known(D.swings(ctx_a), cut), self.known(D.swings(ctx_b), cut))
            sp = {"side": "both", "levels": ["pdl", "onl", "swingl", "pdh", "onh", "swingh"], "penetration_ticks": 2,
                  "reclaim_bars": 3, "timeframe": "5m"}
            self.assertEqual(self.known(D.detect_sweeps(full, ctx_a, sp), cut), self.known(D.detect_sweeps(part, ctx_b, sp), cut))
            la = [l for l in D.reference_levels(full) if l["available_from"] <= cut]
            lb = [l for l in D.reference_levels(part) if l["available_from"] <= cut]
            self.assertEqual(sorted((l["kind"], l["price"]) for l in la), sorted((l["kind"], l["price"]) for l in lb))

    def test_no_event_is_usable_before_it_is_confirmed(self):
        full, _ = series_of(self.rows)
        ctx = engine.TimeframeContext(engine.resample(full, "5m"))
        for f in D.detect_fvgs(ctx, C.default_params("fvg") | {"timeframe": "5m"})[0]:
            self.assertGreaterEqual(f["created_at"], f["c1"] + 3 * 300)
            for _, at in f["states"]:
                self.assertGreaterEqual(at, f["created_at"])
        for sw in D.detect_sweeps(full, ctx, {"side": "both", "levels": ["swingl", "swingh"], "timeframe": "5m"}):
            self.assertGreater(sw["confirmed_at"], sw["occurred_at"])
            self.assertGreaterEqual(sw["occurred_at"], sw["level_known_at"])


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = R.RESEARCH_DIR
        R.RESEARCH_DIR = Path(self.tmp.name)
        self.days = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]
        self.rows = random_days(self.days, seed=5)

    def tearDown(self):
        R.RESEARCH_DIR = self.saved
        self.tmp.cleanup()

    def project(self):
        p = R.create({"observation": "NQ 5m FVG validates after a sweep", "instrument": "NQ"})
        pid = p["id"]
        p = R.add_item(pid, "definitions", {"concept": "liquidity_sweep", "params": {"timeframe": "5m", "side": "both"}})
        p = R.add_item(pid, "definitions", {"concept": "fvg", "params": {"timeframe": "5m"}})
        d = [x["id"] for x in p["definitions"]]
        R.add_item(pid, "conditions", {"definition_id": d[0], "timing": "any time earlier in the same session"})
        R.add_item(pid, "conditions", {"definition_id": d[1], "requirement": "VALIDATED", "timing": "at the setup candle"})
        R.add_item(pid, "entries", {"kind": "validation_close"})
        R.add_item(pid, "stops", {"kind": "fixed_points", "params": {"points": 10}})
        R.add_item(pid, "exits", {"kind": "r_multiple", "params": {"r": 1}})
        return R.raw(pid)

    def dataset(self, rows):
        from intraday.dataset import Dataset
        s, report = series_of(rows)
        return Dataset("NQ", "1m", "yahoo", s, report, engine.build_contexts(s, ["5m"]), None, None)

    def test_setups_and_trades_never_use_the_future(self):
        project = self.project()
        full = strategy.run(project, self.dataset(self.rows))
        self.assertGreater(full["setups"], 0)
        cut = ts_et(self.days[2], "12:00")
        part = strategy.run(project, self.dataset([r for r in self.rows if r["timestamp"] + 60 <= cut]))
        before = lambda r: [(s["time"], s["direction"]) for s in r["setup_list"] if s["time"] <= cut]
        self.assertEqual(before(full), before(part))
        for t in full["primary"]["trades"]:
            self.assertGreaterEqual(t["entry_time"], t["setup_time"])     # entry only once eligible
            self.assertGreater(t["exit_time"], t["entry_time"])

    def test_trade_math_and_costs(self):
        project = self.project()
        r = strategy.run(project, self.dataset(self.rows))
        for t in r["primary"]["trades"]:
            sign = 1 if t["direction"] == "bullish" else -1
            self.assertAlmostEqual(t["points"], round((t["exit"] - t["entry"]) * sign, 2), places=2)
            self.assertAlmostEqual(t["gross"] - t["net"], r["primary"]["stats"]["costs_round_trip"], places=2)
        m = r["primary"]["metrics"]
        if m["n"]:
            self.assertEqual(m["wins"] + m["losses"] + m["breakeven"], m["n"])
            self.assertEqual(m["unit"], "R")

    def test_needs_an_event_condition(self):
        p = R.create({"observation": "x"})
        pid = p["id"]
        p = R.add_item(pid, "definitions", {"concept": "fvg", "params": {"timeframe": "5m"}})
        R.add_item(pid, "conditions", {"definition_id": p["definitions"][0]["id"], "requirement": "ACTIVE"})
        R.add_item(pid, "entries", {"kind": "validation_close"}); R.add_item(pid, "stops", {"kind": "fixed_points"})
        R.add_item(pid, "exits", {"kind": "fixed_points"})
        with self.assertRaises(strategy.StrategyError):
            strategy.run(R.raw(pid), self.dataset(self.rows))


if __name__ == "__main__":
    unittest.main()
