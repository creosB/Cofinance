"""Deterministic wording check for narrator output. Companion to the number check, not a fact checker.

Two rules, both relative to the exact evidence text the narrator was given:
  1. Names: every capitalised or mixed-case token (DSX, GeForce, Microsoft, Blackwell) must appear in the
     evidence. Sentence-initial common words (The, Headlines, Overall) are exempt.
  2. Claim words: finance events, data qualifiers and product categories (earnings, upgrade, high volume,
     oversold, chips) must appear in the evidence unless the sentence is negated ("no earnings data").
It cannot judge paraphrase ("doubles efficiency") or tone. A rejection sends the answer to the
deterministic fallback, exactly like a number-check failure. Not part of the narrator qualification scorer.
"""
from __future__ import annotations

import re
from typing import List

# Common words that are capitalised only because they start a sentence or clause.
COMMON = set("""
a an the this that these those there they their it its in on at for from as and but or with while despite
however given both all key latest recent last previous overall main note summary thesis reports report
headlines headline news data fundamentals shares share price prices risk risks session volume market markets
sentiment direction company companies investors no not yes bullish bearish neutral unclear strong positive
negative also still today currently meanwhile additionally finally first second third one two three
listed equity crypto asset assets stock stocks trading trades traded what why how if then so such other
coverage source sources issuer official announcements announcement these taken together further each
after before since during when until short long near close here we you our
""".split())
# Units and abbreviations that carry no claim of their own.
ALWAYS_OK = {"usd", "utc", "eps", "etf", "pe", "ttm"}

