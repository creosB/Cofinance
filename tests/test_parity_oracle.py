import json
import unittest

from core.orchestrator import TeamLeadOrchestrator
from qualify import parity_oracle as po


class TestParityOracle(unittest.TestCase):
    def test_pairwise_matrix_achieves_complete_pair_coverage_deterministically(self):
        cases_a = po.build_pairwise_cases(seed=20261126)
        cases_b = po.build_pairwise_cases(seed=20261126)
        self.assertEqual(cases_a, cases_b)
        keys = list(po.PAIRWISE_FACTORS.keys())
        covered = {
            (keys[i], c["factors"][keys[i]], keys[j], c["factors"][keys[j]])
            for c in cases_a
            for i in range(len(keys))
            for j in range(i + 1, len(keys))
        }
        self.assertEqual(covered, po.required_pairs(po.PAIRWISE_FACTORS))

    def test_self_parity_passes_full_pairwise_matrix(self):
        cases = po.build_pairwise_cases(seed=20261126)
        report = po.run_differential(po.default_orchestrator_factory, po.default_orchestrator_factory, cases)
        self.assertTrue(report["passed"], report["fingerprint_groups"])
        self.assertEqual(report["matched_cases"], len(cases))
        self.assertEqual(report["invariant_failures"], 0)
        self.assertEqual(report["fingerprint_groups"], [])

    def test_recording_llm_turns_nondeterministic_model_into_deterministic_replay(self):
        calls = {"n": 0}

        class NondeterministicLLM:
            model_id = "nondet-test"
            disabled = False

            def complete(self, system, prompt, *, max_tokens=256, **options):
                calls["n"] += 1
                return f"NVDA price is 142.5. Sample {calls['n']}."

        case = po.build_pairwise_cases(seed=20261126)[0]
        case = dict(case, query="Check NVDA price", classify_out={"intent": {"label": "price_snapshot", "confidence": 0.9}, "horizon": {"label": "swing_week", "confidence": 0.8}}, entities_out={"entities": {"ticker": ["NVDA"]}}, documents=None, context="")
        rec = po.RecordingLLM(NondeterministicLLM())
        first_reply = rec.complete("sys", "prompt")
        self.assertIn("142.5", first_reply)
        replay_a = rec.to_replay()
        replay_b = rec.to_replay()
        out_a = replay_a.complete("sys", "prompt")
        out_b = replay_b.complete("sys", "prompt")
        self.assertEqual(out_a, first_reply)
        self.assertEqual(out_a, out_b)
        self.assertEqual(calls["n"], 1)

    def test_stage_localization_and_privacy_preserving_fingerprints(self):
        cases = po.build_pairwise_cases(seed=20261126)[:4]
        secret_text = "SECRET_ACCOUNT_HOLDING_987654"
        base = po.run_case(po.default_orchestrator_factory, cases[0])
        cand = json.loads(json.dumps(base))
        orig = base["synthesis"]["final_synthesis"]
        cand["synthesis"]["final_synthesis"] = orig[:5] + "X" + orig[6:] + " " + secret_text
        diff = po.compare_checkpoints(base, cand)
        self.assertIsNotNone(diff)
        self.assertEqual(diff.stage, "synthesis")
        self.assertEqual(diff.field, "/final_synthesis")
        self.assertEqual(diff.kind, "string_changed")
        self.assertEqual(diff.first_diff, 5)
        serialized = json.dumps(diff.as_dict())
        self.assertNotIn(secret_text, serialized)

    def test_first_diff_stops_at_earliest_pipeline_stage(self):
        case = po.build_pairwise_cases(seed=20261126)[0]
        base = po.run_case(po.default_orchestrator_factory, case)
        cand = json.loads(json.dumps(base))
        cand["route"]["intent"] = "compare_assets"
        cand["synthesis"]["final_synthesis"] = "completely different answer"
        diff = po.compare_checkpoints(base, cand)
        self.assertIsNotNone(diff)
        self.assertEqual(diff.stage, "route")
        self.assertEqual(diff.field, "/intent")

    def test_candidate_regression_is_detected_and_grouped(self):
        cases = po.build_pairwise_cases(seed=20261126)

        def buggy_candidate(case):
            orch, tools, llm = po.default_orchestrator_factory(case)

            class DroppedNewsLead(TeamLeadOrchestrator):
                def _news_only(self, routed, risk_mode, delegated, news_rep, cues, session_id):
                    res = super()._news_only(routed, risk_mode, delegated, news_rep, cues, session_id)
                    return type(res)(**{**res.__dict__, "final_synthesis": res.final_synthesis + " extra"})

            wrapped = DroppedNewsLead(
                orch.router,
                orch.data_agent,
                orch.news_agent,
                orch.lead_llm,
                orch.tools,
                orch.bus,
                document_agent=orch.document_agent,
            )
            return wrapped, tools, llm

        report = po.run_differential(po.default_orchestrator_factory, buggy_candidate, cases)
        self.assertFalse(report["passed"])
        self.assertGreater(report["mismatched_cases"], 0)
        self.assertGreater(len(report["fingerprint_groups"]), 0)
        self.assertTrue(all(g["stage"] == "synthesis" for g in report["fingerprint_groups"]))

    def test_invariants_catch_watchlist_mutation_and_ungrounded_numbers(self):
        case = po.build_pairwise_cases(seed=20261126)[0]
        base = po.run_case(po.default_orchestrator_factory, case)

        mutated_wl = json.loads(json.dumps(base))
        mutated_wl["route"]["watchlist_writes"] = ["NVDA"]
        self.assertIn("watchlist_mutated_during_chat", po.check_invariants(mutated_wl))

        ungrounded = json.loads(json.dumps(base))
        ungrounded["synthesis"]["answer_numbers_grounded"] = False
        self.assertIn("ungrounded_number_in_synthesis", po.check_invariants(ungrounded))

    def test_trace_diff_catches_semantic_span_drift(self):
        case = po.build_pairwise_cases(seed=20261126)[0]
        base = po.run_case(po.default_orchestrator_factory, case)
        self.assertIn(base["trace"]["spans"][0]["status"], {"complete", "fallback"})
        cand = json.loads(json.dumps(base))
        cand["trace"]["spans"][0]["status"] = "error"
        diff = po.compare_checkpoints(base, cand)
        self.assertIsNotNone(diff)
        self.assertEqual(diff.stage, "trace")
        self.assertEqual(diff.field, "/spans[0]/status")


if __name__ == "__main__":
    unittest.main()
