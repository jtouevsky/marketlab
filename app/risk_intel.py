"""
risk_intel.py — Dependencies & Exposures, Government & Policy, Controversies & Material
Events, and Guidance History, built ONLY from sources that can be shown.

Sources (in order of preference):
    1. SEC filings: the latest annual report (10-K / 20-F) text, 8-K item codes,
       and 8-K earnings releases (exhibit 99.1)
    2. Government primary sources and reporting already collected by the News layer
       (Federal Register, FTC/DOJ releases, news indexes)
    3. Yahoo Finance / SEC SIC for the industry label

Rules:
    * Every item carries its evidence (the sentence or headline), a source URL and a date.
    * A company/entity is only listed as a dependency when a sentence in a filing ties it
      to a relationship (supplier, customer, partner...). Merely being mentioned isn't enough.
    * Quantitative exposure (a % of revenue) is shown only when the filing states it, and is
      kept separate from qualitative dependencies.
    * Macro exposures come from the company's own risk factors; correlations are elsewhere.
    * Controversy statuses distinguish allegation, investigation, charge, lawsuit, settlement,
      dismissal, judgment, regulatory action and confirmed incident. News items are labeled
      "as reported" and never upgraded to findings.
    * Guidance is COMPANY guidance from earnings releases; analyst estimates are elsewhere.
    * Each section reports a state: ok | no_evidence | not_run | error — "nothing found"
      is never confused with "not checked".
"""

import re
import threading
import time
from datetime import date, datetime

from sources import sec, yahoo

CACHE_SECONDS = 6 * 3600
_cache = {}
_lock = threading.Lock()

# ---------------------------------------------------------------- text helpers

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(])")


def sentences(text):
    out = []
    for para in re.split(r"\n{1,}", text):
        para = para.strip()
        if len(para) < 40:
            continue
        for s in SENTENCE.split(para):
            s = s.strip()
            if 40 <= len(s) <= 1500:
                out.append(s)
    return out


def clip(s, n=420):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def section(text, start_pattern, end_pattern, min_len=3000):
    """The longest span between a heading and the next heading (skips the table of contents)."""
    best = ""
    for m in re.finditer(start_pattern, text, re.I):
        end = re.search(end_pattern, text[m.end():], re.I)
        span = text[m.start(): m.end() + (end.start() if end else 200000)]
        if len(span) > len(best):
            best = span
    return best if len(best) >= min_len else ""


def annual_report_sections(text):
    risk = section(text, r"item\s*1a\.?\s*[\-–—:]?\s*risk\s+factors", r"item\s*1b\.?|item\s*1c\.?|item\s*2\.?\s*[\-–—:]?\s*properties")
    business = section(text, r"item\s*1\.?\s*[\-–—:]?\s*business\b", r"item\s*1a\.?\s*[\-–—:]?\s*risk", min_len=400)
    legal = section(text, r"item\s*3\.?\s*[\-–—:]?\s*legal\s+proceedings", r"item\s*4\.?", min_len=150)
    if not risk:     # 20-F and unusual layouts: fall back to the "Risk Factors" heading anywhere
        risk = section(text, r"\brisk\s+factors\b", r"\bunresolved staff comments\b|\binformation on the company\b|\bproperties\b")
    return {"risk": risk, "business": business, "legal": legal, "all": text}


# ---------------------------------------------------------------- dependencies

