import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import dev
from core.gliner_decide import RoutedQuery
from core.guard_telemetry import summarize_guard_directory, summarize_guards
from core.orchestrator import DataAnalystAgent, NewsResearcherAgent, TeamLeadOrchestrator
from core.run_trace import TraceRecorder, run_traced_team
from core.usage import record_usage


NVDA_SNAP = {
    "ticker": "NVDA",
    "name": "NVIDIA Corporation",
    "currency": "USD",
    "status": "available",
    "price": 186.64,
    "previous_session_close": 181.6,
    "change_from_previous_session_pct": 2.78,
    "all_time_high": 243.37,
    "drawdown_from_all_time_high_pct": 23.31,
    "fundamentals_status": "Available",
    "fundamentals_as_of": {"pe_ratio": {}},
}


class _Router:
    def __init__(self, intent="thesis_review", tickers=("NVDA",)):
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


class _ScriptedLLM:
    def __init__(self, replies, usages=None):
        self._replies = list(replies)
        self._usages = list(usages) if usages is not None else []
        self.model_id = "fixture/lfm3"

    def complete(self, _system, _prompt, *, max_tokens=256, **_options):
        if self._usages:
            u = self._usages.pop(0)
            if u is not None:
                record_usage(u)
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]


def _step_clock(step=0.25):
    t = [0.0]

    def tick():
        t[0] += step
        return t[0]

    return tick


