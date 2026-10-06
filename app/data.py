"""
data.py — small shared helpers used by every other file.

    errors          TickerNotFoundError, DataSourceError
    input           clean_ticker()
    numbers         to_number(), safe_divide()
    text            clean_text(), first_sentences()
    time            now_iso()

Fetching data from a provider lives in the `sources/` folder.
Building what each tab shows lives in the `tab_*.py` files.
"""

import math
import re
from datetime import datetime, timezone

from ftfy import fix_text


# A ticker is 1-10 characters: letters, digits, and a few symbols.
#   BRK-B   -> class B shares      SHOP.TO -> Toronto listing
#   ^GSPC   -> an index            BTC-USD -> crypto pair
TICKER_PATTERN = re.compile(r"^[A-Z0-9.\-^=]{1,10}$")


# --- Custom errors -----------------------------------------------------------

class TickerNotFoundError(Exception):
    """The ticker looks fine but Yahoo has no security for it."""


class DataSourceError(Exception):
    """We couldn't reach the main market-data provider at all."""


# --- Clean up and check what the user typed ----------------------------------

def clean_ticker(raw_text):
    """Turn user input like '  aapl ' into 'AAPL', or raise ValueError."""
    ticker = raw_text.strip().upper()

    if not ticker:
        raise ValueError("Please type a ticker symbol, e.g. AAPL.")

    if not TICKER_PATTERN.match(ticker):
        raise ValueError(
            f"'{ticker}' doesn't look like a ticker. "
            "Use letters/numbers only, e.g. AAPL, NVDA, BRK-B."
        )

    return ticker


# --- Numbers -----------------------------------------------------------------

def to_number(value):
    """
    Convert anything to a normal float, or None if it's missing.

    Why this matters: pandas uses NaN ("not a number") for missing values,
    and NaN is NOT valid JSON, so it would break the browser. Everything
    numeric we send goes through this function first.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def safe_divide(top, bottom):
    """top / bottom, or None if either is missing or bottom is 0."""
    if top is None or bottom is None or bottom == 0:
        return None
    return top / bottom


def now_iso():
    """Current time as text, e.g. '2026-10-01T14:03:00+00:00'. Used for freshness labels."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Text --------------------------------------------------------------------

def clean_text(text):
    """
    Tidy text from a data provider before anyone sees it:
      * fix broken characters ("Appleâ€™s" -> "Apple's") with the ftfy library
      * collapse repeated spaces / line breaks
      * remove spaces before punctuation ("products , and" -> "products, and")
      * add a missing space after a sentence ("devices.The" -> "devices. The")
    It does NOT change wording. Typos in the source are fixed later, by the
    AI rewrite step in describe.py.
    """
    if not text:
        return None
    text = fix_text(str(text))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?)])", r"\1", text)
    text = re.sub(r"([a-z])\.([A-Z][a-z])", r"\1. \2", text)
    return text or None


# Words that end in a period but don't end a sentence.
ABBREVIATIONS = ("Inc.", "Corp.", "Co.", "Ltd.", "U.S.", "No.", "St.", "Jr.", "Sr.",
                 "N.V.", "S.A.", "L.P.", "plc.", "e.g.", "i.e.", "approx.", "vs.", "Ph.D.")


def first_sentences(text, count):
    """Return the first `count` sentences of `text` (or None if there's no text)."""
    if not text:
        return None
    # Split after . ! or ? when followed by a space and a capital letter/number.
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text.strip())

    # Glue back pieces that were split after an abbreviation like "Inc."
    sentences = []
    for piece in pieces:
        if sentences and sentences[-1].endswith(ABBREVIATIONS):
            sentences[-1] += " " + piece
        else:
            sentences.append(piece)

    return " ".join(sentences[:count])
