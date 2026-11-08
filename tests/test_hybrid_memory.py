"""HybridMemory fuses keyword and semantic recall without loosening any MemoryPort rule."""
import tempfile
import unittest
from pathlib import Path

from core.memory import HybridMemory, LocalKeywordMemory, MemoryHit


class FakeSemantic:
    """Stands in for HyMemoryLite: stores turns, returns a scripted ranking of run ids."""
    backend = "fake semantic"

    def __init__(self, ranking=None, fail=False):
        self.turns, self.ranking, self.fail, self.closed = {}, ranking or [], fail, False

    def count(self, namespace, session_id):
        return sum(1 for t in self.turns.values() if t[0] == session_id)

    def remember_turn(self, namespace, session_id, question, answer, *, run_id, tickers=()):
        self.turns.setdefault(run_id, (session_id, question, answer, list(tickers)))
        return "sem-" + run_id

    def recall(self, namespace, session_id, query, *, limit=2, exclude_run=None):
        if self.fail:
            raise RuntimeError("embedder down")
        out = []
        for run_id in self.ranking:
            t = self.turns.get(run_id)
            if t and t[0] == session_id and run_id != exclude_run:
                out.append(MemoryHit("sem-" + run_id, session_id, t[1], t[2], run_id, 0.0, 0.5, t[3]))
        return out[:limit]

    def backfill(self, namespace, session):
        return 0

    def close(self):
        self.closed = True


class TestHybridMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.keyword = LocalKeywordMemory(Path(self.tmp.name) / "kw.db")

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self, memory):
        memory.remember_turn("chat", "s1", "Nvidia China exports?", "Nvidia chips are stuck in Hong Kong customs.", run_id="r1", tickers=["NVDA"])
        memory.remember_turn("chat", "s1", "Apple foldable?", "Apple opened pre-orders for the foldable iPhone.", run_id="r2", tickers=["AAPL"])
        memory.remember_turn("chat", "s1", "Nvidia risk?", "Main risk is missing fundamentals.", run_id="r3", tickers=["NVDA"])

    def test_turn_found_by_both_backends_ranks_first(self):
        # Semantic puts r2 first, keyword puts r1 first; r1 is second on semantic, so agreement wins.
        hybrid = HybridMemory(self.keyword, FakeSemantic(ranking=["r2", "r1"]))
        self.fill(hybrid)
        hits = hybrid.recall("chat", "s1", "Nvidia chips customs", limit=2)
        self.assertEqual(hits[0].run_id, "r1")
        self.assertEqual(sorted(hits[0].sources), ["keyword", "semantic"])

    def test_mirrored_ranks_tie_break_on_semantic_rank(self):
        hybrid = HybridMemory(self.keyword, FakeSemantic(ranking=["r3", "r1"]))
        self.fill(hybrid)
        hits = hybrid.recall("chat", "s1", "Nvidia chips customs", limit=2)  # keyword: r1, r3
        self.assertEqual([h.run_id for h in hits], ["r3", "r1"])
        self.assertEqual(hits[0].score, hits[1].score)

    def test_semantic_only_match_is_kept(self):
        hybrid = HybridMemory(self.keyword, FakeSemantic(ranking=["r2"]))
        self.fill(hybrid)
        hits = hybrid.recall("chat", "s1", "bendable handset", limit=2)  # no keyword overlap
        self.assertEqual([h.run_id for h in hits], ["r2"])
        self.assertEqual(hits[0].sources, ["semantic"])

    def test_writes_both_sides_and_stays_idempotent(self):
        semantic = FakeSemantic()
        hybrid = HybridMemory(self.keyword, semantic)
        self.fill(hybrid)
        self.fill(hybrid)
        self.assertEqual(self.keyword.count("chat", "s1"), 3)
        self.assertEqual(semantic.count("chat", "s1"), 3)
        self.assertEqual(hybrid.count("chat", "s1"), 3)

    def test_rules_still_hold(self):
        hybrid = HybridMemory(self.keyword, FakeSemantic(ranking=["r1", "r2", "r3"]))
        self.fill(hybrid)
        with self.assertRaises(ValueError):
            hybrid.remember_turn("news", "s1", "q", "a", run_id="x")
        with self.assertRaises(ValueError):
            hybrid.recall("news", "s1", "Nvidia")
        self.assertEqual(hybrid.recall("chat", "other", "Nvidia chips"), [])
        self.assertNotIn("r3", [h.run_id for h in hybrid.recall("chat", "s1", "Nvidia risk", limit=5, exclude_run="r3")])
        self.assertLessEqual(len(hybrid.recall("chat", "s1", "Nvidia Apple", limit=99)), 5)

    def test_semantic_failure_propagates_for_visible_fallback(self):
        hybrid = HybridMemory(self.keyword, FakeSemantic(fail=True))
        self.fill(hybrid)
        with self.assertRaises(RuntimeError):
            hybrid.recall("chat", "s1", "Nvidia chips")
        self.assertTrue(self.keyword.recall("chat", "s1", "Nvidia chips"))  # keyword already has the data

    def test_close_stops_semantic_backend(self):
        semantic = FakeSemantic()
        HybridMemory(self.keyword, semantic).close()
        self.assertTrue(semantic.closed)


