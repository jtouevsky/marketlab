"""
composite.py — MarketLab's automatic NQ/MNQ research series, built from every free source
that works without an account, a login or an API key.

Sources (checked October 2026):
    Yahoo contract series   NQZ26.CME, NQH27.CME …   each quarterly contract separately, while it is listed
    Yahoo continuous        NQ=F                     front month; Yahoo picks the roll; no contract label
    FirstRate Data sample   frd_sample_futures_NQ    ~2 recent weeks of 1m/5m/1h, an independent vendor
Depth: 1m ≈ 29 days, 5m ≈ 60 days (about 40 trading days), 1h ≈ 2 years (plus everything already in the local archive).
No free anonymous source of second-level or tick NQ data exists, so none is offered.

How one research series is assembled (per trading day, 18:00 → 17:00 ET):

  1. CONTRACT IDENTIFICATION. Yahoo's continuous NQ=F is compared candle by candle with each
     contract series. Where its closes equal a contract's closes, those NQ=F candles ARE that
     contract (labelled and used as a cross-check). The rest is the older front month whose
     own contract series is no longer downloadable ("unlabelled front month").
  2. ROLL RULE (documented, volume-based, never backwards): MarketLab starts with the most
     traded contract and moves to a later contract on the first trading day on which that later
     contract trades more volume. The whole trading day uses one contract. Rolls are explicit
     breaks: no level, pattern or trade is carried across them. Prices are NOT back-adjusted.
  3. FILLING. A minute the chosen contract series lacks is filled only from a source showing
     the SAME contract that day (verified NQ=F candles; the FirstRate sample when ≥ 90 % of its
     closes match that day exactly). Nothing is interpolated or synthesized.
  4. DISAGREEMENT. Where two compatible sources both have a candle and differ (close by more
     than 1 point, high/low by more than 2), the candle is marked SOURCE DISAGREEMENT and
     excluded from detection.
  5. PROVENANCE. Every day records its contract and sources; the Dataset inspector shows the
     date ranges per source.
"""

import time
from collections import defaultdict

from . import sessions
from .providers import IntradayDataProvider, NotSupported, ProviderError, YAHOO_HISTORY_DAYS, get_provider

CLOSE_TOLERANCE = 1.0          # points; larger close differences between two sources = disagreement
EXTREME_TOLERANCE = 2.0        # points, for highs and lows
IDENTITY_SHARE = 0.9           # share of identical closes needed to call two series the same contract
UNLABELLED = "front month (unlabelled)"
ROLL_RULE = ("Volume roll: start with the most traded contract; switch to a later contract on the first trading day "
             "it trades more volume, and never switch back. One contract per trading day; prices are not back-adjusted; "
             "every switch is a break that nothing is carried across.")


def _same(a, b):
    return abs(a["close"] - b["close"]) < 1e-6


def _identity(rows_a, rows_b, minimum=10):
    """Share of identical closes over the candles both have (None if too few in common)."""
    common = [t for t in rows_a if t in rows_b]
    if len(common) < minimum:
        return None
    return sum(1 for t in common if _same(rows_a[t], rows_b[t])) / len(common)


def _differs(a, b):
    return (abs(a["close"] - b["close"]) > CLOSE_TOLERANCE or abs(a["high"] - b["high"]) > EXTREME_TOLERANCE
            or abs(a["low"] - b["low"]) > EXTREME_TOLERANCE)


def _by_day(rows):
    out = defaultdict(dict)
    for r in rows:
        out[sessions.trading_day(r["timestamp"])][r["timestamp"]] = r
    return out


def _order(code):
    expiry = sessions.contract_expiry(code)
    return expiry.toordinal() if expiry else 0


