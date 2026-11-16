"""Risk lab: does recent volatility say how big next week's move will be, better than an asset's usual risk?

EXPERIMENTAL. A descriptive risk measurement on past prices. It says nothing about direction and is not a
trading signal or financial advice. It reuses the frozen strategy-lab cases (hash-checked): no new downloads.

Fixed before any risk number was computed on any split (2026-11-16):
  forecast  s_f^2 = k * 5 * (vol20/100)^2. k is the asset's own mean of ret^2 / (5 * (vol20/100)^2) over its
            cases that exited before this case's entry, so the scale is learned from the past only.
  baseline  s_b^2 = the asset's own mean of ret^2 over the same past cases ("as risky as it usually is").
  history   both need MIN_PAST past cases of the same asset; other cases are skipped and counted.
  loss      QLIKE = log(s^2) + ret^2 / s^2, lower is better (robust to the noise of one week's return).
  unit      one value per origin week: the mean over assets of QLIKE(baseline) - QLIKE(forecast).
  test      one-sided moving-block bootstrap of the weekly mean (BLOCK weeks, RESAMPLES draws, fixed SEED).
  gate      a split passes when the mean gain is above 0 and p <= P_MAX. A claim needs validation AND test.
            The test split opens once, only after validation passed under the same code and cases.
            Opening it is also logged in the strategy lab's test_peeks.jsonl, because it shows test-period returns.

python -m qualify.risk_lab run --root data/lab_20261109 [--final]
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .historical import digest, dump, lines

FORMAT = 'cofinance-risk-lab/1'
MIN_PAST = 52
BLOCK = 8
RESAMPLES = 4000
SEED = 20261116
P_MAX = 0.05
Z90 = 1.6449
ELEVATED = 1.25
BIG_MOVE = 2.0
SPLITS = ('dev', 'validation', 'test')
GATE = {'mean_gain_above': 0.0, 'p_value_max': P_MAX, 'must_pass': ['validation', 'test'],
        'block_weeks': BLOCK, 'resamples': RESAMPLES, 'seed': SEED, 'min_past_cases': MIN_PAST}
WARNING = ('Experimental risk measurement on past prices. Says nothing about direction. '
           'Not a trading signal or financial advice.')


def qlike(var, ret):
    return math.log(var) + ret * ret / var


def forecasts(rows):
    """Forecast and baseline variance per case, from the same asset's earlier cases only."""
    by_asset = defaultdict(list)
    for r in rows:
        by_asset[r['symbol']].append(r)
    out, skipped = [], defaultdict(int)
    for sym, cases in sorted(by_asset.items()):
        cases = sorted(cases, key=lambda r: r['entry_date'])
        for r in cases:
            past = [p for p in cases if p['exit_date'] < r['entry_date'] and p['x']['vol20'] > 0]
            vol = r['x']['vol20'] / 100
            if len(past) < MIN_PAST or vol <= 0:
                skipped[sym] += 1
                continue
            base = sum(p['return'] ** 2 for p in past) / len(past)
            k = sum(p['return'] ** 2 / (5 * (p['x']['vol20'] / 100) ** 2) for p in past) / len(past)
            fc = k * 5 * vol * vol
            if base <= 0 or fc <= 0:
                skipped[sym] += 1
                continue
            out.append({'id': r['id'], 'symbol': sym, 'origin': r['origin'], 'entry_date': r['entry_date'],
                        'split': r['split'], 'return': r['return'], 'var_f': fc, 'var_b': base})
    return out, dict(skipped)


def block_bootstrap_p(values, block=BLOCK, resamples=RESAMPLES, seed=SEED):
    """One-sided p-value that the mean is above 0. Runs of BLOCK weeks stay together because risk clusters."""
    import numpy as np
    x = np.asarray(values, dtype=float)
    n = len(x)
    if n < 2 * block:
        return None
    obs = float(x.mean())
    centered = np.concatenate([x - obs, x - obs])
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(resamples, math.ceil(n / block)))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(resamples, -1)[:, :n]
    means = centered[idx].mean(axis=1)
    return float((1 + int((means >= obs).sum())) / (resamples + 1))


def gain(c):
    return qlike(c['var_b'], c['return']) - qlike(c['var_f'], c['return'])