ENTITIES = [
    # name, pattern, ticker (if listed in the US), kind hint
    ("TSMC", r"Taiwan Semiconductor Manufacturing|\bTSMC\b", "TSM", "manufacturer"),
    ("Samsung", r"\bSamsung\b", None, "manufacturer"),
    ("Intel", r"\bIntel\b(?! Corporation’s)", "INTC", None),
    ("GlobalFoundries", r"GlobalFoundries|GLOBALFOUNDRIES", "GFS", "manufacturer"),
    ("SK hynix", r"SK [Hh]ynix", None, "supplier"),
    ("Micron", r"\bMicron\b", "MU", "supplier"),
    ("Foxconn (Hon Hai)", r"Foxconn|Hon Hai", None, "manufacturer"),
    ("Pegatron", r"\bPegatron\b", None, "manufacturer"),
    ("Wistron", r"\bWistron\b", None, "manufacturer"),
    ("Quanta", r"\bQuanta Computer\b", None, "manufacturer"),
    ("Amkor", r"\bAmkor\b", "AMKR", "supplier"),
    ("ASE", r"Advanced Semiconductor Engineering|\bASE (Technology|Group)\b", "ASX", "supplier"),
    ("ASML", r"\bASML\b", "ASML", "supplier"),
    ("Applied Materials", r"Applied Materials", "AMAT", "supplier"),
    ("Lam Research", r"Lam Research", "LRCX", "supplier"),
    ("Broadcom", r"\bBroadcom\b", "AVGO", None),
    ("Qualcomm", r"\bQualcomm\b", "QCOM", None),
    ("Arm", r"\bArm Holdings\b|\bArm Limited\b|\bARM\b(?= (architecture|license))", "ARM", None),
    ("AMD", r"Advanced Micro Devices|\bAMD\b", "AMD", None),
    ("NVIDIA", r"\bNVIDIA\b", "NVDA", None),
    ("Apple", r"\bApple\b(?! Pay)", "AAPL", None),
    ("Microsoft", r"\bMicrosoft\b|\bAzure\b", "MSFT", None),
    ("Amazon / AWS", r"\bAmazon\b|Amazon Web Services|\bAWS\b", "AMZN", None),
    ("Alphabet / Google", r"\bAlphabet\b|\bGoogle\b", "GOOGL", None),
    ("Meta", r"\bMeta Platforms\b|\bMeta\b(?=,| and| or|’s)", "META", None),
    ("Oracle", r"\bOracle\b", "ORCL", None),
    ("Dell", r"\bDell\b", "DELL", None),
    ("HP", r"\bHP Inc\b|Hewlett[- ]Packard Enterprise|\bHPE\b", "HPQ", None),
    ("Lenovo", r"\bLenovo\b", None, None),
    ("Super Micro", r"Super Micro|Supermicro", "SMCI", None),
    ("IBM", r"\bIBM\b", "IBM", None),
    ("Cisco", r"\bCisco\b", "CSCO", None),
    ("Walmart", r"\bWalmart\b", "WMT", None),
    ("Costco", r"\bCostco\b", "COST", None),
    ("Target", r"\bTarget Corporation\b", "TGT", None),
    ("Home Depot", r"Home Depot", "HD", None),
    ("Best Buy", r"Best Buy", "BBY", None),
    ("AT&T", r"\bAT&T\b", "T", None),
    ("Verizon", r"\bVerizon\b", "VZ", None),
    ("T-Mobile", r"T-Mobile", "TMUS", None),
    ("Visa", r"\bVisa\b(?! (application|requirement))", "V", None),
    ("Mastercard", r"\bMastercard\b", "MA", None),
    ("McKesson", r"\bMcKesson\b", "MCK", "distributor"),
    ("Cencora", r"\bCencora\b|AmerisourceBergen", "COR", "distributor"),
    ("Cardinal Health", r"Cardinal Health", "CAH", "distributor"),
    ("Arrow Electronics", r"Arrow Electronics", "ARW", "distributor"),
    ("Avnet", r"\bAvnet\b", "AVT", "distributor"),
    ("Tesla", r"\bTesla\b", "TSLA", None),
    ("Boeing", r"\bBoeing\b", "BA", None),
    ("Lockheed Martin", r"Lockheed Martin", "LMT", None),
    ("RTX", r"\bRaytheon\b|RTX Corporation", "RTX", None),
    ("U.S. government", r"U\.S\. (federal )?[Gg]overnment|United States [Gg]overnment|U\.S\. Department of Defense|Department of Defense|\bDoD\b", None, "government"),
    ("Medicare / Medicaid", r"\bMedicare\b|\bMedicaid\b", None, "government"),
]
RELATIONS = [
    ("Manufacturer / supplier", r"manufactur|foundr|fabricat|\bwafers?\b|supplier|suppl(y|ies) (us|our|the)|sole[- ]source|single[- ]source|"
                                r"components?|assembl|packag|contract manufactur|we (purchase|procure|source|rely on)"),
    ("Customer", r"\bcustomers?\b|accounted for|of (our |total )?(net )?(revenue|sales)|sales to|revenue from|purchas(e|es|ed) (our|from us)"),
    ("Cloud / infrastructure", r"\bcloud\b|data cent(er|re)s?|hosting|infrastructure (provider|services)"),
    ("Distributor / channel", r"distributors?|distribution|resellers?|wholesal|retail partners?"),
    ("Partner", r"partner(s|ship)?|collaborat|alliance|joint venture|strategic (agreement|relationship)"),
    ("Competitor", r"\bcompet(e|es|itor|itors|ition|itive)\b"),
]
GEOGRAPHIES = [
    ("China", r"\bChina\b|\bPRC\b|Chinese"), ("Taiwan", r"\bTaiwan\b"), ("Hong Kong", r"Hong Kong"),
    ("Japan", r"\bJapan\b"), ("South Korea", r"South Korea|\bKorea\b"), ("India", r"\bIndia\b"),
    ("Israel", r"\bIsrael\b"), ("Vietnam", r"\bVietnam\b"), ("Malaysia", r"\bMalaysia\b"), ("Singapore", r"\bSingapore\b"),
    ("Mexico", r"\bMexico\b"), ("Canada", r"\bCanada\b"), ("Europe", r"\bEurope(an Union)?\b|\bEU\b"),
    ("United Kingdom", r"United Kingdom|\bU\.K\.\b"), ("Germany", r"\bGermany\b"), ("Ireland", r"\bIreland\b"),
    ("Russia / Ukraine", r"\bRussia\b|\bUkraine\b"), ("Middle East", r"Middle East|\bIsrael\b|\bSaudi\b|\bUAE\b"),
]
GEO_REASONS = [
    ("manufacturing / supply", r"manufactur|supplier|foundr|production|facilit|fab\b|assembl"),
    ("sales / customers", r"revenue|sales|customers|demand|market"),
    ("trade / geopolitics", r"export|tariff|sanction|tension|conflict|\bwar\b|geopolit|government|restrict"),
]
MACRO = [
    ("Interest rates", r"interest rates?"),
    ("Inflation", r"\binflation"),
    ("Currency / U.S. dollar", r"foreign currenc|exchange rates?|strengthening of the U\.S\. dollar|currency fluctuations"),
    ("Commodities / energy", r"commodit|oil prices|energy (prices|costs)|raw materials|\bcopper\b|\bsteel\b|aluminum|lithium"),
    ("Consumer spending", r"consumer (spending|demand|confidence)|discretionary spending"),
    ("Business / IT spending", r"(IT|technology|capital) (spending|expenditures?) (by|of) (our )?customers|enterprise spending|customers’ capital expenditures"),
    ("Economic cycle", r"recession|economic (downturn|slowdown|uncertainty)|macroeconomic"),
    ("Credit / financing", r"credit markets|access to (capital|financing)|borrowing costs|capital markets"),
    ("Industry cyclicality", r"\bcyclical|cyclicality"),
]
POLICY = [
    ("Export controls", r"export control|Entity List|Bureau of Industry and Security|\bBIS\b|export licen[cs]|Export Administration Regulations"),
    ("Tariffs & trade", r"\btariffs?\b|trade (war|restrictions|policy|tensions|barriers)|Section 301|anti-?dumping|countervailing"),
    ("Government contracts & procurement", r"government contracts?|U\.S\. government (customers|agencies|contracts)|Department of Defense|federal agencies|procurement"),
    ("Subsidies & incentives", r"CHIPS (and Science )?Act|subsid(y|ies)|government grants?|tax credits?|Inflation Reduction Act"),
    ("Antitrust & competition law", r"antitrust|competition (law|authorit)|Federal Trade Commission|\bFTC\b|Department of Justice|European Commission|\bSAMR\b|Digital Markets Act"),
    ("Sector regulation", r"\bFDA\b|Food and Drug Administration|\bFCC\b|\bFAA\b|\bFERC\b|\bEPA\b|privacy (law|regulation)s?|\bGDPR\b|data protection"),
    ("Tax policy", r"tax (law|legislation|reform)|global minimum tax|Pillar Two|\bOECD\b"),
    ("Defense & national security", r"defense (budget|spending)|national security"),
    ("Energy & climate policy", r"energy policy|emissions (regulation|standards)|climate (regulation|legislation)"),
    ("Healthcare policy", r"\bMedicare\b|\bMedicaid\b|drug pricing|reimbursement rates?"),
]

