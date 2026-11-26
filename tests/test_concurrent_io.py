import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.chat_service import ChatService
from core.crowd_odds import lookup
from core.local_models import Cancelled
from core.market import MarketTools
from core.gliner_decide import RoutedQuery


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


if __name__ == "__main__":
    unittest.main()
