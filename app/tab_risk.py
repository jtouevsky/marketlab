"""
tab_risk.py — "How has this stock behaved, and what could hurt it?"

Everything here is a MEASUREMENT of the past or a reported fact. There is
no risk score: a single number would hide exactly the differences this tab
is meant to show (a stock can have shallow daily swings and deep crashes).

Sections
    horizons        Rolling returns over a holding period (1D ... 5Y, or custom):
                    "if you had bought on any day and held N trading days,
                    what happened?" Windows OVERLAP, so the counts are not
                    independent samples; the percentiles are descriptive.
    drawdowns       Falls from a previous high: current, deepest, episodes.
    volatility      Annualised standard deviation of daily returns, rolling.
    market          Beta and correlation vs SPY (S&P 500) and QQQ (Nasdaq-100).
    sensitivities   Correlation of daily moves with bonds, oil and the dollar.
    tail            Historical Value-at-Risk / Expected Shortfall, downside
                    deviation, Sortino ratio.
    asymmetry       Largest gains vs losses, overnight gaps, short interest.
    fundamental     Cash, debt, free cash flow, cash runway, dilution, margins.
    analysts        Attributed analyst targets and rating counts (not ours).
    unavailable     What we cannot measure reliably yet, said plainly.

Formulas (all on split/dividend-adjusted daily closes):
    daily return      r_t = close_t / close_{t-1} − 1
    annualised vol    stdev(r) × √252
    beta vs index     cov(r_stock, r_index) / var(r_index)
    VaR 95% (1 day)   the 5th percentile of daily returns, reported as a loss
    ES 95%            the average of the returns at or below that percentile
    downside dev.     √(mean(min(r, 0)²)) × √252
    Sortino           (mean(r) × 252) / downside deviation
"""

import math
import statistics
from datetime import datetime, timedelta

import marketdata
import robust
import tab_profile
from data import now_iso, to_number
from sources import sec, yahoo

TRADING_DAYS = 252
HORIZONS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("1Y", 252), ("5Y", 1260)]
SIGNIFICANT_DRAWDOWN = 0.20
BENCHMARKS = {"SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq-100 (QQQ)"}
SENSITIVITIES = {"TLT": "Long-term US Treasuries (TLT)", "USO": "Crude oil (USO)", "UUP": "US dollar (UUP)"}


# ---------------------------------------------------------------- helpers

def _returns(closes):
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]


def _slice_period(bars, period):
    """'full' history or the last 5 years."""
    if period != "5y":
        return bars["date"], bars["close"]
    start = (datetime.fromisoformat(bars["date"][-1]) - timedelta(days=int(365.25 * 5))).strftime("%Y-%m-%d")
    i = next((k for k, d in enumerate(bars["date"]) if d >= start), 0)
    return bars["date"][i:], bars["close"][i:]


def _histogram(values, bins=36):
    if not values:
        return None
    ordered = sorted(values)
    # Clip the display range to the 0.5–99.5 percentiles so one outlier doesn't flatten the picture;
    # values outside are counted in the edge bins and reported.
    low, high = robust.percentile(ordered, 0.005), robust.percentile(ordered, 0.995)
    if high <= low:
        high = low + 1e-9
    width = (high - low) / bins
    counts = [0] * bins
    below = above = 0
    for v in values:
        if v < low:
            below += 1
            counts[0] += 1
        elif v > high:
            above += 1
            counts[-1] += 1
        else:
            counts[min(int((v - low) / width), bins - 1)] += 1
    return {"start": low, "width": width, "counts": counts, "clipped_below": below, "clipped_above": above}


