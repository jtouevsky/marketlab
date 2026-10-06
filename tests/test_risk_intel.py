"""
Tests for risk_intel.py: dependency parsing, disclosed concentration, controversy
statuses, guidance parsing/classification, and honest missing-data states.

Run:  python3 -m unittest discover tests -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import risk_intel as RI     # noqa: E402

TEN_K = """
Table of Contents
Item 1. Business 3
Item 1A. Risk Factors 10

Item 1. Business

We design chips. We utilize foundries, such as Taiwan Semiconductor Manufacturing Company Limited, or TSMC, to produce our wafers.
We purchase memory from SK Hynix Inc. and Micron Technology, Inc. for our products.
Our products are offered through cloud partners such as Amazon Web Services and Microsoft Azure data centers to end customers.

Competition

Our competitors include large companies such as Intel Corporation and Broadcom Inc. that design similar products for the same markets.

Item 1A. Risk Factors

Sales to one direct customer represented 22% of total revenue for fiscal year 2026, and sales to another direct customer represented 14% of total revenue for fiscal year 2026.
* Customer accounted for less than 10% of revenue in the respective periods for fiscal year 2025.
Revenue from customers outside of the United States accounted for 41% of total revenue for fiscal year 2026.
New export controls issued by the U.S. Department of Commerce, including the Entity List, restrict our sales of products to China and could reduce our revenue materially.
Tariffs and trade restrictions between the United States and China could increase our costs and affect demand from customers in China for our products.
Our manufacturing is concentrated in Taiwan, and geopolitical tension involving Taiwan could disrupt our supply of wafers from our suppliers there.
Changes in interest rates and inflation could reduce demand from our customers and increase our cost of capital over time.
Fluctuations in foreign currency exchange rates could affect our reported results in future periods significantly.
Apple is a well-known company that is mentioned here without any relationship to our business at all whatsoever.
""" + ("Additional risk discussion that pads this section to a realistic length. " * 80) + """

Item 1B. Unresolved Staff Comments

None.

Item 3. Legal Proceedings

From time to time, we may be subject to actions, claims, suits and other legal proceedings arising in the ordinary course of business.

On March 7, 2025, the court dismissed the putative securities class action lawsuit captioned In re Example Securities Litigation, filed in the United States District Court.

