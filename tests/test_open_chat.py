import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from core.chat_service import ChatService, UNIVERSE
from core.gliner_decide import DEFAULT_ALIASES, GlinerQueryRouter, RoutedQuery
from core.market import MarketTools, resolve_yahoo_symbol, valid_symbol
from tests.test_chat import FakeMarket, wait

REPO = Path(__file__).resolve().parents[1]


class RecordingNarratorModels:
    def __init__(self, model_id="lfm3-1.7b-atomic-ad-q4km", *, qualified=True, think_qualified=True):
        self.lock = threading.Lock()
        self.thread = None
        self.busy = False
        self.narrator_id = model_id
        self.loaded_model = None
        self.ready = False
        self._qualified = qualified
        self._think_qualified = think_qualified
        self._thinker = False
        self.load_calls = 0
        self.think_load_calls = 0
        self.complete_calls = []
        self.state = {"selected": model_id, "phase": "ready" if qualified else "downloaded", "loaded": False}

    @property
    def model_id(self):
        return f"stub:{self.narrator_id}"

    def qualified(self, backend=None, model=None):
        return self._qualified

    def think_qualified(self, backend=None, model=None):
        return self._think_qualified

    def is_loaded(self, model=None):
        return self.ready and (model is None or self.loaded_model == model)

    def load(self, for_qualification=False, backend=None, model=None):
        self.load_calls += 1
        self.ready = True
        self.loaded_model = model or self.narrator_id
        self.state["loaded"] = True

    def load_thinker(self, for_qualification=False, backend=None, model=None):
        self.think_load_calls += 1
        self._thinker = True

    def thinker_loaded(self, model=None):
        return self._thinker

    def stop_thinker(self):
        self._thinker = False

    def close(self):
        self.ready = False
        self._thinker = False

    def status(self):
        return dict(self.state)

    def complete(self, system, prompt, *, max_tokens=256, stop=None, think=False, messages=None, temperature=0, allow_truncated=False):
        self.complete_calls.append({
            "system": system,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "think": think,
            "messages": messages,
            "temperature": temperature,
            "allow_truncated": allow_truncated,
        })
        if messages is not None:
            return "<think>hidden</think>Paris is the capital of France — happy to help."
        return "NVIDIA Corporation (NVDA) last traded at 195.0 USD, up 2.6316 percent from the previous session close of 190.0 USD."


class ResolvingFakeMarket(FakeMarket):
    def __init__(self, cancel=None, mapping=None):
        super().__init__(cancel=cancel)
        self.mapping = dict(mapping or {
            "palantir": {"symbol": "PLTR", "name": "Palantir Technologies Inc.", "quote_type": "EQUITY", "exchange": "NMS", "source": "Yahoo Finance search"},
            "gold": {"symbol": "GC=F", "name": "Gold Futures", "quote_type": "FUTURE", "exchange": "CMX", "source": "Yahoo Finance search"},
        })
        self.resolve_calls = []

    def resolve_symbol(self, term):
        self.resolve_calls.append(term)
        return self.mapping.get((term or "").strip().lower())


class ScriptedRouter:
    def __init__(self, route_fn):
        self.route_fn = route_fn
        self.calls = []

    def route(self, query):
        self.calls.append(query)
        return self.route_fn(query)


