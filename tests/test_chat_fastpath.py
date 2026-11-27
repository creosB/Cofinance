import os
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from core.chat_fastpath import conversational_reply, fast_route
from core.chat_service import ChatService, UNIVERSE
from core.gliner_decide import DEFAULT_ALIASES, GlinerQueryRouter, RoutedQuery
from tests.test_chat import FakeMarket, wait

REPO = Path(__file__).resolve().parents[1]


class CountingRouter:
    def __init__(self, intent="price_snapshot", tickers=None):
        self.intent = intent
        self.tickers = list(tickers or ["NVDA"])
        self.calls = 0

    def route(self, query):
        self.calls += 1
        return RoutedQuery(self.intent, 0.9, "swing_week", list(self.tickers))


class CountingNarratorModels:
    def __init__(self, *, selected="lfm3-1.7b-atomic-ad-q4km", qualified=True, think_qualified=True, fail_load=False):
        self.lock = threading.RLock()
        self.state = {"selected": selected, "router": "cached"}
        self.busy = False
        self.thread = None
        self.model_id = selected
        self.narrator_id = selected
        self.disabled = False
        self._qualified = qualified
        self._think_qualified = think_qualified
        self._loaded = False
        self._thinker = False
        self.fail_load = fail_load
        self.load_calls = 0
        self.think_load_calls = 0

    def qualified(self, backend=None, model=None):
        return self._qualified

    def think_qualified(self, backend=None, model=None):
        return self._think_qualified

    def is_loaded(self, model=None):
        return self._loaded

    def thinker_loaded(self, model=None):
        return self._thinker

    def load(self):
        time.sleep(0.05)
        if self.fail_load:
            raise RuntimeError("narrator load failed")
        self.load_calls += 1
        self._loaded = True

    def load_thinker(self):
        self.think_load_calls += 1
        self._thinker = True

    def stop_thinker(self):
        self._thinker = False

    def complete(self, *args, **kwargs):
        return "NVIDIA (NVDA) last traded at 138.5 USD."

    def update(self, **kwargs):
        self.state.update(kwargs)

    def close(self):
        self._loaded = False
        self._thinker = False


