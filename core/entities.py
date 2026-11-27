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
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

AMBIGUOUS = frozenset({
    "A", "I", "ALL", "ARE", "AI", "ANY", "BE", "BIG", "CAT", "FOR", "GO", "GOOD",
    "HAS", "IT", "KEY", "LOW", "MAN", "NEW", "NOW", "ON", "ONE", "OPEN", "OUT",
    "PEAK", "REAL", "SEE", "SO", "TWO", "UP", "WELL", "WORK", "YOU",
})

LOWERCASE_TICKER_ALLOWLIST = frozenset({
    "nvda", "aapl", "msft", "tsla", "amzn", "googl", "goog", "amd",
    "btc", "eth", "sol", "qqq", "nflx", "intc", "pltr", "sofi",
    "baba", "rivn", "adbe", "orcl", "avax", "doge", "xrp", "bnb",
})

_TOKEN = re.compile(r"(?<![A-Za-z0-9$])(\$?)([A-Za-z]{1,5})(?![A-Za-z0-9])")
_LOWER_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,5})(?![A-Za-z0-9])")


def extract_tickers(text: str, universe: Iterable[str]) -> List[str]:
    known = {u.upper() for u in universe}
    found: List[str] = []
    for m in _TOKEN.finditer(text or ""):
        cashtag, word = m.group(1) == "$", m.group(2)
        sym = word.upper()
        if sym not in known:
            continue
        if cashtag:
            ok = True
        else:
            ok = word.isupper() and sym not in AMBIGUOUS
        if ok and sym not in found:
            found.append(sym)
    return found


def extract_known_tickers(
    text: str,
    universe: Iterable[str],
    aliases: Optional[Mapping[str, str]] = None,
) -> List[str]:
    known = {u.upper() for u in universe}
    found = extract_tickers(text, known)
    seen = set(found)
    source = text or ""

    alias_hits: List[Tuple[int, str]] = []
    for alias, raw_mapped in (aliases or {}).items():
        clean_alias = alias.strip().lower()
        mapped = raw_mapped.strip().upper()
        if not clean_alias or mapped not in known or mapped in seen:
            continue
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(clean_alias) + r"(?![A-Za-z0-9])", source, re.IGNORECASE)
        if m:
            alias_hits.append((m.start(), mapped))
    alias_hits.sort(key=lambda pair: pair[0])
    for _, mapped in alias_hits:
        if mapped not in seen:
            found.append(mapped)
            seen.add(mapped)

    for m in _LOWER_TOKEN.finditer(source):
        raw = m.group(1)
        low = raw.lower()
        sym = raw.upper()
        if sym in known and sym not in seen and low in LOWERCASE_TICKER_ALLOWLIST:
            found.append(sym)
            seen.add(sym)

    return found
