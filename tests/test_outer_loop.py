import hashlib
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.orchestrator import TeamLeadOrchestrator
from qualify import outer_loop as ol


def _sample_trace(
    *,
    query: str = "What is NVDA price?",
    price: float = 135.0,
    data_reply: str = "NVDA last price is 135.0 USD.",
    lead_reply: str = "NVDA last price is 135.0 USD. The reports do not identify a specific risk.",
) -> dict:
    return {
        "id": "trace-demo",
        "mode": "live",
        "status": "complete",
        "spans": [
            {
                "name": "Interactive research",
                "kind": "run",
                "status": "complete",
                "inputs": {"query": query, "conversation_context": ""},
                "outputs": {"agents": ["Data Analyst"], "answer": lead_reply},
            },
            {
                "name": "Route request",
                "kind": "route",
                "status": "complete",
                "inputs": {"query": query},
                "outputs": {
                    "intent": "price_snapshot",
                    "intent_confidence": 0.94,
                    "horizon": "intraday",
                    "tickers": ["NVDA"],
                },
            },
            {
                "name": "Data Analyst",
                "kind": "agent",
                "status": "complete",
                "outputs": {
                    "agent_name": "Data Analyst",
                    "tickers": ["NVDA"],
                    "raw_facts": {
                        "NVDA": {
                            "ticker": "NVDA",
                            "name": "NVIDIA Corporation",
                            "currency": "USD",
                            "price": price,
                            "high_52_week": 150.0,
                            "all_time_high": 190.0,
                            "source": "Yahoo Finance",
                        }
                    },
                    "allowed_numbers": [price, 150.0, 190.0],
                    "summary": data_reply,
                    "faithful_numbers": True,
                    "validation": {},
                },
            },
            {"name": "Narrate evidence", "kind": "model", "status": "complete", "outputs": data_reply},
            {"name": "Narrate evidence", "kind": "model", "status": "complete", "outputs": lead_reply},
        ],
    }


