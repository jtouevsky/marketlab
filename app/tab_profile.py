"""
tab_profile.py — builds everything the PROFILE tab shows, from several sources.

Where each part comes from (see provenance.py for the priority rules):

    Financial snapshot      SEC filings (10-K, as filed)  -> Yahoo fills gaps
    Company facts           SEC (legal name, industry code, fiscal year, HQ)
                            + Yahoo (CEO, employees, website, market cap)
    Valuation, estimates    Yahoo (our only source for these)
    Balance sheet           Yahoo, most recent quarter
    SEC filings list        SEC

If SEC is unavailable (not configured, offline, or not a US filer), the
tab still works using Yahoo alone, and says so.
"""

import re
from datetime import date

import pandas as pd

from data import now_iso, safe_divide, to_number
from provenance import disagree
from sources import sec, yahoo


# =============================================================================
# 1. Turning raw data into "history" lists
# =============================================================================
# A history is a list of points, oldest -> newest:
#   [{"date": "2025-09-27", "value": 416.2e9, "source": "sec", "detail": "10-K filed ...", ...}]

def yahoo_history(statement, row):
    """One line item from a Yahoo statement table, skipping missing years."""
    if statement.empty or row not in statement.index:
        return []
    series = statement.loc[row]
    if isinstance(series, pd.DataFrame):  # duplicated row name: take the first
        series = series.iloc[0]

    points = []
    for period_end, value in series.items():
        number = to_number(value)
        if number is not None:
            points.append({
                "date": pd.Timestamp(period_end).strftime("%Y-%m-%d"),
                "value": number,
                "source": "yahoo",
                "detail": "Yahoo Finance annual statement",
            })
    points.sort(key=lambda point: point["date"])
    return points


def sec_history(ticker, tags, unit="USD", combine="first"):
    """The same idea, from SEC filings. Returns [] if SEC data isn't available."""
    try:
        values = sec.annual_values(ticker, tags, unit=unit, combine=combine)
    except sec.SecUnavailable:
        return []
    return [{
        "date": v["date"],
        "value": v["value"],
        "source": "sec",
        "detail": f"{v['form']} filed {v['filed']}",
        "url": v["url"],
    } for v in values]


def same_period(date_a, date_b):
    """Two period-end dates refer to the same fiscal year if they're within 10 days."""
    return abs((date.fromisoformat(date_a) - date.fromisoformat(date_b)).days) <= 10


def merge_histories(sec_points, yahoo_points, keep=5):
    """
    One value per fiscal year, following the priority rule in provenance.py:
      * SEC (as filed) wins when it has the year.
      * Yahoo fills years SEC doesn't have.
      * If both have a year and differ by more than 2%, we keep SEC and
        record Yahoo's value under "conflict" so the UI can show it.
    """
    merged = []
    used_yahoo = set()
    for point in sec_points:
        match = next((y for y in yahoo_points if same_period(y["date"], point["date"])), None)
        point = dict(point)
        if match:
            used_yahoo.add(match["date"])
            if disagree(point["value"], match["value"]):
                point["conflict"] = {"source": "yahoo", "value": match["value"]}
        merged.append(point)

    for point in yahoo_points:
        if point["date"] not in used_yahoo and not any(same_period(point["date"], m["date"]) for m in merged):
            merged.append(dict(point))

    merged.sort(key=lambda point: point["date"])
    return merged[-keep:]


def subtract_histories(first, second, detail):
    """first − second, period by period (used for free cash flow = OCF − capex)."""
    result = []
    for a in first:
        b = next((p for p in second if p["date"] == a["date"]), None)
        if b is not None:
            result.append({
                "date": a["date"],
                "value": a["value"] - b["value"],
                "source": a["source"],
                "detail": f"{detail} ({a.get('detail', '')})",
                "url": a.get("url"),
            })
    return result


def ratio_history(top_points, bottom_points):
    """Divide two histories period by period. Used for margins."""
    result = []
    for top in top_points:
        bottom = next((p for p in bottom_points if same_period(p["date"], top["date"])), None)
        ratio = safe_divide(top["value"], bottom["value"] if bottom else None)
        if ratio is not None:
            result.append({"date": top["date"], "value": ratio, "source": "marketlab",
                           "detail": "Calculated from the two figures above"})
    return result


# =============================================================================
# 2. Growth calculations (unchanged from the previous stage)
# =============================================================================

def years_between(date_a, date_b):
    return (date.fromisoformat(date_b) - date.fromisoformat(date_a)).days / 365.25


