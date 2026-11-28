import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.chat_service import ChatService, LiveTrace
from core.gliner_decide import GlinerQueryRouter, RoutedQuery
from core.market import MarketTools
from core.orchestrator import DataAnalystAgent, EventBus, NewsResearcherAgent, TeamLeadOrchestrator
from core.process_guard import ProcessSlotLock, require_memory_headroom
from core.risk_cues import risk_cues
from core.run_trace import TraceRecorder
from ui.workbench import list_runs

REPO = Path(__file__).resolve().parents[1]

AAPL_SNAP = {
    "ticker": "AAPL",
    "name": "Apple Inc.",
    "currency": "USD",
    "status": "available",
    "price": 358.54,
    "previous_session_close": 355.58,
    "change_from_previous_session_pct": 0.83,
    "pe_ratio": 38.87,
    "fundamentals_status": "Available",
    "fundamentals_as_of": {"pe_ratio": {}},
}

NVDA_SNAP = {
    "ticker": "NVDA",
    "name": "NVIDIA Corporation",
    "currency": "USD",
    "status": "available",
    "price": 150.49,
    "previous_session_close": 149.3,
    "change_from_previous_session_pct": 0.80,
    "pe_ratio": 13.48,
    "fundamentals_status": "Available",
    "fundamentals_as_of": {"pe_ratio": {}},
}


class _MultiTools:
    def get_snapshot(self, ticker):
        return dict(AAPL_SNAP if ticker == "AAPL" else NVDA_SNAP)

    def plot_history(self, ticker):
        return f"chart://{ticker}"


class _EmptyNews:
    def get_gated_signals(self, _ticker, limit=5):
        return []


class _FixedRouter:
    def __init__(self, routed: RoutedQuery):
        self.routed = routed

    def route(self, _query):
        return self.routed


class _RecordingLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.model_id = "fixture/lfm3"

    def complete(self, system, prompt, *, max_tokens=256, **options):
        self.calls.append({"system": system, "prompt": prompt, "options": dict(options)})
        return self.replies.pop(0) if self.replies else ""


