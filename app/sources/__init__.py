"""
sources/ — one file per external data provider.

    yahoo.py   Yahoo Finance via yfinance: prices, market stats, valuation
               multiples, analyst estimates, statement fallback
    sec.py     SEC EDGAR: company identity, filings list (10-K/10-Q/8-K),
               officially filed financial numbers (XBRL)
    llm.py     Claude (Anthropic): the only file that talks to the AI

Each provider file only FETCHES and lightly cleans data. Deciding which
source wins is done in the tab_*.py files, using the rules in provenance.py.
"""