PCT_OF = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%\s+of\s+(?:our\s+|the\s+company[’']s\s+)?(?:total\s+|net\s+|consolidated\s+|worldwide\s+)*"
                    r"(revenues?|net sales|sales|accounts receivable|receivables|purchases|inventory purchases)", re.I)


def _count(pattern, text):
    return len(re.findall(pattern, text, re.I))


def _competition_span(business):
    """The 'Competition' subsection of Item 1 (entities listed there are competitors, whatever the wording)."""
    m = re.search(r"\n\s*(Competition|Competitive Landscape|Our Competitors)\s*\n", business or "")
    return business[m.end(): m.end() + 4000] if m else ""


def dependencies(sections, company_name, ticker, source):
    risk_sents = sentences(sections["risk"]) if sections["risk"] else []
    biz_sents = sentences(sections["business"]) if sections["business"] else []
    competition = set(sentences(_competition_span(sections["business"])))
    pool = [("Risk factors", s) for s in risk_sents] + [("Business", s) for s in biz_sents]
    if not pool:
        pool = [("Annual report", s) for s in sentences(sections["all"])[:6000]]
    own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", company_name or "")} - {"inc", "corp", "corporation", "company", "holdings", "the", "group"}
    entities = []
    for name, pattern, sym, hint in ENTITIES:
        if sym and sym == ticker:
            continue
        if any(w in name.lower() for w in own if len(w) > 3):
            continue
        found = {}
        for where, s in pool:
            if not re.search(pattern, s):
                continue
            rels = ["Competitor"] if s in competition else [label for label, rp in RELATIONS if re.search(rp, s, re.I)]
            if not rels:
                continue
            for r in rels[:2]:
                found.setdefault(r, []).append((where, s))
        if found:
            relation = max(found, key=lambda r: len(found[r]))
            evidence = [{"text": clip(s), "section": where} for where, s in found[relation][:3]]
            entities.append({"name": name, "ticker": sym, "kind": "company" if hint != "government" else "government",
                             "relationship": relation, "other_relationships": [r for r in found if r != relation],
                             "mentions": sum(len(v) for v in found.values()), "evidence": evidence, "source": source,
                             "basis": "qualitative"})
    entities.sort(key=lambda e: -e["mentions"])

    # quantitative concentration: only what the filing states as a percentage
    quantitative, seen = [], set()
    for where, s in pool + [("Notes", x) for x in sentences(sections["all"]) if PCT_OF.search(x)]:
        if re.match(r"\s*\*", s) or re.search(r"less than \d", s, re.I):
            continue                      # "less than 10%" footnotes are not a disclosed concentration
        if not PCT_OF.search(s) or not re.search(r"customer|distributor|reseller|partner|supplier|countr|region|outside the United States|"
                                                  r"United States|China|Taiwan|Europe|Asia|one of our|largest", s, re.I):
            continue
        key = re.sub(r"\W+", "", s.lower())[-160:]
        if key in seen:
            continue
        seen.add(key)
        subject = re.search(r"Customer [A-Z]\b|\b(?:[Oo]ne|[Tt]wo|[Tt]hree|[Ff]our|[Aa]nother) (?:direct |indirect )?customers?|largest customers?|"
                             r"one distributor|distributors?|outside (of )?the United States|United States|China|Taiwan|Europe|Asia[- ]Pacific", s)
        year = re.search(r"(?:fiscal (?:year )?)?(20\d\d)", s)
        for m in PCT_OF.finditer(s):
            fact = (m.group(1), m.group(2).lower(), year.group(1) if year else None)
            if fact in seen:
                continue
            seen.add(fact)
            quantitative.append({"subject": subject.group(0) if subject else "see sentence", "percent": float(m.group(1)),
                                 "of": m.group(2).lower(), "evidence": clip(s, 520), "section": where, "source": source,
                                 "basis": "disclosed"})
        if len(quantitative) >= 16:
            break

    geos = []
    risk_and_biz = " ".join(s for _, s in pool)
    for name, pattern in GEOGRAPHIES:
        hits = [s for _, s in pool if re.search(pattern, s)]
        relevant = [(s, [label for label, rp in GEO_REASONS if re.search(rp, s, re.I)]) for s in hits]
        relevant = [(s, r) for s, r in relevant if r]
        if len(relevant) >= 2:
            reasons = {}
            for _, rs in relevant:
                for r in rs:
                    reasons[r] = reasons.get(r, 0) + 1
            geos.append({"name": name, "mentions": len(relevant), "why": sorted(reasons, key=lambda r: -reasons[r])[:2],
                         "evidence": [{"text": clip(s)} for s, _ in relevant[:2]], "source": source})
    geos.sort(key=lambda g: -g["mentions"])

    macro = []
    risk_text = sections["risk"] or ""
    for name, pattern in MACRO:
        hits = [s for s in risk_sents if re.search(pattern, s, re.I)]
        if len(hits) >= 1:
            macro.append({"name": name, "mentions": len(hits), "evidence": [{"text": clip(s)} for s in hits[:2]],
                          "basis": "company-identified risk factor", "source": source})
    macro.sort(key=lambda m: -m["mentions"])
    return {"entities": entities[:24], "quantitative": quantitative, "geographies": geos[:10], "macro": macro,
            "has_risk_section": bool(risk_text)}


def policy_disclosures(sections, source):
    pool = sentences(sections["risk"] or "") + sentences(sections["business"] or "")
    out = []
    for name, pattern in POLICY:
        hits = [s for s in pool if re.search(pattern, s, re.I)]
        if hits:
            out.append({"topic": name, "mentions": len(hits), "evidence": [{"text": clip(s)} for s in hits[:3]], "source": source,
                        "basis": "company disclosure"})
    out.sort(key=lambda p: -p["mentions"])
    return out


# ---------------------------------------------------------------- government actions (news layer)

GOV_ENTITIES = [
    ("Trump administration", r"\bTrump\b|White House"), ("Biden administration", r"\bBiden\b"),
    ("U.S. Department of Commerce", r"Commerce Department|Department of Commerce|\bCommerce Secretary\b|Bureau of Industry and Security"),
    ("U.S. Treasury", r"\bTreasury\b"), ("USTR", r"\bUSTR\b|Trade Representative"),
    ("FTC", r"\bFTC\b|Federal Trade Commission"), ("U.S. Department of Justice", r"\bDOJ\b|Justice Department|Department of Justice"),
    ("SEC", r"\bSEC\b(?! filing)|Securities and Exchange Commission"), ("FDA", r"\bFDA\b|Food and Drug Administration"),
    ("Pentagon / DoD", r"Pentagon|Department of Defense|\bDoD\b|Department of War"), ("U.S. Congress", r"Congress|Senate|House (of Representatives|committee)|lawmakers"),
    ("European Commission / EU", r"European Commission|\bEU\b|Brussels"), ("China (government)", r"Beijing|China’s (commerce|regulator)|\bSAMR\b|\bMOFCOM\b|Chinese (government|regulators?)"),
    ("Federal Reserve", r"Federal Reserve|\bthe Fed\b"), ("Courts", r"\bcourt\b|\bjudge\b"),
]
GOV_STATUS = {"Proposal": "PROPOSAL", "Statement": "STATEMENT", "Investigation": "INVESTIGATION", "Agency action": "AGENCY ACTION",
              "Legislation passed": "LEGISLATION", "Court ruling": "COURT RULING", "Implemented policy": "IMPLEMENTED POLICY",
              "Award or contract": "GOVERNMENT CONTRACT / AWARD"}


def government_actions(news, disclosures):
    rows = []
    for e in news:
        if not e.get("government"):
            continue
        text = f"{e['headline']} {e.get('summary') or ''}"
        entities = [name for name, pattern in GOV_ENTITIES if re.search(pattern, e["headline"])] or \
                   [name for name, pattern in GOV_ENTITIES if re.search(pattern, text)]
        topics = [name for name, pattern in POLICY if re.search(pattern, text, re.I)]
        disclosed = {d["topic"]: d for d in disclosures}
        link = next((t for t in topics if t in disclosed), None)
        primary = e.get("primary") or {}
        src = primary or (e["sources"][0] if e["sources"] else {})
        rows.append({"date": e["published"][:10], "action": e["headline"], "entity": ", ".join(entities[:3]) if entities else (primary.get("name") or "Government (unspecified)"),
                     "entities": entities, "status": GOV_STATUS.get(e["government"]["kind"], "REPORTED"), "topics": topics,
                     "connection": (f"The company's annual report discusses {link.lower()} as a risk." if link else
                                    "Mentions the company; MarketLab hasn't established a business connection beyond that."),
                     "source": {"name": src.get("name"), "url": src.get("url"), "type": src.get("type")},
                     "source_count": e.get("source_count", 1), "primary_source": bool(primary)})
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:20]


