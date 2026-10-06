"""
sources/logos.py — company logos.

Source: Financial Modeling Prep's public logo images
(https://financialmodelingprep.com/image-stock/<TICKER>.png), no key needed.
Logos are downloaded once and cached on disk in cache/logos/, so the page
never waits on the provider twice. If no logo exists we remember that for a
day and the browser shows a tasteful initials monogram instead.

We only accept real image responses (content type image/*, sensible size),
never arbitrary web images.
"""

import re
import time

import requests

import config

LOGO_DIR = config.CACHE_DIR / "logos"
MISSING_SECONDS = 24 * 3600
SAFE = re.compile(r"^[A-Z0-9.\-^=]{1,12}$")


def get_logo(ticker):
    """Returns (bytes, content_type) or None."""
    ticker = ticker.upper()
    if not SAFE.match(ticker):
        return None
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    image = LOGO_DIR / f"{ticker}.png"
    missing = LOGO_DIR / f"{ticker}.missing"
    if image.exists():
        return image.read_bytes(), "image/png"
    if missing.exists() and time.time() - missing.stat().st_mtime < MISSING_SECONDS:
        return None

    try:
        response = requests.get(f"https://financialmodelingprep.com/image-stock/{ticker}.png", timeout=8)
    except requests.RequestException:
        return None   # offline: try again next time, don't record as missing
    kind = response.headers.get("content-type", "")
    if response.ok and kind.startswith("image/") and 200 < len(response.content) < 2_000_000:
        image.write_bytes(response.content)
        return response.content, kind
    missing.touch()
    return None
