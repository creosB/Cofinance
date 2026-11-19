"""Readout coherence check v1. EXPERIMENTAL dev screen, not a forecast benchmark. Cofinance never trades.

Before any outcome is scored, a probability has to behave like one. This check asks the default narrator questions
whose answers are known (facts on the input card) or whose probabilities are tied together by logic (above versus
below one level, a ladder of levels, swapped answer labels), and reads every answer three ways:

  prefix-v2  the Polymarket replay's readout: first-token YES share, one output token, top 20 logprobs
  label      a JSON reply {"label": "YES" | "NO"}, read from the label token's probabilities (as headline-v1 reads labels)
  stated     a JSON reply {"probability": 0..100}, the number the model writes

Inputs are the price cards sealed in Polymarket v3 record 1; no outcome is read. Passing is necessary, not
sufficient: a coherent readout can still have no forecasting skill. One run per protocol.

    python -m qualify.readout_check freeze --root data/readout_check_v1 --publish docs/benchmarks/readout-check-protocol-v1.json
    python -m qualify.readout_check run    --root data/readout_check_v1 --publish docs/benchmarks/readout-check-v1.json
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from .historical import digest, dump
from .pm_replay import SYSTEM_BLIND, auc, first_token_logprobs, ledger, readout

FORMAT = 'cofinance-readout-check/1'
WARNING = ('Experimental dev screen of how a probability is read from the local model. Not a forecast, trading signal '
           'or financial advice. Cofinance never trades.')
SOURCE_ROOT, SOURCE_RECORD = 'data/pm_replay3', 1
NAMES = {'BTC-USD': 'Bitcoin', 'ETH-USD': 'Ethereum', 'NVDA': 'NVIDIA (NVDA)'}
HORIZON_DATE, HORIZON_UTC = 'November 25', '2026-11-25T17:00:00Z'
# Known-answer questions: (kind, card field, subject, multipliers of that field). Each gives 1 YES and 1 NO at least.
KNOWN = (('close', 'last_close', 'the last completed daily close of {name}', (0.5, 0.8, 1.25, 2.0)),
         ('high', 'one_year_high', 'the one-year high of {name}', (0.8, 1.25)),
         ('low', 'one_year_low', 'the one-year low of {name}', (0.8, 1.25)))
LADDER = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3)  # "above" levels as multiples of the last close
PAIRED = (0.9, 1.0, 1.1)                        # also asked as "below", and with swapped labels
TOLERANCE = 0.01                                # a ladder step may rise this much before it is a violation
ASK = 'Answer whether the first listed outcome will happen. Reply with exactly YES or NO.'
SYSTEMS = {
    'prefix-v2': SYSTEM_BLIND,
    'prefix-v2-swapped': SYSTEM_BLIND.replace(ASK, 'Answer whether the first listed outcome will happen. Reply with '
                                              'exactly NO if it will happen and YES if it will not.'),
    'label': SYSTEM_BLIND.replace(ASK, 'Answer whether the first listed outcome will happen. Return a JSON object with '
                                  'one key, label: YES if it will happen, NO if it will not.'),
    'label-swapped': SYSTEM_BLIND.replace(ASK, 'Answer whether the first listed outcome will happen. Return a JSON '
                                          'object with one key, label: NO if it will happen, YES if it will not.'),
    'stated': SYSTEM_BLIND.replace(ASK, 'Estimate the probability that the first listed outcome happens. Return a JSON '
                                   'object with one key, probability: a whole number from 0 to 100.')}
LABEL_SCHEMA = {'type': 'json_schema', 'json_schema': {'name': 'answer', 'strict': True, 'schema': {
    'type': 'object', 'properties': {'label': {'type': 'string', 'enum': ['YES', 'NO']}},
    'required': ['label'], 'additionalProperties': False}}}
STATED_SCHEMA = {'type': 'json_schema', 'json_schema': {'name': 'estimate', 'strict': True, 'schema': {
    'type': 'object', 'properties': {'probability': {'type': 'integer'}},
    'required': ['probability'], 'additionalProperties': False}}}
ARMS = ('prefix-v2', 'label', 'stated')
SWAPPED_ARMS = ('prefix-v2', 'label')
DECODING = {'prefix-v2': {'max_tokens': 1, 'top_logprobs': 20},
            'label': {'max_tokens': 24, 'top_logprobs': 20, 'response_format': LABEL_SCHEMA},
            'stated': {'max_tokens': 24, 'response_format': STATED_SCHEMA}}
GATES = {'known_auc_min': 0.90, 'known_accuracy_min': 0.75, 'complement_error_max': 0.15,
         'monotone_violation_rate_max': 0.10, 'swap_error_max': 0.15, 'failure_rate_max': 0.10}


def protocol(narrator_id, narrator_sha, source_head):
    return {
        'format': FORMAT, 'warning': WARNING,
        'question': 'Does a readout of the default narrator behave like a probability on questions with known answers '
                    'or logically linked probabilities? Necessary, not sufficient, for forecasting skill.',
        'source': {'root': SOURCE_ROOT, 'record': SOURCE_RECORD, 'ledger_head_sha256': source_head,
                   'use': 'the price card and as-of time of the first case of each asset; no outcome is read'},
        'names': NAMES, 'horizon': {'date': HORIZON_DATE, 'end_utc': HORIZON_UTC},
        'levels': 'multiplier times the card field, rounded to 3 significant figures',
        'known': [{'kind': k, 'field': f, 'subject': s, 'multipliers': list(m)} for k, f, s, m in KNOWN],
        'ladder': list(LADDER), 'paired': list(PAIRED), 'ladder_tolerance': TOLERANCE,
        'prompt': 'JSON of question, first_outcome Yes, end_utc, the price card and the no-news line, as in the replay',
        'systems': SYSTEMS, 'decoding': {'temperature': 0, 'thinking': False, **DECODING},
        'readouts': {
            'prefix-v2': 'Polymarket replay v2 readout over the first token; swapped labels: event probability = 1 - P',
            'label': 'label-token probabilities of YES and NO (prefix matched as in headline-v1), P(YES) over both; '
                     'swapped labels: 1 - P',
            'stated': 'the integer written, divided by 100; outside 0 to 100 or unparsable is a failure'},
        'failure': 'scored 0.5 and counted in the failure rate',
        'metrics': {
            'known_auc': 'ranking AUC of P against the known answers (0.5 = no skill)',
            'known_accuracy': 'share of known answers on the right side of 0.5',
            'complement_error': 'mean |P(above K) + P(below K) - 1| over the paired levels',
            'monotone_violation_rate': 'share of adjacent ladder steps where P(above) rises by more than the tolerance',
            'swap_error': 'mean |P - P with swapped labels| over the paired levels (label arms only)',
            'ladder_gap': 'mean P(above 0.7 x close) minus P(above 1.3 x close), reported only'},
        'gates': GATES,
        'verdict': 'an arm is "coherent on these checks" only if every gate holds; otherwise "not coherent" with the '
                   'failed gates listed',
        'narrator': {'id': narrator_id, 'sha256': narrator_sha, 'must_be_qualified': True},
        'scope': 'Dev screen of the readout only. No market outcome, no forecast claim, not the research pipeline.'}


def freeze(root, p, publish=None):
    root = Path(root)
    path = root / 'protocol.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != p:
            raise ValueError('Protocol changed: start a new --root instead of mixing experiments')
    else:
        root.mkdir(parents=True, exist_ok=True)
        dump(path, p)
    if publish:
        Path(publish).write_bytes(path.read_bytes())
    return digest(path.read_bytes())


# ------------------------------------------------------------------ cases

def nice(x):
    return float(f'{x:.3g}')


def money(x):
    return f'${x:,.0f}' if x >= 100 else f'${x:,.2f}'


def cards(entries):
    """({asset: price card}, as-of utc) from the sealed source record: the first case of each tracked asset."""
    entry = entries[SOURCE_RECORD - 1]
    out = {}
    for c in entry['cases']:
        if c['asset'] in NAMES and c.get('prices') and c['asset'] not in out:
            out[c['asset']] = c['prices']
    return out, entry['data_as_of_utc']


def cases(price_cards, as_of):
    rows = []
    for asset in sorted(price_cards):
        card, name = price_cards[asset], NAMES[asset]
        base = {'asset': asset, 'prices': card}
        for kind, field, subject, mults in KNOWN:
            for m in mults:
                level = nice(card[field] * m)
                rows.append({**base, 'id': f'{asset}:known:{kind}:{m}', 'family': 'known', 'mult': m, 'level': level,
                             'truth': card[field] > level, 'end_utc': as_of,
                             'question': f'Was {subject.format(name=name)} above {money(level)}?'})
        for family, word, mults in (('ladder', 'above', LADDER), ('below', 'below', PAIRED)):
            for m in mults:
                level = nice(card['last_close'] * m)
                rows.append({**base, 'id': f'{asset}:{word}:{m}', 'family': family, 'mult': m, 'level': level,
                             'end_utc': HORIZON_UTC,
                             'question': f'Will the price of {name} be {word} {money(level)} on {HORIZON_DATE}?'})
    return rows


def prompt(case):
    from core.orchestrator import NO_NEWS
    return json.dumps({'question': case['question'], 'first_outcome': 'Yes', 'end_utc': case['end_utc'],
                       'prices': case['prices'], 'headlines': NO_NEWS}, sort_keys=True)


def plan(rows):
    """Every (arm, variant, case) call, in a fixed order."""
    calls = [(arm, 'normal', c) for arm in ARMS for c in rows]
    calls += [(arm, 'swapped', c) for arm in SWAPPED_ARMS for c in rows if c['family'] == 'ladder' and c['mult'] in PAIRED]
    return calls


# ------------------------------------------------------------------ readouts

def read_prefix(choice):
    return readout(first_token_logprobs(choice))


def read_label(choice):
    from core.headline_signal import label_probs
    try:
        lps, _ = label_probs(choice, labels=('YES', 'NO'))
    except (ValueError, KeyError, TypeError):
        return .5, True
    yes, no = (math.exp(lps[k]) if lps[k] is not None else 0.0 for k in ('YES', 'NO'))
    if yes + no <= 0:
        return .5, True
    return yes / (yes + no), False


def read_stated(choice):
    try:
        value = json.loads(choice['message']['content']).get('probability')
    except (ValueError, TypeError, KeyError, AttributeError):
        return .5, True
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        return .5, True
    return value / 100, False


READERS = {'prefix-v2': read_prefix, 'label': read_label, 'stated': read_stated}


def event_probability(arm, variant, choice):
    """(probability that the first outcome happens, failed). Swapped labels invert the reading."""
    p, failed = READERS[arm](choice)
    if failed:
        return .5, True
    return (1 - p if variant == 'swapped' else p), False


# ------------------------------------------------------------------ metrics and verdict

def metrics(rows, normal, swapped=None):
    """normal / swapped: {case id: (event probability, failed)} for one arm."""
    p = {k: v[0] for k, v in normal.items()}
    known = [c for c in rows if c['family'] == 'known']
    out = {'known_auc': auc([p[c['id']] for c in known], [c['truth'] for c in known]),
           'known_accuracy': sum((p[c['id']] >= .5) == c['truth'] for c in known) / len(known)}
    below = {(c['asset'], c['mult']): c['id'] for c in rows if c['family'] == 'below'}
    pairs = [(c['id'], below[(c['asset'], c['mult'])]) for c in rows
             if c['family'] == 'ladder' and (c['asset'], c['mult']) in below]
    out['complement_error'] = sum(abs(p[a] + p[b] - 1) for a, b in pairs) / len(pairs)
    steps = violations = 0
    gaps = []
    for asset in sorted({c['asset'] for c in rows}):
        ladder = sorted((c for c in rows if c['family'] == 'ladder' and c['asset'] == asset), key=lambda c: c['mult'])
        for lo, hi in zip(ladder, ladder[1:]):
            steps += 1
            violations += p[hi['id']] > p[lo['id']] + TOLERANCE
        gaps.append(p[ladder[0]['id']] - p[ladder[-1]['id']])
    out['monotone_violation_rate'] = violations / steps
    out['ladder_gap'] = sum(gaps) / len(gaps)
    if swapped is not None:
        out['swap_error'] = sum(abs(p[k] - swapped[k][0]) for k in swapped) / len(swapped)
    calls = list(normal.values()) + list((swapped or {}).values())
    out['calls'] = len(calls)
    out['failure_rate'] = sum(bool(f) for _, f in calls) / len(calls)
    failed = failed_gates(out)
    out['failed_gates'] = failed
    out['verdict'] = 'not coherent' if failed else 'coherent on these checks'
    return out


def failed_gates(m):
    g, out = GATES, []
    if m['known_auc'] is None or m['known_auc'] < g['known_auc_min']:
        out.append('known_auc')
    if m['known_accuracy'] < g['known_accuracy_min']:
        out.append('known_accuracy')
    if m['complement_error'] > g['complement_error_max']:
        out.append('complement_error')
    if m['monotone_violation_rate'] > g['monotone_violation_rate_max']:
        out.append('monotone_violation_rate')
    if 'swap_error' in m and m['swap_error'] > g['swap_error_max']:
        out.append('swap_error')
    if m['failure_rate'] > g['failure_rate_max']:
        out.append('failure_rate')
    return out


# ------------------------------------------------------------------ run

def run(root, *, narrator, narrator_id, narrator_sha, source_root=SOURCE_ROOT, publish=None, clock=time.time):
    root = Path(root)
    entries, head = ledger(source_root)
    p = protocol(narrator_id, narrator_sha, head)
    path = root / 'protocol.json'
    if not path.exists() or json.loads(path.read_text(encoding='utf-8')) != p:
        raise ValueError('Freeze this protocol first; a changed protocol needs a new --root')
    if (root / 'results.json').exists():
        raise ValueError('Already run: one look per protocol')
    rows = cases(*cards(entries))
    calls, answers = plan(rows), {}
    started = clock()
    with narrator() as (call, meta):
        if meta.get('model_sha256') != narrator_sha:
            raise ValueError('The narrator weights differ from the frozen protocol')
        for arm, variant, c in calls:
            system = SYSTEMS[arm if variant == 'normal' else arm + '-swapped']
            rec = {'arm': arm, 'variant': variant, 'id': c['id'], 'error': None, 'content': None, 'first_top': []}
            try:
                choice = call(system, prompt(c), return_choice=True, allow_length=True, timeout=60, **DECODING[arm])
                rec['content'] = (choice.get('message') or {}).get('content')
                rec['first_top'] = [[t, lp] for t, lp in first_token_logprobs(choice)]
                prob, failed = event_probability(arm, variant, choice)
            except Exception as exc:
                rec['error'], prob, failed = f'{type(exc).__name__}: {exc}'[:200], .5, True
            rec.update(p_event=round(prob, 6), failed=failed)
            answers[(arm, variant, c['id'])] = rec
    finished = clock()
    report = {'format': FORMAT, 'warning': WARNING, 'protocol_sha256': digest(path.read_bytes()), 'model': meta,
              'code_sha256': digest(Path(__file__).read_bytes()), 'cases': len(rows), 'calls': len(calls),
              'seconds': round(finished - started, 1),
              'run_at_utc': datetime.fromtimestamp(finished, timezone.utc).isoformat(), 'arms': {}}
    for arm in ARMS:
        normal = {k[2]: (r['p_event'], r['failed']) for k, r in answers.items() if k[0] == arm and k[1] == 'normal'}
        swapped = {k[2]: (r['p_event'], r['failed']) for k, r in answers.items() if k[0] == arm and k[1] == 'swapped'}
        report['arms'][arm] = metrics(rows, normal, swapped if arm in SWAPPED_ARMS else None)
    dump(root / 'results.json', {'cases': [{k: v for k, v in c.items() if k != 'prices'} for c in rows],
                                 'answers': list(answers.values())})
    dump(root / 'report.json', report)
    if publish:
        dump(Path(publish), report)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=['freeze', 'run'])
    ap.add_argument('--root', type=Path, default=Path('data/readout_check_v1'))
    ap.add_argument('--model-root', type=Path, default=Path('data/local_models'))
    ap.add_argument('--publish', type=Path, help='freeze: protocol copy; run: compact report')
    a = ap.parse_args()
    from core.local_models import DEFAULT_NARRATOR, LocalModels, narrator_spec
    sha = narrator_spec(DEFAULT_NARRATOR)['sha']
    if a.action == 'freeze':
        _, head = ledger(SOURCE_ROOT)
        print(freeze(a.root, protocol(DEFAULT_NARRATOR, sha, head), a.publish))
        return
    from .history_runtime import default_backend, narrator
    backend = default_backend(a.model_root)
    if not LocalModels(a.model_root, Path(__file__).resolve().parents[1]).qualified(backend, DEFAULT_NARRATOR):
        raise SystemExit(f'{DEFAULT_NARRATOR} is not qualified on {backend}; qualify it in Models first')
    out = run(a.root, narrator=lambda: narrator(a.model_root, a.root / 'model-runtime.server.log', alias='readout-check',
                                                backend=backend, model_id=DEFAULT_NARRATOR),
              narrator_id=DEFAULT_NARRATOR, narrator_sha=sha, publish=a.publish)
    print(json.dumps(out['arms'], indent=2))


if __name__ == '__main__':
    main()
