import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.chat_service import ChatService
from core.crowd_odds import lookup
from core.local_models import Cancelled, digest, local_json
from core.market import MarketTools, SnapshotCache
from core.gliner_decide import RoutedQuery
from core.run_trace import TraceRecorder, TracedTools


def _chart_payload():
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 100.0,
                        "regularMarketTime": 1794000000,
                        "currency": "USD",
                        "fiftyTwoWeekHigh": 200.0,
                        "fiftyTwoWeekLow": 50.0,
                    },
                    "timestamp": [1793800000, 1793900000, 1794000000],
                    "indicators": {"quote": [{"close": [90.0, 95.0, 100.0]}]},
                }
            ]
        }
    }


def _ath_payload():
    ts = [946684800 + i * 2592000 for i in range(24)]
    return {
        "chart": {
            "result": [
                {
                    "meta": {},
                    "timestamp": ts,
                    "indicators": {"quote": [{"high": [50.0 + i for i in range(23)] + [125.0]}]},
                }
            ]
        }
    }


def _fundamentals_payload():
    return {
        "timeseries": {
            "result": [
                {
                    "meta": {"type": ["trailingPeRatio"]},
                    "trailingPeRatio": [
                        {"asOfDate": "2026-11-06", "periodType": "TTM", "currencyCode": None, "reportedValue": {"raw": 24.5}}
                    ],
                },
                {
                    "meta": {"type": ["trailingMarketCap"]},
                    "trailingMarketCap": [
                        {"asOfDate": "2026-11-06", "periodType": "TTM", "currencyCode": "USD", "reportedValue": {"raw": 2000000000000.0}}
                    ],
                },
            ],
            "error": None,
        }
    }


def _sentiment_payload():
    return {"data": [{"value": "64", "value_classification": "Greed", "timestamp": "1794000000"}]}