# ---------------------------------------------------------------- controversies

CONTRO_CATEGORIES = [
    ("Antitrust", r"antitrust|competition law|monopol|anti-competitive"),
    ("Accounting issue", r"restat|non-reliance|material weakness|accounting (irregularit|error)"),
    ("Fraud allegation", r"\bfraud"),
    ("Cybersecurity / data breach", r"cyber|data breach|ransomware|unauthorized access|security incident"),
    ("Product failure / recall", r"recall|defect|product liability"),
    ("Securities litigation", r"securities (class action|laws?|fraud)|shareholder (class action|lawsuit|derivative)|class action|derivative (action|lawsuit|complaint|suit)"),
    ("Executive misconduct", r"misconduct|harassment"),
    ("Bankruptcy / distress", r"bankrupt|chapter 11|going concern|delist|receivership"),
    ("Major layoffs / restructuring", r"layoff|job cuts|workforce reduction|restructuring|exit (or|and) disposal"),
    ("Environmental / operational", r"environmental|emissions|spill|explosion|outage|\bfire\b|contaminat"),
    ("Patent / IP litigation", r"patent|intellectual property|infring"),
    ("Government investigation", r"investigat|subpoena|civil investigative demand|\binquiry\b|\bprobe\b"),
    ("Regulatory action", r"\bfined?\b|penalt|consent (order|decree)|cease[- ]and[- ]desist|sanction"),
    ("Lawsuit", r"lawsuit|litigation|complaint|\bsued\b|\bsues\b|\bsuits?\b"),
]
STATUS_RULES = [
    ("DISMISSED", r"\bdismiss(ed|es|al)\b|thrown out|\bdrops? (its |the |a )?(\w+ )?(lawsuit|suit|case|claims?)\b|withdr(ew|aws?) (its |the )?(lawsuit|suit|complaint)"),
    ("SETTLED", r"\bsettle(d|s|ment)\b"),
    ("JUDGMENT", r"\b(judgment|verdict|found liable|ruled against|ordered to pay|jury award)"),
    ("CHARGE", r"\b(charged|indicted|indictment|criminal charges?)\b"),
    ("REGULATORY ACTION", r"\b(fined|imposed a fine|penalty of|consent (order|decree)|cease[- ]and[- ]desist|enforcement action)\b"),
    ("RESOLVED", r"\b(resolve[sd]?|concluded (its|the) investigation|closed (its|the) investigation|no further action)\b"),
    ("LAWSUIT FILED", r"\b(filed (a |an )?(putative )?(lawsuit|complaint|suit|class action)|sued|sues|lawsuits?|class actions?|complaints?)\b"),
    ("INVESTIGATION", r"\b(investigat\w*|subpoena\w*|inquiry|probe|civil investigative demand)\b"),
    ("CONFIRMED INCIDENT", r"\b(breach|cyber ?attack|ransomware|recall(ed|s)?|outage|explosion|spill)\b"),
    ("ALLEGATION", r"\b(alleg\w*|accus\w*)\b"),
]
ITEM_EVENTS = {
    "1.05": ("Cybersecurity / data breach", "CONFIRMED INCIDENT", "The company reported a material cybersecurity incident (8-K Item 1.05)."),
    "4.02": ("Accounting issue", "CONFIRMED INCIDENT", "The company said previously issued financial statements should no longer be relied upon (8-K Item 4.02)."),
    "1.03": ("Bankruptcy / distress", "CONFIRMED INCIDENT", "Bankruptcy or receivership (8-K Item 1.03)."),
    "2.05": ("Major layoffs / restructuring", "CONFIRMED INCIDENT", "The company committed to an exit or restructuring plan with material costs (8-K Item 2.05)."),
    "3.01": ("Bankruptcy / distress", "REGULATORY ACTION", "Notice of delisting or failure to meet a listing standard (8-K Item 3.01)."),
}
MONTHS = r"(January|February|March|April|May|June|July|August|September|October|November|December)"


