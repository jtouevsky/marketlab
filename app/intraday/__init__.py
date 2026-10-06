"""
intraday/ — MarketLab's intraday market engine.

One normalized engine for Historical, Replay and (later) Live data:

    providers.py   IntradayDataProvider interface + Yahoo (free, delayed) and local-file providers
    bars.py        generic intervals (1s … 1d) and the normalized bar series
    quality.py     missing / duplicate / out-of-order / roll / abnormal-gap / stale detection
    sessions.py    CME equity-index sessions in US Eastern time (DST-aware)
    engine.py      resampling + TimeframeContext (only CLOSED candles are visible at time t)
    detectors.py   FVGs (with states), PDH/PDL, overnight levels, swings, equal highs/lows, sweeps
    strategy.py    hypothesis matching, trade simulation, metrics
    dataset.py     builds and caches a dataset; the Dataset inspector

Every detected event carries:
    occurred_at   when it happened
    confirmed_at  when the definition could be checked with data that existed then
and strategies may only use an event at or after confirmed_at (entry eligibility),
while outcomes are measured strictly after entry. Tests in tests/test_intraday.py
check this by truncating the data and confirming nothing earlier changes.
"""
