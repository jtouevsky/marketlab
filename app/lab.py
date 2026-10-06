"""
lab.py — compatibility layer for Strategy Lab v0.1.

The real engine now lives in experiments.py (definitions -> results) with
conditions in conditions.py and statistics in robust.py. This file keeps the
original "ticker, direction, threshold, window, forward" call working by
turning it into an experiment definition.
"""

import experiments
from experiments import LabInputError  # noqa: F401  (re-exported for marketlab.py)


def run_experiment(ticker, direction, threshold_pct, window, forward):
    definition = experiments.default_definition(ticker)
    definition["conditions"] = [{"type": "price_move",
                                 "params": {"direction": direction, "threshold": threshold_pct, "window": window}}]
    definition["outcome"] = {"type": "forward_return", "horizon": forward}
    return experiments.run(definition)
