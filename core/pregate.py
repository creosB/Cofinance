"""Deterministic pre-gate for Clock 1. Runs before any model sees an item.

  * quarantine: instruction-like text ("ignore previous instructions", a leading "SYSTEM:", "mark this as
    material") is dropped before judging. GLiNER cannot be talked into calling tools, but the live seed run
    showed such text can still push its materiality score up, so it never reaches the judge.
  * title fingerprint: the same headline syndicated under different URLs is judged once.

Patterns are deliberately narrow: a false positive silently drops real news, so ordinary phrases such as
"operating system: iOS" or "regulators ignore safety rules" must pass (see tests).
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from .signals import Item

_RULES = (
    ("override", re.compile(
        r"\b(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|preceding|"
        r"your|these|those)\s+(instructions?|rules?|prompts?|directions?|guidelines?)\b", re.I)),
    ("role_marker", re.compile(r"(^|[\n\[(<])\s*(system|assistant|developer)\s*:", re.I)),
    ("role_tag", re.compile(r"</?\s*(system|instructions?|prompt)\s*>", re.I)),
    ("persona", re.compile(r"\byou\s+are\s+now\b", re.I)),
    ("label_steering", re.compile(
        r"\b(mark|classify|label|rate|flag|treat|score)\s+(this|it)\s+(item\s+|headline\s+|story\s+|post\s+)?"
        r"(as\s+)?(material|urgent|immediate|bullish|bearish|an?\s+alert|important)\b", re.I)),
)


# Prediction-market style items are bets on an outcome, not reported events. They are kept out of the digest with a
# visible reason (IGNORE "speculation:..." in the run log), never silently dropped and never judged into an alert.
# Genuine reported questions ("Can Apple's iPhone revive sales? Analysts weigh in") still go to the judge: a question
# headline is speculation only if it reads like a market's resolution rule, a yes/no question with a deadline or a
# price line and nothing reported after it.
_MARKETS = frozenset({"polymarket", "kalshi", "manifold", "predictit", "metaculus"})
_MARKET_ODDS = re.compile(r"\b(polymarket|kalshi|manifold|predictit|metaculus|prediction\s+markets?|betting\s+markets?)\b"
                          r"[^.?!]{0,80}\b(odds|chances?|probability|bettors?|traders?\s+(?:price|see|give|bet)|\d{1,3}\s?%)", re.I)
_YES_NO = re.compile(r"^\s*(will|could|can|would|does|do|is|are|should|has|have)\b[^?]{3,160}\?\s*$", re.I)
_RESOLUTION = re.compile(
    r"\b(before|by|until|ahead\s+of)\s+(the\s+)?(end\s+of\s+)?(january|february|march|april|may|june|july|august|september|"
    r"october|november|december|q[1-4]|year[-\s]end|the\s+year|20\d\d|monday|tuesday|wednesday|thursday|friday|next\s+\w+)\b"
    r"|\b(above|below|over|under|past|reach|hit|top|exceed|close\s+above|close\s+below)\s+\$?\d"
    r"|\$\d[\d,.]*\s*(k|m|b|bn|trillion|billion)?\b[^?]*\?\s*$", re.I)


def speculation_reason(item: Item) -> Optional[str]:
    if (item.source or "").strip().lower() in _MARKETS:
        return "prediction_market"
    if _MARKET_ODDS.search(f"{item.title}\n{item.text}"):
        return "market_odds"
    if _YES_NO.match(item.title or "") and _RESOLUTION.search(item.title or ""):
        return "market_style_question"
    return None


def quarantine_reason(item: Item) -> Optional[str]:
    text = f"{item.title}\n{item.text}"
    for name, rx in _RULES:
        if rx.search(text):
            return name
    return None


def title_key(item: Item) -> str:
    norm = " ".join(re.sub(r"[^a-z0-9]+", " ", item.title.lower()).split())
    return hashlib.sha1(f"T|{item.ticker.upper()}|{norm}".encode()).hexdigest()