def _horizon_stats(dates, closes, days):
    values = [closes[i + days] / closes[i] - 1 for i in range(len(closes) - days)]
    if len(values) < 20:
        return None
    ordered = sorted(values)
    worst_i = min(range(len(values)), key=values.__getitem__)
    best_i = max(range(len(values)), key=values.__getitem__)
    return {
        "days": days, "n": len(values),
        "independent_periods": len(values) // days if days else len(values),
        "mean": statistics.fmean(values), "median": robust.percentile(ordered, 0.5),
        "std": statistics.stdev(values),
        "p5": robust.percentile(ordered, 0.05), "p25": robust.percentile(ordered, 0.25),
        "p75": robust.percentile(ordered, 0.75), "p95": robust.percentile(ordered, 0.95),
        "worst": {"value": values[worst_i], "start": dates[worst_i], "end": dates[worst_i + days]},
        "best": {"value": values[best_i], "start": dates[best_i], "end": dates[best_i + days]},
        "p_negative": sum(1 for v in values if v < 0) / len(values),
        "histogram": _histogram(values),
    }


def _drawdowns(dates, closes):
    peak_i, episodes, current = 0, [], None
    series = []
    for i, c in enumerate(closes):
        if c >= closes[peak_i]:
            if current and current["depth"] <= -0.05:
                current["recovered"] = dates[i]
                current["days_to_recover"] = i - current["_trough_i"]
                episodes.append(current)
            current = None
            peak_i = i
        else:
            depth = c / closes[peak_i] - 1
            if current is None:
                current = {"peak": dates[peak_i], "peak_price": closes[peak_i], "depth": depth,
                           "trough": dates[i], "_trough_i": i, "_peak_i": peak_i, "recovered": None, "days_to_recover": None}
            elif depth < current["depth"]:
                current.update(depth=depth, trough=dates[i], _trough_i=i)
        series.append(c / closes[peak_i] - 1)
    ongoing = current
    if ongoing and ongoing["depth"] <= -0.05:
        episodes.append(ongoing)
    for e in episodes:
        e["days_to_trough"] = e["_trough_i"] - e["_peak_i"]
        e.pop("_trough_i"), e.pop("_peak_i")
    significant = [e for e in episodes if e["depth"] <= -SIGNIFICANT_DRAWDOWN]
    recovered = [e["days_to_recover"] for e in significant if e["days_to_recover"] is not None]
    deepest = min(episodes, key=lambda e: e["depth"]) if episodes else None
    step = max(1, len(series) // 600)                      # thin the underwater curve for drawing
    return {
        "current": series[-1] if series else None,
        "current_peak": dates[peak_i] if closes else None,
        "max": deepest,
        "significant_threshold": SIGNIFICANT_DRAWDOWN,
        "significant_count": len(significant),
        "average_significant": statistics.fmean([e["depth"] for e in significant]) if significant else None,
        "median_recovery_days": statistics.median(recovered) if recovered else None,
        "episodes": sorted(significant or episodes, key=lambda e: e["depth"])[:8],
        "underwater": {"date": dates[::step], "value": series[::step]},
    }


def _annual_vol(returns):
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS) if len(returns) > 2 else None


def _rolling_vol(dates, returns, window, last_days=5 * TRADING_DAYS, step=5):
    out_d, out_v = [], []
    start = max(window, len(returns) - last_days)
    for i in range(start, len(returns) + 1, step):
        out_d.append(dates[i - 1])
        out_v.append(statistics.stdev(returns[i - window:i]) * math.sqrt(TRADING_DAYS))
    return {"date": out_d, "value": out_v}


def _aligned_returns(bars, other, since):
    """Daily returns of both series on the dates they share (from `since`)."""
    other_close = dict(zip(other["date"], other["close"]))
    pairs = [(d, c, other_close[d]) for d, c in zip(bars["date"], bars["close"]) if d in other_close and d >= since]
    a = [pairs[i][1] / pairs[i - 1][1] - 1 for i in range(1, len(pairs))]
    b = [pairs[i][2] / pairs[i - 1][2] - 1 for i in range(1, len(pairs))]
    return a, b


def _beta_corr(a, b):
    if len(a) < 60:
        return None
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / (len(a) - 1)
    var_b = statistics.variance(b)
    corr = cov / (statistics.stdev(a) * statistics.stdev(b)) if var_b else None
    return {"beta": cov / var_b if var_b else None, "correlation": corr, "n": len(a)}


