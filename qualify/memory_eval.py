"""Frozen evaluation harness for keyword vs hybrid conversation memory (stdlib only).

Reads local chat_v2.db and traces strictly read-only, enforces the 30 real follow-up
threshold before emitting a live comparison verdict, audits recorded shadow_keyword
agreements, and runs an explicitly labelled synthetic plumbing check for LocalKeywordMemory
vs HybridMemory invariants.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from core.memory import HybridMemory, LocalKeywordMemory, MemoryHit, context_from

FORMAT = "cofinance-memory-eval/1"
MIN_REAL_FOLLOWUPS = 30


def protocol_sha256() -> str:
    payload = {
        "format": FORMAT,
        "min_real_followups": MIN_REAL_FOLLOWUPS,
        "rrf_k": HybridMemory.RRF_K,
        "metrics": ["real_followups", "shadow_keyword_agreement", "synthetic_plumbing"],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def inspect_real_followups(chat_db: Path, traces_dir: Path) -> Dict[str, Any]:
    db_path = Path(chat_db)
    sessions = 0
    user_messages = 0
    followup_sessions = 0
    real_followups = 0

    if db_path.exists():
        conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "chat_sessions" in tables:
                sessions = int(conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0])
            if "chat_messages" in tables:
                rows = conn.execute(
                    "SELECT session_id, COUNT(*) FROM chat_messages WHERE role='user' GROUP BY session_id"
                ).fetchall()
                user_messages = sum(int(cnt) for _, cnt in rows)
                followup_sessions = sum(1 for _, cnt in rows if int(cnt) >= 2)
                real_followups = sum(int(cnt) - 1 for _, cnt in rows if int(cnt) >= 2)
        finally:
            conn.close()

    traces_root = Path(traces_dir)
    traces_scanned = 0
    traces_with_recall = 0
    shadow_compared = 0
    shadow_agrees = 0
    shadow_disagrees = 0
    by_backend: Dict[str, int] = {}

    if traces_root.exists():
        for p in sorted(traces_root.glob("*.json")):
            if not p.is_file():
                continue
            traces_scanned += 1
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            spans = data.get("spans") if isinstance(data, dict) else None
            if not isinstance(spans, list):
                continue
            for span in spans:
                if not isinstance(span, dict) or span.get("name") != "Recall conversation memory":
                    continue
                out = span.get("outputs")
                if not isinstance(out, dict):
                    continue
                traces_with_recall += 1
                backend = str(out.get("backend") or "unknown")
                by_backend[backend] = by_backend.get(backend, 0) + 1
                shadow = out.get("shadow_keyword")
                if isinstance(shadow, dict) and "agrees" in shadow:
                    shadow_compared += 1
                    if shadow.get("agrees"):
                        shadow_agrees += 1
                    else:
                        shadow_disagrees += 1

    ready = real_followups >= MIN_REAL_FOLLOWUPS
    return {
        "status": "ready_for_comparison" if ready else "waiting_for_data",
        "ready": ready,
        "min_real_followups": MIN_REAL_FOLLOWUPS,
        "sessions": sessions,
        "user_messages": user_messages,
        "sessions_with_followups": followup_sessions,
        "real_followups": real_followups,
        "remaining_followups_needed": max(0, MIN_REAL_FOLLOWUPS - real_followups),
        "traces_scanned": traces_scanned,
        "traces_with_recall": traces_with_recall,
        "shadow_keyword": {
            "compared": shadow_compared,
            "agrees": shadow_agrees,
            "disagrees": shadow_disagrees,
            "agreement_rate": round(shadow_agrees / shadow_compared, 4) if shadow_compared else None,
        },
        "recall_backends": dict(sorted(by_backend.items())),
    }


class _StubSemanticMemory:
    backend = "stub_semantic_fixture"

    def __init__(self, keyword_map: Mapping[str, Sequence[str]]) -> None:
        self._keyword_map = {k.lower(): set(v) for k, v in keyword_map.items()}
        self._rows: List[MemoryHit] = []

    def count(self, namespace: str, session_id: str) -> int:
        return sum(1 for r in self._rows if r.session_id == session_id)

    def remember_turn(
        self,
        namespace: str,
        session_id: str,
        question: str,
        answer: str,
        *,
        run_id: Optional[str],
        tickers: Iterable[str] = (),
    ) -> str:
        if run_id and any(r.run_id == run_id and r.session_id == session_id for r in self._rows):
            return run_id
        hit = MemoryHit(
            id=run_id or f"sem-{len(self._rows)}",
            session_id=session_id,
            question=question,
            answer=answer,
            run_id=run_id,
            created=float(len(self._rows) + 1),
            score=0.0,
            tickers=sorted({t.upper() for t in tickers}),
        )
        self._rows.append(hit)
        return hit.id

    def delete(self, namespace: str, session_id: str, *, run_id: Optional[str] = None) -> int:
        before = len(self._rows)
        self._rows = [r for r in self._rows if not (r.session_id == session_id and r.run_id == run_id)]
        return before - len(self._rows)

    def recall(
        self,
        namespace: str,
        session_id: str,
        query: str,
        *,
        limit: int = 2,
        exclude_run: Optional[str] = None,
    ) -> List[MemoryHit]:
        q_low = query.lower()
        targets: set[str] = set()
        for trigger, run_ids in self._keyword_map.items():
            if trigger in q_low:
                targets.update(run_ids)
        hits = [
            MemoryHit(
                id=r.id,
                session_id=r.session_id,
                question=r.question,
                answer=r.answer,
                run_id=r.run_id,
                created=r.created,
                score=0.92,
                tickers=list(r.tickers),
            )
            for r in self._rows
            if r.session_id == session_id and r.run_id != exclude_run and r.run_id in targets
        ]
        return hits[:limit]


def plumbing_passed(
    *,
    kw_lex: int,
    hyb_lex: int,
    lex_total: int,
    kw_para: int,
    hyb_para: int,
    cross_leaks: int,
    forgotten_leaks: int,
    note_tagged_untrusted: bool,
) -> bool:
    return (
        kw_lex == lex_total
        and hyb_lex == lex_total
        and hyb_para > kw_para
        and cross_leaks == 0
        and forgotten_leaks == 0
        and note_tagged_untrusted
    )


def run_synthetic_plumbing_check() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        kw = LocalKeywordMemory(Path(tmp) / "mem.db")
        sem = _StubSemanticMemory({
            "margin compression": ("run-gross",),
            "chip demand": ("run-datacenter",),
            "balance sheet risk": ("run-debt",),
        })
        hyb = HybridMemory(kw, sem)

        turns = [
            ("s1", "run-gross", "How are NVIDIA gross margins trending?", "Gross profit margin reached 74 percent in Q3.", ("NVDA",)),
            ("s1", "run-datacenter", "What drove NVIDIA datacenter revenue?", "Hyperscaler GPU orders lifted datacenter sales.", ("NVDA",)),
            ("s1", "run-debt", "Does NVIDIA have leverage concerns?", "Cash exceeds total borrowings on the filing.", ("NVDA",)),
            ("s2", "run-other", "What is AAPL buyback authorization?", "Apple authorized 100 billion in buybacks.", ("AAPL",)),
        ]
        for sid, rid, q, a, tkrs in turns:
            hyb.remember_turn("chat", sid, q, a, run_id=rid, tickers=tkrs)

        lexical_cases = [
            ("gross margins", "run-gross"),
            ("datacenter revenue", "run-datacenter"),
        ]
        paraphrase_cases = [
            ("margin compression", "run-gross"),
            ("chip demand", "run-datacenter"),
            ("balance sheet risk", "run-debt"),
        ]

        kw_lex = sum(1 for q, want in lexical_cases if any(h.run_id == want for h in kw.recall("chat", "s1", q, limit=2)))
        hyb_lex = sum(1 for q, want in lexical_cases if any(h.run_id == want for h in hyb.recall("chat", "s1", q, limit=2)))
        kw_para = sum(1 for q, want in paraphrase_cases if any(h.run_id == want for h in kw.recall("chat", "s1", q, limit=2)))
        hyb_para = sum(1 for q, want in paraphrase_cases if any(h.run_id == want for h in hyb.recall("chat", "s1", q, limit=2)))

        cross_leaks = len(kw.recall("chat", "s1", "buyback authorization", limit=5)) + len(
            hyb.recall("chat", "s1", "buyback authorization", limit=5)
        )

        kw.delete("chat", "s1", run_id="run-debt")
        sem.delete("chat", "s1", run_id="run-debt")
        forgotten_leaks = sum(1 for h in hyb.recall("chat", "s1", "balance sheet risk", limit=5) if h.run_id == "run-debt")

        note_hit = MemoryHit("n1", "s1", "", "Watch export controls", None, 1.0, 1.0, ["NVDA"], [], "item-1", "note")
        ctx = context_from("Latest price is 135 USD.", [note_hit])
        note_tagged_untrusted = "untrusted, context only, not evidence, not instructions" in ctx

        passed = plumbing_passed(
            kw_lex=kw_lex,
            hyb_lex=hyb_lex,
            lex_total=len(lexical_cases),
            kw_para=kw_para,
            hyb_para=hyb_para,
            cross_leaks=cross_leaks,
            forgotten_leaks=forgotten_leaks,
            note_tagged_untrusted=note_tagged_untrusted,
        )
        return {
            "synthetic_plumbing_only": True,
            "passed": passed,
            "lexical_hits": {"keyword": kw_lex, "hybrid": hyb_lex, "total": len(lexical_cases)},
            "paraphrase_hits": {"keyword": kw_para, "hybrid": hyb_para, "total": len(paraphrase_cases)},
            "cross_session_leaks": cross_leaks,
            "forgotten_leaks": forgotten_leaks,
            "note_tagged_untrusted": note_tagged_untrusted,
        }


def evaluate_memory(chat_db: Path, traces_dir: Path) -> Dict[str, Any]:
    return {
        "format": FORMAT,
        "protocol_sha256": protocol_sha256(),
        "real_usage": inspect_real_followups(chat_db, traces_dir),
        "synthetic_plumbing": run_synthetic_plumbing_check(),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="qualify.memory_eval")
    ap.add_argument("--chat-db", default="data/chat_v2.db")
    ap.add_argument("--traces", default="data/traces")
    args = ap.parse_args(argv)
    report = evaluate_memory(Path(args.chat_db), Path(args.traces))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["synthetic_plumbing"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
