import http.client
import json
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from core.chat_service import ChatService, NewsReader
from core.feeds import FeedError
from core.local_models import ModelError
from core.market import MarketTools
from core.news_service import FeedFetcher, coverage_note, feed_tickers, news_health
from core.news_store import NewsStore
from core.orchestrator import DataAnalystAgent, EventBus, market_fact_lines
from core.trace import TraceRecorder, run_traced_clock1
from core.gliner_decide import GlinerDecideClassifier, GlinerQueryRouter
from tests.test_chat import FakeMarket
from tests.test_orchestrator import StubGliner
from ui.dashboard import create_server

REPO = Path(__file__).resolve().parents[1]


def nvidia_feed(days_old=1, title="NVIDIA announces new data center platform"):
    when = format_datetime(datetime.now(timezone.utc) - timedelta(days=days_old))
    return ("<rss version='2.0'><channel><title>NVIDIA</title>"
            f"<item><title>{title}</title><link>https://nvidianews.nvidia.com/news/platform</link>"
            f"<pubDate>{when}</pubDate><description>Company announcement.</description></item>"
            "<item><title>Off-site link</title><link>https://attacker.test/x</link>"
            f"<pubDate>{when}</pubDate></item></channel></rss>").encode()


def apple_feed():
    when = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    return ("<feed xmlns='http://www.w3.org/2005/Atom'><title>Apple</title><entry><title>Apple updates a product</title>"
            f"<link rel='alternate' href='https://www.apple.com/newsroom/2026/11/update/'/><updated>{when}</updated>"
            "<summary>Update.</summary></entry></feed>").encode()


def fake_fetch(name, timeout=15.0):
    return nvidia_feed() if name == "nvidia_newsroom" else apple_feed()


class SignalBackend:
    """GLiNER-shaped stub: material but not urgent, so the gate stores a DIGEST, never an ALERT."""
    model_id = "stub-signal-heads"

    def classify_text(self, text, schema, include_confidence=False):
        return {"materiality": {"label": "material", "confidence": 0.7},
                "urgency": {"label": "digest", "confidence": 0.8},
                "direction": {"label": "bullish", "confidence": 0.6}}

    def extract_entities(self, text, entity_types):
        return {"entities": {}}


class TestFeedFetcher(unittest.TestCase):
    def test_registered_feed_becomes_items_and_unsafe_links_are_rejected(self):
        result = FeedFetcher(fake_fetch)("NVDA")
        self.assertEqual([i.title for i in result.items], ["NVIDIA announces new data center platform"])
        self.assertEqual(result.items[0].source, "nvidia_newsroom")
        self.assertGreater(result.items[0].published_at, 0)
        self.assertIn("1 rejected", result.source_status["nvidia_newsroom"])

    def test_ticker_without_a_registered_source_is_explicit(self):
        result = FeedFetcher(fake_fetch)("BTC")
        self.assertEqual(result.items, [])
        self.assertIn("no approved source", result.source_status["none"])

    def test_feed_failure_is_a_status_not_a_crash(self):
        def down(name, timeout=15.0):
            raise FeedError("feed fetch failed: URLError")
        result = FeedFetcher(down)("NVDA")
        self.assertEqual(result.items, [])
        self.assertTrue(result.source_status["nvidia_newsroom"].startswith("error"))


