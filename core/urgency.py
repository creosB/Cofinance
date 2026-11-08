"""Rule-based urgency: a deterministic replacement candidate for GLiNER's urgency head.

Why: on the 2026-11-08 corpus the urgency head ranked worse than random against labels (train AUC 0.37), and
materiality scores never exceed ~0.66, so the current alert line could not fire. These rules flag event types that
usually need attention within about a day. Status: candidate only. Written after reading the 2026-11-08 headlines,
so its fit there is optimistic; it is NOT wired into SignalGate until it passes a fresh held-out corpus.

Input is the headline, plus the summary for official issuer feeds. HN text is a reader comment and is ignored.
Every hit returns the rule name and the matched words, so a trace can show exactly why something was urgent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

RULES: Tuple[Tuple[str, str], ...] = (
    # Reported results, beats/misses, guidance. Scheduling notices are excluded below.
    ("results", r"\b(?:q[1-4]|first|second|third|fourth)[- ](?:quarter|fiscal)?.{0,20}\bresults\b"
                r"|\bannounces? (?:q[1-4] )?(?:fiscal )?\d{4} results\b|\breports? .{0,25}\bresults\b"
                r"|\b(?:beats?|miss(?:es)?) (?:wall street |analyst )?(?:expectations|estimates)\b"
                r"|\brecord quarter\b|\b(?:raises?|cuts?|lowers?|withdraws?) (?:its |full-year )?(?:guidance|outlook|forecast)\b"
                r"|\bq[1-4] \d{4} earnings\b|\bearnings (?:beat|miss)\b"),
    # Large market-value moves stated in the headline.
    ("market_move", r"\$\d[\d.,]*\s?(?:b|bn|billion|t|tn|trillion)\b.{0,40}\b(?:drop|rout|loss|lose|loses|lost|wipe\w*|plunge\w*|tumble\w*)\b"
                    r"|\b(?:drop|rout|lose|loses|lost|plunge\w*|tumble\w*|soar\w*|surge\w*)\b.{0,40}\bmarket (?:value|cap\w*)\b"
                    r"|\bshares (?:tumble|plunge|plummet|soar|surge|jump|sink|crater|slump)\w*\b"),
    # Product or production stopped.
    ("production_stop", r"\b(?:ends?|ending|halts?|halting|suspends?|wind(?:s|ing)? down|stops?|discontinu\w+)\b.{0,30}\bproduction\b"
                        r"|\bpull(?:s|ing)? the plug\b|\brecalls? \d[\d,]* (?:vehicles|cars|units)\b"),
    # Trade or regulatory blocks with direct revenue effect.
    ("trade_block", r"\b(?:withholds?|withholding|blocks?|bans?|denies|rejects?|halts?)\b.{0,30}\b(?:import|export)s?\b.{0,20}\b(?:approval|licen[cs]es?)?"
                    r"|\b(?:import|export) (?:ban|curbs?|controls?)\b"),
)
NOT_URGENT = re.compile(r"\b(?:sets?|schedules?) (?:a )?conference call\b|\bto discuss (?:its )?financial results\b", re.I)
OFFICIAL = {"apple_newsroom", "nvidia_newsroom"}
_COMPILED = tuple((name, re.compile(pattern, re.I)) for name, pattern in RULES)


@dataclass(frozen=True)
class UrgencyHit:
    rule: str
    matched: str


def urgency(title: str, text: str = "", source: str = "") -> Optional[UrgencyHit]:
    passage = " ".join((title + (" " + text if source in OFFICIAL else "")).split())
    if not passage or NOT_URGENT.search(passage):
        return None
    for name, pattern in _COMPILED:
        found = pattern.search(passage)
        if found:
            return UrgencyHit(name, found.group(0)[:120])
    return None