def describe(cases):
    if not cases:
        return {'n_cases': 0, 'passes': False}
    weeks = defaultdict(list)
    for c in cases:
        weeks[c['origin']].append(gain(c))
    weekly = [sum(v) / len(v) for _, v in sorted(weeks.items())]
    mean_gain = sum(weekly) / len(weekly)
    p = block_bootstrap_p(weekly)
    n = len(cases)

    def cover(key):
        return round(sum(abs(c['return']) <= Z90 * math.sqrt(c[key]) for c in cases) / n, 4)

    def big(group):
        if not group:
            return None
        return round(sum(abs(c['return']) > BIG_MOVE * math.sqrt(c['var_b']) for c in group) / len(group), 4)

    hot = [c for c in cases if math.sqrt(c['var_f']) > ELEVATED * math.sqrt(c['var_b'])]
    hot_ids = {c['id'] for c in hot}
    other = [c for c in cases if c['id'] not in hot_ids]
    per_asset = {}
    for sym in sorted({c['symbol'] for c in cases}):
        g = [c for c in cases if c['symbol'] == sym]
        per_asset[sym] = {'n': len(g), 'mean_gain': round(sum(gain(c) for c in g) / len(g), 4)}
    return {'n_cases': n, 'n_weeks': len(weekly),
            'from': min(c['entry_date'] for c in cases), 'through': max(c['entry_date'] for c in cases),
            'mean_qlike_forecast': round(sum(qlike(c['var_f'], c['return']) for c in cases) / n, 4),
            'mean_qlike_baseline': round(sum(qlike(c['var_b'], c['return']) for c in cases) / n, 4),
            'mean_weekly_gain': round(mean_gain, 4), 'p_value': p,
            'passes': bool(p is not None and mean_gain > GATE['mean_gain_above'] and p <= P_MAX),
            'range90_coverage': {'forecast': cover('var_f'), 'baseline': cover('var_b')},
            'elevated_case_share': round(len(hot) / n, 4),
            'big_move_rate': {'elevated': big(hot), 'other': big(other)},
            'per_asset': per_asset}


def evaluate(rows, splits):
    cases, skipped = forecasts(rows)
    return {s: describe([c for c in cases if c['split'] == s]) for s in splits}, skipped


def run(root, final=False):
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    cases_sha = digest((root / 'cases.jsonl').read_bytes())
    if cases_sha != manifest['cases_sha256']:
        raise ValueError('Changed cases')
    code = digest(Path(__file__).read_bytes())
    report_path, final_path = root / 'risk_report.json', root / 'risk_final.json'
    if final:
        if final_path.exists():
            raise ValueError('The test split was already opened once: ' + str(final_path))
        if not report_path.exists():
            raise ValueError('Run without --final first: validation must pass before the test opens')
        prior = json.loads(report_path.read_text(encoding='utf-8'))
        if prior.get('code_sha256') != code or prior.get('cases_sha256') != cases_sha:
            raise ValueError('Code or cases changed since the validation run; rerun without --final')
        if not prior['splits']['validation'].get('passes'):
            raise ValueError('Validation did not pass; the test split stays locked')
    splits, skipped = evaluate(lines(root / 'cases.jsonl'), SPLITS if final else SPLITS[:2])
    report = {'format': FORMAT, 'warning': WARNING, 'cases_sha256': cases_sha, 'code_sha256': code,
              'gate': GATE, 'skipped_without_history': skipped, 'splits': splits}
    if final:
        at = datetime.now(timezone.utc).isoformat()
        report['claim'] = all(splits[s]['passes'] for s in GATE['must_pass'])
        report['opened_at'] = at
        entry = {'at': at, 'by': 'risk_lab', 'code_sha256': code, 'claim': report['claim']}
        for name in ('risk_peeks.jsonl', 'test_peeks.jsonl'):
            with (root / name).open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(entry) + '\n')
    dump(final_path if final else report_path, report)
    return report


def table(report):
    out = []
    for name, s in report['splits'].items():
        if not s.get('n_cases'):
            out.append(f'{name}: no cases')
            continue
        out.append(f"{name} {s['from']}..{s['through']} cases={s['n_cases']} weeks={s['n_weeks']} "
                   f"qlike f={s['mean_qlike_forecast']} b={s['mean_qlike_baseline']} gain={s['mean_weekly_gain']} "
                   f"p={s['p_value']} {'PASS' if s['passes'] else 'no pass'} "
                   f"cover90 f={s['range90_coverage']['forecast']} b={s['range90_coverage']['baseline']} "
                   f"big moves elevated={s['big_move_rate']['elevated']} other={s['big_move_rate']['other']}")
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['run'])
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--final', action='store_true')
    a = ap.parse_args()
    rep = run(a.root, a.final)
    print(table(rep))
    if a.final:
        print('claim:', rep['claim'])


if __name__ == '__main__':
    main()