def classify_status(text, default="DISCLOSED"):
    for status, pattern in STATUS_RULES:
        if re.search(pattern, text, re.I):
            return status
    return default


def classify_category(text):
    for name, pattern in CONTRO_CATEGORIES:
        if re.search(pattern, text, re.I):
            return name
    return None


def _date_in(text, fallback):
    m = re.search(MONTHS + r"\s+(\d{1,2},\s+)?(20\d\d|19\d\d)", text)
    if m:
        month = datetime.strptime(m.group(1), "%B").month
        day = int(m.group(2).strip(", ")) if m.group(2) else 1
        return f"{m.group(3)}-{month:02d}-{day:02d}", bool(m.group(2))
    return fallback, False


def controversies(filings, sections, annual, news):
    events = []
    for f in filings:
        for code in f.get("items", []):
            if code in ITEM_EVENTS:
                cat, status, text = ITEM_EVENTS[code]
                events.append({"date": f["filed"], "date_exact": True, "category": cat, "status": status, "title": text,
                               "description": text, "entity": f.get("company"), "relevance": "Reported by the company in a legally required filing.",
                               "sources": [{"name": f"SEC {f['form']} (Item {code})", "url": f["url"], "type": "primary_regulatory"}],
                               "origin": "filing"})
    legal = sections.get("legal") or ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", legal) if len(p.strip()) > 120]
    if annual:
        notes = [p.strip() for p in re.split(r"\n\s*\n", sections["all"]) if 200 < len(p.strip()) < 4000
                 and re.search(r"\b(lawsuit|class action|complaint|subpoena|investigation|antitrust|settle)\w*", p, re.I)
                 and re.search(r"\b(filed|court|plaintiff|alleg|regulator|Commission|Department of Justice|agency)\b", p, re.I)]
        paras += notes[:12]
    seen = set()
    for p in paras:
        if re.match(r"\s*(From time to time|We may|We could|To prevent|This choice|Our (amended and restated )?(certificate|bylaws|charter))", p, re.I) \
                or re.search(r"choice[- ]of[- ]forum|exclusive forum", p, re.I) \
                or not re.search(r"\b(filed|captioned|plaintiffs?|defendants?|court of|district court|v\.\s|In re\b|subpoena|settlement agreement|"
                                  r"investigation into|civil investigative demand|consent (order|decree))\b", p, re.I):
            continue                      # boilerplate or hypothetical, not a specific matter
        cat = classify_category(p)
        if not cat or cat in ("Major layoffs / restructuring",):
            continue
        key = re.sub(r"\W+", "", p.lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        when, exact = _date_in(p, annual["filed"] if annual else None)
        parts = SENTENCE.split(re.sub(r"\s+", " ", p))
        first = next((x for x in parts if len(x) >= 60), parts[0])
        events.append({"date": when, "date_exact": exact, "category": cat, "status": classify_status(p),
                       "title": clip(first, 200), "description": clip(p, 900),
                       "entity": next((name for name, pat in GOV_ENTITIES if re.search(pat, p)), None),
                       "relevance": ("The company says it does not expect a material effect." if re.search(r"not (expect|believe).{0,60}material", p, re.I)
                                     else "Disclosed in the annual report's legal or contingencies discussion."),
                       "sources": [{"name": f"{annual['form']} filed {annual['filed']}", "url": annual["url"], "type": "primary_regulatory"}] if annual else [],
                       "origin": "annual report"})
    for e in news:
        text = f"{e['headline']} {e.get('summary') or ''}"
        # only adverse actions or incidents involving the company (not commentary about policy)
        if not re.search(r"\b(lawsuits?|sued|sues|suing|class action|probes?|probing|investigat\w*|subpoena\w*|recalls?|recalled|breach|hack(ed)?|"
                         r"layoffs?|job cuts|fined|fines|penalt\w*|settles?|settled|settlement|charged|indicted|antitrust (probe|suit|case|investigation|lawsuit)|"
                         r"accused|alleg\w*|whistleblower|restat\w*|outage)\b", e["headline"], re.I):
            continue
        cat = classify_category(e["headline"]) or classify_category(text)
        if not cat:
            continue
        status = classify_status(e["headline"], default="REPORTED")
        if status == "CONFIRMED INCIDENT" and not e.get("primary"):
            status = "REPORTED"            # a confirmed incident needs a primary source
        events.append({"date": e["published"][:10], "date_exact": True, "category": cat, "status": status,
                       "title": e["headline"], "description": e.get("summary") or e["headline"],
                       "entity": next((name for name, pat in GOV_ENTITIES if re.search(pat, text)), None),
                       "relevance": f"As reported by {e.get('source_count', 1)} publisher{'s' if e.get('source_count', 1) != 1 else ''}; not a finding.",
                       "sources": [{"name": s["name"], "url": s["url"], "type": s.get("type")} for s in e["sources"][:4]],
                       "origin": "news"})
    events.sort(key=lambda e: e["date"] or "", reverse=True)
    return events[:40]


# ---------------------------------------------------------------- guidance

METRICS = [
    ("Revenue", r"\b(total )?(net )?(revenues?|net sales|sales)\b"),
    ("Gross margin", r"gross margins?"),
    ("Operating margin", r"operating margins?"),
    ("Operating expenses", r"operating expenses"),
    ("EPS", r"(earnings|net income|net loss|loss) per (diluted )?share|\bEPS\b"),
    ("Capital expenditures", r"capital expenditures|\bcapex\b"),
    ("Free cash flow", r"free cash flow"),
    ("Tax rate", r"tax rate"),
]
ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}
MONEY = r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(billion|million)?"


