"""
sources/sec.py — SEC EDGAR, the US government's database of company filings.

Free and official. No API key, but the SEC's "fair access" rules require:
  * a User-Agent header with your name + email (set SEC_USER_AGENT in .env)
  * no more than 10 requests per second (we stay at 5)

Endpoints used:
  www.sec.gov/files/company_tickers.json                 ticker -> CIK (the SEC's company ID)
  data.sec.gov/submissions/CIK##########.json            company details + recent filings
  data.sec.gov/api/xbrl/companyfacts/CIK##########.json  every number the company has
                                                         filed in machine-readable form (XBRL)

Only US-listed companies that file with the SEC are covered. Foreign
listings (e.g. SHOP.TO), ETFs and indexes are not.

Ready for later stages: the same filings list gives links to every 10-K
(annual report: business, risk factors, segments), 10-Q (quarterly) and
8-K (material events), which future features can download and analyse.
"""

import threading
import time
from datetime import date

import requests

import config
from data import now_iso


class SecUnavailable(Exception):
    """SEC data can't be used right now (not configured, offline, or not a US filer)."""


# Friendly names for the filing types MarketLab shows.
FORM_NAMES = {
    "10-K": "Annual report",
    "10-K/A": "Annual report (amended)",
    "10-Q": "Quarterly report",
    "10-Q/A": "Quarterly report (amended)",
    "8-K": "Material event",
    "20-F": "Annual report (foreign company)",
    "40-F": "Annual report (Canadian company)",
    "6-K": "Current report (foreign company)",
    "DEF 14A": "Proxy statement",
}
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "40-F"}


# --- Polite, cached HTTP -----------------------------------------------------

_cache = {}                      # url -> (time fetched, parsed JSON)
_lock = threading.Lock()
_last_request_at = 0.0
SECONDS_BETWEEN_REQUESTS = 0.2   # max 5 requests/second (SEC allows 10)


def is_configured():
    return bool(config.SEC_USER_AGENT)