class TestAuditFixesAccuracy(unittest.TestCase):
    def test_multi_asset_preserves_both_paragraphs_without_stop_truncation(self):
        llm = _RecordingLLM([
            "Apple Inc. (AAPL) trades at 358.54 USD, up 0.83 percent.\n\n"
            "NVIDIA Corporation (NVDA) trades at 150.49 USD, up 0.80 percent."
        ])
        rep = DataAnalystAgent(llm, _MultiTools()).run(["AAPL", "NVDA"], "fundamental_equity", EventBus(), "s1")
        self.assertTrue(rep.faithful_numbers)
        self.assertNotIn("stop", llm.calls[0]["options"])
        self.assertIn("AAPL", rep.summary)
        self.assertIn("NVDA", rep.summary)
        self.assertIn("150.49 USD", rep.summary)

    def test_multi_asset_rejects_silent_ticker_omission_in_analyst_and_lead(self):
        analyst_llm = _RecordingLLM(["Apple Inc. (AAPL) trades at 358.54 USD, up 0.83 percent."])
        rep = DataAnalystAgent(analyst_llm, _MultiTools()).run(["AAPL", "NVDA"], "fundamental_equity", EventBus(), "s2")
        self.assertFalse(rep.faithful_numbers)
        self.assertEqual(rep.validation.get("validation_reason"), "missing_tickers")
        self.assertEqual(rep.validation.get("missing_tickers"), ["NVDA"])
        self.assertIn("NVIDIA Corporation (NVDA)", rep.summary)

        lead_llm = _RecordingLLM([
            "Apple Inc. (AAPL) trades at 358.54 USD.",
            "Apple Inc. (AAPL) trades at 358.54 USD, while NVIDIA Corporation (NVDA) trades at 150.49 USD.",
        ])
        orch = TeamLeadOrchestrator(
            _FixedRouter(RoutedQuery("compare_assets", 0.9, "long_term", ["AAPL", "NVDA"])),
            DataAnalystAgent(_RecordingLLM(["Apple Inc. (AAPL) trades at 358.54 USD."]), _MultiTools()),
            NewsResearcherAgent(None, _EmptyNews()),
            lead_llm,
            _MultiTools(),
        )
        res = orch.run("Compare Apple and Nvidia")
        self.assertTrue(res.faithful_synthesis)
        self.assertEqual(len(lead_llm.calls), 2)
        self.assertNotIn("stop", lead_llm.calls[0]["options"])
        self.assertIn("NVDA", lead_llm.calls[1]["prompt"])
        self.assertIn("AAPL", res.final_synthesis)
        self.assertIn("NVDA", res.final_synthesis)

    def test_index_snapshots_skip_fundamentals_and_emit_no_fake_risk_or_raw_dump(self):
        seen_urls = []

        def fake_fetch(url):
            seen_urls.append(url)
            if "fundamentals-timeseries" in url:
                raise AssertionError("Indices must never fetch Yahoo fundamentals-timeseries")
            return {
                "chart": {
                    "result": [{
                        "meta": {
                            "symbol": "^GSPC",
                            "currency": "USD",
                            "shortName": "S&P 500",
                            "exchangeName": "SNP",
                            "instrumentType": "INDEX",
                            "regularMarketPrice": 7297.49,
                            "regularMarketTime": 1795800000,
                            "fiftyTwoWeekHigh": 7844.52,
                            "fiftyTwoWeekLow": 6316.91,
                            "exchangeTimezoneName": "America/New_York",
                        },
                        "timestamp": [1795700000 + i * 86400 for i in range(14)],
                        "indicators": {
                            "quote": [{
                                "close": [7182.7798] * 13 + [7297.49],
                                "high": [7844.52] * 14,
                                "volume": [1_720_000_000] * 14,
                            }]
                        },
                    }]
                }
            }

        tools = MarketTools(fetch=fake_fetch)
        snap = tools.get_snapshot("^GSPC")
        self.assertEqual(snap["fundamentals_status"], "Not applicable for index")
        self.assertFalse(any("fundamentals-timeseries" in u for u in seen_urls))
        cues = risk_cues({"^GSPC": snap}, {})
        self.assertFalse(any("fundamentals" in c["text"].lower() for c in cues))

        rep = DataAnalystAgent(_RecordingLLM([]), tools).run(
            ["^GSPC"], "fundamental_equity", EventBus(), "s3", collect_only=True, collect_reason="market_overview"
        )
        self.assertTrue(rep.faithful_numbers)
        self.assertIn("S&P 500 (^GSPC): 7297.49 USD", rep.summary)
        self.assertNotIn("Fundamentals: not available", rep.summary)
        self.assertNotIn("7182.7798", rep.summary)


