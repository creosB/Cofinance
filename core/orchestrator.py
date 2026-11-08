"""Multi-Agent Orchestrator for Cofinance v2 (1B Specialists + GLiNER2.5-Decide Executive).

Why Cofinance v1's orchestration breaks on 1B models:
  In v1 (`agents/team_lead.py`), the Team Lead is a generative LLM that must read 150+ lines of instructions,
  emit nested tool calls (`call_data_analyst`, `call_news_researcher`, `add_to_watchlist`), wait for worker LLMs
  to emit their own tool calls (`get_market_data`, `plot_stock_history`, `get_company_news`), and then synthesize.
  1B models fail nested tool-calling loops (BFCL non-live ~43%).

How Cofinance v2 preserves the 3-agent team ("Data Analyst", "News Researcher", "Team Lead") on 1B:
  1. GLiNER2.5-Decide (340M System-1) acts as the deterministic Routing & Risk Executive:
     - Classifies intent, asset_class (stock vs crypto -> Fundamental vs Risk Guardian mode),
       and which specialist agents (`Data Analyst`, `News Researcher`) to dispatch.
  2. Each Specialist Agent (`DataAnalystAgent`, `NewsResearcherAgent`, `TeamLeadAgent`) has:
     - Deterministic tool pre-execution in Python (charts, fundamentals, gated news, watchlist updates).
     - Its own isolated 1B LLM role prompt (<= 15 lines) that analyzes ONLY its domain slice.
  3. Full A2A EventBus (`A2AMessage`, `A2AEvent`) + Glass-Box trace so the UI shows live agent handoffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence

from .gliner_decide import ExtractorBackend, GlinerQueryRouter, RoutedQuery
from .signals import Action, Outcome
from qualify.scorers import final_answer, numbers_in, score_numbers_faithful


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class A2AEvent:
    session_id: str
    event_type: str  # "EXECUTIVE_ROUTE" | "AGENT_DELEGATION" | "TOOL_CALL" | "AGENT_RESULT" | "GATE_CHECK"
    agent: str
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)


class EventBus:
    """Headless pub/sub bus compatible with Cofinance's `agents/protocol.py`."""

    def __init__(self) -> None:
        self._subscribers: List[Callable[[A2AEvent], None]] = []
        self.history: List[A2AEvent] = []

    def subscribe(self, handler: Callable[[A2AEvent], None]) -> None:
        self._subscribers.append(handler)

    def publish(self, event: A2AEvent) -> None:
        self.history.append(event)
        for handler in list(self._subscribers):
            try:
                handler(event)
            except Exception:
                pass


class SmallLLMBackend(Protocol):
    """Any 1B/2B local model served via llama-server, LM Studio, Ollama, or test stub."""

    def complete(self, system: str, prompt: str, *, max_tokens: int = 256) -> str: ...


class MarketDataTools(Protocol):
    def get_snapshot(self, ticker: str) -> Dict[str, Any]: ...
    def plot_history(self, ticker: str) -> str: ...
    def add_to_watchlist(self, ticker: str) -> None: ...


class NewsStorePort(Protocol):
    def get_gated_signals(self, ticker: str, limit: int = 5) -> List[Outcome]: ...


CRYPTO_SYMBOLS = frozenset({"BTC", "ETH", "SOL", "ADA", "DOGE", "XRP", "BNB", "AVAX"})


def detect_risk_mode(tickers: Sequence[str]) -> str:
    if any(t.upper() in CRYPTO_SYMBOLS for t in tickers):
        return "risk_guardian_crypto"
    return "fundamental_equity"


@dataclass(frozen=True)
class SpecialistReport:
    agent_name: str
    tickers: List[str]
    raw_facts: Dict[str, Any]
    allowed_numbers: List[float]
    chart_refs: List[str]
    summary: str
    faithful_numbers: bool


