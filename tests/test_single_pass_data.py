import unittest

from core.gliner_decide import RoutedQuery
from core.orchestrator import (
    DataAnalystAgent,
    NewsResearcherAgent,
    TeamLeadOrchestrator,
)
from core.run_trace import TraceRecorder, run_traced_team


NVDA_SNAP = {
    "ticker": "NVDA",
    "name": "NVIDIA Corporation",
    "currency": "USD",
    "status": "available",
    "price": 186.64,
    "previous_session_close": 181.6,
    "change_from_previous_session_pct": 2.78,
    "pe_ratio": 42.5,
    "all_time_high": 243.37,
    "drawdown_from_all_time_high_pct": 23.31,
    "fundamentals_status": "Available",
    "fundamentals_as_of": {"pe_ratio": {}},
}


class _Router:
    def __init__(self, intent="price_snapshot", tickers=("NVDA",)):
        self.intent = intent
        self.tickers = list(tickers)

    def route(self, _q):
        return RoutedQuery(self.intent, 0.94, "swing_week", list(self.tickers))


class _Tools:
    def get_snapshot(self, ticker):
        return dict(NVDA_SNAP, ticker=ticker)

    def plot_history(self, ticker):
        return f"chart://{ticker}"


class _EmptyNews:
    def get_gated_signals(self, _ticker, limit=5):
        return []


class _CountingLLM:
    def __init__(self, reply="NVIDIA Corporation trades at 186.64 USD, up 2.78 percent from the previous session close."):
        self.reply = reply
        self.calls = []
        self.model_id = "fixture/lfm3"

    def complete(self, system, prompt, *, max_tokens=256, **options):
        self.calls.append((system, prompt))
        return self.reply


class _PolicyWrapper:
    def __init__(self, backend):
        self.backend = backend
        self.calls = []

    def complete(self, system, prompt, *, max_tokens=256, **options):
        self.calls.append((system, prompt))
        return self.backend.complete(system, prompt, max_tokens=max_tokens, **options)


