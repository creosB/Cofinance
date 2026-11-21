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


def _excerpt(document, i, text, wanted, matched):
    from hashlib import sha256
    out = {'document_id': document.get('id'), 'name': document.get('name'), 'page': i+1, 'matched': matched}
    raw = _source_page(document, i)
    if raw is None:
        return dict(out, text=_window(text, wanted))
    lo, hi = _bounds(text, wanted)
    # Positions are Python character offsets in one extracted page. One normalized space can represent a whole gap.
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
    return dict(out, text=raw[a:b], source_span={'start':a,'end':b,'page_sha256':sha256(raw.encode('utf-8')).hexdigest()})


def select_excerpts(documents: Sequence[Mapping[str, Any]], question: str, top: int = TOP_PAGES) -> List[Dict[str, Any]]:
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
    chosen = [(d, i, p, True) for _, _, d, i, p in sorted(scored, key=lambda x: (x[0], x[1]))[:top]]
    if not chosen:
        first = [x for x in pages if x[0] is pages[0][0]][:2]
        chosen = [(d, i, p, False) for d, i, p in first]
    return [_excerpt(d, i, p, wanted, matched) for d, i, p, matched in chosen]
