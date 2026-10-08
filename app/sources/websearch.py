"""
sources/websearch.py — live web search for Ask MarketLab, behind one small interface.

Ask MarketLab (assistant.py / freshness.py) only ever talks to `WebSearchTool`:

    tool = websearch.get_tool()               # None when search is off / unavailable
    results = tool.search("Rigetti Computing today")   # -> list of result dicts

A result is a plain dict:
    {"title", "url", "domain", "source" (publisher name), "published" (ISO or None),
     "precision" ("time" | "day" | None), "snippet", "provider", "query"}

Providers (all official; nothing here scrapes a website or calls a private endpoint):
    anthropic_api  Anthropic's server-side web search tool (needs ANTHROPIC_API_KEY).
    claude_code    Claude Code's built-in WebSearch tool, through your own signed-in
                   Claude Code (same personal-use arrangement as Ask MarketLab itself).
    To connect another legitimate provider, subclass WebSearchTool and call
    register("name", factory); then set WEB_SEARCH_PROVIDER=name. Ask MarketLab's
    reasoning does not change.

Everything a provider returns is untrusted text: it is only ever shown to the model as
labelled data, and URLs are only kept when they are http(s).
"""

import re
import urllib.parse
from datetime import datetime, timedelta, timezone

import config

try:
    from zoneinfo import ZoneInfo
    MARKET_TZ = ZoneInfo("America/New_York")
except Exception:                      # pragma: no cover - very old Python
    MARKET_TZ = timezone.utc


class WebSearchError(Exception):
    """A search could not be run (provider missing, network, limit...). Never fatal to Ask."""


# =============================================================================
# 1. The interface
# =============================================================================

class WebSearchTool:
    name = "base"
    label = "web search"

    def available(self):
        return True

    def search(self, query, max_results=6):
        """Return a list of result dicts (see module docstring). Raise WebSearchError on failure."""
        raise NotImplementedError


_registry = {}


def register(name, factory):
    """Connect another search provider: register('brave', lambda: BraveSearch())."""
    _registry[name] = factory


def get_tool():
    """The provider to use, or None when web search is switched off or nothing is available."""
    setting = (config.WEB_SEARCH_PROVIDER or "auto").lower()
    if setting in ("off", "none", "false", "0") or not config.WEB_SEARCH:
        return None
    if setting in _registry:
        tool = _registry[setting]()
        return tool if tool and tool.available() else None
    if setting == "auto":                              # follow whichever Claude connection Ask MarketLab uses
        from sources import llm
        setting = llm.assistant_status().get("provider") or ""
    if setting == "anthropic_api":
        tool = AnthropicWebSearch()
    elif setting == "claude_code":
        tool = ClaudeCodeWebSearch()
    else:
        return None
    return tool if tool.available() else None


# =============================================================================
# 2. Dates, domains, source quality
# =============================================================================

def domain_of(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def safe_url(url):
    try:
        parts = urllib.parse.urlparse((url or "").strip())
    except ValueError:
        return None
    return url.strip() if parts.scheme in ("http", "https") and parts.netloc else None


_MONTHS = ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y")


def parse_published(text, now=None):
    """
    Understands ISO timestamps, 'October 7, 2026', '3 hours ago', '2 days ago', 'yesterday'.
    Returns (iso string in UTC, precision) or (None, None). Never guesses: unreadable = undated.
    """
    now = now or datetime.now(timezone.utc)
    text = (text or "").strip()
    if not text:
        return None, None
    low = text.lower()
    m = re.match(r"^(an?|\d+)\s+(minute|min|hour|day|week)s?\s+ago$", low)
    if m:
        n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
        unit = m.group(2)
        if unit in ("minute", "min", "hour"):
            dt = now - timedelta(minutes=n) if unit != "hour" else now - timedelta(hours=n)
            return dt.isoformat(timespec="seconds"), "time"
        dt = now - timedelta(days=n if unit == "day" else 7 * n)
        return dt.astimezone(MARKET_TZ).replace(hour=12, minute=0, second=0).astimezone(timezone.utc).isoformat(timespec="seconds"), "day"
    if low == "yesterday":
        dt = (now.astimezone(MARKET_TZ) - timedelta(days=1)).replace(hour=12, minute=0, second=0)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds"), "day"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if "T" in text or ":" in text:
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds"), "time"
        dt = dt.replace(tzinfo=MARKET_TZ, hour=12)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds"), "day"
    except ValueError:
        pass
    for fmt in _MONTHS:
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=MARKET_TZ, hour=12)
            return dt.astimezone(timezone.utc).isoformat(timespec="seconds"), "day"
        except ValueError:
            continue
    return None, None


def bucket(published, now=None):
    """TODAY / THIS WEEK / OLDER / UNDATED, by the calendar date in New York (where the market is)."""
    if not published:
        return "undated"
    now = now or datetime.now(timezone.utc)
    try:
        when = datetime.fromisoformat(published).astimezone(MARKET_TZ).date()
    except ValueError:
        return "undated"
    days = (now.astimezone(MARKET_TZ).date() - when).days
    if days < -1:
        return "undated"                 # a date in the future is not trustworthy
    return "today" if days <= 0 else "week" if days <= 7 else "older"