class TestAuditFixesRoutingSpeed(unittest.TestCase):
    def test_routing_pass_budgets_for_greetings_concepts_comparisons_and_capabilities(self):
        class SpyBackend:
            def __init__(self):
                self.cls_calls = []
                self.ner_calls = []

            def classify_text(self, text, schema, *, include_confidence=False):
                self.cls_calls.append((text, set(schema.keys())))
                low = text.lower()
                if "capability" in schema:
                    if "watchlist" in low:
                        return {"capability": {"label": "watchlist_overview", "confidence": 0.85}}
                    if "fear" in low:
                        return {"capability": {"label": "crypto_sentiment", "confidence": 0.84}}
                    if "markets" in low:
                        return {"capability": {"label": "market_overview", "confidence": 0.86}}
                    return {"capability": {"label": "general_chat", "confidence": 0.90}}
                if "compare" in low:
                    return {"intent": {"label": "compare_assets", "confidence": 0.72}, "horizon": {"label": "long_term", "confidence": 0.7}}
                if "p/e" in low:
                    return {"intent": {"label": "open_conversation", "confidence": 0.4967}, "horizon": {"label": "long_term", "confidence": 0.6}}
                if "$100" in low:
                    return {"intent": {"label": "open_conversation", "confidence": 0.3572}, "horizon": {"label": "intraday", "confidence": 0.6}}
                if "watchlist" in low:
                    return {"intent": {"label": "watchlist_add", "confidence": 0.67}, "horizon": {"label": "intraday", "confidence": 0.6}}
                return {"intent": {"label": "open_conversation", "confidence": 0.52}, "horizon": {"label": "intraday", "confidence": 0.6}}

            def extract_entities(self, text, entity_types):
                self.ner_calls.append(text)
                if "palantir" in text.lower():
                    return {"entities": {"company": ["Palantir"]}}
                return {"entities": {}}

        spy = SpyBackend()
        router = GlinerQueryRouter(spy, universe={"AAPL", "NVDA", "AMZN"})

        r_hi = router.route("hi")
        self.assertEqual(r_hi.intent, "open_conversation")
        self.assertEqual(len(spy.cls_calls), 0)
        self.assertEqual(len(spy.ner_calls), 0)

        spy.cls_calls.clear(); spy.ner_calls.clear()
        r_pe = router.route("What is a P/E ratio?")
        self.assertEqual(r_pe.intent, "open_conversation")
        self.assertEqual(len(spy.cls_calls), 1)
        self.assertEqual(len(spy.ner_calls), 0)

        spy.cls_calls.clear(); spy.ner_calls.clear()
        r_adv = router.route("I have $100, what should I do?")
        self.assertEqual(r_adv.intent, "open_conversation")
        self.assertEqual(len(spy.cls_calls), 1)
        self.assertEqual(len(spy.ner_calls), 0)

        spy.cls_calls.clear(); spy.ner_calls.clear()
        r_cmp = router.route("Compare Apple and Nvidia")
        self.assertEqual(r_cmp.tickers, ["AAPL", "NVDA"])
        self.assertEqual(len(spy.cls_calls), 1)
        self.assertEqual(len(spy.ner_calls), 0)

        spy.cls_calls.clear(); spy.ner_calls.clear()
        r_cmp_unknown = router.route("Compare Apple and Palantir")
        self.assertEqual(r_cmp_unknown.tickers, ["AAPL"])
        self.assertEqual(r_cmp_unknown.entity_candidates, ("Palantir",))
        self.assertEqual(len(spy.ner_calls), 1)

        spy.cls_calls.clear(); spy.ner_calls.clear()
        r_watch = router.route("How is my watchlist doing?")
        self.assertEqual(r_watch.capability, "watchlist_overview")
        self.assertEqual(len(spy.cls_calls), 2)
        self.assertEqual(len(spy.ner_calls), 0)

        spy.cls_calls.clear(); spy.ner_calls.clear()
        r_fng = router.route("What is the Crypto Fear and Greed index right now?")
        self.assertEqual(r_fng.capability, "crypto_sentiment")
        self.assertEqual(len(spy.cls_calls), 2)
        self.assertEqual(len(spy.ner_calls), 0)

        spy.cls_calls.clear(); spy.ner_calls.clear()
        r_add = router.route("Add Palantir to my watchlist")
        self.assertEqual(r_add.entity_candidates, ("Palantir",))
        self.assertEqual(r_add.capability, "none")
        self.assertEqual(len(spy.ner_calls), 1)


