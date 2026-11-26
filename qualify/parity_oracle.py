"""Black-box differential parity oracle with stage checkpoints, diff fingerprints, and pairwise cases."""
from __future__ import annotations

import hashlib
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.gliner_decide import DEFAULT_ALIASES, GlinerQueryRouter
from core.numeric_guard import numbers_in, score_numbers_faithful
from core.orchestrator import (
    DataAnalystAgent,
    DocumentReaderAgent,
    EventBus,
    NewsResearcherAgent,
    TeamLeadOrchestrator,
    TeamRunResult,
)
from core.run_trace import TraceRecorder, TracedExtractor, TracedLLM, TracedStore, TracedTools
from core.signals import Action, Item, Judgment, Outcome

SCHEMA_VERSION = 1
STAGES = ("route", "delegation", "specialist", "guards", "synthesis", "trace")
VOLATILE_SPAN_KEYS = frozenset({"id", "parent_id", "start_ms", "duration_ms"})
UNIVERSE = frozenset({"NVDA", "AAPL", "MSFT", "BTC", "ETH"})

SNAPSHOTS: Dict[str, Dict[str, Any]] = {
    "NVDA": {
        "ticker": "NVDA",
        "price": 142.5,
        "previous_close": 140.0,
        "change_pct": 1.79,
        "volume": 48200000,
        "market_cap": 3490000000000,
        "pe_ratio": 45.2,
    },
    "AAPL": {
        "ticker": "AAPL",
        "price": 228.4,
        "previous_close": 230.0,
        "change_pct": -0.70,
        "volume": 39100000,
        "market_cap": 3460000000000,
        "pe_ratio": 34.1,
    },
    "BTC": {
        "ticker": "BTC",
        "price": 91250.0,
        "previous_close": 89500.0,
        "change_pct": 1.96,
        "volume": 28400000000,
        "market_cap": 1810000000000,
    },
}

NEWS_FIXTURES: Dict[str, List[Outcome]] = {
    "NVDA": [
        Outcome(
            item=Item(
                ticker="NVDA",
                title="NVIDIA expands datacenter supply by 18 percent in Q4",
                text="Official update on datacenter shipments.",
                source="nvidia_newsroom",
                url="https://nvidianews.nvidia.com/example-q4",
            ),
            judgment=Judgment(material=0.88, urgent=0.45, direction="bullish", direction_p=0.84),
            action=Action.DIGEST,
            reason="material_digest",
        ),
    ],
    "AAPL": [
        Outcome(
            item=Item(
                ticker="AAPL",
                title="Apple announces 12 new retail locations across Europe",
                text="Retail footprint expansion.",
                source="apple_newsroom",
                url="https://www.apple.com/newsroom/example-retail",
            ),
            judgment=Judgment(material=0.72, urgent=0.20, direction="bullish", direction_p=0.71),
            action=Action.DIGEST,
            reason="material_digest",
        ),
    ],
    "BTC": [
        Outcome(
            item=Item(
                ticker="BTC",
                title="Bitcoin ETF inflows reach 410 million dollars this week",
                text="Weekly flow summary.",
                source="hackernews",
                url="https://news.ycombinator.com/item?id=1001",
            ),
            judgment=Judgment(material=0.81, urgent=0.40, direction="bullish", direction_p=0.76),
            action=Action.DIGEST,
            reason="material_digest",
        ),
    ],
}

SAMPLE_DOCUMENTS: List[Dict[str, Any]] = [
    {
        "id": "doc-fee-1",
        "name": "FeeNotice_2026.pdf",
        "pages": [
            "NVIDIA and Bitcoin custody accounts carry a 0.25 percent annual fee. First 2 transfers each month have zero fee.",
        ],
        "raw_pages": [
            "NVIDIA and Bitcoin custody accounts carry a 0.25 percent annual fee. First 2 transfers each month have zero fee.",
        ],
    }
]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def short_hash(value: Any) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


