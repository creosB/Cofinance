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
  2. Each Specialist Agent (`DataAnalystAgent`, `NewsResearcherAgent`, `TeamLeadOrchestrator`) has:
     - Deterministic tool pre-execution in Python (charts, fundamentals, gated news, watchlist updates).
     - The Data Analyst and Team Lead: a short role prompt to the selected narrator over ONLY their slice.
     - The News Researcher: no model call; `news_digest` quotes the served headlines (since 2026-11-17).
     - A news-only question is answered in Python from the digest and listed risks, without the Team Lead call.
  3. Full A2A EventBus (`A2AMessage`, `A2AEvent`) + Glass-Box trace so the UI shows live agent handoffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

import re

from .claim_provenance import check_claim_provenance, format_conflict_summary
from .claims import direction_conflicts, unsupported_terms
from .urgency import OFFICIAL
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

    def complete(self, system: str, prompt: str, *, max_tokens: int = 256, **options: Any) -> str: ...
    # options: optional 'stop' (list of strings). Callers pass it only when set, so plain backends keep working.


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


# Removed, never added (2026-11-17 news diagnostic): the base narrator appended word counts such as "(28 words)" to 21
# of 35 summaries, which then failed the number check; reasoning-tag leftovers; em and en dashes, which the UI must not
# show (Rules page). A dash between digits becomes a hyphen ("2025-2026"), any other becomes a comma.
# Also "(1 sentence, 22 words)" and "(Two headlines grouped.)": a parenthetical about the reply's own length or form.
WORD_COUNT = re.compile(r"\s*[\(\[][^()\[\]]*\b(?:words?|sentences?|headlines grouped)\b[^()\[\]]*[\)\]]", re.I)
THINK = re.compile(r"<think>.*?</think>|</?think>", re.S | re.I)
_DIGIT_DASH = re.compile(r"(?<=\d)\s*[\u2013\u2014]\s*(?=\d)")
_DASH = re.compile(r"\s*[\u2013\u2014]+\s*")


def plain_text(text: str) -> str:
    text = THINK.sub("", text or "")
    text = WORD_COUNT.sub("", text)
    text = text.replace("\u202f", " ").replace("\u00a0", " ").replace("\u2011", "-")
    text = _DIGIT_DASH.sub("-", text)
    text = _DASH.sub(", ", text)
    return re.sub(r",\s*([,.;:!?])", r"\1", text).replace(" ,", ",")


def clean_narration(text: str) -> str:
    """Role tag echoed from the system prompt, markdown emphasis, word counts, dashes and repeated blocks are removed."""
    return first_paragraph(plain_text(ROLE_TAG.sub("", THINK.sub("", text or "").lstrip().replace("**", ""))))


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


def provenance_review(
    candidate: str,
    facts: Mapping[str, Any],
    faithful: bool,
    validation: Dict[str, Any],
    *,
    other_numbers: Sequence[float] = (),
) -> bool:
    if not faithful or not candidate or not facts:
        return faithful
    conflicts = check_claim_provenance(candidate, facts, other_numbers=other_numbers)
    if conflicts:
        validation["provenance_conflicts"] = conflicts
        validation["validation_reason"] = "claim_provenance"
        return False
    return True


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

    def __init__(self, llm: SmallLLMBackend, tools: MarketDataTools, stop: Optional[Sequence[str]] = ("\n\n",)) -> None:
        self.llm = llm
        self.tools = tools
        # LFM3 writes its summary as the first paragraph, then keeps restating the facts until the token cap.
        # Ending the reply at the first blank line took replay fallbacks from 51 of 72 to 0 (v1 vs v2, 2026-11-16).
        # Pass stop=None for the old behaviour (replay v1 does, so it stays reproducible).
        self.stop = list(stop) if stop else None

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
            options = {"stop": self.stop} if self.stop else {}
            raw = self.llm.complete(sys_prompt, prompt, max_tokens=256, **options) if allowed else ""
            clean = clean_narration(final_answer(raw) or "")
        except Exception:
            clean = ""
        faithful = bool(clean) and bool(allowed) and score_numbers_faithful(clean, allowed)
        validation = number_review(clean, allowed, faithful)
        faithful = check_wording(clean, prompt, faithful, validation)
        faithful = direction_review(clean, facts, faithful, validation)
        faithful = provenance_review(clean, facts, faithful, validation)
        invented_risks = unsupported_risks(clean, risk_cues(facts, {})) if faithful else []
        if invented_risks:
            validation["unsupported_risks"] = invented_risks
            validation["validation_reason"] = "unsupported_risk"
            faithful = False
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