class TestSinglePassDataNarration(unittest.TestCase):
    def test_price_snapshot_reuses_guarded_data_analyst_and_deduplicates_risk_cues(self):
        llm = _CountingLLM()
        tools = _Tools()
        orch = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm, tools),
            NewsResearcherAgent(llm, _EmptyNews()),
            llm,
            tools,
        )
        res = orch.run("What is NVDA trading at?")
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(res.faithful_synthesis)
        self.assertIn("186.64 USD", res.final_synthesis)
        self.assertIn("Risk the reports support: NVDA trades well below its all-time high.", res.final_synthesis)
        gate = [e for e in res.events if e.event_type == "GATE_CHECK" and e.agent == TeamLeadOrchestrator.NAME][-1]
        self.assertEqual(gate.payload["method"], TeamLeadOrchestrator.DATA_ONLY)
        self.assertEqual(gate.payload["reused_specialist"], DataAnalystAgent.NAME)
        self.assertEqual(gate.payload["unsupported_risks"], [])
        self.assertIn(186.64, gate.payload["allowed_numbers"])

        llm_dup = _CountingLLM(
            "NVIDIA Corporation trades at 186.64 USD, a 23.31 percent drawdown from its all-time high."
        )
        orch_dup = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm_dup, tools),
            NewsResearcherAgent(llm_dup, _EmptyNews()),
            llm_dup,
            tools,
        )
        res_dup = orch_dup.run("Check NVDA")
        self.assertEqual(len(llm_dup.calls), 1)
        self.assertNotIn("Risk the reports support:", res_dup.final_synthesis)

    def test_run_traced_team_preserves_single_pass_across_traced_wrappers(self):
        llm = _CountingLLM()
        tools = _Tools()
        orch = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm, tools),
            NewsResearcherAgent(llm, _EmptyNews()),
            llm,
            tools,
        )
        trace = TraceRecorder("Single pass trace", mode="fixture", directory=None)
        res = run_traced_team(orch, "NVDA price", trace)
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(res.faithful_synthesis)
        model_spans = [s for s in trace.data["spans"] if s["name"] == "Narrate evidence"]
        self.assertEqual(len(model_spans), 1)
        self.assertEqual(model_spans[0]["actor"], DataAnalystAgent.NAME)

    def test_safety_and_query_coverage_guards_fall_through_to_lead(self):
        tools = _Tools()

        llm_invented = _CountingLLM(
            "NVIDIA Corporation trades at 186.64 USD. Main risk is supply-chain disruption."
        )
        orch_invented = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm_invented, tools),
            NewsResearcherAgent(llm_invented, _EmptyNews()),
            llm_invented,
            tools,
        )
        res_inv = orch_invented.run("What is NVDA trading at?")
        self.assertGreaterEqual(len(llm_invented.calls), 2)
        self.assertFalse(res_inv.faithful_synthesis)
        self.assertNotIn("supply-chain", res_inv.final_synthesis)

        class _StubRiskyDataAgent:
            def __init__(self, llm_ref):
                self.llm = llm_ref

            def run(self, tickers, risk_mode, bus, session_id):
                from core.orchestrator import SpecialistReport
                return SpecialistReport(
                    agent_name=DataAnalystAgent.NAME,
                    tickers=list(tickers),
                    raw_facts={"NVDA": dict(NVDA_SNAP)},
                    allowed_numbers=[186.64],
                    chart_refs=[],
                    summary="NVIDIA Corporation trades at 186.64 USD. Main risk is supply-chain disruption.",
                    faithful_numbers=True,
                    validation={},
                )

        llm_stub_risk = _CountingLLM("NVIDIA Corporation trades at 186.64 USD.")
        orch_stub_risk = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            _StubRiskyDataAgent(llm_stub_risk),
            NewsResearcherAgent(llm_stub_risk, _EmptyNews()),
            llm_stub_risk,
            tools,
        )
        res_stub_risk = orch_stub_risk.run("What is NVDA trading at?")
        self.assertEqual(len(llm_stub_risk.calls), 1)
        self.assertNotIn("supply-chain", res_stub_risk.final_synthesis)

        llm_pe = _CountingLLM("NVIDIA Corporation trades at 186.64 USD.")
        orch_pe = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm_pe, tools),
            NewsResearcherAgent(llm_pe, _EmptyNews()),
            llm_pe,
            tools,
        )
        orch_pe.run("What is NVDA's P/E ratio?")
        self.assertEqual(len(llm_pe.calls), 2)

        llm_pe_ok = _CountingLLM("NVIDIA Corporation trades at 186.64 USD with a P/E of 42.5.")
        orch_pe_ok = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm_pe_ok, tools),
            NewsResearcherAgent(llm_pe_ok, _EmptyNews()),
            llm_pe_ok,
            tools,
        )
        orch_pe_ok.run("What is NVDA's P/E ratio?")
        self.assertEqual(len(llm_pe_ok.calls), 1)

        base_llm = _CountingLLM()
        wrapped_lead = _PolicyWrapper(base_llm)
        orch_wrap = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(base_llm, tools),
            NewsResearcherAgent(base_llm, _EmptyNews()),
            wrapped_lead,
            tools,
        )
        orch_wrap.run("NVDA price")
        self.assertEqual(len(wrapped_lead.calls), 1)

        llm_think_flag = _CountingLLM()
        llm_think_flag.is_thinking = True
        orch_think_flag = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm_think_flag, tools),
            NewsResearcherAgent(llm_think_flag, _EmptyNews()),
            llm_think_flag,
            tools,
        )
        orch_think_flag.run("NVDA price")
        self.assertEqual(len(llm_think_flag.calls), 2)

        llm_follow = _CountingLLM()
        orch_follow = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm_follow, tools),
            NewsResearcherAgent(llm_follow, _EmptyNews()),
            llm_follow,
            tools,
        )
        orch_follow.run("Give me a quick recap.", context="NVIDIA Corporation trades at 186.64 USD.")
        self.assertEqual(len(llm_follow.calls), 2)

        llm_doc = _CountingLLM()
        orch_doc = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm_doc, tools),
            NewsResearcherAgent(llm_doc, _EmptyNews()),
            llm_doc,
            tools,
        )
        orch_doc.run(
            "Check fee waiver for NVDA",
            documents=[{"id": "d1", "name": "Notice.pdf", "pages": ["NVDA fee waiver applies to first transfer."]}],
        )
        self.assertEqual(len(llm_doc.calls), 2)

    def test_no_news_thesis_and_comparison_reuse_data_analyst_and_respect_vetoes(self):
        tools = _Tools()
        llm_thesis = _CountingLLM("NVIDIA Corporation trades at 186.64 USD, up 2.78 percent from the previous session close.")
        orch_thesis = TeamLeadOrchestrator(
            _Router("thesis_review", ("NVDA",)),
            DataAnalystAgent(llm_thesis, tools),
            NewsResearcherAgent(llm_thesis, _EmptyNews()),
            llm_thesis,
            tools,
        )
        res = orch_thesis.run("Give me the NVDA thesis")
        self.assertEqual(len(llm_thesis.calls), 1)
        self.assertTrue(res.faithful_synthesis)
        self.assertIsNotNone(res.news_report)
        self.assertIn("No accepted news is available in the local store", res.final_synthesis)
        gate = [e for e in res.events if e.event_type == "GATE_CHECK" and e.agent == TeamLeadOrchestrator.NAME][-1]
        self.assertEqual(gate.payload["method"], TeamLeadOrchestrator.DATA_ONLY)

        class _TwoAssetTools:
            def get_snapshot(self, ticker):
                name = "Microsoft Corporation" if ticker == "MSFT" else "NVIDIA Corporation"
                return dict(NVDA_SNAP, ticker=ticker, name=name)

            def plot_history(self, ticker):
                return f"chart://{ticker}"

        two_tools = _TwoAssetTools()
        llm_cmp_missing = _CountingLLM("NVIDIA Corporation trades at 186.64 USD.")
        orch_cmp_missing = TeamLeadOrchestrator(
            _Router("compare_assets", ("NVDA", "MSFT")),
            DataAnalystAgent(llm_cmp_missing, two_tools),
            NewsResearcherAgent(llm_cmp_missing, _EmptyNews()),
            llm_cmp_missing,
            two_tools,
        )
        orch_cmp_missing.run("Compare NVDA and MSFT")
        self.assertGreaterEqual(len(llm_cmp_missing.calls), 2)

        llm_cmp_ok = _CountingLLM("NVDA trades at 186.64 USD and MSFT trades at 186.64 USD.")
        orch_cmp_ok = TeamLeadOrchestrator(
            _Router("compare_assets", ("NVDA", "MSFT")),
            DataAnalystAgent(llm_cmp_ok, two_tools),
            NewsResearcherAgent(llm_cmp_ok, _EmptyNews()),
            llm_cmp_ok,
            two_tools,
        )
        res_cmp = orch_cmp_ok.run("Compare NVDA and MSFT")
        self.assertEqual(len(llm_cmp_ok.calls), 1)
        self.assertTrue(res_cmp.faithful_synthesis)

    def test_python_owned_answers_and_incomplete_comparisons_make_zero_llm_calls_and_replay_cleanly(self):
        import tempfile
        import time
        import uuid
        from pathlib import Path
        from core.chat_service import ChatService
        from core.guard_telemetry import summarize_guards
        from qualify.outer_loop import replay_directory, replay_exit_code

        class _ExplodingModels:
            def __init__(self):
                self.state = {"selected": "lfm3-1.7b-atomic-ad-q4km", "phase": "ready"}
                self.lock = __import__("threading").RLock()
                self.thread = None
                self.busy = False
                self.model_id = "fixture/lfm3"
                self.narrator_id = "lfm3-1.7b-atomic-ad-q4km"
                self.loads = 0
                self.calls = 0

            def is_loaded(self, model=None):
                return True

            def qualified(self, backend=None, model=None):
                return True

            def think_qualified(self, backend=None, model=None):
                return True

            def thinker_loaded(self, model=None, backend=None):
                return False

            def stop_thinker(self):
                pass

            def close(self):
                pass

            def load(self, *args, **kwargs):
                self.loads += 1

            def complete(self, *args, **kwargs):
                self.calls += 1
                raise AssertionError("Deterministic Python market answers must never invoke LFM3")

        class _PartialMarket:
            def __init__(self, cancel=None):
                self.snapshots = {}
                self.charts = {}
                self.evidence = []

            def get_snapshot(self, ticker):
                if ticker == "MISS":
                    snap = {"ticker": ticker, "status": "unavailable", "error": "timeout",
                            "quote_status": {"state": "unavailable", "failure": "provider_timeout"}}
                else:
                    snap = {
                        **NVDA_SNAP,
                        "ticker": ticker,
                        "market_cap": 3_490_000_000_000,
                        "previous_session_date": "2026-11-26",
                        "price_as_of": "2026-11-27T16:00:00+00:00",
                        "quote_status": {"state": "last_session", "as_of": "2026-11-27T16:00:00+00:00"},
                        "fundamentals_as_of": {"market_cap": {"as_of": "2026-11-26"}},
                    }
                    self.charts[ticker] = {"ticker": ticker, "currency": "USD", "points": [{"time": "2026-11-26", "close": 181.6}]}
                    self.evidence.append({"title": ticker + " quote", "url": "https://finance.yahoo.com/quote/" + ticker, "source": "Yahoo Finance", "as_of": "2026-11-27"})
                self.snapshots[ticker] = snap
                return snap

            def plot_history(self, ticker):
                return f"chart://{ticker}" if ticker in self.charts else ""

        def route_for(query):
            low = query.lower()
            if "compare" in low:
                return RoutedQuery("compare_assets", 0.92, "swing_week", ["NVDA", "MISS"])
            if "buy" in low:
                return RoutedQuery("thesis_review", 0.90, "swing_week", ["NVDA"])
            return RoutedQuery("price_snapshot", 0.93, "intraday", ["NVDA"])

        class _ScriptedRouter:
            def route(self, query):
                return route_for(query)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = _ExplodingModels()
            svc = ChatService(
                Path(__file__).resolve().parents[1],
                root=root,
                models=models,
                market_factory=_PartialMarket,
                router_factory=lambda: _ScriptedRouter(),
                crowd_odds=lambda _tickers: None,
            )
            try:
                sid = svc.store.create_session(uuid.uuid4().hex)["id"]
                cases = [
                    ("Should I buy NVDA now?", "does not establish whether to buy", "complete"),
                    ("What did NVDA close at yesterday?", "NVDA previous-session close: 181.6 USD on 2026-11-26.", "complete"),
                    ("What is NVDA market cap?", "NVDA market capitalization: 3.49 trillion USD", "complete"),
                    ("Compare NVDA and MISS", "The comparison is incomplete: no usable quote for MISS", "degraded"),
                ]
                for query, expected_text, expected_status in cases:
                    job = svc.submit(sid, query, uuid.uuid4().hex, models.state["selected"])
                    for _ in range(100):
                        cur = svc.store.job(job["id"])
                        if cur["status"] not in {"queued", "running"}:
                            break
                        time.sleep(0.02)
                    self.assertEqual(cur["status"], expected_status)
                    msg = svc.store.session(sid)["messages"][-1]
                    self.assertIn(expected_text, msg["content"])
                    trace = __import__("json").loads((root / "traces" / f"{job['id']}.json").read_text(encoding="utf-8"))
                    self.assertEqual(trace["summary"]["flow"], "python_market_answer")
                    self.assertEqual([s for s in trace["spans"] if s["name"] == "Narrate evidence"], [])
                    gt = summarize_guards(trace["spans"])
                    self.assertEqual(gt["wasted_passes"], 0)
                    self.assertEqual(gt["wasted_ms"], 0.0)
                    self.assertTrue(gt["complete"])
                self.assertEqual(models.calls, 0)
                rep = replay_directory(root / "traces")
                self.assertEqual(rep["coverage"]["replayable"], 4)
                self.assertEqual(rep["coverage"]["matched"], 4)
                self.assertEqual(replay_exit_code(rep, fail_on_mismatch=True), 0)
            finally:
                svc.close()


if __name__ == "__main__":
    unittest.main()