class DataAnalystAgent:
    """Specialist Agent 1: Hybrid Stocks & Crypto Data Analyst (1B)."""

    NAME = "Data Analyst"
    SYSTEM_STOCK = (
        "You are 📊 [Data Analyst], an equity fundamentals specialist. "
        "Summarize the provided metrics in 2 concise sentences using ONLY the exact numbers given. "
        "Do not compute new numbers or percentages."
    )
    SYSTEM_CRYPTO = (
        "You are 📊 [Data Analyst] in Risk Guardian Mode (crypto specialist). "
        "Summarize price, volatility, drawdown from ATH, and Fear & Greed Index in 2 sentences using ONLY "
        "the exact numbers given. Warn about overheating when Fear & Greed > 75."
    )

    def __init__(self, llm: SmallLLMBackend, tools: MarketDataTools) -> None:
        self.llm = llm
        self.tools = tools

    def run(self, tickers: Sequence[str], risk_mode: str, bus: EventBus, session_id: str) -> SpecialistReport:
        facts: Dict[str, Any] = {}
        charts: List[str] = []
        allowed: List[float] = []

        for tkr in tickers:
            bus.publish(A2AEvent(session_id, "TOOL_CALL", self.NAME, {"tool": "get_snapshot", "ticker": tkr}))
            snap = self.tools.get_snapshot(tkr)
            facts[tkr] = snap
            for val in snap.values():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    allowed.append(float(val))

            bus.publish(A2AEvent(session_id, "TOOL_CALL", self.NAME, {"tool": "plot_history", "ticker": tkr}))
            charts.append(self.tools.plot_history(tkr))

        sys_prompt = self.SYSTEM_CRYPTO if risk_mode == "risk_guardian_crypto" else self.SYSTEM_STOCK
        prompt = f"Verified Market Data:\n{facts}"
        raw = self.llm.complete(sys_prompt, prompt, max_tokens=200)
        clean = final_answer(raw) or ""
        faithful = score_numbers_faithful(clean, allowed) if allowed else True
        if not faithful:
            clean = self._deterministic_fallback(facts)

        report = SpecialistReport(
            agent_name=self.NAME,
            tickers=list(tickers),
            raw_facts=facts,
            allowed_numbers=allowed,
            chart_refs=charts,
            summary=clean,
            faithful_numbers=faithful,
        )
        bus.publish(
            A2AEvent(
                session_id,
                "AGENT_RESULT",
                self.NAME,
                {"tickers": list(tickers), "faithful_numbers": faithful, "charts": charts, "summary": clean},
            )
        )
        return report

    @staticmethod
    def _deterministic_fallback(facts: Mapping[str, Any]) -> str:
        parts = []
        for tkr, snap in facts.items():
            kv = ", ".join(f"{k}={v}" for k, v in snap.items())
            parts.append(f"📊 [Data Analyst] {tkr}: {kv}.")
        return " ".join(parts)


class NewsResearcherAgent:
    """Specialist Agent 2: Market News & Catalyst Researcher (1B over GLiNER-gated signals)."""

    NAME = "News Researcher"
    SYSTEM = (
        "You are 📰 [News Researcher], a financial catalyst specialist. "
        "Summarize the verified headlines and their direction (bullish/bearish/neutral) in 2 concise sentences. "
        "Ignore noise and never invent facts or numbers."
    )

    def __init__(self, llm: SmallLLMBackend, store: NewsStorePort) -> None:
        self.llm = llm
        self.store = store

    def run(self, tickers: Sequence[str], bus: EventBus, session_id: str) -> SpecialistReport:
        facts: Dict[str, Any] = {}
        allowed: List[float] = []

        for tkr in tickers:
            bus.publish(A2AEvent(session_id, "TOOL_CALL", self.NAME, {"tool": "get_gated_signals", "ticker": tkr}))
            outcomes = [
                o for o in self.store.get_gated_signals(tkr, limit=5)
                if o.action in (Action.ALERT, Action.DIGEST)
            ]
            facts[tkr] = [
                {
                    "title": o.item.title,
                    "source": o.item.source,
                    "tier": o.action.value,
                    "direction": o.judgment.direction if o.judgment else "unclear",
                    "material_p": o.judgment.material if o.judgment else 0.0,
                }
                for o in outcomes
            ]
            for row in facts[tkr]:
                allowed.extend(numbers_in(row["title"]))
                allowed.append(float(row["material_p"]))

        prompt = f"Gated Signals (already filtered by GLiNER2.5-Decide + SignalGate):\n{facts}"
        raw = self.llm.complete(self.SYSTEM, prompt, max_tokens=200)
        clean = final_answer(raw) or "📰 [News Researcher] No material catalysts in the current window."
        # If the 1B model mentions numbers, they must come from the headlines or scores
        nums = numbers_in(clean)
        faithful = (not nums) or score_numbers_faithful(clean, allowed)
        if not faithful:
            clean = self._deterministic_fallback(facts)

        report = SpecialistReport(
            agent_name=self.NAME,
            tickers=list(tickers),
            raw_facts=facts,
            allowed_numbers=allowed,
            chart_refs=[],
            summary=clean,
            faithful_numbers=faithful,
        )
        bus.publish(
            A2AEvent(
                session_id,
                "AGENT_RESULT",
                self.NAME,
                {"tickers": list(tickers), "faithful_numbers": faithful, "summary": clean},
            )
        )
        return report

    @staticmethod
    def _deterministic_fallback(facts: Mapping[str, Any]) -> str:
        items = []
        for tkr, rows in facts.items():
            for r in rows:
                items.append(f"{tkr} ({r['direction']}): {r['title']}")
        return "📰 [News Researcher] " + ("; ".join(items) if items else "No material catalysts.")


@dataclass(frozen=True)
class TeamRunResult:
    routed: RoutedQuery
    risk_mode: str
    delegated_agents: List[str]
    data_report: Optional[SpecialistReport]
    news_report: Optional[SpecialistReport]
    final_synthesis: str
    faithful_synthesis: bool
    events: List[A2AEvent]


