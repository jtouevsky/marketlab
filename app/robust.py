"""
robust.py — statistics for a list of outcomes, conventional AND robust.

The point: an arithmetic average can be dominated by one extreme outcome.
We never delete real outcomes; we show how much the conclusion depends on
them. Everything here is plain Python so it's easy to read and check.

    summarize(xs)          n, mean, median, std, positive rate, t-based 95% CI
    robust_stats(xs, ...)  trimmed / winsorized means, mean without the most
                           extreme outcome, bootstrap CIs, concentration
    robustness_checks(...) factual pass/caution statements (no scores)
"""

import math
import random
import statistics

# Two-sided 95% critical values of Student's t-distribution, by degrees of freedom.
T_TABLE = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
           10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
           18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
           26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042, 40: 2.021, 60: 2.000, 80: 1.990,
           100: 1.984, 120: 1.980}

BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 7        # fixed seed: the same data always gives the same interval


def t_critical(df):
    """t value for a two-sided 95% interval (table + linear interpolation in 1/df)."""
    if df in T_TABLE:
        return T_TABLE[df]
    if df > 120:
        return 1.960
    keys = sorted(T_TABLE)
    lower = max(k for k in keys if k < df)
    upper = min(k for k in keys if k > df)
    weight = (1 / df - 1 / upper) / (1 / lower - 1 / upper)
    return T_TABLE[upper] + weight * (T_TABLE[lower] - T_TABLE[upper])


def percentile(sorted_values, q):
    """Linear-interpolated percentile of an already-sorted list (q from 0 to 1)."""
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def summarize(values):
    n = len(values)
    if n == 0:
        return None
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if n >= 2 else None
    positives = sum(1 for v in values if v > 0)
    negatives = sum(1 for v in values if v < 0)
    result = {
        "n": n, "mean": mean, "median": statistics.median(values), "std": std,
        "positives": positives, "negatives": negatives, "flat": n - positives - negatives,
        "positive_rate": positives / n, "negative_rate": negatives / n,
        "min": min(values), "max": max(values),
        "ci_low": None, "ci_high": None, "t_crit": None, "std_error": None,
    }
    if std is not None:
        t_value = t_critical(n - 1)
        se = std / math.sqrt(n)
        result.update(t_crit=t_value, std_error=se, ci_low=mean - t_value * se, ci_high=mean + t_value * se)
    return result


def _trim_count(n, fraction):
    """How many outcomes to set aside at EACH end: `fraction` of n, but at least
    one once there are 10+ outcomes (otherwise 5% of a small sample rounds to zero)."""
    k = int(n * fraction)
    if k < 1 and n >= 10 and fraction > 0:
        k = 1
    return k


def trimmed_mean(values, fraction=0.05):
    """Drop the lowest and highest `fraction` of outcomes, average the rest.
    Returns (mean, number dropped from each end)."""
    ordered = sorted(values)
    k = _trim_count(len(ordered), fraction)
    if k < 1 or len(ordered) - 2 * k < 2:
        return None, 0
    return statistics.fmean(ordered[k:len(ordered) - k]), k


def winsorized_mean(values, fraction=0.05):
    """Replace the lowest/highest `fraction` with the nearest remaining value, then average.
    Unlike trimming, every outcome still counts, but extremes are capped."""
    ordered = sorted(values)
    k = _trim_count(len(ordered), fraction)
    if k < 1 or len(ordered) - 2 * k < 2:
        return None, 0
    low, high = ordered[k], ordered[len(ordered) - k - 1]
    return statistics.fmean([min(max(v, low), high) for v in values]), k


def bootstrap_ci(values, statistic, samples=BOOTSTRAP_SAMPLES, seed=BOOTSTRAP_SEED):
    """
    Percentile bootstrap 95% interval: resample the observed outcomes with
    replacement many times, recompute the statistic each time, and take the
    middle 95% of those results. It shows how much the statistic would wobble
    if history had produced a slightly different set of episodes.
    """
    if len(values) < 5:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    results = sorted(statistic([values[rng.randrange(n)] for _ in range(n)]) for _ in range(samples))
    return percentile(results, 0.025), percentile(results, 0.975)


def robust_stats(values, trim=0.05):
    if not values:
        return None
    n = len(values)
    middle = statistics.median(values)
    # The single most extreme outcome = furthest from the median
    extreme_index = max(range(n), key=lambda i: abs(values[i] - middle))
    without_extreme = values[:extreme_index] + values[extreme_index + 1:]
    trimmed, trimmed_k = trimmed_mean(values, trim)
    winsorized, wins_k = winsorized_mean(values, trim)
    mean_ci = bootstrap_ci(values, statistics.fmean)
    median_ci = bootstrap_ci(values, statistics.median)
    positives = [v for v in values if v > 0]
    negatives = [v for v in values if v < 0]
    return {
        "trim_fraction": trim,
        "trimmed_mean": trimmed, "trimmed_each_side": trimmed_k,
        "winsorized_mean": winsorized, "winsorized_each_side": wins_k,
        "most_extreme": {"index": extreme_index, "value": values[extreme_index]},
        "mean_without_extreme": statistics.fmean(without_extreme) if without_extreme else None,
        "bootstrap_mean_ci": mean_ci,
        "bootstrap_median_ci": median_ci,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        # How concentrated are the gains / losses?
        "largest_gain_share": (max(positives) / sum(positives)) if positives else None,
        "largest_loss_share": (min(negatives) / sum(negatives)) if negatives else None,
        "contribution": contribution(values),
        "mean_without_both_extremes": statistics.fmean(sorted(values)[1:-1]) if n >= 5 else None,
    }


