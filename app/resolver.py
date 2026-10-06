"""
resolver.py — turn prose into holdings without guessing.

    "I like Nvidia, $AMD and TSM, but AI and IT stocks are ON fire, ALL of them. Ford (F)?"
        → holdings     NVDA (company name), AMD ($-prefixed), TSM (listed ticker)
        → suggestions  F — "Did you mean Ford (F)?"   (needs a click; never added silently)
        → ignored      AI, IT, ON, ALL               (ordinary words that also happen to be tickers)

Pipeline per candidate
    1. Find candidates: company names / aliases, "$TICK", "(TICK)", "ticker TICK", and ALL-CAPS tokens.
    2. Resolve against security metadata: the SEC list of US-listed companies (ticker → name, cached daily)
       plus MarketLab's own list of well-known names, funds and theme candidates.
    3. Confidence:
         high    company name or alias · $TICK · an ALL-CAPS listed ticker that isn't an ordinary word
         ask     an ordinary-word / single-letter ticker WITH context ("(F)", "$F" is high, "Ford", "ticker F")
         reject  ordinary words, unknown tokens, single letters without context
    4. Only "high" ever becomes a holding automatically; "ask" becomes a question.
"""

import re
import threading

from sources import sec

# Well-known names → ticker (lower-case keys; matched on word boundaries). Small on purpose: it covers how
# people actually write about the largest and most-discussed companies and funds.
ALIASES = {
    "nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT", "amazon": "AMZN", "alphabet": "GOOGL", "google": "GOOGL",
    "meta platforms": "META", "facebook": "META", "tesla": "TSLA", "broadcom": "AVGO", "advanced micro devices": "AMD",
    "taiwan semiconductor": "TSM", "tsmc": "TSM", "intel": "INTC", "netflix": "NFLX", "palantir": "PLTR", "ford": "F",
    "general motors": "GM", "berkshire hathaway": "BRK-B", "berkshire": "BRK-B", "jpmorgan": "JPM", "jp morgan": "JPM",
    "goldman sachs": "GS", "morgan stanley": "MS", "bank of america": "BAC", "visa": "V", "mastercard": "MA",
    "walmart": "WMT", "costco": "COST", "coca-cola": "KO", "coca cola": "KO", "pepsico": "PEP", "mcdonald's": "MCD",
    "mcdonalds": "MCD", "nike": "NKE", "disney": "DIS", "starbucks": "SBUX", "exxon": "XOM", "exxonmobil": "XOM",
    "chevron": "CVX", "eli lilly": "LLY", "lilly": "LLY", "novo nordisk": "NVO", "pfizer": "PFE", "johnson & johnson": "JNJ",
    "unitedhealth": "UNH", "merck": "MRK", "abbvie": "ABBV", "amgen": "AMGN", "oracle": "ORCL", "salesforce": "CRM",
    "adobe": "ADBE", "ibm": "IBM", "qualcomm": "QCOM", "micron": "MU", "asml": "ASML", "applied materials": "AMAT",
    "lam research": "LRCX", "texas instruments": "TXN", "arm holdings": "ARM", "marvell": "MRVL", "arista": "ANET",
    "super micro": "SMCI", "supermicro": "SMCI", "dell": "DELL", "snowflake": "SNOW", "servicenow": "NOW",
    "crowdstrike": "CRWD", "palo alto networks": "PANW", "zscaler": "ZS", "fortinet": "FTNT", "cloudflare": "NET",
    "shopify": "SHOP", "uber": "UBER", "airbnb": "ABNB", "coinbase": "COIN", "microstrategy": "MSTR", "paypal": "PYPL",
    "boeing": "BA", "lockheed martin": "LMT", "rtx": "RTX", "raytheon": "RTX", "northrop grumman": "NOC", "caterpillar": "CAT",
    "ionq": "IONQ", "rigetti": "RGTI", "d-wave": "QBTS", "quantum computing inc": "QUBT", "rocket lab": "RKLB",
    "ast spacemobile": "ASTS", "cameco": "CCJ", "oklo": "OKLO", "nuscale": "SMR", "constellation energy": "CEG",
    "vertiv": "VRT", "rivian": "RIVN", "albemarle": "ALB", "enphase": "ENPH", "honeywell": "HON", "samsung": "005930.KS",
    "spdr s&p 500": "SPY", "s&p 500 etf": "SPY", "nasdaq-100 etf": "QQQ", "invesco qqq": "QQQ", "vanguard s&p 500": "VOO",
    "total stock market": "VTI",
}
# Tickers that are also ordinary English words or common abbreviations: never added from ALL-CAPS text alone.
WORDLIKE = set("""
A I AI IT ON ALL ARE CAN NOW BIG GO SO DO BE AN AT BY OR US UK EU CEO CFO CTO ETF ETFS IPO GDP FED SEC USA OK NEW ONE TWO
KEY LOW HAS ANY WELL REAL SEE MAIN NEXT OPEN CASH FAST PLAY RUN EAT LOVE FUN CAR HOME TRUE GOOD BEST LIFE NICE PEAK SAFE
WISH FLY SKY AIR YOU HE ME MY WE IN OF TO IS THE AND FOR NOT BUT BUY SELL HOLD LONG SHORT PUT CALL MOVE GAIN RISK EV EVS AR
VR ML API CPU GPU GPUS TV PC PCS ROI EPS PE PS TAM YOY QOQ ATH DCA ESG IRA FY Q1 Q2 Q3 Q4 R D X Y Z F T V C K O M S U W
HOT SEE WAY OUT NET HUGE GROW TECH DATA CLOUD CHIP CHIPS PLUS ONLY MOST VERY JUST LIKE THEM THEY THIS THAT WITH FROM
INTO OVER ALSO EVEN EVER MUCH MANY MORE LESS BOTH EACH SUCH HIGH SAME DAY DAYS YEAR YEARS WEEK MONTH TIME NEAR FAR TOP
COST FUND FUNDS GAINS LOSS CAP MID SMALL LARGE MEGA BULL BEAR RATE RATES WAR OIL GAS GOLD
""".split())
# Funds and well-known securities MarketLab recognises even without the SEC list.
KNOWN = {"SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ (Nasdaq-100)", "VOO": "Vanguard S&P 500 ETF", "VTI": "Vanguard Total Stock Market ETF",
         "IWM": "iShares Russell 2000 ETF", "DIA": "SPDR Dow Jones ETF", "SMH": "VanEck Semiconductor ETF", "SOXX": "iShares Semiconductor ETF",
         "QTUM": "Defiance Quantum ETF", "ARKK": "ARK Innovation ETF", "VGT": "Vanguard Information Technology ETF", "XLK": "Technology Select Sector SPDR",
         "XLF": "Financial Select Sector SPDR", "XLE": "Energy Select Sector SPDR", "TLT": "iShares 20+ Year Treasury ETF", "GLD": "SPDR Gold",
         "URA": "Global X Uranium ETF", "NLR": "VanEck Uranium & Nuclear ETF", "BRK-B": "Berkshire Hathaway"}