class TestOperationalCollection(unittest.TestCase):
    def test_collection_writes_gated_rows_that_chat_can_read(self):
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "news.db"
            store = NewsStore(str(db))
            trace = TraceRecorder("collection", directory=Path(root) / "traces")
            try:
                summary = run_traced_clock1(feed_tickers(), FeedFetcher(fake_fetch),
                                            GlinerDecideClassifier(SignalBackend()), trace, store=store)
            finally:
                store.close()
            self.assertEqual(summary.actions.get("digest"), 2)
            self.assertNotIn("alert", summary.actions)
            names = [s["name"] for s in trace.data["spans"]]
            self.assertIn("Clock 1 collection", names)
            self.assertEqual(trace.data["summary"]["persistence"], "local news store")
            self.assertTrue(any(n.startswith("Signal gate: DIGEST") for n in names))
            reader = NewsReader(db)
            rows = reader.get_gated_signals("NVDA")
            self.assertEqual(len(rows), 1)
            self.assertEqual(reader.evidence[0]["url"], "https://nvidianews.nvidia.com/news/platform")
            health = news_health(db, ["NVDA", "BTC"])
            self.assertTrue(health["collected"])
            self.assertEqual(health["uncovered"], ["BTC"])
            nvda = next(r for r in health["tickers"] if r["ticker"] == "NVDA")
            self.assertEqual(nvda["digest"], 1)
            self.assertIn("official company feeds", coverage_note(health, ["NVDA"]))
            self.assertIn("No approved news source covers BTC", coverage_note(health, ["NVDA", "BTC"]))

    def test_shadow_rule_urgency_is_recorded_but_never_acted_on(self):
        def results_fetch(name, timeout=15.0):
            return nvidia_feed(title="NVIDIA reports third quarter results") if name == "nvidia_newsroom" else apple_feed()
        with tempfile.TemporaryDirectory() as root:
            store = NewsStore(str(Path(root) / "news.db"))
            trace = TraceRecorder("collection", directory=Path(root) / "traces")
            try:
                summary = run_traced_clock1(feed_tickers(), FeedFetcher(results_fetch),
                                            GlinerDecideClassifier(SignalBackend()), trace, store=store)
            finally:
                store.close()
            self.assertNotIn("alert", summary.actions)
            gates = {s["outputs"]["title"]: s["outputs"] for s in trace.data["spans"] if s["name"].startswith("Signal gate:")}
            shadow = gates["NVIDIA reports third quarter results"]["shadow_rule_urgency"]
            self.assertEqual((shadow["would_be_urgent"], shadow["rule"]), (True, "results"))
            self.assertEqual(gates["NVIDIA reports third quarter results"]["action"], "digest")
            self.assertFalse(gates["Apple updates a product"]["shadow_rule_urgency"]["would_be_urgent"])
            self.assertEqual(trace.data["summary"]["shadow_rule_urgency"]["would_be_urgent"], 1)

    def test_second_collection_does_not_rejudge_seen_items(self):
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "news.db"
            for expected in (2, 0):
                store = NewsStore(str(db))
                try:
                    summary = run_traced_clock1(feed_tickers(), FeedFetcher(fake_fetch),
                                                GlinerDecideClassifier(SignalBackend()), TraceRecorder("c"), store=store)
                finally:
                    store.close()
                self.assertEqual(summary.actions.get("digest", 0), expected)

    def test_missing_store_reports_not_collected_without_creating_it(self):
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "news.db"
            health = news_health(db)
            self.assertFalse(health["collected"])
            self.assertFalse(db.exists())
            self.assertIn("has not been collected", coverage_note(health, ["NVDA"]))


