"""First-pass guard acceptance telemetry from observed local traces.

Counts narrated guard checks, pass-1 acceptance, guard retries, follow-up brevity retries,
fallbacks, wasted narrator passes, wasted latency, and reported wasted tokens.
Never estimates missing token receipts and never copies raw queries, replies, or rejected text.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .usage import counts as usage_counts

SCHEMA_VERSION = 1
GUARD_GATE_NAMES = frozenset({"Number faithfulness", "Synthesis faithfulness"})
KNOWN_BYPASS_METHODS = frozenset({"single_pass_data_answer", "python_news_answer", "python_digest", "no_news"})
KNOWN_ACTORS = frozenset({"Data Analyst", "News Researcher", "Document Reader", "Market Team Lead"})
SAFE_REASON_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")


def _safe_actor(value: Any) -> str:
    if isinstance(value, str) and value in KNOWN_ACTORS:
        return value
    return "Other"


def _parse_ms(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _sorted_counts(mapping: Mapping[str, int]) -> Dict[str, int]:
    return {k: mapping[k] for k in sorted(mapping, key=lambda item: (-mapping[item], item))}


def _add_reason(reasons: Dict[str, int], reason: Any) -> None:
    if isinstance(reason, str):
        cleaned = reason.strip()
        key = cleaned if SAFE_REASON_RE.match(cleaned) else ("other" if cleaned else "unknown")
    else:
        key = "unknown"
    reasons[key] = reasons.get(key, 0) + 1


def _empty_actor_row(actor: str) -> Dict[str, Any]:
    return {
        "actor": actor,
        "narrated_checks": 0,
        "first_pass_accepted": 0,
        "first_pass_acceptance_rate": None,
        "guard_retries": 0,
        "guard_retry_recovered": 0,
        "follow_up_retries": 0,
        "fallbacks": 0,
        "total_passes": 0,
        "wasted_passes": 0,
        "total_ms": 0.0,
        "wasted_ms": 0.0,
        "failure_reasons": {},
    }


def _select_wasted_spans(
    model_spans: Sequence[Mapping[str, Any]],
    *,
    first_pass_accepted: bool,
    fallback: bool,
    guard_retry: Optional[Mapping[str, Any]],
    follow_up_retry: Optional[Mapping[str, Any]],
) -> tuple[List[Mapping[str, Any]], bool]:
    if not model_spans:
        return [], False
    if fallback:
        return list(model_spans), False
    if guard_retry is not None:
        if len(model_spans) >= 2:
            return list(model_spans[:-1]), False
        return [], True
    if follow_up_retry is not None:
        used = follow_up_retry.get("used")
        if len(model_spans) >= 2:
            return ([model_spans[0]] if used == "retry" else [model_spans[-1]]), False
        return [], True
    if not first_pass_accepted:
        return list(model_spans), False
    return [], False


def summarize_guards(spans: Sequence[Mapping[str, Any]], dropped: int = 0) -> Dict[str, Any]:
    """Summarize first-pass guard acceptance and wasted narrator passes for one trace's spans."""
    model_by_actor: Dict[str, List[Mapping[str, Any]]] = {}
    gate_by_actor: Dict[str, List[Mapping[str, Any]]] = {}
    tool_only_actors = set()

    for span in spans or ():
        if not isinstance(span, Mapping):
            continue
        kind = span.get("kind")
        name = span.get("name")
        actor = _safe_actor(span.get("actor"))
        if kind == "model" and name == "Narrate evidence":
            model_by_actor.setdefault(actor, []).append(span)
        elif kind == "gate" and name in GUARD_GATE_NAMES:
            gate_by_actor.setdefault(actor, []).append(span)
        elif kind == "decision" and name == "Tool-only report":
            tool_only_actors.add(actor)

    by_actor: Dict[str, Dict[str, Any]] = {}
    failure_reasons: Dict[str, int] = {}
    bypass_methods: Dict[str, int] = {}
    unmatched_narrator_passes = 0
    span_mismatch = False
    missing_duration = False

    wasted_tok_in = 0
    wasted_tok_out = 0
    wasted_tok_total = 0
    wasted_tok_reported = 0

    for actor, gates in gate_by_actor.items():
        if len(gates) > 1:
            span_mismatch = True
        gate = gates[-1]
        out = gate.get("outputs") if isinstance(gate.get("outputs"), Mapping) else {}
        actor_models = model_by_actor.get(actor, [])
        method = out.get("method")
        val_reason = out.get("validation_reason")
        guard_retry = out.get("guard_retry") if isinstance(out.get("guard_retry"), Mapping) else None
        follow_up_retry = out.get("follow_up_retry") if isinstance(out.get("follow_up_retry"), Mapping) else None

        if not actor_models:
            if guard_retry is not None or follow_up_retry is not None:
                span_mismatch = True
                continue
            if actor in tool_only_actors:
                key = "tool_only"
            elif isinstance(method, str) and method in KNOWN_BYPASS_METHODS:
                key = method
            elif isinstance(val_reason, str) and val_reason in KNOWN_BYPASS_METHODS:
                key = val_reason
            else:
                key = "deterministic_no_narrator"
            bypass_methods[key] = bypass_methods.get(key, 0) + 1
            continue

        row = by_actor.setdefault(actor, _empty_actor_row(actor))
        row["narrated_checks"] += 1
        row["total_passes"] += len(actor_models)
        actor_total_ms = 0.0
        for sp in actor_models:
            ms = _parse_ms(sp.get("duration_ms"))
            if ms is None:
                missing_duration = True
            else:
                actor_total_ms += ms
        row["total_ms"] = round(row["total_ms"] + actor_total_ms, 3)

        is_synth = gate.get("name") == "Synthesis faithfulness"

        if is_synth:
            passed_final = out.get("faithful_synthesis") is True
        else:
            passed_final = (out.get("passed") is True) and (out.get("fallback_used") is not True)

        if guard_retry is not None:
            first_pass_accepted = False
            row["guard_retries"] += 1
            recovered = passed_final
            fallback = not recovered
            if recovered:
                row["guard_retry_recovered"] += 1
            else:
                row["fallbacks"] += 1
            reason = guard_retry.get("reason") or val_reason
            _add_reason(failure_reasons, reason)
            _add_reason(row["failure_reasons"], reason)
        elif follow_up_retry is not None:
            first_pass_accepted = True
            row["first_pass_accepted"] += 1
            row["follow_up_retries"] += 1
            fallback = not passed_final
            if fallback:
                row["fallbacks"] += 1
                _add_reason(failure_reasons, val_reason)
                _add_reason(row["failure_reasons"], val_reason)
        elif passed_final:
            first_pass_accepted = True
            row["first_pass_accepted"] += 1
            fallback = False
        else:
            first_pass_accepted = False
            fallback = True
            row["fallbacks"] += 1
            _add_reason(failure_reasons, val_reason)
            _add_reason(row["failure_reasons"], val_reason)

        wasted_spans, mismatch = _select_wasted_spans(
            actor_models,
            first_pass_accepted=first_pass_accepted,
            fallback=fallback,
            guard_retry=guard_retry,
            follow_up_retry=follow_up_retry,
        )
        if mismatch:
            span_mismatch = True
        row["wasted_passes"] += len(wasted_spans)
        wasted_actor_ms = 0.0
        for sp in wasted_spans:
            ms = _parse_ms(sp.get("duration_ms"))
            if ms is None:
                missing_duration = True
            else:
                wasted_actor_ms += ms
            u = usage_counts(sp.get("usage"))
            if u is not None:
                wasted_tok_reported += 1
                wasted_tok_in += u["input"]
                wasted_tok_out += u["output"]
                wasted_tok_total += u["total"]
        row["wasted_ms"] = round(row["wasted_ms"] + wasted_actor_ms, 3)

    for actor, actor_models in model_by_actor.items():
        if actor not in gate_by_actor:
            unmatched_narrator_passes += len(actor_models)

    actor_rows: List[Dict[str, Any]] = []
    for actor in sorted(by_actor):
        row = by_actor[actor]
        checks = row["narrated_checks"]
        row["first_pass_acceptance_rate"] = round(row["first_pass_accepted"] / checks, 4) if checks else None
        row["failure_reasons"] = _sorted_counts(row["failure_reasons"])
        actor_rows.append(row)

    narrated_checks = sum(r["narrated_checks"] for r in actor_rows)
    first_pass_accepted = sum(r["first_pass_accepted"] for r in actor_rows)
    guard_retries = sum(r["guard_retries"] for r in actor_rows)
    guard_retry_recovered = sum(r["guard_retry_recovered"] for r in actor_rows)
    follow_up_retries = sum(r["follow_up_retries"] for r in actor_rows)
    fallbacks = sum(r["fallbacks"] for r in actor_rows)
    total_passes = sum(r["total_passes"] for r in actor_rows)
    wasted_passes = sum(r["wasted_passes"] for r in actor_rows)
    total_ms = round(sum(r["total_ms"] for r in actor_rows), 3)
    wasted_ms = round(sum(r["wasted_ms"] for r in actor_rows), 3)
    duration_complete = (not missing_duration) and (not dropped) and (unmatched_narrator_passes == 0) and (not span_mismatch)
    trace_complete = duration_complete

    return {
        "version": SCHEMA_VERSION,
        "narrated_checks": narrated_checks,
        "first_pass_accepted": first_pass_accepted,
        "first_pass_acceptance_rate": round(first_pass_accepted / narrated_checks, 4) if narrated_checks else None,
        "guard_retries": guard_retries,
        "guard_retry_recovered": guard_retry_recovered,
        "follow_up_retries": follow_up_retries,
        "fallbacks": fallbacks,
        "fallback_rate": round(fallbacks / narrated_checks, 4) if narrated_checks else None,
        "total_narrator_passes": total_passes,
        "wasted_passes": wasted_passes,
        "wasted_pass_rate": round(wasted_passes / total_passes, 4) if total_passes else None,
        "total_narrator_ms": total_ms,
        "wasted_ms": wasted_ms,
        "wasted_ms_share": round(wasted_ms / total_ms, 4) if total_ms > 0 else None,
        "wasted_ms_complete": duration_complete,
        "wasted_tokens": {
            "input": wasted_tok_in,
            "output": wasted_tok_out,
            "total": wasted_tok_total,
            "reported": wasted_tok_reported,
            "passes": wasted_passes,
            "complete": wasted_tok_reported == wasted_passes and trace_complete,
        },
        "failure_reasons": _sorted_counts(failure_reasons),
        "bypass_methods": _sorted_counts(bypass_methods),
        "by_actor": actor_rows,
        "unmatched_narrator_passes": unmatched_narrator_passes,
        "complete": trace_complete,
    }