class TestConcurrentIO(unittest.TestCase):
    def test_equity_secondary_fetches_overlap_and_preserve_fields(self):
        delay = 0.18
        barrier = threading.Barrier(2, timeout=1.0)
        overlapped = {"yes": False}

        def fetch(url):
            if "fundamentals-timeseries" in url:
                try:
                    barrier.wait()
                    overlapped["yes"] = True
                except threading.BrokenBarrierError:
                    pass
                time.sleep(delay)
                return _fundamentals_payload()
            if "range=max" in url:
                try:
                    barrier.wait()
                    overlapped["yes"] = True
                except threading.BrokenBarrierError:
                    pass
                time.sleep(delay)
                return _ath_payload()
            return _chart_payload()

        t0 = time.monotonic()
        tools = MarketTools(fetch=fetch)
        snap = tools.get_snapshot("NVDA")
        elapsed = time.monotonic() - t0
        self.assertTrue(overlapped["yes"])
        self.assertLess(elapsed, delay * 1.8)
        self.assertEqual(snap["pe_ratio"], 24.5)
        self.assertEqual(snap["market_cap"], 2000000000000)
        self.assertEqual(snap["all_time_high"], 125.0)
        self.assertEqual(len(tools.evidence), 1)
        self.assertEqual(tools.evidence[0]["source"], "Yahoo Finance")

    def test_crypto_secondary_fetches_overlap_and_preserve_evidence_order(self):
        barrier = threading.Barrier(2, timeout=1.0)
        overlapped = {"yes": False}

        def fetch(url):
            if "range=max" in url:
                try:
                    barrier.wait()
                    overlapped["yes"] = True
                except threading.BrokenBarrierError:
                    pass
                time.sleep(0.05)
                return _ath_payload()
            if "alternative.me" in url:
                try:
                    barrier.wait()
                    overlapped["yes"] = True
                except threading.BrokenBarrierError:
                    pass
                time.sleep(0.12)
                return _sentiment_payload()
            return _chart_payload()

        tools = MarketTools(fetch=fetch)
        snap = tools.get_snapshot("BTC")
        self.assertTrue(overlapped["yes"])
        self.assertEqual(snap["all_time_high"], 125.0)
        self.assertEqual(snap["fear_greed_index"], 64)
        self.assertEqual([e["source"] for e in tools.evidence], ["Alternative.me", "Yahoo Finance"])

    def test_secondary_cancel_propagates_without_swallowing(self):
        cancel = threading.Event()

        def fetch(url):
            if "fundamentals-timeseries" in url or "range=max" in url:
                cancel.set()
            return _chart_payload()

        with self.assertRaises(Cancelled):
            MarketTools(cancel=cancel, fetch=fetch).get_snapshot("NVDA")

    def test_crowd_odds_multi_asset_overlaps_and_preserves_ticker_order(self):
        barrier = threading.Barrier(3, timeout=1.0)
        overlapped = {"yes": False}
        sleeps = {"bitcoin": 0.14, "ethereum": 0.08, "solana": 0.02}

        def search(term):
            try:
                barrier.wait()
                overlapped["yes"] = True
            except threading.BrokenBarrierError:
                pass
            time.sleep(sleeps[term])
            raise TimeoutError(term)

        t0 = time.monotonic()
        res = lookup(["BTC", "ETH", "SOL"], search=search, now=1_790_000_000.0)
        elapsed = time.monotonic() - t0
        self.assertTrue(overlapped["yes"])
        self.assertLess(elapsed, 0.22)
        self.assertEqual(res["errors"], ["BTC: TimeoutError", "ETH: TimeoutError", "SOL: TimeoutError"])

    def test_chat_service_overlaps_crowd_odds_with_team_run(self):
        barrier = threading.Barrier(2, timeout=1.5)
        overlapped = {"yes": False}

        class FakeRouter:
            def route(self, text):
                return RoutedQuery("price_snapshot", 0.95, "swing_week", ["NVDA"])

        class SlowMarket:
            def __init__(self, cancel=None):
                self.charts, self.snapshots, self.evidence = {}, {}, []

            def get_snapshot(self, ticker):
                try:
                    barrier.wait()
                    overlapped["yes"] = True
                except threading.BrokenBarrierError:
                    pass
                snap = {"ticker": ticker, "price": 142.5, "currency": "USD", "status": "available", "price_as_of": "2026-11-26T00:00:00+00:00"}
                self.snapshots[ticker] = snap
                return snap

            def plot_history(self, ticker):
                return ""

            def add_to_watchlist(self, ticker):
                raise RuntimeError("disallowed")

        def slow_odds(tickers):
            try:
                barrier.wait()
                overlapped["yes"] = True
            except threading.BrokenBarrierError:
                pass
            return {
                "source": "Polymarket public API, read-only",
                "fetched_at": "2026-11-26T00:00:00+00:00",
                "markets": [{"ticker": "NVDA", "event": "NVDA test", "question": "Q", "outcome": "Yes", "probability": 0.5, "price_basis": "midpoint", "bid": 0.49, "ask": 0.51, "volume_24h": 5000, "ends": "2027-01-01T00:00:00+00:00", "url": "https://polymarket.com/event/nvda", "history": None}],
                "errors": [],
                "calibration_study": None,
                "note": "not advice",
            }

        with tempfile.TemporaryDirectory() as tmp:
            svc = ChatService(
                Path(__file__).resolve().parents[1],
                root=Path(tmp),
                market_factory=SlowMarket,
                router_factory=lambda: FakeRouter(),
                crowd_odds=slow_odds,
            )
            try:
                sid = "1" * 32
                rid = "2" * 32
                svc.store.create_session(sid)
                job = svc.submit(sid, "Check NVDA", rid, "facts")
                for _ in range(80):
                    cur = svc.store.job(job["id"])
                    if cur["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.05)
                self.assertIn(cur["status"], {"complete", "degraded"})
                self.assertTrue(overlapped["yes"])
                sess = svc.store.session(sid)
                assistant = [m for m in sess["messages"] if m["role"] == "assistant"][-1]
                self.assertIsNotNone(assistant["payload"]["crowd_odds"])
            finally:
                svc.close()

    def test_multi_asset_snapshots_overlap_and_preserve_input_order(self):
        barrier = threading.Barrier(2, timeout=1.0)
        overlapped = {"yes": False}

        def fetch(url):
            if "/chart/" in url and "range=1y" in url:
                try:
                    barrier.wait()
                    overlapped["yes"] = True
                except threading.BrokenBarrierError:
                    pass
                if "AAPL" in url:
                    time.sleep(0.08)
                return _chart_payload()
            if "fundamentals-timeseries" in url:
                return _fundamentals_payload()
            if "range=max" in url:
                return _ath_payload()
            return {}

        tools = MarketTools(fetch=fetch)
        trace = TraceRecorder("Parallel snapshots", mode="fixture", directory=None)
        traced = TracedTools(tools, trace)
        snaps = traced.get_snapshots(["AAPL", "MSFT"])
        self.assertTrue(overlapped["yes"])
        self.assertEqual(list(snaps.keys()), ["AAPL", "MSFT"])
        self.assertEqual(list(tools.snapshots.keys()), ["AAPL", "MSFT"])
        self.assertEqual(list(tools.charts.keys()), ["AAPL", "MSFT"])
        span_tickers = [s["inputs"]["ticker"] for s in trace.data["spans"] if s["name"] == "Market snapshot"]
        self.assertEqual(span_tickers, ["AAPL", "MSFT"])

    def test_multi_candidate_symbol_resolution_overlaps_and_keeps_order(self):
        barrier = threading.Barrier(2, timeout=1.0)
        overlapped = {"yes": False}

        class ParallelResolveMarket:
            def __init__(self, cancel=None):
                self.snapshots = {}
                self.charts = {}
                self.evidence = []

            def resolve_symbol(self, term, context_query=""):
                try:
                    barrier.wait()
                    overlapped["yes"] = True
                except threading.BrokenBarrierError:
                    pass
                if term == "FirstCo":
                    time.sleep(0.08)
                    return {"symbol": "FIRST", "name": "First Corp", "quote_type": "EQUITY", "exchange": "NMS"}
                return {"symbol": "SECOND", "name": "Second Corp", "quote_type": "EQUITY", "exchange": "NMS"}

            def get_snapshot(self, ticker):
                snap = {"ticker": ticker, "status": "available", "price": 100.0, "currency": "USD",
                        "price_as_of": "2026-11-27", "quote_status": {"state": "last_session", "as_of": "2026-11-27"}}
                self.snapshots[ticker] = snap
                return snap

            def plot_history(self, ticker):
                return ""

        class TwoCandidateRouter:
            def route(self, _q):
                return RoutedQuery("compare_assets", 0.9, "swing_week", [], entity_candidates=("FirstCo", "SecondCo"))

        with tempfile.TemporaryDirectory() as tmp:
            svc = ChatService(
                Path(__file__).resolve().parents[1],
                root=Path(tmp),
                market_factory=ParallelResolveMarket,
                router_factory=lambda: TwoCandidateRouter(),
                crowd_odds=lambda _t: None,
            )
            try:
                sid = "3" * 32
                svc.store.create_session(sid)
                job = svc.submit(sid, "Compare FirstCo and SecondCo", "4" * 32, "facts")
                for _ in range(100):
                    cur = svc.store.job(job["id"])
                    if cur["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.03)
                self.assertIn(cur["status"], {"complete", "degraded"})
                self.assertTrue(overlapped["yes"])
                sess = svc.store.session(sid)
                self.assertEqual(sess["messages"][-1]["payload"]["tickers"], ["FIRST", "SECOND"])
                trace = __import__("json").loads((Path(tmp) / "traces" / f"{job['id']}.json").read_text(encoding="utf-8"))
                resolved_terms = [s["inputs"]["term"] for s in trace["spans"] if s["name"] == "Resolve asset symbol"]
                self.assertEqual(resolved_terms, ["FirstCo", "SecondCo"])
            finally:
                svc.close()

    def test_snapshot_cache_hits_expires_preserves_quote_status_and_skips_failures(self):
        now = [100.0]
        calls = {"chart": 0}
        fail_next = {"on": False}

        def fetch(url):
            if fail_next["on"]:
                raise TimeoutError("provider timeout")
            if "/chart/" in url and "range=1y" in url:
                calls["chart"] += 1
                return _chart_payload()
            if "fundamentals-timeseries" in url:
                return _fundamentals_payload()
            if "range=max" in url:
                return _ath_payload()
            return {}

        cache = SnapshotCache(ttl=45.0, clock=lambda: now[0])
        t1 = MarketTools(fetch=fetch, cache=cache)
        s1 = t1.get_snapshot("NVDA")
        self.assertEqual(calls["chart"], 1)
        s1["price"] = 999999.0

        now[0] = 120.0
        t2 = MarketTools(fetch=fetch, cache=cache)
        s2 = t2.get_snapshot("NVDA")
        self.assertEqual(calls["chart"], 1)
        self.assertEqual(s2["price"], 100.0)
        self.assertEqual(s2["price_as_of"], s1["price_as_of"])
        self.assertEqual(s2["quote_status"], s1["quote_status"])
        self.assertIn("NVDA", t2.cache_hits)
        self.assertIn("NVDA", t2.charts)

        now[0] = 150.0
        t3 = MarketTools(fetch=fetch, cache=cache)
        t3.get_snapshot("NVDA")
        self.assertEqual(calls["chart"], 2)

        cache.clear()
        fail_next["on"] = True
        t4 = MarketTools(fetch=fetch, cache=cache)
        bad = t4.get_snapshot("NVDA")
        self.assertEqual(bad["status"], "unavailable")
        fail_next["on"] = False
        t5 = MarketTools(fetch=fetch, cache=cache)
        good = t5.get_snapshot("NVDA")
        self.assertEqual(good["status"], "available")

    def test_digest_memoization_strict_mode_and_local_json_keepalive_reconnect(self):
        import hashlib
        import http.server
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "weights.bin"
            sample.write_bytes(b"abcd1234")
            first = digest(sample)
            self.assertEqual(first, hashlib.sha256(b"abcd1234").hexdigest())
            st = sample.stat()
            sample.write_bytes(b"efgh5678")
            os.utime(sample, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertEqual(digest(sample), first)
            with patch.dict(os.environ, {"COFINANCE_STRICT_MODEL_VERIFY": "1"}):
                self.assertEqual(digest(sample), hashlib.sha256(b"efgh5678").hexdigest())

        connections = []
        drop_once = {"done": False}

        class KeepAliveHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                connections.append(self.client_address)
                if self.path == "/drop" and not drop_once["done"]:
                    drop_once["done"] = True
                    self.close_connection = True
                    self.connection.close()
                    return
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), KeepAliveHandler)
        srv.daemon_threads = True
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            self.assertEqual(local_json(base + "/health", key="k", timeout=5), {"ok": True})
            self.assertEqual(local_json(base + "/health", key="k", timeout=5), {"ok": True})
            self.assertEqual(connections[0], connections[1])
            self.assertEqual(local_json(base + "/drop", key="k", timeout=5), {"ok": True})
            self.assertGreaterEqual(len(connections), 3)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
