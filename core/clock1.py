"""Clock 1: the background news loop.  collect -> pre-gate -> judge -> SignalGate -> SQLite (namespace "news").

  python -m core.clock1 --replay fixtures/news_replay.json --db data/news.db
  python -m core.clock1 --last30days <path>/skills/last30days/scripts/last30days.py --preflight
  python -m core.clock1 --last30days <path>/skills/last30days/scripts/last30days.py --tickers NVDA,TSLA
  python -m core.clock1 --feeds                      # Apple and NVIDIA official newsroom feeds only

Guarantees (tested in tests/test_clock1.py):
  * one run is one SQLite transaction: a crash commits nothing, so nothing is half-alerted;
  * seen keys and title fingerprints persist, so a replayed or syndicated item never alerts twice;
  * the ticker cooldown and the alert cap (a rolling window, not per process) survive restarts;
  * quarantined (instruction-like) text never reaches the classifier;
  * a classifier outage yields DEFERRED (retried next run), never ALERT.
No tools, notifications or watchlist writes happen here: outcomes are rows, nothing else.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List

from .collector import FetchResult, Last30DaysCollector, ReplayCollector, epoch
from .news_store import NewsStore
from .pregate import quarantine_reason, speculation_reason, title_key
from .signals import Action, Classifier, GateConfig, Outcome, SignalGate

Fetch = Callable[[str], FetchResult]


@dataclass
class Clock1Config:
    gate: GateConfig = field(default_factory=GateConfig)
    alert_window_s: float = 3600.0      # gate.max_alerts applies per rolling window of this length
    quarantine: bool = True
    speculation: bool = True            # prediction-market style items: IGNORE "speculation:...", never judged


@dataclass
class RunSummary:
    started_at: float
    fetched: int = 0
    quarantined: int = 0
    speculation: int = 0
    duplicate_titles: int = 0
    alerts_in_window_before: int = 0
    actions: Dict[str, int] = field(default_factory=dict)
    reasons: Dict[str, int] = field(default_factory=dict)
    alerts: List[Dict[str, str]] = field(default_factory=list)
    source_status: Dict[str, Dict[str, str]] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)
    purged: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_once(tickers: Iterable[str], fetch: Fetch, classifier: Classifier, store: NewsStore,
             cfg: Clock1Config | None = None, clock: Callable[[], float] = time.time) -> RunSummary:
    cfg = cfg or Clock1Config()
    now = clock()
    s = RunSummary(started_at=now)
    actions: Counter = Counter()
    reasons: Counter = Counter()
    with store.transaction():
        s.purged = store.purge_expired(now)
        prior = s.alerts_in_window_before = store.alerts_since(now - cfg.alert_window_s)
        gate = SignalGate(classifier, cfg.gate, seen=store.seen, last_alert=store.cooldowns, clock=lambda: now)
        alerts_this_run = 0
        for raw in tickers:
            tkr = (raw or "").strip().upper()
            if not tkr:
                continue
            try:
                res = fetch(tkr)
            except Exception as e:                                   # one bad source never sinks the run
                s.errors[tkr] = f"{type(e).__name__}: {e}"[:300]
                continue
            s.source_status[tkr] = dict(res.source_status)
            s.fetched += len(res.items)
            pre: List[Outcome] = []
            batch, batch_titles = [], set()
            for it in res.items:
                if it.ticker.upper() != tkr:                         # a source cannot re-target another ticker
                    it = replace(it, ticker=tkr)
                why = quarantine_reason(it) if cfg.quarantine else None
                if why:
                    store.seen.add(it.key)
                    pre.append(Outcome(it, Action.IGNORE, f"quarantined:{why}"))
                    s.quarantined += 1
                    continue
                spec = speculation_reason(it) if cfg.speculation else None
                if spec:
                    store.seen.add(it.key)
                    pre.append(Outcome(it, Action.IGNORE, f"speculation:{spec}"))
                    s.speculation += 1
                    continue
                tk = title_key(it)
                if tk in batch_titles or tk in store.seen:
                    pre.append(Outcome(it, Action.IGNORE, "duplicate_title"))
                    s.duplicate_titles += 1
                    continue
                batch_titles.add(tk)
                batch.append(it)
            outs = gate.process(batch, alerts_already=prior + alerts_this_run)
            for o in outs:
                if o.judgment is not None:
                    store.seen.add(title_key(o.item))
                if o.action is Action.ALERT:
                    alerts_this_run += 1
                    s.alerts.append({"ticker": tkr, "title": o.item.title, "source": o.item.source, "url": o.item.url})
            for o in pre + outs:
                actions[o.action.value] += 1
                reasons[o.reason] += 1
            store.record(outs, now)
        s.actions, s.reasons = dict(actions), dict(reasons)
        store.record_run(now, s.as_dict())
    return s


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cofinance Clock 1: collect -> pre-gate -> judge -> gate -> SQLite")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--replay", help="cofinance-replay/1 JSON file")
    src.add_argument("--last30days", help="path to skills/last30days/scripts/last30days.py")
    src.add_argument("--feeds", action="store_true", help="registered official company feeds (core.feeds.SOURCES)")
    ap.add_argument("--tickers", default="", help="comma-separated (replay defaults to every recorded ticker)")
    ap.add_argument("--db", default="data/news.db")
    ap.add_argument("--judge", default="gliner", help="gliner | systemone=URL | cascade:gliner+systemone=URL")
    ap.add_argument("--preflight", action="store_true", help="with --last30days: print its preflight JSON and exit")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--now", default="", help="ISO run time (replay defaults to the recording time)")
    a = ap.parse_args(argv)
    tickers = [t for t in a.tickers.split(",") if t.strip()]
    if a.last30days:
        fetch: Any = Last30DaysCollector(a.last30days, days=a.days, timeout_s=a.timeout)
        if a.preflight:
            print(json.dumps(fetch.preflight(), indent=2))
            return 0
        if not tickers:
            ap.error("--tickers is required with --last30days")
        fixed = epoch(a.now) if a.now else None
    elif a.feeds:
        from .news_service import FeedFetcher, feed_tickers
        fetch = FeedFetcher()
        tickers = tickers or feed_tickers()
        fixed = epoch(a.now) if a.now else None
    else:
        fetch = ReplayCollector(a.replay)
        tickers = tickers or fetch.tickers
        fixed = epoch(a.now) if a.now else (fetch.recorded_at or None)
    clock = (lambda: fixed) if fixed else time.time
    from qualify.decide_eval import build_arm                     # model loading stays out of import time

    classifier = build_arm(a.judge)
    os.makedirs(os.path.dirname(os.path.abspath(a.db)), exist_ok=True)
    store = NewsStore(a.db, clock=clock)
    try:
        summary = run_once(tickers, fetch, classifier, store, clock=clock)
    finally:
        store.close()
    print(json.dumps(summary.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
