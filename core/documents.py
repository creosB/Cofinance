"""PDFs attached to a conversation: text extraction and excerpt selection, all in Python.

The narrator never reads a whole file. For each question Python picks the few pages that share the most words with
it and passes short verbatim excerpts with their page numbers. Numbers in an answer must appear in those excerpts
(or be their page numbers), under the same guards as every other report. Scanned PDFs (no text layer) and
password-protected files are refused with a plain message; there is no OCR.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

WORKER = Path(__file__).with_name('pdf_worker.py')
MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 300
MAX_DOCUMENTS = 5            # per conversation
MAX_PAGE_CHARS = 20000
PARSE_TIMEOUT_S = 30
TOP_PAGES = 3
WINDOW_CHARS = 900           # per excerpt; three excerpts stay well inside the 4,096-token context
MIN_TEXT_CHARS = 20
STOP = set('''the and for with that this from are was were has have had not but its into over under than then
they them their there what which who whom when where why how does did can could would should will shall may might
about after before also any all each per our out off own same such very just only more most other some been being
you your yours his her hers him she he it is be by of on in to at as or an a do so if no nor too up down'''.split())


class DocumentError(ValueError):
    """A refusal the user should see as written."""


def clean_name(name: Any) -> str:
    if not isinstance(name, str):
        raise DocumentError('A file name is required.')
    name = Path(name.replace('\\', '/')).name
    name = ''.join(c for c in name if c.isprintable()).strip()
    if not name or len(name) > 120 or not name.lower().endswith('.pdf'):
        raise DocumentError('Attach a .pdf file with a name of up to 120 characters.')
    return name


def normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()[:MAX_PAGE_CHARS]


class ExtractedPages(list):
    """Normalized search pages plus bounded, complete parser text. No PDF bytes or inferred layout."""

    def __init__(self, raw_pages):
        if not isinstance(raw_pages, (tuple, list)) or len(raw_pages) > MAX_PAGES or any(not isinstance(p, str) for p in raw_pages):
            raise DocumentError('Invalid page data from PDF extraction.')
        super().__init__(normalize(p) for p in raw_pages)
        self.source_pages = tuple(p if len(p) <= MAX_PAGE_CHARS else None for p in raw_pages)


def _source_page(document, index):
    pages, source = document.get('pages'), document.get('source_pages')
    if not isinstance(pages, (list, tuple)) or not isinstance(source, (list, tuple)) or len(source) != len(pages):
        return None
    raw = source[index]
    return raw if isinstance(raw, str) and len(raw) <= MAX_PAGE_CHARS and normalize(raw) == pages[index] else None


def evidence_pages(document):
    """Complete source view only. Never synthesize a missing witness from legacy search text."""
    pages = document.get('pages') or []
    src = [_source_page(document, i) for i in range(len(pages))]
    if not src or any(p is None for p in src):
        raise DocumentError('Complete extracted page text is unavailable; normalized text is for search only.')
    return src


def extract_pages(data: bytes, *, timeout: float = PARSE_TIMEOUT_S, command: Sequence[str] | None = None) -> ExtractedPages:
    """Page texts, first page first. Raises DocumentError with a message fit for the user."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise DocumentError('The file is empty.')
    if len(data) > MAX_BYTES:
        raise DocumentError('PDFs are limited to 20 MB.')
    if not bytes(data[:1024]).lstrip().startswith(b'%PDF-'):
        raise DocumentError('This file is not a PDF.')
    cmd = list(command) if command else [sys.executable, '-B', str(WORKER), str(MAX_PAGES)]
    try:
        done = subprocess.run(cmd, input=bytes(data), capture_output=True, timeout=timeout,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise DocumentError('Reading this PDF took too long, so it was stopped. Try a smaller or simpler file.') from None
    try:
        out = json.loads(done.stdout.decode('utf-8', 'replace').strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise DocumentError('This file could not be read as a PDF.') from None
    if out.get('error'):
        raise DocumentError(str(out['error'])[:200])
    pages = ExtractedPages(out.get('pages') or [])
    if sum(len(p) for p in pages) < MIN_TEXT_CHARS:
        raise DocumentError('This PDF has no text layer (it may be scanned). Scanned PDFs are not supported.')
    return pages


def terms(text: str) -> List[str]:
    return [w for w in re.findall(r'[a-z]{3,}|\d+(?:[.,]\d+)*', (text or '').lower()) if w not in STOP]


def _bounds(text: str, wanted: set):
    """Half-open indices in search text; find occurrences on the original string, not a length-changing case fold."""
    if len(text) <= WINDOW_CHARS:
        return 0, len(text)
    starts = sorted({m.start() for t in wanted for m in re.finditer(r'\b' + re.escape(t), text, re.I)}) or [0]
    a = max((max(0, s-120) for s in starts), key=lambda i: sum(t in text[i:i+WINDOW_CHARS].lower() for t in wanted))
    b = min(len(text), a+WINDOW_CHARS)
    if a and text[a-1] != ' ' and text[a] != ' ':
        gap = text.find(' ', a, b)
        if gap >= 0 and gap+1 < b:
            a = gap+1
    if b < len(text) and text[b] != ' ' and text[b-1] != ' ':
        gap = text.rfind(' ', a, b)
        if gap > a:
            b = gap
    return a, b


def _window(text: str, wanted: set) -> str:
    a, b = _bounds(text, wanted)
    return ('...' if a else '') + text[a:b] + ('...' if b < len(text) else '')


_UNIT_BANNER_RE = re.compile(
    r"(?:\((?:[^()]{0,40}\b(?:in|amounts?\s+in)\s+(?:thousands?|millions?|billions?)\b[^()]{0,40})\)"
    r"|(?:(?:amounts?\s+)?in\s+(?:USD|EUR|GBP|JPY|TRY|\$)?\s*(?:thousands?|millions?|billions?)\b))",
    re.I,
)
_SCALE_WORD_VALUE = {"thousand": 1e3, "thousands": 1e3, "million": 1e6, "millions": 1e6, "billion": 1e9, "billions": 1e9}
_EXPLICIT_SCALE_RE = re.compile(
    r"(?:\s+(?P<word>thousands?|millions?|billions?|trillions?)\b|(?P<short>k|K|M|mn|B|bn|T|tn)\b)$",
    re.I,
)
_NON_SCALABLE_PREFIX_RE = re.compile(r"\b(?:page|p\.|note|table|section|part|item|quarter|q)\s*$", re.I)
_CONTINUATION_CUE_RE = re.compile(r"\b(?:continued|table\s+\d|see\s+(?:next|following)\s+page|footnote|note\s+\d)\b", re.I)


def banner_scale(unit_banner: str | None) -> float | None:
    if not unit_banner:
        return None
    low = unit_banner.lower()
    for word, mult in _SCALE_WORD_VALUE.items():
        if re.search(rf"\b{word}\b", low):
            return mult
    return None


def _span_scale(span_text: str) -> float | None:
    from .numeric_guard import SCALES

    m = _EXPLICIT_SCALE_RE.search(span_text or "")
    if not m:
        return None
    if m.group("word"):
        w = m.group("word").lower().rstrip("s")
        return SCALES.get(w)
    short = m.group("short")
    return SCALES.get(short)


def scalable_excerpt_numbers(text: str) -> List[float]:
    """Return unscaled table/financial numbers in `text`, excluding years, percents, and note/page markers."""
    from .numeric_guard import number_spans

    out: List[float] = []
    src = text or ""
    for start, end, val in number_spans(src):
        span_text = src[start:end]
        if _span_scale(span_text) is not None or abs(val) >= 1e6:
            continue
        if span_text.endswith("%") or re.match(r"\s*(?:%|percent\b)", src[end:end + 12], re.I):
            continue
        if float(val).is_integer() and 1900 <= int(val) <= 2099 and re.fullmatch(r"\d{4}", span_text):
            continue
        if _NON_SCALABLE_PREFIX_RE.search(src[max(0, start - 16):start]):
            continue
        out.append(val)
    return out


def extract_page_structure(raw: str, span_start: int) -> Dict[str, str]:
    """Recover the nearest section/table header and unit banner above `span_start` on one page."""
    cut = raw.rfind("\n", 0, max(0, span_start))
    prefix = raw[:cut] if cut > 0 else ""
    lines = [ln.strip() for ln in prefix.splitlines() if ln.strip()]
    out: Dict[str, str] = {}
    for ln in reversed(lines):
        if "unit_banner" not in out:
            m = _UNIT_BANNER_RE.search(ln)
            if m:
                out["unit_banner"] = re.sub(r"\s+", " ", m.group(0)).strip()
        if "section_header" not in out and 4 <= len(ln) <= 90 and not ln.endswith((".", "?", "!")) and not re.fullmatch(r"[\d\s,.$€£¥%()-]+", ln):
            words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", ln)
            if 1 <= len(words) <= 12 and (
                ln.isupper()
                or ln.endswith(":")
                or re.match(r"^(?:table|section|part|note|item|appendix)\s+\d", ln, re.I)
                or sum(1 for w in words if w[0].isupper()) >= max(1, (len(words) + 1) // 2)
            ):
                if ln != out.get("unit_banner"):
                    out["section_header"] = re.sub(r"\s+", " ", ln).strip()
        if "unit_banner" in out and "section_header" in out:
            break
    return out


def _excerpt(document, i, text, wanted, matched, *, adjacent: bool = False):
    from hashlib import sha256
    out = {'document_id': document.get('id'), 'name': document.get('name'), 'page': i+1, 'matched': matched}
    if adjacent:
        out['adjacent'] = True
    raw = _source_page(document, i)
    if raw is None:
        return dict(out, text=_window(text, wanted))
    lo, hi = _bounds(text, wanted)
    offsets = []
    last = 0
    for m in re.finditer(r'\S+', raw):
        if offsets:
            offsets.append((last, m.start()))
        offsets.extend((k, k+1) for k in range(m.start(), m.end()))
        last = m.end()
    a, b = offsets[lo][0], offsets[hi-1][1]
    if b-a > WINDOW_CHARS:
        hits = [m.start() for t in wanted for m in re.finditer(r'\b'+re.escape(t), text[lo:hi], re.I)]
        center = offsets[lo+min(hits)][0] if hits else a
        a = min(max(a, center-120), b-WINDOW_CHARS); b = a+WINDOW_CHARS
    snippet = raw[a:b]
    struct = extract_page_structure(raw, a)
    if not struct.get('unit_banner'):
        m = _UNIT_BANNER_RE.search(snippet)
        if m:
            struct['unit_banner'] = re.sub(r'\s+', ' ', m.group(0)).strip()
    if struct.get('section_header'):
        out['section_header'] = struct['section_header']
    if struct.get('unit_banner'):
        out['unit_banner'] = struct['unit_banner']
    return dict(out, text=snippet, source_span={'start':a,'end':b,'page_sha256':sha256(raw.encode('utf-8')).hexdigest()})


def select_excerpts(documents: Sequence[Mapping[str, Any]], question: str, top: int = TOP_PAGES, *, include_adjacent: bool = True) -> List[Dict[str, Any]]:
    """Up to `top` excerpts: the pages sharing the most (idf-weighted) words with the question. With no shared word,
    the first pages of the first document, marked matched=False, so a 'summarize this' question still gets text."""
    pages = [(d, i, p) for d in documents for i, p in enumerate(d.get('pages') or []) if p]
    if not pages:
        return []
    wanted = set(terms(question))
    split = [(d, i, p, terms(p)) for d, i, p in pages]
    df = {t: sum(t in set(ws) for *_, ws in split) for t in wanted}
    n = len(split)
    scored = []
    for order, (d, i, p, ws) in enumerate(split):
        s = sum((1 + math.log(ws.count(t))) * (math.log((n + 1) / (df[t] + 1)) + 1) for t in wanted if t in ws)
        if s > 0:
            scored.append((-s, order, d, i, p))
    chosen = [(d, i, p, True, False) for _, _, d, i, p in sorted(scored, key=lambda x: (x[0], x[1]))[:top]]
    if include_adjacent and chosen and len(chosen) < top:
        seen_keys = {(d.get('id'), d.get('name'), i) for d, i, _, _, _ in chosen}
        by_doc_page = {(d.get('id'), d.get('name'), i): (d, i, p, set(ws)) for d, i, p, ws in split}
        anchor_doc, anchor_i, anchor_p, _, _ = chosen[0]
        anchor_raw = _source_page(anchor_doc, anchor_i) or anchor_p
        if _CONTINUATION_CUE_RE.search(anchor_raw):
            anchor_words = set(terms(anchor_p))
            for delta in (1, -1):
                if len(chosen) >= top:
                    break
                key = (anchor_doc.get('id'), anchor_doc.get('name'), anchor_i + delta)
                if key in seen_keys or key not in by_doc_page:
                    continue
                nd, ni, np, nws = by_doc_page[key]
                if len(anchor_words & nws) >= 2:
                    chosen.append((nd, ni, np, True, True))
                    seen_keys.add(key)
    if not chosen:
        first = [x for x in pages if x[0] is pages[0][0]][:min(2, max(1, top))]
        chosen = [(d, i, p, False, False) for d, i, p in first]
    return [_excerpt(d, i, p, wanted, matched, adjacent=adj) for d, i, p, matched, adj in chosen]


def check_document_claims(candidate: str, doc_facts: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Verify atomic unit-scale and cross-document attribution invariants for answers citing attached PDFs."""
    from .numeric_guard import matches, number_spans, numbers_in

    excerpts = list(doc_facts.get('excerpts') or [])
    if not candidate or not excerpts:
        return []
    conflicts: List[Dict[str, Any]] = []

    banner_nums: Dict[float, set[float]] = {}
    all_raw_nums: List[float] = []
    for ex in excerpts:
        body = str(ex.get('text') or '')
        all_raw_nums.extend(numbers_in(body))
        mult = banner_scale(ex.get('unit_banner'))
        if mult is not None:
            for v in scalable_excerpt_numbers(body):
                banner_nums.setdefault(v, set()).add(mult)

    if banner_nums:
        for start, end, val in number_spans(candidate):
            span_text = candidate[start:end]
            cand_mult = _span_scale(span_text)
            if cand_mult is None:
                continue
            if any(matches(val, raw_v) for raw_v in all_raw_nums):
                continue
            unscaled = val / cand_mult
            for base_v, allowed_mults in banner_nums.items():
                if matches(unscaled, base_v) and cand_mult not in allowed_mults:
                    conflicts.append({
                        "kind": "document_unit_mismatch",
                        "text": span_text,
                        "token": span_text,
                        "expected_scale": sorted(allowed_mults),
                    })
                    break

    groups = list(doc_facts.get('groups') or [])
    if len(groups) >= 2:
        doc_allowed: Dict[str, List[float]] = {}
        doc_patterns: List[Tuple[str, re.Pattern[str]]] = []
        for g in groups:
            name = str(g.get('name') or '')
            if not name:
                continue
            stem = Path(name).stem
            pats = [re.escape(name)]
            if len(stem) >= 4:
                pats.append(re.escape(stem))
            doc_patterns.append((name, re.compile(r'(?<!\w)(?:' + '|'.join(pats) + r')(?!\w)', re.I)))
            vals: List[float] = []
            for ex in excerpts:
                if ex.get('name') == name:
                    body = str(ex.get('text') or '')
                    vals.extend(numbers_in(body))
                    vals.append(float(ex.get('page') or 0))
                    vals.extend(numbers_in(name))
                    mult = banner_scale(ex.get('unit_banner'))
                    if mult is not None:
                        vals.extend(v * mult for v in scalable_excerpt_numbers(body))
            doc_allowed[name] = vals

        for sentence in re.split(r'(?<=[.!?])\s+', candidate):
            named = [name for name, pat in doc_patterns if pat.search(sentence)]
            if len(named) != 1:
                continue
            owner = named[0]
            owner_nums = doc_allowed.get(owner, [])
            other_nums = [v for other_name, vs in doc_allowed.items() if other_name != owner for v in vs]
            for start, end, val in number_spans(sentence):
                span_text = sentence[start:end]
                if any(matches(val, ov) for ov in owner_nums):
                    continue
                if any(matches(val, xv) for xv in other_nums):
                    conflicts.append({
                        "kind": "cross_document_mismatch",
                        "text": span_text,
                        "token": span_text,
                        "document": owner,
                    })
    return conflicts


def group_excerpts(excerpts: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Group excerpts by source document in first-appearance order, sorting pages ascending within each file."""
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for ex in excerpts:
        key = (ex.get('document_id'), ex.get('name'))
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {'document_id': ex.get('document_id'), 'name': ex.get('name'), 'excerpts': []}
            grouped[key] = bucket
        bucket['excerpts'].append(dict(ex))
    out: List[Dict[str, Any]] = []
    for bucket in grouped.values():
        bucket['excerpts'].sort(key=lambda e: int(e.get('page', 0)))
        out.append(bucket)
    return out
