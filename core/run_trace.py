"""Opt-in local traces. Observed I/O, timings and rule outcomes, not private chain-of-thought.

Adapters wrap actual calls; they never invent progress or execute an event payload.
run_traced_team clones wiring, not models, and leaves the original orchestrator untouched.
run_traced_clock1 uses a NEW in-memory store for diagnostic replay, so replay cannot mutate user data.
Only an explicit operational collection passes a real NewsStore; it still sends no notifications.
"""
from __future__ import annotations

import contextvars
import dataclasses
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

TRACE_FORMAT = "cofinance-trace/1"
TRACE_REPLACE_BACKOFF = (0.015, 0.035, 0.075, 0.15, 0.25)
WINDOWS_REPLACE_CONTENTION = frozenset({5, 32, 33})
_TRACE_GENERATION_LOCK = threading.Lock()
_TRACE_IO_LOCK = threading.RLock()
_TRACE_GENERATION = 0
SECRET = re.compile(r"api.?key|token|cookie|password|secret|authorization|credential|session", re.I)
SAFE_TRACE_KEYS = frozenset({
    "previous_session_close",
    "previous_session_date",
    "change_from_previous_session_pct",
    "output_token_limit",
    "wasted_tokens",
})
THINK = re.compile(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|$)", re.I | re.S)


TRACE_INTEGRITY_ISSUES = frozenset({
    "trace_not_terminal", "trace_unverified_counters", "trace_storage_errors", "trace_dropped_spans", "trace_running_spans",
})


def trace_generation() -> int:
    with _TRACE_GENERATION_LOCK:
        return _TRACE_GENERATION


def _bump_trace_generation() -> None:
    global _TRACE_GENERATION
    with _TRACE_GENERATION_LOCK:
        _TRACE_GENERATION += 1


def trace_integrity_issue(trace: Mapping[str, Any]) -> str | None:
    dropped, storage = trace.get("dropped_spans"), trace.get("storage_errors")
    if any(type(value) is not int or value < 0 for value in (dropped, storage)):
        return "trace_unverified_counters"
    if storage:
        return "trace_storage_errors"
    if dropped:
        return "trace_dropped_spans"
    status = trace.get("status")
    if status not in ("complete", "degraded"):
        return "trace_not_terminal"
    spans = trace.get("spans")
    if isinstance(spans, list) and any(isinstance(span, Mapping) and span.get("status") == "running" for span in spans):
        return "trace_running_spans"
    return None


def safe(value: Any, depth: int = 0) -> Any:
    """Bounded JSON data with secret-key redaction and thinking blocks removed."""
    if depth > 5:
        return "[depth limited]"
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k)[:100]: "[redacted]" if (str(k) not in SAFE_TRACE_KEYS and SECRET.search(str(k))) else safe(v, depth + 1)
                for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple, set)):
        return [safe(v, depth + 1) for v in list(value)[:40]]
    if isinstance(value, str):
        return THINK.sub("[private thinking omitted]", value)[:2500]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"[{type(value).__name__}]"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def replace_trace(temp: Path, destination: Path) -> None:
    for delay in TRACE_REPLACE_BACKOFF:
        try:
            with _TRACE_IO_LOCK:
                os.replace(temp, destination)
            _bump_trace_generation()
            return
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in WINDOWS_REPLACE_CONTENTION:
                raise
            time.sleep(delay)
    with _TRACE_IO_LOCK:
        os.replace(temp, destination)
    _bump_trace_generation()


def read_trace_bytes(path: Path, *, max_bytes: int | None = None) -> bytes:
    def _read() -> bytes:
        with _TRACE_IO_LOCK:
            if path.is_symlink():
                raise ValueError("invalid trace size or link")
            if max_bytes is not None and path.stat().st_size > max_bytes:
                raise ValueError("invalid trace size or link")
            return path.read_bytes()

    for delay in TRACE_REPLACE_BACKOFF:
        try:
            return _read()
        except OSError as exc:
            if not (isinstance(exc, PermissionError) or getattr(exc, 'winerror', None) in WINDOWS_REPLACE_CONTENTION):
                raise
            time.sleep(delay)
    return _read()


def save_error_count(directory: Path, run_id: str) -> int:
    marker = directory / (run_id + ".save-error")
    if not marker.is_file():
        return 0
    try:
        value = int(marker.read_text(encoding="utf-8").strip())
        return value if value > 0 else 1
    except (OSError, ValueError):
        return 1


