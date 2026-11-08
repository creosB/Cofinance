"""Multi-Agent Orchestrator for Cofinance v2 (1B Specialists + GLiNER2.5-Decide Executive).

Why Cofinance v1's orchestration breaks on 1B models:
  In v1 (`agents/team_lead.py`), the Team Lead is a generative LLM that must read 150+ lines of instructions,
  emit nested tool calls (`call_data_analyst`, `call_news_researcher`, `add_to_watchlist`), wait for worker LLMs
  to emit their own tool calls (`get_market_data`, `plot_stock_history`, `get_company_news`), and then synthesize.
  1B models fail nested tool-calling loops (BFCL non-live ~43%).

How Cofinance v2 preserves the 3-agent team ("Data Analyst", "News Researcher", "Team Lead") on 1B:
  1. GLiNER2.5-Decide (System-1 encoder; 486,444,053 parameters measured locally) acts as the deterministic Routing & Risk Executive:
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
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

import re

from .claims import direction_conflicts, unsupported_terms
from .risk_cues import cue_block, risk_cues, unsupported_risks
from .gliner_decide import ExtractorBackend, GlinerQueryRouter, RoutedQuery
from .signals import Action, Outcome
from qualify.scorers import final_answer  # scorers.py is in the qualification fingerprint: never edit it for guard changes
from .numeric_guard import numbers_in, score_numbers_faithful  # not "numbers": that name shadows the stdlib in core/ scripts


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


def number_review(candidate: str, allowed, passed: bool) -> Dict[str, Any]:
    """Observed numeric tokens and policy result, never model reasoning."""
    values = list(allowed)
    observed = numbers_in(candidate)
    rejected = list(dict.fromkeys(n for n in observed if not any(
        abs(n-a) <= max(1e-9, abs(a)*1e-9) for a in values)))
    reason = ("numbers_matched" if passed else "empty_candidate" if not candidate else
              "no_numeric_evidence" if not values else "unsupported_numeric_tokens" if rejected else "candidate_rejected")
    return {"allowed_numbers": values, "observed_numbers": observed,
            "rejected_numbers": rejected, "validation_reason": reason}


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
    validation: Dict[str, Any] = field(default_factory=dict)


# Narrator-facing labels avoid digits ("one-year", not "52-week") because every numeric token in an
# answer must match tool evidence. Timestamps, URLs and status strings never reach the narrator; the UI
# shows provenance deterministically from the same snapshot.
FACT_LABELS = (
    ("price", "Last price"), ("previous_session_close", "Previous session close"),
    ("change_from_previous_session_pct", "Change since previous session close (percent)"),
    ("high_52_week", "One-year high"), ("low_52_week", "One-year low"), ("volume", "Session volume"),
    ("pe_ratio", "Trailing P/E"), ("forward_pe_ratio", "Forward P/E"), ("eps", "Trailing EPS"),
    ("market_cap", "Market cap"), ("revenue_ttm", "Revenue over the trailing twelve months"),
    ("all_time_high", "All-time high in source history"),
    ("drawdown_from_all_time_high_pct", "Drawdown from all-time high (percent)"),
    ("drawdown_from_52_week_high_pct", "Drawdown from one-year high (percent)"),
    ("annualized_daily_log_volatility_pct", "Annualized volatility (percent)"),
    ("fear_greed_index", "Crypto Fear and Greed index (market-wide)"),
)
CURRENCY_FIELDS = {"price", "previous_session_close", "high_52_week", "low_52_week", "eps", "all_time_high", "market_cap",
                   "revenue_ttm"}
FUNDAMENTAL_FIELDS = ("pe_ratio", "forward_pe_ratio", "eps", "market_cap", "revenue_ttm")
# Raw tool fields that are provenance or easily misread, never narrated as facts.
NOT_NARRATED = {"fetched_at", "price_as_of", "fear_greed_as_of", "close_before_window", "series_previous_close",
                "previous_close", "status", "source", "error", "currency", "name", "ticker"}


def first_paragraph(text: str) -> str:
    """Small narrators often restate the same facts as a second block. Keep the first block only;
    trimming can remove model text but never adds any, and the number check runs on what is kept."""
    return text.strip().split("\n\n", 1)[0].strip()


ROLE_TAG = re.compile(r"^\s*(?:[^\w\s\[]{1,3}\s*)?\[(?:Data Analyst|News Researcher|Market Team Lead)\]\s*[:,\-]?\s*")


def clean_narration(text: str) -> str:
    """Role tag echoed from the system prompt, markdown emphasis and repeated blocks are removed."""
    return first_paragraph(ROLE_TAG.sub("", (text or "").replace("**", "")))


def check_wording(clean: str, evidence: str, faithful: bool, validation: Dict[str, Any]) -> bool:
    """Number check first, then the wording check (core.claims). Records the terms that failed."""
    terms = unsupported_terms(clean, evidence) if faithful else []
    validation["unsupported_terms"] = terms
    if terms:
        validation["validation_reason"] = "unsupported_terms"
    return faithful and not terms


def direction_review(candidate: str, facts: Mapping[str, Any], faithful: bool, validation: Dict[str, Any]) -> bool:
    """Rejects a narration that states the opposite of the computed move since the previous close, for any asset."""
    if not faithful or not candidate:
        return faithful
    wrong: List[str] = []
    for snap in facts.values():
        if not isinstance(snap, Mapping):
            continue
        change, close = snap.get("change_from_previous_session_pct"), snap.get("previous_session_close")
        if _numeric(change) and _numeric(close):
            direction = "up" if change > 0 else "down" if change < 0 else "unchanged"
            wrong += direction_conflicts(candidate, [display_value("previous_session_close", close)[1]], direction)
    if wrong:
        validation["direction_conflicts"] = list(dict.fromkeys(wrong))
        validation["validation_reason"] = "direction_conflict"
    return not wrong


def fact_value(value: Any) -> str:
    """Exact source value. Large integers get separators; floats keep their recorded digits."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int) and abs(value) >= 10000:
        return f"{value:,}"
    return str(value)


