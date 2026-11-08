"""HyMemoryLite: MemoryPort backed by Hy-Memory lite (Apache-2.0), fully local.

Pieces (all approved 2026-11-08):
  * embedder: Qwen3-Embedding-0.6B-Q8_0.gguf (SHA-256 06507c7b...) on the managed llama-server, 127.0.0.1 only;
  * worker: core/hymemory_worker.py in the isolated venv data/hymemory/venv (hy-memory==2.0.14), lite mode only;
  * links: data/hymemory/links.db maps Hy-Memory memory_id -> run id, tickers, question (the lite read path does
    not return caller meta, so provenance is kept here).
Network: the worker gets an allowlisted environment, its LLM endpoint is a closed local port, and every
non-local connection is routed to a closed local proxy (fails fast). tiktoken is blocked inside the worker.
Verified 2026-11-08 with a trap proxy: zero outbound attempts, zero LLM calls.
Same rules as LocalKeywordMemory: "chat" namespace only, per-conversation recall, idempotent per run id,
recalled text is context only.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from .memory import MemoryHit, _namespace

MODEL_FILE = "Qwen3-Embedding-0.6B-Q8_0.gguf"
MODEL_SIZE = 639150592
ALLOWED_ENV = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "COMSPEC"}
CLOSED = "http://127.0.0.1:9"


class HyMemoryError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HyMemoryLite:
    backend = "Hy-Memory lite (Qwen3-Embedding-0.6B, local)"

    def __init__(self, repo: str | Path, data: str | Path, *, min_score: float = 0.40, timeout_s: float = 90.0,
                 store: Optional[str | Path] = None) -> None:
        self.repo, self.data = Path(repo), Path(data)
        self.install = self.data / "hymemory"
        self.base = Path(store) if store else self.install  # links.db, vector store and logs; tests use a temp dir
        self.venv_python = self.install / "venv" / "Scripts" / "python.exe"
        self.model = self.install / "models" / MODEL_FILE
        self.server = self.data / "local_models" / "b12110" / "llama-server.exe"
        self.min_score, self.timeout_s = min_score, timeout_s
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.embedder: Optional[subprocess.Popen] = None
        self.worker: Optional[subprocess.Popen] = None
        self.replies: "queue.Queue[Optional[str]]" = queue.Queue()
        self.base.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS links(memory_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, session_id TEXT NOT NULL,"
                       " run_id TEXT UNIQUE, tickers TEXT NOT NULL, question TEXT NOT NULL, created REAL NOT NULL)")

    def available(self) -> bool:
        return self.venv_python.is_file() and self.server.is_file() and self.model.is_file() and self.model.stat().st_size == MODEL_SIZE

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.base / "links.db", timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    # -- processes -------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self.worker and self.worker.poll() is None:
            return
        if not self.available():
            raise HyMemoryError("Hy-Memory lite is not installed (venv, embedder model or llama-server missing).")
        self.close()
        logs = self.base / "logs"
        logs.mkdir(exist_ok=True)
        port = _free_port()
        self.embedder = subprocess.Popen(
            [str(self.server), "--model", str(self.model), "--embedding", "--pooling", "last", "--host", "127.0.0.1",
             "--port", str(port), "-c", "8192", "-ub", "8192", "--alias", "qwen3-embedding-0.6b"],
            cwd=str(self.server.parent), stdin=subprocess.DEVNULL, stdout=(logs / "embedder.log").open("a"),
            stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        until = time.monotonic() + 120
        while time.monotonic() < until:
            if self.embedder.poll() is not None:
                raise HyMemoryError("Embedding server exited. See data/hymemory/logs/embedder.log.")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.5)
        else:
            raise HyMemoryError("Embedding server did not become ready.")
        env = {k: v for k, v in os.environ.items() if k.upper() in ALLOWED_ENV}
        env.update({
            "MEMORY_MODE": "lite", "MEMORY_DATA_DIR": str(self.base / "store"), "MEMORY_LANGUAGE": "en", "MEMORY_VECTOR_STORE": "chroma",
            "MEMORY_EMBEDDER_BASE_URL": f"http://127.0.0.1:{port}/v1", "MEMORY_EMBEDDER_API_KEY": "local-only",
            "MEMORY_EMBEDDER_MODEL": "qwen3-embedding-0.6b", "MEMORY_EMBEDDING_DIMS": "1024", "MEMORY_EMBEDDER_TIMEOUT": "60",
            "MEMORY_LLM_BASE_URL": CLOSED + "/v1", "MEMORY_LLM_API_KEY": "disabled", "MEMORY_LLM_MAX_RETRIES": "0",
            "MEMORY_CODING_ENABLED": "false", "MEMORY_ENABLE_INTENTION": "false", "MEMORY_LOCAL_RERANK_ENABLED": "false",
            "MEMORY_LOG_DIR": str(logs), "ANONYMIZED_TELEMETRY": "False", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
            "PYTHONIOENCODING": "utf-8", "HTTP_PROXY": CLOSED, "HTTPS_PROXY": CLOSED, "ALL_PROXY": CLOSED,
            "NO_PROXY": "127.0.0.1,localhost"})
        self.worker = subprocess.Popen(
            [str(self.venv_python), "-u", str(self.repo / "core" / "hymemory_worker.py")], cwd=str(self.base),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=(logs / "worker.log").open("a"), env=env,
            text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.replies = queue.Queue()
        def pump(stream, q):
            for line in stream:
                q.put(line)
            q.put(None)
        threading.Thread(target=pump, args=(self.worker.stdout, self.replies), daemon=True).start()
        ready = self._reply()
        if not ready.get("ready"):
            raise HyMemoryError("Hy-Memory worker did not start: " + str(ready.get("error", ""))[:200])

    def _reply(self) -> dict:
        try:
            line = self.replies.get(timeout=self.timeout_s)
        except queue.Empty:
            self.close()
            raise HyMemoryError("Hy-Memory worker timed out.")
        if line is None:
            raise HyMemoryError("Hy-Memory worker exited. See data/hymemory/logs/worker.log.")
        return json.loads(line)

    def _call(self, request: dict) -> dict:
        with self.lock:
            self.start()
            self.worker.stdin.write(json.dumps(request) + "\n")
            self.worker.stdin.flush()
            reply = self._reply()
        if not reply.get("ok"):
            raise HyMemoryError(str(reply.get("error", "unknown worker error"))[:300])
        return reply

    def close(self) -> None:
        for proc in (self.worker, self.embedder):
            if proc and proc.poll() is None:
                try:
                    if proc is self.worker and proc.stdin:
                        proc.stdin.close()
                        proc.wait(timeout=10)
                    else:
                        proc.terminate(); proc.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    proc.kill()
        self.worker = self.embedder = None

    # -- MemoryPort ------------------------------------------------------------------------------------------------
    def count(self, namespace: str, session_id: str) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM links WHERE namespace=? AND session_id=?", (_namespace(namespace), session_id)).fetchone()[0]

    def remember_turn(self, namespace, session_id, question, answer, *, run_id, tickers=()):
        namespace = _namespace(namespace)
        if not session_id or not answer.strip():
            raise ValueError("A memory turn needs a conversation and an accepted answer.")
        with self.write_lock:  # check, add and link as one step: a backfill racing the post-run write cannot duplicate
            if run_id:
                with self._db() as db:
                    row = db.execute("SELECT memory_id FROM links WHERE run_id=?", (run_id,)).fetchone()
                if row:
                    return row[0]
            tickers = sorted({t.upper() for t in tickers})
            reply = self._call({"op": "add", "session_id": session_id, "question": question[:2000], "answer": answer[:4000],
                                "run_id": run_id or "", "tickers": tickers})
            memory_id = (reply.get("result") or {}).get("memory_id")
            if not memory_id:
                raise HyMemoryError("Hy-Memory returned no memory id.")
            with self._db() as db:
                db.execute("INSERT OR IGNORE INTO links VALUES (?,?,?,?,?,?,?)",
                           (memory_id, namespace, session_id, run_id or None, json.dumps(tickers), question[:2000], time.time()))
            return memory_id

    def recall(self, namespace, session_id, query, *, limit=2, exclude_run=None) -> List[MemoryHit]:
        namespace = _namespace(namespace)
        if not query.strip() or not self.count(namespace, session_id):
            return []
        reply = self._call({"op": "search", "session_id": session_id, "query": query[:1000], "limit": max(1, min(5, int(limit))) + 2,
                            "min_score": self.min_score})
        found = ((reply.get("result") or {}).get("memories") or {}).get("normal") or []
        hits: List[MemoryHit] = []
        with self._db() as db:
            for m in found:
                row = db.execute("SELECT run_id, tickers, question, created FROM links WHERE memory_id=? AND namespace=? AND session_id=?",
                                 (m.get("memory_id"), namespace, session_id)).fetchone()
                if not row or (exclude_run and row[0] == exclude_run):
                    continue  # unknown or other-conversation ids are never trusted
                content = m.get("content") or ""
                answer = content.split("[assistant]:", 1)[1].strip() if "[assistant]:" in content else content
                hits.append(MemoryHit(m["memory_id"], session_id, row[2], answer, row[0], float(row[3]), float(m.get("score") or 0.0), json.loads(row[1])))
        return sorted(hits, key=lambda h: -h.score)[:max(1, min(5, int(limit)))]

    def memory_id_for_run(self, run_id: str, session_id: Optional[str] = None) -> Optional[str]:
        """Vector id of a turn written before the ledger existed (links.db holds the run id). Scoped to one conversation."""
        with self._db() as db:
            row = db.execute("SELECT memory_id FROM links WHERE run_id=? AND (? IS NULL OR session_id=?)", (run_id, session_id, session_id)).fetchone()
        return row[0] if row else None

    def delete_session(self, namespace: str, session_id: str) -> int:
        """Delete every vector copy of ONE conversation, linked or not, then its links. Raises HyMemoryError on failure."""
        namespace = _namespace(namespace)
        if not session_id:
            raise ValueError("A conversation id is required.")
        with self.write_lock:
            reply = self._call({"op": "delete_session", "session_id": session_id})
            result = reply.get("result") or {}
            if not result.get("success", True):
                raise HyMemoryError("Hy-Memory session delete reported failure.")
            with self._db() as db:
                db.execute("DELETE FROM links WHERE namespace=? AND session_id=?", (namespace, session_id))
            return int(result.get("deleted_count", 0) or 0)

    def session_links(self, namespace: str, session_id: str) -> list:
        """(vector id, run id or None) of every vector copy of one conversation, so forget-all can reach copies no ledger item knows."""
        with self._db() as db:
            rows = db.execute("SELECT memory_id, run_id FROM links WHERE namespace=? AND session_id=?", (_namespace(namespace), session_id)).fetchall()
        return [(r[0], r[1] or None) for r in rows]

    def delete(self, namespace: str, session_id: str, memory_id: str) -> int:
        """Delete one vector copy. Only ids linked to this conversation are accepted. Raises HyMemoryError on failure,
        so the caller keeps the item delete_pending (the ledger tombstone already blocks recall)."""
        namespace = _namespace(namespace)
        with self.write_lock:
            with self._db() as db:
                row = db.execute("SELECT memory_id FROM links WHERE memory_id=? AND namespace=? AND session_id=?",
                                 (memory_id, namespace, session_id)).fetchone()
            if not row:
                return 0  # already gone or never ours: nothing to delete, never touch another conversation
            reply = self._call({"op": "delete", "memory_id": memory_id})
            result = reply.get("result") or {}
            if not result.get("success", True):
                # A repeat delete (crash after the vector delete, before the link delete) also reports failure. Only a
                # copy that still exists is a real failure; one that is gone is finished, so the retry can complete.
                still = ((self._call({"op": "exists", "memory_id": memory_id}).get("result") or {}).get("exists"))
                if still is not False:
                    raise HyMemoryError("Hy-Memory delete reported failure.")
                result = {"deleted_count": 0}
            with self._db() as db:
                db.execute("DELETE FROM links WHERE memory_id=?", (memory_id,))
            return int(result.get("deleted_count", 1) or 0)

    def backfill(self, namespace: str, session: dict) -> int:
        added, question = 0, ""
        for message in session.get("messages", []):
            if message.get("role") == "user":
                question = message.get("content", "")
            elif (message.get("payload") or {}).get("kind") == "research" and message.get("content"):
                self.remember_turn(namespace, session["id"], question, message["content"], run_id=message.get("run_id"),
                                   tickers=(message.get("payload") or {}).get("tickers", []))
                added += 1
        return added
