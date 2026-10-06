"""
config.py — settings and API keys.

Secrets are NOT written in the code. They live in a file called `.env`
in the project's top folder (copy `.env.example`), which looks like:

    ANTHROPIC_API_KEY=sk-ant-...
    SEC_USER_AGENT=Your Name your.email@example.com

python-dotenv reads that file at start-up. Every feature that needs a key
checks whether it's set and politely switches itself off if it isn't,
so the app always runs, just with fewer features.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent          # the code (app/)
BASE_DIR = APP_DIR.parent                          # project root: .env and your local data live here
load_dotenv(BASE_DIR / ".env")

# Claude (Anthropic) — powers description clean-up and Ask MarketLab. Paid, per use.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

# Which AI powers Ask MarketLab:
#   auto           (default) your own Claude Code sign-in if available, otherwise ANTHROPIC_API_KEY
#   claude_code    only your Claude subscription, through the Claude Code program on this computer
#   anthropic_api  only ANTHROPIC_API_KEY (billed per request)
# Company-summary clean-up only uses an API key; without one, Yahoo's description is shown as-is.
AI_PROVIDER = os.getenv("AI_PROVIDER", "auto").strip().lower() or "auto"
CLAUDE_CODE_PATH = os.getenv("CLAUDE_CODE_PATH", "").strip()    # only if `claude` isn't found automatically
CLAUDE_CODE_MODEL = os.getenv("CLAUDE_CODE_MODEL", "").strip()  # sonnet / opus / haiku (default: sonnet)

# Two models: a fast, cheap one for small jobs (rewriting a description)
# and a stronger one for answering questions.
AI_MODEL_FAST = os.getenv("AI_MODEL_FAST", "claude-haiku-4-5-20251001").strip()
AI_MODEL_ASSISTANT = os.getenv("AI_MODEL_ASSISTANT", "claude-sonnet-5-5").strip()

# SEC EDGAR — free, no key, but the SEC requires every program to identify
# itself with a name and contact email in the "User-Agent" header.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()

# Optional news providers (free keys). Each one switches itself on when its key is set.
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()          # finnhub.io  (60 requests/min free)
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()  # alphavantage.co (25 requests/day free)
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "").strip()          # polygon.io (5 requests/min free)

# Where MarketLab saves things it doesn't want to recompute (AI summaries).
CACHE_DIR = BASE_DIR / "cache"
