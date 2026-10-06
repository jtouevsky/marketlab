"""
describe.py — turns a raw provider description into a clean MarketLab summary.

Pipeline:
    1. SOURCE     Yahoo's company description (longBusinessSummary) — the
                  only free source of a written business description.
                  Known problem: it can contain typos and garbled sentences
                  (e.g. RGTI's "...quantum processors the United States...").
    2. CLEAN      fix broken characters and spacing (data.clean_text).
    3. REWRITE    Claude rewrites it in 2-4 plain sentences, told to use ONLY
                  facts in the source text + official SEC details.
    4. VERIFY     a simple automatic check: every number and every proper
                  name in the rewrite must appear in the source material.
                  If not, we throw the rewrite away and show the cleaned
                  source excerpt instead. Better plain than invented.
    5. CACHE      approved summaries are saved in cache/summaries.json, so
                  each company costs one AI call, not one per visit.

Without an API key, steps 3-5 are skipped and a cleaned excerpt is shown.
"""

import hashlib
import json
import re
import threading

import config
from data import clean_text, first_sentences, now_iso
from sources import llm, sec, yahoo

CACHE_FILE = config.CACHE_DIR / "summaries.json"
_cache_lock = threading.Lock()

SUMMARY_SYSTEM_PROMPT = """You write company descriptions for MarketLab, a stock research tool for intelligent people who are not finance professionals.

Rewrite the SOURCE TEXT into 2-4 clear, plain-English sentences (at most 90 words) that answer, as far as the source allows:
1. What the company actually does.
2. Its main products or services.
3. Who buys or uses them.
4. What is distinctive about the business (only if the source states it).

Strict rules:
- Use ONLY facts stated in the SOURCE TEXT or the OFFICIAL SEC DETAILS. Never add facts, numbers, customers, partners, rankings or history from your own knowledge.
- Fix spelling, grammar and garbled phrases, but never "repair" a garbled phrase by guessing facts it might have meant. Drop it instead.
- Copy product and company names exactly as written in the source.
- Neutral tone. No marketing words such as "leading", "innovative", "cutting-edge", "world-class".
- Output only the sentences: no heading, no bullet points, no preamble."""

# Capitalised words that may legitimately appear in a summary without being in the source.
COMMON_WORDS = {
    "The", "It", "Its", "Their", "They", "This", "These", "In", "Its", "A", "An", "And",
    "Through", "With", "Customers", "Products", "Services", "Company", "Inc", "I",
}


# --- Cache helpers -----------------------------------------------------------

def _load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_to_cache(ticker, entry):
    with _cache_lock:
        cache = _load_cache()
        cache[ticker] = entry
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _fingerprint(text):
    """Short ID of the source text: if Yahoo changes the description, we re-summarise."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# --- The grounding check -----------------------------------------------------

def find_ungrounded(summary, source_material):
    """
    Return the first number or proper name in `summary` that does NOT appear
    in `source_material`, or None if everything checks out.

    It's deliberately simple: it can't prove a summary is right, but it
    catches the most harmful kind of AI mistake: invented specifics.
    """
    haystack = source_material.lower()

    for number in re.findall(r"\d[\d,.]*\d|\d", summary):
        if number.lower() not in haystack:
            return number

    for sentence in re.split(r"(?<=[.!?])\s+", summary):
        words = re.findall(r"[A-Za-z][\w&+\-']*", sentence)
        for word in words[1:]:  # first word of a sentence is capitalised anyway
            clean = re.sub(r"'s$", "", word)
            if clean[0].isupper() and clean not in COMMON_WORDS and clean.lower() not in haystack:
                return clean
    return None


# --- Main entry point --------------------------------------------------------

def company_summary(ticker):
    """
    Returns:
      {"text": "...", "method": "ai" | "excerpt" | "none",
       "source_text": cleaned original, "note": explanation for the UI,
       "generated_at": ..., "model": ...}
    """
    info = yahoo.get_info(ticker)
    name = info.get("longName") or info.get("shortName") or ticker
    source_text = clean_text(info.get("longBusinessSummary"))

    if not source_text:
        return {"text": None, "method": "none", "source_text": None,
                "note": "No company description is available from our sources."}

    excerpt = {
        "text": first_sentences(source_text, 3),
        "method": "excerpt",
        "source_text": source_text,
        "note": "Excerpt from Yahoo Finance's description (cleaned). Add an Anthropic API key for a rewritten summary.",
    }
    if not llm.is_configured():
        return excerpt

    # Official SEC details: used both as extra context and for the grounding check.
    sec_details = ""
    try:
        company = sec.get_company_filings(ticker)
        sec_details = (f"Legal name: {company['name']}. Industry classification: {company['sic_description']}. "
                       f"Headquarters: {company['business_city']}, {company['business_state']}.")
    except sec.SecUnavailable:
        pass

    fingerprint = _fingerprint(source_text + sec_details)
    cached = _load_cache().get(ticker)
    if cached and cached.get("fingerprint") == fingerprint:
        return {**cached, "source_text": source_text}

    prompt = (f"Company: {name} ({ticker})\n"
              f"OFFICIAL SEC DETAILS: {sec_details or 'not available'}\n\n"
              f"SOURCE TEXT (Yahoo Finance company description):\n<<<\n{source_text}\n>>>")
    try:
        summary = llm.complete(SUMMARY_SYSTEM_PROMPT, [{"role": "user", "content": prompt}],
                               model=config.AI_MODEL_FAST, max_tokens=300)
    except llm.LLMError as error:
        return {**excerpt, "note": f"Showing the cleaned source excerpt: {error}"}

    summary = " ".join(summary.split())
    problem = find_ungrounded(summary, f"{source_text} {sec_details} {name} {ticker}")
    if problem or not summary:
        return {**excerpt, "note": f"The AI rewrite mentioned \"{problem}\", which isn't in the source text, "
                                   "so MarketLab is showing the cleaned source excerpt instead."}

    entry = {
        "text": summary,
        "method": "ai",
        "note": "AI-written summary of Yahoo Finance's description and SEC company details. Checked automatically "
                "so that every number and name appears in those sources.",
        "model": config.AI_MODEL_FAST,
        "generated_at": now_iso(),
        "fingerprint": fingerprint,
    }
    _save_to_cache(ticker, entry)
    return {**entry, "source_text": source_text}