# Readable display, done by Python, never by the model (live 2026-11-08: exact copies such as "4,506,796,080,000" and
# "23.5954" passed every check but were unreadable). Large counts get a scale word, ratios and percentages two decimals.
# The narrator is shown only the displayed value, and the allowlist holds exactly that value with its scale
# (core/numbers.py), so "4.51 billion" for a 4.51 trillion market cap is rejected. Prices stay as the source wrote them.
SCALED_FIELDS = {"market_cap", "revenue_ttm", "volume"}
TWO_DECIMAL_FIELDS = {"pe_ratio", "forward_pe_ratio", "eps", "drawdown_from_52_week_high_pct",
                      "drawdown_from_all_time_high_pct", "annualized_daily_log_volatility_pct"}
_SCALE_WORDS = (("trillion", 1e12), ("billion", 1e9), ("million", 1e6))


def display_value(key: str, value: Any) -> Tuple[str, float]:
    """(text shown to the narrator, the number that text means)."""
    if key == "change_from_previous_session_pct":
        # The sign is a word, so the narrator never has to write or read a minus sign; the size is allowlisted.
        shown = round(abs(value), 2)
        word = "up" if value > 0 else "down" if value < 0 else "unchanged"
        return (f"{word} {shown:.2f}" if shown else word), shown
    if key in SCALED_FIELDS and abs(value) >= 1e6:
        for word, size in _SCALE_WORDS:
            if abs(value) >= size:
                shown = round(value / size, 2)
                return f"{shown:.2f}".rstrip("0").rstrip(".") + " " + word, shown * size
    if key in TWO_DECIMAL_FIELDS and isinstance(value, float):
        shown = round(value, 2)
        return f"{shown:.2f}", shown
    return fact_value(value), float(value)


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def market_fact_numbers(ticker: str, snap: Mapping[str, Any]) -> List[float]:
    """Exactly the numbers market_fact_lines shows, in the meaning shown (scale included). The narrator allowlist."""
    if snap.get("error") or snap.get("status") == "unavailable" or not any(_numeric(v) for v in snap.values()):
        return []
    return [display_value(key, value)[1] for key, value in snap.items() if key not in NOT_NARRATED and _numeric(value)]


def market_fact_lines(ticker: str, snap: Mapping[str, Any]) -> List[str]:
    """Labelled facts for one asset, without dates, sources or status strings."""
    name = str(snap.get("name") or ticker)
    head = f"{name} ({ticker})" if name != ticker else ticker
    if snap.get("error") or snap.get("status") == "unavailable" or not any(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in snap.values()):
        return [f"{head}: market data unavailable. No values were substituted."]
    currency = str(snap.get("currency") or "")
    lines = [head]
    known = {key for key, _ in FACT_LABELS}
    for key, label in FACT_LABELS:
        value = snap.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            unit = f" {currency}" if currency and key in CURRENCY_FIELDS else ""
            lines.append(f"{label}: {display_value(key, value)[0]}{unit}")
    if isinstance(snap.get("fear_greed_label"), str) and "fear_greed_index" in snap:
        lines[-1] += f" ({snap['fear_greed_label']})"
    for key, value in snap.items():  # unknown numeric fields stay visible rather than silently dropped
        if key in known or key in NOT_NARRATED or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        lines.append(f"{key.replace('_', ' ').capitalize()}: {display_value(key, value)[0]}")
    if snap.get("fundamentals_status") and not any(k in snap for k in FUNDAMENTAL_FIELDS):
        lines.append("Fundamentals: not available from this source")
    return lines


