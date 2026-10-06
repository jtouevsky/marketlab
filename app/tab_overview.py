"""
tab_overview.py — everything the OVERVIEW tab's first screen needs.

Source: Yahoo Finance (prices and market statistics). This is the fast,
first request when a window opens; slower things (AI summary, SEC filings)
load separately so they never hold this up.
"""

from datetime import datetime, timezone

from data import now_iso, safe_divide, to_number
from sources import yahoo


def get_company_info(ticker):
    """
    A small, clean dictionary for the Overview tab.
    Any field Yahoo doesn't have comes back as None (never made up).
    """
    raw = yahoo.get_info(ticker)

    price = to_number(raw.get("currentPrice") or raw.get("regularMarketPrice"))

    # Today's move = price now vs. yesterday's closing price.
    previous_close = to_number(raw.get("previousClose") or raw.get("regularMarketPreviousClose"))
    change = change_pct = None
    if price is not None and previous_close:
        change = price - previous_close
        change_pct = change / previous_close * 100

    # Time of the last trade (Yahoo gives seconds since 1970).
    market_time = None
    if raw.get("regularMarketTime"):
        market_time = datetime.fromtimestamp(raw["regularMarketTime"], tz=timezone.utc).isoformat()

    # Dividend yield = forward annual dividend per share / current price.
    # (Yahoo's own 'dividendYield' field has changed format over time,
    #  sometimes 0.0041 and sometimes 0.41 for the same 0.41%, so we avoid it.)
    dividend_rate = to_number(raw.get("dividendRate"))
    dividend_yield = safe_divide(dividend_rate, price)

    return {
        "ticker": ticker,
        "name": raw.get("longName") or raw.get("shortName"),
        "quote_type": raw.get("quoteType"),          # "EQUITY", "ETF", ...
        "price": price,
        "change": change,
        "change_pct": change_pct,                    # already in percent (1.24 = 1.24%)
        "currency": raw.get("currency"),
        "exchange": raw.get("fullExchangeName") or raw.get("exchange"),
        "market_cap": to_number(raw.get("marketCap")),
        "market_time": market_time,                  # when the price was set
        "market_state": raw.get("marketState"),      # PRE / REGULAR / POST / CLOSED
        "fetched_at": now_iso(),                     # when MarketLab downloaded it
        "source": {"key": "yahoo", "url": yahoo.quote_url(ticker)},
        "glance": {
            "week52_low": to_number(raw.get("fiftyTwoWeekLow")),
            "week52_high": to_number(raw.get("fiftyTwoWeekHigh")),
            "pe": to_number(raw.get("trailingPE")),
            "forward_pe": to_number(raw.get("forwardPE")),
            "eps_ttm": to_number(raw.get("trailingEps")),
            "dividend_rate": dividend_rate,
            "dividend_yield": dividend_yield,
            "avg_volume": to_number(raw.get("averageVolume")),
            "beta": to_number(raw.get("beta")),
            # Trailing-twelve-month / latest-quarter figures for the Overview's quick read (Yahoo)
            "revenue_growth": to_number(raw.get("revenueGrowth")),        # latest quarter vs a year earlier
            "earnings_growth": to_number(raw.get("earningsGrowth")),
            "revenue_ttm": to_number(raw.get("totalRevenue")),
            "gross_margin": to_number(raw.get("grossMargins")),
            "operating_margin": to_number(raw.get("operatingMargins")),
            "profit_margin": to_number(raw.get("profitMargins")),
            "free_cash_flow": to_number(raw.get("freeCashflow")),        # TTM, Yahoo's calculation
            "total_cash": to_number(raw.get("totalCash")),
            "total_debt": to_number(raw.get("totalDebt")),
        },
        # Kept for the terminal version (marketlab_terminal.py).
        "sector": raw.get("sector"),
        "industry": raw.get("industry"),
    }
