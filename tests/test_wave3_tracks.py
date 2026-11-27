"""Wave 3 tests: pass-1 guard acceptance & hopeless-retry skip, Track 2 offline signal screen,
Track 3 structure-aware PDF retrieval + atomic claim verification, and Track 4 zero-ticker capability routing.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import documents as docs
from core.chat_service import ChatService
from core.gliner_decide import (
    ZERO_TICKER_CAPABILITY_SCHEMA,
    GlinerQueryRouter,
    RoutedQuery,
)
from core.market import MARKET_OVERVIEW_TICKERS, MarketTools
from core.orchestrator import (
    DataAnalystAgent,
    DocumentReaderAgent,
    EventBus,
    NewsResearcherAgent,
    TeamLeadOrchestrator,
)
from qualify import signal_screen as ss
from tests.test_chat import REPO, wait
from tests.test_orchestrator import StubNewsStore, StubTools


class _SeqLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, prompt, *, max_tokens=256, **options):
        self.calls.append((system, prompt, max_tokens, options))
        return self.replies.pop(0) if self.replies else ""


class _FixedRouter:
    def __init__(self, routed):
        self.routed = routed

    def route(self, text):
        return self.routed


class TestWave3APass1AndRetry(unittest.TestCase):
    def test_system_prompt_discourages_copying_user_nicknames_and_country_labels(self):
        self.assertIn("do not copy nicknames or country labels", TeamLeadOrchestrator.SYSTEM)

    def test_hopeless_numeric_hallucination_skips_wasted_retry_and_uses_fallback(self):
        data_llm = _SeqLLM(["NVDA trades at 138.5 USD."])
        lead_llm = _SeqLLM([
            "NVDA jumped to 999 USD with 888 billion revenue and 777 million volume.",
            "This retry should never run.",
        ])
        tools = StubTools()
        orch = TeamLeadOrchestrator(
            _FixedRouter(RoutedQuery("thesis_review", 0.9, "swing_week", ["NVDA"])),
            DataAnalystAgent(data_llm, tools),
            NewsResearcherAgent(None, StubNewsStore()),
            lead_llm,
            tools,
        )
        res = orch.run("Full thesis on NVDA with risk check", context="Earlier turn.")
        self.assertFalse(res.faithful_synthesis)
        self.assertEqual(len(lead_llm.calls), 1)
        gate_events = [e.payload for e in res.events if e.event_type == "GATE_CHECK" and e.agent == orch.NAME]
        self.assertEqual(gate_events[-1]["guard_retry_skipped"]["reason"], "unsupported_numeric_tokens")
        self.assertGreaterEqual(gate_events[-1]["guard_retry_skipped"]["rejected_count"], 3)
        self.assertNotIn("guard_retry", gate_events[-1])


class TestTrack2SignalScreen(unittest.TestCase):
    def test_dedup_story_clusters_near_duplicate_headlines_and_keeps_strongest_impact(self):
        items = [
            {"title": "Tesla Ends Cybertruck Production", "impact": -0.82, "material_p": 0.9, "source": "hackernews"},
            {"title": "Tesla to wind down Cybertruck production", "impact": -0.95, "material_p": 0.88, "source": "hackernews"},
            {"title": "Tesla reports Q3 deliveries", "impact": 0.60, "material_p": 0.92, "source": "company_release"},
        ]
        deduped = ss.dedup_story_items(items)
        self.assertEqual(len(deduped), 2)
        impacts = sorted(x["impact"] for x in deduped)
        self.assertEqual(impacts, [-0.95, 0.60])

    def test_source_tier_and_materiality_weighted_mean_rank_correctly(self):
        cases = [
            {
                "id": "2026-11-01:AAA", "symbol": "AAA", "origin": "2026-11-01", "return": 0.04,
                "items": [
                    {"title": "AAA raises guidance", "impact": 0.90, "material_p": 0.95, "source": "reuters"},
                    {"title": "AAA sponsors marathon", "impact": -0.40, "material_p": 0.05, "source": "company_release"},
                ],
            },
            {
                "id": "2026-11-01:BBB", "symbol": "BBB", "origin": "2026-11-01", "return": -0.03,
                "items": [
                    {"title": "BBB CFO resigns amid probe", "impact": -0.90, "material_p": 0.95, "source": "reuters"},
                    {"title": "BBB launches blog post", "impact": 0.60, "material_p": 0.05, "source": "company_release"},
                ],
            },
        ]
        res = ss.evaluate_arm(cases, "materiality_dedup_source_mean")
        self.assertEqual(res["auc"], 1.0)
        self.assertEqual(res["balanced_accuracy_covered"], 1.0)


class TestTrack3StructureAwarePDF(unittest.TestCase):
    def test_extracts_section_header_unit_banner_and_scales_allowlist(self):
        raw = (
            "Consolidated Statements of Operations\n"
            "($ in millions)\n"
            + ("Routine corporate footnote line.\n" * 40)
            + "Cloud segment revenue reached 412 in the third quarter.\n"
        )
        doc = {"id": "d1", "name": "10q.pdf", "pages": [docs.normalize(raw)], "source_pages": [raw]}
        ex = docs.select_excerpts([doc], "What was cloud segment revenue?")[0]
        self.assertEqual(ex["section_header"], "Consolidated Statements of Operations")
        self.assertIn("millions", ex["unit_banner"].lower())
        self.assertEqual(raw[ex["source_span"]["start"]:ex["source_span"]["end"]], ex["text"])

        rep = DocumentReaderAgent().run("What was cloud segment revenue?", [doc], EventBus(), "s")
        self.assertIn("Consolidated Statements of Operations", rep.summary)
        self.assertIn("($ in millions)", rep.summary)
        self.assertIn(412e6, rep.allowed_numbers)

    def test_banner_scaling_excludes_years_percents_and_notes_and_passes_team_lead(self):
        raw = (
            "Consolidated Statements of Operations\n"
            "($ in millions)\n"
            "In 2026, Note 1 shows cloud revenue reached 412 with 18% operating margin.\n"
        )
        doc = {"id": "d1", "name": "10q.pdf", "pages": [docs.normalize(raw)], "source_pages": [raw]}
        rep = DocumentReaderAgent().run("cloud revenue margin", [doc], EventBus(), "s")
        self.assertIn(412e6, rep.allowed_numbers)
        self.assertNotIn(2026e6, rep.allowed_numbers)
        self.assertNotIn(18e6, rep.allowed_numbers)
        self.assertNotIn(1e6, rep.allowed_numbers)

        lead_ok = _SeqLLM(["Cloud revenue reached 412 million in 2026, page 1."])
        orch = TeamLeadOrchestrator(
            _FixedRouter(RoutedQuery("open_conversation", 0.9, "swing_week", [])),
            DataAnalystAgent(_SeqLLM([]), StubTools()),
            NewsResearcherAgent(None, StubNewsStore()),
            lead_ok,
            StubTools(),
        )
        res = orch.run("What was cloud revenue?", documents=[doc])
        self.assertTrue(res.faithful_synthesis)
        self.assertIn("412 million", res.final_synthesis)

        shorthand_bad = docs.check_document_claims("10q.pdf reports 412B on page 1.", rep.raw_facts)
        self.assertEqual(shorthand_bad[0]["kind"], "document_unit_mismatch")

    def test_adjacent_page_expansion_only_fires_on_continuation_cue(self):
        doc_cont = {
            "id": "d2",
            "name": "filing.pdf",
            "pages": [
                docs.normalize("Table 2: Segment Breakdown (continued on next page)\nCloud quarterly bookings 412."),
                docs.normalize("Table 2: Segment Breakdown (continued)\nOperating income 95 and margin 23 percent."),
                docs.normalize("Unrelated governance policy about travel reimbursement."),
            ],
            "source_pages": [
                "Table 2: Segment Breakdown (continued on next page)\nCloud quarterly bookings 412.",
                "Table 2: Segment Breakdown (continued)\nOperating income 95 and margin 23 percent.",
                "Unrelated governance policy about travel reimbursement.",
            ],
        }
        ex = docs.select_excerpts([doc_cont], "Cloud quarterly bookings")
        self.assertEqual([e["page"] for e in ex], [1, 2])
        self.assertTrue(ex[1].get("adjacent"))

    def test_atomic_document_claims_catch_unit_scale_and_cross_document_mismatch(self):
        doc_a = {
            "id": "da",
            "name": "Alpha_10Q.pdf",
            "pages": [docs.normalize("($ in millions)\nAlpha revenue was 412 in Q3.")],
            "source_pages": ["($ in millions)\nAlpha revenue was 412 in Q3."],
        }
        doc_b = {
            "id": "db",
            "name": "Beta_10Q.pdf",
            "pages": [docs.normalize("($ in millions)\nBeta revenue was 890 in Q3.")],
            "source_pages": ["($ in millions)\nBeta revenue was 890 in Q3."],
        }
        rep = DocumentReaderAgent().run("Alpha Beta revenue Q3", [doc_a, doc_b], EventBus(), "s")
        self.assertEqual(docs.check_document_claims("Alpha_10Q.pdf reports 412 million on page 1.", rep.raw_facts), [])
        unit_bad = docs.check_document_claims("Alpha_10Q.pdf reports 412 billion on page 1.", rep.raw_facts)
        self.assertEqual(unit_bad[0]["kind"], "document_unit_mismatch")
        cross_bad = docs.check_document_claims("Alpha_10Q.pdf reports 890 million on page 1.", rep.raw_facts)
        self.assertEqual(cross_bad[0]["kind"], "cross_document_mismatch")


class TestTrack4CapabilityRouting(unittest.TestCase):
    def test_router_routes_zero_ticker_capabilities_without_slowing_ticker_queries(self):
        seen_schemas = []

        class CapBackend:
            def classify_text(self, text, schema, *, include_confidence=False):
                seen_schemas.append((text, set(schema.keys())))
                if "capability" in schema:
                    if "fear" in text.lower():
                        return {"capability": {"label": "crypto_sentiment", "confidence": 0.82}}
                    if "markets" in text.lower():
                        return {"capability": {"label": "market_overview", "confidence": 0.85}}
                    return {"capability": {"label": "general_chat", "confidence": 0.88}}
                return {
                    "intent": {"label": "open_conversation", "confidence": 0.72},
                    "horizon": {"label": "intraday", "confidence": 0.70},
                }

            def extract_entities(self, text, entity_types):
                return {"entities": {}}

        router = GlinerQueryRouter(CapBackend(), universe={"NVDA", "AAPL"})
        r_nvda = router.route("What is NVDA trading at?")
        self.assertEqual(r_nvda.capability, "none")
        self.assertFalse(any("capability" in s for q, s in seen_schemas if "NVDA" in q))

        r_hi = router.route("hi")
        self.assertEqual(r_hi.capability, "none")
        self.assertFalse(any("capability" in s for q, s in seen_schemas if q == "hi"))

        r_sent = router.route("What is the crypto fear and greed index?")
        self.assertEqual(r_sent.capability, "crypto_sentiment")

        r_mkt = router.route("How are US markets doing today?")
        self.assertEqual(r_mkt.capability, "market_overview")

    def test_chat_service_executes_crypto_sentiment_and_market_overview_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_fetch(url):
                if "alternative.me" in url:
                    return {"data": [{"value": "64", "value_classification": "Greed", "timestamp": "1795800000"}]}
                return {
                    "chart": {
                        "result": [{
                            "meta": {
                                "currency": "USD",
                                "shortName": "Index",
                                "exchangeName": "SNP",
                                "instrumentType": "INDEX",
                                "regularMarketPrice": 5850.25,
                                "regularMarketTime": 1795800000,
                                "chartPreviousClose": 5800.0,
                                "exchangeTimezoneName": "America/New_York",
                            },
                            "timestamp": [1795700000, 1795800000],
                            "indicators": {"quote": [{"close": [5800.0, 5850.25], "volume": [1000, 1200]}]},
                        }]
                    }
                }

            class StubRouter:
                def route(self, query):
                    if "sentiment" in query.lower():
                        return RoutedQuery("open_conversation", 0.7, "intraday", [], capability="crypto_sentiment")
                    return RoutedQuery("open_conversation", 0.7, "intraday", [], capability="market_overview")

            svc = ChatService(
                REPO,
                Path(tmp),
                router_factory=lambda: StubRouter(),
                market_factory=lambda cancel=None: MarketTools(fetch=fake_fetch, cancel=cancel),
            )
            try:
                sess = svc.store.create_session("c" * 32)
                job1 = svc.submit(sess["id"], "Check crypto market sentiment", "a" * 32, "facts")
                done1 = wait(svc, job1)
                self.assertEqual(done1["status"], "complete")
                msg1 = svc.store.session(sess["id"])["messages"][-1]
                self.assertIn("64 (Greed)", msg1["content"])
                self.assertTrue(msg1["payload"]["faithful_synthesis"])

                job2 = svc.submit(sess["id"], "How are US markets doing today?", "b" * 32, "facts")
                done2 = wait(svc, job2)
                self.assertEqual(done2["status"], "complete")
                msg2 = svc.store.session(sess["id"])["messages"][-1]
                self.assertEqual(msg2["payload"]["tickers"], list(MARKET_OVERVIEW_TICKERS))
                self.assertEqual(len(msg2["payload"]["snapshots"]), 3)

                sess2 = svc.store.create_session("d" * 32)
                svc.router_factory = lambda: _FixedRouter(RoutedQuery("price_snapshot", 0.9, "intraday", ["NVDA"]))
                done_nvda = wait(svc, svc.submit(sess2["id"], "Quote NVDA", "1" * 32, "facts"))
                self.assertIn(done_nvda["status"], {"complete", "degraded"})
                svc.router_factory = lambda: _FixedRouter(RoutedQuery("price_snapshot", 0.8, "intraday", [], capability="market_overview"))
                job3 = svc.submit(sess2["id"], "How is it doing today?", "e" * 32, "facts")
                done3 = wait(svc, job3)
                self.assertIn(done3["status"], {"complete", "degraded"})
                msg3 = svc.store.session(sess2["id"])["messages"][-1]
                self.assertEqual(msg3["payload"]["tickers"], ["NVDA"])
            finally:
                svc.close()


if __name__ == "__main__":
    unittest.main()
