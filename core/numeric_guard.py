"""Scale-aware numeric tokens for the narrator guards in core.orchestrator.

"4.51 trillion" is read as 4.51e12, so a narrator that writes "4.51 billion" for a 4.51 trillion market cap is
rejected instead of matching the bare 4.51. Scale words (thousand, million, billion, trillion) and attached
shorthand (9k, 11.5M, 11.5B, 11.5bn, 4.51T) mean the same value, on both sides: a headline "$11.5B" allows
"$11.5 billion" and "$11.5B" but never a bare "11.5" or "11.5 million" (live 2026-11-17: the AMZN Globalstar
headline fell back because "$11.5B" was read as 11.5 and the narration's "$11.5 billion" as 11.5e9).
Shorthand must touch the digits ("4B model", not "4 B"); M6, Q3 or RTX 5090 are never scaled.

qualify/scorers.py is deliberately left unchanged: it is part of the narrator qualification fingerprint
(core/local_models.py), and the qualification cases contain no scale words.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Tuple

SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
          "k": 1e3, "K": 1e3, "M": 1e6, "mn": 1e6, "B": 1e9, "bn": 1e9, "T": 1e12, "tn": 1e12}
_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*\.?\d*%?"
                  r"(?:\s+(?P<word>thousand|million|billion|trillion)\b|(?P<short>k|K|M|mn|B|bn|T|tn)\b)?", re.I)


def number_spans(text: str) -> List[Tuple[int, int, float]]:
    """(start, end, value) for every number the guards read, scale words included in the span and the value."""
    out: List[Tuple[int, int, float]] = []
    for m in _NUM.finditer(text or ""):
        word, short = m.group("word"), m.group("short")
        cut = m.start("word") if word else m.start("short") if short else m.end()
        # re.I makes the shorthand group case-insensitive; only the exact spellings in SCALES count ("5m" stays 5).
        if short and short not in SCALES:
            short = None
        raw = m.group(0)[: cut - m.start(0)].strip().rstrip("%").replace(",", "").rstrip(".")
        try:
            value = float(raw)
        except ValueError:
            continue
        end = m.end() if (word or short) else cut
        while end > m.start() and text[end - 1] in ",.":
            end -= 1
        scale = SCALES[word.lower()] if word else SCALES[short] if short else 1.0
        out.append((m.start(), end, value * scale))
    return out


def numbers_in(text: str) -> List[float]:
    return [value for _, _, value in number_spans(text)]


def matches(n: float, a: float, tol: float = 1e-9) -> bool:
    return abs(n - a) <= max(tol, abs(a) * tol)


def score_numbers_faithful(answer: str, allowed: Iterable[float], tol: float = 1e-9) -> bool:
    """Every number in the answer, scale included, must be one the narrator was shown; at least one must appear."""
    allowed = [float(a) for a in allowed]
    nums = numbers_in(answer)
    return bool(nums) and all(any(matches(n, a, tol) for a in allowed) for n in nums)