class TraceRecorder:
    def __init__(self, title: str, *, mode: str = "live", directory: str | Path | None = None,
                 clock: Callable[[], float] = time.perf_counter, max_spans: int = 2000) -> None:
        if mode not in {"live", "fixture", "replay"}:
            raise ValueError("mode must be live, fixture or replay")
        self.clock, self.started = clock, clock()
        self.run_id = uuid.uuid4().hex
        self.parent = contextvars.ContextVar("trace_parent_" + self.run_id, default=None)
        self.lock = threading.RLock()
        self.max_spans = max_spans
        self.directory = Path(directory) if directory is not None else None
        self.data = {"format": TRACE_FORMAT, "id": self.run_id, "title": safe(title), "mode": mode,
                     "started_at": utc(), "status": "running", "duration_ms": None, "spans": [],
                     "dropped_spans": 0, "storage_errors": 0,
                     "disclosure": "Observed calls and rule outcomes only. No private chain-of-thought. Local diagnostic traces may contain project data."}
        self.flush()

    def flush(self) -> bool:
        if self.directory is None:
            return True
        with self.lock:
            temp = None
            marker = self.directory / (self.run_id + ".save-error")
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                                 prefix=".trace-", suffix=".tmp", delete=False) as file:
                    temp = Path(file.name)
                    json.dump(self.data, file, ensure_ascii=True, allow_nan=False)
                replace_trace(temp, self.directory / (self.run_id + ".json"))
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass
                return True
            except (OSError, ValueError):
                self.data["storage_errors"] += 1
                try:
                    marker.write_text(str(self.data["storage_errors"]), encoding="utf-8")
                    _bump_trace_generation()
                except OSError:
                    pass
                if temp:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
                return False

    @contextmanager
    def span(self, name: str, kind: str, *, actor: str = "Python", model: str = "", inputs: Any = None):
        start = self.clock()
        with self.lock:
            if len(self.data["spans"]) >= self.max_spans:
                self.data["dropped_spans"] += 1
                node = None
            else:
                node = {"id": uuid.uuid4().hex, "parent_id": self.parent.get(), "name": safe(name), "kind": kind,
                        "actor": safe(actor), "model": safe(model), "status": "running",
                        "start_ms": round((start - self.started) * 1000, 3), "duration_ms": None,
                        "inputs": safe(inputs), "outputs": None}
                self.data["spans"].append(node)
                self.flush()
        token = self.parent.set(node["id"]) if node else None
        result: dict = {}
        try:
            yield result
        except BaseException as exc:
            if node:
                node.update(status="error", error_type=type(exc).__name__)
            raise
        finally:
            if node:
                if node["status"] != "error":
                    node["status"] = "fallback" if result.get("fallback") else "complete"
                node["outputs"] = safe(result.get("output"))
                if name in {"Narrate evidence", "Answer open conversation"} and kind == "model":
                    from .usage import counts, summarize
                    node["usage"] = counts(result.get("usage"))
                    self.data["usage"] = summarize(self.data["spans"], self.data["dropped_spans"])
                node["duration_ms"] = round(max(0, self.clock() - start) * 1000, 3)
                self.parent.reset(token)
                self.flush()

    def call(self, name: str, kind: str, fn: Callable, *, actor: str = "Python", model: str = "", inputs: Any = None):
        with self.span(name, kind, actor=actor, model=model, inputs=inputs) as result:
            output = fn()
            result["output"] = output
            return output

    def event(self, name: str, kind: str, payload: Any, *, actor: str = "Python", fallback: bool = False,
              parent_id: str | None = None) -> None:
        token = self.parent.set(parent_id) if parent_id is not None else None
        try:
            with self.span(name, kind, actor=actor) as result:
                result.update(output=payload, fallback=fallback)
        finally:
            if token is not None:
                self.parent.reset(token)

    def record_completed(self, name: str, kind: str, *, actor: str = "Python", model: str = "", inputs: Any = None,
                         outputs: Any = None, start_ms: float | None = None, duration_ms: float = 0.0,
                         error_type: str = "", fallback: bool = False, parent_id: str | None = None) -> None:
        with self.lock:
            if len(self.data["spans"]) >= self.max_spans:
                self.data["dropped_spans"] += 1
                self.flush()
                return
            status = "error" if error_type else ("fallback" if fallback else "complete")
            node = {"id": uuid.uuid4().hex, "parent_id": parent_id if parent_id is not None else self.parent.get(),
                    "name": safe(name), "kind": kind, "actor": safe(actor), "model": safe(model),
                    "status": status, "start_ms": round(max(0.0, (self.clock() - self.started) * 1000) if start_ms is None else start_ms, 3),
                    "duration_ms": round(max(0.0, float(duration_ms)), 3),
                    "inputs": safe(inputs), "outputs": safe(outputs)}
            if error_type:
                node["error_type"] = safe(error_type)
            self.data["spans"].append(node)
            self.flush()

    def finish(self, *, error: bool = False, summary: Any = None) -> dict:
        with self.lock:
            self.data["status"] = "error" if error else "degraded" if any(
                n["status"] in {"error", "fallback"} for n in self.data["spans"]) else "complete"
            self.data["duration_ms"] = round(max(0, self.clock() - self.started) * 1000, 3)
            self.data["summary"] = safe(summary)
            from .usage import summarize
            from .guard_telemetry import summarize_guards
            self.data["usage"] = summarize(self.data["spans"], self.data["dropped_spans"])
            self.data["guard_telemetry"] = summarize_guards(self.data["spans"], self.data["dropped_spans"])
            self.flush()
            return self.data