def _money(v, unit):
    x = float(v.replace(",", ""))
    return x * (1e9 if unit == "billion" else 1e6 if unit == "million" else 1)


def parse_values(sentence, metric):
    """Return (low, high, unit) from a guidance sentence, or None."""
    pct = metric in ("Gross margin", "Operating margin", "Tax rate")
    if pct:
        m = re.search(r"(\d{1,2}(?:\.\d+)?)%\s*(?:,\s*)?plus or minus\s*(\d+)\s*basis points", sentence)
        if m:
            mid, bps = float(m.group(1)), float(m.group(2)) / 100
            return mid - bps, mid + bps, "%"
        m = re.search(r"(\d{1,2}(?:\.\d+)?)%\s*(?:to|and|-|–)\s*(\d{1,2}(?:\.\d+)?)%", sentence)
        if m:
            return float(m.group(1)), float(m.group(2)), "%"
        m = re.search(r"(\d{1,2}(?:\.\d+)?)%", sentence)
        return (float(m.group(1)), float(m.group(1)), "%") if m else None
    per_share = metric == "EPS"
    m = re.search(MONEY + r"\s*,?\s*plus or minus\s*(\d+(?:\.\d+)?)%", sentence)
    if m and not per_share:
        mid = _money(m.group(1), m.group(2))
        p = float(m.group(3)) / 100
        return mid * (1 - p), mid * (1 + p), "$"
    m = re.search(MONEY + r"\s*(?:to|and|-|–)\s*" + MONEY, sentence)
    if m:
        unit = m.group(4) or m.group(2)
        return _money(m.group(1), m.group(2) or unit), _money(m.group(3), unit), "$/share" if per_share else "$"
    m = re.search(MONEY, sentence)
    if m:
        v = _money(m.group(1), m.group(2))
        return v, v, "$/share" if per_share else "$"
    return None


def guidance_period(text, filed):
    m = re.search(r"(first|second|third|fourth) quarter of fiscal(?: year)? (\d{4})", text, re.I)
    if m:
        return f"Q{ORDINAL[m.group(1).lower()]} FY{m.group(2)}"
    m = re.search(r"(first|second|third|fourth)[- ]quarter (?:of )?(\d{4})", text, re.I)
    if m:
        return f"Q{ORDINAL[m.group(1).lower()]} {m.group(2)}"
    m = re.search(r"(?:full[- ]year|fiscal(?: year)?|for the year)\s*(\d{4})|(\d{4})\s*(?:full[- ]year|fiscal year)", text, re.I)
    if m:
        return f"FY{m.group(1) or m.group(2)}"
    if re.search(r"next quarter|the (upcoming|coming) quarter|current quarter", text, re.I):
        return f"Next quarter (as of {filed})"
    return None


def extract_guidance(text, filed, url):
    """Guidance statements in one earnings release (only sentences with an expectation verb, a metric and a number)."""
    region = text
    head = re.search(r"\n\s*(Outlook|Business Outlook|Financial Outlook|Guidance|Fiscal \d{4} Outlook)[^\n]{0,120}\n", text, re.I)
    if head:
        region = text[head.start(): head.start() + 5000]
    period_hint = guidance_period(region[:400], filed)
    items = []
    withdrawn = bool(re.search(r"withdr(a|e)w(s|n|ing)? (its |our |the )?(financial )?(guidance|outlook)|suspend(s|ed|ing)? (its |our )?(guidance|outlook)", text, re.I))
    for s in sentences(region):
        if not re.search(r"\b(expect|expects|expected|anticipat\w*|outlook|guidance|forecast\w*|project(s|ed)?)\b", s, re.I):
            continue
        if re.search(r"analysts?|consensus", s, re.I):
            continue                      # analyst estimates are not company guidance
        period = guidance_period(s, filed) or period_hint
        both = re.search(r"GAAP and non-GAAP (gross margins?|operating expenses|operating margins?)[^.]*?"
                         r"(\$?\d[\d.,]*\s*(?:billion|million)?%?)\s+and\s+(\$?\d[\d.,]*\s*(?:billion|million)?%?),? respectively", s, re.I)
        if both:
            metric = next(name for name, p in METRICS if re.search(p, both.group(1), re.I))
            tail = s[both.end():]
            for qual, raw in (("GAAP", both.group(2)), ("non-GAAP", both.group(3))):
                values = parse_values(raw + tail, metric)
                if values:
                    items.append(_gitem(metric, qual, period, values, s, filed, url))
            continue
        for metric, pattern in METRICS:
            if re.search(pattern, s, re.I):
                values = parse_values(s, metric)
                if values and not (metric == "Revenue" and values[2] == "$" and values[1] < 1e6):
                    qual = "non-GAAP" if re.search(r"non-GAAP|adjusted", s, re.I) else "GAAP" if re.search(r"\bGAAP\b", s) else ""
                    items.append(_gitem(metric, qual, period, values, s, filed, url))
                break
    return items, withdrawn


