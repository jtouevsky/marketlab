"""
tab_earnings.py — "How did the company do last quarter, and what comes next?"

Sources
    Yahoo earnings calendar     dates + times, EPS estimate vs reported EPS
                                (Yahoo's "reported EPS" is the figure analysts
                                track, usually ADJUSTED / non-GAAP)
    Yahoo calendar              next date, consensus EPS + revenue (estimates)
    SEC filings (preferred) /   quarterly revenue actuals; Q4 derived from the
    Yahoo quarterly statements  10-K when not filed separately
    Daily prices                the stock's move around each report

What is NOT available and therefore not shown as data:
    * historical revenue ESTIMATES (only the upcoming quarter's consensus exists)
    * company guidance (no reliable structured source yet)

Earnings reaction (a measurement, not a cause):
    day0 = the first trading session after the announcement
           (after-close report -> next day; before-open report -> same day)
    base = the close just BEFORE day0
    1-day reaction = close(day0) / base − 1
    5-day reaction = close(day0 + 4) / base − 1
"""

import bisect
from datetime import datetime

import pandas as pd

import marketdata
from data import now_iso, to_number
from sources import sec, yahoo

REVENUE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]


def _timing(ts):
    """Before open / after close from the announcement time (US Eastern)."""
    minutes = ts.hour * 60 + ts.minute
    if minutes == 0:
        return "unknown"
    if minutes < 9 * 60 + 30:
        return "before_open"
    if minutes >= 16 * 60:
        return "after_close"
    return "during_market"


def _reaction(bars, day, timing):
    dates, closes = bars["date"], bars["close"]
    if timing == "before_open" or timing == "during_market":
        day0 = bisect.bisect_left(dates, day)          # same day
    else:                                               # after close (or unknown: assume after close)
        day0 = bisect.bisect_right(dates, day)          # next session
    if day0 <= 0 or day0 >= len(dates):
        return None
    base = closes[day0 - 1]
    return {
        "day0": dates[day0],
        "base_date": dates[day0 - 1],
        "one_day": closes[day0] / base - 1,
        "five_day": (closes[day0 + 4] / base - 1) if day0 + 4 < len(dates) else None,
        "assumed_timing": timing == "unknown",
    }


def _quarterly_revenue(ticker, currency):
    """[{date, value, source, derived}] oldest -> newest. SEC first, Yahoo fills gaps."""
    points = {}
    try:
        for q in sec.quarterly_values(ticker, REVENUE_TAGS, unit=currency or "USD"):
            points[q["date"]] = {"date": q["date"], "value": q["value"], "source": "sec", "derived": q["derived"]}
    except sec.SecUnavailable:
        pass
    table = yahoo.load_statement(ticker, "income", "quarterly")
    if not table.empty and "TotalRevenue" in table.index:
        for period_end, value in table.loc["TotalRevenue"].items():
            number = to_number(value)
            day = pd.Timestamp(period_end).strftime("%Y-%m-%d")
            near = any(abs((datetime.fromisoformat(day) - datetime.fromisoformat(d)).days) <= 10 for d in points)
            if number is not None and not near:
                points[day] = {"date": day, "value": number, "source": "yahoo", "derived": False}
    return sorted(points.values(), key=lambda p: p["date"])


def _match_quarter(revenue, report_day, max_days=120):
    """The fiscal quarter a report covers: the latest quarter end before the report date (within ~4 months)."""
    best = None
    for q in revenue:
        gap = (datetime.fromisoformat(report_day) - datetime.fromisoformat(q["date"])).days
        if 0 < gap <= max_days:
            best = q
    return best


def _year_ago(revenue, quarter):
    for q in revenue:
        gap = (datetime.fromisoformat(quarter["date"]) - datetime.fromisoformat(q["date"])).days
        if 350 <= gap <= 380:
            return q
    return None


