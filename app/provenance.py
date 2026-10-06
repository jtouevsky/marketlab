"""
provenance.py — where data comes from, and which source wins.

Every important number in MarketLab can be described as a "fact":

    {
      "label": "Revenue",
      "value": 416200000000,           # raw number (or text)
      "display": "$416.2B",            # how it's shown to people / the AI
      "type": "reported",              # reported | estimate | market | calculated | generated
      "period": "FY ending 2025-09-27",
      "source": "sec",                 # key into SOURCES below
      "detail": "10-K filed 2025-10-31",
      "url": "https://www.sec.gov/...",
      "retrieved_at": "2026-10-01T14:02:00+00:00",
    }

The AI assistant receives facts in this shape, so it can never confuse an
analyst ESTIMATE with a REPORTED result, or a stale number with a fresh one.
"""

# Every data source MarketLab knows about.
SOURCES = {
    "yahoo": {"label": "Yahoo Finance", "kind": "Market data provider (unofficial, delayed)"},
    "sec": {"label": "SEC EDGAR", "kind": "Official company filing"},
    "marketlab": {"label": "MarketLab", "kind": "Calculated by MarketLab from the data above"},
    "ai": {"label": "Claude (AI)", "kind": "AI-written from retrieved sources"},
}

# Which source wins when several provide the same kind of information.
# ---------------------------------------------------------------------------
# reported_financials: SEC first. Companies file these numbers themselves,
#     under legal liability; Yahoo shows a secondary copy that can be
#     re-classified, rounded, or late. Yahoo fills any gaps.
# company_identity: SEC first for legal name, industry code, fiscal year
#     and headquarters; Yahoo as fallback (it covers non-US companies).
# market_data / estimates / description: Yahoo is currently our only source.
PRIORITY = {
    "reported_financials": ["sec", "yahoo"],
    "company_identity": ["sec", "yahoo"],
    "market_data": ["yahoo"],
    "estimates": ["yahoo"],
    "description": ["yahoo"],
}

# Two sources "disagree" when their values differ by more than 2%.
# We then keep the higher-priority value AND record the other one, so the
# UI can show the disagreement instead of hiding it.
CONFLICT_THRESHOLD = 0.02


def fact(label, value, display, type, source, period=None, detail=None, url=None, retrieved_at=None):
    return {
        "label": label,
        "value": value,
        "display": display,
        "type": type,
        "period": period,
        "source": source,
        "detail": detail,
        "url": url,
        "retrieved_at": retrieved_at,
    }


def disagree(value_a, value_b, threshold=CONFLICT_THRESHOLD):
    """True if two numbers differ by more than `threshold` (relative)."""
    if value_a is None or value_b is None:
        return False
    biggest = max(abs(value_a), abs(value_b))
    if biggest == 0:
        return False
    return abs(value_a - value_b) / biggest > threshold