class AutoProvider(IntradayDataProvider):
    """The automatic research series (see module docstring). Same interface as every provider."""
    key = "auto"

    def __init__(self):
        self._last = {}

    def provider_name(self):
        return "MarketLab automatic (free sources)"

    def supported_intervals(self):
        return ["1m", "5m", "15m", "1h"]

    def history_available(self, interval):
        days = YAHOO_HISTORY_DAYS.get(interval)
        return {"days": days, "note": f"About {days} days of {interval} candles from free sources, plus the local archive."}

    def data_delay(self):
        return 600

    def get_historical_bars(self, symbol, interval, start=None, end=None, refresh=False, **_):
        rows, info = self.compose(symbol, interval, refresh=refresh)
        self._last[(symbol, interval)] = info
        return [r for r in rows if (start is None or r["timestamp"] >= start) and (end is None or r["timestamp"] < end)]

    def provenance(self, symbol, interval):
        return self._last.get((symbol, interval))

    def fetched_at(self, symbol, interval):
        return get_provider("yahoo").fetched_at(symbol, interval)

    # ------------------------------------------------------------------ assembly
    def compose(self, root, interval, refresh=False):
        if interval not in self.supported_intervals():
            raise NotSupported(f"The automatic series is built at {', '.join(self.supported_intervals())}.")
        yahoo, frd = get_provider("yahoo"), get_provider("firstrate")
        sources = []
        try:
            cont = yahoo.get_historical_bars(root, interval, refresh=refresh)
            sources.append({"name": f"Yahoo continuous {root}=F", "key": "yahoo_continuous", "status": "used", "bars": len(cont),
                            "first": cont[0]["timestamp"] if cont else None, "last": cont[-1]["timestamp"] if cont else None})
        except (ProviderError, NotSupported) as error:
            cont = []
            sources.append({"name": f"Yahoo continuous {root}=F", "key": "yahoo_continuous", "status": "failed", "why": str(error)})
        contract_rows = {}
        if cont:
            first_day = sessions.trading_day(cont[0]["timestamp"])
            last_day = sessions.trading_day(cont[-1]["timestamp"])
            for code in sessions.contracts_between(root, first_day, last_day):
                try:
                    rows = yahoo.get_historical_bars(code, interval, refresh=refresh)
                except (ProviderError, NotSupported):
                    sources.append({"name": f"Yahoo contract {code}", "key": code, "status": "unavailable",
                                    "why": "Not listed on Yahoo any more (expired) and not in the local archive."})
                    continue
                if rows:
                    contract_rows[code] = rows
                    sources.append({"name": f"Yahoo contract {code}", "key": code, "status": "used", "bars": len(rows),
                                    "first": rows[0]["timestamp"], "last": rows[-1]["timestamp"]})
        try:
            frd_rows = frd.get_historical_bars(root, interval, refresh=refresh)
            frd_status = {"name": "FirstRate Data sample", "key": "firstrate", "status": "used", "bars": len(frd_rows),
                          "attribution": "FirstRate Data (free sample, private use)"}
            if frd_rows:
                frd_status.update(first=frd_rows[0]["timestamp"], last=frd_rows[-1]["timestamp"])
        except (ProviderError, NotSupported) as error:
            frd_rows, frd_status = [], {"name": "FirstRate Data sample", "key": "firstrate", "status": "failed", "why": str(error)}
        sources.append(frd_status)
        if not cont and not contract_rows:
            raise ProviderError(f"No free {root} {interval} data could be downloaded right now.")
        rows, info = assemble(root, interval, cont, contract_rows, frd_rows)
        info["sources"] = sources
        info["built_at"] = time.time()
        return rows, info