def contribution(values, ks=(1, 3, 5)):
    """
    How much of the aggregate comes from the largest outcomes.
        gains:  share of the SUM OF ALL POSITIVE outcomes contributed by the k largest gains
        losses: share of the sum of all negative outcomes contributed by the k largest losses
    Shares of a one-sided total are well defined (both are sums of same-signed numbers);
    shares of the net total are not (it can be near zero or flip sign), so we don't report them.
    """
    gains = sorted((v for v in values if v > 0), reverse=True)
    losses = sorted(v for v in values if v < 0)
    total_gain, total_loss = sum(gains), sum(losses)
    out = {"gains": [], "losses": [], "total_gain": total_gain, "total_loss": total_loss,
           "n_gains": len(gains), "n_losses": len(losses)}
    for k in ks:
        if len(gains) > k:
            out["gains"].append({"k": k, "share": sum(gains[:k]) / total_gain})
        if len(losses) > k:
            out["losses"].append({"k": k, "share": sum(losses[:k]) / total_loss})
    return out


def robustness_checks(values, dates, stats, robust, baseline_mean=None, min_sample=30):
    """
    Factual checks that could CHALLENGE the result. Each is
    {"ok": True/False/None, "text": ...}. ok=None means "information only".
    No combined score: each check means something different.
    """
    checks = []
    n = stats["n"]
    mean, median_ = stats["mean"], stats["median"]

    checks.append({"ok": n >= min_sample,
                   "text": f"{n} historical episodes" + ("" if n >= min_sample else f" (fewer than {min_sample}: treat results as rough)")})

    same_sign = (mean > 0) == (median_ > 0) or mean == 0 or median_ == 0
    checks.append({"ok": same_sign, "text": "Average and median point the same way" if same_sign
                   else "Average and median point in opposite directions: a few outcomes drive the average"})

    if robust and robust["trimmed_mean"] is not None:
        keeps = (robust["trimmed_mean"] > 0) == (mean > 0)
        checks.append({"ok": keeps, "text": f"Result keeps its sign after trimming the top and bottom {robust['trim_fraction']:.0%}"
                       if keeps else f"Result changes sign after trimming the top and bottom {robust['trim_fraction']:.0%}"})

    if robust and robust["mean_without_extreme"] is not None and mean != 0:
        change = robust["mean_without_extreme"] - mean
        material = abs(change) > max(0.25 * abs(mean), 0.002)
        checks.append({"ok": not material,
                       "text": (f"Removing the single most extreme episode moves the average from {mean:+.2%} to "
                                f"{robust['mean_without_extreme']:+.2%}") if material
                       else "No single episode materially changes the average"})

    share = robust.get("largest_gain_share") if robust else None
    if share is not None and mean > 0:
        checks.append({"ok": share < 0.25,
                       "text": f"The largest gain is {share:.0%} of all gains combined"})

    # Does the effect show up across time, or only in one stretch?
    if n >= 10:
        half = n // 2
        first = statistics.fmean(values[:half])
        second = statistics.fmean(values[half:])
        consistent = (first > 0) == (second > 0)
        checks.append({"ok": consistent,
                       "text": (f"Same direction in both halves of history ({dates[0][:4]}–{dates[half - 1][:4]}: {first:+.2%}, "
                                f"{dates[half][:4]}–{dates[-1][:4]}: {second:+.2%})") if consistent
                       else (f"Direction differs between halves of history ({dates[0][:4]}–{dates[half - 1][:4]}: {first:+.2%}, "
                             f"{dates[half][:4]}–{dates[-1][:4]}: {second:+.2%})")})
        years = {}
        for value, day in zip(values, dates):
            years.setdefault(day[:4], []).append(value)
        if len(years) >= 3:
            agree = sum(1 for v in years.values() if (statistics.fmean(v) > 0) == (mean > 0))
            checks.append({"ok": None, "text": f"Average points the same way in {agree} of {len(years)} calendar years with episodes"})

    if baseline_mean is not None and stats["ci_low"] is not None:
        inside = stats["ci_low"] <= baseline_mean <= stats["ci_high"]
        checks.append({"ok": not inside,
                       "text": "Normal-period average lies inside the 95% interval: not distinguishable from usual behaviour"
                       if inside else "Normal-period average lies outside the 95% interval"})
    return checks


def rolling_stats(values, dates, window=20):
    """
    Statistics over a moving window of consecutive episodes (oldest first):
    [{"end_date", "n", "mean", "median", "positive_rate"}, ...]
    Foundation for "edge decay": does a relationship weaken or strengthen over
    time? Windows of episodes, not of calendar time, so each point rests on the
    same number of observations. No "health" label is attached: that needs a
    defined statistical test first.
    """
    out = []
    for end in range(window, len(values) + 1):
        chunk = values[end - window:end]
        out.append({"end_date": dates[end - 1], "n": window, "mean": statistics.fmean(chunk),
                    "median": statistics.median(chunk), "positive_rate": sum(1 for v in chunk if v > 0) / window})
    return out