class TestShadowKeyword(unittest.TestCase):
    def test_hybrid_recall_records_what_keyword_alone_would_return(self):
        import json, uuid
        from tests.test_chat import service, wait
        with tempfile.TemporaryDirectory() as root:
            s = service(Path(root))
            try:
                semantic = FakeSemantic()
                s.memory = HybridMemory(s.keyword_memory, semantic)
                session = s.store.create_session(uuid.uuid4().hex)
                def ask(q):
                    return wait(s, s.submit(session["id"], q, uuid.uuid4().hex, "facts"))
                first = ask("Review NVDA guidance risk")
                ask("What about its price?")
                semantic.ranking = [first["id"]]
                third = ask("Back to the guidance risk for NVDA")
                trace = json.loads((Path(root) / "traces" / (third["id"] + ".json")).read_text(encoding="utf-8"))
                out = [x for x in trace["spans"] if x["name"] == "Recall conversation memory"][0]["outputs"]
                shadow = out["shadow_keyword"]
                self.assertIn(first["id"], [h["run_id"] for h in shadow["keyword_only"]])
                self.assertTrue(shadow["agrees"])
                # the shadow never becomes context: payload recall is the hybrid result only
                message = s.store.session(session["id"])["messages"][-1]
                self.assertNotIn("shadow_keyword", message["payload"]["memory"])
            finally:
                s.close()

    def test_keyword_only_backend_records_no_shadow(self):
        import json, uuid
        from tests.test_chat import service, wait
        with tempfile.TemporaryDirectory() as root:
            s = service(Path(root))
            try:
                session = s.store.create_session(uuid.uuid4().hex)
                job = wait(s, s.submit(session["id"], "Review NVDA guidance risk", uuid.uuid4().hex, "facts"))
                trace = json.loads((Path(root) / "traces" / (job["id"] + ".json")).read_text(encoding="utf-8"))
                out = [x for x in trace["spans"] if x["name"] == "Recall conversation memory"][0]["outputs"]
                self.assertNotIn("shadow_keyword", out)
            finally:
                s.close()


class TestMemoryDefault(unittest.TestCase):
    """Hybrid is the default when Hy-Memory is installed; keyword otherwise or when asked for."""
    def build(self, root, env=None, installed=True):
        import os
        from unittest.mock import patch
        from core.hymemory_port import HyMemoryLite
        from tests.test_chat import service
        environ = {k: v for k, v in os.environ.items() if k != "COFINANCE_MEMORY"}
        if env:
            environ["COFINANCE_MEMORY"] = env
        with patch.dict(os.environ, environ, clear=True), patch.object(HyMemoryLite, "available", return_value=installed):
            return service(Path(root))

    def test_default_is_hybrid_when_installed(self):
        with tempfile.TemporaryDirectory() as root:
            s = self.build(root)
            try:
                self.assertIsInstance(s.memory, HybridMemory)
            finally:
                s.close()

    def test_missing_install_or_override_uses_keyword(self):
        for env, installed in ((None, False), ("keyword", True)):
            with tempfile.TemporaryDirectory() as root:
                s = self.build(root, env, installed)
                try:
                    self.assertIs(s.memory, s.keyword_memory)
                finally:
                    s.close()


if __name__ == "__main__":
    unittest.main()
