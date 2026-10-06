"""
Tests for the expanded concept engine and the multi-source data layer:
displacement, BOS / CHoCH / MSS, candle / momentum / volume / volatility events,
weekly levels, opening range, VWAP, look-ahead safety of all of them,
multi-source assembly (provenance, explicit rolls, gap filling, source disagreement),
resolution aggregation and the automatic base resolution.

Run:  python3 -m unittest discover tests -v
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import concept_library as C                     # noqa: E402
from intraday import composite, concepts as K, engine, quality, sessions   # noqa: E402
from intraday import dataset as DS             # noqa: E402
from intraday.bars import normalize_row         # noqa: E402
from test_intraday import TUE, random_days, rows_from, series_of, ts_et   # noqa: E402

DAYS = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]


def ctx_of(rows, tf="1m"):
    s, _ = series_of(rows)
    return engine.TimeframeContext(engine.resample(s, tf)), s


def quiet(n, price=100.0):
    """n small alternating candles (ATR ≈ 1 point)."""
    out = []
    for i in range(n):
        o = price
        c = price + (0.25 if i % 2 else -0.25)
        out.append((o, max(o, c) + 0.25, min(o, c) - 0.25, c))
        price = c
    return out


class DisplacementTests(unittest.TestCase):
    def test_large_decisive_candle_is_displacement_known_at_its_close(self):
        bars = quiet(20) + [(100.0, 106.0, 99.9, 105.8)] + quiet(3, 105.8)
        ctx, s = ctx_of(rows_from(ts_et(TUE, "10:00"), bars))
        ev = K.displacement(ctx, C.default_params("displacement"))
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual(e["direction"], "bullish")
        self.assertEqual(e["occurred_at"], ts_et(TUE, "10:20"))
        self.assertEqual(e["confirmed_at"], ts_et(TUE, "10:21"))          # the candle's close, never earlier

    def test_body_and_close_location_rules(self):
        # big range but closes in the middle (long upper wick) → not displacement
        bars = quiet(20) + [(100.0, 106.0, 99.9, 102.5)] + quiet(3, 102.5)
        ctx, _ = ctx_of(rows_from(ts_et(TUE, "10:00"), bars))
        self.assertEqual(K.displacement(ctx, C.default_params("displacement")), [])

    def test_require_fvg_delays_confirmation_by_one_candle(self):
        bars = quiet(20) + [(100.0, 106.0, 99.9, 105.8), (105.8, 107.0, 105.5, 106.5)] + quiet(3, 106.5)
        ctx, _ = ctx_of(rows_from(ts_et(TUE, "10:00"), bars))
        ev = K.displacement(ctx, C.default_params("displacement") | {"require_fvg": True})
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["confirmed_at"], ts_et(TUE, "10:22"))       # candle 3 of the gap must have closed
        self.assertIsNotNone(ev[0]["fvg"])

    def test_never_spans_suspect_data(self):
        bars = quiet(20) + [(100.0, 106.0, 99.9, 105.8)] + quiet(3, 105.8)
        rows = rows_from(ts_et(TUE, "10:00"), bars)
        rows[20]["_disagree"] = "test"
        ctx, _ = ctx_of(rows)
        self.assertEqual(K.displacement(ctx, C.default_params("displacement")), [])


class StructureTests(unittest.TestCase):
    """BOS / CHoCH / MSS with explicit swing confirmation."""

    def bars(self):
        p = 100.0
        out = []
        # rise to a swing high at 110 (index 5), fall to a swing low at 104 (index 11), break above 110 (BOS),
        # then fall through the new swing low (CHoCH).
        path = [101, 103, 105, 107, 109, 110, 108, 107, 106, 105, 104.5, 104, 105, 106, 107, 108, 109, 111, 112, 113,
                112, 111, 110, 109, 108, 106, 104, 103, 101, 100]
        for c in path:
            o = (p + c) / 2                  # opens between the previous close and its own close, so peaks are unique
            out.append((o, max(o, c) + 0.25, min(o, c) - 0.25, float(c)))
            p = float(c)
        return out

    def test_breaks_only_after_the_swing_is_confirmed(self):
        ctx, s = ctx_of(rows_from(ts_et(TUE, "10:00"), self.bars()))
        events = K.structure(ctx, C.default_params("market_structure_break") | {"swing_left": 2, "swing_right": 2})
        self.assertTrue(events)
        for e in events:
            self.assertLessEqual(e["swing_known_at"], e["occurred_at"])      # the swing was known before the breaking candle opened
            self.assertEqual(e["confirmed_at"], e["occurred_at"] + 60)
        first_bull = next(e for e in events if e["direction"] == "bullish")
        self.assertGreater(s.close[(first_bull["occurred_at"] - s.ts[0]) // 60], first_bull["level"])

    def test_bos_choch_and_type_filter(self):
        ctx, _ = ctx_of(rows_from(ts_et(TUE, "10:00"), self.bars()))
        base = C.default_params("market_structure_break") | {"swing_left": 2, "swing_right": 2}
        types = [e["type"] for e in K.structure(ctx, base)]
        self.assertIn("break", types)                 # the first break: trend not known yet
        self.assertTrue(any(t in ("CHoCH", "MSS") for t in types))
        only_choch = K.structure(ctx, base | {"type": "choch"})
        self.assertTrue(all(e["type"] in ("CHoCH", "MSS") for e in only_choch))
        only_bull = K.structure(ctx, base | {"direction": "bullish"})
        self.assertTrue(all(e["direction"] == "bullish" for e in only_bull))

    def test_mss_needs_a_displacement_candle(self):
        ctx, _ = ctx_of(rows_from(ts_et(TUE, "10:00"), self.bars()))
        base = C.default_params("market_structure_break") | {"swing_left": 2, "swing_right": 2}
        strict = K.structure(ctx, base | {"type": "mss", "mss_atr_mult": 50})
        self.assertEqual(strict, [])


class OtherEventTests(unittest.TestCase):
    def test_candle_engulfing(self):
        bars = quiet(20) + [(100.0, 100.3, 99.0, 99.2), (99.1, 101.2, 99.0, 101.0)]
        ctx, _ = ctx_of(rows_from(ts_et(TUE, "10:00"), bars))
        ev = K.candle(ctx, C.default_params("candle") | {"engulfing": True})
        self.assertEqual([e["direction"] for e in ev][-1:], ["bullish"])
        self.assertEqual(ev[-1]["confirmed_at"], ts_et(TUE, "10:22"))

    def test_directional_move_first_candle_of_a_run(self):
        bars = quiet(20) + [(100 + i, 101.2 + i, 99.9 + i, 101 + i) for i in range(8)]
        ctx, _ = ctx_of(rows_from(ts_et(TUE, "10:00"), bars))
        ev = K.directional_move(ctx, C.default_params("directional_move") | {"lookback": 4, "min_atr": 2})
        bulls = [e for e in ev if e["direction"] == "bullish"]
        self.assertEqual(len(bulls), 1)

    def test_volume_spike_excludes_the_candle_itself(self):
        rows = rows_from(ts_et(TUE, "10:00"), quiet(25))
        for r in rows:
            r["volume"] = 10
        rows[24]["volume"] = 50
        ctx, _ = ctx_of(rows)
        ev = K.volume_spike(ctx, C.default_params("volume_spike"))
        self.assertEqual(len(ev), 1)
        self.assertAlmostEqual(ev[0]["ratio"], 5.0)

    def test_weekly_levels_need_a_complete_previous_week(self):
        week1 = [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)]
        rows = random_days(week1 + [date(2026, 9, 28)], seed=3)
        s, _ = series_of(rows)
        levels = K.weekly_levels(s)
        kinds = {l["kind"] for l in levels}
        self.assertEqual(kinds, {"pwh", "pwl"})
        pwh = next(l for l in levels if l["kind"] == "pwh")
        self.assertEqual(pwh["available_from"], sessions.day_bounds(date(2026, 9, 28))[0])
        self.assertEqual(pwh["price"], max(s.high[i] for i in range(len(s)) if s.day[i] in week1 and not s.suspect(i)))

    def test_opening_range_known_only_after_it_ends(self):
        rows = random_days([TUE], seed=9)
        s, _ = series_of(rows)
        ranges = K.opening_ranges(s, 15)
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["available_from"], ts_et(TUE, "09:45"))
        ctx = engine.TimeframeContext(engine.resample(s, "5m"))
        for e in K.opening_range_breakouts(s, ctx, 15):
            self.assertGreaterEqual(e["occurred_at"], ts_et(TUE, "09:45"))

    def test_vwap_uses_only_closed_candles(self):
        rows = random_days([TUE], seed=4)
        s, _ = series_of(rows)
        v = K.Vwap(s, "rth")
        self.assertIsNone(v.at(ts_et(TUE, "09:30")))            # nothing closed in the session yet
        t = ts_et(TUE, "09:32")
        idx = [i for i in range(len(s)) if ts_et(TUE, "09:30") <= s.ts[i] and s.ts[i] + 60 <= t]
        tp = [(s.high[i] + s.low[i] + s.close[i]) / 3 for i in idx]
        expected = sum(tp[k] * s.volume[i] for k, i in enumerate(idx)) / sum(s.volume[i] for i in idx)
        self.assertAlmostEqual(v.at(t), expected)


class ConceptLookAheadTests(unittest.TestCase):
    """Cutting the data at T must not change any event confirmed by T, for every new detector."""

    def test_new_detectors_unchanged_by_future_data(self):
        rows = random_days(DAYS, seed=21)
        full, _ = series_of(rows)
        for cut in (ts_et(DAYS[1], "10:37"), ts_et(DAYS[2], "11:11")):
            part, _ = series_of([r for r in rows if r["timestamp"] + 60 <= cut])
            for tf in ("1m", "5m"):
                a, b = engine.TimeframeContext(engine.resample(full, tf)), engine.TimeframeContext(engine.resample(part, tf))
                for name, fn, params in (
                        ("displacement", K.displacement, C.default_params("displacement") | {"atr_mult": 1.2}),
                        ("structure", K.structure, C.default_params("market_structure_break")),
                        ("candle", K.candle, C.default_params("candle") | {"engulfing": True}),
                        ("move", K.directional_move, C.default_params("directional_move")),
                        ("volexp", K.volatility_expansion, C.default_params("volatility_expansion"))):
                    ea = sorted((e["confirmed_at"], e["direction"], e.get("type")) for e in fn(a, params) if e["confirmed_at"] <= cut)
                    eb = sorted((e["confirmed_at"], e["direction"], e.get("type")) for e in fn(b, params) if e["confirmed_at"] <= cut)
                    self.assertEqual(ea, eb, (name, tf, cut))
                    for e in fn(a, params):
                        self.assertGreater(e["confirmed_at"], e["occurred_at"])


# ---------------------------------------------------------------- multi-source data

def bars_for(day, start, end, price, contract=None, step=60, volume=10):
    rows = []
    t, p = ts_et(day, start), price
    stop = ts_et(day, end)
    while t < stop:
        rows.append(normalize_row({"timestamp": t, "open": p, "high": p + 1, "low": p - 1, "close": p + 0.25, "volume": volume},
                                  "NQ", "1m", "test", contract=contract))
        p += 0.25
        t += step
    return rows


class CompositeTests(unittest.TestCase):
    """composite.assemble: identification, the volume roll rule, filling, disagreement, provenance."""

    def setUp(self):
        self.d1, self.d2 = date(2026, 9, 29), date(2026, 9, 30)

    def test_roll_rule_identification_and_provenance(self):
        # Day 1: the continuous series is an expired contract (different prices); the new contract trades little.
        # Day 2: the continuous series equals NQZ26 and NQZ26 is the volume leader → roll.
        old = bars_for(self.d1, "09:30", "10:00", 20000, volume=100)
        z_day1 = bars_for(self.d1, "09:30", "10:00", 20300, contract="NQZ26", volume=5)
        z_day2 = bars_for(self.d2, "09:30", "10:00", 20400, contract="NQZ26", volume=100)
        cont_day2 = [dict(r, contract=None) for r in z_day2]
        rows, info = composite.assemble("NQ", "1m", old + cont_day2, {"NQZ26": z_day1 + z_day2}, [])
        self.assertEqual([r["contract"] for r in rows if sessions.trading_day(r["timestamp"]) == self.d1], [None] * 30)
        self.assertTrue(all(r["contract"] == "NQZ26" for r in rows if sessions.trading_day(r["timestamp"]) == self.d2))
        self.assertEqual(len(info["rolls"]), 1)
        self.assertEqual(info["rolls"][0]["to"], "NQZ26")
        self.assertEqual([s["contract"] for s in info["segments"]], [None, "NQZ26"])
        # the explicit roll becomes a regime break: nothing crosses it
        s, report = quality.inspect(rows, "1m", symbol="NQ")
        self.assertEqual(len(s.meta["breaks"]), 1)
        self.assertEqual(report["counts"].get("CONTRACT ROLL"), 1)
        self.assertNotIn("POSSIBLE ROLL ARTIFACT", report["counts"])

    def test_never_rolls_backwards(self):
        z = bars_for(self.d1, "09:30", "10:00", 20300, contract="NQZ26", volume=100)
        h_day2 = bars_for(self.d2, "09:30", "10:00", 20500, contract="NQH27", volume=500)
        z_day2 = bars_for(self.d2, "09:30", "10:00", 20300, contract="NQZ26", volume=50)
        rows, info = composite.assemble("NQ", "1m", [], {"NQZ26": z + z_day2, "NQH27": h_day2}, [])
        self.assertEqual(info["rolls"][-1]["to"], "NQH27")
        z_back = bars_for(date(2026, 10, 1), "09:30", "10:00", 20300, contract="NQZ26", volume=900)
        h_low = bars_for(date(2026, 10, 1), "09:30", "10:00", 20500, contract="NQH27", volume=10)
        rows, info = composite.assemble("NQ", "1m", [], {"NQZ26": z + z_day2 + z_back, "NQH27": h_day2 + h_low}, [])
        last_day = [r["contract"] for r in rows if sessions.trading_day(r["timestamp"]) == date(2026, 10, 1)]
        self.assertTrue(all(c == "NQH27" for c in last_day))

    def test_gap_fill_only_from_a_matching_source_and_disagreement_flagged(self):
        z = bars_for(self.d1, "09:30", "10:00", 20300, contract="NQZ26")
        frd = [dict(r, contract=None, source="firstrate") for r in z]
        hole = z.pop(10)                                      # Yahoo lacks one minute
        frd[20] = dict(frd[20], close=frd[20]["close"] + 5, high=frd[20]["high"] + 5)    # FirstRate disagrees on one minute
        rows, info = composite.assemble("NQ", "1m", [], {"NQZ26": z}, frd)
        filled = [r for r in rows if r["timestamp"] == hole["timestamp"]]
        self.assertEqual(filled[0]["source"], "firstrate")
        self.assertEqual(filled[0]["contract"], "NQZ26")
        bad = [r for r in rows if r.get("_disagree")]
        self.assertEqual(len(bad), 1)
        s, report = quality.inspect(rows, "1m", symbol="NQ")
        self.assertEqual(report["counts"].get("SOURCE DISAGREEMENT"), 1)
        self.assertTrue(any("suspect" in f for f in s.flags))
        self.assertEqual(info["segments"][0]["filled"].get("FirstRate sample"), 1)

    def test_incompatible_source_is_not_spliced(self):
        z = bars_for(self.d1, "09:30", "10:00", 20300, contract="NQZ26")
        other = bars_for(self.d1, "09:30", "10:00", 20000)        # a different contract month
        z.pop(5)
        rows, info = composite.assemble("NQ", "1m", [], {"NQZ26": z}, other)
        self.assertFalse(any(r["source"] == "firstrate" for r in rows))
        self.assertTrue(info["notes"])

    def test_resampling_keeps_contract_and_never_builds_finer_data(self):
        z = bars_for(self.d1, "09:30", "10:30", 20300, contract="NQZ26")
        s, _ = quality.inspect(z, "1m", symbol="NQ")
        five = engine.resample(s, "5m")
        self.assertEqual(set(five.contracts), {"NQZ26"})
        self.assertEqual(len(five), 12)
        with self.assertRaises(ValueError):
            engine.resample(five, "1m")


class AutoBaseTests(unittest.TestCase):
    def test_coarsest_base_that_builds_every_timeframe(self):
        p = lambda tfs, execution=None: {"definitions": [{"params": {"timeframe": tf}} for tf in tfs],
                                         "timeframes": {"roles": {"execution": execution}}}
        self.assertEqual(DS.auto_base(p(["1h", "5m"]))[0], "5m")
        self.assertEqual(DS.auto_base(p(["1h", "5m"], "1m"))[0], "1m")
        self.assertEqual(DS.auto_base(p(["1h", "4h"]))[0], "1h")
        self.assertEqual(DS.auto_base(p(["15m", "1h"]))[0], "15m")
        self.assertEqual(DS.auto_base(p([]))[0], "1m")

    def test_seconds_are_never_offered_without_a_second_level_source(self):
        z = bars_for(date(2026, 9, 29), "09:30", "10:00", 20300, contract="NQZ26")
        s, report = quality.inspect(z, "1m", symbol="NQ")
        ds = DS.Dataset("NQ", "1m", "auto", s, report, engine.build_contexts(s, ["5m"]), None, None)
        ins = DS.inspector(ds)
        off = {iv["interval"] for iv in ins["intervals"] if not iv["available"]}
        self.assertTrue({"tick", "1s", "5s", "15s", "30s"} <= off)
        self.assertNotEqual(ins["status"]["label"], "LIVE")


if __name__ == "__main__":
    unittest.main()
