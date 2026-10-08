"""
Ask MarketLab web search: when it triggers, how results are ranked/labelled/cited, graceful failure, no
fabricated conclusions, and hand-off into the News system. Fully offline (stub search tool, stubbed market data).

Run:  python3 -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import assistant                                    # noqa: E402
import freshness                                    # noqa: E402
from sources import news as news_sources           # noqa: E402
from sources import websearch                       # noqa: E402

NOW = datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)


def ctx():
    return {"ticker": "RGTI", "name": "Rigetti Computing, Inc.", "currency": "USD", "built_at": "2026-10-07T17:59:00+00:00",
            "facts": [], "documents": [], "filings": [], "available": {}, "not_available": [],
            "sources": [{"id": "S1", "label": "Yahoo Finance", "kind": "Market data", "url": "https://finance.yahoo.com/quote/RGTI", "retrieved_at": None}],
            "overview": {"change_pct": -5.2, "industry": "Computer Hardware", "website": "https://www.rigetti.com"},
            "profile": {"filings": []}}


def result(title, url, published, snippet=None, query="q"):
    return {"title": title, "url": url, "domain": websearch.domain_of(url), "source": websearch.domain_of(url),
            "published": published, "precision": "time" if published and "T" in published else None,
            "snippet": snippet, "provider": "stub", "query": query}


class StubTool(websearch.WebSearchTool):
    name, label = "stub", "Stub search"

    def __init__(self, results=None, error=None):
        self.results, self.error, self.queries = results or [], error, []

    def search(self, query, max_results=6):
        self.queries.append(query)
        if self.error:
            raise websearch.WebSearchError(self.error)
        return [dict(r, query=query) for r in self.results]


class Gate(unittest.TestCase):
    def test_current_questions_trigger_search(self):
        for q, kind in [("Why is RGTI down today?", "move"), ("What happened to NVDA this morning?", "move"),
                        ("Did anything happen with IONQ this week?", "recent"), ("Why are quantum stocks selling off?", "move"),
                        ("Was there a downgrade today?", "analyst"), ("What happened with NVDA this week?", "recent"),
                        ("Any tariff news affecting this?", "policy")]:
            d = freshness.classify(q)
            self.assertTrue(d["needs_web"], q)
            self.assertEqual(d["kind"], kind, q)
        self.assertEqual(freshness.classify("Why are quantum stocks selling off?")["scope"], "sector")
        self.assertEqual(freshness.classify("Why is RGTI down today?")["scope"], "company")

    def test_evergreen_questions_do_not(self):
        for q in ("What does RGTI do?", "What is EPS?", "Explain P/E to me", "How does RGTI make money?",
                  "What are the biggest risks I should research for RGTI?", "What was the latest quarter's revenue?",
                  "Is RGTI more of a short-term trade or a long-term research idea? Walk me through each time horizon."):
            self.assertFalse(freshness.classify(q)["needs_web"], q)

    def test_followup_inherits(self):
        hist = [{"role": "user", "content": "Why is RGTI down today?"}, {"role": "assistant", "content": "x"},
                {"role": "user", "content": "and this week?"}]
        self.assertTrue(freshness.classify("and this week?", hist)["needs_web"])


class Queries(unittest.TestCase):
    def test_multiple_distinct_queries(self):
        qs = freshness.build_queries("move", "company", "Why is RGTI down today?", "Rigetti Computing, Inc.", "RGTI",
                                     "Computer Hardware", NOW.date(), limit=5)
        self.assertGreaterEqual(len(qs), 4)
        self.assertEqual(len(set(q.lower() for q in qs)), len(qs))
        joined = " | ".join(qs)
        self.assertIn("Rigetti Computing today", joined)
        self.assertIn("RGTI stock today", joined)
        self.assertIn("October 7 2026", joined)

    def test_sector_topic(self):
        qs = freshness.build_queries("move", "sector", "Why are quantum stocks selling off?", "Rigetti", "RGTI", None, NOW.date())
        self.assertTrue(any("quantum stocks today" in q for q in qs))


class Quality(unittest.TestCase):
    def test_ranking_labels(self):
        self.assertEqual(websearch.source_quality("sec.gov")[0], 0)
        self.assertEqual(websearch.source_quality("ir.rigetti.com")[0], 0)
        self.assertEqual(websearch.source_quality("rigetti.com", "rigetti.com")[1], "Company source")
        self.assertEqual(websearch.source_quality("reuters.com")[0], 1)
        self.assertEqual(websearch.source_quality("thequantuminsider.com")[0], 2)
        self.assertEqual(websearch.source_quality("seekingalpha.com")[0], 3)
        self.assertEqual(websearch.source_quality("reddit.com")[0], 4)
        self.assertEqual(websearch.source_quality("someblog.xyz")[0], 3)

    def test_buckets_and_dates(self):
        iso, prec = websearch.parse_published("October 7, 2026", NOW)
        self.assertEqual((websearch.bucket(iso, NOW), prec), ("today", "day"))
        self.assertEqual(websearch.bucket(websearch.parse_published("3 days ago", NOW)[0], NOW), "week")
        self.assertEqual(websearch.bucket(websearch.parse_published("2026-08-01", NOW)[0], NOW), "older")
        self.assertEqual(websearch.parse_published("sometime soon", NOW), (None, None))
        self.assertEqual(websearch.bucket(None), "undated")
        self.assertEqual(websearch.bucket("2026-12-25T12:00:00+00:00", NOW), "undated")        # future date: not trusted

    def test_unsafe_urls_dropped(self):
        out = websearch.clean_results([{"title": "x", "url": "javascript:alert(1)"}, {"title": "ok", "url": "https://a.com/x"},
                                       {"title": "dup", "url": "https://www.a.com/x/"}], "q", "p")
        self.assertEqual([r["url"] for r in out], ["https://a.com/x"])


class Providers(unittest.TestCase):
    def test_anthropic_parse(self):
        blocks = [{"type": "server_tool_use", "name": "web_search"},
                  {"type": "web_search_tool_result", "content": [
                      {"type": "web_search_result", "title": "Rigetti slides", "url": "https://www.reuters.com/a", "page_age": "October 7, 2026"},
                      {"type": "web_search_result", "title": "Old", "url": "https://x.com/b", "page_age": None}]},
                  {"type": "text", "text": "done", "citations": [{"url": "https://www.reuters.com/a", "cited_text": "Shares fell."}]}]
        out = websearch.AnthropicWebSearch.parse(blocks, "q")
        self.assertEqual(out[0]["snippet"], "Shares fell.")
        self.assertEqual(out[0]["precision"], "day")
        self.assertIsNone(out[1]["published"])

    def test_anthropic_error_block(self):
        with self.assertRaises(websearch.WebSearchError):
            websearch.AnthropicWebSearch.parse([{"type": "web_search_tool_result", "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}}], "q")

    def test_claude_code_parse_trusts_only_tool_urls(self):
        events = [
            {"type": "user", "message": {"content": [{"type": "tool_result", "content":
                'Web search results for query: "q"\n\nLinks: [{"title":"A","url":"https://www.reuters.com/a"},{"title":"B","url":"https://c.com/b"}]\n'}]}},
            {"type": "result", "is_error": False, "result": json.dumps([
                {"url": "https://www.reuters.com/a", "title": "A", "published": "2026-10-07T14:05:00Z", "snippet": "Rigetti fell."},
                {"url": "https://invented.example/zzz", "title": "Invented", "published": "2026-10-07", "snippet": "n/a"}])}]
        out = websearch.ClaudeCodeWebSearch.parse(events, "q")
        urls = [r["url"] for r in out]
        self.assertNotIn("https://invented.example/zzz", urls)
        self.assertEqual(urls[0], "https://www.reuters.com/a")
        self.assertEqual(out[0]["precision"], "time")
        self.assertIsNone(next(r for r in out if "c.com" in r["url"])["published"])

    def test_registry_and_off(self):
        websearch.register("stub", lambda: StubTool())
        with mock.patch.object(websearch.config, "WEB_SEARCH_PROVIDER", "stub"):
            self.assertEqual(websearch.get_tool().name, "stub")
        with mock.patch.object(websearch.config, "WEB_SEARCH", False):
            self.assertIsNone(websearch.get_tool())


def patch_market():
    return [mock.patch.object(freshness, "_change", lambda s: {"IONQ": -4.8, "QBTS": -6.1, "SPY": 0.2}.get(s)),
            mock.patch.object(freshness.yahoo, "get_price_history", lambda t: [(f"2026-09-{i:02d}", 100 + i) for i in range(1, 29)]),
            mock.patch("tab_news.build_news", lambda t: {"events": []})]


PEERS = lambda t: [{"id": "similar", "symbols": ["IONQ", "QBTS"]}, {"id": "market", "symbols": ["SPY"]}]


class Workflow(unittest.TestCase):
    def setUp(self):
        self.patches = patch_market()
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        orig = websearch.bucket
        p = mock.patch.object(websearch, "bucket", lambda published, now=None: orig(published, NOW))
        p.start()
        self.addCleanup(p.stop)

    def run_stream(self, question, tool, system_capture):
        def fake_llm(system, messages, model, max_tokens=1200):
            system_capture.append(system)
            yield "Answer text."
        with mock.patch.object(websearch, "get_tool", lambda: tool), \
                mock.patch.object(freshness, "_peer_groups", PEERS), \
                mock.patch.object(assistant.llm, "assistant_stream", fake_llm), \
                mock.patch.object(freshness, "ingest", lambda *a: 0):
            return "".join(assistant.answer_stream(ctx(), [{"role": "user", "content": question}]))

    def test_move_question_searches_and_cites(self):
        tool = StubTool([result("Quantum stocks slide as sector sells off", "https://www.reuters.com/q", "2026-10-07T15:00:00+00:00", "Rigetti and IonQ fell."),
                         result("Rigetti background", "https://thequantuminsider.com/old", "2026-08-01T12:00:00+00:00"),
                         result("RGTI chatter", "https://reddit.com/r/x", None)])
        cap = []
        out = self.run_stream("Why is RGTI down today?", tool, cap)
        self.assertGreaterEqual(len(tool.queries), 4)
        self.assertIn("[[status]]Searching current sources", out)
        payload = json.loads(out.split("[[web]]")[1].split("\n")[0])
        self.assertEqual(payload["used"], 3)
        for s in payload["sources"]:
            self.assertTrue(s["url"].startswith("https://"))
            self.assertTrue(s["headline"] and s["publisher"] and "bucket" in s)
        self.assertEqual(payload["sources"][0]["bucket"], "today")
        system = cap[0]
        self.assertIn("<web_results>", system)
        self.assertIn("TODAY", system)
        self.assertIn("OLDER BACKGROUND", system)
        self.assertIn("UNDATED", system)
        self.assertIn("CATALYST CHECK", system)
        self.assertIn("2 of 2 peers moved", system)             # IONQ, QBTS both fell with RGTI
        self.assertIn("none found", system)                     # no company press release / filing
        self.assertIn("Social post / unverified", system)
        self.assertIn("[S2]", system)                           # web tags continue after MarketLab's own S1

    def test_evergreen_never_searches(self):
        for q in ("What does RGTI do?", "What is EPS?"):
            tool, cap = StubTool([result("x", "https://a.com/x", None)]), []
            out = self.run_stream(q, tool, cap)
            self.assertEqual(tool.queries, [], q)
            self.assertNotIn("[[web]]", out)
            self.assertNotIn("<web_results>", cap[0])

    def test_failed_search_does_not_crash_or_invent(self):
        tool, cap = StubTool(error="rate limited"), []
        out = self.run_stream("Why is RGTI down today?", tool, cap)
        self.assertTrue(out.endswith("Answer text."))
        self.assertIn("A search failed: rate limited", cap[0])
        self.assertIn("The searches returned no usable results.", cap[0])
        self.assertIn("do not guess", cap[0].lower().replace("don't", "do not"))

    def test_no_provider_available(self):
        cap = []
        out = self.run_stream("Why is RGTI down today?", None, cap)
        self.assertTrue(out.endswith("Answer text."))
        self.assertIn("Web search is not available", cap[0])

    def test_search_layer_crash_is_contained(self):
        cap = []
        with mock.patch.object(freshness, "gather", side_effect=RuntimeError("boom")):
            out = self.run_stream("Why is RGTI down today?", StubTool(), cap)
        self.assertTrue(out.endswith("Answer text."))
        self.assertIn("could not be checked", cap[0])

    def test_rules_forbid_unsupported_causes(self):
        self.assertIn("Do NOT assume every move has a company-specific catalyst", freshness.WEB_RULES)
        self.assertIn("no clear catalyst", freshness.WEB_RULES)
        self.assertIn("correlation, not proof", freshness.WEB_RULES)


class NewsHandoff(unittest.TestCase):
    def test_dated_relevant_results_become_normal_articles(self):
        ev = {"results": [
            dict(result("Rigetti Computing slides", "https://www.reuters.com/a", "2026-10-07T15:00:00+00:00"), quality_rank=1, relevant=True),
            dict(result("Rigetti rumor", "https://reddit.com/r/x", "2026-10-07T15:00:00+00:00"), quality_rank=4, relevant=True),
            dict(result("Undated", "https://b.com/x", None), quality_rank=3, relevant=True),
            dict(result("Off topic", "https://c.com/x", "2026-10-07T15:00:00+00:00"), quality_rank=1, relevant=False)]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(news_sources, "WEB_STORE", Path(tmp)):
            self.assertEqual(freshness.ingest("RGTI", ev), 1)
            arts = news_sources.p_web_search({"ticker": "RGTI"})
        self.assertEqual([a["url"] for a in arts], ["https://www.reuters.com/a"])
        self.assertEqual(arts[0]["provider"], "web_search")
        self.assertEqual(arts[0]["source_type"], "reporting")
        self.assertIn("web_search", [p["id"] for p in news_sources.PROVIDERS])


if __name__ == "__main__":
    unittest.main()