# Issuer sources publish the company's own claims; every official feed in core.urgency is one (test_news_grounding).
ISSUER_SOURCES = frozenset(OFFICIAL) | {"company_release"}
SOURCE_NAMES = {"apple_newsroom": "Apple Newsroom", "nvidia_newsroom": "NVIDIA Newsroom", "hackernews": "Hacker News",
                "company_release": "company release"}


def source_label(source: str) -> str:
    """What the narrator and the fallback call a headline's one source."""
    name = SOURCE_NAMES.get(source, str(source).replace("_", " "))
    if source in ISSUER_SOURCES:
        return f"{name}, company announcement"
    if source == "hackernews":
        return f"{name}, shared link"
    return name


class NewsResearcherAgent:
    """Specialist Agent 2: News Researcher. A Python digest of the gated headlines, never model narration.

    grounded-v1 to v4 (2026-11-17, Work log): every prompt and wording-guard variant still let the 1.7B narrator
    mislabel sources ("Tesla reported", "Amazon says") or reverse a headline, under rules frozen before their data.
    Headlines quoted as served, grouped by their one source, are faithful by construction (Baris chose this, 11/17).
    `llm` stays in the signature so callers and traces keep working; this agent never calls it.
    """

    NAME = "News Researcher"
    METHOD = "python_digest"

    def __init__(self, llm: Optional[SmallLLMBackend], store: NewsStorePort) -> None:
        self.llm = llm  # unused on purpose: Python writes the news report
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

        has_news = any(facts.values())
        summary = news_digest(facts)
        observed = numbers_in(summary)
        rejected = [n for n in observed if not any(abs(n - a) <= max(1e-9, abs(a) * 1e-9) for a in allowed)]
        # Built from the served titles, so every number is a title's own; the check stays as a runtime proof.
        faithful = has_news and not rejected
        validation = {"allowed_numbers": allowed, "observed_numbers": observed, "rejected_numbers": rejected,
                      "unsupported_terms": [], "method": self.METHOD,
                      "validation_reason": ("no_news" if not has_news else "python_digest" if faithful
                                            else "unsupported_numeric_tokens")}

        report = SpecialistReport(
            agent_name=self.NAME,
            tickers=list(tickers),
            raw_facts=facts,
            allowed_numbers=allowed,
            chart_refs=[],
            summary=summary,
            faithful_numbers=faithful,
            validation=validation,
        )
        bus.publish(
            A2AEvent(
                session_id,
                "AGENT_RESULT",
                self.NAME,
                {"tickers": list(tickers), "faithful_numbers": faithful, "summary": summary, "method": self.METHOD},
            )
        )
        return report


NO_NEWS = "No accepted news is available in the local store. This does not establish that no catalysts exist."