_names_lock = threading.Lock()
_names = None


def listed():
    """{TICKER: company name} from the SEC list (cached by sources.sec) plus MarketLab's known list."""
    global _names
    with _names_lock:
        if _names is not None:
            return _names
    names = dict(KNOWN)
    for alias, t in ALIASES.items():
        names.setdefault(t, alias.title())
    try:
        if sec.is_configured():
            table = sec._get_json("https://www.sec.gov/files/company_tickers.json", 24 * 3600)
            for row in table.values():
                t = str(row.get("ticker", "")).upper().replace(".", "-")
                if t:
                    names.setdefault(t, str(row.get("title", "")).title())
    except Exception:                           # noqa: BLE001
        pass                                    # offline: MarketLab's own list still works
    with _names_lock:
        _names = names
    return names


def _name_mentions(text):
    """Company names / aliases in the text: [(ticker, alias, start)]."""
    low = text.lower()
    out = []
    for alias in sorted(ALIASES, key=len, reverse=True):          # longest first: "meta platforms" before "meta"
        for m in re.finditer(r"(?<![\w$])" + re.escape(alias) + r"(?![\w])", low):
            if any(s <= m.start() < e for _, _, s, e in out):
                continue
            out.append((ALIASES[alias], alias, m.start(), m.end()))
    return [(t, a, s) for t, a, s, _ in out]


def resolve(text, extra_known=()):
    text = text or ""
    names = listed()
    known = set(names) | set(extra_known)
    holdings, suggestions, ignored = {}, {}, []

    def add(t, why):
        if t not in holdings:
            holdings[t] = {"ticker": t, "name": names.get(t, t), "confidence": "high", "reason": why}
        suggestions.pop(t, None)

    mentioned = {}
    for t, alias, _ in _name_mentions(text):
        mentioned.setdefault(t, alias)
    # 1) explicit: $TICK
    for m in re.finditer(r"\$([A-Za-z]{1,5}(?:[.-][A-Za-z])?)\b", text):
        t = m.group(1).upper().replace(".", "-")
        if t in known:
            add(t, f"written as ${t}")
        else:
            ignored.append(f"${m.group(1)}")
    # 2) company names
    for t, alias in mentioned.items():
        add(t, f"company name “{alias}”")
    # 3) ALL-CAPS tokens
    for m in re.finditer(r"(?<![\w$])([A-Z]{1,5}(?:-[A-Z])?)(?![\w])", text):
        t = m.group(1)
        if t in holdings:
            continue
        before, after = text[max(0, m.start() - 12):m.start()], text[m.end():m.end() + 2]
        parenthesised = before.endswith("(") and after.startswith(")")
        tickered = re.search(r"(?:ticker|symbol|shares of|stock)\s*:?\s*$", before, re.I)
        if t not in known:
            if len(t) >= 2:
                ignored.append(t)
            continue
        if t in WORDLIKE or len(t) == 1:
            if parenthesised or tickered or t in mentioned:
                name = names.get(t, t)
                suggestions[t] = {"ticker": t, "name": name, "confidence": "ask",
                                  "question": f"Did you mean {name} ({t})?"}
            else:
                ignored.append(t)
            continue
        add(t, "listed ticker")
    return {"holdings": list(holdings.values()), "suggestions": list(suggestions.values()),
            "ignored": sorted({x for x in ignored if x.lower() not in ALIASES})}
