"""Delete one conversation and Clear all (asked by Baris 2026-11-08). A delete must be real: memory copies, tombstone text,
messages, runs and run traces all go; other conversations are untouched; nothing can run or write in a conversation
while it is being deleted."""
import http.client
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from core.local_models import ModelError
from core.memory import HybridMemory
from core.memory_items import ACTIVE, FORGOTTEN
from tests.test_chat import service, wait
from tests.test_memory_items import DeletableSemantic, ServiceCase
from ui.dashboard import create_server


def on_disk(path: Path, needle: bytes) -> bool:
    """Is the text still readable in the database file or its WAL?"""
    return any(p.exists() and needle in p.read_bytes() for p in (path, Path(str(path) + "-wal")))


class TestDeleteConversation(ServiceCase):
    def setUp(self):
        super().setUp()
        self.semantic = DeletableSemantic()
        self.s.memory = HybridMemory(self.s.keyword_memory, self.semantic)

    def trace(self, run_id):
        return self.s.root / "traces" / (run_id + ".json")

    def test_delete_removes_history_memory_and_traces_of_one_conversation_only(self):
        sid = self.session["id"]
        first, _ = self.ask("Review NVDA guidance risk")
        note = self.s.add_note(sid, "Watch Nvidia export rules", uuid.uuid4().hex)
        other = self.s.store.create_session(uuid.uuid4().hex)
        other_turn, _ = self.ask("Review TSLA guidance risk", other)
        self.assertTrue(self.trace(first).exists())
        req = uuid.uuid4().hex
        result = self.s.delete_conversation(sid, req)
        self.assertEqual((result["deleted"], result["messages"], result["runs"], result["traces_deleted"]), (True, 2, 1, 1))
        self.assertEqual((result["memory_forgotten"], result["memory_pending"], result["sweep_pending"]), (2, 0, False))
        self.assertEqual(self.s.delete_conversation(sid, req), result)  # same request id: same answer, even though it is gone
        with self.assertRaises(KeyError):
            self.s.store.session(sid)
        self.assertNotIn(sid, [s["id"] for s in self.s.store.sessions()])
        self.assertFalse(self.trace(first).exists())
        # Memory: tombstones stay (so nothing is re-created) but hold no text; no copy is left anywhere.
        rows = self.s.ledger.items(sid)
        self.assertEqual({(r["state"], r["text"], r["tickers"]) for r in rows}, {(FORGOTTEN, "", "[]")})
        self.assertIn(note["item_id"], {r["item_id"] for r in rows})
        self.assertEqual(self.s.keyword_memory.recall("chat", sid, "guidance risk Nvidia export", limit=5), [])
        self.assertFalse([k for k, v in self.semantic.turns.items() if v[0] == sid])
        self.assertIn(sid, self.semantic.swept)
        # Nothing readable on disk.
        for db in (self.s.store.path, self.s.ledger.path):
            self.assertFalse(on_disk(db, b"Review NVDA guidance risk"), db)
            self.assertFalse(on_disk(db, b"Watch Nvidia export rules"), db)
        # The other conversation is untouched.
        self.assertEqual(len(self.s.store.session(other["id"])["messages"]), 2)
        self.assertEqual(self.s.ledger.get(other_turn)["state"], ACTIVE)
        self.assertTrue(self.trace(other_turn).exists())
        self.assertTrue(self.s.keyword_memory.recall("chat", other["id"], "guidance risk TSLA", limit=5))

    def test_refused_while_a_run_or_its_memory_write_is_in_flight(self):
        sid = self.session["id"]
        first, _ = self.ask("Review NVDA guidance risk")
        self.s.threads[first] = threading.current_thread()  # finished run still writing its memory
        with self.assertRaises(ModelError):
            self.s.delete_conversation(sid, uuid.uuid4().hex)
        with self.assertRaises(ModelError):
            self.s.clear_conversations(uuid.uuid4().hex)
        del self.s.threads[first]
        self.assertEqual(len(self.s.store.session(sid)["messages"]), 2)  # a refused delete changed nothing
        self.assertEqual(self.s.ledger.get(first)["state"], ACTIVE)
        self.assertTrue(self.s.delete_conversation(sid, uuid.uuid4().hex)["deleted"])

    def test_nothing_starts_in_a_conversation_being_deleted(self):
        sid = self.session["id"]
        self.s.deleting.add(sid)
        with self.assertRaises(ModelError):
            self.s.submit(sid, "Review NVDA guidance risk", uuid.uuid4().hex, "facts")
        with self.assertRaises(ModelError):
            self.s.add_note(sid, "Watch Nvidia export rules", uuid.uuid4().hex)
        with self.assertRaises(ModelError):
            self.s.delete_conversation(sid, uuid.uuid4().hex)
        self.s.deleting.discard(sid)

    def test_clear_all_deletes_every_conversation_and_keeps_the_watchlist(self):
        self.ask("Review NVDA guidance risk")
        other = self.s.store.create_session(uuid.uuid4().hex)
        self.ask("Review TSLA guidance risk", other)
        self.s.store.add_watch("NVDA")
        req = uuid.uuid4().hex
        result = self.s.clear_conversations(req)
        self.assertEqual((result["deleted"], result["messages"], result["traces_deleted"], result["memory_pending"]), (2, 4, 2, 0))
        self.assertEqual(self.s.store.session_ids(), [])
        self.assertEqual([w["ticker"] for w in self.s.store.watchlist()], ["NVDA"])
        self.assertEqual(self.s.clear_conversations(req), result)
        self.assertEqual(self.s.clear_conversations(uuid.uuid4().hex)["deleted"], 0)  # nothing left: a no-op, not an error

    def test_a_failed_vector_delete_still_deletes_history_and_stays_pending(self):
        sid = self.session["id"]
        first, _ = self.ask("Review NVDA guidance risk")
        self.semantic.fail_delete = True
        result = self.s.delete_conversation(sid, uuid.uuid4().hex)
        self.assertTrue(result["deleted"])
        self.assertTrue(result["memory_pending"] or result["sweep_pending"])
        self.assertNotEqual(self.s.ledger.get(first)["state"], ACTIVE)  # tombstoned: recall can never return it
        self.assertEqual(self.s.ledger.get(first)["text"], "")


class TestDeleteAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = service(root)
        dist = root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text('<div id="root"></div>')
        self.server = create_server(port=0, dist=dist, trace_dir=root / "traces", corpus_dir=root / "missing", service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        self.token = self.request("/api/state")[1]["csrf"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.service.close()
        self.temp.cleanup()

    def request(self, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        h = {} if body is None else {"Origin": self.origin, "Content-Type": "application/json", "X-Cofinance-CSRF": getattr(self, "token", "")}
        conn.request("GET" if body is None else "POST", path, body=None if body is None else json.dumps(body), headers=h)
        response = conn.getresponse()
        status, value = response.status, json.loads(response.read())
        conn.close()
        return status, value

    def test_delete_and_clear_round_trip(self):
        sid = self.request("/api/sessions", {"request_id": uuid.uuid4().hex})[1]["id"]
        keep = self.request("/api/sessions", {"request_id": uuid.uuid4().hex})[1]["id"]
        status, value = self.request("/api/sessions/delete", {"session_id": sid, "request_id": uuid.uuid4().hex})
        self.assertEqual((status, value["deleted"]), (200, True))
        self.assertEqual([s["id"] for s in self.request("/api/state")[1]["sessions"]], [keep])
        self.assertEqual(self.request("/api/sessions/delete", {"session_id": sid, "request_id": uuid.uuid4().hex})[0], 400)  # gone
        self.assertEqual(self.request("/api/sessions/delete", {"session_id": keep})[0], 400)  # fields are exact
        self.assertEqual(self.request("/api/sessions/delete", {"session_id": "../x", "request_id": uuid.uuid4().hex})[0], 400)
        status, value = self.request("/api/sessions/clear", {"request_id": uuid.uuid4().hex})
        self.assertEqual((status, value["deleted"]), (200, 1))
        self.assertEqual(self.request("/api/state")[1]["sessions"], [])


if __name__ == "__main__":
    unittest.main()