class TestGuardTelemetry(unittest.TestCase):
    def test_single_pass_data_and_news_bypass_do_not_pollute_narrated_rates(self):
        llm = _ScriptedLLM(
            ["NVIDIA Corporation trades at 186.64 USD, up 2.78 percent from the previous session close."],
            [{"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}],
        )
        tools = _Tools()
        orch = TeamLeadOrchestrator(
            _Router("price_snapshot"),
            DataAnalystAgent(llm, tools),
            NewsResearcherAgent(llm, _EmptyNews()),
            llm,
            tools,
        )
        trace = TraceRecorder("Single pass price", mode="live", clock=_step_clock())
        run_traced_team(orch, "What is NVDA trading at?", trace)
        gt = trace.data["guard_telemetry"]
        self.assertEqual(gt["narrated_checks"], 1)
        self.assertEqual(gt["first_pass_accepted"], 1)
        self.assertEqual(gt["first_pass_acceptance_rate"], 1.0)
        self.assertEqual(gt["wasted_passes"], 0)
        self.assertEqual(gt["bypass_methods"], {"single_pass_data_answer": 1})
        self.assertTrue(gt["complete"])
        self.assertTrue(gt["wasted_tokens"]["complete"])

        orch_news = TeamLeadOrchestrator(
            _Router("news_catalysts"),
            DataAnalystAgent(llm, tools),
            NewsResearcherAgent(llm, _EmptyNews()),
            llm,
            tools,
        )
        trace_news = TraceRecorder("News only", mode="live", clock=_step_clock())
        run_traced_team(orch_news, "NVDA news", trace_news)
        gt_news = trace_news.data["guard_telemetry"]
        self.assertEqual(gt_news["narrated_checks"], 0)
        self.assertIsNone(gt_news["first_pass_acceptance_rate"])
        self.assertEqual(gt_news["bypass_methods"].get("python_news_answer"), 1)

    def test_guard_retry_recovered_and_fallback_track_exact_wasted_passes_and_tokens(self):
        tools = _Tools()
        data_llm = _ScriptedLLM(
            ["NVIDIA Corporation trades at 186.64 USD."],
            [{"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}],
        )
        lead_recover = _ScriptedLLM(
            [
                "NVIDIA Corporation trades at 186.64 USD. Main risk is supply-chain disruption.",
                "NVIDIA Corporation trades at 186.64 USD. Main risk is that NVDA trades well below its all-time high.",
            ],
            [
                {"prompt_tokens": 200, "completion_tokens": 40, "total_tokens": 240},
                {"prompt_tokens": 260, "completion_tokens": 45, "total_tokens": 305},
            ],
        )
        orch_rec = TeamLeadOrchestrator(
            _Router("thesis_review"),
            DataAnalystAgent(data_llm, tools),
            NewsResearcherAgent(data_llm, _EmptyNews()),
            lead_recover,
            tools,
        )
        t_rec = TraceRecorder("Recover retry", mode="live", clock=_step_clock(0.1))
        run_traced_team(orch_rec, "Thesis on NVDA", t_rec)
        gt_rec = t_rec.data["guard_telemetry"]
        self.assertEqual(gt_rec["narrated_checks"], 2)
        self.assertEqual(gt_rec["first_pass_accepted"], 1)
        self.assertEqual(gt_rec["first_pass_acceptance_rate"], 0.5)
        self.assertEqual(gt_rec["guard_retries"], 1)
        self.assertEqual(gt_rec["guard_retry_recovered"], 1)
        self.assertEqual(gt_rec["fallbacks"], 0)
        self.assertEqual(gt_rec["total_narrator_passes"], 3)
        self.assertEqual(gt_rec["wasted_passes"], 1)
        self.assertEqual(gt_rec["failure_reasons"], {"unsupported_risk": 1})
        self.assertEqual(gt_rec["wasted_tokens"], {
            "input": 200,
            "output": 40,
            "total": 240,
            "reported": 1,
            "passes": 1,
            "complete": True,
        })

        lead_fail = _ScriptedLLM(
            [
                "NVIDIA Corporation trades at 186.64 USD after the Blackwell launch.",
                "NVIDIA Corporation trades at 186.64 USD after the Blackwell launch.",
            ],
            [
                {"prompt_tokens": 210, "completion_tokens": 35, "total_tokens": 245},
                {"prompt_tokens": 270, "completion_tokens": 35, "total_tokens": 305},
            ],
        )
        orch_fail = TeamLeadOrchestrator(
            _Router("thesis_review"),
            DataAnalystAgent(_ScriptedLLM(["NVIDIA Corporation trades at 186.64 USD."]), tools),
            NewsResearcherAgent(data_llm, _EmptyNews()),
            lead_fail,
            tools,
        )
        t_fail = TraceRecorder("Fallback retry", mode="live", clock=_step_clock(0.1))
        run_traced_team(orch_fail, "Thesis on NVDA", t_fail)
        gt_fail = t_fail.data["guard_telemetry"]
        self.assertEqual(gt_fail["guard_retries"], 1)
        self.assertEqual(gt_fail["guard_retry_recovered"], 0)
        self.assertEqual(gt_fail["fallbacks"], 1)
        self.assertEqual(gt_fail["wasted_passes"], 2)
        self.assertEqual(gt_fail["failure_reasons"], {"unsupported_terms": 1})
        self.assertEqual(gt_fail["wasted_tokens"]["total"], 550)
        self.assertTrue(gt_fail["wasted_tokens"]["complete"])

    def test_follow_up_retry_tool_only_partial_usage_privacy_and_incompleteness(self):
        spans_used_retry = [
            {"kind": "model", "name": "Narrate evidence", "actor": "Market Team Lead", "duration_ms": 400.0,
             "usage": {"input": 100, "output": 50, "total": 150}},
            {"kind": "model", "name": "Narrate evidence", "actor": "Market Team Lead", "duration_ms": 250.0,
             "usage": {"input": 120, "output": 20, "total": 140}},
            {"kind": "gate", "name": "Synthesis faithfulness", "actor": "Market Team Lead",
             "outputs": {"faithful_synthesis": True, "follow_up_retry": {"used": "retry", "retry_passed_guards": True}}},
        ]
        s1 = summarize_guards(spans_used_retry)
        self.assertEqual((s1["first_pass_accepted"], s1["follow_up_retries"], s1["guard_retries"]), (1, 1, 0))
        self.assertEqual((s1["wasted_passes"], s1["wasted_ms"], s1["wasted_tokens"]["total"]), (1, 400.0, 150))

        spans_used_first = [
            spans_used_retry[0],
            spans_used_retry[1],
            {"kind": "gate", "name": "Synthesis faithfulness", "actor": "Market Team Lead",
             "outputs": {"faithful_synthesis": True, "follow_up_retry": {"used": "first", "retry_passed_guards": False}}},
        ]
        s2 = summarize_guards(spans_used_first)
        self.assertEqual((s2["wasted_passes"], s2["wasted_ms"], s2["wasted_tokens"]["total"]), (1, 250.0, 140))

        tool_only_spans = [
            {"kind": "decision", "name": "Tool-only report", "actor": "Data Analyst", "outputs": {"reason": "narrator_disabled"}},
            {"kind": "gate", "name": "Number faithfulness", "actor": "Data Analyst", "outputs": {"passed": False, "fallback_used": True}},
        ]
        s3 = summarize_guards(tool_only_spans)
        self.assertEqual(s3["narrated_checks"], 0)
        self.assertEqual(s3["bypass_methods"], {"tool_only": 1})

        partial_and_unsafe = [
            {"kind": "model", "name": "Narrate evidence", "actor": "Market Team Lead", "duration_ms": 500.0, "usage": None},
            {"kind": "model", "name": "Narrate evidence", "actor": "Data Analyst", "duration_ms": 300.0, "usage": None},
            {"kind": "gate", "name": "Synthesis faithfulness", "actor": "Market Team Lead",
             "outputs": {
                 "faithful_synthesis": False,
                 "validation_reason": "raw query leak: secret customer text",
                 "guard_retry": {"reason": "raw leak with spaces", "used": "retry"},
             }},
        ]
        s4 = summarize_guards(partial_and_unsafe)
        self.assertEqual(s4["fallbacks"], 1)
        self.assertEqual(s4["guard_retry_recovered"], 0)
        self.assertEqual(s4["failure_reasons"], {"other": 1})
        self.assertNotIn("secret", json.dumps(s4))
        self.assertFalse(s4["wasted_tokens"]["complete"])
        self.assertEqual(s4["unmatched_narrator_passes"], 1)
        self.assertFalse(s4["complete"])

        followup_fail = [
            spans_used_retry[0],
            spans_used_retry[1],
            {"kind": "gate", "name": "Synthesis faithfulness", "actor": "Market Team Lead",
             "outputs": {"faithful_synthesis": False, "validation_reason": "unsupported_terms",
                         "follow_up_retry": {"used": "fallback", "retry_passed_guards": False}}},
        ]
        s5 = summarize_guards(followup_fail)
        self.assertEqual((s5["fallbacks"], s5["wasted_passes"], s5["failure_reasons"]), (1, 2, {"unsupported_terms": 1}))

        zero_model_guard_retry = [
            {"kind": "gate", "name": "Synthesis faithfulness", "actor": "Market Team Lead",
             "outputs": {"faithful_synthesis": True, "guard_retry": {"reason": "unsupported_risk", "used": "retry"}}},
        ]
        s6 = summarize_guards(zero_model_guard_retry)
        self.assertEqual((s6["bypass_methods"], s6["complete"]), ({}, False))

        zero_model_followup_retry = [
            {"kind": "gate", "name": "Synthesis faithfulness", "actor": "Market Team Lead",
             "outputs": {"faithful_synthesis": True, "follow_up_retry": {"used": "retry"}}},
        ]
        s7 = summarize_guards(zero_model_followup_retry)
        self.assertEqual((s7["bypass_methods"], s7["complete"]), ({}, False))

        missing_dur_and_untrusted_actor = [
            {"kind": "model", "name": "Narrate evidence", "actor": "SECRET_USER_PROMPT_ACTOR", "duration_ms": None,
             "usage": {"input": 10, "output": 5, "total": 15}},
            {"kind": "gate", "name": "Number faithfulness", "actor": "SECRET_USER_PROMPT_ACTOR",
             "outputs": {"passed": True, "fallback_used": False}},
        ]
        s8 = summarize_guards(missing_dur_and_untrusted_actor)
        self.assertFalse(s8["wasted_ms_complete"])
        self.assertFalse(s8["complete"])
        self.assertNotIn("SECRET_USER_PROMPT_ACTOR", json.dumps(s8))
        self.assertEqual(s8["by_actor"][0]["actor"], "Other")

    def test_directory_aggregation_mode_filter_coverage_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            (tdir / "bad.json").write_text("{not-json", encoding="utf-8")
            (tdir / "clock1.json").write_text(json.dumps({
                "mode": "live",
                "spans": [{"kind": "run", "name": "Clock 1 collection", "actor": "Python"}],
            }), encoding="utf-8")
            (tdir / "live_chat.json").write_text(json.dumps({
                "mode": "live",
                "status": "complete",
                "storage_errors": 0,
                "dropped_spans": 0,
                "spans": [
                    {"kind": "run", "name": "Interactive research", "actor": "Python"},
                    {"kind": "model", "name": "Narrate evidence", "actor": "Data Analyst", "duration_ms": 600.0,
                     "usage": {"input": 100, "output": 40, "total": 140}},
                    {"kind": "gate", "name": "Number faithfulness", "actor": "Data Analyst",
                     "outputs": {"passed": True, "fallback_used": False, "validation_reason": "numbers_matched"}},
                    {"kind": "gate", "name": "Synthesis faithfulness", "actor": "Market Team Lead",
                     "outputs": {"faithful_synthesis": True, "method": "single_pass_data_answer"}},
                ],
            }), encoding="utf-8")
            (tdir / "fixture_chat.json").write_text(json.dumps({
                "mode": "fixture",
                "status": "degraded",
                "storage_errors": 0,
                "dropped_spans": 0,
                "spans": [
                    {"kind": "run", "name": "Interactive research", "actor": "Python"},
                    {"kind": "model", "name": "Narrate evidence", "actor": "Market Team Lead", "duration_ms": 900.0,
                     "usage": {"input": 150, "output": 50, "total": 200}},
                    {"kind": "gate", "name": "Synthesis faithfulness", "actor": "Market Team Lead",
                     "outputs": {"faithful_synthesis": False, "validation_reason": "claim_provenance"}},
                ],
            }), encoding="utf-8")

            all_rep = summarize_guard_directory(tdir, mode="all")
            cov = all_rep["coverage"]
            self.assertEqual(cov["total_files"], 4)
            self.assertEqual(
                cov["chat_traces"] + cov["non_chat_traces"] + cov["skipped_by_mode"] + cov["unreadable_files"],
                cov["total_files"],
            )
            self.assertEqual((cov["chat_traces"], cov["non_chat_traces"], cov["unreadable_files"]), (2, 1, 1))
            self.assertEqual((all_rep["first_pass_accepted"], all_rep["narrated_checks"]), (1, 2))
            self.assertEqual(all_rep["failure_reasons"], {"claim_provenance": 1})
            self.assertEqual(all_rep["bypass_methods"], {"single_pass_data_answer": 1})
            self.assertFalse(all_rep["wasted_tokens"]["complete"])

            live_rep = summarize_guard_directory(tdir, mode="live")
            self.assertEqual(live_rep["coverage"]["chat_traces"], 1)
            self.assertEqual(live_rep["coverage"]["skipped_by_mode"], 1)
            self.assertEqual(live_rep["first_pass_acceptance_rate"], 1.0)

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = dev.guard_stats_cmd(traces=str(tdir), mode="live", as_json=True)
            self.assertEqual(rc, 0)
            parsed = json.loads(buf.getvalue())
            self.assertEqual(parsed, live_rep)


    def test_incomplete_disk_traces_are_counted_but_do_not_inflate_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = {
                "mode": "live", "status": "complete", "dropped_spans": 0, "storage_errors": 0,
                "spans": [
                    {"kind": "model", "name": "Narrate evidence", "actor": "Data Analyst", "duration_ms": 500.0},
                    {"kind": "gate", "name": "Number faithfulness", "actor": "Data Analyst",
                     "outputs": {"passed": True, "fallback_used": False, "validation_reason": "numbers_matched"}},
                ],
            }
            specimens = {
                "eligible": {"status": "degraded"}, "storage": {"storage_errors": 1},
                "omitted": {"dropped_spans": 1}, "stale": {"status": "running"},
                "unverified": {"storage_errors": "0"},
            }
            for name, changes in specimens.items():
                (root / (name + ".json")).write_text(json.dumps({**trace, **changes}), encoding="utf-8")
            report = summarize_guard_directory(root, mode="live")
            coverage = report["coverage"]
            self.assertEqual((coverage["chat_traces"], coverage["included_traces"]), (5, 1))
            self.assertEqual(coverage["incomplete_traces"], 4)
            self.assertEqual(coverage["excluded_trace_integrity"], {
                "trace_storage_errors": 1, "trace_dropped_spans": 1, "trace_not_terminal": 1,
                "trace_unverified_counters": 1,
            })
            self.assertEqual((report["first_pass_accepted"], report["narrated_checks"]), (1, 1),
                             "A trace with unverified storage must not add a successful guard check")
            self.assertFalse(report["wasted_ms_complete"])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(dev.guard_stats_cmd(str(root), mode="live"), 0)
            self.assertIn("1/5 chat traces included", output.getvalue())
            self.assertIn("trace_storage_errors=1", output.getvalue())

    def test_incomplete_span_summary_is_skipped_and_strict_cli_fails_on_unusable_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            incomplete_duration = {
                "mode": "live", "status": "complete", "dropped_spans": 0, "storage_errors": 0,
                "spans": [
                    {"kind": "model", "name": "Narrate evidence", "actor": "Data Analyst", "duration_ms": None},
                    {"kind": "gate", "name": "Number faithfulness", "actor": "Data Analyst",
                     "outputs": {"passed": True, "fallback_used": False, "validation_reason": "numbers_matched"}},
                ],
            }
            (root / "incomplete.json").write_text(json.dumps(incomplete_duration), encoding="utf-8")
            rep = summarize_guard_directory(root, mode="live")
            self.assertEqual(rep["coverage"]["included_traces"], 0)
            self.assertEqual(rep["coverage"]["incomplete_traces"], 1)
            self.assertEqual(rep["narrated_checks"], 0)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(dev.guard_stats_cmd(str(root), mode="live", strict=True), 1)
            self.assertIn("not benchmarkable", out.getvalue())

            clean = {
                "mode": "live", "status": "complete", "dropped_spans": 0, "storage_errors": 0,
                "spans": [
                    {"kind": "model", "name": "Narrate evidence", "actor": "Data Analyst", "duration_ms": 250.0},
                    {"kind": "gate", "name": "Number faithfulness", "actor": "Data Analyst",
                     "outputs": {"passed": True, "fallback_used": False, "validation_reason": "numbers_matched"}},
                ],
            }
            (root / "incomplete.json").unlink()
            (root / "clean.json").write_text(json.dumps(clean), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dev.guard_stats_cmd(str(root), mode="live", strict=True), 0)


if __name__ == "__main__":
    unittest.main()