def growth_rate(older, newer):
    """% growth. None when the older value is 0 or negative (growth from a loss isn't meaningful)."""
    if older is None or newer is None or older <= 0:
        return None
    return newer / older - 1


def yoy(points, offset=0):
    """Year-over-year growth of the latest point (offset=0) or an earlier one.
    Only compares periods ~1 year apart, so a missing year never fakes a 'YoY'."""
    if len(points) < 2 + offset:
        return None
    newer = points[-1 - offset]
    older = points[-2 - offset]
    if not 0.8 <= years_between(older["date"], newer["date"]) <= 1.2:
        return None
    return growth_rate(older["value"], newer["value"])


def yoy_change(points):
    """Change in raw units between the last two periods (used for margins, in % points)."""
    if len(points) < 2 or not 0.8 <= years_between(points[-2]["date"], points[-1]["date"]) <= 1.2:
        return None
    return points[-1]["value"] - points[-2]["value"]


def cagr(points, years=3):
    """Compound annual growth rate over `years`."""
    if not points:
        return None
    latest = points[-1]
    for point in points:
        span = years_between(point["date"], latest["date"])
        if abs(span - years) < 0.2 and point["value"] > 0 and latest["value"] > 0:
            return (latest["value"] / point["value"]) ** (1 / span) - 1
    return None


def metric(points, kind):
    """Package one Financial Snapshot row; every history point gets its own YoY."""
    change_function = yoy_change if kind == "percent" else yoy
    for index, point in enumerate(points):
        point["yoy"] = change_function(points[: index + 1])

    latest = points[-1] if points else None
    return {
        "kind": kind,                                    # "money", "percent", "per_share"
        "points": points,
        "latest": latest["value"] if latest else None,
        "latest_date": latest["date"] if latest else None,
        "yoy": latest["yoy"] if latest else None,
        "source": latest["source"] if latest else None,  # where the latest value came from
        "detail": latest.get("detail") if latest else None,
        "url": latest.get("url") if latest else None,
        "conflict": latest.get("conflict") if latest else None,
    }


def latest_period(statement, rows):
    """
    Values of several line items from the SAME most recent period that has data.
    Returns (period_end_date, {row: value or None}). Using one period matters:
    "net cash" must subtract debt and cash from the same quarter.
    """
    if statement.empty:
        return None, {}
    for period_end in sorted(statement.columns, reverse=True):
        values = {}
        for row in rows:
            values[row] = to_number(statement.at[row, period_end]) if row in statement.index else None
        if any(value is not None for value in values.values()):
            return pd.Timestamp(period_end).strftime("%Y-%m-%d"), values
    return None, {}


# =============================================================================
# 3. Smaller pieces
# =============================================================================

def analyst_estimates(ticker):
    """Consensus forecasts: '0y' = current fiscal year, '+1y' = next fiscal year."""
    estimates = {}
    for name, year_ago_column in (("revenue", "yearAgoRevenue"), ("eps", "yearAgoEps")):
        estimates[name] = {}
        table = yahoo.load_estimates(ticker, name)
        for period, label in (("0y", "current_fy"), ("+1y", "next_fy")):
            if not table.empty and period in table.index:
                row = table.loc[period]
                estimates[name][label] = {
                    "avg": to_number(row.get("avg")),
                    "growth": to_number(row.get("growth")),
                    "analysts": to_number(row.get("numberOfAnalysts")),
                    "year_ago": to_number(row.get(year_ago_column)),
                }
    return estimates


def find_ceo(officers):
    for officer in officers or []:
        title = (officer.get("title") or "").lower()
        if "ceo" in title or "chief executive officer" in title:
            return " ".join((officer.get("name") or "").split()) or None  # tidy double spaces
    return None


# Sentences from the company's own description, sorted by topic. These are
# QUOTED, not summarised: the UI labels them "from the company description".
BUSINESS_TOPICS = [   # order matters: a sentence goes to the first topic it matches
    ("segments", r"\b(segments?|operates (in|through) (two|three|four|five|\w+) |divisions?)\b"),
    ("customers", r"\b(customers?|clients?|serves|sells? (\w+ ){0,3}(to|through)|sold (to|through)|end users?)\b"),
    ("partnerships", r"\b(partner(ship)?s?|collaborat\w+|alliances?|joint venture|agreements? with)\b"),
    ("products", r"\b(products?|offers?|offerings?|provides?|platforms?|services|solutions|designs?|develops?|manufactures?|sells?|software|hardware|devices?|systems?)\b"),
]