def assemble(root, interval, cont, contract_rows, frd_rows):
    """Pure function (testable without network): rows from each source → (merged rows, provenance info)."""
    cont_days = _by_day(cont)
    contract_days = {code: _by_day(rows) for code, rows in contract_rows.items()}
    frd_days = _by_day(frd_rows)
    days = sorted(set(cont_days) | {d for by in contract_days.values() for d in by})
    out, segments, rolls, notes = [], [], [], []
    current = None
    used = defaultdict(int)
    for day in days:
        # 1. identify the continuous series' contract candle by candle
        c_today = cont_days.get(day, {})
        labelled = {}                                   # NQ=F candles proven to be contract X
        unlabelled = dict(c_today)
        for code, by in contract_days.items():
            k_today = by.get(day, {})
            if not k_today:
                continue
            for t, row in c_today.items():
                if t in k_today and _same(row, k_today[t]):
                    labelled.setdefault(code, {})[t] = row
                    unlabelled.pop(t, None)
        # Unlabelled candles on a day where NQ=F is mostly identified are noise between two feeds, not another contract.
        if c_today and len(unlabelled) < (1 - IDENTITY_SHARE) * len(c_today) and labelled:
            noise, unlabelled = unlabelled, {}
        else:
            noise = {}
        # 2. volumes and the roll rule
        volume = {code: sum(r["volume"] or 0 for r in by.get(day, {}).values()) for code, by in contract_days.items() if by.get(day)}
        if unlabelled:
            volume[UNLABELLED] = sum(r["volume"] or 0 for r in unlabelled.values())
        if not volume:
            continue
        rank = lambda code: -1 if code == UNLABELLED else _order(code)
        if current is None:
            current = max(volume, key=lambda c: (volume[c], rank(c)))
        else:
            later = [c for c in volume if rank(c) > rank(current) and volume[c] > volume.get(current, 0)]
            if current not in volume and not later:
                later = [c for c in volume if rank(c) >= rank(current)]
            if later:
                new = max(later, key=lambda c: (volume[c], rank(c)))
                if new != current:
                    rolls.append({"day": str(day), "from": current, "to": new,
                                  "why": f"{new} traded {volume[new]:,.0f} vs {volume.get(current, 0):,.0f} for {current}"})
                    current = new
        # 3. the day's candles
        if current == UNLABELLED:
            chosen = {t: {**r, "contract": None, "source": "yahoo_continuous"} for t, r in unlabelled.items()}
            primary, verify = "Yahoo continuous (contract not identified)", {}
        else:
            chosen = {t: {**r, "contract": current, "source": f"yahoo:{current}"} for t, r in contract_days[current].get(day, {}).items()}
            primary = f"Yahoo contract {current}"
            verify = labelled.get(current, {})
            for t, r in verify.items():                 # NQ=F candles proven identical fill the contract series' holes
                if t not in chosen:
                    chosen[t] = {**r, "contract": current, "source": "yahoo_continuous"}
            for t, r in noise.items():                  # same-contract feeds that disagree on a candle
                if t in chosen and _differs(chosen[t], r):
                    chosen[t]["_disagree"] = (f"Yahoo's {current} series and its continuous series differ "
                                              f"(close {chosen[t]['close']:.2f} vs {r['close']:.2f})")
        # 4. FirstRate sample: only when it shows the same contract that day
        f_today = frd_days.get(day, {})
        filled = 0
        if f_today and chosen:
            share = _identity(f_today, chosen, minimum=20)
            if share is not None and share >= IDENTITY_SHARE:
                for t, r in f_today.items():
                    if t not in chosen:
                        chosen[t] = {**r, "contract": None if current == UNLABELLED else current, "source": "firstrate"}
                        filled += 1
                    elif _differs(chosen[t], r) and "_disagree" not in chosen[t]:
                        chosen[t]["_disagree"] = (f"Yahoo and FirstRate differ (close {chosen[t]['close']:.2f} vs {r['close']:.2f}, "
                                                  f"high {chosen[t]['high']:.2f} vs {r['high']:.2f}, low {chosen[t]['low']:.2f} vs {r['low']:.2f})")
            elif share is not None:
                notes.append(f"{day}: the FirstRate sample shows a different contract than {current}; not used that day.")
        for t in sorted(chosen):
            out.append(chosen[t])
            used[chosen[t]["source"]] += 1
        seg_key = (current, primary)
        if segments and segments[-1]["key"] == seg_key:
            seg = segments[-1]
            seg["to_day"] = str(day)
        else:
            seg = {"key": seg_key, "contract": None if current == UNLABELLED else current, "source": primary,
                   "from_day": str(day), "to_day": str(day), "bars": 0, "filled": defaultdict(int), "verified_days": 0}
            segments.append(seg)
        seg["bars"] += len(chosen)
        seg["filled"]["FirstRate sample"] += filled
        seg["filled"]["Yahoo continuous (verified same contract)"] += sum(1 for r in chosen.values() if r["source"] == "yahoo_continuous" and current != UNLABELLED)
        seg["verified_days"] += 1 if (verify or filled or (f_today and chosen and (_identity(f_today, chosen, 20) or 0) >= IDENTITY_SHARE)) else 0
    for seg in segments:
        seg.pop("key")
        seg["filled"] = {k: v for k, v in seg["filled"].items() if v}
    labelled_share = sum(s["bars"] for s in segments if s["contract"]) / max(1, sum(s["bars"] for s in segments))
    limitations = []
    if any(s["contract"] is None for s in segments):
        limitations.append("Some days come from Yahoo's continuous series whose contract can't be identified "
                           "(that contract has expired and is no longer downloadable). Roll artifacts there are detected heuristically.")
    limitations.append("No free anonymous second-level or tick data exists for NQ/MNQ; 1 minute is the finest resolution.")
    info = {"root": root, "interval": interval, "segments": segments, "rolls": rolls, "roll_rule": ROLL_RULE,
            "rows_by_source": dict(used), "labelled_share": labelled_share, "notes": notes[:20], "limitations": limitations,
            "continuous": True, "adjustment": "none (unadjusted prices, explicit breaks at rolls)"}
    return out, info


from .providers import PROVIDERS  # noqa: E402  (registered here to avoid a circular import)

PROVIDERS["auto"] = AutoProvider()
