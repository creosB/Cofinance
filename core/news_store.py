"""SQLite store for Clock 1 (namespace "news"): gated signals with a TTL, seen keys and alert cooldowns.

What the gate needs to stay correct across restarts lives here:
  * seen keys      -> an item (or a syndicated headline) is judged at most once
  * cooldowns      -> the per-ticker alert cooldown survives a restart
  * signals        -> ALERT / DIGEST rows with expires_at (TTL); IGNORE and DEFERRED are not stored
  * alerts_since() -> the alert cap is a rolling window, so a crash-and-restart cannot reset it

One run is one transaction (`with store.transaction():`). If the process dies mid-run nothing from that
run is committed: the items are re-judged next time and no alert is half-recorded.
Namespace isolation: every row carries `namespace` and a store only reads and writes its own.
WAL mode lets Clock 2 read signals while a Clock 1 run holds the write lock.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from collections.abc import MutableMapping, MutableSet
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

from .signals import Action, Item, Judgment, Outcome

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    namespace TEXT NOT NULL, key TEXT NOT NULL, first_seen REAL NOT NULL, PRIMARY KEY (namespace, key));
CREATE TABLE IF NOT EXISTS cooldown (
    namespace TEXT NOT NULL, ticker TEXT NOT NULL, last_alert REAL NOT NULL, PRIMARY KEY (namespace, ticker));
CREATE TABLE IF NOT EXISTS signals (
    namespace TEXT NOT NULL, key TEXT NOT NULL, ticker TEXT NOT NULL, source TEXT, url TEXT, title TEXT,
    body TEXT, published_at REAL, action TEXT NOT NULL, reason TEXT, material REAL, urgent REAL,
    direction TEXT, direction_p REAL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
    PRIMARY KEY (namespace, key));
CREATE INDEX IF NOT EXISTS signals_by_ticker ON signals (namespace, ticker, created_at);
CREATE TABLE IF NOT EXISTS runs (namespace TEXT NOT NULL, started_at REAL NOT NULL, summary TEXT);
"""
_STORED = (Action.ALERT, Action.DIGEST)


class _SeenKeys(MutableSet):
    def __init__(self, conn: sqlite3.Connection, ns: str, clock: Callable[[], float]) -> None:
        self._c, self._ns, self._clock = conn, ns, clock

    def __contains__(self, key: object) -> bool:
        return self._c.execute("SELECT 1 FROM seen WHERE namespace=? AND key=?", (self._ns, key)).fetchone() is not None

    def __iter__(self) -> Iterator[str]:
        return iter([r[0] for r in self._c.execute("SELECT key FROM seen WHERE namespace=?", (self._ns,))])

    def __len__(self) -> int:
        return self._c.execute("SELECT COUNT(*) FROM seen WHERE namespace=?", (self._ns,)).fetchone()[0]

    def add(self, key: str) -> None:
        self._c.execute("INSERT OR IGNORE INTO seen VALUES (?, ?, ?)", (self._ns, key, self._clock()))

    def discard(self, key: str) -> None:
        self._c.execute("DELETE FROM seen WHERE namespace=? AND key=?", (self._ns, key))


class _Cooldowns(MutableMapping):
    def __init__(self, conn: sqlite3.Connection, ns: str) -> None:
        self._c, self._ns = conn, ns

    def __getitem__(self, ticker: str) -> float:
        row = self._c.execute("SELECT last_alert FROM cooldown WHERE namespace=? AND ticker=?",
                              (self._ns, ticker)).fetchone()
        if row is None:
            raise KeyError(ticker)
        return row[0]

    def __setitem__(self, ticker: str, ts: float) -> None:
        self._c.execute("INSERT INTO cooldown VALUES (?, ?, ?) ON CONFLICT(namespace, ticker) "
                        "DO UPDATE SET last_alert=excluded.last_alert", (self._ns, ticker, float(ts)))

    def __delitem__(self, ticker: str) -> None:
        if self._c.execute("DELETE FROM cooldown WHERE namespace=? AND ticker=?", (self._ns, ticker)).rowcount == 0:
            raise KeyError(ticker)

    def __iter__(self) -> Iterator[str]:
        return iter([r[0] for r in self._c.execute("SELECT ticker FROM cooldown WHERE namespace=?", (self._ns,))])

    def __len__(self) -> int:
        return self._c.execute("SELECT COUNT(*) FROM cooldown WHERE namespace=?", (self._ns,)).fetchone()[0]


class NewsStore:
    def __init__(self, path: str, namespace: str = "news", ttl_s: float = 7 * 86400.0,
                 seen_ttl_s: float = 45 * 86400.0, clock: Callable[[], float] = time.time) -> None:
        if not namespace or not namespace.strip():
            raise ValueError("namespace is required")
        self.namespace, self.ttl_s, self.seen_ttl_s, self.clock = namespace, ttl_s, seen_ttl_s, clock
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=30.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.seen = _SeenKeys(self.conn, namespace, clock)
        self.cooldowns = _Cooldowns(self.conn, namespace)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def record(self, outcomes: Iterable[Outcome], now: Optional[float] = None) -> int:
        now = self.clock() if now is None else now
        n = 0
        for o in outcomes:
            if o.action not in _STORED or o.judgment is None:
                continue
            it, j = o.item, o.judgment
            self.conn.execute(
                "INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.namespace, it.key, it.ticker.upper(), it.source, it.url, it.title, it.text, it.published_at,
                 o.action.value, o.reason, j.material, j.urgent, j.direction, j.direction_p, now, now + self.ttl_s))
            n += 1
        return n

    def alerts_since(self, since: float) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM signals WHERE namespace=? AND action=? AND created_at>=?",
                                 (self.namespace, Action.ALERT.value, since)).fetchone()[0]

    def get_gated_signals(self, ticker: str, limit: int = 5) -> List[Outcome]:
        """NewsStorePort for the News Researcher: live ALERT/DIGEST rows, alerts first, newest first."""
        rows = self.conn.execute(
            "SELECT ticker, source, url, title, body, published_at, action, reason, material, urgent, direction, "
            "direction_p FROM signals WHERE namespace=? AND ticker=? AND expires_at>? "
            "ORDER BY (action='alert') DESC, created_at DESC LIMIT ?",
            (self.namespace, ticker.upper(), self.clock(), int(limit))).fetchall()
        return [Outcome(Item(r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or "", r[5] or 0.0), Action(r[6]),
                        r[7] or "", Judgment(r[8], r[9], r[10] or "unclear", r[11] or 0.0)) for r in rows]

    def purge_expired(self, now: Optional[float] = None) -> Dict[str, int]:
        now = self.clock() if now is None else now
        sig = self.conn.execute("DELETE FROM signals WHERE namespace=? AND expires_at<=?", (self.namespace, now)).rowcount
        seen = self.conn.execute("DELETE FROM seen WHERE namespace=? AND first_seen<?",
                                 (self.namespace, now - self.seen_ttl_s)).rowcount
        return {"signals": sig, "seen": seen}

    def record_run(self, started_at: float, summary: Dict[str, Any]) -> None:
        self.conn.execute("INSERT INTO runs VALUES (?, ?, ?)", (self.namespace, started_at, json.dumps(summary)))

    def close(self) -> None:
        self.conn.close()