# Source quality. Rank: lower = more trustworthy as a primary record.
_PRIMARY_WIRES = {"prnewswire.com", "businesswire.com", "globenewswire.com", "accesswire.com", "newsfilecorp.com"}
_REPORTING = {"reuters.com", "apnews.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com", "barrons.com", "nytimes.com",
              "marketwatch.com", "investors.com", "washingtonpost.com", "economist.com", "fortune.com", "forbes.com",
              "finance.yahoo.com", "morningstar.com", "axios.com", "theinformation.com", "bbc.com", "cnn.com"}
_INDUSTRY = {"thequantuminsider.com", "quantumcomputingreport.com", "semiconductor-digest.com", "semianalysis.com",
             "fiercebiotech.com", "fiercepharma.com", "statnews.com", "techcrunch.com", "theverge.com", "arstechnica.com",
             "datacenterdynamics.com", "tomshardware.com", "eetimes.com", "spglobal.com", "zdnet.com", "wired.com"}
_COMMENTARY = {"seekingalpha.com", "fool.com", "benzinga.com", "investorplace.com", "zacks.com", "tipranks.com",
               "stocktitan.net", "msn.com", "yahoo.com", "nasdaq.com", "thestreet.com", "insidermonkey.com", "247wallst.com"}
_SOCIAL = {"reddit.com", "x.com", "twitter.com", "stocktwits.com", "facebook.com", "youtube.com", "tiktok.com", "threads.net",
           "medium.com", "substack.com", "quora.com", "discord.com", "telegram.org", "t.me", "linkedin.com"}


def _in(domain, group):
    return any(domain == d or domain.endswith("." + d) for d in group)


def source_quality(domain, company_domain=None):
    """-> (rank, label). 0 primary · 1 wire/major reporting · 2 industry · 3 commentary/aggregator · 4 social/unverified."""
    domain = (domain or "").lower()
    if domain == "sec.gov" or domain.endswith(".sec.gov"):
        return 0, "SEC filing"
    if domain.endswith(".gov") or domain.endswith(".mil") or domain.endswith(".gov.uk") or domain == "europa.eu" or domain.endswith(".europa.eu"):
        return 0, "Government source"
    if company_domain and (domain == company_domain or domain.endswith("." + company_domain)):
        return 0, "Company source"
    if re.match(r"^(ir|investors?|investor-relations|press|newsroom|news)\.", domain):
        return 0, "Company source"
    if _in(domain, _PRIMARY_WIRES):
        return 0, "Press release (company-issued)"
    if domain in ("reuters.com", "apnews.com") or domain.endswith(".reuters.com"):
        return 1, "Wire service"
    if _in(domain, _REPORTING):
        return 1, "Major financial/news outlet"
    if _in(domain, _INDUSTRY):
        return 2, "Industry reporting"
    if _in(domain, _SOCIAL):
        return 4, "Social post / unverified"
    if _in(domain, _COMMENTARY):
        return 3, "Commentary / aggregator"
    return 3, "Other source (not vetted)"


def company_domain(website):
    d = domain_of(website or "")
    return d or None


def _norm_url(url):
    p = urllib.parse.urlparse(url)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def clean_results(results, query="", provider=""):
    """Drop unsafe URLs and exact duplicates; fill domain/source; parse dates. Keeps provider order."""
    out, seen = [], set()
    for r in results or []:
        url = safe_url(r.get("url"))
        title = re.sub(r"\s+", " ", str(r.get("title") or "")).strip()
        if not url or not title:
            continue
        key = _norm_url(url)
        if key in seen:
            continue
        seen.add(key)
        published, precision = r.get("published"), r.get("precision")
        if published and not precision:
            published, precision = parse_published(published)
        elif not published and r.get("page_age"):
            published, precision = parse_published(r["page_age"])
        domain = domain_of(url)
        out.append({"title": title[:300], "url": url, "domain": domain,
                    "source": (r.get("source") or domain or "Unknown")[:80],
                    "published": published, "precision": precision if published else None,
                    "snippet": re.sub(r"\s+", " ", str(r.get("snippet") or "")).strip()[:500] or None,
                    "provider": provider or r.get("provider") or "", "query": query})
    return out


# =============================================================================
# 3. Provider: Anthropic API web search tool
# =============================================================================

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}


