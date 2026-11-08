"""Validated ticker extraction.

Replaces the legacy `\\$?[A-Z]{1,5}` extractor in utils/memory.py, which matches the
first capital letter of every capitalised word ("What about Tesla?" -> W, T) and
treats words like THE / USA / AI as tickers.

Rules:
  * a candidate must be in the caller-supplied universe (watchlist + known symbols);
  * symbols that collide with common English words only count with an explicit $cashtag;
  * tokens are matched on word boundaries, so "Should" never yields "S".
"""
from __future__ import annotations

import re
from typing import Iterable, List

# Real symbols that are also everyday words. Bare occurrences are ambiguous.
AMBIGUOUS = frozenset({
    "A", "I", "ALL", "ARE", "AI", "ANY", "BE", "BIG", "CAT", "FOR", "GO", "GOOD",
    "HAS", "IT", "KEY", "LOW", "MAN", "NEW", "NOW", "ON", "ONE", "OPEN", "OUT",
    "PEAK", "REAL", "SEE", "SO", "TWO", "UP", "WELL", "WORK", "YOU",
})

_TOKEN = re.compile(r"(?<![A-Za-z0-9$])(\$?)([A-Za-z]{1,5})(?![A-Za-z0-9])")


def extract_tickers(text: str, universe: Iterable[str]) -> List[str]:
    known = {u.upper() for u in universe}
    found: List[str] = []
    for m in _TOKEN.finditer(text or ""):
        cashtag, word = m.group(1) == "$", m.group(2)
        sym = word.upper()
        if sym not in known:
            continue
        if cashtag:                       # $nvda or $NVDA: explicit intent
            ok = True
        else:                             # bare token must be written in capitals
            ok = word.isupper() and sym not in AMBIGUOUS
        if ok and sym not in found:
            found.append(sym)
    return found