def _gitem(metric, qual, period, values, sentence, filed, url):
    low, high, unit = values
    return {"metric": f"{metric}{' (' + qual + ')' if qual else ''}", "period": period, "low": low, "high": high,
            "mid": (low + high) / 2, "unit": unit, "date": filed, "text": clip(sentence, 400), "url": url}


def classify_guidance(items):
    """INITIATED / RAISED / LOWERED / REITERATED per (metric, period); MIXED at release level."""
    by_key = {}
    for it in sorted(items, key=lambda x: x["date"]):
        key = (it["metric"], it["period"])
        prev = by_key.get(key)
        if prev is None:
            it["change"] = "INITIATED"
        else:
            it["previous"] = {"low": prev["low"], "high": prev["high"], "mid": prev["mid"], "date": prev["date"]}
            if it["unit"] == "%":
                diff = it["mid"] - prev["mid"]
                it["change"] = "RAISED" if diff > 0.1 else "LOWERED" if diff < -0.1 else "REITERATED"
            else:
                rel = (it["mid"] / prev["mid"] - 1) if prev["mid"] else 0
                it["change"] = "RAISED" if rel > 0.005 else "LOWERED" if rel < -0.005 else "REITERATED"
            if it["change"] == "REITERATED" and (it["high"] - it["low"]) < (prev["high"] - prev["low"]) * 0.9:
                it["note"] = "range narrowed"
        by_key[key] = it
    releases = {}
    for it in items:
        releases.setdefault(it["date"], []).append(it["change"])
    for it in items:
        changes = set(releases[it["date"]])
        it["release_change"] = "MIXED" if {"RAISED", "LOWERED"} <= changes else None
    return items


def guidance_history(ticker, filings, max_releases=10):
    releases = [f for f in filings if f["form"] in ("8-K", "8-K/A") and "2.02" in f.get("items", [])][:max_releases]
    if not releases:
        return {"state": "no_evidence", "items": [], "events": [],
                "message": "No earnings releases (8-K Item 2.02) found in recent SEC filings."}
    items, withdrawn, checked = [], [], 0
    for f in releases:
        try:
            url = sec.exhibit_url(f["cik"], f["accession"])
            if not url:
                continue
            found, wd = extract_guidance(sec.document_text(url), f["filed"], url)
            checked += 1
            items += found
            if wd:
                withdrawn.append({"date": f["filed"], "change": "WITHDRAWN", "url": url,
                                  "text": "The release says guidance was withdrawn or suspended."})
        except sec.SecUnavailable:
            continue
    items = [i for i in items if i["period"]]
    classify_guidance(items)
    _attach_actuals(ticker, items)
    if not items and not withdrawn:
        return {"state": "no_evidence", "items": [], "events": [], "checked": checked,
                "message": f"No numeric company guidance found in the last {checked} earnings releases. "
                           "Some companies (Apple, for example) don't publish numeric guidance in their releases."}
    return {"state": "ok", "items": sorted(items, key=lambda i: i["date"], reverse=True), "events": withdrawn, "checked": checked}