def business_sentences(description):
    """{'products': [...], 'customers': [...], ...} from the description; each sentence used once."""
    if not description:
        return None
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z])", description) if len(x.strip()) > 30]
    used, result = set(), {}
    for topic, pattern in BUSINESS_TOPICS:
        found = [x for x in sentences if x not in used and re.search(pattern, x, re.I)][:3]
        used.update(found)
        result[topic] = found
    return result


def founded_year(description):
    """Yahoo has no 'founded' field; read it from "... was founded in 1976 ..." if present."""
    match = re.search(r"\bfounded in (\d{4})\b", description or "")
    return int(match.group(1)) if match else None


def fiscal_year_end_text(mmdd):
    """SEC writes fiscal year end as 'MMDD' (e.g. '0927'). Turn it into 'Sep 27'."""
    if not mmdd or len(mmdd) != 4:
        return None
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month, day = int(mmdd[:2]), int(mmdd[2:])
    return f"{months[month - 1]} {day}" if 1 <= month <= 12 else None


# XBRL names (tags) for each line item. Companies don't all use the same
# names, so we list the common ones in priority order.
REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
]


# Capital expenditures: most companies use the first tag; some (e.g. NVIDIA)
# report "productive assets" (equipment + intangible assets) instead.
CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
]


# =============================================================================
# 4. The Profile tab
# =============================================================================

