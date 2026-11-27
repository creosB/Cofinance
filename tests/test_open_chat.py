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

    def test_resolve_yahoo_symbol_ranks_by_relevance_explicit_ticker_and_abstains_on_junk(self):
        def fake_fetch(url):
            if "Palantir" in url:
                return {"quotes": [
                    {"symbol": "PLTR", "shortname": "Palantir Technologies Inc.", "quoteType": "EQUITY", "exchange": "NMS", "score": 166781.0},
                    {"symbol": "SPLTR=F", "shortname": "Palantir Futures", "quoteType": "FUTURE", "exchange": "CME", "score": 30053.0},
                ]}
            if "q=gold" in url or "q=GOLD" in url or "q=%24GOLD" in url:
                return {"quotes": [
                    {"symbol": "GC=F", "shortname": "Gold Dec 26", "quoteType": "FUTURE", "exchange": "CMX", "score": 19244500.0},
                    {"symbol": "GOLD", "shortname": "Gold.com, Inc.", "quoteType": "EQUITY", "exchange": "NYQ", "score": 12051500.0},
                    {"symbol": "GLD", "shortname": "SPDR Gold Shares", "quoteType": "ETF", "exchange": "PCX", "score": 78588.0},
                ]}
            if "US%2010Y" in url:
                return {"quotes": [
                    {"symbol": "DE000SLA1029.SG", "shortname": None, "quoteType": "INDEX", "exchange": "STU", "score": 20003.0},
                    {"symbol": "INFU.SW", "shortname": "AMU Inflation Expec ACC", "quoteType": "ETF", "exchange": "EBS", "score": 20001.0},
                ]}
            if "BIST%20100" in url:
                return {"quotes": [
                    {"symbol": "XU100.IS", "shortname": "BIST 100", "quoteType": "INDEX", "exchange": "IST", "score": 20549.0},
                ]}
            if "Bitcoin" in url:
                return {"quotes": [
                    {"symbol": "BTC-USD", "shortname": "Bitcoin USD", "quoteType": "CRYPTOCURRENCY", "exchange": "CCC", "score": 498654.4},
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
        gold_future = resolve_yahoo_symbol("gold", fetch=fake_fetch)
        self.assertEqual(gold_future["symbol"], "GC=F")
        self.assertEqual(gold_future["quote_type"], "FUTURE")
        self.assertEqual([a["symbol"] for a in gold_future["alternatives"]], ["GC=F", "GOLD", "GLD"])
        gold_equity = resolve_yahoo_symbol("GOLD", fetch=fake_fetch)
        self.assertEqual(gold_equity["symbol"], "GOLD")
        gold_cashtag = resolve_yahoo_symbol("$GOLD", fetch=fake_fetch)
        self.assertEqual(gold_cashtag["symbol"], "GOLD")
        self.assertIsNone(resolve_yahoo_symbol("US 10Y", fetch=fake_fetch))
        bist = resolve_yahoo_symbol("BIST 100", fetch=fake_fetch)
        self.assertEqual(bist["symbol"], "XU100.IS")
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

    def test_context_aware_yahoo_resolution_prefers_turkish_equity_and_flags_ambiguity(self):
        thy_quotes = {"quotes": [
            {"symbol": "THY", "shortname": "Toews Agility Shares Dynamic Ta", "quoteType": "ETF", "exchange": "PCX", "score": 10009500.0},
            {"symbol": "TKA.DE", "shortname": "thyssenkrupp AG", "quoteType": "EQUITY", "exchange": "GER", "score": 20410.0},
            {"symbol": "THYAO.IS", "shortname": "TURK HAVA YOLLARI", "quoteType": "EQUITY", "exchange": "IST", "score": 20162.0},
            {"symbol": "THYP", "shortname": "21shares Hyperliquid Staking ET", "quoteType": "ETF", "exchange": "NGM", "score": 20040.0},
        ]}
        fetch = lambda url: thy_quotes

        tr = resolve_yahoo_symbol("THY", fetch=fetch, context_query="THY, turkish stock. Should I buy now?")
        self.assertIsNotNone(tr)
        self.assertEqual(tr["symbol"], "THYAO.IS")
        self.assertEqual(tr["exchange"], "IST")
        self.assertFalse(tr.get("ambiguous", False))

        etf = resolve_yahoo_symbol("THY", fetch=fetch, context_query="Check THY ETF performance")
        self.assertIsNotNone(etf)
        self.assertEqual(etf["symbol"], "THY")
        self.assertEqual(etf["quote_type"], "ETF")
        self.assertFalse(etf.get("ambiguous", False))

        de = resolve_yahoo_symbol("THY", fetch=fetch, context_query="german stock TKA")
        self.assertIsNotNone(de)
        self.assertEqual(de["symbol"], "TKA.DE")

        amb = resolve_yahoo_symbol("thy", fetch=fetch, context_query="give info about thy stock")
        self.assertIsNotNone(amb)
        self.assertTrue(amb.get("ambiguous"))
        alt_syms = [a["symbol"] for a in amb["alternatives"]]
        self.assertIn("THYAO.IS", alt_syms)
        self.assertIn("TKA.DE", alt_syms)
        self.assertNotEqual(amb["symbol"], "THY")

    def test_gliner_router_extracts_fallback_asset_before_stock_and_keeps_generic_chat_open(self):
        class EmptyNerBackend:
            def __init__(self, intent, conf):
                self.intent, self.conf, self.ner_calls = intent, conf, 0

            def classify_text(self, text, schema, *, include_confidence=False):
                return {"intent": {"label": self.intent, "confidence": self.conf}, "horizon": {"label": "intraday", "confidence": 0.5}}

            def extract_entities(self, text, entity_types):
                self.ner_calls += 1
                return {"entities": {"company": [], "cryptocurrency": [], "ticker": []}}

        b_thy = EmptyNerBackend("open_conversation", 0.785)
        r_thy = GlinerQueryRouter(b_thy, UNIVERSE).route("give info about thy stock")
        self.assertEqual(b_thy.ner_calls, 1)
        self.assertEqual(r_thy.intent, "price_snapshot")
        self.assertEqual(r_thy.entity_candidates, ("thy",))

        b_edu = EmptyNerBackend("open_conversation", 0.85)
        r_edu = GlinerQueryRouter(b_edu, UNIVERSE).route("what is a dividend stock?")
        self.assertEqual(r_edu.intent, "open_conversation")
        self.assertEqual(r_edu.entity_candidates, ())

    def test_ambiguous_symbol_clarifies_and_open_chat_skips_deep_think(self):
        class AmbiguousMarket(FakeMarket):
            def resolve_symbol(self, term, context_query=""):
                return {
                    "symbol": "TKA.DE",
                    "name": "thyssenkrupp AG",
                    "quote_type": "EQUITY",
                    "exchange": "GER",
                    "ambiguous": True,
                    "alternatives": [
                        {"symbol": "TKA.DE", "name": "thyssenkrupp AG", "quote_type": "EQUITY", "exchange": "GER"},
                        {"symbol": "THYAO.IS", "name": "TURK HAVA YOLLARI", "quote_type": "EQUITY", "exchange": "IST"},
                    ],
                }

        with tempfile.TemporaryDirectory() as tmp:
            models = RecordingNarratorModels()

            def route_fn(q):
                if "thy" in q.lower():
                    return RoutedQuery("price_snapshot", 0.78, "intraday", [], entity_candidates=("thy",))
                return RoutedQuery("open_conversation", 0.9, "intraday", [])

            svc = ChatService(REPO, tmp, models=models, market_factory=AmbiguousMarket, router_factory=lambda: ScriptedRouter(route_fn))
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                j_amb = svc.submit(sess["id"], "give info about thy stock", uuid.uuid4().hex, models.narrator_id)
                done_amb = wait(svc, j_amb)
                self.assertEqual(done_amb["status"], "complete")
                msg = svc.store.session(sess["id"])["messages"][-1]
                self.assertEqual(msg["payload"]["kind"], "clarification")
                self.assertIn("THYAO.IS", msg["content"])
                self.assertIn("TKA.DE", msg["content"])
                self.assertEqual(models.complete_calls, [])

                j_open = svc.submit(sess["id"], "what's the capital of Turkey", uuid.uuid4().hex, models.narrator_id, think=True)
                done_open = wait(svc, j_open)
                self.assertEqual(done_open["status"], "complete")
                self.assertEqual(models.think_load_calls, 0)
                self.assertEqual(len(models.complete_calls), 1)
                self.assertFalse(models.complete_calls[0]["think"])
                open_msg = svc.store.session(sess["id"])["messages"][-1]
                self.assertFalse(open_msg["payload"]["deep_think"]["used"])
                self.assertIn("general chat", open_msg["payload"]["deep_think"]["note"].lower())
            finally:
                svc.close()

    def test_no_news_keeps_faithful_research_complete_and_preserves_safe_trace_fields(self):
        from core.run_trace import safe
        from ui.workbench import read_trace

        self.assertEqual(
            safe({
                "previous_session_close": 309.75,
                "previous_session_date": "2026-11-26",
                "change_from_previous_session_pct": 0.73,
                "output_token_limit": 256,
                "session_id": "secret-sid",
                "api_key": "secret-key",
                "access_token": "secret-tok",
                "token_count": "secret-tc",
            }),
            {
                "previous_session_close": 309.75,
                "previous_session_date": "2026-11-26",
                "change_from_previous_session_pct": 0.73,
                "output_token_limit": 256,
                "session_id": "[redacted]",
                "api_key": "[redacted]",
                "access_token": "[redacted]",
                "token_count": "[redacted]",
            },
        )

        class FaithfulModels(RecordingNarratorModels):
            def complete(self, system, prompt, **kwargs):
                if "User question:" in prompt:
                    return (
                        "Türk Hava Yollari Anonim Ortakligi (THYAO.IS) last price is 312.0 TRY, "
                        "up 0.73 percent from previous session close 309.75 TRY, while no accepted news is available in the local store. "
                        "The reports do not identify a specific risk."
                    )
                return "Türk Hava Yollari Anonim Ortakligi (THYAO.IS) last price is 312.0 TRY, up 0.73 percent from previous session close 309.75 TRY."

        class TryMarket(FakeMarket):
            def resolve_symbol(self, term, context_query=''):
                if term == 'THYAO.IS':
                    return {'symbol': term, 'name': 'Turkish Airlines', 'exchange': 'IST', 'quote_type': 'EQUITY'}
                return None

            def get_snapshot(self, ticker):
                snap = {
                    "ticker": ticker,
                    "name": "Türk Hava Yollari Anonim Ortakligi",
                    "currency": "TRY",
                    "status": "available",
                    "price": 312.0,
                    "previous_session_close": 309.75,
                    "previous_session_date": "2026-11-26",
                    "change_from_previous_session_pct": 0.73,
                    "source": "Yahoo Finance",
                }
                self.snapshots[ticker] = snap
                return snap

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = FaithfulModels()
            router = ScriptedRouter(lambda q: RoutedQuery("thesis_review", 0.8, "intraday", ["THYAO.IS"]))
            svc = ChatService(REPO, root, models=models, market_factory=TryMarket, router_factory=lambda: router)
            try:
                sess = svc.store.create_session(uuid.uuid4().hex)
                job = svc.submit(sess["id"], "THY, turkish stock. Should I buy now?", uuid.uuid4().hex, models.narrator_id)
                done = wait(svc, job)
                self.assertEqual(done["status"], "complete")
                tr = read_trace(root / "traces", job["id"])
                self.assertEqual(tr["status"], "complete")
                news_span = next(s for s in tr["spans"] if s["name"] == "News Researcher")
                self.assertEqual(news_span["status"], "complete")
                self.assertEqual(news_span["outputs"]["validation"]["validation_reason"], "no_news")
                da_span = next(s for s in tr["spans"] if s["name"] == "Data Analyst")
                raw_snap = da_span["outputs"]["raw_facts"]["THYAO.IS"]
                self.assertEqual(raw_snap["previous_session_close"], 309.75)
                self.assertEqual(raw_snap["previous_session_date"], "2026-11-26")
                self.assertEqual(raw_snap["change_from_previous_session_pct"], 0.73)
            finally:
                svc.close()

    def test_process_guard_matches_only_cofinance_children_and_binds_on_windows(self):
        import subprocess
        import sys
        from core.process_guard import _is_cofinance_child, bind_child

        repo_str = str(REPO.resolve()).lower()
        data_str = str((REPO / "data").resolve()).lower() + os.sep.lower()
        self.assertTrue(_is_cofinance_child(
            {"Name": "llama-server.exe", "ExecutablePath": str(REPO / "data/local_models/b12110-vulkan/llama-server.exe"), "CommandLine": ""},
            repo_str, data_str,
        ))
        self.assertTrue(_is_cofinance_child(
            {"Name": "python.exe", "ExecutablePath": "python.exe", "CommandLine": f"python.exe -u {REPO / 'core/hymemory_worker.py'}"},
            repo_str, data_str,
        ))
        self.assertFalse(_is_cofinance_child(
            {"Name": "llama-server.exe", "ExecutablePath": r"C:\OtherApp\llama-server.exe", "CommandLine": ""},
            repo_str, data_str,
        ))
        self.assertFalse(_is_cofinance_child(
            {"Name": "pythonw.exe", "ExecutablePath": "pythonw.exe", "CommandLine": r"pythonw.exe C:\Users\baris\Desktop\new bridge\PCbridge.pyw"},
            repo_str, data_str,
        ))
        if os.name == "nt":
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
            try:
                self.assertTrue(bind_child(proc))
            finally:
                proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
