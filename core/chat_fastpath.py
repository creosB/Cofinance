"""Deterministic sub-millisecond chat fast-lane for greetings and unambiguous queries.

Runs before model or router startup so conversational messages answer immediately
without blocking on local model loads, and crystal-clear ticker queries avoid
unnecessary encoder waits while nuanced or PDF queries fall through to GLiNER.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

from .entities import extract_known_tickers
from .gliner_decide import DEFAULT_ALIASES, RoutedQuery

_GREETINGS = frozenset({
    "hi", "hey", "hello", "yo", "hiya", "sup", "selam", "merhaba", "gm",
    "good morning", "good afternoon", "good evening",
    "hi there", "hey there", "hello there",
    "how are you", "how are you doing", "whats up", "what's up", "how's it going",
})

_THANKS = frozenset({
    "thanks", "thank you", "thx", "ty", "cheers", "appreciated",
    "thanks a lot", "thank you so much", "tesekkurler", "sag ol",
    "ok", "okay", "got it", "cool", "great", "nice", "awesome", "sounds good",
})

_HELP = frozenset({
    "help", "what can you do", "how does this work", "who are you",
    "what are you", "commands", "examples", "how do i use this",
})

_PRICE_RE = re.compile(
    r"\b(?:price|priced|trading\s+at|quote|worth|market\s+cap|valuation|p/e|pe\s+ratio|"
    r"eps|volume|ath|all-time\s+high|52[- ]week)\b",
    re.IGNORECASE,
)
_NEWS_RE = re.compile(
    r"\b(?:news|headlines?|catalysts?|announcements?|latest\s+updates?|"
    r"what(?:'s|\s+is)\s+moving|happening\s+with|sentiment)\b",
    re.IGNORECASE,
)
_COMPARE_RE = re.compile(
    r"\b(?:compare|comparison|vs\.?|versus|side[- ]by[- ]side|against|"
    r"which\s+(?:one\s+)?looks\s+stronger)\b",
    re.IGNORECASE,
)
_WATCHLIST_RE = re.compile(
    r"\b(?:add\b.*\bwatchlist|watchlist|track|keep\s+an\s+eye\s+on|follow\s+(?:up\s+on\s+)?(?:this\s+)?(?:asset|stock|ticker)?)\b",
    re.IGNORECASE,
)
_FOLLOW_EXCLUDE_RE = re.compile(r"\bfollow[- ]?up\b|\bfollowed\s+by\b", re.IGNORECASE)
_THESIS_BLOCK_RE = re.compile(
    r"\b(?:thesis|bull|bear|long[- ]term\s+hold|risks?|outlook|analysis|should\s+i|"
    r"deep\s+dive|fundamental|pros\s+and\s+cons|case\s+for|why\s+is|explain)\b",
    re.IGNORECASE,
)
_INTRADAY_RE = re.compile(
    r"\b(?:today|right\s+now|now|current|currently|live|intraday|trading\s+at|priced\s+today)\b",
    re.IGNORECASE,
)
_LONG_TERM_RE = re.compile(
    r"\b(?:long[- ]term|next\s+year|years|5[- ]year|multi[- ]year)\b",
    re.IGNORECASE,
)


def _normalize_social(query: str) -> str:
    cleaned = re.sub(r"[!?.,:;~]+", " ", (query or "").strip().lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def conversational_reply(
    query: str,
    *,
    session_tickers: Sequence[str] = (),
    has_documents: bool = False,
    universe: Iterable[str] = (),
    aliases: Optional[Mapping[str, str]] = None,
) -> Optional[Tuple[str, str]]:
    text = (query or "").strip()
    if not text or "$" in text:
        return None
    norm = _normalize_social(text)
    if not norm or len(norm.split()) > 6:
        return None
    active_aliases = DEFAULT_ALIASES if aliases is None else aliases
    if universe and extract_known_tickers(text, universe, active_aliases):
        return None

    tickers = [t for t in session_tickers if t]
    ticker_list = ", ".join(tickers)

    if norm in _GREETINGS:
        if tickers:
            return (
                "greeting",
                f"Hi! Still focused on {ticker_list}. Ask for a price snapshot, collected news, a comparison, or attach a PDF.",
            )
        if has_documents:
            return (
                "greeting",
                "Hi! Your attached PDF is ready. Ask a question about it, or name a stock or crypto ticker like NVDA or BTC.",
            )
        return (
            "greeting",
            "Hi! Name a stock or crypto asset (like NVDA, Apple, or BTC) for live prices, collected news, comparisons, or attach a PDF to inspect.",
        )

    if norm in _THANKS:
        if tickers:
            return (
                "acknowledgement",
                f"You're welcome. Keeping {ticker_list} in context if you want news, risks, or a comparison next.",
            )
        return (
            "acknowledgement",
            "You're welcome. Let me know which ticker, comparison, or PDF you want to look at next.",
        )

    if norm in _HELP:
        return (
            "help",
            "I run local financial research on stocks and crypto: live price snapshots, collected news digests, side-by-side comparisons, watchlist suggestions, and attached PDF analysis. Try: 'What is NVDA trading at?' or 'Compare AMD and NVDA'.",
        )

    return None


def fast_route(
    query: str,
    universe: Iterable[str],
    aliases: Optional[Mapping[str, str]] = None,
    *,
    has_documents: bool = False,
) -> Optional[RoutedQuery]:
    if has_documents:
        return None
    text = (query or "").strip()
    if not text or len(text) > 220:
        return None
    if _THESIS_BLOCK_RE.search(text):
        return None

    active_aliases = DEFAULT_ALIASES if aliases is None else aliases
    tickers = extract_known_tickers(text, universe, active_aliases)
    if not tickers:
        return None

    stripped_tokens = re.findall(r"[A-Za-z0-9$]+", text)
    ticker_only = (
        len(tickers) == 1
        and len(stripped_tokens) == 1
        and stripped_tokens[0].lstrip("$").upper() == tickers[0]
    )

    matched: List[str] = []
    if ticker_only or _PRICE_RE.search(text):
        matched.append("price_snapshot")
    if _NEWS_RE.search(text):
        matched.append("news_catalysts")
    if _COMPARE_RE.search(text):
        if len(tickers) < 2:
            return None
        matched.append("compare_assets")
    if _WATCHLIST_RE.search(text) and not _FOLLOW_EXCLUDE_RE.search(text):
        matched.append("watchlist_add")

    if len(matched) != 1:
        return None

    intent = matched[0]
    if _INTRADAY_RE.search(text) or ticker_only:
        horizon = "intraday"
    elif _LONG_TERM_RE.search(text):
        horizon = "long_term"
    else:
        horizon = "swing_week"

    return RoutedQuery(
        intent=intent,
        intent_confidence=0.99,
        horizon=horizon,
        tickers=tickers,
    )