class TestServiceCollection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = ChatService(REPO, self.tmp.name, market_factory=FakeMarket,
                                   router_factory=lambda: GlinerQueryRouter(StubGliner(), {"NVDA"}),
                                   news_backend_factory=SignalBackend, feed_fetch=fake_fetch)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def wait(self):
        for _ in range(200):
            if self.service.news["phase"] != "running":
                return self.service.news_snapshot()
            time.sleep(.02)
        raise AssertionError("collection did not finish")

    def test_explicit_collection_feeds_the_next_answer(self):
        request = uuid.uuid4().hex
        self.service.collect_news(request)
        self.assertEqual(self.service.collect_news(request)["run_id"], self.service.news["run_id"])  # idempotent
        news = self.wait()
        self.assertEqual(news["phase"], "idle", news)
        self.assertTrue(news["health"]["collected"])
        self.assertTrue((Path(self.tmp.name) / "traces" / (news["run_id"] + ".json")).is_file())
        session = self.service.store.create_session(uuid.uuid4().hex)
        job = self.service.submit(session["id"], "Review the NVDA thesis with news", uuid.uuid4().hex, "facts")
        for _ in range(200):
            if not self.service.active:
                break
            time.sleep(.02)
        answer = self.service.store.session(session["id"])["messages"][-1]
        self.assertIn("NVIDIA announces new data center platform", answer["content"])
        self.assertTrue(answer["payload"]["news_collected"])
        self.assertIn("official company feeds", answer["payload"]["coverage_note"])
        self.assertTrue(any(e["url"].startswith("https://nvidianews") for e in answer["payload"]["evidence"]))

    def test_research_waits_while_collection_runs(self):
        self.service.news["phase"] = "running"
        session = self.service.store.create_session(uuid.uuid4().hex)
        with self.assertRaises(ModelError):
            self.service.submit(session["id"], "What is NVDA trading at?", uuid.uuid4().hex, "facts")
        self.service.news["phase"] = "idle"

    def test_collection_failure_is_visible(self):
        def broken():
            raise RuntimeError("judge unavailable")
        self.service.news_backend_factory = broken
        self.service.collect_news(uuid.uuid4().hex)
        news = self.wait()
        self.assertEqual(news["phase"], "error")
        self.assertIn("judge unavailable", news["error"])


class TestCollectEndpoint(unittest.TestCase):
    def test_collection_requires_same_origin_and_token(self):
        with tempfile.TemporaryDirectory() as root:
            service = ChatService(REPO, root, market_factory=FakeMarket, news_backend_factory=SignalBackend,
                                  feed_fetch=fake_fetch)
            server = create_server(port=0, service=service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_port
                body = json.dumps({"request_id": uuid.uuid4().hex})
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/api/news/collect", body, {"Content-Type": "application/json",
                             "Origin": f"http://127.0.0.1:{port}", "Host": f"127.0.0.1:{port}"})
                response = conn.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                conn.close()
                self.assertEqual(service.news["phase"], "idle")
                self.assertIsNone(service.news["run_id"])
            finally:
                server.shutdown()
                server.server_close()
                service.close()


class TestMarketFacts(unittest.TestCase):
    def test_window_close_is_not_presented_as_previous_close(self):
        payload = {"chart": {"result": [{"meta": {"regularMarketPrice": 186.64, "regularMarketTime": 1794000000,
                   "chartPreviousClose": 188.08, "currency": "USD", "fiftyTwoWeekHigh": 243.37, "fiftyTwoWeekLow": 164.27},
                   "timestamp": [1793827800, 1793914200, 1794000600],
                   "indicators": {"quote": [{"close": [200.130005, 181.600006, 186.639999]}]}}]}}
        snap = MarketTools(fetch=lambda url: payload).get_snapshot("NVDA")
        self.assertEqual(snap["close_before_window"], 188.08)
        self.assertNotIn("series_previous_close", snap)
        self.assertEqual(snap["previous_session_close"], 181.6)
        lines = "\n".join(market_fact_lines("NVDA", snap))
        self.assertIn("Previous session close: 181.6 USD", lines)
        self.assertNotIn("188.08", lines)
        for leaked in ("2026", "fetched", "Yahoo", "status", "endpoint", "52"):
            self.assertNotIn(leaked, lines)

    def test_narrator_cannot_reuse_hidden_provenance_numbers(self):
        class Tools:
            def get_snapshot(self, ticker):
                return {"price": 186.64, "close_before_window": 188.08, "currency": "USD", "status": "available"}
            def plot_history(self, ticker):
                return ""
        class Narrator:
            def complete(self, *a, **k):
                return "📊 [Data Analyst] NVDA trades at 186.64 USD, down from 188.08 USD."
        report = DataAnalystAgent(Narrator(), Tools()).run(["NVDA"], "fundamental_equity", EventBus(), "t")
        self.assertFalse(report.faithful_numbers)
        self.assertNotIn("188.08", report.summary)
        self.assertIn("Last price: 186.64 USD", report.summary)


if __name__ == "__main__":
    unittest.main()