# Claim words. A phrase is matched on word boundaries; singular and plural forms are equivalent.
CLAIM_TERMS = [
    # corporate and market events
    "earnings", "revenue", "sales", "profit", "profits", "guidance", "forecast", "outlook", "beat", "beats", "missed",
    "upgrade", "upgraded", "downgrade", "downgraded", "analyst", "price target", "lawsuit", "sued", "investigation",
    "probe", "antitrust", "regulator", "regulators", "recall", "acquisition", "acquire", "acquires", "merger",
    "stake", "buyback", "repurchase", "dividend", "stock split", "layoff", "layoffs", "ipo", "bankruptcy",
    "tariff", "tariffs", "sanction", "sanctions", "export ban", "export controls", "short interest", "short seller",
    "insider", "delisting",  # partner names are covered by the name rule; "partnership" for "collaboration" is fair paraphrase
    # qualifiers that need comparison data the tools do not supply
    "high volume", "heavy volume", "low volume", "unusually", "elevated", "heavy", "record", "all-time", "all time",
    "oversold", "overbought", "undervalued", "overvalued", "cheap", "expensive", "surge", "surged", "soared",
    "plunge", "plunged", "plummeted", "crash", "crashed", "rally", "rallied", "rebound", "breakout", "selloff",
    "sell-off", "momentum", "outperform", "underperform",
    # product categories
    "chip", "chips", "gpu", "gpus", "cpu", "cpus", "processor", "processors", "semiconductor", "smartphone",
    "data center", "datacenter",
]
NEGATION = re.compile(r"\b(no|not|without|lack|lacks|unavailable|none|cannot|n't)\b", re.I)
SENTENCE = re.compile(r"(?<=[.!?;:])\s+|\s+[\u2014\u2013-]\s+|\u2014")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:['\u2019][A-Za-z]+)?")


def normalise(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2011", "-").lower()
    text = re.sub(r"'s\b", "", text)
    return " " + " ".join(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text)) + " "


def _variants(term: str) -> List[str]:
    term = " ".join(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", term.lower()))
    out = {term}
    if term.endswith("ies"):
        out.add(term[:-3] + "y")
    elif term.endswith("y"):
        out.add(term[:-1] + "ies")
    if term.endswith("es"):
        out.add(term[:-2])
    if term.endswith("s"):
        out.add(term[:-1])
    else:
        out.update({term + "s", term + "es"})
    return [v for v in out if v]


# Standard finance shorthand for wording the evidence already contains (found 2026-11-08: every live "latest Apple news"
# answer fell back because the narrator wrote "Q4 results" and "earnings" for the headline "Apple reports fourth quarter
# results"). Used ONLY to look a term up in the evidence, never to widen what counts as a claim. A results headline
# supports "earnings"; it never supports revenue, profit, guidance or a beat, which stay claim words of their own.
_QUARTERS = {"q1": "first", "q2": "second", "q3": "third", "q4": "fourth"}
_RESULTS = ["quarter results", "quarterly results", "financial results", "fiscal results", "annual results",
            "full-year results", "full year results", "earnings report"] + [q + " results" for q in _QUARTERS]
EQUIVALENT = {
    **{q: [n + " quarter", n + "-quarter", "fiscal " + q] for q, n in _QUARTERS.items()},
    "fy": ["fiscal year", "fiscal"],
    "ath": ["all-time high", "all time high"],  # live 2026-11-08: "drawdown from ATH" for the all-time high fact
    "earnings": _RESULTS,
}


def _present(term: str, evidence: str) -> bool:
    return any(f" {v} " in evidence for v in _variants(term))


def _supported(term: str, evidence: str) -> bool:
    """The evidence contains the term, a plural/singular form, or standard shorthand for wording it contains."""
    return _present(term, evidence) or any(f" {p} " in evidence for p in EQUIVALENT.get(term, ()))


# Direction check (live 2026-11-08: "last price 186.64 USD (down from 181.6 USD)" passed the number check, the move was
# up). A clause that names the previous close, or says "previous session", must not state the opposite move.
_DOWN = re.compile(r"\b(down|fell|fall|falls|falling|dropped|drop|drops|declined|decline|declines|lower|slipped|slid|"
                   r"decreased|lost|loses|retreated)\b", re.I)
_UP = re.compile(r"\b(up|rose|rise|rises|rising|gained|gain|gains|higher|climbed|climbs|increased|advanced|rallied)\b", re.I)
_CLAUSE = re.compile(r"\.(?=\s|$)|[;!?()\[\]]|:\s|,\s|\s[\u2014\u2013-]\s|\u2014")  # a decimal point never splits


def direction_conflicts(candidate: str, anchors: List[float], direction: str) -> List[str]:
    """`anchors`: the previous-close values shown to the narrator; `direction`: up, down or unchanged, computed by Python.
    Returns the wrong direction words found in clauses about that comparison. Empty list means no conflict."""
    from .numeric_guard import matches, numbers_in
    wrong = {"up": _DOWN, "down": _UP}.get(direction)
    if not candidate or wrong is None:
        return []
    found: List[str] = []
    for clause in _CLAUSE.split(candidate.replace("\u2011", "-")):
        about = re.search(r"\bprevious (session|close)\b", clause, re.I) or any(
            matches(n, a) for n in numbers_in(clause) for a in anchors)
        if about:
            found += [m.group(0).lower() for m in wrong.finditer(clause)]
    return list(dict.fromkeys(found))


def unsupported_terms(candidate: str, evidence: str) -> List[str]:
    """Terms in `candidate` that the evidence does not contain. Empty list means the wording check passed."""
    if not candidate:
        return []
    ev = normalise(evidence)
    text = candidate.replace("\u2019", "'").replace("**", "")
    found: List[str] = []
    for sentence in SENTENCE.split(text):
        words = WORD.findall(sentence)
        for index, word in enumerate(words):
            base = re.sub(r"'s$", "", word)
            low = base.lower()
            named = base[0].isupper() or any(c.isupper() for c in base[1:])
            if not named or len(base) < 2 or low in ALWAYS_OK:
                continue
            if low in COMMON and (index == 0 or base[1:].islower()):
                continue
            if not _supported(low, ev):
                found.append(base)
        if NEGATION.search(sentence):
            continue
        body = normalise(sentence)
        for term in CLAIM_TERMS:
            if _present(term, body) and not _supported(term, ev):
                found.append(term)
    return list(dict.fromkeys(found))
