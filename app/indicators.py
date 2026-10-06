"""
indicators.py — small, reusable technical calculations.

Used by the Chart tab (overlays), the Risk tab and Strategy Lab conditions,
so every part of MarketLab computes "the 50-day average" the same way.

Every function takes a plain list of numbers (oldest first) and returns a
list of the same length. Positions where the value can't be computed yet
(not enough history) are None. Each value at index t uses only data up to
and including t, so nothing here can look into the future.
"""

import math


def sma(values, period):
    """Simple moving average of the last `period` values (including today)."""
    out = [None] * len(values)
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def trailing_mean(values, period):
    """Average of the `period` values BEFORE today (today excluded).
    Used for relative volume, so today's volume is compared with what came before."""
    out = [None] * len(values)
    running = 0.0
    for i in range(len(values)):
        if i >= 1:
            running += values[i - 1]
        if i - 1 >= period:
            running -= values[i - 1 - period]
        if i >= period:
            out[i] = running / period
    return out


def rsi(closes, period=14):
    """
    Relative Strength Index, Wilder's smoothing (the standard definition).
    RSI = 100 − 100 / (1 + average gain / average loss), on a 0–100 scale.
    """
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def returns(closes):
    """Simple daily returns; index 0 is None."""
    return [None] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]


def rolling_std(values, period):
    """Sample standard deviation of the last `period` values (None-safe window required)."""
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if any(v is None for v in window):
            continue
        mean = sum(window) / period
        out[i] = math.sqrt(sum((v - mean) ** 2 for v in window) / (period - 1))
    return out