def news_digest(facts: Mapping[str, Sequence[Mapping[str, Any]]]) -> str:
    """Served headlines verbatim (dashes cleaned), grouped by ticker and by their one source, in served order.
    Company feeds are labelled company announcements, Hacker News items shared links. No gate labels, no scores."""
    parts: List[str] = []
    for tkr, rows in facts.items():
        groups: Dict[str, List[str]] = {}
        for row in rows:
            title = plain_text(str(row["title"])).strip().rstrip(".")
            if title:
                groups.setdefault(row["source"], []).append(title)
        for source, titles in groups.items():
            one = len(titles) == 1
            kind = ("company announcement" if one else "company announcements") if source in ISSUER_SOURCES else (
                ("shared link" if one else "shared links") if source == "hackernews" else ("headline" if one else "headlines"))
            name = SOURCE_NAMES.get(source, str(source).replace("_", " "))
            parts.append(f"{tkr}, from {name} ({kind}): " + "; ".join(titles) + ".")
    return " ".join(parts) if parts else NO_NEWS


class DocumentReaderAgent:
    """Python only: verbatim excerpts from the PDFs attached to this conversation, with page numbers. No model call.
    Allowed numbers: the numbers inside the excerpts, the page numbers and any in the file names Python wrote."""

    NAME = "Document Reader"

    def run(self, question: str, documents: Sequence[Mapping[str, Any]], bus: EventBus, session_id: str) -> SpecialistReport:
        from .documents import group_excerpts, select_excerpts
        excerpts = select_excerpts(documents, question)
        groups = group_excerpts(excerpts)
        ordered = [e for g in groups for e in g["excerpts"]]
        if ordered:
            note = "" if all(e["matched"] for e in ordered) else " No page shares words with the question, so the first pages are shown."
            if len(groups) > 1:
                blocks = [
                    f'Source "{g["name"]}":\n' + "\n".join(f'  Page {e["page"]}: "{e["text"]}"' for e in g["excerpts"])
                    for g in groups
                ]
                summary = ("Excerpts from the attached files, quoted by Python and grouped by source file." + note + "\n"
                           + "\n".join(blocks))
            else:
                summary = ("Excerpts from the attached files, quoted by Python." + note + "\n"
                           + "\n".join(f'From "{e["name"]}", page {e["page"]}: "{e["text"]}"' for e in ordered))
        else:
            summary = "The attached files have no readable text."
        allowed = ([n for e in ordered for n in numbers_in(e["text"])] + [float(e["page"]) for e in ordered]
                   + [n for e in ordered for n in numbers_in(str(e["name"]))])
        group_meta = [{"document_id": g["document_id"], "name": g["name"], "pages": [e["page"] for e in g["excerpts"]]} for g in groups]
        validation = {"validation_reason": "python_excerpts",
                      "document_count": len(groups),
                      "groups": group_meta,
                      "excerpts": [{"name": e["name"], "page": e["page"], "matched": e["matched"], "characters": len(e["text"])}
                                   for e in ordered]}
        return SpecialistReport(agent_name=self.NAME, tickers=[], raw_facts={"excerpts": ordered, "groups": group_meta}, allowed_numbers=allowed,
                                chart_refs=[], summary=summary, faithful_numbers=True, validation=validation)


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
    document_report: Optional[SpecialistReport] = None


