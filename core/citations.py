"""Which tool value backs each number in an answer. The guards already require every number to match a value the
narrator was shown; this keeps that match per value instead of pooled, so the UI can point at the source.
Python decides every citation. The UI only draws them, and never infers one. A number with no match gets none.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from .numeric_guard import matches, number_spans, numbers_in
from .orchestrator import FACT_LABELS, NOT_NARRATED, display_value

LABELS = dict(FACT_LABELS)
MAX_CITATIONS = 40
MAX_REFS = 3


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def shown_facts(snapshots: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Every number the narrator could see, in the meaning it was shown (scale included), with its source."""
    out = []
    for ticker, snap in (snapshots or {}).items():
        if snap.get("error") or snap.get("status") == "unavailable":
            continue
        for key, value in snap.items():
            if key in NOT_NARRATED or not _numeric(value):
                continue
            text, meaning = display_value(key, value)
            crypto_index = key == "fear_greed_index"
            out.append({"kind": "fact", "ticker": str(ticker), "field": key, "label": LABELS.get(key, key), "shown": text,
                        "value": meaning, "source": "Alternative.me" if crypto_index else str(snap.get("source") or ""),
                        "as_of": str(snap.get("fear_greed_as_of" if crypto_index else "price_as_of") or snap.get("fetched_at") or "")})
    return out


def cite(answer: str, snapshots: Mapping[str, Mapping[str, Any]], evidence: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """[{start, end, text, refs}] for each number in the answer that a tool value backs. Facts first; a number no
    fact explains may cite an evidence row whose title contains it (index into the payload's evidence list)."""
    facts, rows = shown_facts(snapshots), list(evidence or [])
    out = []
    for start, end, value in number_spans(answer)[:MAX_CITATIONS]:
        refs = [{k: v for k, v in f.items() if k != "value"} for f in facts if matches(value, f["value"])]
        if not refs:
            refs = [{"kind": "evidence", "index": i, "title": str(row.get("title") or "")[:160], "source": str(row.get("source") or ""),
                     "as_of": str(row.get("as_of") or "")}
                    for i, row in enumerate(rows) if any(matches(value, n) for n in numbers_in(str(row.get("title") or "")))]
        if refs:
            out.append({"start": start, "end": end, "text": answer[start:end], "refs": refs[:MAX_REFS]})
    return out
