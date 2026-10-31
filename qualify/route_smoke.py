"""Live smoke test: GlinerQueryRouter on the real fastino/GLiNER2.5-Decide checkpoint (CPU by default).

  python -m qualify.route_smoke --out reports/route_smoke.json

Ten hand-written queries with an expected intent and ticker set. This is a wiring check, not a
benchmark: n=10 cannot estimate accuracy. Latency excludes model load and one warm-up call.
Exit code 1 when any query misses its intent or tickers.
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable, Dict, List

from core.gliner_decide import DEFAULT_ALIASES, GlinerQueryRouter

from .run import pct

QUERIES: List[Dict[str, Any]] = [
    {"query": "What is NVDA trading at right now?", "intent": "price_snapshot", "tickers": ["NVDA"]},
    {"query": "Any news on Tesla this week?", "intent": "news_catalysts", "tickers": ["TSLA"]},
    {"query": "Compare AMD and NVDA for the next month", "intent": "compare_assets", "tickers": ["AMD", "NVDA"]},
    {"query": "Add $SOL to my watchlist", "intent": "watchlist_add", "tickers": ["SOL"]},
    {"query": "Give me the bull and bear case for Apple", "intent": "thesis_review", "tickers": ["AAPL"]},
    {"query": "How is Bitcoin priced today?", "intent": "price_snapshot", "tickers": ["BTC"]},
    {"query": "What are the latest catalysts for Microsoft stock?", "intent": "news_catalysts", "tickers": ["MSFT"]},
    {"query": "Ethereum vs Solana: which one looks stronger?", "intent": "compare_assets", "tickers": ["ETH", "SOL"]},
    {"query": "Keep an eye on GOOGL for me", "intent": "watchlist_add", "tickers": ["GOOGL"]},
    {"query": "Is Amazon a good long-term hold? Full thesis please", "intent": "thesis_review", "tickers": ["AMZN"]},
]


def run(router: GlinerQueryRouter, queries: List[Dict[str, Any]],
        clock: Callable[[], float] = time.perf_counter) -> Dict[str, Any]:
    rows = []
    for q in queries:
        t0 = clock()
        r = router.route(q["query"])
        ms = (clock() - t0) * 1000
        intent_ok = r.intent == q["intent"]
        tickers_ok = sorted(r.tickers) == sorted(q["tickers"])
        rows.append({"query": q["query"], "intent": r.intent, "intent_confidence": r.intent_confidence,
                     "horizon": r.horizon, "tickers": r.tickers, "expected_intent": q["intent"],
                     "expected_tickers": q["tickers"], "intent_ok": intent_ok, "tickers_ok": tickers_ok,
                     "ok": intent_ok and tickers_ok, "ms": round(ms, 1)})
    ms_all = [row["ms"] for row in rows]
    return {"n": len(rows), "passed": sum(row["ok"] for row in rows),
            "intent_correct": sum(row["intent_ok"] for row in rows),
            "tickers_correct": sum(row["tickers_ok"] for row in rows),
            "p50_ms": pct(ms_all, .5), "p95_ms": pct(ms_all, .95), "rows": rows,
            "note": "Wiring check on 10 hand-written queries, not an accuracy estimate."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="fastino/GLiNER2.5-Decide")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    from gliner2 import AutoExtractor  # heavy import kept out of module import time

    t0 = time.perf_counter()
    backend = AutoExtractor.from_pretrained(a.model)
    load_s = time.perf_counter() - t0
    router = GlinerQueryRouter(backend, universe=set(DEFAULT_ALIASES.values()))
    router.route("warm-up: price of NVDA")
    report = run(router, QUERIES)
    report.update({"model": a.model, "load_s": round(load_s, 2)})
    if a.out:
        import os
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    for row in report["rows"]:
        mark = "ok  " if row["ok"] else "MISS"
        print(f"{mark} {row['ms']:7.1f}ms  {row['intent']:<15} {row['intent_confidence']:.2f}  {row['tickers']}  | {row['query']}")
    print(f"passed {report['passed']}/{report['n']}  intent {report['intent_correct']}/{report['n']}  "
          f"tickers {report['tickers_correct']}/{report['n']}  p50 {report['p50_ms']:.1f}ms  p95 {report['p95_ms']:.1f}ms  load {report['load_s']}s")
    return 0 if report["passed"] == report["n"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
