"""Manual, read-only outer-loop shadow replay over saved Cofinance traces (stdlib only).

Replays real local traces offline using their recorded tool facts and model
outputs via ReplayLLM, compares all six inner-loop checkpoints, reports exact
replayability coverage, and maps privacy-safe diff fingerprints against an
optional human-reviewed registry. Never schedules runs, touches the network,
or mutates live state.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core.gliner_decide import RoutedQuery
from core.orchestrator import (
    DataAnalystAgent,
    DocumentReaderAgent,
    NewsResearcherAgent,
    TeamLeadOrchestrator,
)
from core.run_trace import TRACE_INTEGRITY_ISSUES, TraceRecorder, trace_integrity_issue
from core.signals import Action, Item, Judgment, Outcome
from qualify.parity_oracle import (
    STAGES,
    DiffFingerprint,
    ReplayLLM,
    ScriptedNewsStore,
    ScriptedTools,
    _make_fingerprint,
    _trace_orchestrator,
    check_invariants,
    compare_checkpoints,
    extract_checkpoints,
    group_fingerprints,
)

INCOMPLETE_REASONS = TRACE_INTEGRITY_ISSUES | frozenset({
    "invalid_json",
    "trace_not_object",
    "missing_spans",
    "missing_chat_query",
    "missing_route",
    "missing_specialist_facts",
    "missing_model_transcript",
    "non_research_flow",
})
NON_RESEARCH_FLOWS = frozenset({"open_conversation", "clarification", "cancelled", "error"})

REGISTRY_STATUSES = frozenset({"bug", "intentional_change", "waived"})


@dataclass(frozen=True)
class TraceReplayCase:
    case_id: str
    query: str
    context: str
    routed: RoutedQuery
    snapshots: Dict[str, Dict[str, Any]]
    news_outcomes: Dict[str, List[Outcome]]
    documents: List[Dict[str, Any]]
    narrator_responses: Tuple[str, ...]
    expected_agents: Tuple[str, ...]


class FixedRouter:
    def __init__(self, routed: RoutedQuery) -> None:
        self._routed = routed

    def route(self, query: str) -> RoutedQuery:
        return self._routed


def _trace_token(path: Path, raw_bytes: bytes) -> str:
    return "trace-" + hashlib.sha256(raw_bytes).hexdigest()[:16]


def _coerce_numeric_snapshot(raw: Mapping[str, Any]) -> Dict[str, Any]:
    snap = copy.deepcopy(dict(raw))
    for key in ("previous_session_close", "change_from_previous_session_pct", "price", "all_time_high"):
        val = snap.get(key)
        if isinstance(val, str):
            try:
                snap[key] = float(val.strip().rstrip("%"))
            except ValueError:
                pass
    return snap


def _build_news_outcomes(raw_facts: Mapping[str, Any]) -> Dict[str, List[Outcome]]:
    out: Dict[str, List[Outcome]] = {}
    for ticker, rows in raw_facts.items():
        if not isinstance(rows, list):
            continue
        bucket: List[Outcome] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            tier = str(row.get("tier") or "DIGEST").upper()
            action = Action.ALERT if tier == "ALERT" else Action.DIGEST
            mat = row.get("material_p", 0.5)
            try:
                mat_f = float(mat)
            except (TypeError, ValueError):
                mat_f = 0.5
            direction = str(row.get("direction") or "unclear").lower()
            if direction not in ("bullish", "bearish", "unclear"):
                direction = "unclear"
            item = Item(
                ticker=str(ticker),
                title=str(row.get("title") or ""),
                source=str(row.get("source") or "company_release"),
                url="",
            )
            judgment = Judgment(material=mat_f, urgent=0.0, direction=direction)
            bucket.append(Outcome(item=item, action=action, reason=f"trace_replay_{idx}", judgment=judgment))
        out[str(ticker)] = bucket
    return out


def extract_replay_case(path: Path) -> Tuple[Optional[TraceReplayCase], Optional[str]]:
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None, "invalid_json"
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "invalid_json"
    if not isinstance(data, dict):
        return None, "trace_not_object"
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    recorded_flow = str(summary.get("flow") or "")
    has_trace_envelope = any(k in data for k in ("status", "storage_errors", "dropped_spans"))
    if has_trace_envelope and (issue := trace_integrity_issue(data)):
        if issue == "trace_not_terminal" and recorded_flow in {"cancelled", "error"} and data.get("status") == "error":
            return None, "non_research_flow"
        return None, issue
    spans = data.get("spans")
    if not isinstance(spans, list) or not spans:
        return None, "missing_spans"
    if recorded_flow in NON_RESEARCH_FLOWS:
        return None, "non_research_flow"

    ir = next((s for s in spans if isinstance(s, dict) and s.get("name") == "Interactive research"), None)
    rr = next((s for s in spans if isinstance(s, dict) and s.get("name") in ("Route request", "Route conversational request")), None)

    query = ""
    context = ""
    if ir and isinstance(ir.get("inputs"), dict):
        query = str(ir["inputs"].get("query") or "").strip()
        context = str(ir["inputs"].get("conversation_context") or "")
    if not query and rr and isinstance(rr.get("inputs"), dict):
        query = str(rr["inputs"].get("query") or "").strip()
    if not query:
        return None, "missing_chat_query"

    if issue := trace_integrity_issue(data):
        return None, issue
    if not rr or not isinstance(rr.get("outputs"), dict):
        return None, "missing_route"
    rout = rr["outputs"]
    intent = rout.get("intent")
    horizon = rout.get("horizon")
    tickers = rout.get("tickers")
    if not isinstance(intent, str) or not intent or not isinstance(horizon, str) or not isinstance(tickers, list):
        return None, "missing_route"
    try:
        conf = float(rout.get("intent_confidence", 0.0))
    except (TypeError, ValueError):
        return None, "missing_route"
    routed = RoutedQuery(
        intent=intent,
        intent_confidence=conf,
        tickers=[str(t) for t in tickers],
        horizon=horizon,
    )

    da = next((s for s in spans if isinstance(s, dict) and s.get("name") == "Data Analyst"), None)
    nr = next((s for s in spans if isinstance(s, dict) and s.get("name") == "News Researcher"), None)
    dr = next((s for s in spans if isinstance(s, dict) and s.get("name") == "Document Reader"), None)

    snapshots: Dict[str, Dict[str, Any]] = {}
    if da is not None:
        da_out = da.get("outputs")
        if not isinstance(da_out, dict) or not isinstance(da_out.get("raw_facts"), dict):
            return None, "missing_specialist_facts"
        for tkr, snap in da_out["raw_facts"].items():
            if isinstance(snap, Mapping):
                snapshots[str(tkr)] = _coerce_numeric_snapshot(snap)

    news_outcomes: Dict[str, List[Outcome]] = {}
    if nr is not None:
        nr_out = nr.get("outputs")
        if not isinstance(nr_out, dict) or not isinstance(nr_out.get("raw_facts"), dict):
            return None, "missing_specialist_facts"
        news_outcomes = _build_news_outcomes(nr_out["raw_facts"])

    documents: List[Dict[str, Any]] = []
    if dr is not None:
        dr_out = dr.get("outputs")
        if not isinstance(dr_out, dict) or not isinstance(dr_out.get("raw_facts"), dict):
            return None, "missing_specialist_facts"
        excerpts = dr_out["raw_facts"].get("excerpts")
        if not isinstance(excerpts, list):
            return None, "missing_specialist_facts"
        by_doc: Dict[Tuple[str, str], List[str]] = {}
        for ex in excerpts:
            if not isinstance(ex, Mapping):
                continue
            key = (str(ex.get("document_id") or "doc"), str(ex.get("name") or "document.pdf"))
            by_doc.setdefault(key, []).append(str(ex.get("text") or ""))
        for (doc_id, name), pages in by_doc.items():
            documents.append({"id": doc_id, "name": name, "pages": pages, "raw_pages": list(pages)})

    if da is None and nr is None and dr is None:
        return None, "missing_specialist_facts"

    narr_spans = [s for s in spans if isinstance(s, dict) and s.get("name") == "Narrate evidence"]
    transcript: List[str] = []
    for ns in narr_spans:
        out = ns.get("outputs")
        if not isinstance(out, str):
            return None, "missing_model_transcript"
        transcript.append(out)

    needs_model = da is not None or dr is not None
    if needs_model and not transcript:
        return None, "missing_model_transcript"

    expected_agents: List[str] = []
    if da is not None:
        expected_agents.append("Data Analyst")
    if nr is not None:
        expected_agents.append("News Researcher")
    if dr is not None:
        expected_agents.append("Document Reader")

    return (
        TraceReplayCase(
            case_id=_trace_token(path, raw_bytes),
            query=query,
            context=context,
            routed=routed,
            snapshots=snapshots,
            news_outcomes=news_outcomes,
            documents=documents,
            narrator_responses=tuple(transcript),
            expected_agents=tuple(expected_agents),
        ),
        None,
    )


class StrictReplayLLM(ReplayLLM):
    def complete(self, system: str, prompt: str, *, max_tokens: int = 256, **options: Any) -> str:
        if not self.responses:
            raise RuntimeError("replay_unexpected_model_call")
        if self._index >= len(self.responses):
            raise RuntimeError("replay_transcript_exhausted")
        return super().complete(system, prompt, max_tokens=max_tokens, **options)


def default_trace_factory(case: TraceReplayCase) -> Tuple[TeamLeadOrchestrator, ScriptedTools, ReplayLLM]:
    router = FixedRouter(case.routed)
    tools = ScriptedTools(copy.deepcopy(case.snapshots))
    store = ScriptedNewsStore(copy.deepcopy(case.news_outcomes))
    pairs = [(text, None) for text in case.narrator_responses]
    llm = StrictReplayLLM(pairs, disabled=not pairs)
    orch = TeamLeadOrchestrator(
        router,
        DataAnalystAgent(llm, tools),
        NewsResearcherAgent(llm, store),
        llm,
        tools,
        document_agent=DocumentReaderAgent(),
    )
    return orch, tools, llm


TraceFactory = Callable[[TraceReplayCase], Tuple[TeamLeadOrchestrator, ScriptedTools, ReplayLLM]]


def _run_with_network_fence(fn: Callable[[], Any]) -> Any:
    orig_getaddrinfo = socket.getaddrinfo
    orig_create_connection = socket.create_connection

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("forbidden_network: outer-loop replay cannot open network connections")

    socket.getaddrinfo = blocked
    socket.create_connection = blocked
    try:
        return fn()
    finally:
        socket.getaddrinfo = orig_getaddrinfo
        socket.create_connection = orig_create_connection


def run_trace_case(factory: TraceFactory, case: TraceReplayCase) -> Dict[str, Any]:
    def execute() -> Dict[str, Any]:
        orch, tools, llm = factory(case)
        trace = TraceRecorder(f"outer:{case.case_id}", mode="replay", directory=None)
        traced_orch = _trace_orchestrator(orch, trace)
        try:
            with trace.span(
                "Interactive research",
                "run",
                inputs={
                    "query": case.query,
                    "tool_side_effects": "same as original orchestrator",
                    "conversation_context": "previous accepted answer" if case.context else "none",
                },
            ) as span:
                result = traced_orch.run(
                    case.query,
                    session_id=case.case_id,
                    fallback_tickers=case.routed.tickers,
                    context=case.context,
                    documents=case.documents or None,
                )
                span.update(
                    output={"answer": result.final_synthesis, "agents": result.delegated_agents},
                    fallback=not result.faithful_synthesis,
                )
            trace.finish(summary={"answer": result.final_synthesis, "risk_mode": result.risk_mode})
        except BaseException:
            trace.finish(error=True)
            raise
        return extract_checkpoints(
            result,
            trace.data,
            watchlist_writes=tools.watchlist_writes,
            llm_calls=llm.calls,
        )

    return _run_with_network_fence(execute)


def load_registry(path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    if path is None:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Registry must be a JSON object with schema_version=1")
    entries = raw.get("fingerprints")
    if not isinstance(entries, dict):
        raise ValueError("Registry fingerprints field must be an object")
    clean: Dict[str, Dict[str, Any]] = {}
    for fp, meta in entries.items():
        if not isinstance(meta, Mapping):
            raise ValueError(f"Invalid registry entry for {fp}")
        status = meta.get("status")
        if status not in REGISTRY_STATUSES:
            raise ValueError(f"Unsupported registry status {status!r} for {fp}")
        approved = bool(meta.get("human_approved") is True)
        clean[str(fp)] = {"status": status, "human_approved": approved}
    return clean


def replay_directory(
    traces_dir: Path,
    *,
    baseline_factory: TraceFactory = default_trace_factory,
    candidate_factory: TraceFactory = default_trace_factory,
    registry_in: Optional[Path] = None,
) -> Dict[str, Any]:
    root = Path(traces_dir)
    registry = load_registry(registry_in)
    files = sorted(p for p in root.glob("*.json") if p.is_file())

    incomplete_by_reason: Dict[str, int] = {}
    flow_counts: Dict[str, int] = {}
    replayable_cases: List[TraceReplayCase] = []
    for fp in files:
        try:
            raw_obj = json.loads(fp.read_bytes().decode("utf-8"))
            if isinstance(raw_obj, dict) and isinstance(raw_obj.get("summary"), dict):
                f = raw_obj["summary"].get("flow")
                if isinstance(f, str) and f:
                    flow_counts[f] = flow_counts.get(f, 0) + 1
        except (OSError, UnicodeDecodeError, ValueError):
            pass
        case, reason = extract_replay_case(fp)
        if case is None:
            key = reason if reason in INCOMPLETE_REASONS else "invalid_json"
            incomplete_by_reason[key] = incomplete_by_reason.get(key, 0) + 1
        else:
            replayable_cases.append(case)

    matched = 0
    mismatched = 0
    diff_records: List[Dict[str, Any]] = []
    invariant_failures = 0

    for case in replayable_cases:
        try:
            base_cp = run_trace_case(baseline_factory, case)
        except Exception as exc:
            mismatched += 1
            err_kind = "forbidden_network" if isinstance(exc, PermissionError) and "forbidden_network" in str(exc) else type(exc).__name__
            fp = _make_fingerprint("trace", "/baseline_error", err_kind, 0, err_kind, "")
            diff_records.append({"case_id": case.case_id, "diff": fp.as_dict()})
            continue

        try:
            cand_cp = run_trace_case(candidate_factory, case)
        except Exception as exc:
            mismatched += 1
            err_kind = "forbidden_network" if isinstance(exc, PermissionError) and "forbidden_network" in str(exc) else type(exc).__name__
            fp = _make_fingerprint("trace", "/candidate_error", err_kind, 0, "", err_kind)
            diff_records.append({"case_id": case.case_id, "diff": fp.as_dict()})
            continue

        inv_errors = check_invariants(cand_cp)
        diff = compare_checkpoints(base_cp, cand_cp)
        if inv_errors:
            invariant_failures += 1
            mismatched += 1
            code = inv_errors[0].split(":", 1)[0]
            fp = _make_fingerprint("guards", "/invariants", "safety_invariant_failed", 0, "", code)
            diff_records.append({"case_id": case.case_id, "diff": fp.as_dict()})
        elif diff is not None:
            mismatched += 1
            diff_records.append({"case_id": case.case_id, "diff": diff.as_dict()})
        else:
            matched += 1

    raw_groups = group_fingerprints(diff_records)
    annotated_groups: List[Dict[str, Any]] = []
    unapproved_mismatches = 0
    for grp in raw_groups:
        fp_id = grp["fingerprint_id"]
        reg = registry.get(fp_id)
        if reg is None:
            reg_status = "unclassified"
            human_approved = False
        else:
            reg_status = reg["status"]
            human_approved = reg["human_approved"]
        effective_waived = reg_status in ("intentional_change", "waived") and human_approved
        if not effective_waived:
            unapproved_mismatches += grp["count"]
        annotated_groups.append({
            "fingerprint": fp_id,
            "count": grp["count"],
            "stage": grp["stage"],
            "field": grp["field"],
            "kind": grp["kind"],
            "first_diff": grp["first_diff"],
            "registry_status": reg_status,
            "human_approved": human_approved,
            "effective_waived": effective_waived,
        })

    return {
        "schema_version": 1,
        "checkpoint_stages": list(STAGES),
        "coverage": {
            "total_traces": len(files),
            "replayable": len(replayable_cases),
            "incomplete_by_reason": dict(sorted(incomplete_by_reason.items())),
            "flow_counts": dict(sorted(flow_counts.items())),
            "matched": matched,
            "mismatched": mismatched,
            "unapproved_mismatches": unapproved_mismatches,
            "invariant_failures": invariant_failures,
            "fingerprint_groups": len(annotated_groups),
        },
        "groups": annotated_groups,
    }


def replay_exit_code(report: Mapping[str, Any], *, fail_on_mismatch: bool) -> int:
    coverage = report["coverage"]
    return int(fail_on_mismatch and (
        not coverage["replayable"]
        or coverage["unapproved_mismatches"] > 0
        or bool(TRACE_INTEGRITY_ISSUES.intersection(coverage["incomplete_by_reason"]))
    ))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="qualify.outer_loop")
    ap.add_argument("--traces", default="data/traces")
    ap.add_argument("--registry-in", default=None)
    ap.add_argument("--fail-on-mismatch", action="store_true",
                    help="Fail on an unapproved diff, unverified source trace, or no replayable cases.")
    args = ap.parse_args(argv)

    report = replay_directory(
        Path(args.traces),
        registry_in=Path(args.registry_in) if args.registry_in else None,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return replay_exit_code(report, fail_on_mismatch=args.fail_on_mismatch)


if __name__ == "__main__":
    raise SystemExit(main())