def get_profile(ticker):
    info = yahoo.get_info(ticker)
    currency = info.get("currency")
    statement_currency = info.get("financialCurrency") or currency

    # Which sources worked? (shown in the UI and given to the AI)
    status = {"yahoo": {"ok": True, "retrieved_at": now_iso()}}

    # ---- SEC identity + filings (optional) ----
    sec_company = None
    try:
        sec_company = sec.get_company_filings(ticker)
        status["sec"] = {"ok": True, "retrieved_at": sec_company["retrieved_at"]}
    except sec.SecUnavailable as error:
        status["sec"] = {"ok": False, "message": str(error)}

    # SEC numbers are only used when they're in the same currency Yahoo reports in.
    sec_unit = statement_currency if statement_currency else "USD"
    use_sec = sec_company is not None

    def sec_points(tags, unit=None, combine="first"):
        return sec_history(ticker, tags, unit or sec_unit, combine) if use_sec else []

    # ---- Yahoo statements ----
    income = yahoo.load_statement(ticker, "income", "yearly")
    cash_flow = yahoo.load_statement(ticker, "cash_flow", "yearly")
    balance = yahoo.load_statement(ticker, "balance", "quarterly")

    # ---- Financial snapshot: SEC first, Yahoo fills gaps ----
    revenue = merge_histories(sec_points(REVENUE_TAGS, combine="max"), yahoo_history(income, "TotalRevenue"))
    gross_profit = merge_histories(sec_points(["GrossProfit"]), yahoo_history(income, "GrossProfit"))
    operating_income = merge_histories(sec_points(["OperatingIncomeLoss"]), yahoo_history(income, "OperatingIncome"))
    net_income = merge_histories(sec_points(["NetIncomeLoss"]), yahoo_history(income, "NetIncome"))
    eps = merge_histories(sec_points(["EarningsPerShareDiluted"], unit=f"{sec_unit}/shares"),
                          yahoo_history(income, "DilutedEPS"))

    # Free cash flow = operating cash flow − capital expenditures.
    sec_fcf = subtract_histories(
        sec_points(["NetCashProvidedByUsedInOperatingActivities"]),
        sec_points(CAPEX_TAGS),
        "Operating cash flow − capital expenditures",
    )
    free_cash_flow = merge_histories(sec_fcf, yahoo_history(cash_flow, "FreeCashFlow"))

    snapshot = {
        "revenue": metric(revenue, "money"),
        "operating_income": metric(operating_income, "money"),
        "net_income": metric(net_income, "money"),
        "free_cash_flow": metric(free_cash_flow, "money"),
        "gross_margin": metric(ratio_history(gross_profit, revenue), "percent"),
        "operating_margin": metric(ratio_history(operating_income, revenue), "percent"),
        "eps": metric(eps, "per_share"),
    }

    # ---- Growth ----
    growth = {
        "revenue_yoy": yoy(revenue),
        "revenue_yoy_prior": yoy(revenue, offset=1),
        "revenue_cagr_3y": cagr(revenue, 3),
        "eps_yoy": yoy(eps),
        "fcf_yoy": yoy(free_cash_flow),
        "quarterly_revenue_yoy": to_number(info.get("revenueGrowth")),
        "quarterly_earnings_yoy": to_number(info.get("earningsGrowth")),
        "estimates": analyst_estimates(ticker),
    }

    # ---- Valuation ----
    market_cap = to_number(info.get("marketCap"))
    latest_fcf = free_cash_flow[-1] if free_cash_flow else None
    same_currency = statement_currency == currency
    fcf_yield = safe_divide(latest_fcf["value"], market_cap) if (latest_fcf and same_currency) else None

    valuation = {
        "pe": to_number(info.get("trailingPE")),
        "forward_pe": to_number(info.get("forwardPE")),
        "price_to_sales": to_number(info.get("priceToSalesTrailing12Months")),
        "price_to_book": to_number(info.get("priceToBook")),
        "ev_to_ebitda": to_number(info.get("enterpriseToEbitda")),
        "peg": to_number(info.get("trailingPegRatio")),
        "fcf_yield": fcf_yield,
        "fcf_yield_basis": latest_fcf["date"] if fcf_yield is not None else None,
    }

    # ---- Financial health (most recent quarterly balance sheet, Yahoo) ----
    balance_date, b = latest_period(balance, [
        "CashCashEquivalentsAndShortTermInvestments", "TotalDebt",
        "CurrentAssets", "CurrentLiabilities", "StockholdersEquity",
    ])
    cash = b.get("CashCashEquivalentsAndShortTermInvestments")
    debt = b.get("TotalDebt")
    equity = b.get("StockholdersEquity")
    debt_to_equity = safe_divide(debt, equity) if (equity and equity > 0) else None

    # Interest coverage = EBIT / interest expense, same fiscal year (Yahoo).
    # Not meaningful when EBIT is negative, so we skip it then.
    income_date, i = latest_period(income, ["EBIT", "InterestExpense"])
    interest_coverage = None
    if i.get("EBIT") is not None and i["EBIT"] > 0 and i.get("InterestExpense"):
        interest_coverage = i["EBIT"] / abs(i["InterestExpense"])

    health = {
        "as_of": balance_date,
        "cash": cash,
        "total_debt": debt,
        "net_cash": (cash - debt) if (cash is not None and debt is not None) else None,
        "current_ratio": safe_divide(b.get("CurrentAssets"), b.get("CurrentLiabilities")),
        "debt_to_equity": debt_to_equity,
        "free_cash_flow": latest_fcf["value"] if latest_fcf else None,
        "free_cash_flow_date": latest_fcf["date"] if latest_fcf else None,
        "interest_coverage": interest_coverage,
        "interest_coverage_date": income_date if interest_coverage is not None else None,
    }

    # ---- Company facts: SEC for official identity, Yahoo for the rest ----
    description = info.get("longBusinessSummary")
    yahoo_location = ", ".join(p for p in (info.get("city"), info.get("state"), info.get("country")) if p)
    sec_location = None
    if sec_company and sec_company.get("business_city"):
        sec_location = ", ".join(p for p in (sec_company["business_city"], sec_company.get("business_state")) if p)

    facts = {
        "legal_name": sec_company["name"] if sec_company else None,
        "cik": sec_company["cik"] if sec_company else None,
        "ceo": find_ceo(info.get("companyOfficers")),
        "headquarters": sec_location or yahoo_location or None,
        "headquarters_source": "sec" if sec_location else ("yahoo" if yahoo_location else None),
        "employees": to_number(info.get("fullTimeEmployees")),
        "founded": founded_year(description),
        "website": info.get("website"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "market_cap": market_cap,
        "enterprise_value": to_number(info.get("enterpriseValue")),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "sec_industry": (sec_company["sic_description"] or None) if sec_company else None,
        "fiscal_year_end": fiscal_year_end_text(sec_company["fiscal_year_end"]) if sec_company else None,
        "incorporated_in": sec_company["state_of_incorporation"] if sec_company else None,
    }

    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName"),
        "fetched_at": now_iso(),
        "currency": currency,
        "statement_currency": statement_currency,
        "sources": status,
        "facts": facts,
        "segments": None,  # not available from our current sources (see UI note)
        "business": business_sentences(description),
        "snapshot": snapshot,
        "growth": growth,
        "valuation": valuation,
        "health": health,
        "filings": sec_company["filings"] if sec_company else [],
        "edgar_url": sec_company["edgar_url"] if sec_company else None,
    }