Item 4. Mine Safety Disclosures
"""


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.sections = RI.annual_report_sections(TEN_K)
        self.dep = RI.dependencies(self.sections, "Example Corp", "EXMP", {"name": "10-K", "url": "u", "date": "2026-02-25"})
        self.by_name = {e["name"]: e for e in self.dep["entities"]}

    def test_sections_skip_table_of_contents(self):
        self.assertIn("one direct customer", self.sections["risk"])
        self.assertIn("TSMC", self.sections["business"])
        self.assertIn("In re Example", self.sections["legal"])

    def test_relationships_come_from_the_sentence(self):
        self.assertEqual(self.by_name["TSMC"]["relationship"], "Manufacturer / supplier")
        self.assertEqual(self.by_name["Micron"]["relationship"], "Manufacturer / supplier")
        self.assertEqual(self.by_name["Intel"]["relationship"], "Competitor")          # listed under Competition
        self.assertIn("Amazon / AWS", self.by_name)
        self.assertNotIn("Apple", self.by_name)          # mentioned, but no relationship stated → not a dependency
        self.assertEqual(self.by_name["TSMC"]["ticker"], "TSM")
        self.assertTrue(self.by_name["TSMC"]["evidence"][0]["text"].startswith("We utilize foundries"))

    def test_quantitative_is_only_what_is_stated(self):
        q = {(x["percent"], x["of"]) for x in self.dep["quantitative"]}
        self.assertIn((22.0, "revenue"), q)
        self.assertIn((14.0, "revenue"), q)
        self.assertIn((41.0, "revenue"), q)
        self.assertNotIn((10.0, "revenue"), q)            # "less than 10%" footnote ignored
        self.assertTrue(all(x["basis"] == "disclosed" for x in self.dep["quantitative"]))
        self.assertTrue(all(e["basis"] == "qualitative" for e in self.dep["entities"]))

    def test_geography_and_macro_need_a_reason(self):
        geos = {g["name"]: g for g in self.dep["geographies"]}
        self.assertIn("China", geos)
        self.assertIn("trade / geopolitics", geos["China"]["why"])
        macro = {m["name"] for m in self.dep["macro"]}
        self.assertTrue({"Interest rates", "Inflation", "Currency / U.S. dollar"} <= macro)
        self.assertTrue(all(m["basis"] == "company-identified risk factor" for m in self.dep["macro"]))

    def test_policy_disclosures(self):
        topics = {p["topic"] for p in RI.policy_disclosures(self.sections, None)}
        self.assertTrue({"Export controls", "Tariffs & trade"} <= topics)


class ControversyTests(unittest.TestCase):
    def test_status_wording(self):
        cases = {
            "Judge dismisses lawsuit against the company": "DISMISSED",
            "Company settles patent suit": "SETTLED",
            "Jury finds company liable, verdict of $10 million": "JUDGMENT",
            "Executive charged with fraud": "CHARGE",
            "Company fined $5 million by regulator": "REGULATORY ACTION",
            "Shareholders file class action lawsuit": "LAWSUIT FILED",
            "DOJ opens probe into acquisition": "INVESTIGATION",
            "Short seller alleges accounting problems": "ALLEGATION",
            "Plaintiff drops lawsuit over app": "DISMISSED",
            "Parties resolve antitrust dispute": "RESOLVED",
        }
        for text, status in cases.items():
            self.assertEqual(RI.classify_status(text, "REPORTED"), status, text)
        self.assertEqual(RI.classify_status("Company hosts investor day", "REPORTED"), "REPORTED")

    def test_events_from_filings_annual_report_and_news(self):
        sections = RI.annual_report_sections(TEN_K)
        annual = {"form": "10-K", "filed": "2026-02-25", "url": "https://sec/10k"}
        filings = [{"form": "8-K", "filed": "2025-06-01", "items": ["1.05", "9.01"], "url": "https://sec/8k", "company": "Example"},
                   {"form": "8-K", "filed": "2025-07-01", "items": ["2.02"], "url": "https://sec/8k2", "company": "Example"}]
        news = [{"headline": "Example Corp faces DOJ probe over deal", "summary": "", "published": "2026-09-10T12:00:00",
                 "categories": ["Legal"], "sources": [{"name": "Reuters", "url": "https://r", "type": "reporting"}], "source_count": 3, "primary": None},
                {"headline": "Example CEO opposes antitrust carve-outs for AI labs", "summary": "", "published": "2026-09-11T12:00:00",
                 "categories": ["Legal"], "sources": [{"name": "X", "url": "https://x"}], "source_count": 1, "primary": None},
                {"headline": "Example Corp confirms data breach, reports say", "summary": "", "published": "2026-09-12T12:00:00",
                 "categories": [], "sources": [{"name": "Y", "url": "https://y"}], "source_count": 1, "primary": None}]
        events = RI.controversies(filings, sections, annual, news)
        kinds = [(e["origin"], e["status"], e["category"]) for e in events]
        self.assertIn(("filing", "CONFIRMED INCIDENT", "Cybersecurity / data breach"), kinds)       # 8-K Item 1.05
        self.assertIn(("annual report", "DISMISSED", "Securities litigation"), kinds)
        self.assertIn(("news", "INVESTIGATION", "Government investigation"), kinds)
        self.assertFalse([e for e in events if "carve-outs" in e["title"]])                    # commentary, not a controversy
        breach = next(e for e in events if "breach" in e["title"])
        self.assertEqual(breach["status"], "REPORTED")       # news alone can't make it a confirmed incident
        self.assertFalse([e for e in events if e["title"].startswith("From time to time")])     # boilerplate skipped
        dated = next(e for e in events if e["origin"] == "annual report")
        self.assertEqual(dated["date"], "2025-03-07")
        self.assertTrue(all(e["sources"] for e in events))


class GuidanceTests(unittest.TestCase):
    RELEASE = """NVIDIA Announces Financial Results
Revenue was a record.