def _is_chat_trace(trace_data: Mapping[str, Any]) -> bool:
    spans = trace_data.get("spans")
    if not isinstance(spans, list):
        return False
    return any(
        isinstance(s, Mapping)
        and (
            (s.get("kind") == "run" and s.get("name") == "Interactive research")
            or (s.get("kind") == "gate" and s.get("name") in GUARD_GATE_NAMES)
            or (s.get("kind") == "model" and s.get("name") == "Narrate evidence")
        )
        for s in spans
    )


def summarize_guard_directory(
    traces_dir: str | Path,
    *,
    mode: str = "all",
) -> Dict[str, Any]:
    """Read-only aggregation of first-pass guard acceptance across saved trace files."""
    if mode not in {"all", "live", "fixture", "replay"}:
        raise ValueError("mode must be all, live, fixture, or replay")
    root = Path(traces_dir)
    files = sorted(root.glob("*.json")) if root.exists() else []

    unreadable_files = 0
    skipped_by_mode = 0
    non_chat_traces = 0
    chat_traces = 0
    incomplete_traces = 0
    mode_counts: Dict[str, int] = {}

    by_actor: Dict[str, Dict[str, Any]] = {}
    failure_reasons: Dict[str, int] = {}
    bypass_methods: Dict[str, int] = {}

    wasted_tok_in = 0
    wasted_tok_out = 0
    wasted_tok_total = 0
    wasted_tok_reported = 0
    unmatched_narrator_passes = 0

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable_files += 1
            continue
        if not isinstance(data, dict) or not isinstance(data.get("spans"), list):
            unreadable_files += 1
            continue
        trace_mode = str(data.get("mode") or "unknown")
        if mode != "all" and trace_mode != mode:
            skipped_by_mode += 1
            continue
        if not _is_chat_trace(data):
            non_chat_traces += 1
            continue

        chat_traces += 1
        mode_counts[trace_mode] = mode_counts.get(trace_mode, 0) + 1
        dropped = int(data.get("dropped_spans") or 0)
        summary = summarize_guards(data["spans"], dropped=dropped)
        if not summary["complete"]:
            incomplete_traces += 1
        unmatched_narrator_passes += summary["unmatched_narrator_passes"]

        for k, v in summary["failure_reasons"].items():
            failure_reasons[k] = failure_reasons.get(k, 0) + v
        for k, v in summary["bypass_methods"].items():
            bypass_methods[k] = bypass_methods.get(k, 0) + v

        wt = summary["wasted_tokens"]
        wasted_tok_in += wt["input"]
        wasted_tok_out += wt["output"]
        wasted_tok_total += wt["total"]
        wasted_tok_reported += wt["reported"]

        for arow in summary["by_actor"]:
            agg = by_actor.setdefault(arow["actor"], _empty_actor_row(arow["actor"]))
            for field in (
                "narrated_checks",
                "first_pass_accepted",
                "guard_retries",
                "guard_retry_recovered",
                "follow_up_retries",
                "fallbacks",
                "total_passes",
                "wasted_passes",
            ):
                agg[field] += arow[field]
            agg["total_ms"] = round(agg["total_ms"] + arow["total_ms"], 3)
            agg["wasted_ms"] = round(agg["wasted_ms"] + arow["wasted_ms"], 3)
            for rk, rv in arow["failure_reasons"].items():
                agg["failure_reasons"][rk] = agg["failure_reasons"].get(rk, 0) + rv

    actor_rows: List[Dict[str, Any]] = []
    for actor in sorted(by_actor):
        row = by_actor[actor]
        checks = row["narrated_checks"]
        row["first_pass_acceptance_rate"] = round(row["first_pass_accepted"] / checks, 4) if checks else None
        row["failure_reasons"] = _sorted_counts(row["failure_reasons"])
        actor_rows.append(row)

    narrated_checks = sum(r["narrated_checks"] for r in actor_rows)
    first_pass_accepted = sum(r["first_pass_accepted"] for r in actor_rows)
    guard_retries = sum(r["guard_retries"] for r in actor_rows)
    guard_retry_recovered = sum(r["guard_retry_recovered"] for r in actor_rows)
    follow_up_retries = sum(r["follow_up_retries"] for r in actor_rows)
    fallbacks = sum(r["fallbacks"] for r in actor_rows)
    total_passes = sum(r["total_passes"] for r in actor_rows)
    wasted_passes = sum(r["wasted_passes"] for r in actor_rows)
    total_ms = round(sum(r["total_ms"] for r in actor_rows), 3)
    wasted_ms = round(sum(r["wasted_ms"] for r in actor_rows), 3)

    return {
        "version": SCHEMA_VERSION,
        "mode_filter": mode,
        "coverage": {
            "total_files": len(files),
            "chat_traces": chat_traces,
            "non_chat_traces": non_chat_traces,
            "skipped_by_mode": skipped_by_mode,
            "unreadable_files": unreadable_files,
            "incomplete_traces": incomplete_traces,
            "mode_counts": _sorted_counts(mode_counts),
        },
        "narrated_checks": narrated_checks,
        "first_pass_accepted": first_pass_accepted,
        "first_pass_acceptance_rate": round(first_pass_accepted / narrated_checks, 4) if narrated_checks else None,
        "guard_retries": guard_retries,
        "guard_retry_recovered": guard_retry_recovered,
        "follow_up_retries": follow_up_retries,
        "fallbacks": fallbacks,
        "fallback_rate": round(fallbacks / narrated_checks, 4) if narrated_checks else None,
        "total_narrator_passes": total_passes,
        "wasted_passes": wasted_passes,
        "wasted_pass_rate": round(wasted_passes / total_passes, 4) if total_passes else None,
        "total_narrator_ms": total_ms,
        "wasted_ms": wasted_ms,
        "wasted_ms_share": round(wasted_ms / total_ms, 4) if total_ms > 0 else None,
        "wasted_ms_complete": incomplete_traces == 0 and unreadable_files == 0,
        "wasted_tokens": {
            "input": wasted_tok_in,
            "output": wasted_tok_out,
            "total": wasted_tok_total,
            "reported": wasted_tok_reported,
            "passes": wasted_passes,
            "complete": wasted_tok_reported == wasted_passes and incomplete_traces == 0 and unreadable_files == 0,
        },
        "failure_reasons": _sorted_counts(failure_reasons),
        "bypass_methods": _sorted_counts(bypass_methods),
        "by_actor": actor_rows,
        "unmatched_narrator_passes": unmatched_narrator_passes,
    }