class DataAnalystAgent:
    """Specialist Agent 1: Hybrid Stocks & Crypto Data Analyst (1B)."""

    NAME = "Data Analyst"
    SYSTEM_STOCK = (
        "You are 📊 [Data Analyst], an equity data specialist. "
        "Summarize the facts in two short sentences. Copy every number exactly as written. "
        "Never round. Do not add dates, times, percentages, headings, or any number that is not in the facts."
    )
    SYSTEM_CRYPTO = (
        "You are 📊 [Data Analyst] in Risk Guardian Mode (crypto specialist). "
        "Summarize price, drawdown, volatility, and market sentiment in two short sentences. "
        "Copy every number exactly as written. Never round. Do not add dates, times, headings, or new numbers. "
        "If the Fear and Greed index is above seventy-five, warn that sentiment may be overheated."
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
            # Only values shown to the narrator, in the form shown (readable, scale included), are allowed back out.
            allowed.extend(market_fact_numbers(tkr, snap))

            bus.publish(A2AEvent(session_id, "TOOL_CALL", self.NAME, {"tool": "plot_history", "ticker": tkr}))
            charts.append(self.tools.plot_history(tkr))

        sys_prompt = self.SYSTEM_CRYPTO if risk_mode == "risk_guardian_crypto" else self.SYSTEM_STOCK
        prompt = "Facts from market-data tools:\n" + "\n\n".join(
            "\n".join(market_fact_lines(tkr, snap)) for tkr, snap in facts.items())
        try:
            raw = self.llm.complete(sys_prompt, prompt, max_tokens=256) if allowed else ""
            clean = clean_narration(final_answer(raw) or "")
        except Exception:
            clean = ""
        faithful = bool(clean) and bool(allowed) and score_numbers_faithful(clean, allowed)
        validation = number_review(clean, allowed, faithful)
        faithful = check_wording(clean, prompt, faithful, validation)
        faithful = direction_review(clean, facts, faithful, validation)
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
            validation=validation,
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
        """Tool values as plain sentences. No dates, so later narration cannot echo them as numbers."""
        parts = []
        for tkr, snap in facts.items():
            lines = market_fact_lines(tkr, snap)
            if len(lines) == 1:
                parts.append(lines[0])
            else:
                parts.append(lines[0] + ". " + ". ".join(lines[1:]) + ".")
        return " ".join(parts) or "Market data unavailable. No values were substituted."


class NewsResearcherAgent:
    """Specialist Agent 2: Market News & Catalyst Researcher (1B over GLiNER-gated signals)."""

    NAME = "News Researcher"
    SYSTEM = (
        "You are 📰 [News Researcher], a financial catalyst specialist. "
        "In at most two sentences and under sixty words, summarize what the accepted headlines say and their direction. "
        "Group similar headlines instead of listing each one. Use product and company names exactly as the headlines do, "
        "and do not add product categories or claims about momentum, volume, or price. "
        "Company announcements are issuer claims. Never invent facts, dates, or numbers."
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

        # Headlines only, with tier and direction words. Scores and dates stay in the trace and UI.
        lines = [f"- {tkr}: {row['title']} (source: {row['source'].replace('_', ' ')}; {row['tier']}; {row['direction']})"
                 for tkr, rows in facts.items() for row in rows]
        prompt = "Accepted headlines from the local news store:\n" + "\n".join(lines)
        try:
            raw = self.llm.complete(self.SYSTEM, prompt, max_tokens=320) if any(facts.values()) else ""
            clean = clean_narration(final_answer(raw) or "")
        except Exception:
            clean = ""
        nums = numbers_in(clean)
        faithful = bool(clean) and bool(any(facts.values())) and ((not nums) or score_numbers_faithful(clean, allowed))
        validation = number_review(clean, allowed, faithful)
        faithful = check_wording(clean, prompt, faithful, validation)
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
            validation=validation,
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
                items.append(f"{tkr}: {r['title']} ({r['source'].replace('_', ' ')}, {r['tier']}, {r['direction']})")
        return ("Accepted news: " + "; ".join(items) + ".") if items else (
            "No accepted news is available in the local store. This does not establish that no catalysts exist.")


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
    MAX_RESTATED = 2
    STRICT_RISK = ("\nStrict: name a risk only from the listed risks, in their words, or say the reports do not identify "
                   "a specific risk. Do not mention execution, supply chain, competition, valuation or any other risk that is not listed.")
    RETRY_REASONS = {"unsupported_risk", "unsupported_terms", "unsupported_numeric_tokens", "direction_conflict"}

    @staticmethod
    def rejected(validation: Mapping[str, Any]) -> List[str]:
        """What a guard rejected, as short strings (shown in the trace and fed back to the retry)."""
        return [str(x)[:80] for key in ("unsupported_risks", "unsupported_terms", "rejected_numbers", "direction_conflicts")
                for x in (validation.get(key) or [])][:6]

    def feedback(self, validation: Mapping[str, Any]) -> str:
        reason, items = validation.get("validation_reason"), "; ".join(self.rejected(validation))
        if reason == "unsupported_risk":
            return self.STRICT_RISK + f" Remove: {items}."
        if reason == "unsupported_terms":
            return (f"\nStrict: these words are not in the reports, so do not use them: {items}. "
                    "Use the reports' own words and do not abbreviate.")
        if reason == "unsupported_numeric_tokens":
            return "\nStrict: some numbers in your draft are not in the reports. Copy numbers exactly from the reports, or leave them out."
        return ("\nStrict: your draft stated the wrong move since the previous close. Use the reports' change since the previous "
                "close exactly, or leave the comparison out.")
    STRICT_FOLLOW_UP = ("\nStrict: your answer must be one or two sentences with no more than two numbers. "
                        "Do not list the price, range, volume, valuation or revenue again; the user already has them.")
    SYSTEM = (
        "You are 🧠 [Market Team Lead], Senior Investment Analyst. "
        "Answer the user's question in at most three short sentences, using only the specialist reports. "
        "For a thesis, cover what the data shows, what the news shows, and the main risk, taken only from the listed risks. "
        "If no risk is listed, say the reports do not identify a specific risk. Never add a risk of your own. "
        "For a follow-up, answer that question directly and do not repeat the previous answer. "
        "Copy numbers exactly as written and never round. Do not add headings, dates, or numbers that are not in the reports. "
        "Do not call volume, momentum, or valuation high, low, strong, or weak: the reports contain no comparison data. "
        "Use only names and terms that appear in the reports. "
        "If a report says data or news is unavailable, say so plainly instead of guessing."
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

    def run(self, user_query: str, session_id: str = "default", fallback_tickers: Sequence[str] = (),
            context: str = "") -> TeamRunResult:
        """`context` is the previous accepted answer in this conversation (local chat history, not a
        memory model). It is data for the lead only; its numbers are NOT added to the allowlist."""
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

        # Chat never changes the watchlist. Explicit UI actions own user-data writes.
        # A model-classified intent is not permission to mutate user state.
        if routed.intent == "watchlist_add":
            self.bus.publish(A2AEvent(session_id, "WATCHLIST_ACTION_REQUIRED", self.NAME,
                                     {"tickers": tickers, "reason": "explicit_user_action_required"}))

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

        previous = " ".join(context.split())[:1400]
        # Closed risk list from tool facts (core/risk_cues.py): the Lead may name only these, or none.
        cues = risk_cues(data_rep.raw_facts if data_rep else {}, news_rep.raw_facts if news_rep else {})
        synth_prompt = (
            f"User question: {' '.join(user_query.split())[:600]}\n"
            + (f"Conversation memory, earlier accepted answers (context only): {previous}\n" if previous else "")
            + ("Crypto asset: emphasize downside risk and sentiment.\n" if risk_mode == "risk_guardian_crypto" else "")
            + "\n"
            f"Data Analyst report:\n{data_rep.summary if data_rep else 'Not requested for this question.'}\n\n"
            f"News Researcher report:\n{news_rep.summary if news_rep else 'Not requested for this question.'}\n\n"
            + cue_block(cues)
            # Last, because small narrators follow the most recent instruction (live 2026-11-08: placed before the
            # reports, it was ignored and every follow-up restated the full fact block).
            + ("\n\nTask: this is a follow-up. The earlier answer already gave the figures. Answer only the user question "
               "in at most two sentences, and use at most two numbers, only ones the question asks for." if previous else "")
        )
        # Only numbers in accepted reports can be reused by the lead, not unseen tool fields.
        allowed_numbers = [n for report in (data_rep, news_rep) if report for n in numbers_in(report.summary)
                           if any(abs(n-a) <= max(1e-9, abs(a)*1e-9) for a in report.allowed_numbers)]
        # Accepted reports plus the Python-derived risk list (digit-free, from tool facts). Never the question or memory.
        # The agent role names are the prompt's own section headers ("News Researcher report:"), not factual claims.
        # Found live 2026-11-08: an Apple answer saying "the News Researcher reports..." fell back on "Researcher".
        wording_evidence = "\n".join(["Data Analyst report. News Researcher report. Market Team Lead."]
                                     + [p.summary for p in (data_rep, news_rep) if p] + [c["text"] for c in cues])
        earlier = numbers_in(previous) if previous else []

        def narrate(prompt: str, max_tokens: int) -> str:
            try:
                raw = self.lead_llm.complete(self.SYSTEM, prompt, max_tokens=max_tokens) if (data_rep or news_rep) else ""
                # Markdown emphasis is stripped (the UI renders plain text); a restated second block is dropped.
                return clean_narration(final_answer(raw) or "")
            except Exception:
                return ""

        def judge(text: str) -> Tuple[bool, Dict[str, Any]]:
            """Every guard, in order: numbers (with scale), wording, direction. Same rules for a retry."""
            ok = bool(text) and ((not numbers_in(text)) or score_numbers_faithful(text, allowed_numbers))
            val = number_review(text, allowed_numbers, ok)
            # Wording evidence is the accepted reports only: not the question, not the previous answer.
            ok = check_wording(text, wording_evidence, ok, val)
            ok = direction_review(text, data_rep.raw_facts if data_rep else {}, ok, val)
            invented = unsupported_risks(text, cues) if ok else []
            if invented:
                val["unsupported_risks"] = invented
                val["validation_reason"] = "unsupported_risk"
                ok = False
            if previous:
                val["follow_up"] = True
                val["restated_numbers"] = sum(1 for n in numbers_in(text) if any(abs(n - p) <= max(1e-9, abs(p) * 1e-9) for p in earlier))
            return ok, val

        clean_synth = narrate(synth_prompt, 256)
        faithful, validation = judge(clean_synth)
        retried = False
        # Feedback retry (2026-11-08): a first draft rejected by a guard gets ONE retry whose last instruction names exactly
        # what was rejected (an invented risk, a word not in the reports such as "ATH", a number, a wrong direction).
        # Same guards; if the retry also fails, the deterministic fallback (the specialist reports) is used.
        reason = validation.get("validation_reason")
        if not faithful and clean_synth and reason in self.RETRY_REASONS:
            retried = True
            retry = narrate(synth_prompt + self.feedback(validation), 256)
            retry_ok, retry_val = judge(retry)
            summary = {"reason": reason, "rejected": self.rejected(validation), "retry_passed_guards": retry_ok,
                       "used": "retry" if retry_ok else "fallback",
                       **({} if retry_ok else {"retry_reason": retry_val.get("validation_reason"), "retry_rejected": self.rejected(retry_val)})}
            if retry_ok:
                clean_synth, faithful, validation = retry, True, retry_val
            validation["guard_retry"] = summary
        # Strict follow-ups (approved 2026-11-08): an accepted follow-up that repeats more than two figures from the
        # earlier answer gets ONE retry with a stricter last instruction. The retry passes every guard again and is
        # used only if it is faithful and repeats fewer figures, so strictness costs at most one narrator call and
        # can never make the answer worse. Greedy decoding needs a different prompt to give a different answer.
        if previous and faithful and not retried and validation.get("restated_numbers", 0) > self.MAX_RESTATED:
            retry = narrate(synth_prompt + self.STRICT_FOLLOW_UP, 160)
            retry_ok, retry_val = judge(retry)
            better = retry_ok and retry_val["restated_numbers"] < validation["restated_numbers"]
            summary = {"first_restated": validation["restated_numbers"], "retry_restated": retry_val.get("restated_numbers"),
                       "retry_passed_guards": retry_ok, "used": "retry" if better else "first",
                       **({} if retry_ok else {"retry_reason": retry_val.get("validation_reason")})}
            if better:
                clean_synth, validation = retry, retry_val
            validation["follow_up_retry"] = summary
        if not faithful:
            parts = [p.summary for p in (data_rep, news_rep) if p]
            clean_synth = " ".join(parts)

        self.bus.publish(
            A2AEvent(
                session_id,
                "GATE_CHECK",
                self.NAME,
                {"faithful_synthesis": faithful, "allowed_numbers_count": len(allowed_numbers), **validation},
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
