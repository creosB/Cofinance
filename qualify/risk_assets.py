"""Risk flag on new assets: the frozen risk lab v2 rule, unchanged, on nine assets the lab never used.

EXPERIMENTAL. A descriptive risk measurement on past prices. It says nothing about direction and is not a
trading signal or financial advice.

Fixed 2026-11-16, before any of these assets' prices were fetched:
  assets    GOOGL AMZN JPM XOM KO JNJ WMT CAT SPY (one per sector, plus the market)
  data      same Yahoo chart host, window, target and splits as the strategy lab (qualify.lab)
  rule      qualify.risk_lab v2 unchanged: flagged when the forecast is above 1.25 x usual risk;
            outcome is a move above one usual weekly sd; weekly block bootstrap, same seed
  decide    on the test window only: the pooled flag passes (difference above 0, p <= 0.05) AND at least
            MIN_AGREE of the nine assets point the same way (at least one flagged case and a flagged rate
            above the other rate). Anything else: not confirmed, and the flag stays out of chat.
  context   dev and validation are reported; their market-wide periods were partly seen through the lab's assets
  one look  evaluate writes verdict.json once and refuses to run again

python -m qualify.risk_assets collect  --root data/risk_assets_20261116
python -m qualify.risk_assets build    --root data/risk_assets_20261116
python -m qualify.risk_assets evaluate --root data/risk_assets_20261116
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from . import risk_lab
from .historical import END, bars, digest, dump, lines, write_lines
from .lab import FETCH_START, extras, split_of

FORMAT = 'cofinance-risk-assets/1'
ASSETS = ('GOOGL', 'AMZN', 'JPM', 'XOM', 'KO', 'JNJ', 'WMT', 'CAT', 'SPY')
MIN_AGREE = 6


def collect(root):
    from core.market import fetch_json
    root.mkdir(parents=True, exist_ok=True)
    start = int(FETCH_START.timestamp())
    end = int(datetime.combine(END, datetime.min.time(), timezone.utc).timestamp())
    for s in ASSETS:
        path = root / (s + '.json')
        if path.exists():
            continue
        raw = fetch_json(f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(s)}'
                         f'?period1={start}&period2={end}&interval=1d')
        if not (raw.get('chart') or {}).get('result'):
            raise ValueError('Missing source result: ' + s)
        path.write_text(json.dumps(raw), encoding='utf-8')
    receipt = {'downloaded_at': datetime.now(timezone.utc).isoformat(),
               'sources': {s: digest((root / (s + '.json')).read_bytes()) for s in ASSETS}}
    dump(root / 'collection.json', receipt)
    return receipt


def build(root):
    if (root / 'cases.jsonl').exists():
        raise ValueError('Cases already built: use a new root')
    series = {s: bars(json.loads((root / (s + '.json')).read_text(encoding='utf-8'))) for s in ASSETS}
    cal = sorted(series['SPY'])
    rows, dropped = [], defaultdict(int)
    for i in range(60, len(cal) - 5, 5):
        origin, entry, exit_ = cal[i], cal[i + 1], cal[i + 5]
        dates = cal[i - 60:i + 1]
        split = split_of(entry, exit_)
        if not split:
            dropped['embargo'] += 1
            continue
        spy = [series['SPY'][d] for d in dates]
        for a in ASSETS:
            if any(d not in series[a] for d in dates + [entry, exit_]):
                dropped['missing_' + a] += 1
                continue
            x = extras([series[a][d] for d in dates], spy)
            x['is_crypto'] = 0
            rows.append({'id': f'{a}:{origin}', 'symbol': a, 'origin': origin, 'entry_date': entry,
                         'exit_date': exit_, 'split': split, 'x': x,
                         'return': series[a][exit_]['close'] / series[a][entry]['open'] - 1})
    write_lines(root / 'cases.jsonl', rows)
    manifest = {'format': FORMAT, 'assets': list(ASSETS), 'cases_sha256': digest((root / 'cases.jsonl').read_bytes()),
                'n': len(rows), 'by_split': {s: sum(r['split'] == s for r in rows) for s in risk_lab.SPLITS},
                'dropped': dict(dropped)}
    dump(root / 'manifest.json', manifest)
    return manifest


def per_asset(cases):
    """Flagged and other counts of bigger-than-usual weeks per asset, and whether the asset agrees."""
    out = {}
    for sym in ASSETS:
        g = [c for c in cases if c['symbol'] == sym]
        hot = [c for c in g if math.sqrt(c['var_f']) > risk_lab.ELEVATED * math.sqrt(c['var_b'])]
        oth = [c for c in g if math.sqrt(c['var_f']) <= risk_lab.ELEVATED * math.sqrt(c['var_b'])]
        big = lambda grp: sum(abs(c['return']) > risk_lab.FLAG_SD * math.sqrt(c['var_b']) for c in grp)
        hr = big(hot) / len(hot) if hot else None
        orr = big(oth) / len(oth) if oth else None
        out[sym] = {'flagged': len(hot), 'flagged_big': big(hot), 'other': len(oth), 'other_big': big(oth),
                    'agrees': bool(hot and oth and hr > orr)}
    return out


def decide(test_split, assets):
    agree = sum(a['agrees'] for a in assets.values())
    return {'pooled_flag_passes': bool(test_split['flag']['passes']), 'assets_agreeing': agree,
            'assets_needed': MIN_AGREE,
            'confirmed': bool(test_split['flag']['passes'] and agree >= MIN_AGREE)}


def evaluate(root):
    verdict_path = root / 'verdict.json'
    if verdict_path.exists():
        raise ValueError('Already evaluated once: ' + str(verdict_path))
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if digest((root / 'cases.jsonl').read_bytes()) != manifest['cases_sha256']:
        raise ValueError('Changed cases')
    if manifest.get('assets') != list(ASSETS):
        raise ValueError('Asset list changed since build')
    rows = lines(root / 'cases.jsonl')
    splits, skipped = risk_lab.evaluate(rows, risk_lab.SPLITS)
    test_cases = [c for c in risk_lab.forecasts(rows)[0] if c['split'] == 'test']
    assets = per_asset(test_cases)
    verdict = {'format': FORMAT, 'warning': risk_lab.WARNING, 'evaluated_at': datetime.now(timezone.utc).isoformat(),
               'cases_sha256': manifest['cases_sha256'], 'code_sha256': digest(Path(__file__).read_bytes()),
               'risk_lab_code_sha256': digest(Path(risk_lab.__file__).read_bytes()),
               'assets': list(ASSETS), 'skipped_without_history': skipped,
               'decision': decide(splits['test'], assets), 'test_per_asset': assets, 'splits': splits}
    dump(verdict_path, verdict)
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['collect', 'build', 'evaluate'])
    ap.add_argument('--root', type=Path, required=True)
    a = ap.parse_args()
    if a.action == 'collect':
        print(json.dumps(collect(a.root), indent=2))
    elif a.action == 'build':
        print(json.dumps(build(a.root), indent=2))
    else:
        v = evaluate(a.root)
        print(risk_lab.table({'splits': v['splits']}))
        print(json.dumps(v['decision']))
        print(json.dumps(v['test_per_asset']))


if __name__ == '__main__':
    main()
