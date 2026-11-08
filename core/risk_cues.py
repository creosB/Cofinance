"""Risks the evidence supports, derived by Python, and a check that the Lead names no other.

Found 2026-11-08 in live answers: the Lead kept inventing risks ("execution uncertainty amid rapid rollout and
partnership dependencies", "supply-chain or earnings volatility"). Numbers and names passed every guard; the claim was
still made up. So the risks are now a closed list built from tool facts, and a risk clause must match one of them.

Each cue has digit-free text (it is shown to the narrator and joins the wording evidence) and anchor phrases. A clause
that talks about risk (risk, concern, headwind, uncertainty, downside, ...) must contain an anchor of a listed cue, or be
a negation ("the reports name no specific risk"). Deterministic and conservative: a supported risk phrased with none of
its anchors is rejected too, and the Lead then gets one stricter retry.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

RISK_WORDS = re.compile(r"\b(risks?|concerns?|headwinds?|threats?|uncertain\w*|downside|vulnerab\w*|caution\w*|"
                        r"challenges?|pressures?|weakness\w*|exposure)\b", re.I)
NEGATED = re.compile(r"\b(no|not|none|without|cannot|n't|do not|does not)\b", re.I)
# Sentences and contrast clauses. Not colons: "Main risk: X" must stay one clause.
CLAUSE = re.compile(r"\.(?=\s|$)|[;!?]|\s[\u2014\u2013-]\s|\u2014|,\s(?=(?:but|while|although|though|whereas|yet)\b)", re.I)
# Content words of a headline that can anchor a risk about it ("the iPhone production stop").
STOP = set("""apple nvidia company companies announces announced introduces introducing launches reports report latest
new with from into over after about their this that will says said more most than gives update biggest ever""".split())


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def risk_cues(data_facts: Mapping[str, Any], news_facts: Mapping[str, Any]) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    for ticker, snap in (data_facts or {}).items():
        if not isinstance(snap, Mapping) or snap.get("status") == "unavailable":
            continue
        dd = snap.get("drawdown_from_all_time_high_pct")
        if _num(dd) and dd >= 10:
            cues.append({"text": f"{ticker} trades well below its all-time high",
                         "anchors": ["all-time high", "all time high", "drawdown", "below its high", "off its high", "below its peak"]})
        dd1 = snap.get("drawdown_from_52_week_high_pct")
        if _num(dd1) and dd1 >= 10 and not (_num(dd) and dd >= 10):
            cues.append({"text": f"{ticker} trades well below its one-year high",
                         "anchors": ["one-year high", "drawdown", "below its high", "off its high"]})
        change = snap.get("change_from_previous_session_pct")
        if _num(change) and change < 0:
            cues.append({"text": f"{ticker} is down since the previous session close",
                         "anchors": ["previous session", "previous close", "down since", "decline", "fell", "lower"]})
        if snap.get("fundamentals_status") and not snap.get("fundamentals_as_of"):
            cues.append({"text": f"fundamentals for {ticker} are not available from the source",
                         "anchors": ["fundamental", "valuation data", "earnings data", "financial data"]})
        fg = snap.get("fear_greed_index")
        if _num(fg) and fg > 75:
            cues.append({"text": "crypto market sentiment may be overheated (Fear and Greed index)",
                         "anchors": ["sentiment", "overheated", "greed", "euphoria"]})
        elif _num(fg) and fg < 25:
            cues.append({"text": "crypto market sentiment shows fear (Fear and Greed index)",
                         "anchors": ["sentiment", "fear"]})
        vol = snap.get("annualized_daily_log_volatility_pct")
        if _num(vol) and vol >= 60:
            cues.append({"text": f"{ticker} has large daily price swings (annualized volatility)",
                         "anchors": ["volatility", "volatile", "swings"]})
    issuer_only = True
    any_news = False
    for ticker, items in (news_facts or {}).items():
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            any_news = True
            if "newsroom" not in str(item.get("source", "")):
                issuer_only = False
            if item.get("direction") == "bearish":
                title = str(item.get("title", ""))
                words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", title) if w.lower() not in STOP]
                cues.append({"text": f"a bearish headline: {title}", "anchors": list(dict.fromkeys(words))[:6] + ["bearish"]})
    if any_news and issuer_only:
        cues.append({"text": "the news comes only from company announcements, which are issuer claims",
                     "anchors": ["issuer", "company announcements", "self-reported", "press release", "official announcements"]})
    return cues


_ITEM = re.compile(r"\s(?:and|or|plus|as well as)\s|,\s", re.I)
_FILLER = set("""the a an is are was were be include includes including remains remain lies lie in of that this these
main key primary biggest highlighted supported support supports by reports report data news sources source they it
flagging flagged noted named identified potential possible
matches match matching matched listed list ones one which also""".split())  # live 2026-11-08: "... matches the listed" is about the list, not a risk


def _words(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())) + " "


def unsupported_risks(candidate: str, cues: List[Mapping[str, Any]]) -> List[str]:
    """Risk items in `candidate` that match no listed cue. Empty list means every stated risk is supported.

    A risk phrase is the text after the first risk word ("Main risk: A and B"), or before it when the risk word comes
    last ("A remains the main risk"). Every item joined by and/or/commas must contain an anchor of a listed cue, so a
    supported half cannot carry an invented half ("lack of fundamental data and potential execution gaps")."""
    anchors = [_words(a).strip() for cue in cues for a in cue.get("anchors", [])]
    out: List[str] = []
    for clause in CLAUSE.split((candidate or "").replace("\u2011", "-").replace("\u2019", "'")):
        hit = RISK_WORDS.search(clause)
        if not hit or NEGATED.search(clause):
            continue
        tail, head = clause[hit.end():], clause[:hit.start()]
        content = lambda t: [w for w in re.findall(r"[a-z]+", t.lower()) if w not in _FILLER]
        phrase = tail if len(content(tail)) >= 2 else head
        cut = re.split(r"\bdespite\b", phrase, flags=re.I)[0]  # "X despite the bullish narrative on A, B and C": only X is the risk
        phrase = cut if content(cut) else phrase
        for item in _ITEM.split(phrase):
            if not content(item):
                continue
            if not any(" " + a in _words(item) for a in anchors):
                out.append(_tidy(item))
    return list(dict.fromkeys(out))


def _tidy(item: str) -> str:
    """Display text fed back to the retry: leading filler dropped ("is supply-chain disruption" -> "supply-chain disruption").
    Matching above uses the raw item; this only changes what the model is told to remove."""
    words = item.strip(" :,").split()
    while len(words) > 1 and words[0].lower().strip(":,") in _FILLER:
        words.pop(0)
    return " ".join(words).strip(" :,")[:160]


def cue_block(cues: List[Mapping[str, Any]]) -> str:
    """Prompt text. Digit-free, so it adds nothing to any number allowlist."""
    if not cues:
        return "Risks the reports support: none identified. If you mention risk, say the reports do not identify a specific one."
    return "Risks the reports support (name only these, or none):\n" + "\n".join("- " + c["text"] for c in cues)
