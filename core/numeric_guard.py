"""Scale-aware numeric tokens for the narrator guards in core.orchestrator.

"4.51 trillion" is read as 4.51e12, so a narrator that writes "4.51 billion" for a 4.51 trillion market cap is
rejected instead of matching the bare 4.51. Only full scale words count (thousand, million, billion, trillion);
shorthand such as "4.51T" is read as 4.51 and fails unless that exact value was shown, which keeps the rule strict.

qualify/scorers.py is deliberately left unchanged: it is part of the narrator qualification fingerprint
(core/local_models.py), and the qualification cases contain no scale words.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Tuple

SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*\.?\d*%?(?:\s+(thousand|million|billion|trillion)\b)?", re.I)


def number_spans(text: str) -> List[Tuple[int, int, float]]:
    """(start, end, value) for every number the guards read, scale words included in the span and the value."""
    out: List[Tuple[int, int, float]] = []
    for m in _NUM.finditer(text or ""):
        scale = m.group(1)
        raw = m.group(0)[: m.start(1) - m.start(0)] if scale else m.group(0)
        raw = raw.strip().rstrip("%").replace(",", "").rstrip(".")
        try:
            value = float(raw)
        except ValueError:
            continue
        end = m.end()
        while end > m.start() and text[end - 1] in ",.":
            end -= 1
        out.append((m.start(), end, value * SCALES[scale.lower()] if scale else value))
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