@dataclass(frozen=True)
class DiffFingerprint:
    stage: str
    field: str
    kind: str
    first_diff: Optional[int]
    baseline_len: int
    candidate_len: int
    baseline_hash: str
    candidate_hash: str
    fingerprint_id: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _make_fingerprint(
    stage: str,
    field: str,
    kind: str,
    first_diff: Optional[int],
    baseline: Any,
    candidate: Any,
) -> DiffFingerprint:
    b_len = len(baseline.encode("utf-8")) if isinstance(baseline, str) else (len(baseline) if isinstance(baseline, (list, dict)) else 1)
    c_len = len(candidate.encode("utf-8")) if isinstance(candidate, str) else (len(candidate) if isinstance(candidate, (list, dict)) else 1)
    fp_key = f"{stage}|{field}|{kind}|{first_diff}|{b_len}|{c_len}"
    return DiffFingerprint(
        stage=stage,
        field=field,
        kind=kind,
        first_diff=first_diff,
        baseline_len=b_len,
        candidate_len=c_len,
        baseline_hash=short_hash(baseline),
        candidate_hash=short_hash(candidate),
        fingerprint_id=hashlib.sha256(fp_key.encode("utf-8")).hexdigest()[:12],
    )


def _first_byte_diff(a: str, b: str) -> int:
    ab, bb = a.encode("utf-8"), b.encode("utf-8")
    for idx, (x, y) in enumerate(zip(ab, bb)):
        if x != y:
            return idx
    return min(len(ab), len(bb))


def first_diff(stage: str, baseline: Any, candidate: Any, path: str = "") -> Optional[DiffFingerprint]:
    if type(baseline) is not type(candidate):
        return _make_fingerprint(stage, path or "/", "type_changed", None, baseline, candidate)
    if isinstance(baseline, dict):
        b_keys = sorted(baseline.keys())
        c_keys = sorted(candidate.keys())
        for key in sorted(set(b_keys) | set(c_keys)):
            child_path = f"{path}/{key}"
            if key not in candidate:
                return _make_fingerprint(stage, child_path, "missing_key", None, baseline[key], None)
            if key not in baseline:
                return _make_fingerprint(stage, child_path, "extra_key", None, None, candidate[key])
            diff = first_diff(stage, baseline[key], candidate[key], child_path)
            if diff is not None:
                return diff
        return None
    if isinstance(baseline, list):
        for idx, (bv, cv) in enumerate(zip(baseline, candidate)):
            diff = first_diff(stage, bv, cv, f"{path}[{idx}]")
            if diff is not None:
                return diff
        if len(baseline) != len(candidate):
            return _make_fingerprint(stage, path or "/", "length_changed", min(len(baseline), len(candidate)), baseline, candidate)
        return None
    if isinstance(baseline, str):
        if baseline != candidate:
            return _make_fingerprint(stage, path or "/", "string_changed", _first_byte_diff(baseline, candidate), baseline, candidate)
        return None
    if baseline != candidate:
        return _make_fingerprint(stage, path or "/", "value_changed", None, baseline, candidate)
    return None


