"""Frozen 30-follow-up benchmark: LocalKeywordMemory (FTS5) vs HybridMemory (FTS5 + HyMemoryLite).

Runs strictly in an isolated temporary store without touching data/chat_v2.db or
data/hymemory/store. Protocol is frozen and hashed before execution (`freeze`), then
run once (`run`) with a one-look lock (`started.json`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core.memory import HybridMemory, LocalKeywordMemory
from qualify.run import pct

FORMAT = "cofinance-memory-benchmark/1"
DEFAULT_CASES = "qualify/memory_benchmark_cases.json"
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261126
RECALL_LIMIT = 2


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_suite(path: Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "cofinance-memory-benchmark-cases/1":
        raise ValueError(f"Unsupported cases format: {data.get('format')!r}")
    followups = data.get("followups") or []
    if len(followups) != 30:
        raise ValueError(f"Expected 30 followups, got {len(followups)}")
    return data


def score_arm(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    pos = [r for r in rows if r["category"] in ("lexical", "paraphrase")]
    lex = [r for r in rows if r["category"] == "lexical"]
    para = [r for r in rows if r["category"] == "paraphrase"]
    neg = [r for r in rows if r["category"] == "negative"]

    def _agg(group: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        n = len(group)
        if not n:
            return {"n": 0, "recall_at_1": None, "recall_at_2": None, "mrr": None}
        return {
            "n": n,
            "recall_at_1": round(sum(1 for r in group if r["hit_at_1"]) / n, 4),
            "recall_at_2": round(sum(1 for r in group if r["hit_at_2"]) / n, 4),
            "mrr": round(sum(float(r["rr"]) for r in group) / n, 4),
        }

    ms_vals = [float(r.get("ms", 0.0)) for r in rows]
    errors = sum(1 for r in rows if r.get("error"))
    neg_hits = sum(1 for r in neg if r.get("returned_run_ids"))
    cross_session_leaks = sum(int(r.get("cross_session_leaks", 0)) for r in rows)
    return {
        "n_total": len(rows),
        "errors": errors,
        "positive": _agg(pos),
        "lexical": _agg(lex),
        "paraphrase": _agg(para),
        "negative": {
            "n": len(neg),
            "false_hit_count": neg_hits,
            "false_hit_rate": round(neg_hits / len(neg), 4) if neg else 0.0,
            "clean_abstention_rate": round((len(neg) - neg_hits) / len(neg), 4) if neg else 1.0,
        },
        "cross_session_leaks": cross_session_leaks,
        "p50_ms": round(pct(ms_vals, 0.5), 2) if ms_vals else 0.0,
        "p95_ms": round(pct(ms_vals, 0.95), 2) if ms_vals else 0.0,
    }


def paired_bootstrap_memory(
    kw_rows: Sequence[Mapping[str, Any]],
    hyb_rows: Sequence[Mapping[str, Any]],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    kw_pos = [r for r in kw_rows if r["category"] in ("lexical", "paraphrase")]
    hyb_pos = [r for r in hyb_rows if r["category"] in ("lexical", "paraphrase")]
    n = len(kw_pos)
    rng = random.Random(seed)
    diffs: Dict[str, List[float]] = {"recall_at_1": [], "recall_at_2": [], "mrr": []}
    for _ in range(resamples):
        idxs = [rng.randrange(n) for _ in range(n)]
        r1_d = sum((1 if hyb_pos[i]["hit_at_1"] else 0) - (1 if kw_pos[i]["hit_at_1"] else 0) for i in idxs) / n
        r2_d = sum((1 if hyb_pos[i]["hit_at_2"] else 0) - (1 if kw_pos[i]["hit_at_2"] else 0) for i in idxs) / n
        mrr_d = sum(float(hyb_pos[i]["rr"]) - float(kw_pos[i]["rr"]) for i in idxs) / n
        diffs["recall_at_1"].append(r1_d)
        diffs["recall_at_2"].append(r2_d)
        diffs["mrr"].append(mrr_d)

    out: Dict[str, Any] = {}
    for key, metric_fn in (
        ("recall_at_1", lambda r: 1.0 if r["hit_at_1"] else 0.0),
        ("recall_at_2", lambda r: 1.0 if r["hit_at_2"] else 0.0),
        ("mrr", lambda r: float(r["rr"])),
    ):
        vals = sorted(diffs[key])
        pt = round(sum(metric_fn(hyb_pos[i]) - metric_fn(kw_pos[i]) for i in range(n)) / n, 4)
        lo = round(vals[int(0.025 * (len(vals) - 1))], 4)
        hi = round(vals[int(0.975 * (len(vals) - 1))], 4)
        out[key] = {"diff": pt, "ci95": [lo, hi]}
    return out


def split_half_memory(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    mid = len(rows) // 2
    s1 = score_arm(rows[:mid])
    s2 = score_arm(rows[mid:])
    return {
        "first_half": {"positive": s1["positive"], "negative": s1["negative"]},
        "second_half": {"positive": s2["positive"], "negative": s2["negative"]},
    }


def build_protocol(cases_path: Path = Path(DEFAULT_CASES)) -> Dict[str, Any]:
    suite = load_suite(cases_path)
    plan = {
        "format": FORMAT,
        "cases_path": cases_path.as_posix(),
        "cases_sha256": file_sha256(cases_path),
        "n_sessions": len(suite["sessions"]),
        "n_followups": len(suite["followups"]),
        "n_forget_checks": len(suite.get("forget_checks") or []),
        "recall_limit": RECALL_LIMIT,
        "rrf_k": HybridMemory.RRF_K,
        "arms": ["keyword_fts5", "hybrid_fts5_hymemory"],
        "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED},
    }
    plan["protocol_sha256"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


def freeze(out_dir: Path, cases_path: Path = Path(DEFAULT_CASES)) -> Dict[str, Any]:
    root = Path(out_dir)
    proto_path = root / "protocol.json"
    if proto_path.exists():
        raise FileExistsError(f"Protocol already frozen at {proto_path}. Create a new directory for a new run.")
    root.mkdir(parents=True, exist_ok=True)
    plan = build_protocol(cases_path)
    proto_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return plan


def verify_protocol(out_dir: Path) -> Dict[str, Any]:
    root = Path(out_dir)
    plan = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
    if plan.get("format") != FORMAT:
        raise ValueError(f"Unsupported protocol format: {plan.get('format')!r}")
    saved_sha = plan.get("protocol_sha256")
    check_copy = {k: v for k, v in plan.items() if k != "protocol_sha256"}
    recomputed = hashlib.sha256(
        json.dumps(check_copy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if saved_sha != recomputed:
        raise ValueError("Protocol SHA-256 mismatch.")
    cases_path = Path(plan["cases_path"])
    if not cases_path.exists() or file_sha256(cases_path) != plan["cases_sha256"]:
        raise ValueError("Cases SHA-256 changed after protocol freeze.")
    return plan


def _eval_followups(
    port: Any,
    followups: Sequence[Mapping[str, Any]],
    session_runs: Mapping[str, set],
    *,
    limit: int = RECALL_LIMIT,
    clock: Callable[[], float] = time.perf_counter,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for fu in followups:
        sid = str(fu["session_id"])
        expected = list(fu["expected_run_ids"])
        t0 = clock()
        err = None
        try:
            hits = port.recall("chat", sid, str(fu["query"]), limit=limit)
        except Exception as exc:
            hits = []
            err = f"{type(exc).__name__}: {exc}"
        elapsed_ms = round((clock() - t0) * 1000.0, 2)
        returned = [h.run_id for h in hits if h.run_id]
        cross_leaks = sum(1 for rid in returned if rid not in session_runs.get(sid, set()))
        hit_at_1 = bool(expected and returned and returned[0] in expected)
        hit_at_2 = bool(expected and any(rid in expected for rid in returned[:2]))
        rr = 0.0
        for rank, rid in enumerate(returned[:limit], 1):
            if rid in expected:
                rr = 1.0 / rank
                break
        rows.append({
            "id": str(fu["id"]),
            "session_id": sid,
            "category": str(fu["category"]),
            "expected_run_ids": expected,
            "returned_run_ids": returned,
            "sources": [list(h.sources) for h in hits],
            "hit_at_1": hit_at_1,
            "hit_at_2": hit_at_2,
            "rr": round(rr, 4),
            "cross_session_leaks": cross_leaks,
            "error": err,
            "ms": elapsed_ms,
        })
    return rows


def run_frozen(
    out_dir: Path,
    *,
    semantic_factory: Optional[Callable[[Path], Any]] = None,
) -> Dict[str, Any]:
    root = Path(out_dir)
    plan = verify_protocol(root)
    started_path = root / "started.json"
    if started_path.exists():
        raise FileExistsError(f"One-look guard: {started_path} already exists. Create a new folder for a new run.")
    started_path.write_text(
        json.dumps({"protocol_sha256": plan["protocol_sha256"], "started_epoch": time.time()}, indent=2) + "\n",
        encoding="utf-8",
    )

    suite = load_suite(Path(plan["cases_path"]))
    tmp_dir = Path(tempfile.mkdtemp(prefix="cof_mem_bench_"))
    semantic = None
    try:
        kw = LocalKeywordMemory(tmp_dir / "keyword.db")
        if semantic_factory is None:
            from core.hymemory_port import HyMemoryLite

            semantic = HyMemoryLite(Path.cwd(), Path.cwd() / "data", store=tmp_dir / "hymemory")
            semantic_backend = semantic.backend
        else:
            semantic = semantic_factory(tmp_dir)
            semantic_backend = getattr(semantic, "backend", "injected_semantic")
        hyb = HybridMemory(kw, semantic)

        session_runs: Dict[str, set] = {}
        t_ingest = time.perf_counter()
        for sess in suite["sessions"]:
            sid = str(sess["session_id"])
            session_runs[sid] = set()
            for turn in sess["turns"]:
                rid = str(turn["run_id"])
                session_runs[sid].add(rid)
                hyb.remember_turn(
                    "chat",
                    sid,
                    str(turn["question"]),
                    str(turn["answer"]),
                    run_id=rid,
                    tickers=turn.get("tickers") or (),
                )
        ingest_s = round(time.perf_counter() - t_ingest, 2)

        kw_rows = _eval_followups(kw, suite["followups"], session_runs, limit=plan["recall_limit"])
        hyb_rows = _eval_followups(hyb, suite["followups"], session_runs, limit=plan["recall_limit"])

        forget_results: List[Dict[str, Any]] = []
        for fc in suite.get("forget_checks") or []:
            sid = str(fc["session_id"])
            rid = str(fc["forget_run_id"])
            kw.delete("chat", sid, run_id=rid)
            if hasattr(semantic, "memory_id_for_run"):
                mid = semantic.memory_id_for_run(rid, sid)
                if mid:
                    semantic.delete("chat", sid, mid)
            elif hasattr(semantic, "delete"):
                semantic.delete("chat", sid, run_id=rid)
            kw_after = [h.run_id for h in kw.recall("chat", sid, str(fc["query"]), limit=5)]
            hyb_after = [h.run_id for h in hyb.recall("chat", sid, str(fc["query"]), limit=5)]
            forget_results.append({
                "session_id": sid,
                "forget_run_id": rid,
                "keyword_leaked": rid in kw_after,
                "hybrid_leaked": rid in hyb_after,
            })

        kw_score = score_arm(kw_rows)
        hyb_score = score_arm(hyb_rows)
        paired = paired_bootstrap_memory(kw_rows, hyb_rows)
        forget_leaks = sum(1 for f in forget_results if f["keyword_leaked"] or f["hybrid_leaked"])

        report = {
            "warning": "Experimental one-look memory benchmark on 30 authored, subagent-reviewed follow-ups (not live user chats).",
            "format": FORMAT,
            "protocol_sha256": plan["protocol_sha256"],
            "cases_sha256": plan["cases_sha256"],
            "cases_path": plan["cases_path"],
            "semantic_backend": semantic_backend,
            "ingest_s": ingest_s,
            "arms": {
                "keyword_fts5": kw_score,
                "hybrid_fts5_hymemory": hyb_score,
            },
            "splits": {
                "keyword_fts5": split_half_memory(kw_rows),
                "hybrid_fts5_hymemory": split_half_memory(hyb_rows),
            },
            "paired_hybrid_minus_keyword": paired,
            "forget_checks": {
                "total": len(forget_results),
                "leaks": forget_leaks,
                "rows": forget_results,
            },
            "limits": [
                "30 authored, subagent-reviewed follow-ups across 6 illustrative sessions, not live Baris chat_v2.db history (which remains 0/30)",
                "Paraphrase queries were intentionally designed to avoid lexical overlap with stored turns, highlighting semantic retrieval upside",
                "Measures recall accuracy and latency in isolation, not downstream narrator answer quality",
            ],
        }
        outputs = {
            "protocol_sha256": plan["protocol_sha256"],
            "keyword_fts5": kw_rows,
            "hybrid_fts5_hymemory": hyb_rows,
            "forget_checks": forget_results,
        }
        (root / "outputs.json").write_text(json.dumps(outputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report
    finally:
        if semantic is not None and hasattr(semantic, "close"):
            semantic.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="qualify.memory_benchmark")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_fr = sub.add_parser("freeze")
    p_fr.add_argument("out_dir")
    p_fr.add_argument("--cases", default=DEFAULT_CASES)
    p_rn = sub.add_parser("run")
    p_rn.add_argument("out_dir")
    a = ap.parse_args(argv)
    if a.cmd == "freeze":
        plan = freeze(Path(a.out_dir), Path(a.cases))
        print(f"protocol {plan['protocol_sha256']}")
        return 0
    rep = run_frozen(Path(a.out_dir))
    print(json.dumps({"arms": rep["arms"], "paired": rep["paired_hybrid_minus_keyword"], "forget_checks": rep["forget_checks"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
