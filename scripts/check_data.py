"""
check_data.py — checks every data source MarketLab uses, for one ticker.

    python3 scripts/check_data.py AAPL

Prints what each source returned, so when something shows "N/A" in the app
you can tell whether the data is missing at the source or it's a MarketLab bug.
"""

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "app"))   # the app's modules live in app/

import sys

import config
from sources import llm, sec, yahoo

INFO_FIELDS = [
    "longName", "currentPrice", "regularMarketPrice", "previousClose", "currency",
    "financialCurrency", "marketCap", "enterpriseValue", "fiftyTwoWeekLow", "fiftyTwoWeekHigh",
    "trailingPE", "forwardPE", "trailingEps", "dividendRate", "averageVolume", "beta",
    "priceToSalesTrailing12Months", "priceToBook", "enterpriseToEbitda", "trailingPegRatio",
    "revenueGrowth", "earningsGrowth", "fullTimeEmployees", "city", "website",
]


def check_yahoo(ticker):
    print(f"\n=== Yahoo Finance: info fields for {ticker} ===")
    info = yahoo.get_info(ticker)
    for field in INFO_FIELDS:
        value = info.get(field)
        print(f"  {'OK ' if value is not None else '-- '} {field:32} {value}")
    for kind, frequency in (("income", "yearly"), ("cash_flow", "yearly"), ("balance", "quarterly")):
        table = yahoo.load_statement(ticker, kind, frequency)
        periods = ", ".join(str(c.date()) for c in table.columns) if not table.empty else "none"
        print(f"  {kind:10} statement periods: {periods}")


def check_sec(ticker):
    print("\n=== SEC EDGAR ===")
    if not sec.is_configured():
        print("  off: add SEC_USER_AGENT to .env")
        return
    try:
        company = sec.get_company_filings(ticker)
        print(f"  OK  {company['name']} (CIK {company['cik']}), {company['sic_description']}")
        for filing in company["filings"][:5]:
            print(f"      {filing['form']:6} filed {filing['filed']}  {filing['url']}")
        revenue = sec.annual_values(ticker, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                             "RevenueFromContractWithCustomerIncludingAssessedTax"], combine="max")
        print(f"  Annual revenue from filings: {[(r['date'], r['value']) for r in revenue[-4:]]}")
    except sec.SecUnavailable as error:
        print(f"  --  {error}")


def check_ai():
    print("\n=== Claude (Anthropic) ===")
    ai = llm.assistant_status(refresh=True)
    print(f"  Ask MarketLab: {ai['label'] if ai['enabled'] else 'not connected (' + str(ai['setup']) + ' needed)'}")
    if not llm.is_configured():
        print("  API key: off (company summaries use Yahoo's text as-is)")
        return
    try:
        reply = llm.complete("Reply with exactly: OK", [{"role": "user", "content": "Test"}],
                             model=config.AI_MODEL_FAST, max_tokens=5)
        print(f"  OK  {config.AI_MODEL_FAST} replied: {reply!r}")
    except llm.LLMError as error:
        print(f"  --  {error}")


if __name__ == "__main__":
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper()
    check_yahoo(symbol)
    check_sec(symbol)
    check_ai()
