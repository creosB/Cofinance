"""Experimental cached-news diagnostic. No fetching, inference, tuning or live changes.

Reuses previously inspected outcomes. Shuffles are sensitivity checks, not new
independent market observations or proof of forecast skill.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

FORMAT = 'cofinance-local-news-simulation/1'
ASSETS = ('NVDA', 'AAPL', 'MSFT', 'AMZN', 'TSLA', 'AMD', 'GOOGL', 'BTC')
FILES = {
    'dev': 'data/replay_20261116/predictions_v2.jsonl',
    'window': 'data/decision_window_20261109_headline_atomic/calls.jsonl',
    'dev_judgments': 'data/decision_screen_20261117_headline_atomic/predictions.jsonl',
    'window_judgments': 'data/decision_window_20261109_headline_atomic/judgments.jsonl',
}
PRICE_ROOT = 'data/prices_20261119/raw'
OLD_PRICE_ROOT = 'data/replay_20261116/raw'
SPEC = {
    'format': FORMAT, 'first_origin': '2026-10-27', 'last_origin': '2026-11-11',
    'split': '2026-11-06', 'horizon_spy_sessions': 5, 'news_lookback_days': 7,
    'call_line': 0.5, 'judge': 'lfm3-1.7b-atomic-ad-q4km', 'aggregation': 'sum',
    'seed': 29, 'permutations': 2000, 'bootstrap_draws': 2000,
    'arms': ['headline', 'gate', 'momentum20', 'news_count', 'always_up'],
    'conditional_metrics': ['within_origin_pair_auc', 'within_asset_pair_auc'],
    'asset_shuffle': 'Permute whole asset score paths across assets; retain every date and autocorrelation.',
    'date_shuffle': 'Every nonzero circular shift of whole cross-asset score vectors; retains asset identity and order except wrap.',
    'stress': ['no_news', 'duplicate_all', 'drop_strongest', 'reverse_sign'],
    'decision': 'Diagnostic only. No fitting, selection or promotion. Identify date/asset confounding and fragile calls.',
    'limits': 'Previously inspected outcomes, 12 overlapping origins, correlated assets. Shuffles are not new data. No independent price source.',
    'network': 'Process-local Python audit hook blocks socket, DNS, subprocess and shell events. Not OS isolation.',
}


class LocalOnlyError(RuntimeError):
    pass


def deny_network(event, args):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.posix_spawnp'}:
        raise LocalOnlyError('Local simulation cannot use sockets, DNS or child commands. Use the existing cache; missing evidence must stay missing.')


def install_local_only():
    sys.addaudithook(deny_network)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2).encode()


def write_new(path, data):
    with path.open('xb') as f:
        f.write(data)


def rows(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def price_name(asset):
    return ('BTC-USD' if asset == 'BTC' else asset) + '.json'


def input_paths():
    return [*FILES.values(), *[str(Path(folder) / price_name(t)) for folder in (PRICE_ROOT, OLD_PRICE_ROOT)
                              for t in (*ASSETS, 'SPY')]]


def freeze(root, files=None):
    if root.exists():
        raise ValueError('Use a new output folder; frozen runs cannot be overwritten')
    paths = input_paths() if files is None else files
    hashes = {p: digest(Path(p).read_bytes()) for p in paths}
    root.mkdir(parents=True)
    value = {'spec': SPEC, 'inputs': hashes, 'code_sha256': digest(Path(__file__).read_bytes())}
    write_new(root / 'protocol.json', encoded(value))
    return digest((root / 'protocol.json').read_bytes())


def verify(root):
    p = json.loads((root / 'protocol.json').read_bytes())
    if p['spec'] != SPEC or p['code_sha256'] != digest(Path(__file__).read_bytes()):
        raise ValueError('Simulation rules or code changed after freeze; use a new run')
    for name, expected in p['inputs'].items():
        if digest(Path(name).read_bytes()) != expected:
            raise ValueError('Cached input changed after freeze: ' + name)
    return p


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def price_rows(path):
    r = json.loads(path.read_bytes())['chart']['result'][0]
    q = r['indicators']['quote'][0]
    out = {}
    for i, ts in enumerate(r.get('timestamp') or []):
        day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        op, close = q['open'][i], q['close'][i]
        if finite(op) and finite(close) and op > 0 and close > 0:
            out[day] = {'open': op, 'close': close}
    return out


def judgments(*paths):
    out, failures = {}, set()
    for path in paths:
        for r in rows(path):
            ok = r.get('ok') is True and r.get('label') in ('UP', 'DOWN', 'ABSTAIN') and finite(r.get('impact'))
            value = r['impact'] if ok else 0.0
            if not ok:
                failures.add(r['id'])
            if r['id'] in out and abs(out[r['id']] - value) > 1e-6:
                raise ValueError('Conflicting cached judgments for ' + r['id'])
            out[r['id']] = value
    return out, failures


def build_case(r, bars, spy_dates, judged):
    origin, asset = r['origin'], r['symbol']
    idx = spy_dates.index(origin)
    if idx + 5 >= len(spy_dates):
        return None, 'missing_exit_session'
    entry, exit_ = spy_dates[idx + 1], spy_dates[idx + 5]
    for field, expected in [('entry_date', entry), ('exit_date', exit_)]:
        if field in r and r[field] != expected:
            raise ValueError('Cached target date disagrees: ' + r['id'])
    if entry not in bars or exit_ not in bars:
        return None, 'missing_price_bar'
    cutoff = (datetime.fromisoformat(origin).replace(tzinfo=timezone.utc) + timedelta(days=1)).timestamp()
    impacts, missing = [], 0
    for n in r.get('news_served') or []:
        at = n.get('available_at')
        if not finite(at) or not cutoff - 7 * 86400 <= at <= cutoff:
            raise ValueError('News timestamp outside origin cutoff: ' + r['id'])
        key = asset + '|' + n['title']
        missing += key not in judged
        impacts.append(judged.get(key, 0.0))
    ret = bars[exit_]['close'] / bars[entry]['open'] - 1
    if ret == 0:
        return None, 'flat_outcome'
    ds = [d for d in spy_dates if d <= origin and d in bars][-21:]
    if len(ds) != 21:
        return None, 'missing_momentum_history'
    gate = sum((n.get('direction') == 'bullish') - (n.get('direction') == 'bearish') for n in r.get('news_served') or [])
    return {'id': r['id'], 'origin': origin, 'asset': asset, 'part': 'dev' if origin <= SPEC['split'] else 'reused_window',
            'entry': entry, 'exit': exit_, 'return': ret, 'y': ret > 0, 'impacts': impacts,
            'headline': sum(impacts), 'gate': gate, 'momentum20': bars[ds[-1]]['close'] / bars[ds[0]]['close'] - 1,
            'news_count': len(impacts), 'always_up': 1.0, 'missing_judgments': missing}, None


def load_cases():
    judged, failures = judgments(Path(FILES['dev_judgments']), Path(FILES['window_judgments']))
    prices = {t: price_rows(Path(PRICE_ROOT) / price_name(t)) for t in (*ASSETS, 'SPY')}
    spy = sorted(prices['SPY'])
    cases, excluded, ids = [], Counter(), set()
    for r in rows(Path(FILES['dev'])) + rows(Path(FILES['window'])):
        if not SPEC['first_origin'] <= r['origin'] <= SPEC['last_origin']:
            excluded['outside_fixed_origins'] += 1
            continue
        if r['id'] in ids or r['symbol'] not in ASSETS:
            raise ValueError('Duplicate case ID or unknown asset')
        ids.add(r['id'])
        c, reason = build_case(r, prices[r['symbol']], spy, judged)
        if reason:
            excluded[reason] += 1
        else:
            cases.append(c)
    # Two downloads of the SAME provider, not an independent-source verification.
    checks, differences = 0, []
    for t in ASSETS:
        old = price_rows(Path(OLD_PRICE_ROOT) / price_name(t))
        for c in (c for c in cases if c['asset'] == t):
            for day, field in ((c['entry'], 'open'), (c['exit'], 'close')):
                if day in old:
                    checks += 1
                    if abs(old[day][field] / prices[t][day][field] - 1) > 1e-8:
                        differences.append({'asset': t, 'date': day, 'field': field})
    return cases, {'excluded': dict(excluded), 'cached_failed_judgments': len(failures),
                   'missing_served_judgments': sum(c['missing_judgments'] for c in cases),
                   'same_provider_snapshot_checks': checks, 'same_provider_differences': differences}


def pair_auc(cases, key='headline', group=None):
    buckets = defaultdict(list)
    for c in cases:
        buckets[c[group] if group else 'all'].append(c)
    wins, pairs = 0.0, 0
    for batch in buckets.values():
        ordered = sorted(batch, key=lambda c: c[key])
        negatives, i = 0, 0
        while i < len(ordered):
            j = i + 1
            while j < len(ordered) and ordered[j][key] == ordered[i][key]:
                j += 1
            pos = sum(c['y'] for c in ordered[i:j])
            neg = j - i - pos
            wins += pos * (negatives + 0.5 * neg)
            negatives += neg
            i = j
        positives = sum(c['y'] for c in batch)
        pairs += positives * (len(batch) - positives)
    return {'auc': wins / pairs if pairs else None, 'comparable_pairs': pairs}


def metrics(cases, key='headline'):
    line = 0 if key == 'momentum20' else SPEC['call_line']
    calls = ['UP' if c[key] > 0 and c[key] >= line else 'DOWN' if c[key] < 0 and c[key] <= -line else 'ABSTAIN' for c in cases]
    n_up = sum(c['y'] for c in cases)
    n_down = len(cases) - n_up
    up_ok = sum(c['y'] and label == 'UP' for c, label in zip(cases, calls))
    down_ok = sum(not c['y'] and label == 'DOWN' for c, label in zip(cases, calls))
    return {'n': len(cases), 'up_outcomes': n_up, 'auc': pair_auc(cases, key)['auc'],
            'within_origin': pair_auc(cases, key, 'origin'), 'within_asset': pair_auc(cases, key, 'asset'),
            'calls': dict(Counter(calls)), 'coverage': sum(v != 'ABSTAIN' for v in calls) / len(cases) if cases else None,
            'balanced_accuracy': 0.5 * (up_ok / n_up + down_ok / n_down) if n_up and n_down else None}


def quantiles(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return {'low': values[int(.025 * (len(values) - 1))], 'median': values[len(values) // 2],
            'high': values[int(.975 * (len(values) - 1))], 'draws': len(values)}


def shuffled(cases, mode, rng=None, shift=1):
    assets = sorted({c['asset'] for c in cases})
    dates = sorted({c['origin'] for c in cases})
    scores = {(c['asset'], c['origin']): c['headline'] for c in cases}
    if len(scores) != len(assets) * len(dates) or len(scores) != len(cases):
        raise ValueError('Shuffling requires the complete asset-by-origin panel; no silent case dropping')
    if mode == 'asset':
        perm = assets[:]
        rng.shuffle(perm)
        mapping = dict(zip(assets, perm))
        return [{**c, 'headline': scores[mapping[c['asset']], c['origin']]} for c in cases]
    if mode != 'date' or not 0 < shift < len(dates):
        raise ValueError('Use a nonzero date shift within the panel')
    mapping = {d: dates[(i + shift) % len(dates)] for i, d in enumerate(dates)}
    return [{**c, 'headline': scores[c['asset'], mapping[c['origin']]]} for c in cases]


def stress(cases, mode):
    out = []
    for c in cases:
        ns = c['impacts'][:]
        if mode == 'no_news':
            ns = []
        elif mode == 'duplicate_all':
            ns *= 2
        elif mode == 'drop_strongest':
            if ns:
                ns.pop(max(range(len(ns)), key=lambda i: abs(ns[i])))
        elif mode == 'reverse_sign':
            ns = [-s for s in ns]
        else:
            raise ValueError('Unknown stress mode')
        out.append({**c, 'headline': sum(ns), 'impacts': ns})
    return out


def paired_bootstrap(cases, reference, draws=2000):
    groups = defaultdict(list)
    for c in cases:
        groups[c['origin']].append(c)
    blocks = list(groups.values())
    rng, values = random.Random(SPEC['seed']), []
    for _ in range(draws):
        sample = [c for _ in blocks for c in rng.choice(blocks)]
        a, b = pair_auc(sample)['auc'], pair_auc(sample, reference)['auc']
        if a is not None and b is not None:
            values.append(a - b)
    return quantiles(values)


def analyze(cases, permutations=None, bootstrap=None):
    b = SPEC['permutations'] if permutations is None else permutations
    boot = SPEC['bootstrap_draws'] if bootstrap is None else bootstrap
    actual = pair_auc(cases)['auc']
    rng = random.Random(SPEC['seed'])
    asset_values = [pair_auc(shuffled(cases, 'asset', rng))['auc'] for _ in range(b)]
    date_values = [pair_auc(shuffled(cases, 'date', shift=k))['auc'] for k in range(1, len({c['origin'] for c in cases}))]
    return {'arms': {k: metrics(cases, k) for k in SPEC['arms']},
            'parts': {p: metrics([c for c in cases if c['part'] == p]) for p in sorted({c['part'] for c in cases})},
            'news_only': metrics([c for c in cases if c['impacts']]),
            'asset_path_shuffle': {**(quantiles(asset_values) or {}),
                                   'fraction_at_least_observed': sum(x >= actual for x in asset_values) / len(asset_values)},
            'circular_date_shift': {**(quantiles(date_values) or {}), 'aucs': date_values,
                                    'fraction_at_least_observed': sum(x >= actual for x in date_values) / len(date_values)},
            'paired_auc_differences': {k: {'point': actual - pair_auc(cases, k)['auc'],
                                         'origin_bootstrap': paired_bootstrap(cases, k, boot)} for k in ('gate', 'momentum20')},
            'stress': {k: metrics(stress(cases, k)) for k in SPEC['stress']}}


def run(root):
    verify(root)
    if (root / 'started.json').exists():
        raise ValueError('Simulation already started; do not silently rerun a viewed result')
    write_new(root / 'started.json', encoded({'protocol_sha256': digest((root / 'protocol.json').read_bytes())}))
    cases, accounting = load_cases()
    result = {'format': FORMAT, 'warning': 'Experimental diagnostic on reused outcomes. Not a forecast or financial advice.',
              'protocol_sha256': digest((root / 'protocol.json').read_bytes()), 'network': SPEC['network'],
              'cases': len(cases), 'origins': len({c['origin'] for c in cases}), 'accounting': accounting,
              'analysis': analyze(cases), 'production_changed': False, 'limits': SPEC['limits']}
    verify(root)
    write_new(root / 'cases.jsonl', ''.join(json.dumps(c, sort_keys=True) + '\n' for c in cases).encode())
    write_new(root / 'report.json', encoded(result))
    return result


def main():
    install_local_only()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['freeze', 'run'])
    ap.add_argument('--root', type=Path, required=True)
    a = ap.parse_args()
    root = a.root.resolve()
    private = Path('data').resolve()
    if root == private or not root.is_relative_to(private):
        raise ValueError('Use a new ignored data/ subfolder')
    print(freeze(root) if a.action == 'freeze' else json.dumps(run(root), indent=2))


if __name__ == '__main__':
    main()