def get_earnings(ticker):
    info = yahoo.get_info(ticker)
    currency = info.get("financialCurrency") or info.get("currency")
    ticker_object = yahoo.get_ticker(ticker)

    try:
        table = ticker_object.get_earnings_dates(limit=16)
    except Exception:
        table = None
    try:
        calendar = ticker_object.calendar or {}
    except Exception:
        calendar = {}

    bars = marketdata.daily_bars(ticker)
    revenue = _quarterly_revenue(ticker, currency)
    today = datetime.now().strftime("%Y-%m-%d")

    rows, upcoming = [], None
    if table is not None and not table.empty:
        for ts, row in table.sort_index().iterrows():
            day = ts.strftime("%Y-%m-%d")
            entry = {
                "date": day, "time": ts.strftime("%H:%M"), "timing": _timing(ts),
                "eps_estimate": to_number(row.get("EPS Estimate")),
                "eps_actual": to_number(row.get("Reported EPS")),
                "surprise_pct": to_number(row.get("Surprise(%)")),
            }
            if entry["eps_actual"] is None:
                if day >= today and upcoming is None:
                    upcoming = entry
                continue
            quarter = _match_quarter(revenue, day)
            if quarter:
                prior = _year_ago(revenue, quarter)
                entry["revenue"] = {"value": quarter["value"], "quarter_end": quarter["date"], "source": quarter["source"],
                                    "derived": quarter["derived"],
                                    "yoy": (quarter["value"] / prior["value"] - 1) if prior and prior["value"] > 0 else None}
            entry["reaction"] = _reaction(bars, day, entry["timing"])
            rows.append(entry)

    # EPS growth vs the same quarter a year earlier (4 reports back, ~1 year apart)
    for i, entry in enumerate(rows):
        if i >= 4:
            prior = rows[i - 4]
            gap = (datetime.fromisoformat(entry["date"]) - datetime.fromisoformat(prior["date"])).days
            if 330 <= gap <= 400 and prior["eps_actual"] and prior["eps_actual"] > 0:
                entry["eps_yoy"] = entry["eps_actual"] / prior["eps_actual"] - 1

    history = list(reversed(rows))[:8]            # newest first
    cal_date = (calendar.get("Earnings Date") or [None])[0]
    next_report = {
        "date": upcoming["date"] if upcoming else (cal_date.isoformat() if cal_date else None),
        "time": upcoming["time"] if upcoming else None,
        # Yahoo uses placeholder times for unconfirmed dates; only trust clear before-open / after-close times
        "timing": upcoming["timing"] if upcoming and upcoming["timing"] in ("before_open", "after_close") else "unknown",
        "eps_estimate": to_number(calendar.get("Earnings Average")) or (upcoming or {}).get("eps_estimate"),
        "eps_low": to_number(calendar.get("Earnings Low")), "eps_high": to_number(calendar.get("Earnings High")),
        "revenue_estimate": to_number(calendar.get("Revenue Average")),
        "revenue_low": to_number(calendar.get("Revenue Low")), "revenue_high": to_number(calendar.get("Revenue High")),
    }

    return {
        "ticker": ticker, "currency": currency, "retrieved_at": now_iso(),
        "next": next_report if next_report["date"] else None,
        "latest": history[0] if history else None,
        "history": history,
        "revenue_quarters": revenue[-12:],
        "guidance": None,
        "notes": {
            "eps": "Reported EPS is the figure Yahoo's analyst data tracks; it is usually adjusted (non-GAAP) and can differ from EPS in the 10-Q.",
            "revenue_estimates": "Past quarters' revenue estimates aren't available from our sources; only the upcoming quarter's consensus is.",
            "guidance": "Company guidance isn't available from a reliable structured source yet, so none is shown.",
            "reaction": "Price moves around a report measure what happened, not why. Other news can move a stock on the same days.",
        },
        "sources": {"estimates": "Yahoo Finance (analyst consensus)", "revenue": "SEC filings, Yahoo for gaps",
                    "prices": bars["provider"]},
    }


def earnings_markers(ticker):
    """Compact list for chart markers: [{date, eps_actual, eps_estimate, surprise_pct, reaction}]."""
    try:
        data = get_earnings(ticker)
    except Exception:
        return []
    return [{k: e.get(k) for k in ("date", "timing", "eps_actual", "eps_estimate", "surprise_pct", "reaction", "revenue")}
            for e in data["history"]]