def _since(bars, years):
    return (datetime.fromisoformat(bars["date"][-1]) - timedelta(days=int(365.25 * years))).strftime("%Y-%m-%d")


def _extremes(dates, closes, days):
    moves = [(closes[i + days] / closes[i] - 1, i) for i in range(len(closes) - days)]
    if not moves:
        return None
    best, worst = max(moves), min(moves)
    return {"days": days,
            "largest_gain": {"value": best[0], "start": dates[best[1]], "end": dates[best[1] + days]},
            "largest_loss": {"value": worst[0], "start": dates[worst[1]], "end": dates[worst[1] + days]}}


def _gaps(bars, since):
    gaps = []
    for i in range(1, len(bars["date"])):
        if bars["date"][i] < since:
            continue
        prev, opening = bars["close"][i - 1], bars["open"][i]
        if prev and opening:
            gaps.append((opening / prev - 1, bars["date"][i]))
    if not gaps:
        return None
    up, down = max(gaps), min(gaps)
    return {
        "since": since, "sessions": len(gaps),
        "largest_up": {"value": up[0], "date": up[1]}, "largest_down": {"value": down[0], "date": down[1]},
        "share_over_5pct": sum(1 for g, _ in gaps if abs(g) >= 0.05) / len(gaps),
        "count_down_over_10pct": sum(1 for g, _ in gaps if g <= -0.10),
    }


def _dilution(ticker, info, splits):
    """Change in shares outstanding (SEC cover-page counts), 1 and 3 years.
    Older counts are restated for stock splits in between, so a 10-for-1 split
    is not mistaken for 900% dilution."""
    try:
        points = sec.shares_outstanding(ticker)
    except Exception:
        points = []
    if len(points) < 2:
        return {"points": points, "one_year": None, "three_year": None, "source": None,
                "current": to_number(info.get("sharesOutstanding"))}
    latest = points[-1]

    def change(years):
        target = datetime.fromisoformat(latest["date"]) - timedelta(days=int(365.25 * years))
        older = [p for p in points if abs((datetime.fromisoformat(p["date"]) - target).days) <= 75]
        if not older:
            return None
        base = min(older, key=lambda p: abs((datetime.fromisoformat(p["date"]) - target).days))
        if not base["value"]:
            return None
        factor = 1.0
        for day, ratio in (splits or {}).items():
            if base["date"] < day <= latest["date"]:
                factor *= ratio
        return {"value": latest["value"] / (base["value"] * factor) - 1, "from": base["date"], "to": latest["date"],
                "split_adjusted": factor != 1.0}

    return {"points": points[-16:], "one_year": change(1), "three_year": change(3), "source": "sec", "current": latest["value"]}