class TestChatFastpath(unittest.TestCase):
    def test_conversational_reply_greetings_thanks_help_and_blockers(self):
        for msg, kind in (("hi", "greeting"), ("Hello!", "greeting"), ("selam", "greeting"),
                          ("thanks", "acknowledgement"), ("What can you do?", "help")):
            got = conversational_reply(msg, universe=UNIVERSE, aliases=DEFAULT_ALIASES)
            self.assertIsNotNone(got, msg)
            self.assertEqual(got[0], kind, msg)

        for research in (
            "hi, what is NVDA trading at?",
            "thanks, what about risks?",
            "NVDA",
            "hello apple news",
            "help me compare AMD and NVDA",
            "hi $btc",
        ):
            self.assertIsNone(conversational_reply(research, universe=UNIVERSE, aliases=DEFAULT_ALIASES), research)
        self.assertIsNone(conversational_reply("HI", universe={"HI", "NVDA"}, aliases=DEFAULT_ALIASES))

    def test_fast_route_clear_cases_and_ambiguous_fallthrough(self):
        cases = [
            ("What is NVDA trading at right now?", "price_snapshot", "intraday", ["NVDA"]),
            ("Any news on Tesla this week?", "news_catalysts", "swing_week", ["TSLA"]),
            ("Compare AMD and NVDA for the next month", "compare_assets", "swing_week", ["AMD", "NVDA"]),
            ("Add $SOL to my watchlist", "watchlist_add", "swing_week", ["SOL"]),
            ("How is Bitcoin priced today?", "price_snapshot", "intraday", ["BTC"]),
            ("What are the latest catalysts for Microsoft stock?", "news_catalysts", "swing_week", ["MSFT"]),
            ("Ethereum vs Solana: which one looks stronger?", "compare_assets", "swing_week", ["ETH", "SOL"]),
            ("Keep an eye on GOOGL for me", "watchlist_add", "swing_week", ["GOOGL"]),
            ("btc price", "price_snapshot", "swing_week", ["BTC"]),
            ("WHAT IS NVDA PRICE TODAY?", "price_snapshot", "intraday", ["NVDA"]),
        ]
        for q, intent, horizon, tickers in cases:
            got = fast_route(q, UNIVERSE, DEFAULT_ALIASES)
            self.assertIsNotNone(got, q)
            self.assertEqual((got.intent, got.horizon, got.tickers), (intent, horizon, tickers), q)

        for fallthrough in (
            "Give me the bull and bear case for Apple",
            "Is Amazon a good long-term hold? Full thesis please",
            "Explain why NVDA price is up today",
            "Full bull and bear thesis and news outlook for Apple",
            "What about risks?",
            "What is NVDA price and latest news?",
            "Compare NVDA",
            "What is the Fed dot plot price reaction?",
            "Will they spy on competitors news?",
            "Check snap and arm price",
        ):
            self.assertIsNone(fast_route(fallthrough, UNIVERSE | {"SNAP", "ARM"}, DEFAULT_ALIASES), fallthrough)
        self.assertIsNone(fast_route("What is NVDA trading at?", UNIVERSE, DEFAULT_ALIASES, has_documents=True))

    def test_gliner_router_skips_redundant_ner_when_deterministic_resolved(self):
        class SpyBackend:
            def __init__(self):
                self.ner_calls = 0

            def classify_text(self, text, schema, include_confidence=False):
                return {"intent": {"label": "thesis_review", "confidence": 0.9}, "horizon": {"label": "swing_week", "confidence": 0.8}}

            def extract_entities(self, text, entity_types):
                self.ner_calls += 1
                return {"entities": {}}

        spy = SpyBackend()
        router = GlinerQueryRouter(spy, {"NVDA", "TSLA", "BTC"})
        r1 = router.route("Give me the bull and bear case for NVDA")
        self.assertEqual(r1.tickers, ["NVDA"])
        self.assertEqual(spy.ner_calls, 0)
        router.route("Give me the bull and bear case for that chip maker")
        self.assertEqual(spy.ner_calls, 1)

    def test_greeting_skips_narrator_and_router_and_preserves_session_tickers(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = CountingNarratorModels()
            router = CountingRouter("price_snapshot", ["NVDA"])
            svc = ChatService(REPO, tmp, models=models, market_factory=FakeMarket, router_factory=lambda: router)
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                job1 = svc.submit(sess["id"], "What is NVDA trading at?", uuid.uuid4().hex, models.model_id)
                wait(svc, job1)
                self.assertEqual(models.load_calls, 1)
                self.assertEqual(router.calls, 1)
                self.assertEqual(svc.store.session(sess["id"])["tickers"], ["NVDA"])

                job2 = svc.submit(sess["id"], "hi", uuid.uuid4().hex, models.model_id, think=True)
                done2 = wait(svc, job2)
                self.assertEqual(done2["status"], "complete")
                self.assertEqual(models.load_calls, 1)
                self.assertEqual(models.think_load_calls, 0)
                self.assertEqual(router.calls, 1)
                updated = svc.store.session(sess["id"])
                self.assertEqual(updated["tickers"], ["NVDA"])
                last = updated["messages"][-1]
                self.assertEqual(last["payload"]["kind"], "conversation")
                self.assertIn("NVDA", last["content"])
                self.assertFalse(last["payload"]["deep_think"]["used"])
            finally:
                svc.close()

    def test_news_only_skips_narrator_but_news_with_pdf_loads_narrator(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = CountingNarratorModels()
            router = CountingRouter("news_catalysts", ["NVDA"])
            svc = ChatService(REPO, tmp, models=models, market_factory=FakeMarket, router_factory=lambda: router)
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                job1 = svc.submit(sess["id"], "Any news on NVDA?", uuid.uuid4().hex, models.model_id, think=True)
                done1 = wait(svc, job1)
                self.assertIn(done1["status"], {"complete", "degraded"})
                self.assertEqual(models.load_calls, 0)
                self.assertEqual(models.think_load_calls, 0)

                svc.store.add_document(sess["id"], "brief.pdf", "a" * 64, 120, [{"page": 1, "text": "NVDA revenue rose 20%."}])
                job2 = svc.submit(sess["id"], "Any news on NVDA in the PDF?", uuid.uuid4().hex, models.model_id)
                wait(svc, job2)
                self.assertEqual(models.load_calls, 1)
            finally:
                svc.close()

    def test_live_fast_route_skips_gliner_when_router_factory_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = CountingNarratorModels()
            svc = ChatService(REPO, tmp, models=models, market_factory=FakeMarket, router_factory=None)
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                with mock.patch.object(svc, "local_extractor", side_effect=AssertionError("GLiNER should not start on fast route")):
                    job = svc.submit(sess["id"], "What is NVDA trading at right now?", uuid.uuid4().hex, models.model_id)
                    done = wait(svc, job)
                    self.assertIn(done["status"], {"complete", "degraded"})
                self.assertEqual(svc.store.session(sess["id"])["tickers"], ["NVDA"])
                trace_path = Path(tmp) / "traces" / f"{job['id']}.json"
                import json
                trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
                route_span = next(s for s in trace_data["spans"] if s["name"] == "Route conversational request")
                self.assertEqual(route_span["actor"], "Fast router (Python)")
                self.assertTrue(route_span["inputs"].get("fast_path"))

                with mock.patch.dict(os.environ, {"COFINANCE_FAST_ROUTE": "off"}), \
                     mock.patch.object(svc, "local_extractor", side_effect=RuntimeError("router_called")) as called:
                    job2 = svc.submit(sess["id"], "What is NVDA trading at right now?", uuid.uuid4().hex, models.model_id)
                    done2 = wait(svc, job2)
                    self.assertEqual(done2["status"], "error")
                    called.assert_called_once()
            finally:
                svc.close()

    def test_greeting_with_ticker_and_risk_followup_use_research_not_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = CountingNarratorModels()
            router = CountingRouter("price_snapshot", ["NVDA"])
            svc = ChatService(REPO, tmp, models=models, market_factory=FakeMarket, router_factory=lambda: router)
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                job1 = svc.submit(sess["id"], "hi, what is NVDA trading at?", uuid.uuid4().hex, models.model_id)
                wait(svc, job1)
                self.assertEqual(router.calls, 1)
                self.assertEqual(svc.store.session(sess["id"])["messages"][-1]["payload"]["kind"], "research")

                job2 = svc.submit(sess["id"], "thanks, what about risks?", uuid.uuid4().hex, models.model_id)
                wait(svc, job2)
                self.assertEqual(router.calls, 2)
                self.assertEqual(svc.store.session(sess["id"])["messages"][-1]["payload"]["kind"], "research")
            finally:
                svc.close()

    def test_injected_router_factory_is_never_bypassed_by_fast_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = CountingNarratorModels()
            router = CountingRouter("price_snapshot", ["NVDA"])
            svc = ChatService(REPO, tmp, models=models, market_factory=FakeMarket, router_factory=lambda: router)
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                job = svc.submit(sess["id"], "What is NVDA trading at right now?", uuid.uuid4().hex, models.model_id)
                wait(svc, job)
                self.assertEqual(router.calls, 1)
            finally:
                svc.close()

    def test_warm_narrator_loads_once_respects_flags_and_handles_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = CountingNarratorModels()
            svc = ChatService(REPO, tmp, models=models, market_factory=FakeMarket, router_factory=lambda: CountingRouter())
            try:
                with mock.patch.dict(os.environ, {"COFINANCE_NARRATOR_WARMUP": "off"}):
                    self.assertIsNone(svc.warm_narrator())
                    self.assertEqual(svc.narrator_warm["state"], "skipped")
                    self.assertEqual(models.load_calls, 0)

                t1 = svc.warm_narrator()
                self.assertIsNotNone(t1)
                t1.join(5)
                self.assertEqual(models.load_calls, 1)
                self.assertEqual(svc.narrator_warm["state"], "loaded")
            finally:
                svc.close()

        with tempfile.TemporaryDirectory() as tmp:
            models_unq = CountingNarratorModels(qualified=False)
            svc_unq = ChatService(REPO, tmp, models=models_unq, market_factory=FakeMarket, router_factory=lambda: CountingRouter())
            try:
                self.assertIsNone(svc_unq.warm_narrator())
                self.assertEqual(models_unq.load_calls, 0)
            finally:
                svc_unq.close()

        with tempfile.TemporaryDirectory() as tmp:
            models = CountingNarratorModels(fail_load=True)
            svc = ChatService(REPO, tmp, models=models, market_factory=FakeMarket, router_factory=lambda: CountingRouter())
            try:
                t = svc.warm_narrator()
                t.join(5)
                self.assertEqual(svc.narrator_warm["state"], "failed")
            finally:
                svc.close()


if __name__ == "__main__":
    unittest.main()