class TeamLeadOrchestrator:
    """Agent 3 + Deterministic Coordinator: dispatches specialists via GLiNER2.5-Decide, synthesizes via 1B."""

    NAME = "Market Team Lead"
    SYSTEM = (
        "You are 🧠 [Market Team Lead], Senior Investment Analyst. "
        "Combine the Data Analyst report and News Researcher report into a 3-sentence executive synthesis. "
        "Use ONLY numbers present in the specialist reports. State key risks clearly."
    )

    def __init__(
        self,
        router: GlinerQueryRouter,
        data_agent: DataAnalystAgent,
        news_agent: NewsResearcherAgent,
        lead_llm: SmallLLMBackend,
        tools: MarketDataTools,
        bus: Optional[EventBus] = None,
    ) -> None:
        self.router = router
        self.data_agent = data_agent
        self.news_agent = news_agent
        self.lead_llm = lead_llm
        self.tools = tools
        self.bus = bus or EventBus()

    def run(self, user_query: str, session_id: str = "default", fallback_tickers: Sequence[str] = ()) -> TeamRunResult:
        routed = self.router.route(user_query)
        tickers = routed.tickers or list(fallback_tickers)
        risk_mode = detect_risk_mode(tickers)

        # Decide which specialist agents run based on GLiNER2.5-Decide intent
        delegated: List[str] = []
        if routed.intent in {"price_snapshot", "compare_assets", "thesis_review", "watchlist_add"}:
            delegated.append(DataAnalystAgent.NAME)
        if routed.intent in {"news_catalysts", "compare_assets", "thesis_review"}:
            delegated.append(NewsResearcherAgent.NAME)

        self.bus.publish(
            A2AEvent(
                session_id,
                "EXECUTIVE_ROUTE",
                "GLiNER2.5-Decide",
                {
                    "intent": routed.intent,
                    "confidence": routed.intent_confidence,
                    "horizon": routed.horizon,
                    "tickers": tickers,
                    "risk_mode": risk_mode,
                    "delegated_agents": delegated,
                },
            )
        )

        # Deterministic side-effect replace v1's "MANDATORY SILENT TOOL EXECUTION" prompt rule
        for tkr in tickers:
            self.tools.add_to_watchlist(tkr)
            self.bus.publish(
                A2AEvent(session_id, "TOOL_CALL", self.NAME, {"tool": "add_to_watchlist", "ticker": tkr})
            )

        data_rep: Optional[SpecialistReport] = None
        news_rep: Optional[SpecialistReport] = None

        if DataAnalystAgent.NAME in delegated and tickers:
            self.bus.publish(
                A2AEvent(
                    session_id,
                    "AGENT_DELEGATION",
                    self.NAME,
                    {"to": DataAnalystAgent.NAME, "tickers": tickers, "risk_mode": risk_mode},
                )
            )
            data_rep = self.data_agent.run(tickers, risk_mode, self.bus, session_id)

        if NewsResearcherAgent.NAME in delegated and tickers:
            self.bus.publish(
                A2AEvent(
                    session_id,
                    "AGENT_DELEGATION",
                    self.NAME,
                    {"to": NewsResearcherAgent.NAME, "tickers": tickers},
                )
            )
            news_rep = self.news_agent.run(tickers, self.bus, session_id)

        allowed_numbers: List[float] = []
        if data_rep:
            allowed_numbers.extend(data_rep.allowed_numbers)
        if news_rep:
            allowed_numbers.extend(news_rep.allowed_numbers)

        synth_prompt = (
            f"Risk Mode: {risk_mode}\n"
            f"Data Analyst Report: {data_rep.summary if data_rep else 'N/A'}\n"
            f"News Researcher Report: {news_rep.summary if news_rep else 'N/A'}"
        )
        raw_synth = self.lead_llm.complete(self.SYSTEM, synth_prompt, max_tokens=256)
        clean_synth = final_answer(raw_synth) or ""
        nums = numbers_in(clean_synth)
        faithful = bool(clean_synth) and ((not nums) or score_numbers_faithful(clean_synth, allowed_numbers))
        if not faithful:
            parts = [p.summary for p in (data_rep, news_rep) if p]
            clean_synth = " ".join(parts)

        self.bus.publish(
            A2AEvent(
                session_id,
                "GATE_CHECK",
                self.NAME,
                {"faithful_synthesis": faithful, "allowed_numbers_count": len(allowed_numbers)},
            )
        )

        return TeamRunResult(
            routed=routed,
            risk_mode=risk_mode,
            delegated_agents=delegated,
            data_report=data_rep,
            news_report=news_rep,
            final_synthesis=clean_synth,
            faithful_synthesis=faithful,
            events=list(self.bus.history),
        )
