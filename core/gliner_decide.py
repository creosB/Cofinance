"""GLiNER2.5-Decide local System-1 adapter (`fastino/GLiNER2.5-Decide`, 340M encoder).

Why this fits Cofinance v2:
  * Runs 100% locally on CPU or GPU via `pip install "gliner2[local]"`.
  * Single forward pass scores multiple classification heads + extracts entities.
  * Generates zero tokens -> immune to generative prompt-injection jailbreaks and JSON syntax failures.
  * Label descriptions (`{"labels": {name: description}}`) give zero-shot financial taxonomy precision.

This module keeps `gliner2` optional at import time so unit tests and headless CI run without
downloading model weights. Pass any object implementing `ExtractorBackend` (or use
`GlinerDecideAdapter.from_pretrained()`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Set, Tuple

from .entities import extract_tickers
from .signals import Classifier, Item, Judgment


class ExtractorBackend(Protocol):
    def classify_text(
        self,
        text: str,
        schema: Mapping[str, Any],
        *,
        include_confidence: bool = False,
    ) -> Dict[str, Any]: ...

    def extract_entities(
        self,
        text: str,
        entity_types: Sequence[str],
    ) -> Dict[str, Any]: ...


SIGNAL_SCHEMA: Dict[str, Any] = {
    "materiality": {
        "labels": {
            "material": (
                "Catalyst that plausibly changes the investment thesis or moves the price within a week: "
                "earnings, guidance change, M&A, regulatory or legal action, executive change, major contract or product shift"
            ),
            "noise": (
                "Routine commentary, listicle, generic market recap, minor PR, charity event, or repetition without new facts"
            ),
        },
    },
    "urgency": {
        "labels": {
            "immediate": "Breaking catalyst or halt risk requiring attention before the next scheduled digest",
            "digest": "Worth reading in the daily or weekly digest, not an immediate alert",
            "none": "No follow-up needed",
        },
    },
    "direction": {
        "labels": {
            "bullish": "Positive fundamental or sentiment catalyst for the asset",
            "bearish": "Negative fundamental, regulatory, or sentiment catalyst for the asset",
            "neutral": "Balanced or purely informational update",
            "unclear": "Ambiguous or conflicting impact",
        },
    },
}

USER_QUERY_SCHEMA: Dict[str, Any] = {
    "intent": {
        "labels": {
            "price_snapshot": "Current price, volume, valuation multiples, or technical snapshot for one or more assets",
            "news_catalysts": "Recent news, social sentiment, or catalysts over the last few days or weeks",
            "compare_assets": "Side-by-side comparison of two or more tickers or crypto assets",
            "watchlist_add": "Track, follow, or add an asset to the user's watchlist",
            "thesis_review": "Full bull vs bear thesis combining price fundamentals, recent news, and memory",
        },
    },
    "horizon": ["intraday", "swing_week", "long_term"],
}


def _unpack_label_conf(raw: Any, default_label: str = "") -> Tuple[str, float]:
    """Normalise gliner2 classify_text outputs across plain-string and confidence-dict modes."""
    if isinstance(raw, str):
        return raw, 1.0
    if isinstance(raw, Mapping):
        label = str(raw.get("label") or raw.get("category") or default_label)
        conf = raw.get("confidence", raw.get("score", 1.0))
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            conf_f = 1.0
        return label, max(0.0, min(1.0, conf_f))
    return default_label, 0.0


class GlinerOutputError(ValueError):
    """A gating head came back missing or with a label outside the schema. SignalGate turns it into DEFERRED."""


def _schema_labels(entry: Any) -> Set[str]:
    labels = entry.get("labels", []) if isinstance(entry, Mapping) else entry
    return set(labels.keys() if isinstance(labels, Mapping) else labels)


@dataclass(frozen=True)
class RoutedQuery:
    intent: str
    intent_confidence: float
    horizon: str
    tickers: List[str]


class GlinerDecideClassifier(Classifier):
    """Implements `core.signals.Classifier` on top of `fastino/GLiNER2.5-Decide`."""

    def __init__(self, backend: ExtractorBackend) -> None:
        self.backend = backend

    @classmethod
    def from_pretrained(cls, model_id: str = "fastino/GLiNER2.5-Decide") -> "GlinerDecideClassifier":
        from gliner2 import AutoExtractor  # lazy import so tests stay lightweight

        return cls(AutoExtractor.from_pretrained(model_id))

    def judge(self, item: Item) -> Judgment:
        passage = f"[{item.ticker.upper()}] {item.title}".strip()
        if item.text:
            passage = f"{passage}\n{item.text.strip()}"
        try:
            res = self.backend.classify_text(passage, SIGNAL_SCHEMA, include_confidence=True)
        except TypeError:
            # Fallback if a backend stub does not accept include_confidence
            res = self.backend.classify_text(passage, SIGNAL_SCHEMA)

        # Fail closed: a missing or unknown head must never default to a confident label.
        # (Defaulting materiality to "noise" @ 0.0 used to produce material_p = 1.0.)
        heads: Dict[str, Tuple[str, float]] = {}
        for head in ("materiality", "urgency", "direction"):
            raw = res.get(head) if isinstance(res, Mapping) else None
            if raw is None:
                raise GlinerOutputError(f"GLiNER output has no {head!r} head")
            label, conf = _unpack_label_conf(raw)
            if label not in _schema_labels(SIGNAL_SCHEMA[head]):
                raise GlinerOutputError(f"GLiNER {head!r} label {label!r} is not in the schema")
            heads[head] = (label, conf)
        mat_label, mat_conf = heads["materiality"]
        urg_label, urg_conf = heads["urgency"]
        dir_label, dir_conf = heads["direction"]

        if mat_label == "material":
            material_p = mat_conf
        else:
            material_p = max(0.0, 1.0 - mat_conf)

        if urg_label == "immediate":
            urgent_p = urg_conf
        elif urg_label == "digest":
            urgent_p = min(0.55, urg_conf * 0.55)
        else:
            urgent_p = max(0.0, (1.0 - urg_conf) * 0.25)

        return Judgment(
            material=round(material_p, 4),
            urgent=round(urgent_p, 4),
            direction=dir_label,
            direction_p=round(dir_conf, 4),
        )


class GlinerQueryRouter:
    """Routes a user question + resolves both explicit tickers (`NVDA`, `$sol`) and company names (`Tesla` -> `TSLA`)."""

    def __init__(
        self,
        backend: ExtractorBackend,
        universe: Iterable[str],
        alias_to_ticker: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.backend = backend
        self.universe: Set[str] = {u.upper() for u in universe}
        self.aliases: Dict[str, str] = {
            k.strip().lower(): v.strip().upper()
            for k, v in (alias_to_ticker or DEFAULT_ALIASES).items()
        }

    def route(self, query: str) -> RoutedQuery:
        try:
            cls_res = self.backend.classify_text(query, USER_QUERY_SCHEMA, include_confidence=True)
        except TypeError:
            cls_res = self.backend.classify_text(query, USER_QUERY_SCHEMA)

        intent, intent_conf = _unpack_label_conf(cls_res.get("intent"), "price_snapshot")
        horizon, _ = _unpack_label_conf(cls_res.get("horizon"), "swing_week")

        tickers = extract_tickers(query, self.universe)
        seen = set(tickers)

        # Resolve company / crypto names extracted by GLiNER2.5 NER spans
        try:
            ner = self.backend.extract_entities(query, ["company", "cryptocurrency", "ticker"])
            entities = (ner or {}).get("entities", ner or {})
        except Exception:
            entities = {}

        for span_list in entities.values() if isinstance(entities, Mapping) else []:
            for span in span_list or []:
                norm = str(span).strip().lstrip("$")
                upper = norm.upper()
                mapped = self.aliases.get(norm.lower()) or (upper if upper in self.universe else None)
                if mapped and mapped in self.universe and mapped not in seen:
                    tickers.append(mapped)
                    seen.add(mapped)

        return RoutedQuery(
            intent=intent,
            intent_confidence=round(intent_conf, 4),
            horizon=horizon,
            tickers=tickers,
        )


DEFAULT_ALIASES: Dict[str, str] = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "advanced micro devices": "AMD",
    "amd": "AMD",
    "tesla": "TSLA",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
}