def _get_json(url, max_age_seconds):
    """Download JSON from the SEC, reusing a cached copy if it's fresh enough."""
    if not is_configured():
        raise SecUnavailable("SEC data is off. Add SEC_USER_AGENT to your .env file to turn it on.")

    cached = _cache.get(url)
    if cached and time.time() - cached[0] < max_age_seconds:
        return cached[1]

    global _last_request_at
    with _lock:  # one request at a time, spaced out
        wait = SECONDS_BETWEEN_REQUESTS - (time.time() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.time()

    headers = {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as error:
        raise SecUnavailable("Couldn't reach SEC EDGAR.") from error

    if response.status_code == 404:
        raise SecUnavailable("No SEC data found for this company.")
    if response.status_code == 403:
        raise SecUnavailable("The SEC refused the request. Check SEC_USER_AGENT in .env (name + email).")
    if not response.ok:
        raise SecUnavailable(f"SEC EDGAR returned an error ({response.status_code}).")

    data = response.json()
    _cache[url] = (time.time(), data)
    return data


# --- Company lookup ----------------------------------------------------------

def cik_for_ticker(ticker):
    """The SEC identifies companies by a number called a CIK, not by ticker."""
    table = _get_json("https://www.sec.gov/files/company_tickers.json", 24 * 3600)
    wanted = ticker.upper().replace(".", "-")   # the SEC writes BRK.B as BRK-B
    for row in table.values():
        if str(row.get("ticker", "")).upper() == wanted:
            return int(row["cik_str"])
    raise SecUnavailable(f"{ticker} isn't an SEC filer (common for ETFs, indexes and non-US listings).")


def filing_url(cik, accession_number, document=None):
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_number.replace('-', '')}"
    return f"{folder}/{document}" if document else f"{folder}/"


def get_company_filings(ticker, limit=12):
    """Official company details plus its most recent 10-K / 10-Q / 8-K style filings."""
    cik = cik_for_ticker(ticker)
    submissions = _get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", 3600)

    recent = submissions.get("filings", {}).get("recent", {})
    rows = zip(
        recent.get("form", []), recent.get("filingDate", []), recent.get("reportDate", []),
        recent.get("accessionNumber", []), recent.get("primaryDocument", []),
    )
    filings = []
    for form, filed, period, accession, document in rows:
        if form in FORM_NAMES:
            filings.append({
                "form": form,
                "description": FORM_NAMES[form],
                "filed": filed,
                "period": period or None,
                "url": filing_url(cik, accession, document),
            })
        if len(filings) >= limit:
            break

    address = submissions.get("addresses", {}).get("business", {}) or {}
    return {
        "cik": cik,
        "name": submissions.get("name"),
        "sic_description": submissions.get("sicDescription"),        # official industry code
        "fiscal_year_end": submissions.get("fiscalYearEnd"),         # e.g. "0927" = Sep 27
        "state_of_incorporation": submissions.get("stateOfIncorporationDescription")
                                  or submissions.get("stateOfIncorporation"),
        "business_city": (address.get("city") or "").title() or None,
        "business_state": address.get("stateOrCountryDescription") or address.get("stateOrCountry"),
        "filings": filings,
        "edgar_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=40",
        "retrieved_at": now_iso(),
    }


# --- Financial numbers (XBRL "company facts") --------------------------------

def get_company_facts(ticker):
    cik = cik_for_ticker(ticker)
    return cik, _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", 6 * 3600)


def annual_values(ticker, tags, unit="USD", combine="first"):
    """
    Full-fiscal-year values for one line item, oldest -> newest:
        [{"date": "2025-09-27", "value": 416161000000.0, "form": "10-K",
          "filed": "2025-10-31", "tag": "...", "url": "..."}, ...]

    tags     XBRL names to try. Companies don't all use the same name for the
             same thing (and some switch names over the years).
    combine  "first": the first tag in the list that has a value wins.
             "max":   take the largest value across tags. Used for revenue,
                      because a company may tag both total revenue and a
                      narrower "revenue from contracts" figure; total is larger.

    Data-quality rules:
      * only annual filings (10-K etc.) and only ~1-year periods (350-380
        days). 10-Ks also contain 3-month quarter figures; those are ignored.
      * a period can appear in several filings (each 10-K repeats prior
        years). We keep the most recently FILED value, so restatements win.
      * only US-GAAP data; companies reporting under IFRS aren't covered yet.
    """
    cik, facts = get_company_facts(ticker)
    gaap = facts.get("facts", {}).get("us-gaap", {})

    per_tag = []
    for tag in tags:
        best = {}
        for item in gaap.get(tag, {}).get("units", {}).get(unit, []):
            if item.get("form") not in ANNUAL_FORMS or not item.get("start") or item.get("val") is None:
                continue
            days = (date.fromisoformat(item["end"]) - date.fromisoformat(item["start"])).days
            if not 350 <= days <= 380:
                continue
            candidate = {
                "date": item["end"],
                "value": float(item["val"]),
                "form": item["form"],
                "filed": item.get("filed", ""),
                "tag": tag,
                "url": filing_url(cik, item.get("accn", "")),
            }
            existing = best.get(item["end"])
            if existing is None or candidate["filed"] > existing["filed"]:
                best[item["end"]] = candidate
        per_tag.append(best)

    merged = {}
    for best in per_tag:
        for end, candidate in best.items():
            if end not in merged:
                merged[end] = candidate
            elif combine == "max" and candidate["value"] > merged[end]["value"]:
                merged[end] = candidate

    return sorted(merged.values(), key=lambda point: point["date"])


def quarterly_values(ticker, tags, unit="USD", combine="max"):
    """
    Three-month (fiscal quarter) values for a line item, oldest -> newest.

    Companies file Q1-Q3 in 10-Qs, but Q4 usually only appears inside the
    annual 10-K. Where Q4 is missing we DERIVE it: annual total minus the
    three quarters inside that fiscal year. Derived points are marked
    "derived": True so the UI can say so.
    """
    cik, facts = get_company_facts(ticker)
    gaap = facts.get("facts", {}).get("us-gaap", {})
    quarters, annuals = {}, {}
    for tag in tags:
        for item in gaap.get(tag, {}).get("units", {}).get(unit, []):
            if not item.get("start") or item.get("val") is None:
                continue
            days = (date.fromisoformat(item["end"]) - date.fromisoformat(item["start"])).days
            target = quarters if 80 <= days <= 100 else annuals if 350 <= days <= 380 else None
            if target is None:
                continue
            key = item["end"]
            candidate = {"date": key, "start": item["start"], "value": float(item["val"]),
                         "form": item.get("form"), "filed": item.get("filed", ""), "derived": False}
            existing = target.get(key)
            better = existing is None or (combine == "max" and candidate["value"] > existing["value"]) \
                or (candidate["value"] == existing["value"] and candidate["filed"] > existing["filed"])
            if better:
                target[key] = candidate

    for end, annual in annuals.items():
        inside = [q for q in quarters.values() if annual["start"] <= q["start"] and q["date"] <= end]
        if len(inside) == 3 and end not in quarters:
            quarters[end] = {"date": end, "start": max(q["date"] for q in inside), "value": annual["value"] - sum(q["value"] for q in inside),
                             "form": annual["form"], "filed": annual["filed"], "derived": True}
    return sorted(quarters.values(), key=lambda q: q["date"])


def shares_outstanding(ticker):
    """Shares outstanding reported on filing cover pages (dei), oldest -> newest: [{date, value}]."""
    cik, facts = get_company_facts(ticker)
    points = {}
    for item in facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", []):
        if item.get("val"):
            points[item["end"]] = float(item["val"])   # several share classes on one date: keep one per date
    return [{"date": d, "value": v} for d, v in sorted(points.items())]


# --- Recent current reports (8-K / 6-K) as primary-source company events -----

ITEM_NAMES = {
    "1.01": "Entry into a material agreement", "1.02": "Termination of a material agreement",
    "1.03": "Bankruptcy or receivership", "1.05": "Material cybersecurity incident",
    "2.01": "Completion of an acquisition or disposition", "2.02": "Results of operations (earnings)",
    "2.03": "New material debt obligation", "2.05": "Exit or restructuring costs", "2.06": "Material impairment",
    "3.01": "Delisting notice", "3.02": "Unregistered sale of shares", "3.03": "Change to shareholder rights",
    "4.01": "Change of auditor", "4.02": "Prior financial statements no longer reliable",
    "5.01": "Change in control", "5.02": "Director or officer change", "5.03": "Charter or bylaw amendment",
    "5.07": "Shareholder vote results", "7.01": "Regulation FD disclosure", "8.01": "Other material event",
    "9.01": "Financial statements and exhibits",
}


def recent_current_reports(ticker, days=45):
    """
    8-K (US) / 6-K (foreign) filings from the last `days`, newest first:
    [{"form", "filed", "accepted", "items": ["2.02", ...], "item_names": [...], "url"}]
    The item codes say what kind of event the company is legally reporting.
    """
    from datetime import date, timedelta
    cik = cik_for_ticker(ticker)
    submissions = _get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", 1800)
    recent = submissions.get("filings", {}).get("recent", {})
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    rows = zip(recent.get("form", []), recent.get("filingDate", []), recent.get("acceptanceDateTime", []),
               recent.get("items", []), recent.get("accessionNumber", []), recent.get("primaryDocument", []))
    for form, filed, accepted, items, accession, document in rows:
        if filed < cutoff:
            break
        if form not in ("8-K", "6-K", "8-K/A"):
            continue
        codes = [c.strip() for c in (items or "").split(",") if c.strip()]
        out.append({"form": form, "filed": filed, "accepted": accepted or None, "items": codes,
                    "item_names": [ITEM_NAMES[c] for c in codes if c in ITEM_NAMES and c != "9.01"],
                    "url": filing_url(cik, accession, document), "company": submissions.get("name")})
    return out


# --- Filing documents as text (risk intelligence) ----------------------------
# Filed documents never change, so their extracted text is cached on disk forever.

import hashlib
import html as _html
import re as _re
from html.parser import HTMLParser

from config import CACHE_DIR

DOC_DIR = CACHE_DIR / "sec_docs"
_BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "td", "th"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "ix:header", "head"):
            self._skip += 1
        if tag in _BLOCK:
            self.parts.append("\n" if tag not in ("td", "th") else " ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "ix:header", "head") and self._skip:
            self._skip -= 1
        if tag in _BLOCK:
            self.parts.append("\n" if tag not in ("td", "th") else " ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw):
    raw = _re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", raw)
    parser = _TextExtractor()
    parser.feed(raw)
    text = "".join(parser.parts)
    text = _html.unescape(text).replace("\xa0", " ")
    text = _re.sub(r"[ \t\r\f\v]+", " ", text)
    text = _re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _get_raw(url):
    if not is_configured():
        raise SecUnavailable("SEC data is off. Add SEC_USER_AGENT to your .env file to turn it on.")
    global _last_request_at
    with _lock:
        wait = SECONDS_BETWEEN_REQUESTS - (time.time() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.time()
    try:
        response = requests.get(url, headers={"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    except requests.RequestException as error:
        raise SecUnavailable("Couldn't reach SEC EDGAR.") from error
    if not response.ok:
        raise SecUnavailable(f"SEC EDGAR returned an error ({response.status_code}) for a document.")
    return response.text


def document_text(url):
    """Plain text of a filed HTML document (cached on disk; filings are immutable)."""
    key = hashlib.sha1(url.encode()).hexdigest()
    path = DOC_DIR / f"{key}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    text = html_to_text(_get_raw(url))
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def filings_with_items(ticker, forms=None):
    """Every recent filing with accession number, 8-K item codes and document URL (newest first)."""
    cik = cik_for_ticker(ticker)
    submissions = _get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", 3600)
    recent = submissions.get("filings", {}).get("recent", {})
    out = []
    rows = zip(recent.get("form", []), recent.get("filingDate", []), recent.get("reportDate", []), recent.get("items", []),
               recent.get("accessionNumber", []), recent.get("primaryDocument", []))
    for form, filed, period, items, accession, document in rows:
        if forms and form not in forms:
            continue
        codes = [c.strip() for c in (items or "").split(",") if c.strip()]
        out.append({"form": form, "filed": filed, "period": period or None, "items": codes, "accession": accession,
                    "cik": cik, "url": filing_url(cik, accession, document), "company": submissions.get("name")})
    return out


def exhibit_url(cik, accession, wanted=("EX-99.1", "EX-99")):
    """URL of an exhibit (e.g. the earnings press release, EX-99.1) from the filing's index page."""
    index = filing_url(cik, accession, f"{accession}-index.htm")
    key = hashlib.sha1(index.encode()).hexdigest()
    path = DOC_DIR / f"{key}.idx"
    if path.exists():
        raw = path.read_text(encoding="utf-8")
    else:
        raw = _get_raw(index)
        DOC_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
    for row in _re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", raw):
        cells = _re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)
        if len(cells) < 4:
            continue
        kind = _re.sub(r"<[^>]+>", "", cells[3]).strip().upper()
        link = _re.search(r'href="([^"]+)"', cells[2]) or _re.search(r'href="([^"]+)"', row)
        if link and any(kind.startswith(w) for w in wanted):
            href = link.group(1)
            href = href.replace("/ix?doc=", "")
            return "https://www.sec.gov" + href if href.startswith("/") else href
    return None
