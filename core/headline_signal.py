"""headline-v1: the shortlisted Decision technique, one implementation for the Decision lab and the live app.

LFM3 judges one served headline from the asset and the title only (no gate label, no prices). Python reads the
label-token probabilities and owns everything after that: a headline's impact is (P(UP) - P(DOWN)) over all label
mass, an asset's score is the sum over the headlines the News Researcher was served, and the call line is the
pre-registered plus or minus 0.5 (untuned). Experimental: ranking AUC 0.585 on the dev window (72 correlated
cases, 0.5 is no skill); the unseen window is sealed and not scored yet. Not a forecast, signal to trade, or advice.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional

VERSION = "headline-v1"
LABELS = ("UP", "DOWN", "ABSTAIN")
SYSTEM = (
    'You are [Headline Judge]. You get one accepted news headline about one asset. Judge its likely effect on that '
    'asset\'s price over the next five US market sessions. Return a JSON object with one key, label: UP if it is '
    'likely to push the price up, DOWN if it is likely to push it down, ABSTAIN if it has no clear price effect. '
    'Company announcements are claims, not proof. No explanation.')
SCHEMA = {'type': 'json_schema', 'json_schema': {'name': 'direction', 'strict': True, 'schema': {
    'type': 'object', 'properties': {'label': {'type': 'string', 'enum': ['UP', 'DOWN', 'ABSTAIN']}},
    'required': ['label'], 'additionalProperties': False}}}
TOP_LOGPROBS = 20
MAX_TOKENS = 24
CALL_LINE = 0.5
DEV_AUC = 0.585
NOTE = ('Experimental. The local model judged each served headline on its own (asset and title only); Python adds '
        'the judgments up. Ranking skill on past dev data was 0.585, where 0.5 is no skill, and it has not been '
        'confirmed on unseen data yet. Not a forecast or financial advice. Cofinance never trades.')
# Judgments from different weights never mix: the base narrator keeps the screened key, any other judge gets its own
# version key, so switching narrators re-judges headlines instead of summing two models' judgments together.
BASE_JUDGE = 'lfm3-1.7b-q4km'
JUDGES = {
    BASE_JUDGE: {'name': 'LFM3 1.7B', 'dev_auc': DEV_AUC, 'note': NOTE},
    'lfm3-1.7b-atomic-ad-q4km': {'name': 'LFM3 1.7B Uncensored (AtomicChat)', 'dev_auc': 0.625, 'note': (
        'Experimental. AtomicChat\'s LFM3 1.7B Uncensored judged each served headline on its own (asset and title only); '
        'Python adds the judgments up. Ranking skill on past dev data was 0.625 (base model 0.574 on the same runtime), '
        'where 0.5 is no skill, and it has not been confirmed on unseen data yet. Not a forecast or financial advice. '
        'Cofinance never trades.')},
}


def version_for(judge=None):
    if judge in (None, BASE_JUDGE):
        return VERSION
    if judge not in JUDGES:
        raise ValueError('Unknown headline judge')
    return f'{VERSION}@{judge}'


def judge_of(version):
    return next((j for j in JUDGES if version_for(j) == version), None)


def prompt(symbol: str, title: str) -> str:
    return f"Asset: {symbol}\nHeadline: {title}"


def impact(lps: Mapping[str, Optional[float]]) -> float:
    """(P(UP) - P(DOWN)) over all listed label mass, so a no-effect judgment counts near 0; 0 when nothing is listed."""
    p = {k: math.exp(v) if v is not None else 0.0 for k, v in lps.items()}
    total = sum(p.values())
    return round((p['UP'] - p['DOWN']) / total, 6) if total > 0 else 0.0


def parse_label(text: str) -> str:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and set(obj) == {'label'} and obj['label'] in LABELS:
            return obj['label']
    except (ValueError, TypeError):
        pass
    return 'ERROR'


def label_probs(choice, labels=LABELS):
    """Log-probability of each label at the label token, from the runtime's top list (raw scores before the schema).
    Returns ({label: logprob or None}, floor); a label missing from the list is None, floor is the lowest listed."""
    toks = (choice.get('logprobs') or {}).get('content') or []
    names = [t.get('token', '') for t in toks]
    if 'label' not in names:
        raise ValueError('No label token in the reply')
    for tok in toks[names.index('label') + 1:]:
        body = tok['token'].lstrip(' "')
        if body.strip(' ":\n'):
            prefix = tok['token'][:len(tok['token']) - len(body)]
            break
    else:
        raise ValueError('No label value in the reply')
    alts = tok.get('top_logprobs') or []
    mass = {k: 0.0 for k in labels}
    for alt in alts:
        rest = alt['token'][len(prefix):] if alt['token'].startswith(prefix) else ''
        hits = [k for k in mass if rest and k.startswith(rest)]
        if len(hits) == 1:
            mass[hits[0]] += math.exp(alt['logprob'])
    floor = min((a['logprob'] for a in alts), default=None)
    return {k: (math.log(v) if v > 0 else None) for k, v in mass.items()}, floor


def judge(choose: Callable, symbol: str, title: str) -> Dict:
    """One judgment. `choose(system, prompt, max_tokens=, response_format=, top_logprobs=)` returns the runtime's
    choice object. Raises on any runtime or readout failure; the caller decides what a failure means."""
    choice = choose(SYSTEM, prompt(symbol, title), max_tokens=MAX_TOKENS, response_format=SCHEMA, top_logprobs=TOP_LOGPROBS)
    lps, floor = label_probs(choice)
    return {'label': parse_label(choice['message']['content']), 'logprobs': lps, 'floor': floor, 'impact': impact(lps)}


def call_for(score: float) -> str:
    return 'UP' if score >= CALL_LINE else 'DOWN' if score <= -CALL_LINE else 'ABSTAIN'


def asset_score(impacts: Iterable[float]) -> Dict:
    s = round(sum(impacts), 6)
    return {'score': s, 'call': call_for(s)}


# Storage: one row per (version, ticker, title), next to the gate's signals in the Clock 1 database.
TABLE = """CREATE TABLE IF NOT EXISTS headline_judgments (
    namespace TEXT NOT NULL, version TEXT NOT NULL, ticker TEXT NOT NULL, title TEXT NOT NULL, label TEXT NOT NULL,
    p_up REAL, p_down REAL, p_abstain REAL, impact REAL NOT NULL, model TEXT NOT NULL, judged_at REAL NOT NULL,
    PRIMARY KEY (namespace, version, ticker, title))"""


def pending(conn: sqlite3.Connection, namespace: str = 'news', now: Optional[float] = None, limit: int = 300,
            version: str = VERSION) -> List[tuple]:
    """Live gated headlines (alert or digest, not expired) without a judgment for this version, newest first."""
    now = time.time() if now is None else now
    return conn.execute(
        "SELECT s.ticker, s.title FROM signals s WHERE s.namespace=? AND s.expires_at>? AND s.action IN ('alert','digest') "
        "AND COALESCE(s.title,'')<>'' AND NOT EXISTS (SELECT 1 FROM headline_judgments h WHERE h.namespace=s.namespace "
        "AND h.version=? AND h.ticker=s.ticker AND h.title=s.title) GROUP BY s.ticker, s.title "
        "ORDER BY MAX(s.created_at) DESC LIMIT ?", (namespace, now, version, int(limit))).fetchall()


def judge_pending(conn: sqlite3.Connection, choose: Callable, *, model: str, namespace: str = 'news',
                  clock: Callable[[], float] = time.time, check: Optional[Callable[[], None]] = None,
                  version: str = VERSION) -> Dict:
    """Judge every unjudged live headline once. Each row commits on its own, so a stop or crash keeps finished
    judgments; a failed judgment writes nothing and is retried on the next collection."""
    conn.execute(TABLE)
    todo = pending(conn, namespace, clock(), version=version)
    judged = failed = 0
    started = time.monotonic()
    for ticker, title in todo:
        if check:
            check()
        try:
            j = judge(choose, ticker, title)
        except Exception as exc:
            if type(exc).__name__ == 'Cancelled':
                raise
            failed += 1
            continue
        p = {k: (math.exp(v) if v is not None else None) for k, v in j['logprobs'].items()}
        conn.execute("INSERT OR REPLACE INTO headline_judgments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (namespace, version, ticker, title, j['label'], p['UP'], p['DOWN'], p['ABSTAIN'], j['impact'], model, clock()))
        judged += 1
    return {'version': version, 'pending': len(todo), 'judged': judged, 'failed': failed,
            'seconds': round(time.monotonic() - started, 2)}


def card(db_path: Path, served: Mapping[str, Iterable[str]], namespace: str = 'news', version: str = VERSION) -> Optional[Dict]:
    """The chat signal for one run: per asset, the sum over exactly the headlines the News Researcher was served.
    Read-only. A served headline without a judgment yet counts 0, as a failed judgment did in the screen."""
    served = {t: list(dict.fromkeys(titles)) for t, titles in served.items() if titles}
    if not served or not Path(db_path).is_file():
        return None
    try:
        db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            rows = {}
            for ticker, titles in served.items():
                marks = ','.join('?' * len(titles))
                for r in db.execute(f"SELECT title, label, impact FROM headline_judgments WHERE namespace=? AND version=? "
                                    f"AND ticker=? AND title IN ({marks})", (namespace, version, ticker, *titles)):
                    rows[(ticker, r[0])] = {'label': r[1], 'impact': r[2]}
        finally:
            db.close()
    except sqlite3.Error:
        return None
    assets = []
    for ticker, titles in served.items():
        heads = [{'title': t, **rows[(ticker, t)]} if (ticker, t) in rows else {'title': t, 'label': None, 'impact': None}
                 for t in titles]
        judged = [h for h in heads if h['impact'] is not None]
        assets.append({'ticker': ticker, **asset_score(h['impact'] for h in judged), 'served': len(heads),
                       'judged': len(judged), 'headlines': heads})
    if not any(a['judged'] for a in assets):
        return None
    judge = JUDGES[judge_of(version) or BASE_JUDGE]
    return {'version': version, 'judge': judge['name'], 'call_line': CALL_LINE, 'dev_auc': judge['dev_auc'],
            'assets': assets, 'note': judge['note']}
