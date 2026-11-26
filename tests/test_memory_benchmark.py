import json
import tempfile
import unittest
from pathlib import Path

from core.memory import MemoryHit
from qualify import memory_benchmark as mb


class _FakeSemantic:
    backend = "fake_semantic"

    def __init__(self) -> None:
        self.rows: list[MemoryHit] = []

    def count(self, namespace: str, session_id: str) -> int:
        return sum(1 for r in self.rows if r.session_id == session_id)

    def remember_turn(self, namespace, session_id, question, answer, *, run_id, tickers=()):
        self.rows.append(MemoryHit(run_id, session_id, question, answer, run_id, 1.0, 0.9, list(tickers)))
        return run_id

    def delete(self, namespace, session_id, *, run_id=None):
        self.rows = [r for r in self.rows if not (r.session_id == session_id and r.run_id == run_id)]
        return 1

    def recall(self, namespace, session_id, query, *, limit=2, exclude_run=None):
        q = query.lower()
        hits = []
        for r in self.rows:
            if r.session_id != session_id:
                continue
            if "residual liquidity" in q and r.run_id == "s1_r02":
                hits.append(r)
            elif "factory hurdle" in q and r.run_id == "s1_r04":
                hits.append(r)
            elif "gross profit margin" in q and r.run_id == "s1_r01":
                hits.append(r)
        return hits[:limit]


class TestMemoryBenchmark(unittest.TestCase):
    def test_cases_file_has_exact_30_followups_and_no_em_dashes(self):
        cases_path = Path("qualify/memory_benchmark_cases.json")
        raw = cases_path.read_text(encoding="utf-8")
        self.assertNotIn("\u2014", raw)
        self.assertNotIn("\u2013", raw)
        suite = mb.load_suite(cases_path)
        self.assertEqual(len(suite["sessions"]), 6)
        self.assertEqual(len(suite["followups"]), 30)
        cats = [f["category"] for f in suite["followups"]]
        self.assertEqual(cats.count("lexical"), 10)
        self.assertEqual(cats.count("paraphrase"), 15)
        self.assertEqual(cats.count("negative"), 5)
        self.assertEqual(len(suite["forget_checks"]), 2)

    def test_freeze_one_look_lock_scoring_and_forget_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run1"
            plan = mb.freeze(run_dir, Path("qualify/memory_benchmark_cases.json"))
            self.assertEqual(mb.verify_protocol(run_dir)["protocol_sha256"], plan["protocol_sha256"])

            with self.assertRaises(FileExistsError):
                mb.freeze(run_dir, Path("qualify/memory_benchmark_cases.json"))

            report = mb.run_frozen(run_dir, semantic_factory=lambda _d: _FakeSemantic())
            self.assertEqual(report["arms"]["keyword_fts5"]["n_total"], 30)
            self.assertEqual(report["arms"]["keyword_fts5"]["cross_session_leaks"], 0)
            self.assertEqual(report["arms"]["hybrid_fts5_hymemory"]["cross_session_leaks"], 0)
            self.assertGreater(
                report["arms"]["hybrid_fts5_hymemory"]["paraphrase"]["recall_at_2"],
                report["arms"]["keyword_fts5"]["paraphrase"]["recall_at_2"],
            )
            self.assertEqual(report["forget_checks"]["total"], 2)
            self.assertEqual(report["forget_checks"]["leaks"], 0)

            with self.assertRaises(FileExistsError):
                mb.run_frozen(run_dir, semantic_factory=lambda _d: _FakeSemantic())

            proto_file = run_dir / "protocol.json"
            bad = json.loads(proto_file.read_text(encoding="utf-8"))
            bad["recall_limit"] = 99
            proto_file.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Protocol SHA-256"):
                mb.verify_protocol(run_dir)

            bad_cases = Path(tmp) / "bad_cases.json"
            bad_cases.write_text(
                json.dumps({"format": "cofinance-memory-benchmark-cases/1", "sessions": [], "followups": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Expected 30 followups"):
                mb.load_suite(bad_cases)


if __name__ == "__main__":
    unittest.main()