class TestOuterLoop(unittest.TestCase):
    def test_coverage_classification_privacy_and_read_only_source(self):
        secret_query = "SECRET_PORTFOLIO_QUERY_774411"
        secret_reply = "NVDA last price is 135.0 USD SECRET_REPLY_MARKER_9911"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "secret_filename_8822.json").write_text(
                json.dumps(_sample_trace(query=secret_query, lead_reply=secret_reply)),
                encoding="utf-8",
            )
            (root / "bad_json.json").write_text("{not valid json", encoding="utf-8")
            (root / "not_obj.json").write_text("[1, 2, 3]", encoding="utf-8")
            (root / "no_spans.json").write_text('{"id":"x","spans":[]}', encoding="utf-8")
            (root / "clock1_news.json").write_text(
                json.dumps({"id": "n1", "spans": [{"name": "Fetch feeds", "kind": "tool", "outputs": {}}]}),
                encoding="utf-8",
            )
            no_transcript = _sample_trace()
            no_transcript["spans"] = [s for s in no_transcript["spans"] if s["name"] != "Narrate evidence"]
            (root / "missing_narr.json").write_text(json.dumps(no_transcript), encoding="utf-8")

            extracted, reason = ol.extract_replay_case(root / "secret_filename_8822.json")
            self.assertIsNone(reason)
            self.assertTrue(extracted.case_id.startswith("trace-"))
            self.assertNotIn("secret_filename_8822", extracted.case_id)

            before_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.json")}
            report = ol.replay_directory(root)
            after_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.json")}
            self.assertEqual(before_hashes, after_hashes)

            cov = report["coverage"]
            self.assertEqual(cov["total_traces"], 6)
            self.assertEqual(cov["replayable"], 1)
            self.assertEqual(cov["matched"], 1)
            self.assertEqual(cov["mismatched"], 0)
            self.assertEqual(cov["total_traces"], cov["replayable"] + sum(cov["incomplete_by_reason"].values()))
            self.assertEqual(cov["replayable"], cov["matched"] + cov["mismatched"])
            self.assertEqual(
                cov["incomplete_by_reason"],
                {
                    "invalid_json": 1,
                    "missing_chat_query": 1,
                    "missing_model_transcript": 1,
                    "missing_spans": 1,
                    "trace_not_object": 1,
                },
            )
            serialized = json.dumps(report)
            self.assertNotIn(secret_query, serialized)
            self.assertNotIn("SECRET_REPLY_MARKER_9911", serialized)
            self.assertNotIn("secret_filename_8822", serialized)

    def test_grouped_diff_registry_human_approval_and_preserved_mismatch_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            traces = root / "traces"
            traces.mkdir()
            (traces / "t1.json").write_text(json.dumps(_sample_trace(price=135.0)), encoding="utf-8")
            (traces / "t2.json").write_text(json.dumps(_sample_trace(price=135.0, query="Check NVDA now")), encoding="utf-8")

            class MutatingLead(TeamLeadOrchestrator):
                def run(self, *args, **kwargs):
                    res = super().run(*args, **kwargs)
                    return type(res)(**{**res.__dict__, "final_synthesis": res.final_synthesis + " Extra."})

            def cand_factory(case):
                orch, tools, llm = ol.default_trace_factory(case)
                mut = MutatingLead(
                    orch.router,
                    orch.data_agent,
                    orch.news_agent,
                    orch.lead_llm,
                    orch.tools,
                    bus=orch.bus,
                    document_agent=orch.document_agent,
                )
                return mut, tools, llm

            first = ol.replay_directory(traces, candidate_factory=cand_factory)
            self.assertEqual(first["coverage"]["mismatched"], 2)
            self.assertEqual(first["coverage"]["unapproved_mismatches"], 2)
            self.assertEqual(len(first["groups"]), 1)
            fp_id = first["groups"][0]["fingerprint"]
            self.assertEqual(first["groups"][0]["count"], 2)
            self.assertEqual(first["groups"][0]["stage"], "synthesis")
            self.assertEqual(first["groups"][0]["registry_status"], "unclassified")

            reg_path = root / "registry.json"
            unapproved_doc = json.dumps({
                "schema_version": 1,
                "fingerprints": {fp_id: {"status": "waived", "human_approved": False}},
            })
            reg_path.write_text(unapproved_doc, encoding="utf-8")
            unapproved = ol.replay_directory(traces, candidate_factory=cand_factory, registry_in=reg_path)
            self.assertEqual(unapproved["coverage"]["mismatched"], 2)
            self.assertEqual(unapproved["coverage"]["unapproved_mismatches"], 2)
            self.assertFalse(unapproved["groups"][0]["effective_waived"])
            self.assertEqual(reg_path.read_text(encoding="utf-8"), unapproved_doc)

            approved_doc = json.dumps({
                "schema_version": 1,
                "fingerprints": {fp_id: {"status": "waived", "human_approved": True}},
            })
            reg_path.write_text(approved_doc, encoding="utf-8")
            approved = ol.replay_directory(traces, candidate_factory=cand_factory, registry_in=reg_path)
            self.assertEqual(approved["coverage"]["mismatched"], 2)
            self.assertEqual(approved["coverage"]["unapproved_mismatches"], 0)
            self.assertTrue(approved["groups"][0]["effective_waived"])
            self.assertEqual(reg_path.read_text(encoding="utf-8"), approved_doc)

            bad_reg = root / "bad_registry.json"
            bad_reg.write_text(
                json.dumps({"schema_version": 1, "fingerprints": {fp_id: {"status": "auto_ignore", "human_approved": True}}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                ol.replay_directory(traces, candidate_factory=cand_factory, registry_in=bad_reg)

    def test_network_fence_strict_replay_and_safety_invariant_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "t1.json").write_text(json.dumps(_sample_trace()), encoding="utf-8")

            def net_factory(case):
                socket.getaddrinfo("example.com", 443)
                return ol.default_trace_factory(case)

            net_rep = ol.replay_directory(root, candidate_factory=net_factory)
            self.assertEqual(net_rep["coverage"]["mismatched"], 1)
            self.assertEqual(net_rep["groups"][0]["kind"], "forbidden_network")

            class ExtraCallLead(TeamLeadOrchestrator):
                def run(self, *args, **kwargs):
                    res = super().run(*args, **kwargs)
                    self.lead_llm.complete("sys", "extra unrecorded call")
                    return res

            def extra_call_factory(case):
                orch, tools, llm = ol.default_trace_factory(case)
                wrapped = ExtraCallLead(
                    orch.router,
                    orch.data_agent,
                    orch.news_agent,
                    orch.lead_llm,
                    orch.tools,
                    bus=orch.bus,
                    document_agent=orch.document_agent,
                )
                return wrapped, tools, llm

            extra_rep = ol.replay_directory(root, candidate_factory=extra_call_factory)
            self.assertEqual(extra_rep["coverage"]["mismatched"], 1)
            self.assertEqual(extra_rep["groups"][0]["kind"], "RuntimeError")

            class WatchlistMutator(TeamLeadOrchestrator):
                def run(self, *args, **kwargs):
                    self.tools.add_to_watchlist("NVDA")
                    return super().run(*args, **kwargs)

            def inv_factory(case):
                orch, tools, llm = ol.default_trace_factory(case)
                wrapped = WatchlistMutator(
                    orch.router,
                    orch.data_agent,
                    orch.news_agent,
                    orch.lead_llm,
                    orch.tools,
                    bus=orch.bus,
                    document_agent=orch.document_agent,
                )
                return wrapped, tools, llm

            inv_rep = ol.replay_directory(root, candidate_factory=inv_factory)
            self.assertEqual(inv_rep["coverage"]["invariant_failures"], 1)
            self.assertEqual(inv_rep["groups"][0]["kind"], "safety_invariant_failed")


if __name__ == "__main__":
    unittest.main()