def _analysts(ticker, info):
    targets = {k: to_number(info.get(f)) for k, f in (("low", "targetLowPrice"), ("mean", "targetMeanPrice"),
                                                     ("median", "targetMedianPrice"), ("high", "targetHighPrice"))}
    ratings = None
    try:
        table = yahoo.get_ticker(ticker).recommendations
        if table is not None and not table.empty:
            row = table.iloc[0]
            ratings = {k: int(row.get(k) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
    except Exception:
        pass
    eps = yahoo.load_estimates(ticker, "eps")
    dispersion = None
    if not eps.empty and "0y" in eps.index:
        row = eps.loc["0y"]
        low, high, avg = to_number(row.get("low")), to_number(row.get("high")), to_number(row.get("avg"))
        if None not in (low, high, avg):
            dispersion = {"period": "current fiscal year EPS", "low": low, "avg": avg, "high": high,
                          "analysts": to_number(row.get("numberOfAnalysts"))}
    return {"targets": targets if any(v is not None for v in targets.values()) else None,
            "analyst_count": to_number(info.get("numberOfAnalystOpinions")), "ratings": ratings,
            "eps_dispersion": dispersion, "price": to_number(info.get("currentPrice") or info.get("regularMarketPrice")),
            "source": "Yahoo Finance (aggregated from brokerage analysts)"}


def _fundamental(ticker, info, splits):
    try:
        profile = tab_profile.get_profile(ticker)
    except Exception:
        return None, None
    h, snap = profile["health"], profile["snapshot"]
    fcf = h.get("free_cash_flow")
    runway = (h["cash"] / -fcf) if (fcf is not None and fcf < 0 and h.get("cash")) else None
    op_margin = snap["operating_margin"]
    margins = [p for p in (op_margin.get("points") or [])][-4:]
    return {
        "as_of": h.get("as_of"), "currency": profile.get("statement_currency"),
        "cash": h.get("cash"), "total_debt": h.get("total_debt"), "net_cash": h.get("net_cash"),
        "current_ratio": h.get("current_ratio"), "debt_to_equity": h.get("debt_to_equity"),
        "interest_coverage": h.get("interest_coverage"),
        "free_cash_flow": fcf, "free_cash_flow_date": h.get("free_cash_flow_date"),
        "burning_cash": fcf is not None and fcf < 0,
        "runway_years": runway,
        "operating_margin": op_margin.get("latest"), "operating_margin_history": margins,
        "gross_margin": snap["gross_margin"].get("latest"),
        "dilution": _dilution(ticker, info, splits),
    }, profile


# ---------------------------------------------------------------- main

def get_risk(ticker, period="full", custom_days=None):
    period = "5y" if period == "5y" else "full"
    bars = marketdata.daily_bars(ticker)
    info = yahoo.get_info(ticker)
    dates, closes = _slice_period(bars, period)
    returns = _returns(closes)

    horizons = []
    for label, days in HORIZONS:
        stats = _horizon_stats(dates, closes, days)
        horizons.append({"label": label, "days": days, "stats": stats})
    if custom_days:
        days = max(1, min(int(custom_days), 2520))
        horizons.append({"label": f"{days}D", "days": days, "custom": True, "stats": _horizon_stats(dates, closes, days)})

    # Market relationships (1 and 3 years of daily returns)
    market = []
    for symbol, label in BENCHMARKS.items():
        try:
            other = marketdata.daily_bars(symbol)
        except Exception:
            market.append({"symbol": symbol, "label": label, "error": "unavailable"})
            continue
        row = {"symbol": symbol, "label": label}
        for years in (1, 3):
            a, b = _aligned_returns(bars, other, _since(bars, years))
            row[f"y{years}"] = _beta_corr(a, b)
        market.append(row)
    sensitivities = []
    for symbol, label in SENSITIVITIES.items():
        try:
            a, b = _aligned_returns(bars, marketdata.daily_bars(symbol), _since(bars, 3))
            result = _beta_corr(a, b)
            sensitivities.append({"symbol": symbol, "label": label, "correlation": result and result["correlation"],
                                  "n": result and result["n"]})
        except Exception:
            sensitivities.append({"symbol": symbol, "label": label, "correlation": None})

    # Tail risk on the last 5 years of daily returns (or all, if shorter)
    recent = _returns(_slice_period(bars, "5y")[1])
    ordered = sorted(recent)
    tail = None
    if len(recent) > 100:
        var95, var99 = robust.percentile(ordered, 0.05), robust.percentile(ordered, 0.01)
        downside = math.sqrt(statistics.fmean([min(r, 0) ** 2 for r in recent])) * math.sqrt(TRADING_DAYS)
        annual_mean = statistics.fmean(recent) * TRADING_DAYS
        tail = {
            "window": "last 5 years of daily returns" if len(recent) > 4 * TRADING_DAYS else "all available daily returns",
            "n": len(recent),
            "var95": var95, "es95": statistics.fmean([r for r in recent if r <= var95]),
            "var99": var99, "es99": statistics.fmean([r for r in recent if r <= var99]),
            "downside_deviation": downside,
            "annual_mean": annual_mean,
            "sortino": annual_mean / downside if downside else None,
            "worst_day": {"value": ordered[0]}, "best_day": {"value": ordered[-1]},
        }

    full_returns = _returns(bars["close"])
    volatility = {
        "full": _annual_vol(full_returns),
        "one_year": _annual_vol(full_returns[-TRADING_DAYS:]),
        "rolling_30": _annual_vol(full_returns[-30:]),
        "rolling_90": _annual_vol(full_returns[-90:]),
        "rolling_252": _annual_vol(full_returns[-TRADING_DAYS:]),
        "series_30": _rolling_vol(bars["date"][1:], full_returns, 30),
        "series_90": _rolling_vol(bars["date"][1:], full_returns, 90),
    }
    if len(full_returns) > 60:
        all_30 = _rolling_vol(bars["date"][1:], full_returns, 30, last_days=len(full_returns), step=5)["value"]
        volatility["rolling_30_percentile"] = sum(1 for v in all_30 if v <= volatility["rolling_30"]) / len(all_30)

    asymmetry = {
        "moves": [m for m in (_extremes(bars["date"], bars["close"], d) for d in (1, 5, 21)) if m],
        "gaps": _gaps(bars, _since(bars, 5)),
        "short_interest": {
            "percent_of_float": to_number(info.get("shortPercentOfFloat")),
            "shares_short": to_number(info.get("sharesShort")),
            "days_to_cover": to_number(info.get("shortRatio")),
            "as_of": datetime.fromtimestamp(info["dateShortInterest"]).strftime("%Y-%m-%d") if info.get("dateShortInterest") else None,
            "prior_month": to_number(info.get("sharesShortPriorMonth")),
            "source": "Yahoo Finance (exchange-reported, published twice a month)",
        },
    }

    fundamental, profile = _fundamental(ticker, info, bars.get("splits"))
    ten_k = None
    try:
        filings = sec.get_company_filings(ticker, limit=80)["filings"]
        ten_k = next((f for f in filings if f.get("form") in ("10-K", "20-F", "40-F")), None)
    except Exception:
        pass

    return {
        "ticker": ticker, "name": info.get("longName") or info.get("shortName"),
        "currency": info.get("currency"), "retrieved_at": now_iso(),
        "period": period, "tested_from": dates[0], "tested_to": dates[-1], "trading_days": len(dates),
        "horizons": horizons,
        "drawdowns": _drawdowns(dates, closes),
        "volatility": volatility,
        "market": market, "sensitivities": sensitivities,
        "tail": tail, "asymmetry": asymmetry,
        "fundamental": fundamental,
        "analysts": _analysts(ticker, info),
        "sector": {"sector": info.get("sector"), "industry": info.get("industry"),
                   "risk_factors_filing": ten_k,
                   "note": "Dependencies, policy exposure, controversies and guidance below are read from this filing, "
                           "other SEC filings and the News layer, with the evidence for each item."},
        "unavailable": [],     # dependency map, controversies and guidance are now measured (risk_intel.py)
        "data": {"provider": bars["provider"], "adjusted": bars["adjusted"], "quality": bars["quality"]["summary"]},
        "notes": {
            "horizons": "Holding-period returns use every possible start day, so windows overlap and are not independent. "
                        "They describe the past; they are not a forecast.",
            "beta": "Beta measures how much the stock has moved with the index (1.5 ≈ 1.5% for each 1% index move, on average). "
                    "Correlation measures how consistently they moved together (−1 to 1). A stock can have a high beta and low "
                    "correlation: big moves that only loosely follow the market.",
            "tail": "Historical VaR: on 5% of days the stock fell at least this much. Expected shortfall: the average of those bad days. "
                    "Both come from the past and can be exceeded.",
            "spy_qqq": "QQQ is concentrated in large technology companies; SPY is the broader market. A tech stock usually tracks QQQ more closely.",
        },
    }
