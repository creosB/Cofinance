"""Fresh-sample retest of the two Polymarket bucket hypotheses. EXPERIMENTAL, read-only, no trading, not advice.

The 2026-11-10 study looked at many price buckets and flagged two as hypotheses only. This retest freezes both tests,
the decision rule and a new sample (other end days, so no market or event repeats) before any price is downloaded,
then takes exactly one look.

python -m qualify.pm_retest freeze   --root data/pm_retest_20261116
python -m qualify.pm_lab    history  --root data/pm_retest_20261116 --shard 0 --shards 4    (shards 1 to 3 too)
python -m qualify.pm_retest evaluate --root data/pm_retest_20261116                        (one look, then locked)
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from core.polymarket import list_markets
from . import pm_lab
from .historical import digest, dump, lines

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / 'data' / 'pm_lab_20261110'
SEED = 20261116
SIZE = 3000
ALPHA = .025   # two tests share the usual 5%
PREREG = {
    'format': 'cofinance-pm-retest/1',
    'why': 'The first study inspected many buckets. These two stood out and were labelled hypotheses, not findings.',
    'alpha_each': ALPHA,
    'tests': {
        'H1': {'horizon': '30d', 'price_lo': 0.0, 'price_hi': .20, 'direction': 'fewer',
               'statement': 'A month before close, outcomes priced below 20 cents win less often than their prices say.'},
        'H2': {'horizon': '1d', 'price_lo': .20, 'price_hi': .30, 'direction': 'more',
               'statement': 'A day before close, outcomes priced 20 to 30 cents win more often than their prices say.'}},
    'unit': 'One market per event (the lowest id), because markets in one event move together. All markets is reported too.',
    'p_value': 'Exact one-sided Poisson-binomial tail of the observed wins, each market winning at its recorded price.',
    'decision': 'Confirmed only when the p-value is below alpha_each. Anything else is not confirmed.',
    'single_look': 'evaluate records its verdict once in retest.json and refuses to run again.'}


def tail(ps, k, direction):
    """P(X <= k) for 'fewer', P(X >= k) for 'more', where X counts wins and market i wins with probability ps[i]."""
    dist = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dist) + 1)
        for j, q in enumerate(dist):
            nxt[j] += q * (1 - p)
            nxt[j + 1] += q * p
        dist = nxt
    return min(1.0, sum(dist[:k + 1]) if direction == 'fewer' else sum(dist[k:]))


def one_per_event(rows):
    pick = {}
    for r in sorted(rows, key=lambda r: r['id']):
        pick.setdefault(r['event_id'] or r['id'], r)
    return list(pick.values())


def verdict(rows, spec):
    """rows: dicts with id, event_id, p (price at the horizon) and y (1 if the scored outcome won)."""
    chosen = [r for r in rows if spec['price_lo'] <= r['p'] < spec['price_hi']]
    out = {}
    for unit, sel in (('per_event', one_per_event(chosen)), ('all_markets', chosen)):
        ps, k = [r['p'] for r in sel], sum(r['y'] for r in sel)
        out[unit] = {'n': len(sel), 'expected_wins': round(sum(ps), 3), 'wins': k,
                     'p_value': tail(ps, k, spec['direction']) if sel else None}
    p = out['per_event']['p_value']
    out['verdict'] = 'confirmed' if p is not None and p < ALPHA else 'not confirmed'
    return out


def freeze(root, lister=list_markets, original=ORIGINAL):
    """New sample from end days the first study never used, with the tests written into the protocol before any price."""
    old = json.loads((original / 'protocol.json').read_text(encoding='utf-8'))
    out = pm_lab.collect(root, lister, seed=SEED, days=pm_lab.DAYS, size=SIZE, avoid_days=old['sampled_end_days'],
                         extra={'retest': PREREG, 'first_study': {'protocol_sha256': digest((original / 'protocol.json').read_bytes()),
                                                                  'sample_sha256': old['sample_sha256']}})
    seen = {m['id']: m['event_id'] for m in lines(original / 'sample.jsonl')}
    fresh = lines(root / 'sample.jsonl')
    repeats = [m['id'] for m in fresh if m['id'] in seen]
    if repeats:
        raise ValueError(f'{len(repeats)} sampled markets were already in the first study. Delete {root} and look at why.')
    old_events = {e for e in seen.values() if e}
    out['events_shared_with_first_study'] = len({m['event_id'] for m in fresh if m['event_id'] in old_events})
    return out


def rows_at(sample, hist, horizon):
    days = pm_lab.HORIZONS[horizon]
    rows = []
    for mid, m in sample.items():
        h = hist.get(mid)
        price = pm_lab.price_at(h['points'], m['anchor'] - days * 86400) if h else None
        if price is not None:
            rows.append({'id': mid, 'event_id': m['event_id'], 'p': price, 'y': m['y']})
    return rows


def evaluate(root):
    """The one look. Refuses a changed sample, changed tests, missing histories or a second run."""
    protocol = json.loads((root / 'protocol.json').read_text(encoding='utf-8'))
    if digest((root / 'sample.jsonl').read_bytes()) != protocol['sample_sha256']:
        raise ValueError('The sample changed after it was frozen.')
    if protocol.get('retest') != json.loads(json.dumps(PREREG)):
        raise ValueError('The tests changed after the sample was frozen. A new idea needs a new root.')
    if (root / 'retest.json').exists():
        raise ValueError('The one look is already taken: see retest.json. A new idea needs a new root.')
    sample = {m['id']: m for m in lines(root / 'sample.jsonl')}
    hist = {h['id']: h for f in pm_lab.history_files(root) for h in lines(f) if h['id'] in sample}
    if len(hist) < len(sample):
        raise ValueError(f'{len(sample) - len(hist)} markets have no price history yet. Finish the history step first.')
    tests = {name: verdict(rows_at(sample, hist, spec['horizon']), spec) for name, spec in PREREG['tests'].items()}
    pm_lab.evaluate(root)   # full calibration table to report.json, for context only
    report = {'format': PREREG['format'], 'warning': pm_lab.WARNING,
              'looked_at_utc': datetime.now(timezone.utc).isoformat(),
              'protocol_sha256': digest((root / 'protocol.json').read_bytes()),
              'sampled': len(sample), 'history_errors': sum(1 for h in hist.values() if h.get('error')),
              'tests': {name: {**PREREG['tests'][name], **t} for name, t in tests.items()}}
    dump(root / 'retest.json', report)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['freeze', 'evaluate'])
    ap.add_argument('--root', type=Path, required=True)
    a = ap.parse_args()
    if a.action == 'freeze':
        print(json.dumps(freeze(a.root), indent=2))
    else:
        r = evaluate(a.root)
        print(f"sampled {r['sampled']}, history errors {r['history_errors']}")
        for name, t in r['tests'].items():
            e, m = t['per_event'], t['all_markets']
            print(f"{name} [{t['horizon']}, {t['price_lo']:.2f} to {t['price_hi']:.2f}, {t['direction']} wins than priced]: {t['verdict']}")
            print(f"  per event: n={e['n']} expected {e['expected_wins']} won {e['wins']} p={e['p_value']}")
            print(f"  all markets: n={m['n']} expected {m['expected_wins']} won {m['wins']} p={m['p_value']}")


if __name__ == '__main__':
    main()
