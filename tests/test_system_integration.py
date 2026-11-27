import http.client
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from core.chat_service import CHAT_FLOWS, ChatService
from core.documents import ExtractedPages
from core.gliner_decide import RoutedQuery
from core.local_models import Cancelled
from core.memory import HybridMemory
from core.run_trace import TraceRecorder
from core.usage import record_usage
from tests.test_chat import FakeMarket, wait
from tests.test_deep_think import FakeModels
from tests.test_memory_items import DeletableSemantic
from ui.dashboard import create_server
from ui.workbench import list_runs, read_trace


def _raw_bytes_contain(path: Path, needle: bytes) -> bool:
    for p in (path, Path(str(path) + "-wal")):
        if p.exists() and needle.lower() in p.read_bytes().lower():
            return True
    return False


class _FlowRouter:
    def __init__(self, route_fn):
        self._route_fn = route_fn

    def route(self, query):
        return self._route_fn(query)


class _FlowModels(FakeModels):
    def __init__(self, root, repo=None):
        super().__init__(root, repo or Path(__file__).resolve().parents[1])
        self.state["selected"] = "lfm3-1.7b-atomic-ad-q4km"
        self.state["can_select"] = True
        self.cancel_event = None
        self.fail_next = False

    def qualified(self, model=None):
        return True

    def complete(self, system, prompt, *, max_tokens=256, stop=None, think=False, messages=None, temperature=0.0, allow_truncated=False):
        if self.cancel_event is not None:
            self.cancel_event.set()
            raise Cancelled("Stopped during generation")
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("Simulated runtime failure")
        record_usage({"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60})
        if messages is not None:
            return "With $100 you can start by deciding your goal and timeline, or name a ticker like NVDA for live data."
        if "[Data Analyst]" in system:
            return "NVIDIA Corporation (NVDA) last price is 138.5 USD."
        if "Document Reader" in system or "attached" in prompt.lower() or "Circular_47.pdf" in prompt:
            return "Circular_47.pdf reports revenue grew 18 percent in Q3."
        return "NVIDIA Corporation (NVDA) last price is 138.5 USD."


class _FlowMarket(FakeMarket):
    def get_snapshot(self, ticker):
        return {
            "ticker": ticker,
            "name": "NVIDIA Corporation" if ticker == "NVDA" else ticker,
            "currency": "USD",
            "status": "available",
            "price": 138.5,
            "previous_session_close": 135.0,
            "change_from_previous_session_pct": 2.59,
            "source": "Yahoo Finance",
        }


def _seed_news(db_path: Path, ticker: str = "NVDA"):
    import time as _time
    from core.news_store import NewsStore
    from core.signals import Action, Item, Judgment, Outcome

    now = _time.time() - 3600.0
    store = NewsStore(str(db_path), namespace="news")
    try:
        item = Item(
            ticker=ticker,
            source="nvidia_newsroom",
            url="https://nvidianews.nvidia.com/item",
            title="NVIDIA announces Blackwell platform update",
            text="NVIDIA announced the Blackwell platform update.",
            published_at=now,
        )
        outcome = Outcome(item, Action.DIGEST, "material_not_urgent", Judgment(0.9, 0.4, "bullish", 0.8))
        with store.transaction():
            store.record([outcome], now=now)
    finally:
        store.close()


class TestSystemIntegrationAndFlows(unittest.TestCase):
    def _make_service(self, root: Path, route_fn):
        models = _FlowModels(root / "local_models")
        svc = ChatService(
            root,
            root=root,
            models=models,
            router_factory=lambda: _FlowRouter(route_fn),
            market_factory=lambda cancel=None: _FlowMarket(),
            crowd_odds=lambda tickers: None,
        )
        return svc, models

    def test_all_nine_chat_flows_emit_valid_traces_and_pass_frontend_run_map_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_news(root / "news.db", "NVDA")

            def route_fn(q):
                low = q.lower()
                if "100" in low or "capital" in low:
                    return RoutedQuery("open_conversation", 0.88, "swing_week", [])
                if "clarify" in low:
                    return RoutedQuery("open_conversation", 0.82, "swing_week", [])
                if "news" in low:
                    return RoutedQuery("news_catalysts", 0.85, "swing_week", ["NVDA"])
                if "track" in low:
                    return RoutedQuery("watchlist_add", 0.92, "swing_week", ["NVDA"])
                if "thesis" in low:
                    return RoutedQuery("thesis_review", 0.90, "long_term", ["NVDA"])
                if "circular" in low:
                    return RoutedQuery("thesis_review", 0.80, "swing_week", [])
                return RoutedQuery("price_snapshot", 0.91, "intraday", ["NVDA"])

            svc, models = self._make_service(root, route_fn)
            traces_by_flow = {}
            try:
                s_open = svc.store.create_session(uuid.uuid4().hex)
                j = svc.submit(s_open["id"], "I've $100. Now make a suggestion", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                self.assertEqual(done["status"], "complete")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "open_conversation")
                self.assertEqual(tr["summary"]["origin"], "chat")
                self.assertEqual(tr["summary"]["conversation_id"], s_open["id"])
                self.assertEqual(tr["usage"]["total"], 60)
                traces_by_flow["open_conversation"] = tr

                models.state["selected"] = "facts"
                s_clar = svc.store.create_session(uuid.uuid4().hex)
                j = svc.submit(s_clar["id"], "clarify please", uuid.uuid4().hex, "facts")
                done = wait(svc, j)
                self.assertEqual(done["status"], "complete")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "clarification")
                traces_by_flow["clarification"] = tr
                models.state["selected"] = "lfm3-1.7b-atomic-ad-q4km"

                s_data = svc.store.create_session(uuid.uuid4().hex)
                j = svc.submit(s_data["id"], "What is NVDA trading at?", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                self.assertEqual(done["status"], "complete")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "single_pass_data_answer")
                traces_by_flow["single_pass_data_answer"] = tr

                s_py = svc.store.create_session(uuid.uuid4().hex)
                j = svc.submit(s_py["id"], "Previous close for NVDA", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                self.assertEqual(done["status"], "complete")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "python_market_answer")
                traces_by_flow["python_market_answer"] = tr

                s_news = svc.store.create_session(uuid.uuid4().hex)
                j = svc.submit(s_news["id"], "Any news on NVDA?", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                self.assertEqual(done["status"], "complete")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "python_news_answer")
                traces_by_flow["python_news_answer"] = tr

                s_multi = svc.store.create_session(uuid.uuid4().hex)
                j = svc.submit(s_multi["id"], "Give me the NVDA thesis", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                self.assertEqual(done["status"], "complete")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "multi_agent_research")
                traces_by_flow["multi_agent_research"] = tr

                s_doc = svc.store.create_session(uuid.uuid4().hex)
                pages = ExtractedPages(["Circular_47 revenue grew 18 percent in Q3."])
                svc.store.add_document(s_doc["id"], "Circular_47.pdf", "a" * 64, 128, pages)
                j = svc.submit(s_doc["id"], "What does Circular_47 say about revenue?", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                self.assertIn(done["status"], {"complete", "degraded"})
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "document_research")
                traces_by_flow["document_research"] = tr

                s_watch = svc.store.create_session(uuid.uuid4().hex)
                j = svc.submit(s_watch["id"], "Track NVDA for me", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                self.assertEqual(done["status"], "complete")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "watchlist_add")
                traces_by_flow["watchlist_add"] = tr

                s_cancel = svc.store.create_session(uuid.uuid4().hex)
                cancel_evt = threading.Event()
                models.cancel_event = cancel_evt
                j = svc.submit(s_cancel["id"], "I've $100 cancel test", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                models.cancel_event = None
                self.assertEqual(done["status"], "cancelled")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "cancelled")
                traces_by_flow["cancelled"] = tr

                s_err = svc.store.create_session(uuid.uuid4().hex)
                models.fail_next = True
                j = svc.submit(s_err["id"], "I've $100 error test", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                self.assertEqual(done["status"], "error")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "error")
                traces_by_flow["error"] = tr

                self.assertEqual(set(traces_by_flow), set(CHAT_FLOWS))

                dump = root / "real_flow_traces.json"
                dump.write_text(json.dumps(traces_by_flow), encoding="utf-8")
                fe_dir = Path(__file__).resolve().parents[1] / "frontend"
                env = {**os.environ, "COFINANCE_REAL_TRACES_FILE": str(dump)}
                res = subprocess.run(
                    ["npx", "vitest", "run", "src/lib/run-map-flows.test.tsx"],
                    cwd=fe_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    shell=os.name == "nt",
                )
                self.assertEqual(res.returncode, 0, f"Cross-layer RunMap contract failed:\n{res.stdout}\n{res.stderr}")
            finally:
                svc.close()

    def test_delete_and_clear_all_scrub_fts5_tokens_orphan_traces_pdfs_and_notify_after_unlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            svc, models = self._make_service(root, lambda q: RoutedQuery("price_snapshot", 0.9, "intraday", ["NVDA"]))
            semantic = DeletableSemantic()
            svc.memory = HybridMemory(svc.keyword_memory, semantic)
            try:
                svc.store.add_watch("NVDA")
                s1 = svc.store.create_session(uuid.uuid4().hex)
                pages = ExtractedPages(["ConfidentialPdfClause zeta99"])
                svc.store.add_document(s1["id"], "secret.pdf", "b" * 64, 64, pages)
                j1 = svc.submit(s1["id"], "What is NVDA trading at for zephyrquartz?", uuid.uuid4().hex, models.state["selected"])
                wait(svc, j1)
                for t in list(svc.threads.values()):
                    t.join(timeout=5)
                j1b = svc.submit(s1["id"], "What is NVDA trading at for cobaltfjord?", uuid.uuid4().hex, models.state["selected"])
                wait(svc, j1b)
                for t in list(svc.threads.values()):
                    t.join(timeout=5)
                svc.add_note(s1["id"], "Remember UniqueExportToken42", uuid.uuid4().hex)

                orphan = TraceRecorder("Orphaned chat run", mode="live", directory=root / "traces")
                orphan.event("Route conversational request", "route", {"intent": "open_conversation", "tickers": []}, actor="Routing executive")
                orphan.finish(summary={"origin": "chat", "conversation_id": uuid.uuid4().hex, "flow": "open_conversation", "answer": "orphan reply"})

                sys_trace = TraceRecorder("News collection: official company feeds", mode="live", directory=root / "traces")
                sys_trace.event("Clock 1 collection", "run", {"actions": {"digest": 1}}, actor="Python")
                sys_trace.finish(summary={"fetched": 1})

                fix_trace = TraceRecorder("Three-agent thesis / fixture", mode="fixture", directory=root / "traces")
                fix_trace.finish(summary={"answer": "fixture"})

                categories = {r["id"]: r["category"] for r in list_runs(root / "traces")}
                self.assertEqual(categories[j1["id"]], "chat")
                self.assertEqual(categories[orphan.run_id], "chat")
                self.assertEqual(categories[sys_trace.run_id], "news")
                self.assertEqual(categories[fix_trace.run_id], "fixture")

                trace_exists_during_notify = []
                orig_notify = svc.notify_change

                def spy_notify():
                    trace_exists_during_notify.append((root / "traces" / f"{j1['id']}.json").exists())
                    orig_notify()

                svc.notify_change = spy_notify
                cleared = svc.clear_conversations(uuid.uuid4().hex)
                self.assertEqual(cleared["deleted"], 1)
                self.assertEqual(cleared["traces_deleted"], 3)
                self.assertTrue(trace_exists_during_notify)
                self.assertFalse(trace_exists_during_notify[-1], "Final SSE notify must fire AFTER trace unlink")

                db = sqlite3.connect(root / "chat_v2.db")
                try:
                    for table in ("chat_sessions", "chat_messages", "chat_jobs", "chat_documents", "pdf_source_v1"):
                        self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
                finally:
                    db.close()

                for token in (b"zephyrquartz", b"cobaltfjord", b"uniqueexporttoken42", b"confidentialpdfclause"):
                    self.assertFalse(_raw_bytes_contain(root / "memory_v2.db", token), f"Token leaked in memory_v2.db: {token!r}")
                    self.assertFalse(_raw_bytes_contain(root / "chat_v2.db", token), f"Token leaked in chat_v2.db: {token!r}")

                remaining = {r["id"]: r["category"] for r in list_runs(root / "traces")}
                self.assertNotIn(j1["id"], remaining)
                self.assertNotIn(orphan.run_id, remaining)
                self.assertEqual(remaining.get(sys_trace.run_id), "news")
                self.assertEqual(remaining.get(fix_trace.run_id), "fixture")
                self.assertEqual([w["ticker"] for w in svc.store.watchlist()], ["NVDA"])
            finally:
                svc.close()

    def test_forgotten_turn_never_reaches_open_chat_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captured_messages = []

            def route_fn(q):
                if "nvda" in q.lower():
                    return RoutedQuery("price_snapshot", 0.9, "intraday", ["NVDA"])
                return RoutedQuery("open_conversation", 0.9, "swing_week", [])

            svc, models = self._make_service(root, route_fn)
            orig_complete = models.complete

            def spy_complete(system, prompt, **kwargs):
                if kwargs.get("messages") is not None:
                    captured_messages.append(list(kwargs["messages"]))
                return orig_complete(system, prompt, **kwargs)

            models.complete = spy_complete
            try:
                s = svc.store.create_session(uuid.uuid4().hex)
                j1 = svc.submit(s["id"], "What is NVDA trading at for SecretForgotSentinel?", uuid.uuid4().hex, models.state["selected"])
                wait(svc, j1)
                for t in list(svc.threads.values()):
                    t.join(timeout=5)
                svc.forget(s["id"], j1["id"], uuid.uuid4().hex)

                j2 = svc.submit(s["id"], "I've $100. Now make a suggestion", uuid.uuid4().hex, models.state["selected"])
                done2 = wait(svc, j2)
                self.assertEqual(done2["status"], "complete")
                self.assertTrue(captured_messages)
                flat = json.dumps(captured_messages[-1])
                self.assertNotIn("SecretForgotSentinel", flat)
                self.assertNotIn("138.5", flat)
            finally:
                svc.close()

    def test_hymemory_sqlite_stores_scrub_deleted_tokens_from_disk_bytes(self):
        from core.hymemory_port import HyMemoryLite

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store_root = root / "hymemory"
            hm = HyMemoryLite(Path(__file__).resolve().parents[1], root, store=store_root)
            sentinel = "UltrasecretSentinelToken991"
            sid = uuid.uuid4().hex
            mid = "mem_" + uuid.uuid4().hex

            with hm._db() as db:
                db.execute("INSERT INTO links VALUES (?,?,?,?,?,?,?)", (mid, "chat", sid, uuid.uuid4().hex, '["NVDA"]', sentinel, 1.0))

            data_dir = store_root / "store" / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            cdb = sqlite3.connect(data_dir / "cache.db")
            cdb.executescript("""
                CREATE TABLE memory_operations_v2(id INTEGER PRIMARY KEY, memory_id TEXT, old_memory_id TEXT, content TEXT);
                CREATE TABLE pipeline_logs_v2(id INTEGER PRIMARY KEY, session_id TEXT, prompt TEXT);
                CREATE TABLE write_outcomes(request_id TEXT PRIMARY KEY, session_id TEXT, result_json TEXT);
                CREATE TABLE episode_state(episode_key TEXT PRIMARY KEY, session_id TEXT);
            """)
            cdb.execute("INSERT INTO memory_operations_v2(memory_id, old_memory_id, content) VALUES (?, '', ?)", (mid, sentinel))
            cdb.execute("INSERT INTO pipeline_logs_v2(session_id, prompt) VALUES (?, ?)", (sid, sentinel))
            cdb.execute("INSERT INTO write_outcomes(request_id, session_id, result_json) VALUES ('r1', ?, ?)", (sid, sentinel))
            cdb.execute("INSERT INTO episode_state(episode_key, session_id) VALUES (?, ?)", (f"u::a::{sid}", sid))
            cdb.commit(); cdb.close()

            hdb = sqlite3.connect(data_dir / "history.db")
            hdb.execute("CREATE TABLE memory_history(id INTEGER PRIMARY KEY, memory_id TEXT, isolation_key TEXT, new_memory TEXT)")
            hdb.execute("INSERT INTO memory_history(memory_id, isolation_key, new_memory) VALUES (?, ?, ?)", (mid, f"u::a::{sid}", sentinel))
            hdb.commit(); hdb.close()

            vdir = data_dir / "vector_db"
            vdir.mkdir(parents=True, exist_ok=True)
            vdb = sqlite3.connect(vdir / "chroma.sqlite3")
            vdb.executescript("""
                CREATE TABLE embeddings(id INTEGER PRIMARY KEY);
                CREATE TABLE embedding_metadata(id INTEGER, string_value TEXT);
                CREATE TABLE embedding_metadata_array(id INTEGER, string_value TEXT);
                CREATE TABLE embeddings_queue(seq_id INTEGER PRIMARY KEY, id TEXT, metadata TEXT);
                CREATE VIRTUAL TABLE embedding_fulltext_search USING fts5(string_value, tokenize='trigram');
            """)
            vdb.execute("INSERT INTO embeddings_queue(id, metadata) VALUES (?, ?)", (mid, json.dumps({"session_id": sid, "text": sentinel})))
            vdb.execute("INSERT INTO embedding_fulltext_search(string_value) VALUES (?)", (sentinel,))
            vdb.execute("DELETE FROM embedding_fulltext_search")
            vdb.commit(); vdb.close()

            log_dir = store_root / "logs" / "default"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "2026-11-27.log"
            log_file.write_text(f"INFO stored {sentinel}\n", encoding="utf-8")

            with hm._db() as db:
                db.execute("DELETE FROM links WHERE session_id=?", (sid,))
            hm.scrub_all()

            self.assertFalse(log_file.exists())
            for db_path in (
                store_root / "links.db",
                data_dir / "cache.db",
                data_dir / "history.db",
                vdir / "chroma.sqlite3",
            ):
                self.assertFalse(_raw_bytes_contain(db_path, sentinel.encode()), f"Sentinel leaked in {db_path}")

    def test_concurrency_barriers_for_clear_news_and_headline_judge(self):
        from core.local_models import ModelError

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            svc, models = self._make_service(root, lambda q: RoutedQuery("open_conversation", 0.9, "swing_week", []))
            try:
                s = svc.store.create_session(uuid.uuid4().hex)
                svc._clearing = True
                with self.assertRaises(ModelError):
                    svc.submit(s["id"], "hello", uuid.uuid4().hex, models.state["selected"])
                with self.assertRaises(ModelError):
                    svc.collect_news(uuid.uuid4().hex)
                svc._clearing = False

                svc.threads["f" * 32] = threading.current_thread()
                with self.assertRaises(ModelError):
                    svc.collect_news(uuid.uuid4().hex)
                svc.threads.clear()

                observed = {}
                entered = threading.Event()
                release = threading.Event()

                class _FakeStore:
                    conn = None

                def fake_judge_pending(*args, **kwargs):
                    observed["busy"] = models.busy
                    observed["narrator_locked"] = not svc.narrator_lock.acquire(blocking=False)
                    if not observed["narrator_locked"]:
                        svc.narrator_lock.release()
                    entered.set()
                    release.wait(timeout=3)
                    return {"judged": 0, "failed": 0, "skipped": 0}

                with mock.patch("core.headline_signal.judge_pending", side_effect=fake_judge_pending):
                    t = threading.Thread(target=svc.judge_headlines, args=(_FakeStore(), lambda: None), daemon=True)
                    t.start()
                    self.assertTrue(entered.wait(timeout=3))
                    with self.assertRaises(ModelError):
                        models.select("facts")
                    release.set()
                    t.join(timeout=3)
                self.assertTrue(observed.get("busy"))
                self.assertTrue(observed.get("narrator_locked"))
                self.assertFalse(models.busy)
            finally:
                svc.close()

    def test_sse_notifies_on_trace_change_memory_mutation_and_label_save(self):
        from ui import dashboard

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            svc, _ = self._make_service(root, lambda q: RoutedQuery("open_conversation", 0.9, "swing_week", []))
            (root / "traces").mkdir(parents=True, exist_ok=True)
            s = svc.store.create_session(uuid.uuid4().hex)
            m0 = dashboard.event_mark(svc, s["id"], trace_dir=root / "traces")

            tr = TraceRecorder("SSE trace test", mode="fixture", directory=root / "traces")
            tr.finish(summary={"answer": "ok"})
            m1 = dashboard.event_mark(svc, s["id"], trace_dir=root / "traces")
            self.assertNotEqual(m0, m1)

            seq0 = svc.change_seq
            note = svc.add_note(s["id"], "Watch NVDA export note", uuid.uuid4().hex)
            self.assertGreater(svc.change_seq, seq0)
            m2 = dashboard.event_mark(svc, s["id"], trace_dir=root / "traces")
            self.assertNotEqual(m1, m2)

            seq1 = svc.change_seq
            svc.forget(s["id"], note["item_id"], uuid.uuid4().hex)
            self.assertGreater(svc.change_seq, seq1)
            m3 = dashboard.event_mark(svc, s["id"], trace_dir=root / "traces")
            self.assertNotEqual(m2, m3)

            (root / "traces" / f"{tr.run_id}.json").unlink()
            m4 = dashboard.event_mark(svc, s["id"], trace_dir=root / "traces")
            self.assertNotEqual(m3, m4)
            svc.close()

    def test_trace_revision_is_js_safe_and_changes_on_same_tick_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp) / "traces"
            tr = TraceRecorder("Fast revision test", mode="live", directory=tdir)
            r1 = read_trace(tdir, tr.run_id)["revision"]
            tr.finish(summary={"origin": "chat", "flow": "open_conversation", "answer": "done"})
            r2 = read_trace(tdir, tr.run_id)["revision"]
            self.assertLessEqual(r1, 2**53 - 1)
            self.assertLessEqual(r2, 2**53 - 1)
            self.assertNotEqual(r1, r2)

    def test_empty_watchlist_clarifies_truthfully_and_watchlist_remove_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            svc, models = self._make_service(root, lambda q: RoutedQuery("thesis_review", 0.85, "swing_week", []))
            dist = root / "dist"
            dist.mkdir()
            (dist / "index.html").write_text('<div id="root"></div>')
            srv = create_server(port=0, dist=dist, trace_dir=root / "traces", corpus_dir=root / "none", service=svc)
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            try:
                origin = f"http://127.0.0.1:{srv.server_port}"
                conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
                conn.request("GET", "/api/state", headers={"Host": f"127.0.0.1:{srv.server_port}"})
                csrf = json.loads(conn.getresponse().read())["csrf"]

                s = svc.store.create_session(uuid.uuid4().hex)
                j = svc.submit(s["id"], "Check my watchlist", uuid.uuid4().hex, models.state["selected"])
                done = wait(svc, j)
                self.assertEqual(done["status"], "complete")
                tr = read_trace(root / "traces", j["id"])
                self.assertEqual(tr["summary"]["flow"], "clarification")
                self.assertIn("watchlist is empty", tr["summary"]["answer"].lower())

                hdrs = {"Host": f"127.0.0.1:{srv.server_port}", "Origin": origin, "Content-Type": "application/json", "X-Cofinance-CSRF": csrf}
                conn.request("POST", "/api/watchlist", body=json.dumps({"ticker": "NVDA"}), headers=hdrs)
                r1 = conn.getresponse()
                self.assertEqual(r1.status, 200)
                self.assertEqual([w["ticker"] for w in json.loads(r1.read())], ["NVDA"])

                conn.request("POST", "/api/watchlist/remove", body=json.dumps({"ticker": "NVDA"}), headers=hdrs)
                r2 = conn.getresponse()
                self.assertEqual(r2.status, 200)
                self.assertEqual(json.loads(r2.read()), [])
            finally:
                srv.shutdown()
                srv.server_close()
                th.join(timeout=2)
                svc.close()


if __name__ == "__main__":
    unittest.main()