class TestAuditFixesCrashSafety(unittest.TestCase):
    def test_process_slot_lock_blocks_duplicate_and_recovers_stale_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "llama-server.lock"
            first = ProcessSlotLock(lock_path, "test runtime", error_type=RuntimeError).acquire()
            try:
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    ProcessSlotLock(lock_path, "test runtime", error_type=RuntimeError).acquire()
            finally:
                first.release()
            self.assertFalse(lock_path.exists())

            lock_path.write_text(json.dumps({"pid": 99999999, "owner": "dead"}), encoding="utf-8")
            with patch("core.process_guard.pid_is_alive", return_value=False):
                recovered = ProcessSlotLock(lock_path, "test runtime", error_type=RuntimeError).acquire()
                recovered.release()

            lock_path.write_text(json.dumps({"pid": 4242, "owner": "other"}), encoding="utf-8")
            with patch("core.process_guard.pid_is_alive", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "PID 4242"):
                    ProcessSlotLock(lock_path, "test runtime", error_type=RuntimeError).acquire()

    def test_memory_headroom_guard_fails_closed_when_low(self):
        with patch.dict(os.environ, {"COFINANCE_SKIP_MEM_CHECK": "0"}), \
             patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 900, "total_phys_mb": 16384}):
            with self.assertRaisesRegex(RuntimeError, "900 MB free"):
                require_memory_headroom(1400, "local narrator", error_type=RuntimeError)
        with patch.dict(os.environ, {"COFINANCE_SKIP_MEM_CHECK": "0"}), \
             patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 4096, "total_phys_mb": 16384}):
            require_memory_headroom(1400, "local narrator", error_type=RuntimeError)

    def test_startup_warmups_serialize_never_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlap = []
            active = {"count": 0}
            gate = threading.Lock()

            class FakeModels:
                state = {"selected": "lfm3-1.7b-atomic-ad-q4km", "phase": "downloaded"}
                lock = threading.RLock()
                thread = None
                busy = False
                loaded = False
                model_id = "fixture/lfm3"
                narrator_id = "lfm3-1.7b-atomic-ad-q4km"
                pinged = 0

                def is_loaded(self, model=None):
                    return self.loaded

                def qualified(self, backend=None, model=None):
                    return True

                def load(self):
                    with gate:
                        active["count"] += 1
                        overlap.append(active["count"])
                    time.sleep(0.08)
                    self.loaded = True
                    with gate:
                        active["count"] -= 1

                def complete(self, *args, **kwargs):
                    self.pinged += 1
                    return "OK"

                def close(self):
                    pass

            svc = ChatService(REPO, root=Path(tmp), models=FakeModels())

            def fake_local_extractor(_trace, _cancel):
                with gate:
                    active["count"] += 1
                    overlap.append(active["count"])
                time.sleep(0.08)
                with gate:
                    active["count"] -= 1
                return object()

            svc.gliner_path = lambda: Path(tmp)
            svc.local_extractor = fake_local_extractor
            try:
                t1 = svc.warm_router()
                t2 = svc.warm_narrator()
                if t1:
                    t1.join(timeout=5)
                if t2:
                    t2.join(timeout=5)
                self.assertEqual(max(overlap), 1)
                self.assertEqual(svc.models.pinged, 1)
            finally:
                svc.close()

    def test_live_trace_throttles_intermediate_flushes_but_always_persists_terminal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            writes = []
            orig_replace = __import__("core.run_trace", fromlist=["replace_trace"]).replace_trace

            def spy_replace(src, dst):
                writes.append(dst.name)
                return orig_replace(src, dst)

            with patch("core.run_trace.replace_trace", side_effect=spy_replace):
                tr = LiveTrace("Throttle test", directory=Path(tmp), flush_interval_s=5.0)
                for i in range(10):
                    tr.event(f"Step {i}", "tool", {"i": i})
                self.assertEqual(len(writes), 1)
                tr.finish(summary={"answer": "done"})
                self.assertEqual(len(writes), 2)
            saved = json.loads((Path(tmp) / f"{tr.run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "complete")
            self.assertEqual(len(saved["spans"]), 10)

    def test_list_runs_cache_is_thread_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = TraceRecorder("Threaded list", directory=Path(tmp))
            t.finish(summary={"answer": "ok"})
            errors = []

            def worker():
                try:
                    for _ in range(30):
                        rows = list_runs(Path(tmp))
                        if len(rows) != 1:
                            errors.append(f"unexpected len {len(rows)}")
                except Exception as exc:
                    errors.append(repr(exc))

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=5)
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