class TestOpenChatAndDynamicResolution(unittest.TestCase):
    def test_valid_symbol_supports_indices_futures_fx_and_blocks_traversal(self):
        for good in ("NVDA", "PLTR", "BRK-B", "^GSPC", "GC=F", "EURUSD=X", "XU100.IS"):
            self.assertEqual(valid_symbol(good), good)
        for bad in ("", "nvda", "A..B", "NVDA/../../etc", "A B", "NVDA?x=1", "A" * 20):
            with self.assertRaises(ValueError, msg=bad):
                valid_symbol(bad)

    def test_resolve_yahoo_symbol_ranks_and_normalizes(self):
        def fake_fetch(url):
            if "Palantir" in url:
                return {"quotes": [
                    {"symbol": "SPLTR=F", "shortname": "Palantir Futures", "quoteType": "FUTURE", "exchange": "CME"},
                    {"symbol": "PLTR", "shortname": "Palantir Technologies Inc.", "quoteType": "EQUITY", "exchange": "NMS"},
                ]}
            if "Bitcoin" in url:
                return {"quotes": [
                    {"symbol": "BTC-USD", "shortname": "Bitcoin USD", "quoteType": "CRYPTOCURRENCY", "exchange": "CCC"},
                ]}
            if "garbage" in url:
                return {"quotes": [
                    {"symbol": "../bad", "shortname": "Bad", "quoteType": "EQUITY"},
                    {"symbol": "OPT1", "shortname": "Option", "quoteType": "OPTION"},
                ]}
            return {"quotes": []}

        pltr = resolve_yahoo_symbol("Palantir", fetch=fake_fetch)
        self.assertEqual(pltr["symbol"], "PLTR")
        self.assertEqual(pltr["quote_type"], "EQUITY")
        btc = resolve_yahoo_symbol("Bitcoin", fetch=fake_fetch)
        self.assertEqual(btc["symbol"], "BTC")
        self.assertIsNone(resolve_yahoo_symbol("garbage", fetch=fake_fetch))

    def test_gliner_router_open_conversation_skips_ner_and_promotes_low_conf_explicit_ticker(self):
        class Backend:
            def __init__(self, intent, conf, entities=None):
                self.intent = intent
                self.conf = conf
                self.entities = entities or {}
                self.ner_calls = 0

            def classify_text(self, text, schema, include_confidence=False):
                return {
                    "intent": {"label": self.intent, "confidence": self.conf},
                    "horizon": {"label": "intraday", "confidence": 0.6},
                }

            def extract_entities(self, text, entity_types):
                self.ner_calls += 1
                return {"entities": self.entities}

        open_b = Backend("open_conversation", 0.85)
        r_open = GlinerQueryRouter(open_b, UNIVERSE).route("what's the capital of france")
        self.assertEqual(r_open.intent, "open_conversation")
        self.assertEqual(r_open.tickers, [])
        self.assertEqual(open_b.ner_calls, 0)

        spy_b = Backend("open_conversation", 0.44)
        r_spy = GlinerQueryRouter(spy_b, UNIVERSE).route("What is SPY doing today?")
        self.assertEqual(r_spy.intent, "price_snapshot")
        self.assertEqual(r_spy.tickers, ["SPY"])
        self.assertEqual(spy_b.ner_calls, 0)

        edu_b = Backend("open_conversation", 0.72)
        r_edu = GlinerQueryRouter(edu_b, UNIVERSE).route("Explain how SPY tracks the S&P 500")
        self.assertEqual(r_edu.intent, "open_conversation")

        pltr_b = Backend("price_snapshot", 0.62, {"company": ["Palantir"]})
        r_pltr = GlinerQueryRouter(pltr_b, {"NVDA"}).route("What is Palantir trading at?")
        self.assertEqual(r_pltr.intent, "price_snapshot")
        self.assertEqual(r_pltr.tickers, [])
        self.assertEqual(r_pltr.entity_candidates, ("Palantir",))
        self.assertEqual(pltr_b.ner_calls, 1)

    def test_open_chat_answers_with_local_model_preserves_session_tickers_and_skips_yahoo(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = RecordingNarratorModels()
            markets = []

            def mk_market(cancel=None):
                m = ResolvingFakeMarket(cancel=cancel)
                markets.append(m)
                return m

            def route_fn(q):
                if "NVDA" in q:
                    return RoutedQuery("price_snapshot", 0.9, "intraday", ["NVDA"])
                if "risks" in q.lower():
                    return RoutedQuery("thesis_review", 0.8, "swing_week", [])
                return RoutedQuery("open_conversation", 0.88, "intraday", [], entity_candidates=("France",))

            svc = ChatService(REPO, tmp, models=models, market_factory=mk_market, router_factory=lambda: ScriptedRouter(route_fn))
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                j1 = svc.submit(sess["id"], "What is NVDA trading at?", uuid.uuid4().hex, models.narrator_id)
                self.assertIn(wait(svc, j1)["status"], {"complete", "degraded"})
                self.assertEqual(svc.store.session(sess["id"])["tickers"], ["NVDA"])

                j2 = svc.submit(sess["id"], "what's the capital of france", uuid.uuid4().hex, models.narrator_id)
                d2 = wait(svc, j2)
                self.assertEqual(d2["status"], "complete")
                msg = svc.store.session(sess["id"])["messages"][-1]
                self.assertEqual(msg["payload"]["kind"], "conversation")
                self.assertTrue(msg["payload"]["unverified_chat"])
                self.assertNotIn("<think>", msg["content"])
                self.assertNotIn("\u2014", msg["content"])
                self.assertIn("Paris", msg["content"])
                self.assertEqual(svc.store.session(sess["id"])["tickers"], ["NVDA"])
                self.assertEqual(markets[-1].resolve_calls, [])
                self.assertEqual(markets[-1].snapshots, {})
                self.assertIsNotNone(models.complete_calls[-1]["messages"])
                self.assertEqual(models.complete_calls[-1]["messages"][-1]["content"], "what's the capital of france")
                self.assertEqual(len(svc.ledger.items(sess["id"])), 1)

                j3 = svc.submit(sess["id"], "What about risks?", uuid.uuid4().hex, models.narrator_id)
                self.assertIn(wait(svc, j3)["status"], {"complete", "degraded"})
                self.assertIn("NVDA", markets[-1].snapshots)
            finally:
                svc.close()

    def test_dynamic_yahoo_resolution_on_research_intent_and_no_watchlist_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = RecordingNarratorModels()
            markets = []

            def mk_market(cancel=None):
                m = ResolvingFakeMarket(cancel=cancel)
                markets.append(m)
                return m

            router = ScriptedRouter(lambda q: RoutedQuery("watchlist_add", 0.85, "swing_week", [], entity_candidates=("Palantir", "gold")))
            svc = ChatService(REPO, tmp, models=models, market_factory=mk_market, router_factory=lambda: router)
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                job = svc.submit(sess["id"], "Track Palantir and gold for me", uuid.uuid4().hex, models.narrator_id)
                done = wait(svc, job)
                self.assertIn(done["status"], {"complete", "degraded"})
                self.assertEqual(markets[-1].resolve_calls, ["Palantir", "gold"])
                self.assertEqual(svc.store.session(sess["id"])["tickers"], ["PLTR", "GC=F"])
                self.assertEqual(svc.store.watchlist(), [])
                trace = json.loads((Path(tmp) / "traces" / f"{job['id']}.json").read_text(encoding="utf-8"))
                self.assertTrue(any(s["name"] == "Resolve asset symbol" and s["actor"] == "Yahoo Finance search" for s in trace["spans"]))
            finally:
                svc.close()

    def test_facts_mode_never_invokes_narrator_for_open_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = RecordingNarratorModels()
            models.state["selected"] = "facts"
            router = ScriptedRouter(lambda q: RoutedQuery("open_conversation", 0.9, "intraday", []))
            svc = ChatService(REPO, tmp, models=models, market_factory=ResolvingFakeMarket, router_factory=lambda: router)
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                job = svc.submit(sess["id"], "how you doin", uuid.uuid4().hex, "facts")
                self.assertEqual(wait(svc, job)["status"], "complete")
                self.assertEqual(models.load_calls, 0)
                self.assertEqual(models.complete_calls, [])
                self.assertEqual(svc.store.session(sess["id"])["messages"][-1]["payload"]["kind"], "clarification")
            finally:
                svc.close()

    def test_attached_pdf_answers_document_question_but_allows_unrelated_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = RecordingNarratorModels()
            router = ScriptedRouter(lambda q: RoutedQuery("open_conversation", 0.72, "intraday", []))
            svc = ChatService(REPO, tmp, models=models, market_factory=FakeMarket, router_factory=lambda: router)
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                svc.store.add_document(sess["id"], "q3.pdf", "b" * 64, 100, ["Quarterly revenue reached 412 million USD."])
                j_doc = svc.submit(sess["id"], "What does the PDF say about revenue?", uuid.uuid4().hex, models.narrator_id)
                self.assertIn(wait(svc, j_doc)["status"], {"complete", "degraded"})
                self.assertEqual(svc.store.session(sess["id"])["messages"][-1]["payload"]["kind"], "research")

                j_chat = svc.submit(sess["id"], "how you doin", uuid.uuid4().hex, models.narrator_id)
                self.assertEqual(wait(svc, j_chat)["status"], "complete")
                self.assertEqual(svc.store.session(sess["id"])["messages"][-1]["payload"]["kind"], "conversation")
            finally:
                svc.close()

    def test_query_universe_ignores_uppercase_english_words_and_keeps_cashtags(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = ChatService(REPO, tmp, models=RecordingNarratorModels(), market_factory=FakeMarket)
            try:
                u1 = svc._query_universe("WHAT IS THE CAPITAL OF FRANCE? Explain ETF")
                self.assertNotIn("FRANCE", u1)
                self.assertNotIn("ETF", u1)
                self.assertNotIn("CAPITAL", u1)
                u2 = svc._query_universe("Check $MSTR today")
                self.assertIn("MSTR", u2)
            finally:
                svc.close()

    def test_news_only_skips_narrator_and_warm_narrator_respects_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = RecordingNarratorModels()
            router = ScriptedRouter(lambda q: RoutedQuery("news_catalysts", 0.9, "swing_week", ["NVDA"]))
            svc = ChatService(REPO, tmp, models=models, market_factory=FakeMarket, router_factory=lambda: router)
            try:
                with mock.patch.dict(os.environ, {"COFINANCE_NARRATOR_WARMUP": "off"}):
                    self.assertIsNone(svc.warm_narrator())
                self.assertEqual(models.load_calls, 0)
                sess = svc.store.create_session(uuid.uuid4().hex)
                j1 = svc.submit(sess["id"], "Any news on NVDA?", uuid.uuid4().hex, models.narrator_id, think=True)
                self.assertIn(wait(svc, j1)["status"], {"complete", "degraded"})
                self.assertEqual(models.load_calls, 0)
                t = svc.warm_narrator()
                self.assertIsNotNone(t)
                t.join(timeout=2)
                self.assertEqual(models.load_calls, 1)
            finally:
                svc.close()


if __name__ == "__main__":
    unittest.main()