def compare_checkpoints(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> Optional[DiffFingerprint]:
    for stage in STAGES:
        if stage not in baseline or stage not in candidate:
            return _make_fingerprint(stage, "/", "missing_key", None, baseline.get(stage), candidate.get(stage))
        diff = first_diff(stage, baseline[stage], candidate[stage], "")
        if diff is not None:
            return diff
    return None


def group_fingerprints(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        fp = rec.get("diff")
        if not fp:
            continue
        fid = fp["fingerprint_id"]
        entry = groups.setdefault(
            fid,
            {
                "fingerprint_id": fid,
                "stage": fp["stage"],
                "field": fp["field"],
                "kind": fp["kind"],
                "first_diff": fp["first_diff"],
                "count": 0,
                "cases": [],
            },
        )
        entry["count"] += 1
        entry["cases"].append(rec["case_id"])
    return sorted(groups.values(), key=lambda g: (-g["count"], g["stage"], g["field"], g["fingerprint_id"]))


class ReplayExtractor:
    def __init__(self, classify_out: Mapping[str, Any], entities_out: Mapping[str, Any]) -> None:
        self.classify_out = dict(classify_out)
        self.entities_out = dict(entities_out)
        self.calls: List[Tuple[str, str]] = []

    def classify_text(self, text: str, schema: Mapping[str, Any], **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("classify_text", short_hash(text)))
        return dict(self.classify_out)

    def extract_entities(self, text: str, entity_types: Sequence[str]) -> Dict[str, Any]:
        self.calls.append(("extract_entities", short_hash(text)))
        return dict(self.entities_out)


class ReplayLLM:
    def __init__(
        self,
        responses: Sequence[Tuple[Optional[str], Optional[str]]],
        *,
        model_id: str = "replay-narrator-v1",
        disabled: bool = False,
    ) -> None:
        self.responses = list(responses)
        self.model_id = model_id
        self.disabled = disabled
        self.calls: List[Dict[str, Any]] = []
        self._index = 0

    def complete(self, system: str, prompt: str, *, max_tokens: int = 256, **options: Any) -> str:
        idx = self._index
        self._index += 1
        self.calls.append(
            {
                "index": idx,
                "system_hash": short_hash(system),
                "prompt_hash": short_hash(prompt),
                "max_tokens": max_tokens,
                "options": sorted(options.keys()),
            }
        )
        if not self.responses:
            return ""
        reply, err = self.responses[min(idx, len(self.responses) - 1)]
        if err:
            raise RuntimeError(err)
        return reply or ""


class RecordingLLM:
    """Wrap a live/local LLM once and capture responses into a ReplayLLM transcript."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model_id = getattr(inner, "model_id", "recorded-narrator")
        self.disabled = getattr(inner, "disabled", False)
        self.transcript: List[Tuple[Optional[str], Optional[str]]] = []

    def complete(self, system: str, prompt: str, *, max_tokens: int = 256, **options: Any) -> str:
        try:
            reply = self.inner.complete(system, prompt, max_tokens=max_tokens, **options)
            self.transcript.append((reply, None))
            return reply
        except Exception as exc:
            self.transcript.append((None, f"{type(exc).__name__}: {exc}"))
            raise

    def to_replay(self) -> ReplayLLM:
        return ReplayLLM(self.transcript, model_id=self.model_id, disabled=self.disabled)


class ScriptedTools:
    def __init__(self, snapshots: Mapping[str, Mapping[str, Any]]) -> None:
        self.snapshots = {k: dict(v) for k, v in snapshots.items()}
        self.watchlist_writes: List[str] = []

    def get_snapshot(self, ticker: str) -> Dict[str, Any]:
        return dict(self.snapshots.get(ticker, {"ticker": ticker}))

    def plot_history(self, ticker: str) -> str:
        return f"chart://{ticker}"

    def add_to_watchlist(self, ticker: str) -> None:
        self.watchlist_writes.append(ticker)


class ScriptedNewsStore:
    def __init__(self, outcomes: Mapping[str, Sequence[Outcome]]) -> None:
        self.outcomes = {k: list(v) for k, v in outcomes.items()}

    def get_gated_signals(self, ticker: str, limit: int = 5) -> List[Outcome]:
        return list(self.outcomes.get(ticker, []))[:limit]


def _canonical_span(span: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "name": span.get("name"),
        "kind": span.get("kind"),
        "actor": span.get("actor"),
        "model": span.get("model"),
        "status": span.get("status"),
        "error_type": span.get("error_type"),
    }


def _specialist_checkpoint(report: Any) -> Optional[Dict[str, Any]]:
    if report is None:
        return None
    val = dict(getattr(report, "validation", {}) or {})
    return {
        "agent_name": report.agent_name,
        "tickers": list(report.tickers),
        "faithful_numbers": bool(report.faithful_numbers),
        "allowed_numbers": [round(float(n), 9) for n in report.allowed_numbers],
        "validation_reason": val.get("validation_reason"),
        "method": val.get("method"),
        "summary": report.summary,
        "raw_facts_hash": short_hash(report.raw_facts),
        "chart_refs": list(report.chart_refs),
    }


def extract_checkpoints(
    result: TeamRunResult,
    trace_data: Mapping[str, Any],
    *,
    watchlist_writes: Sequence[str] = (),
    llm_calls: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    events = result.events or []
    exec_events = [e.payload for e in events if e.event_type == "EXECUTIVE_ROUTE"]
    delegation_events = [e.payload for e in events if e.event_type == "AGENT_DELEGATION"]
    watchlist_events = [e.payload for e in events if e.event_type == "WATCHLIST_ACTION_REQUIRED"]
    gate_events = [e.payload for e in events if e.event_type == "GATE_CHECK"]

    spans = trace_data.get("spans") or []
    number_gates = [
        {
            "actor": s.get("actor"),
            "status": s.get("status"),
            "passed": (s.get("outputs") or {}).get("passed"),
            "validation_reason": (s.get("outputs") or {}).get("validation_reason"),
        }
        for s in spans
        if s.get("name") == "Number faithfulness"
    ]
    synth_gates = [
        {
            "status": s.get("status"),
            "faithful_synthesis": (s.get("outputs") or {}).get("faithful_synthesis"),
            "validation_reason": (s.get("outputs") or {}).get("validation_reason"),
            "guard_retry": (s.get("outputs") or {}).get("guard_retry"),
            "follow_up_retry": (s.get("outputs") or {}).get("follow_up_retry"),
        }
        for s in spans
        if s.get("name") == "Synthesis faithfulness"
    ]

    all_allowed: List[float] = []
    for rep in (result.data_report, result.news_report, result.document_report):
        if rep:
            all_allowed.extend(rep.allowed_numbers)
    ans_nums = numbers_in(result.final_synthesis)
    grounded_ok = (not ans_nums) or (bool(all_allowed) and score_numbers_faithful(result.final_synthesis, all_allowed))

    return {
        "schema_version": SCHEMA_VERSION,
        "route": {
            "intent": result.routed.intent,
            "intent_confidence": round(float(result.routed.intent_confidence), 4),
            "horizon": result.routed.horizon,
            "tickers": list(result.routed.tickers),
            "risk_mode": result.risk_mode,
            "executive_route_events": exec_events,
            "watchlist_action_required": bool(watchlist_events),
            "watchlist_writes": list(watchlist_writes),
        },
        "delegation": {
            "delegated_agents": list(result.delegated_agents),
            "delegation_events": delegation_events,
        },
        "specialist": {
            "data": _specialist_checkpoint(result.data_report),
            "news": _specialist_checkpoint(result.news_report),
            "document": _specialist_checkpoint(result.document_report),
        },
        "guards": {
            "faithful_synthesis": bool(result.faithful_synthesis),
            "gate_events": gate_events,
            "number_gates": number_gates,
            "synthesis_gates": synth_gates,
        },
        "synthesis": {
            "final_synthesis": result.final_synthesis,
            "faithful_synthesis": bool(result.faithful_synthesis),
            "answer_numbers": [round(float(n), 9) for n in ans_nums],
            "answer_numbers_grounded": bool(grounded_ok),
        },
        "trace": {
            "status": trace_data.get("status"),
            "dropped_spans": int(trace_data.get("dropped_spans", 0)),
            "storage_errors": int(trace_data.get("storage_errors", 0)),
            "spans": [_canonical_span(s) for s in spans],
            "model_call_count": len(llm_calls),
            "model_calls": [
                {"index": c["index"], "max_tokens": c["max_tokens"], "prompt_hash": c["prompt_hash"]}
                for c in llm_calls
            ],
        },
    }


PAIRWISE_FACTORS: Dict[str, Tuple[str, ...]] = {
    "intent": ("price_snapshot", "news_catalysts", "compare_assets", "thesis_review", "watchlist_add"),
    "asset_kind": ("equity_single", "crypto_single", "multi_asset", "fallback_only"),
    "documents": ("none", "attached"),
    "context": ("none", "follow_up"),
    "narrator_mode": (
        "faithful",
        "invented_number_then_fixed",
        "bad_word_then_fallback",
        "disabled_facts_only",
        "model_error",
    ),
    "news_state": ("populated", "empty"),
}


def required_pairs(factors: Mapping[str, Sequence[str]]) -> set:
    keys = list(factors.keys())
    pairs = set()
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            ki, kj = keys[i], keys[j]
            for vi in factors[ki]:
                for vj in factors[kj]:
                    pairs.add((ki, vi, kj, vj))
    return pairs


def pairwise_matrix(factors: Mapping[str, Sequence[str]], *, seed: int = 20261126) -> List[Dict[str, str]]:
    """Deterministic greedy covering array achieving 100% pairwise factor coverage."""
    rng = random.Random(seed)
    keys = list(factors.keys())
    uncovered = required_pairs(factors)
    rows: List[Dict[str, str]] = []
    while uncovered:
        best_row: Optional[Dict[str, str]] = None
        best_gain = -1
        for _ in range(48):
            row: Dict[str, str] = {}
            order = list(keys)
            rng.shuffle(order)
            for k in order:
                vals = list(factors[k])
                rng.shuffle(vals)
                chosen = vals[0]
                best_val_gain = -1
                for v in vals:
                    gain = 0
                    for ok, ov in row.items():
                        pair = (ok, ov, k, v) if keys.index(ok) < keys.index(k) else (k, v, ok, ov)
                        if pair in uncovered:
                            gain += 1
                    if gain > best_val_gain:
                        best_val_gain = gain
                        chosen = v
                row[k] = chosen
            covered = {
                (keys[i], row[keys[i]], keys[j], row[keys[j]])
                for i in range(len(keys))
                for j in range(i + 1, len(keys))
            }
            gain = len(covered & uncovered)
            if gain > best_gain:
                best_gain = gain
                best_row = row
        assert best_row is not None and best_gain > 0
        rows.append(best_row)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                uncovered.discard((keys[i], best_row[keys[i]], keys[j], best_row[keys[j]]))
    return rows


def _narrator_responses(mode: str, tickers: Sequence[str]) -> Tuple[List[Tuple[Optional[str], Optional[str]]], bool]:
    primary = tickers[0] if tickers else "NVDA"
    snap = SNAPSHOTS.get(primary, SNAPSHOTS["NVDA"])
    price = snap["price"]
    if mode == "faithful":
        if primary == "BTC":
            return [
                (f"BTC is at {price} and the reports do not identify a specific risk.", None),
                (f"BTC is at {price}. The reports do not identify a specific risk.", None),
            ], False
        return [
            (f"{primary} price is {price}.", None),
            (f"{primary} trades at {price}. The reports do not identify a specific risk.", None),
        ], False
    if mode == "invented_number_then_fixed":
        return [
            (f"{primary} price is 999999.99.", None),
            (f"{primary} price is {price}. The reports do not identify a specific risk.", None),
            (f"{primary} trades at {price}. The reports do not identify a specific risk.", None),
        ], False
    if mode == "bad_word_then_fallback":
        return [
            (f"{primary} price is {price} with guaranteed moonshot upside.", None),
            (f"{primary} price is {price} with guaranteed moonshot upside.", None),
            (f"{primary} price is {price} with guaranteed moonshot upside.", None),
        ], False
    if mode == "disabled_facts_only":
        return [], True
    return [(None, "SimulatedNarratorError")], False


def _case_from_row(idx: int, row: Mapping[str, str]) -> Dict[str, Any]:
    asset_kind = row["asset_kind"]
    if asset_kind == "equity_single":
        query_text, ner, fallback = "Check NVDA latest figures", {"entities": {"ticker": ["NVDA"]}}, []
        expected_tickers = ["NVDA"]
    elif asset_kind == "crypto_single":
        query_text, ner, fallback = "Check Bitcoin latest figures", {"entities": {"cryptocurrency": ["Bitcoin"]}}, []
        expected_tickers = ["BTC"]
    elif asset_kind == "multi_asset":
        query_text, ner, fallback = "Compare NVDA and AAPL", {"entities": {"ticker": ["NVDA", "AAPL"]}}, []
        expected_tickers = ["NVDA", "AAPL"]
    else:
        query_text, ner, fallback = "How does the update look?", {"entities": {}}, ["NVDA"]
        expected_tickers = ["NVDA"]

    responses, disabled = _narrator_responses(row["narrator_mode"], expected_tickers)
    news_map = NEWS_FIXTURES if row["news_state"] == "populated" else {}
    context = "NVDA closed at 142.5 with market cap 3.49 trillion." if row["context"] == "follow_up" else ""
    docs = SAMPLE_DOCUMENTS if row["documents"] == "attached" else None
    return {
        "case_id": f"pair-{idx:02d}",
        "factors": dict(row),
        "query": query_text,
        "fallback_tickers": fallback,
        "context": context,
        "documents": docs,
        "classify_out": {
            "intent": {"label": row["intent"], "confidence": 0.91},
            "horizon": {"label": "swing_week", "confidence": 0.85},
        },
        "entities_out": ner,
        "snapshots": SNAPSHOTS,
        "news_outcomes": news_map,
        "narrator_responses": responses,
        "narrator_disabled": disabled,
    }


def build_pairwise_cases(*, seed: int = 20261126) -> List[Dict[str, Any]]:
    rows = pairwise_matrix(PAIRWISE_FACTORS, seed=seed)
    return [_case_from_row(i, row) for i, row in enumerate(rows, 1)]


def default_orchestrator_factory(case: Mapping[str, Any]) -> Tuple[TeamLeadOrchestrator, ScriptedTools, ReplayLLM]:
    extractor = ReplayExtractor(case["classify_out"], case["entities_out"])
    router = GlinerQueryRouter(extractor, UNIVERSE, DEFAULT_ALIASES)
    tools = ScriptedTools(case["snapshots"])
    store = ScriptedNewsStore(case["news_outcomes"])
    llm = ReplayLLM(case["narrator_responses"], disabled=bool(case["narrator_disabled"]))
    orch = TeamLeadOrchestrator(
        router,
        DataAnalystAgent(llm, tools),
        NewsResearcherAgent(llm, store),
        llm,
        tools,
        document_agent=DocumentReaderAgent(),
    )
    return orch, tools, llm


OrchestratorFactory = Callable[[Mapping[str, Any]], Tuple[TeamLeadOrchestrator, ScriptedTools, ReplayLLM]]


def _trace_orchestrator(orch: TeamLeadOrchestrator, trace: TraceRecorder) -> TeamLeadOrchestrator:
    class AgentTrace:
        def __init__(self, agent: Any, name: str) -> None:
            self.agent, self.name = agent, name

        def run(self, *args: Any, **kwargs: Any) -> Any:
            with trace.span(self.name, "agent", actor=self.name) as span:
                report = self.agent.run(*args, **kwargs)
                trace.event(
                    "Number faithfulness",
                    "gate",
                    {
                        "passed": report.faithful_numbers,
                        "fallback_used": not report.faithful_numbers,
                        "allowed_numbers": report.allowed_numbers,
                        **getattr(report, "validation", {}),
                    },
                    actor=self.name,
                    fallback=not report.faithful_numbers,
                )
                span.update(output=report, fallback=not report.faithful_numbers)
                return report

    router = orch.router
    if isinstance(router, GlinerQueryRouter):
        router = GlinerQueryRouter(
            TracedExtractor(router.backend, trace, "fastino/GLiNER2.5-Decide", "Routing executive"),
            router.universe,
            router.aliases,
        )

    class RouterTrace:
        def route(self, text: str) -> Any:
            return trace.call(
                "Route request",
                "route",
                lambda: router.route(text),
                actor="Routing executive",
                inputs={"query": text},
            )

    bus = EventBus()

    def observe(event: Any) -> None:
        if event.event_type == "GATE_CHECK":
            passed = event.payload.get("faithful_synthesis")
            trace.event("Synthesis faithfulness", "gate", event.payload, actor=event.agent, fallback=passed is False)
        elif event.event_type in {"EXECUTIVE_ROUTE", "AGENT_DELEGATION"}:
            trace.event(event.event_type.replace("_", " ").title(), "handoff", event.payload, actor=event.agent)

    bus.subscribe(observe)
    bus.subscribe(orch.bus.publish)

    traced_data = DataAnalystAgent(
        TracedLLM(orch.data_agent.llm, trace, "Data Analyst"),
        TracedTools(orch.data_agent.tools, trace),
        stop=getattr(orch.data_agent, "stop", None),
    )
    traced_news = NewsResearcherAgent(
        TracedLLM(orch.news_agent.llm, trace, "News Researcher"),
        TracedStore(orch.news_agent.store, trace),
    )
    traced_doc = getattr(orch, "document_agent", None) or DocumentReaderAgent()

    orch.router = RouterTrace()
    orch.data_agent = AgentTrace(traced_data, "Data Analyst")
    orch.news_agent = AgentTrace(traced_news, "News Researcher")
    orch.lead_llm = TracedLLM(orch.lead_llm, trace, "Market Team Lead")
    orch.tools = TracedTools(orch.tools, trace)
    orch.bus = bus
    orch.document_agent = AgentTrace(traced_doc, "Document Reader")
    return orch


def run_case(factory: OrchestratorFactory, case: Mapping[str, Any]) -> Dict[str, Any]:
    orch, tools, llm = factory(case)
    trace = TraceRecorder(f"parity:{case['case_id']}", mode="replay", directory=None)
    traced_orch = _trace_orchestrator(orch, trace)
    try:
        with trace.span(
            "Interactive research",
            "run",
            inputs={
                "query": case["query"],
                "tool_side_effects": "same as original orchestrator",
                "conversation_context": "previous accepted answer" if case["context"] else "none",
            },
        ) as span:
            result = traced_orch.run(
                case["query"],
                case["case_id"],
                case["fallback_tickers"],
                context=case["context"],
                documents=case["documents"],
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


def check_invariants(checkpoints: Mapping[str, Any]) -> List[str]:
    violations: List[str] = []
    if checkpoints["route"]["watchlist_writes"]:
        violations.append("watchlist_mutated_during_chat")
    if not checkpoints["synthesis"]["answer_numbers_grounded"]:
        violations.append("ungrounded_number_in_synthesis")
    if checkpoints["trace"]["dropped_spans"] != 0:
        violations.append("dropped_spans_nonzero")
    if checkpoints["trace"]["storage_errors"] != 0:
        violations.append("storage_errors_nonzero")
    return violations


def run_differential(
    baseline_factory: OrchestratorFactory,
    candidate_factory: OrchestratorFactory,
    cases: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    started = time.perf_counter()
    results: List[Dict[str, Any]] = []
    matched = 0
    invariant_failures = 0
    for case in cases:
        base_cp = run_case(baseline_factory, case)
        cand_cp = run_case(candidate_factory, case)
        diff = compare_checkpoints(base_cp, cand_cp)
        base_inv = check_invariants(base_cp)
        cand_inv = check_invariants(cand_cp)
        ok = diff is None and not base_inv and not cand_inv
        if ok:
            matched += 1
        if base_inv or cand_inv:
            invariant_failures += 1
        results.append(
            {
                "case_id": case["case_id"],
                "factors": case.get("factors", {}),
                "matched": ok,
                "diff": diff.as_dict() if diff else None,
                "baseline_invariants": base_inv,
                "candidate_invariants": cand_inv,
            }
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": matched == len(cases),
        "total_cases": len(cases),
        "matched_cases": matched,
        "mismatched_cases": len(cases) - matched,
        "invariant_failures": invariant_failures,
        "elapsed_ms": elapsed_ms,
        "fingerprint_groups": group_fingerprints(results),
        "cases": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "self-check"
    if cmd != "self-check":
        print(f"Unsupported command: {cmd}", file=sys.stderr)
        return 2
    cases = build_pairwise_cases()
    report = run_differential(default_orchestrator_factory, default_orchestrator_factory, cases)
    summary = {
        "schema_version": report["schema_version"],
        "passed": report["passed"],
        "total_cases": report["total_cases"],
        "matched_cases": report["matched_cases"],
        "invariant_failures": report["invariant_failures"],
        "elapsed_ms": report["elapsed_ms"],
        "fingerprint_groups": report["fingerprint_groups"],
    }
    print(json.dumps(summary, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
