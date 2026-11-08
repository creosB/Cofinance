"""Memory ledger: one identity and one state per remembered item, whatever backends hold copies.

Spec: docs/designs/remember_forget_draft.md (review round 1). Lives in data/memory_v2.db next to the keyword index.
  * item_id: the run id for a research turn, a fresh id for a user note. Empty run ids are never an identity.
  * state: active | delete_pending | forgotten. Anything not active is a tombstone: it is filtered from every recall
    path and never re-written, so a forget can not be undone by a delayed write, a backfill or a failed vector delete.
  * keyword_id / semantic_id: the backend copies. NULL means "not written yet" (repaired by ChatService.sync_session).
  * request_id: idempotency for notes; memory_ops records forget results by request id.
  * lock: every memory mutation (post-run write, note, forget, sync, retry) takes it.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

ACTIVE, PENDING, FORGOTTEN = "active", "delete_pending", "forgotten"
KINDS = frozenset({"turn", "note"})
NOTE_LIMIT = 50


class MemoryLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS memory_items(item_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,"
                       " kind TEXT NOT NULL CHECK(kind IN ('turn','note')), run_id TEXT UNIQUE, text TEXT NOT NULL DEFAULT '',"
                       " tickers TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL,"
                       " state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','delete_pending','forgotten')),"
                       " keyword_id TEXT, semantic_id TEXT, request_id TEXT UNIQUE, last_error TEXT NOT NULL DEFAULT '')")
            db.execute("CREATE INDEX IF NOT EXISTS memory_items_session ON memory_items(session_id, kind, state)")
            # FORGOTTEN is only set after every copy is deleted; rows forgotten before copy ids were cleared still list them.
            db.execute("UPDATE memory_items SET keyword_id=NULL, semantic_id=NULL WHERE state='forgotten' AND (keyword_id IS NOT NULL OR semantic_id IS NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS memory_ops(request_id TEXT PRIMARY KEY, op TEXT NOT NULL, session_id TEXT NOT NULL,"
                       " result TEXT NOT NULL, created REAL NOT NULL)")
            # Forget-all also sweeps the vector store for this conversation (copies no item or link points to). A sweep that
            # could not run is kept here and retried, so "forget all" is never silently partial.
            db.execute("CREATE TABLE IF NOT EXISTS memory_sweeps(session_id TEXT PRIMARY KEY, created REAL NOT NULL, last_error TEXT NOT NULL DEFAULT '')")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            # Every write, not only the final scrub: a tombstone UPDATE moves a row and leaves the old copy, text included,
            # in free page space (found 2026-11-08 by tests/test_delete_conversations.py reading the file bytes).
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db
        finally:
            db.close()

    # -- reads -----------------------------------------------------------------------------------------------------
    def get(self, item_id: str) -> Optional[dict]:
        if not item_id:
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM memory_items WHERE item_id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def by_request(self, request_id: str) -> Optional[dict]:
        with self._db() as db:
            row = db.execute("SELECT * FROM memory_items WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def items(self, session_id: str) -> List[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM memory_items WHERE session_id=? ORDER BY created", (session_id,))]

    def inactive_runs(self, session_id: str) -> set:
        with self._db() as db:
            return {r[0] for r in db.execute("SELECT run_id FROM memory_items WHERE session_id=? AND state<>'active' AND run_id IS NOT NULL",
                                             (session_id,))}

    def pending(self) -> List[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM memory_items WHERE state='delete_pending' ORDER BY created")]

    def active_notes(self, session_id: str) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM memory_items WHERE session_id=? AND kind='note' AND state='active'",
                              (session_id,)).fetchone()[0]

    def op(self, request_id: str, session_id: Optional[str] = None, op: Optional[str] = None) -> Optional[dict]:
        """Stored result of an earlier request. A request id reused for another conversation or another kind of action
        is refused, never answered with someone else's result."""
        with self._db() as db:
            row = db.execute("SELECT result, session_id, op FROM memory_ops WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        if (session_id is not None and row[1] != session_id) or (op is not None and row[2] != op):
            raise ValueError("That request id was already used for something else.")
        return json.loads(row[0])

    # -- writes (callers hold self.lock) ---------------------------------------------------------------------------
    def ensure(self, item_id: str, session_id: str, kind: str, *, run_id: Optional[str] = None, text: str = "",
               tickers: Iterable[str] = (), request_id: Optional[str] = None) -> dict:
        if kind not in KINDS or not item_id or not session_id:
            raise ValueError("A memory item needs an id, a conversation and a known kind.")
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO memory_items(item_id, session_id, kind, run_id, text, tickers, created, request_id)"
                       " VALUES (?,?,?,?,?,?,?,?)", (item_id, session_id, kind, run_id, text[:4000],
                       json.dumps(sorted({t.upper() for t in tickers})), time.time(), request_id))
            row = db.execute("SELECT * FROM memory_items WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise ValueError("That request id or run id is already used by another memory item.")
        if row["session_id"] != session_id:
            raise ValueError("That memory item belongs to another conversation.")
        return dict(row)

    def update(self, item_id: str, **values) -> None:
        allowed = {"state", "keyword_id", "semantic_id", "last_error"}
        if not values or set(values) - allowed:
            raise ValueError("Unknown memory item field.")
        if "state" in values and values["state"] not in (ACTIVE, PENDING, FORGOTTEN):
            raise ValueError("Unknown memory item state.")
        sets = ", ".join(f"{k}=?" for k in values)
        with self._db() as db:
            db.execute(f"UPDATE memory_items SET {sets} WHERE item_id=?", (*values.values(), item_id))

    def scrub(self, session_id: str) -> int:
        """A deleted conversation keeps its tombstones (state and copy ids, so a pending delete still retries and nothing
        is re-created), but not their content: question text, note text and assets are emptied. Never touches an active
        item; callers forget the conversation first. secure_delete zeroes the old text on disk."""
        with self._db() as db:
            db.execute("PRAGMA secure_delete=ON")
            n = db.execute("UPDATE memory_items SET text='', tickers='[]' WHERE session_id=? AND state<>'active'",
                           (session_id,)).rowcount
        with self._db() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return n

    def sweep_pending(self, session_id: str, error: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO memory_sweeps VALUES (?,?,?) ON CONFLICT(session_id) DO UPDATE SET last_error=excluded.last_error",
                       (session_id, time.time(), error[:300]))

    def sweep_done(self, session_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM memory_sweeps WHERE session_id=?", (session_id,))

    def pending_sweeps(self) -> List[str]:
        with self._db() as db:
            return [r[0] for r in db.execute("SELECT session_id FROM memory_sweeps ORDER BY created")]

    def record_op(self, request_id: str, op: str, session_id: str, result: dict) -> None:
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO memory_ops VALUES (?,?,?,?,?)", (request_id, op, session_id, json.dumps(result), time.time()))

    # -- the one tombstone predicate -------------------------------------------------------------------------------
    def resolve(self, session_id: str, hits) -> list:
        """Keep only hits that map to an ACTIVE item of this conversation; tag them with item_id and kind.
        Unknown hits (legacy rows never registered, other-conversation ids) are dropped, never trusted."""
        if not hits:
            return []
        with self._db() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM memory_items WHERE session_id=?", (session_id,))]
        by_run = {r["run_id"]: r for r in rows if r["run_id"]}
        by_backend = {r[k]: r for r in rows for k in ("keyword_id", "semantic_id") if r[k]}
        out = []
        for hit in hits:
            item = by_run.get(hit.run_id) if hit.run_id else None
            item = item or by_backend.get(hit.id)
            if not item or item["state"] != ACTIVE:
                continue
            out.append(dataclasses.replace(hit, item_id=item["item_id"], kind=item["kind"]))
        return out
