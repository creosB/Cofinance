"""Deterministic claim-provenance check for narrator outputs (stdlib only).

Verifies that numbers matching structured market facts are bound to a compatible
asset, metric field, and unit rather than merely appearing anywhere in the
numeric allowlist.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .numeric_guard import matches, number_spans

FIELD_UNIT: Dict[str, str] = {
    "price": "currency",
    "previous_session_close": "currency",
    "change_from_previous_session_pct": "percent",
    "high_52_week": "currency",
    "low_52_week": "currency",
    "volume": "count",
    "pe_ratio": "ratio",
    "forward_pe_ratio": "ratio",
    "eps": "currency",
    "market_cap": "currency",
    "revenue_ttm": "currency",
    "all_time_high": "currency",
    "drawdown_from_all_time_high_pct": "percent",
    "drawdown_from_52_week_high_pct": "percent",
    "annualized_daily_log_volatility_pct": "percent",
    "fear_greed_index": "index",
}

METRIC_CUES: Tuple[Tuple[re.Pattern[str], Tuple[str, ...], str], ...] = (
    (
        re.compile(
            r"\b(?:drawdown|down|below|off)\s+(?:from\s+)?(?:its\s+|the\s+)?(?:all[- ]time high|ath)\b",
            re.I,
        ),
        ("drawdown_from_all_time_high_pct",),
        "all_time_drawdown",
    ),
    (
        re.compile(
            r"\b(?:drawdown|down|below|off)\s+(?:from\s+)?(?:its\s+|the\s+)?(?:52[- ]week|one[- ]year|1[- ]year)\s+high\b",
            re.I,
        ),
        ("drawdown_from_52_week_high_pct",),
        "52_week_drawdown",
    ),
    (
        re.compile(r"\ball[- ]time high\b|\bath\b", re.I),
        ("all_time_high",),
        "all_time_high",
    ),
    (
        re.compile(r"\b(?:52[- ]week|one[- ]year|1[- ]year)\s+high\b", re.I),
        ("high_52_week",),
        "52_week_high",
    ),
    (
        re.compile(r"\b(?:52[- ]week|one[- ]year|1[- ]year)\s+low\b", re.I),
        ("low_52_week",),
        "52_week_low",
    ),
    (
        re.compile(
            r"\b(?:annualized(?:\s+daily(?:\s+log)?)?\s+volatility|daily\s+log\s+volatility|realized\s+volatility|volatility)\b",
            re.I,
        ),
        ("annualized_daily_log_volatility_pct",),
        "volatility",
    ),
    (
        re.compile(r"\bfear\s*(?:and|&)\s*greed(?:\s+index)?\b", re.I),
        ("fear_greed_index",),
        "fear_greed",
    ),
    (
        re.compile(r"\bforward\s+(?:p\s*/\s*e|pe|price[- ]to[- ]earnings)(?:\s+ratio)?\b", re.I),
        ("forward_pe_ratio",),
        "forward_pe",
    ),
    (
        re.compile(r"\btrailing\s+(?:p\s*/\s*e|pe|price[- ]to[- ]earnings)(?:\s+ratio)?\b", re.I),
        ("pe_ratio",),
        "trailing_pe",
    ),
    (
        re.compile(r"\b(?:p\s*/\s*e(?:\s+ratio)?|pe\s+ratio)\b", re.I),
        ("pe_ratio", "forward_pe_ratio"),
        "pe",
    ),
    (
        re.compile(r"\bmarket\s+cap(?:italization)?\b", re.I),
        ("market_cap",),
        "market_cap",
    ),
    (
        re.compile(r"\b(?:ttm\s+revenue|trailing(?:\s+twelve[- ]month)?\s+revenue|revenue(?:\s+over\s+the\s+trailing\s+twelve\s+months)?)\b", re.I),
        ("revenue_ttm",),
        "revenue",
    ),
    (
        re.compile(r"\b(?:trailing\s+eps|earnings\s+per\s+share|eps)\b", re.I),
        ("eps",),
        "eps",
    ),
    (
        re.compile(r"\b(?:session\s+volume|trading\s+volume|volume|shares\s+traded)\b", re.I),
        ("volume",),
        "volume",
    ),
    (
        re.compile(r"\b(?:previous\s+session\s+close|previous\s+close|prior\s+close)\b", re.I),
        ("previous_session_close",),
        "previous_close",
    ),
    (
        re.compile(
            r"\b(?:change\s+(?:since|from)\s+(?:the\s+)?previous\s+(?:session\s+)?close|daily\s+change|session\s+change)\b",
            re.I,
        ),
        ("change_from_previous_session_pct",),
        "change_pct",
    ),
    (
        re.compile(r"\b(?:last\s+price|current\s+price|trading\s+at|trades?\s+at|price)\b", re.I),
        ("price",),
        "price",
    ),
    (
        re.compile(r"\bdrawdown\b", re.I),
        ("drawdown_from_all_time_high_pct", "drawdown_from_52_week_high_pct"),
        "drawdown",
    ),
    (
        re.compile(r"\bhigh\b", re.I),
        ("high_52_week", "all_time_high"),
        "high",
    ),
    (
        re.compile(r"\blow\b", re.I),
        ("low_52_week",),
        "low",
    ),
)

_TEMPORAL_MODIFIER = re.compile(r"^\s*[- ]?(?:week|weeks|day|days|month|months|year|years|hour|hours)\b", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|;\s*")
_SUBCLAUSE_SPLIT = re.compile(r",\s*(?:and\s+|while\s+|whereas\s+|but\s+)?|\s+\b(?:while|whereas|versus|vs\.?)\b\s+", re.I)
_CURRENCY_BEFORE = re.compile(r"(?:[$€£])\s*$")
_CURRENCY_AFTER = re.compile(r"^\s*(?:usd|eur|gbp|dollars?|euros?|pounds?)\b", re.I)
_PERCENT_AFTER = re.compile(r"^\s*(?:%|percent|percentage)\b", re.I)
_COUNT_AFTER = re.compile(r"^\s*(?:shares?|contracts?)\b", re.I)
_NAME_STOP = frozenset({
    "inc", "corp", "corporation", "company", "co", "ltd", "limited", "plc",
    "holdings", "group", "technologies", "technology", "international", "class",
    "common", "stock", "trust", "etf", "fund", "usd", "usdt", "the", "and",
})


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fact_rows(raw_facts: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    from .orchestrator import NOT_NARRATED, display_value

    rows: List[Dict[str, Any]] = []
    aliases: Dict[str, List[str]] = {}
    for ticker, snap in (raw_facts or {}).items():
        if not isinstance(snap, Mapping):
            continue
        if snap.get("error") or snap.get("status") == "unavailable":
            continue
        asset = str(ticker)
        names: List[str] = [asset]
        base = asset.split("-", 1)[0]
        if base and base not in names:
            names.append(base)
        raw_name = str(snap.get("name") or "").strip()
        if raw_name:
            if raw_name not in names:
                names.append(raw_name)
            first_word = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", raw_name)
            if first_word and first_word[0].lower() not in _NAME_STOP and first_word[0] not in names:
                names.append(first_word[0])
        aliases[asset] = names
        for key, value in snap.items():
            if key in NOT_NARRATED or key not in FIELD_UNIT or not _numeric(value):
                continue
            shown_text, shown_val = display_value(key, value)
            rows.append({
                "asset": asset,
                "field": key,
                "value": float(shown_val),
                "shown": shown_text,
                "unit": FIELD_UNIT[key],
            })
    return rows, aliases


def _protected(start: int, end: int, protected_spans: Sequence[Tuple[int, int]]) -> bool:
    return any(start < b and end > a for a, b in protected_spans)


def _claimed_unit(text: str, start: int, end: int) -> str | None:
    token = text[start:end]
    before = text[max(0, start - 4):start]
    after = text[end:min(len(text), end + 16)]
    if "%" in token or _PERCENT_AFTER.search(after):
        return "percent"
    if _CURRENCY_BEFORE.search(before) or _CURRENCY_AFTER.search(after):
        return "currency"
    if _COUNT_AFTER.search(after):
        return "count"
    return None


def _sentence_bounds(text: str, start: int, end: int) -> Tuple[int, int]:
    lo = 0
    for m in _SENTENCE_SPLIT.finditer(text):
        if m.end() <= start:
            lo = m.end()
        elif m.start() >= end:
            return lo, m.start()
    return lo, len(text)


def _subclause_bounds(text: str, sent_lo: int, sent_hi: int, start: int, end: int) -> Tuple[int, int]:
    lo = sent_lo
    for m in _SUBCLAUSE_SPLIT.finditer(text, sent_lo, sent_hi):
        if m.end() <= start:
            lo = m.end()
        elif m.start() >= end:
            return lo, m.start()
    return lo, sent_hi


def _asset_for_span(
    text: str,
    sent_lo: int,
    sent_hi: int,
    sub_lo: int,
    sub_hi: int,
    start: int,
    aliases: Mapping[str, Sequence[str]],
) -> str | None:
    if len(aliases) <= 1:
        return next(iter(aliases.keys())) if aliases else None

    def scan(lo: int, hi: int) -> List[Tuple[int, int, str]]:
        segment = text[lo:hi]
        hits: List[Tuple[int, int, str]] = []
        for asset, names in aliases.items():
            for alias in sorted(names, key=len, reverse=True):
                pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", re.I)
                for m in pat.finditer(segment):
                    pos = lo + m.start()
                    hits.append((pos, lo + m.end(), asset))
        return hits

    for lo, hi in ((sub_lo, sub_hi), (sent_lo, sent_hi)):
        hits = scan(lo, hi)
        if not hits:
            continue
        distinct = {a for _, _, a in hits}
        if len(distinct) == 1:
            return next(iter(distinct))
        before = [h for h in hits if h[1] <= start]
        if before:
            best_end = max(h[1] for h in before)
            nearest = {h[2] for h in before if h[1] == best_end}
            if len(nearest) == 1:
                chosen = next(iter(nearest))
                other_before = [h for h in before if h[2] != chosen and best_end - h[1] <= 8]
                if not other_before:
                    return chosen
        return None
    return None


def _is_temporal_number(text: str, end: int) -> bool:
    return bool(_TEMPORAL_MODIFIER.search(text[end:min(len(text), end + 16)]))


_DIRECTION_PCT_LEFT = re.compile(
    r"\b(?:up|down|rose|fell|gained|lost|higher|lower|change|changed|moved?)\s*$",
    re.I,
)
_RIGHT_PREFIX_BREAK = re.compile(r"[\(\[:,;]|\b(?:from|with|and|or|for|versus|vs|to|at)\b", re.I)
_INTRODUCES_NEXT_NUM = re.compile(r"^\s*(?:of|at|is|was|near|around|:|=|\$|€|£)?\s*$", re.I)


def _local_cues(
    text: str,
    start: int,
    end: int,
    sub_lo: int,
    sub_hi: int,
    all_num_spans: Sequence[Tuple[int, int]],
    claimed_unit: str | None,
) -> Tuple[Tuple[str, ...], str | None]:
    left_bound = sub_lo
    right_bound = sub_hi
    has_next_num = False
    for ns, ne in all_num_spans:
        if ne <= start and not _is_temporal_number(text, ne):
            if ne > left_bound:
                left_bound = ne
        elif ns >= end and not _is_temporal_number(text, ne):
            if ns < right_bound:
                right_bound = ns
                has_next_num = True
                break

    left_text = text[left_bound:start]
    right_text = text[end:right_bound]

    def match_in(segment: str, prefer_rightmost: bool) -> List[Tuple[int, int, int, int, Tuple[str, ...], str]]:
        found: List[Tuple[int, int, int, int, Tuple[str, ...], str]] = []
        occupied: List[Tuple[int, int]] = []
        for pat, fields, label in METRIC_CUES:
            for m in pat.finditer(segment):
                ms, me = m.span()
                if any(ms >= os and me <= oe for os, oe in occupied):
                    continue
                occupied.append((ms, me))
                dist = (len(segment) - me) if prefer_rightmost else ms
                found.append((dist, -(me - ms), ms, me, fields, label))
        return found

    left_hits = match_in(left_text, prefer_rightmost=True)
    if claimed_unit == "percent" and _DIRECTION_PCT_LEFT.search(left_text):
        left_hits.append((
            0,
            -4,
            max(0, len(left_text) - 4),
            len(left_text),
            ("change_from_previous_session_pct", "drawdown_from_all_time_high_pct", "drawdown_from_52_week_high_pct"),
            "direction_percent",
        ))
    if left_hits:
        left_hits.sort(key=lambda x: (x[0], x[1]))
        best_dist = left_hits[0][0]
        if best_dist <= 40:
            chosen = [h for h in left_hits if h[0] <= best_dist + 2]
            fields = tuple(sorted({f for h in chosen for f in h[4]}))
            labels = "|".join(sorted({h[5] for h in chosen}))
            return fields, labels

    right_hits = []
    for h in match_in(right_text, prefer_rightmost=False):
        dist, neg_len, ms, me, fields, label = h
        before_cue = right_text[:ms]
        after_cue = right_text[me:]
        if _RIGHT_PREFIX_BREAK.search(before_cue):
            continue
        if has_next_num and _INTRODUCES_NEXT_NUM.match(after_cue):
            continue
        right_hits.append(h)

    if right_hits:
        right_hits.sort(key=lambda x: (x[0], x[1]))
        best_dist = right_hits[0][0]
        if best_dist <= 12:
            chosen = [h for h in right_hits if h[0] <= best_dist + 2]
            fields = tuple(sorted({f for h in chosen for f in h[4]}))
            labels = "|".join(sorted({h[5] for h in chosen}))
            return fields, labels

    return (), None


def check_claim_provenance(
    text: str,
    raw_facts: Mapping[str, Any],
    *,
    protected_spans: Sequence[Tuple[int, int]] = (),
    other_numbers: Iterable[float] = (),
) -> List[Dict[str, Any]]:
    """Return structured provenance conflicts where a matched number contradicts its claimed asset, metric, or unit."""
    if not text:
        return []
    rows, aliases = _fact_rows(raw_facts)
    if not rows:
        return []

    other_vals = [float(v) for v in (other_numbers or ())]
    spans = number_spans(text)
    raw_span_coords = [(s, e) for s, e, _ in spans]
    conflicts: List[Dict[str, Any]] = []

    for start, end, value in spans:
        if _protected(start, end, protected_spans) or _is_temporal_number(text, end):
            continue

        matched_rows = [r for r in rows if matches(value, r["value"])]
        if not matched_rows:
            continue

        sent_lo, sent_hi = _sentence_bounds(text, start, end)
        sub_lo, sub_hi = _subclause_bounds(text, sent_lo, sent_hi, start, end)
        claimed_unit = _claimed_unit(text, start, end)
        claimed_fields, cue_label = _local_cues(text, start, end, sub_lo, sub_hi, raw_span_coords, claimed_unit)
        claimed_asset = _asset_for_span(text, sent_lo, sent_hi, sub_lo, sub_hi, start, aliases)

        if not claimed_fields and not claimed_unit and (len(aliases) <= 1 or not claimed_asset):
            continue

        if any(matches(value, ov) for ov in other_vals) and not claimed_fields:
            continue

        def row_ok(r: Mapping[str, Any]) -> bool:
            if claimed_asset and r["asset"].upper() != claimed_asset.upper():
                return False
            if claimed_fields and r["field"] not in claimed_fields:
                return False
            if claimed_unit and r["unit"] != claimed_unit:
                return False
            return True

        if any(row_ok(r) for r in matched_rows):
            continue

        violations: List[str] = []
        if claimed_asset and not any(r["asset"].upper() == claimed_asset.upper() for r in matched_rows):
            violations.append("asset")
        if claimed_fields and not any(r["field"] in claimed_fields for r in matched_rows):
            violations.append("metric")
        if claimed_unit and not any(r["unit"] == claimed_unit for r in matched_rows):
            violations.append("unit")
        if not violations:
            violations.append("combination")

        if "asset" in violations:
            kind = "asset_misattribution"
        elif "metric" in violations and "unit" not in violations:
            kind = "metric_misbinding"
        elif "unit" in violations and "metric" not in violations:
            kind = "unit_mismatch"
        else:
            kind = "claim_provenance"

        conflicts.append({
            "kind": kind,
            "span": [start, end],
            "text": text[start:end],
            "claim": {
                "asset": claimed_asset,
                "fields": list(claimed_fields),
                "unit": claimed_unit,
                "cue": cue_label,
                "clause": text[sent_lo:sent_hi].strip()[:200],
            },
            "matches": [
                {
                    "asset": r["asset"],
                    "field": r["field"],
                    "shown": r["shown"],
                    "unit": r["unit"],
                }
                for r in matched_rows
            ],
            "violations": violations,
        })

    return sorted(conflicts, key=lambda c: (c["span"][0], c["kind"]))


def format_conflict_summary(conflicts: Sequence[Mapping[str, Any]]) -> List[str]:
    out: List[str] = []
    for c in conflicts[:6]:
        claim = c.get("claim") or {}
        parts = [p for p in (claim.get("asset"), claim.get("cue"), claim.get("unit")) if p]
        desc = " ".join(parts) if parts else c.get("kind", "claim_provenance")
        out.append(f"{c.get('text', '')} ({desc})"[:80])
    return out