Outlook
NVIDIA's outlook for the third quarter of fiscal 2027 is as follows:
Revenue is expected to be $108.0 billion, plus or minus 2%.
GAAP and non-GAAP gross margins are expected to be 73.3% and 73.5%, respectively, plus or minus 50 basis points.
Full-year fiscal 2027 GAAP and non-GAAP tax rates are expected to be 16.0% to 18.0%.
Analysts expect revenue of $110 billion.
"""

    def test_parse_values(self):
        low, high, unit = RI.parse_values("Revenue is expected to be $108.0 billion, plus or minus 2%.", "Revenue")
        self.assertAlmostEqual(low, 105.84e9, delta=1e6)
        self.assertAlmostEqual(high, 110.16e9, delta=1e6)
        self.assertEqual(unit, "$")
        self.assertEqual(RI.parse_values("EPS between $1.20 and $1.30", "EPS"), (1.2, 1.3, "$/share"))
        self.assertEqual(RI.parse_values("gross margin of 45% to 47%", "Gross margin"), (45.0, 47.0, "%"))

    def test_extract_release(self):
        items, withdrawn = RI.extract_guidance(self.RELEASE, "2026-08-26", "https://sec/ex99")
        got = {(i["metric"], i["period"]): i for i in items}
        self.assertIn(("Revenue", "Q3 FY2027"), got)
        self.assertIn(("Gross margin (GAAP)", "Q3 FY2027"), got)
        self.assertIn(("Gross margin (non-GAAP)", "Q3 FY2027"), got)
        self.assertAlmostEqual(got[("Gross margin (non-GAAP)", "Q3 FY2027")]["low"], 73.0)
        self.assertFalse([i for i in items if "110" in i["text"] and "Analysts" in i["text"]])   # analyst numbers excluded
        self.assertFalse(withdrawn)
        _, wd = RI.extract_guidance("Outlook\nGiven uncertainty, the company is withdrawing its guidance for fiscal 2026.\n", "2026-05-01", "u")
        self.assertTrue(wd)

    def test_classification(self):
        base = {"metric": "Revenue", "period": "FY2026", "unit": "$", "text": "", "url": ""}
        items = [dict(base, date="2026-02-01", low=100e9, high=110e9, mid=105e9),
                 dict(base, date="2026-05-01", low=108e9, high=112e9, mid=110e9),
                 dict(base, date="2026-08-01", low=108e9, high=112e9, mid=110e9),
                 dict(base, date="2026-11-01", low=100e9, high=104e9, mid=102e9),
                 {"metric": "Gross margin", "period": "FY2026", "unit": "%", "text": "", "url": "", "date": "2026-11-01", "low": 46, "high": 48, "mid": 47},
                 {"metric": "Gross margin", "period": "FY2026", "unit": "%", "text": "", "url": "", "date": "2026-08-01", "low": 44, "high": 46, "mid": 45}]
        RI.classify_guidance(items)
        rev = [i["change"] for i in sorted(items[:4], key=lambda x: x["date"])]
        self.assertEqual(rev, ["INITIATED", "RAISED", "REITERATED", "LOWERED"])
        nov = [i for i in items if i["date"] == "2026-11-01"]
        self.assertTrue(all(i["release_change"] == "MIXED" for i in nov))     # revenue lowered + margin raised


class MissingDataTests(unittest.TestCase):
    def test_states_distinguish_not_run_from_no_evidence(self):
        with mock.patch.object(RI.sec, "is_configured", return_value=False), \
             mock.patch.object(RI.yahoo, "get_info", return_value={"longName": "Example", "sector": "Tech", "industry": "Chips"}), \
             mock.patch.dict(sys.modules, {"tab_news": mock.Mock(build_news=mock.Mock(side_effect=RuntimeError("offline")))}):
            out = RI._build("EXMP")
        self.assertEqual(out["dependencies"]["state"], "error")
        self.assertIn("SEC_USER_AGENT", out["dependencies"]["message"])
        self.assertEqual(out["guidance"]["state"], "error")
        self.assertEqual(out["controversies"]["state"], "error")

    def test_no_evidence_when_sources_ran(self):
        filings = [{"form": "10-K", "filed": "2026-01-01", "items": [], "url": "u", "accession": "a", "cik": 1, "company": "Example"}]
        with mock.patch.object(RI.sec, "is_configured", return_value=True), \
             mock.patch.object(RI.sec, "filings_with_items", return_value=filings), \
             mock.patch.object(RI.sec, "document_text", return_value="Item 1A. Risk Factors\n" + "Nothing specific here. " * 300 + "\nItem 1B."), \
             mock.patch.object(RI.yahoo, "get_info", return_value={"longName": "Example"}), \
             mock.patch.dict(sys.modules, {"tab_news": mock.Mock(build_news=mock.Mock(return_value={"events": [], "providers": []}))}):
            out = RI._build("EXMP")
        self.assertEqual(out["dependencies"]["state"], "no_evidence")
        self.assertEqual(out["controversies"]["state"], "no_evidence")
        self.assertEqual(out["controversies"]["message"], "No major controversies identified from connected sources.")
        self.assertEqual(out["guidance"]["state"], "no_evidence")


if __name__ == "__main__":
    unittest.main()