class AnthropicWebSearch(WebSearchTool):
    """Anthropic's own server-side web search tool, through the Anthropic API."""
    name = "anthropic_api"
    label = "Anthropic web search"

    def available(self):
        return bool(config.ANTHROPIC_API_KEY)

    def search(self, query, max_results=6):
        import anthropic
        from sources import llm
        try:
            response = llm._get_client().messages.create(
                model=config.AI_MODEL_FAST, max_tokens=400,
                tools=[WEB_SEARCH_TOOL],
                tool_choice={"type": "tool", "name": "web_search"},
                messages=[{"role": "user", "content": f"Search the web for: {query}\nRun the search once. Reply with the single word done."}],
            )
        except anthropic.AnthropicError as error:
            raise WebSearchError(str(llm._friendly(error))) from error
        return self.parse(response.content, query)[:max_results]

    @staticmethod
    def parse(blocks, query):
        """Pull results (and the engine's own quoted passages) out of a Messages API response."""
        rows, snippets = [], {}
        for block in blocks or []:
            kind = getattr(block, "type", None) if not isinstance(block, dict) else block.get("type")
            get = (lambda k, d=None: block.get(k, d)) if isinstance(block, dict) else (lambda k, d=None: getattr(block, k, d))
            if kind == "web_search_tool_result":
                content = get("content")
                if isinstance(content, dict) or (content is not None and hasattr(content, "error_code")):
                    code = content.get("error_code") if isinstance(content, dict) else content.error_code
                    raise WebSearchError(f"Web search failed ({code}).")
                for item in content or []:
                    g = item.get if isinstance(item, dict) else (lambda k, d=None: getattr(item, k, d))
                    rows.append({"title": g("title"), "url": g("url"), "page_age": g("page_age")})
            elif kind == "text":
                for cite in get("citations") or []:
                    g = cite.get if isinstance(cite, dict) else (lambda k, d=None: getattr(cite, k, d))
                    if g("url") and g("cited_text"):
                        snippets.setdefault(g("url"), g("cited_text"))
        for row in rows:
            row["snippet"] = snippets.get(row["url"])
        return clean_results(rows, query, "anthropic_api")


# =============================================================================
# 4. Provider: Claude Code's built-in WebSearch (your own sign-in)
# =============================================================================

class ClaudeCodeWebSearch(WebSearchTool):
    """Claude Code's official WebSearch tool, run headless with every other tool switched off."""
    name = "claude_code"
    label = "Claude Code web search"

    def available(self):
        from sources import claude_code
        status = claude_code.status()
        return bool(status["installed"] and status["signed_in"])

    PROMPT = (
        "Use the WebSearch tool once to search for: {query}\n"
        "Then, for at most 3 of the most relevant NEWS results, you may use WebFetch to read the page so you can see when it was "
        "published. Reply with ONLY a JSON array (no prose, no code fence) of up to 8 objects "
        '{{"url": ..., "title": ..., "published": ..., "snippet": ...}}. Use only URLs that the tools returned. '
        '"published" must be the date/time shown on the page or in the search result, copied as written, or null if you did not see one - '
        "never estimate it. \"snippet\" is at most 25 words and must restate what the page says, nothing more."
    )

    def search(self, query, max_results=6):
        from sources import claude_code
        try:
            events = claude_code.run_with_tools(self.PROMPT.format(query=query), tools="WebSearch,WebFetch",
                                                model=config.AI_MODEL_FAST, max_turns=6, timeout=120)
        except claude_code.ClaudeCodeError as error:
            raise WebSearchError(str(error)) from error
        return self.parse(events, query)[:max_results]

    @staticmethod
    def parse(events, query):
        """
        Trust only what the tools returned: a result is kept only if its URL appeared in a WebSearch result list or
        was fetched. Claude's own JSON adds the publication date/snippet it read on the page (never the URL itself).
        """
        import json
        links, texts, final = {}, [], ""
        for event in events or []:
            kind = event.get("type")
            if kind == "result" and not event.get("is_error"):
                final = str(event.get("result") or "")
            for part in (event.get("message") or {}).get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_result":
                    content = part.get("content")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    texts.append(str(content or ""))
                elif part.get("type") == "tool_use" and isinstance(part.get("input"), dict) and part["input"].get("url"):
                    links.setdefault(_norm_url(part["input"]["url"]), {"url": part["input"]["url"], "title": None})
        for text in texts:
            for m in re.finditer(r"Links:\s*(\[.*?\])\s*(?:\n|$)", text, re.S):
                try:
                    for item in json.loads(m.group(1)):
                        if isinstance(item, dict) and item.get("url"):
                            links.setdefault(_norm_url(item["url"]), {"url": item["url"], "title": item.get("title"),
                                                                       "page_age": item.get("page_age") or item.get("date")})
                except ValueError:
                    continue
        said = {}
        m = re.search(r"\[.*\]", final, re.S)
        if m:
            try:
                for item in json.loads(m.group(0)):
                    if isinstance(item, dict) and item.get("url"):
                        said[_norm_url(str(item["url"]))] = item
            except ValueError:
                pass
        rows = []
        for key, link in links.items():
            extra = said.get(key, {})
            rows.append({"title": link.get("title") or extra.get("title"), "url": link["url"],
                         "page_age": extra.get("published") or link.get("page_age"), "snippet": extra.get("snippet")})
        rows.sort(key=lambda r: 0 if r["url"] and _norm_url(r["url"]) in said else 1)
        return clean_results(rows, query, "claude_code")


# Neither official tool is guaranteed to return a date for every result, so undated results are labelled UNDATED and
# are never presented as "today" (see bucket()).