def model_name(backend: Any) -> str:
    """Never infer a named model from a role. Backends should expose model_id explicitly."""
    for attr in ("model_id", "model_name"):
        value = getattr(backend, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(backend).__name__ + " (identity not supplied)"


class TracedExtractor:
    def __init__(self, backend: Any, trace: TraceRecorder, model: str, actor: str = "Decision model") -> None:
        self.backend, self.trace, self.model, self.actor = backend, trace, model, actor

    def classify_text(self, text, schema, **kwargs):
        return self.trace.call("Classify heads", "model", lambda: self.backend.classify_text(text, schema, **kwargs),
                               actor=self.actor, model=self.model, inputs={"text": text, "heads": list(schema)})

    def batch_classify_text(self, texts, schema, **kwargs):
        batch_fn = getattr(self.backend, "batch_classify_text", None)
        if not callable(batch_fn):
            return [self.classify_text(t, schema, **kwargs) for t in texts]
        items = list(texts)
        return self.trace.call(
            "Classify heads (batch)",
            "model",
            lambda: batch_fn(items, schema, **kwargs),
            actor=self.actor,
            model=self.model,
            inputs={"count": len(items), "heads": list(schema)},
        )

    def extract_entities(self, text, entity_types):
        return self.trace.call("Resolve entities", "model", lambda: self.backend.extract_entities(text, entity_types),
                               actor=self.actor, model=self.model, inputs={"text": text, "entity_types": entity_types})


class TracedLLM:
    def __init__(self, backend: Any, trace: TraceRecorder, actor: str) -> None:
        self.backend, self.trace, self.actor = backend, trace, actor

    def complete(self, system, prompt, *, max_tokens=256, **options):
        # Tool-only mode is a deterministic fallback, never counted as a model call.
        if getattr(self.backend, "disabled", False):
            self.trace.event("Tool-only report", "decision", {"reason": "narrator_disabled"}, actor=self.actor, fallback=True)
            return ""
        # Do not record private system prompts or hidden reasoning tokens.
        from .usage import capture_usage
        with self.trace.span("Narrate evidence", "model", actor=self.actor, model=model_name(self.backend),
                             inputs={"evidence": prompt, "system_characters": len(system), "output_token_limit": max_tokens}) as result:
            with capture_usage() as receipts:
                try:
                    output = self.backend.complete(system, prompt, max_tokens=max_tokens, **options)
                    result["output"] = output
                    return output
                finally:
                    # One complete() means one runtime request. Uninstrumented backends remain unknown, not zero.
                    result["usage"] = receipts[0] if len(receipts) == 1 else None


class TracedTools:
    def __init__(self, tools: Any, trace: TraceRecorder) -> None:
        self.tools, self.trace = tools, trace

    def get_snapshot(self, ticker):
        return self.trace.call("Market snapshot", "tool", lambda: self.tools.get_snapshot(ticker),
                               actor="Data Analyst", inputs={"ticker": ticker})

    def get_snapshots(self, tickers):
        ordered = list(tickers)
        if len(ordered) <= 1:
            return {t: self.get_snapshot(t) for t in ordered}
        from concurrent.futures import ThreadPoolExecutor
        parent_id = self.trace.parent.get()

        def run_one(ticker):
            start = self.trace.clock()
            start_ms = round(max(0.0, (start - self.trace.started) * 1000), 3)
            try:
                value = self.tools.get_snapshot(ticker)
                return ticker, start_ms, (self.trace.clock() - start) * 1000.0, value, None
            except BaseException as exc:
                return ticker, start_ms, (self.trace.clock() - start) * 1000.0, None, exc

        pool = ThreadPoolExecutor(max_workers=min(len(ordered), 4))
        futures = [pool.submit(run_one, t) for t in ordered]
        check_fn = getattr(self.tools, "check", None)
        results = []
        try:
            for fut in futures:
                while True:
                    if callable(check_fn):
                        check_fn()
                    try:
                        results.append(fut.result(timeout=0.05))
                        break
                    except TimeoutError:
                        continue
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

        out = {}
        first_err = None
        for ticker, start_ms, dur_ms, value, exc in results:
            if exc is not None:
                self.trace.record_completed("Market snapshot", "tool", actor="Data Analyst", inputs={"ticker": ticker},
                                            start_ms=start_ms, duration_ms=dur_ms, error_type=type(exc).__name__,
                                            parent_id=parent_id)
                if first_err is None:
                    first_err = exc
            else:
                self.trace.record_completed("Market snapshot", "tool", actor="Data Analyst", inputs={"ticker": ticker},
                                            outputs=value, start_ms=start_ms, duration_ms=dur_ms, parent_id=parent_id)
                out[ticker] = value
        if first_err is not None:
            raise first_err
        if hasattr(self.tools, "reorder_collected"):
            self.tools.reorder_collected(ordered)
        return out

    def plot_history(self, ticker):
        return self.trace.call("Price history", "tool", lambda: self.tools.plot_history(ticker),
                               actor="Data Analyst", inputs={"ticker": ticker})

    def add_to_watchlist(self, ticker):
        return self.trace.call("Watchlist write", "tool", lambda: self.tools.add_to_watchlist(ticker),
                               actor="Market Team Lead", inputs={"ticker": ticker, "side_effect": True})


class TracedStore:
    def __init__(self, store: Any, trace: TraceRecorder) -> None:
        self.store, self.trace = store, trace

    def get_gated_signals(self, ticker, limit=5):
        return self.trace.call("Read gated news", "tool", lambda: self.store.get_gated_signals(ticker, limit=limit),
                               actor="News Researcher", inputs={"ticker": ticker, "limit": limit})


class TracedClassifier:
    def __init__(self, classifier: Any, trace: TraceRecorder, model: str | None = None, *, leaf_model: bool = False) -> None:
        self.classifier, self.trace = classifier, trace
        self.model = model or model_name(classifier)
        self.leaf_model = leaf_model

    def judge(self, item):
        inputs = {"ticker": item.ticker, "title": item.title, "source": item.source, "url": item.url, "item_key": item.key}
        with self.trace.span("Judge headline", "decision", actor="Signal judge", model=self.model, inputs=inputs) as span:
            if self.leaf_model:
                output = self.trace.call("Classifier inference", "model", lambda: self.classifier.judge(item),
                                         actor="Signal judge", model=self.model, inputs=inputs)
            else:
                output = self.classifier.judge(item)
            span["output"] = output
            return output

    def judge_batch(self, items):
        batch_items = list(items)
        batch_fn = getattr(self.classifier, "judge_batch", None)
        if self.leaf_model or not callable(batch_fn) or len(batch_items) <= 1:
            results = []
            for item in batch_items:
                try:
                    results.append(self.judge(item))
                except Exception as exc:
                    results.append(exc)
            return results
        raw_results = list(batch_fn(batch_items))
        for item, outcome in zip(batch_items, raw_results):
            inputs = {"ticker": item.ticker, "title": item.title, "source": item.source, "url": item.url, "item_key": item.key}
            with self.trace.span("Judge headline", "decision", actor="Signal judge", model=self.model, inputs=inputs) as span:
                if isinstance(outcome, BaseException):
                    span["status"] = "error"
                    span["error"] = f"{type(outcome).__name__}: {str(outcome)[:200]}"
                else:
                    span["output"] = outcome
        return raw_results


def instrument_classifier(classifier: Any, trace: TraceRecorder):
    from .cascade import CascadeClassifier
    from .gliner_decide import GlinerDecideClassifier
    if isinstance(classifier, CascadeClassifier):
        clone = CascadeClassifier(instrument_classifier(classifier.primary, trace),
                                  instrument_classifier(classifier.adjudicator, trace), classifier.gate, classifier.gray_low)
        class CascadeTrace:
            def judge(self, item):
                before = dict(clone.routes)
                with trace.span("Cascade decision", "decision", actor="Signal gate", inputs={
                        "ticker": item.ticker, "title": item.title, "url": item.url, "item_key": item.key, "gray_low": clone.gray_low,
                        "alert_material": clone.gate.alert_p, "alert_urgent": clone.gate.alert_urgent_p}) as span:
                    result = clone.judge(item)
                    route = next((k for k, v in clone.routes.items() if v > before.get(k, 0)), "unknown")
                    span.update(output={"route": route, "judgment": result}, fallback=route == "adjudicator_down")
                    return result
        return CascadeTrace()
    if isinstance(classifier, GlinerDecideClassifier):
        model = "fastino/GLiNER2.5-Decide"
        clone = GlinerDecideClassifier(
            TracedExtractor(classifier.backend, trace, model),
            include_urgency=getattr(classifier, "include_urgency", True),
        )
        return TracedClassifier(clone, trace, model)
    return TracedClassifier(classifier, trace, leaf_model=True)


def run_traced_clock1(tickers, fetch, classifier, trace: TraceRecorder, *, cfg=None, clock=None, store=None):
    """Without `store`: diagnostic replay into a fresh in-memory store, never an operational run.
    With `store`: an explicit operational collection that writes that NewsStore (the caller owns and
    closes it). Neither path sends notifications or touches the watchlist."""
    from .clock1 import Clock1Config, run_once
    from .news_store import NewsStore
    from .pregate import quarantine_reason
    cfg = cfg or Clock1Config()
    tickers = tuple(tickers)
    now = clock or time.time
    operational = store is not None
    own_store = store is None
    store = store if operational else NewsStore(":memory:", clock=now)
    from .urgency import urgency
    shadow_hits: list = []
    class RecordingStore:
        def __getattr__(self, key):
            return getattr(store, key)
        def record(self, outcomes, timestamp):
            outcomes = list(outcomes)
            for outcome in outcomes:
                # Shadow mode: what the rule-urgency candidate would say. Recorded only; the action is unchanged.
                hit = urgency(outcome.item.title, outcome.item.text, outcome.item.source) if outcome.judgment else None
                shadow_hits.append(bool(hit))
                shadow = {"would_be_urgent": bool(hit), "rule": hit.rule if hit else None, "matched": hit.matched if hit else None,
                          "material": outcome.judgment.material if outcome.judgment else None,
                          "live": cfg.gate.urgency_source == "rules",
                          "note": ("Live: rule urgency decided this action." if cfg.gate.urgency_source == "rules"
                                   else "Shadow only. The gate action above is unchanged.")} if outcome.judgment else None
                # Gate evaluation follows inference; attach to the outer decision for that exact item.
                candidates = [node for node in trace.data["spans"] if node["kind"] == "decision"
                              and (node.get("inputs") or {}).get("item_key") == outcome.item.key]
                parent = candidates[0]["id"] if candidates else None
                trace.event("Signal gate: " + outcome.action.value.upper(), "gate", {
                    "ticker": outcome.item.ticker, "title": outcome.item.title, "url": outcome.item.url,
                    "item_key": outcome.item.key, "action": outcome.action.value,
                    "reason": outcome.reason, "judgment": outcome.judgment,
                    "thresholds": dataclasses.asdict(cfg.gate), "shadow_rule_urgency": shadow}, actor="Python SignalGate",
                    fallback=outcome.action.value == "deferred", parent_id=parent)
            return store.record(outcomes, timestamp)
    def traced_fetch(ticker):
        result = trace.call("Read source batch", "tool", lambda: fetch(ticker), actor="Collector", inputs={"ticker": ticker})
        for item in result.items:
            reason = quarantine_reason(item) if cfg.quarantine else None
            if reason:
                trace.event("Pre-gate quarantine", "gate", {"ticker": ticker, "title": item.title, "reason": reason}, actor="Python pre-gate")
        return result
    name = "Clock 1 collection" if operational else "Clock 1 diagnostic replay"
    persistence = "local news store" if operational else "fresh in-memory SQLite"
    try:
        with trace.span(name, "run", inputs={"tickers": list(tickers), "persistence": persistence, "notifications": False}) as span:
            summary = run_once(tickers, traced_fetch, instrument_classifier(classifier, trace), RecordingStore(), cfg, clock=now)
            shadow_total = {"judged": len(shadow_hits), "would_be_urgent": sum(shadow_hits), "mode": "shadow, no action changed"}
            span["output"] = {**summary.as_dict(), "shadow_rule_urgency": shadow_total}
        trace.finish(summary={**summary.as_dict(), "persistence": persistence, "shadow_rule_urgency": shadow_total})
        return summary
    except BaseException:
        trace.finish(error=True)
        raise
    finally:
        if own_store:
            store.close()


def run_traced_team(orchestrator, query: str, trace: TraceRecorder, *, session_id: str = "trace", fallback_tickers=(), context: str = "",
                    documents=None, finish: bool = True, collect_only: bool = False, collect_reason: str = ""):
    """Instrument every called backend. Preserves existing tool side effects, so this is not a dry run."""
    from .gliner_decide import GlinerQueryRouter
    from .orchestrator import DataAnalystAgent, DocumentReaderAgent, EventBus, NewsResearcherAgent, TeamLeadOrchestrator
    original = orchestrator
    class AgentTrace:
        def __init__(self, agent, name):
            self.agent, self.name = agent, name
        def run(self, *args, **kwargs):
            with trace.span(self.name, "agent", actor=self.name) as span:
                report = self.agent.run(*args, **kwargs)
                val = getattr(report, "validation", {}) or {}
                is_no_news = val.get("validation_reason") == "no_news" and not val.get("rejected_numbers")
                is_fallback = (not report.faithful_numbers) and not is_no_news
                trace.event("Number faithfulness", "gate", {"passed": report.faithful_numbers,
                            "fallback_used": is_fallback, "allowed_numbers": report.allowed_numbers,
                            **val},
                            actor=self.name, fallback=is_fallback)
                span.update(output=report, fallback=is_fallback)
                return report
    router = original.router
    if isinstance(router, GlinerQueryRouter):
        router = GlinerQueryRouter(TracedExtractor(router.backend, trace, "fastino/GLiNER2.5-Decide", "Routing executive"),
                                  router.universe, router.aliases)
    class RouterTrace:
        def route(self, text):
            return trace.call("Route request", "route", lambda: router.route(text), actor="Routing executive", inputs={"query": text})
    tools = TracedTools(original.tools, trace)
    bus = EventBus()
    def observe(event):
        if event.event_type == "GATE_CHECK":
            passed = event.payload.get("faithful_synthesis")
            trace.event("Synthesis faithfulness", "gate", event.payload, actor=event.agent, fallback=passed is False)
        elif event.event_type in {"EXECUTIVE_ROUTE", "AGENT_DELEGATION"}:
            trace.event(event.event_type.replace("_", " ").title(), "handoff", event.payload, actor=event.agent)
    bus.subscribe(observe)
    bus.subscribe(original.bus.publish)
    clone = TeamLeadOrchestrator(RouterTrace(),
        AgentTrace(DataAnalystAgent(TracedLLM(original.data_agent.llm, trace, "Data Analyst"), TracedTools(original.data_agent.tools, trace),
                                    stop=getattr(original.data_agent, "stop", None)), "Data Analyst"),
        AgentTrace(NewsResearcherAgent(TracedLLM(original.news_agent.llm, trace, "News Researcher"), TracedStore(original.news_agent.store, trace)), "News Researcher"),
        TracedLLM(original.lead_llm, trace, "Market Team Lead"), tools, bus,
        document_agent=AgentTrace(getattr(original, "document_agent", None) or DocumentReaderAgent(), "Document Reader"),
        stop=getattr(original, "stop", ("\n\n",)))
    try:
        with trace.span("Interactive research", "run", inputs={"query": query, "tool_side_effects": "same as original orchestrator",
                                                                "conversation_context": "previous accepted answer" if context else "none"}) as span:
            result = clone.run(query, session_id, fallback_tickers, context=context, documents=documents,
                               collect_only=collect_only, collect_reason=collect_reason)
            span.update(output={"answer": result.final_synthesis, "agents": result.delegated_agents}, fallback=not result.faithful_synthesis)
        if finish:
            trace.finish(summary={"answer": result.final_synthesis, "risk_mode": result.risk_mode})
        return result
    except BaseException:
        if finish:
            trace.finish(error=True)
        raise
