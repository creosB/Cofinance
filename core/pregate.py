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


def quarantine_reason(item: Item) -> Optional[str]:
    text = f"{item.title}\n{item.text}"
    for name, rx in _RULES:
        if rx.search(text):
            return name
    return None


def title_key(item: Item) -> str:
    norm = " ".join(re.sub(r"[^a-z0-9]+", " ", item.title.lower()).split())
    return hashlib.sha1(f"T|{item.ticker.upper()}|{norm}".encode()).hexdigest()