class TeamLeadOrchestrator:
    """Agent 3 + Deterministic Coordinator: dispatches specialists via GLiNER2.5-Decide, synthesizes via 1B."""

    NAME = "Market Team Lead"
    MAX_RESTATED = 2
    STRICT_RISK = ("\nStrict: name a risk only from the listed risks, in their words, or say the reports do not identify "
                   "a specific risk. Do not mention execution, supply chain, competition, valuation or any other risk that is not listed.")
    RETRY_REASONS = {"unsupported_risk", "unsupported_terms", "unsupported_numeric_tokens", "direction_conflict", "claim_provenance"}

    @staticmethod
    def rejected(validation: Mapping[str, Any]) -> List[str]:
        """What a guard rejected, as short strings (shown in the trace and fed back to the retry)."""
        items = [str(x)[:80] for key in ("unsupported_risks", "unsupported_terms", "rejected_numbers", "direction_conflicts")
                 for x in (validation.get(key) or [])]
        if validation.get("provenance_conflicts"):
            items.extend(format_conflict_summary(validation["provenance_conflicts"]))
        return items[:6]

    def feedback(self, validation: Mapping[str, Any]) -> str:
        reason, items = validation.get("validation_reason"), "; ".join(self.rejected(validation))
        if reason == "unsupported_risk":
            return self.STRICT_RISK + f" Remove: {items}."
        if reason == "unsupported_terms":
            return (f"\nStrict: these words are not in the reports, so do not use them: {items}. "
                    "Use the reports' own words and do not abbreviate.")
        if reason == "unsupported_numeric_tokens":
            return "\nStrict: some numbers in your draft are not in the reports. Copy numbers exactly from the reports, or leave them out."
        if reason == "claim_provenance":
            return (f"\nStrict: some numbers were attached to the wrong asset, metric, or unit: {items}. "
                    "Match each number to its exact asset, label, and unit from the reports, or leave it out.")
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
        document_agent: Optional[DocumentReaderAgent] = None,
    ) -> None:
        self.router = router
        self.data_agent = data_agent
        self.news_agent = news_agent
        self.lead_llm = lead_llm
        self.tools = tools
        self.bus = bus or EventBus()
        self.document_agent = document_agent or DocumentReaderAgent()

    NEWS_ONLY = "python_news_answer"
    DATA_ONLY = "single_pass_data_answer"
    REQUESTED_METRICS = (
        (re.compile(r"\bforward\s+p/?e\b", re.I), ("forward_pe_ratio",), ("forward p/e", "forward pe")),
        (re.compile(r"\b(?:p/?e|price[- ]to[- ]earnings)\b", re.I), ("pe_ratio", "forward_pe_ratio"), ("p/e", "pe ratio", "price-to-earnings")),
        (re.compile(r"\beps\b|\bearnings per share\b", re.I), ("eps",), ("eps", "earnings per share")),
        (re.compile(r"\brevenue\b", re.I), ("revenue_ttm",), ("revenue",)),
        (re.compile(r"\bmarket\s+cap\b", re.I), ("market_cap",), ("market cap",)),
        (re.compile(r"\bvolume\b", re.I), ("volume",), ("volume",)),
        (re.compile(r"\b(?:52[- ]week|one[- ]year)\b", re.I), ("high_52_week", "low_52_week", "drawdown_from_52_week_high_pct"), ("52-week", "52 week", "one-year", "one year")),
        (re.compile(r"\b(?:all[- ]time\s+high|ath|drawdown)\b", re.I), ("all_time_high", "drawdown_from_all_time_high_pct", "ath_drawdown", "ath_drawdown_pct"), ("all-time high", "all time high", "ath", "drawdown")),
        (re.compile(r"\bvolatilit\w*\b", re.I), ("annualized_daily_log_volatility_pct",), ("volatilit", "price swing")),
        (re.compile(r"\b(?:fear|greed|sentiment)\b", re.I), ("fear_greed_index", "fear_greed"), ("fear", "greed", "sentiment", "overheated")),
        (re.compile(r"\bprevious\s+(?:session\s+)?close\b", re.I), ("previous_session_close",), ("previous session", "previous close")),
    )

    @staticmethod
    def _unwrap_traced_llm(llm: Any) -> Any:
        cur = llm
        if cur is not None and type(cur).__name__ == "TracedLLM" and hasattr(cur, "backend"):
            cur = cur.backend
        return cur

    def _same_narrator(self) -> bool:
        agent = getattr(self.data_agent, "agent", self.data_agent)
        data_llm = self._unwrap_traced_llm(getattr(agent, "llm", None))
        lead_llm = self._unwrap_traced_llm(self.lead_llm)
        if getattr(self.lead_llm, "is_thinking", False) or getattr(lead_llm, "is_thinking", False):
            return False
        return data_llm is not None and data_llm is lead_llm

    @classmethod
    def _missing_requested_metric(cls, user_query: str, summary: str, raw_facts: Mapping[str, Any]) -> bool:
        low_summary = (summary or "").lower()
        for pat, fact_keys, summary_terms in cls.REQUESTED_METRICS:
            if not pat.search(user_query or ""):
                continue
            has_fact = any(
                isinstance(snap, Mapping) and any(snap.get(k) is not None for k in fact_keys)
                for snap in (raw_facts or {}).values()
            )
            if has_fact and not any(term in low_summary for term in summary_terms):
                return True
        return False

    def _data_only(self, routed: RoutedQuery, risk_mode: str, delegated: List[str], data_rep: SpecialistReport,
                   cues: Sequence[Mapping[str, Any]], session_id: str) -> TeamRunResult:
        base = data_rep.summary.rstrip()
        low_base = base.lower()
        risks = [
            str(c["text"])
            for c in cues
            if not any(str(a).lower() in low_base for a in (c.get("anchors") or ()))
        ]
        if risks and base and not base.endswith((".", "!", "?")):
            base += "."
        answer = base + (" Risk the reports support: " + "; ".join(risks) + "." if risks else "")
        self.bus.publish(A2AEvent(session_id, "GATE_CHECK", self.NAME, {
            "faithful_synthesis": True,
            "method": self.DATA_ONLY,
            "validation_reason": self.DATA_ONLY,
            "allowed_numbers": list(data_rep.allowed_numbers),
            "allowed_numbers_count": len(data_rep.allowed_numbers),
            "unsupported_risks": [],
            "reused_specialist": DataAnalystAgent.NAME,
            "risks": risks,
        }))
        return TeamRunResult(routed=routed, risk_mode=risk_mode, delegated_agents=delegated, data_report=data_rep,
                             news_report=None, final_synthesis=answer, faithful_synthesis=True,
                             events=list(self.bus.history))

    def _news_only(self, routed: RoutedQuery, risk_mode: str, delegated: List[str], news_rep: SpecialistReport,
                   cues: Sequence[Mapping[str, Any]], session_id: str) -> TeamRunResult:
        """Digest + the closed risk list, both from Python. Faithful unless the digest itself failed its number check."""
        risks = [str(c["text"]) for c in cues]
        answer = news_rep.summary + (" Risk the reports support: " + "; ".join(risks) + "." if risks else "")
        reason = news_rep.validation.get("validation_reason")
        faithful = reason in ("python_digest", "no_news")
        self.bus.publish(A2AEvent(session_id, "GATE_CHECK", self.NAME, {
            "faithful_synthesis": faithful, "method": self.NEWS_ONLY, "validation_reason": self.NEWS_ONLY if faithful else reason,
            "allowed_numbers_count": len(news_rep.allowed_numbers), "risks": risks}))
        return TeamRunResult(routed=routed, risk_mode=risk_mode, delegated_agents=delegated, data_report=None,
                             news_report=news_rep, final_synthesis=answer, faithful_synthesis=faithful,
                             events=list(self.bus.history))

    def run(self, user_query: str, session_id: str = "default", fallback_tickers: Sequence[str] = (),
            context: str = "", documents: Optional[Sequence[Mapping[str, Any]]] = None) -> TeamRunResult:
        """`context` is the previous accepted answer in this conversation (local chat history, not a
        memory model). It is data for the lead only; its numbers are NOT added to the allowlist.
        `documents` are the PDFs attached to this conversation ({id, name, pages}); with any, the Document Reader runs
        and its excerpts are a report like the others."""
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

        doc_rep: Optional[SpecialistReport] = None
        if documents:
            self.bus.publish(A2AEvent(session_id, "AGENT_DELEGATION", self.NAME,
                                      {"to": DocumentReaderAgent.NAME, "documents": len(documents)}))
            doc_rep = self.document_agent.run(user_query, documents, self.bus, session_id)

        allowed_numbers: List[float] = []
        for report in (data_rep, news_rep, doc_rep):
            if report:
                allowed_numbers.extend(report.allowed_numbers)

        previous = " ".join(context.split())[:1400]
        # Closed risk list from tool facts (core/risk_cues.py): the Lead may name only these, or none.
        cues = risk_cues(data_rep.raw_facts if data_rep else {}, news_rep.raw_facts if news_rep else {})
        # News-only question (Baris, 2026-11-17): the answer is the Python digest plus the listed risks, no Lead call.
        # Live, the Lead only restated the digest and twice failed the risk guard ("the reports do not name a
        # particular risk" while one was listed), so the model had nothing to add except ways to be wrong.
        if news_rep is not None and data_rep is None and doc_rep is None:
            return self._news_only(routed, risk_mode, delegated, news_rep, cues, session_id)
        if (data_rep is not None and news_rep is None and doc_rep is None
                and not previous and data_rep.faithful_numbers and self._same_narrator()
                and not unsupported_risks(data_rep.summary, cues)
                and not self._missing_requested_metric(user_query, data_rep.summary, data_rep.raw_facts)):
            return self._data_only(routed, risk_mode, delegated, data_rep, cues, session_id)
        synth_prompt = (
            f"User question: {' '.join(user_query.split())[:600]}\n"
            + (f"Conversation memory, earlier accepted answers (context only): {previous}\n" if previous else "")
            + ("Crypto asset: emphasize downside risk and sentiment.\n" if risk_mode == "risk_guardian_crypto" else "")
            + "\n"
            f"Data Analyst report:\n{data_rep.summary if data_rep else 'Not requested for this question.'}\n\n"
            f"News Researcher report:\n{news_rep.summary if news_rep else 'Not requested for this question.'}\n\n"
            + (
                f"Document Reader report (files the user attached; keep each fact and restriction inside its own source file, and name the file and page you use):\n{doc_rep.summary}\n\n"
                if doc_rep and len(doc_rep.raw_facts.get("groups") or []) > 1
                else (f"Document Reader report (files the user attached; name the page you use):\n{doc_rep.summary}\n\n" if doc_rep else "")
            )
            + cue_block(cues)
            # Last, because small narrators follow the most recent instruction (live 2026-11-08: placed before the
            # reports, it was ignored and every follow-up restated the full fact block).
            + ("\n\nTask: this is a follow-up. The earlier answer already gave the figures. Answer only the user question "
               "in at most two sentences, and use at most two numbers, only ones the question asks for." if previous else "")
        )
        # Only numbers in accepted reports can be reused by the lead, not unseen tool fields.
        allowed_numbers = [n for report in (data_rep, news_rep, doc_rep) if report for n in numbers_in(report.summary)
                           if any(abs(n-a) <= max(1e-9, abs(a)*1e-9) for a in report.allowed_numbers)]
        # Accepted reports plus the Python-derived risk list (digit-free, from tool facts). Never the question or memory.
        # The agent role names are the prompt's own section headers ("News Researcher report:"), not factual claims.
        # Found live 2026-11-08: an Apple answer saying "the News Researcher reports..." fell back on "Researcher".
        wording_evidence = "\n".join(["Data Analyst report. News Researcher report. Document Reader report. Market Team Lead."]
                                     + [p.summary for p in (data_rep, news_rep, doc_rep) if p] + [c["text"] for c in cues])
        earlier = numbers_in(previous) if previous else []

        def narrate(prompt: str, max_tokens: int) -> str:
            try:
                raw = self.lead_llm.complete(self.SYSTEM, prompt, max_tokens=max_tokens) if (data_rep or news_rep or doc_rep) else ""
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
            extra_nums = [
                *(news_rep.allowed_numbers if news_rep else ()),
                *(doc_rep.allowed_numbers if doc_rep else ()),
            ]
            ok = provenance_review(
                text,
                data_rep.raw_facts if data_rep else {},
                ok,
                val,
                other_numbers=extra_nums,
            )
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
            parts = [p.summary for p in (data_rep, news_rep, doc_rep) if p]
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
            document_report=doc_rep,
        )
