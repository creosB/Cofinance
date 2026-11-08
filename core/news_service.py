"""Operational Clock 1 over the approved official company feeds.

One explicit collection run: registered HTTPS feeds -> pre-gate -> GLiNER judge -> SignalGate -> data/news.db.
No scheduler, notifications, watchlist writes or hosted models. An ALERT is a stored row; nothing is sent.
Network authority is limited to core.feeds.SOURCES. The judge runs offline in its own process.

Timestamp honesty: Apple's Atom feed supplies `updated`, not publication time. The stored item time keeps
the feed's own value, and the health view reports which kind each source supplies.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

from .collector import FetchResult, epoch
from .feeds import SOURCES, FeedError, fetch_feed, parse_feed
from .signals import Item

TIMESTAMP_KIND = {"apple_newsroom": "feed update time", "nvidia_newsroom": "publication time"}
LABELS = {"apple_newsroom": "Apple Newsroom", "nvidia_newsroom": "NVIDIA Newsroom"}


def feed_tickers() -> list[str]:
    return sorted({source.ticker for source in SOURCES.values()})


def iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class FeedFetcher:
    """Clock 1 Fetch over registered official feeds. A ticker without a feed gets an explicit status."""

    def __init__(self, fetch: Callable = fetch_feed, *, timeout: float = 15.0,
                 check: Optional[Callable[[], None]] = None, text_limit: int = 600) -> None:
        self.fetch, self.timeout, self.check, self.text_limit = fetch, timeout, check, text_limit

    def __call__(self, ticker: str) -> FetchResult:
        items: list[Item] = []
        status: dict[str, str] = {}
        for name, source in SOURCES.items():
            if source.ticker != ticker:
                continue
            if self.check:
                self.check()
            try:
                rows, rejected = parse_feed(self.fetch(name, timeout=self.timeout), source)
            except FeedError as exc:
                status[name] = f"error: {exc}"[:200]
                continue
            for row in rows:
                items.append(Item(ticker, name, row["url"], row["title"], row["text"][:self.text_limit],
                                  epoch(row["timestamp"])))
            skipped = sum(rejected.values())
            status[name] = f"ok: {len(rows)} entries" + (f", {skipped} rejected" if skipped else "")
        if not status:
            status["none"] = "no approved source for this ticker"
        return FetchResult(items, status)


def news_health(path: str | Path, watchlist: Iterable[str] = (), now: Optional[float] = None) -> dict:
    """Read-only freshness and coverage summary. Never creates the store."""
    now = time.time() if now is None else now
    path = Path(path)
    covered = feed_tickers()
    sources = [{"name": name, "label": LABELS.get(name, name), "ticker": s.ticker, "host": urlsplit(s.url).hostname,
                "timestamp_kind": TIMESTAMP_KIND.get(name, "unknown")} for name, s in SOURCES.items()]
    health = {"collected": False, "last_run": None, "sources": sources, "tickers": [],
              "uncovered": sorted({t.upper() for t in watchlist} - set(covered)), "error": ""}
    if not path.is_file():
        health["tickers"] = [{"ticker": t, "digest": 0, "alert": 0, "newest_item": None} for t in covered]
        return health
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            run = db.execute("SELECT started_at, summary FROM runs WHERE namespace='news' "
                             "ORDER BY started_at DESC LIMIT 1").fetchone()
            counts = db.execute("SELECT ticker, action, COUNT(*), MAX(published_at) FROM signals "
                                "WHERE namespace='news' AND expires_at>? GROUP BY ticker, action", (now,)).fetchall()
        finally:
            db.close()
    except sqlite3.Error as exc:
        health["error"] = f"News store unreadable: {type(exc).__name__}"
        return health
    rows: dict[str, dict] = {t: {"ticker": t, "digest": 0, "alert": 0, "newest_item": None} for t in covered}
    for ticker, action, n, newest in counts:
        row = rows.setdefault(ticker, {"ticker": ticker, "digest": 0, "alert": 0, "newest_item": None})
        if action in ("digest", "alert"):
            row[action] = int(n)
        stamp = iso(newest)
        if stamp and (row["newest_item"] is None or stamp > row["newest_item"]):
            row["newest_item"] = stamp
    health["tickers"] = sorted(rows.values(), key=lambda r: r["ticker"])
    if run:
        try:
            summary = json.loads(run[1] or "{}")
        except ValueError:
            summary = {}
        health["collected"] = True
        health["last_run"] = {
            "started_at": iso(run[0]), "fetched": int(summary.get("fetched") or 0),
            "quarantined": int(summary.get("quarantined") or 0),
            "duplicates": int(summary.get("duplicate_titles") or 0),
            "actions": {str(k): int(v) for k, v in (summary.get("actions") or {}).items()},
            "source_status": {str(t): {str(k): str(v) for k, v in (s or {}).items()}
                              for t, s in (summary.get("source_status") or {}).items()},
            "errors": {str(k): str(v)[:200] for k, v in (summary.get("errors") or {}).items()},
        }
    return health


def coverage_note(health: dict, tickers: Iterable[str]) -> str:
    """Deterministic news-coverage sentence for an answer. Not narrated by a model."""
    tickers = [t.upper() for t in tickers]
    covered = set(feed_tickers())
    missing = [t for t in tickers if t not in covered]
    if not health.get("collected"):
        note = "News has not been collected yet, so this answer has no news evidence."
    else:
        when = (health.get("last_run") or {}).get("started_at") or "an unknown time"
        note = f"News comes from official company feeds collected at {when[:16].replace('T', ' ')} UTC."
    if missing:
        note += " No approved news source covers " + ", ".join(missing) + "."
    return note