def _attach_actuals(ticker, items):
    """Compare quarterly revenue guidance with the reported quarter (SEC XBRL), when the quarter can be matched."""
    wanted = [i for i in items if i["metric"].startswith("Revenue") and i["period"] and i["period"].startswith("Q")]
    if not wanted:
        return
    try:
        quarters = sec.quarterly_values(ticker, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"])
    except Exception:
        return
    for it in wanted:
        # the reported quarter is the first quarter ending after the guidance date, within ~5 months
        guided = date.fromisoformat(it["date"])
        after = [q for q in quarters if guided < date.fromisoformat(q["date"]) <= date.fromordinal(guided.toordinal() + 150)]
        if after:
            q = after[0]
            it["actual"] = {"value": q["value"], "period_end": q["date"], "source": "SEC XBRL (10-Q/10-K)",
                            "vs_mid": q["value"] / it["mid"] - 1 if it["mid"] else None, "derived": q.get("derived", False)}


# ---------------------------------------------------------------- orchestration

def build(ticker, refresh=False):
    with _lock:
        hit = _cache.get(ticker)
        if hit and not refresh and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    result = _build(ticker)
    with _lock:
        _cache[ticker] = (time.time(), result)
    return result


def _state(state, message=None, **extra):
    return {"state": state, "message": message, **extra}


def _build(ticker):
    out = {"ticker": ticker, "retrieved_at": datetime.now().isoformat(timespec="seconds"), "sources_used": []}
    try:
        info = yahoo.get_info(ticker)
    except Exception:
        info = {}
    name = info.get("longName") or info.get("shortName") or ticker
    industry = {"sector": info.get("sector"), "industry": info.get("industry"), "source": "Yahoo Finance"}

    # SEC: filings + annual report text
    filings, annual, sections = [], None, None
    if not sec.is_configured():
        sec_problem = "SEC data is off (add SEC_USER_AGENT to .env), so filings couldn't be read."
    else:
        sec_problem = None
        try:
            filings = sec.filings_with_items(ticker)
            annual = next((f for f in filings if f["form"] in ("10-K", "20-F", "40-F")), None)
            if annual:
                sections = annual_report_sections(sec.document_text(annual["url"]))
                out["sources_used"].append({"name": f"{annual['form']} filed {annual['filed']}", "url": annual["url"]})
        except sec.SecUnavailable as error:
            sec_problem = str(error)
    source = {"name": f"{annual['form']} filed {annual['filed']}", "url": annual["url"], "date": annual["filed"]} if annual else None

    # News layer (already multi-source; cached)
    news, news_problem = [], None
    try:
        import tab_news
        data = tab_news.build_news(ticker)
        news = data.get("events", [])
        out["sources_used"].append({"name": f"News layer: {len(data.get('providers', []))} providers", "url": None})
    except Exception as error:      # noqa: BLE001 - shown to the user
        news_problem = f"News sources unavailable ({type(error).__name__})."

    # Dependencies & exposures
    if sections:
        dep = dependencies(sections, name, ticker, source)
        found = dep["entities"] or dep["quantitative"] or dep["geographies"] or dep["macro"]
        out["dependencies"] = {"state": "ok" if found else "no_evidence", **dep, "industry": industry, "source": source,
                               "message": None if found else "No dependencies stated clearly enough in the annual report to list."}
    else:
        out["dependencies"] = _state("not_run" if not sec_problem else "error", sec_problem or "No annual report (10-K / 20-F) found.",
                                     industry=industry)

    # Government & policy
    disclosures = policy_disclosures(sections, source) if sections else []
    actions = government_actions(news, disclosures) if news else []
    if sections or news:
        out["policy"] = {"state": "ok" if (disclosures or actions) else "no_evidence", "disclosures": disclosures, "actions": actions,
                         "message": None if (disclosures or actions) else "No government or policy exposure identified in connected sources.",
                         "notes": [m for m in (sec_problem, news_problem) if m]}
    else:
        out["policy"] = _state("error", "; ".join(m for m in (sec_problem, news_problem) if m) or "No sources available.")

    # Controversies & material events
    if filings or news:
        events = controversies(filings, sections or {"legal": "", "all": ""}, annual, news)
        out["controversies"] = {"state": "ok" if events else "no_evidence", "events": events,
                                "message": None if events else "No major controversies identified from connected sources.",
                                "notes": [m for m in (sec_problem, news_problem) if m],
                                "coverage": "SEC 8-K item codes in recent filings, the latest annual report's legal sections, and recent news (about a month)."}
    else:
        out["controversies"] = _state("error", "; ".join(m for m in (sec_problem, news_problem) if m) or "No sources available.")

    # Guidance
    if filings:
        try:
            out["guidance"] = guidance_history(ticker, filings)
        except Exception as error:   # noqa: BLE001
            out["guidance"] = _state("error", f"Guidance history unavailable ({type(error).__name__}).")
    else:
        out["guidance"] = _state("not_run" if not sec_problem else "error",
                                 sec_problem or "Guidance history unavailable from currently connected sources.")
    out["company"] = name
    return out


# ---------------------------------------------------------------- light exposure read (Investment Lab)

THEME_TERMS = {
    "ai": r"\bAI\b|artificial intelligence|generative|accelerated computing|machine learning|large language model",
    "data_center": r"data cent(?:er|re)s?",
    "hyperscale": r"hyperscale|cloud service providers?|\bCSPs?\b",
    "quantum": r"\bquantum\b",
    "crypto": r"crypto|bitcoin|digital assets?",
    "ev": r"electric vehicles?|\bEVs?\b",
    "defense": r"\bdefen[cs]e\b|military|national security",
    "nuclear": r"\bnuclear\b|uranium|reactor",
    "semis": r"semiconductors?|wafers?|\bchips?\b",
}
_exposure_cache = {}


def exposures(ticker):
    """
    What the latest annual report says the company depends on (the same extractors as the Risk tab,
    without news or guidance, so it is fast enough to run for a whole basket). Cached for 6 hours.
    """
    with _lock:
        hit = _exposure_cache.get(ticker)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    out = {"ticker": ticker, "state": "ok", "message": None}
    try:
        info = yahoo.get_info(ticker)
    except Exception:
        info = {}
    out.update(company=info.get("longName") or info.get("shortName") or ticker, sector=info.get("sector"),
               industry=info.get("industry"), quote_type=info.get("quoteType"))
    if (info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND"):
        out.update(state="fund", message="A fund: its look-through holdings aren't analysed yet; it is treated as one diversified position.")
        result = out
    elif not sec.is_configured():
        out.update(state="not_run", message="SEC data is off (add SEC_USER_AGENT to .env).")
        result = out
    else:
        try:
            filings = sec.filings_with_items(ticker)
            annual = next((f for f in filings if f["form"] in ("10-K", "20-F", "40-F")), None)
            if not annual:
                out.update(state="no_evidence", message="No annual report (10-K / 20-F) found.")
            else:
                sections = annual_report_sections(sec.document_text(annual["url"]))
                source = {"name": f"{annual['form']} filed {annual['filed']}", "url": annual["url"], "date": annual["filed"]}
                dep = dependencies(sections, out["company"], ticker, source)
                text = (sections.get("business") or "") + " " + (sections.get("risk") or "")
                out.update(dependencies=dep, policy=policy_disclosures(sections, source), source=source,
                           themes={k: len(re.findall(p, text, re.I)) for k, p in THEME_TERMS.items()})
        except Exception as error:      # noqa: BLE001 - shown as a state
            out.update(state="error", message=f"SEC filings unavailable ({type(error).__name__}).")
        result = out
    with _lock:
        _exposure_cache[ticker] = (time.time(), result)
    return result
