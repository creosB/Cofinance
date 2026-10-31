"""Strict, deterministic scorers for small-model qualification (no LLM judge)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Optional

_THINK = re.compile(r"<think>.*?</think>", re.S)


def final_answer(text: str) -> Optional[str]:
    """Strip reasoning. Returns None when thinking never closed (incomplete output)."""
    if text is None:
        return None
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1].strip()
    if "<think>" in text:
        return None
    return text.strip()


def _json(answer: str) -> Any:
    a = answer.strip()
    if a.startswith("```"):                       # tolerate a fenced block, nothing else
        a = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", a).strip()
    return json.loads(a)


def score_exact(answer: str, expected: str) -> bool:
    return answer.strip().lower() == str(expected).strip().lower()


def score_set(answer: str, expected: Iterable[str]) -> bool:
    try:
        got = _json(answer)
    except Exception:
        return False
    return isinstance(got, list) and all(isinstance(x, str) for x in got) \
        and len(got) == len(set(got)) and {x.upper() for x in got} == {e.upper() for e in expected}


def score_json(answer: str, expected: Dict[str, Any]) -> bool:
    """Bare JSON object, exactly the expected keys and values (nested compare)."""
    try:
        got = _json(answer)
    except Exception:
        return False
    return isinstance(got, dict) and got == expected


_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*\.?\d*%?")


def numbers_in(text: str) -> list:
    out = []
    for m in _NUM.findall(text):
        raw = m.rstrip("%").replace(",", "").rstrip(".")
        try:
            out.append(float(raw))
        except ValueError:
            pass
    return out


def score_numbers_faithful(answer: str, allowed: Iterable[float], tol: float = 1e-9) -> bool:
    """Every number in the answer must come from the supplied data; at least one must appear."""
    allowed = [float(a) for a in allowed]
    nums = numbers_in(answer)
    return bool(nums) and all(any(abs(n - a) <= max(tol, abs(a) * tol) for a in allowed) for n in nums)


_SENT = re.compile(r"(?<=[.!?])\s+")


def sentence_count(text: str) -> int:
    """Splits on . ! ? followed by whitespace. Decimals (41.20) are safe; abbreviations like "U.S. " overcount."""
    t = text.strip()
    return 0 if not t else len([s for s in _SENT.split(t) if s.strip()])


def score_narration(answer: str, allowed: Iterable[float], max_sentences: int) -> bool:
    """Narrator job in v2: cite only supplied numbers (at least one) and stay within the sentence budget."""
    return score_numbers_faithful(answer, allowed) and 1 <= sentence_count(answer) <= max_sentences


def score(case: Dict[str, Any], raw_output: Optional[str]) -> Dict[str, Any]:
    ans = final_answer(raw_output) if raw_output is not None else None
    if ans is None:
        return {"passed": False, "reason": "incomplete_or_no_output"}
    if not ans:
        return {"passed": False, "reason": "empty_answer"}
    kind, exp = case["check"], case.get("expected")
    if kind == "exact":
        ok = score_exact(ans, exp)
    elif kind == "set":
        ok = score_set(ans, exp)
    elif kind == "json":
        ok = score_json(ans, exp)
    elif kind == "numbers":
        ok = score_numbers_faithful(ans, case["allowed_numbers"])
    elif kind == "narrate":
        ok = score_narration(ans, case["allowed_numbers"], int(case.get("max_sentences", 2)))
    else:
        raise ValueError(f"unknown check {kind!r}")
    return {"passed": bool(ok), "reason": "ok" if ok else f"{kind}_mismatch"}
