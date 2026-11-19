"""Bitcoin threshold baseline v1 (accuracy plan step 3). EXPERIMENTAL, read-only; Cofinance never trades.

Python-only probabilities from price, volatility and horizon for Polymarket's daily "Bitcoin above ___ on <date>?"
markets, chosen on the first half of the year and scored once on the second half. The readout check (step 1) found
no coherent probability in the narrator, so a model feature can only be added later on top of this baseline.

Data: the frozen sample of the v1 crowd comparison (markets, recorded crowd prices, Yahoo hourly BTC) in
data/btc_crowd_20261116. Nothing is downloaded. That report already showed the crowd, the untuned 720 h model and
their average on both halves; none of the fitted candidates here has been scored on the second half.

    python -m qualify.btc_baseline freeze --root data/btc_baseline_v1 --publish docs/benchmarks/btc-baseline-protocol-v1.json
    python -m qualify.btc_baseline run    --root data/btc_baseline_v1 --publish docs/benchmarks/btc-baseline-v1.json
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .btc_crowd import CONTESTED, SPLIT_DAY, crowd_files, hourly, model_p, spot_at, vol_at
from .historical import digest, dump, lines

FORMAT = 'cofinance-btc-baseline/1'
WARNING = ('Experimental. Retrospective comparison with recorded crowd prices on resolved markets. Not a forecast, '
           'trading signal or financial advice. Cofinance never trades.')
SOURCE = 'data/btc_crowd_20261116'
HORIZONS = {'24h': 24, '6h': 6}
PRIMARY = '24h'
HALF_LIVES = (24, 72, 168, 336)       # EWMA half-life grid, hours
DFS = (3, 4, 6, 10)                   # Student-t degrees of freedom grid
CLIP = 1e-3
BOOT, SEED = 2000, 20261119
BASELINES = ('v1', 'ewma', 'student_t', 'recal')   # Python only; one is chosen on the first half
RIDGE = 1e-4


# ------------------------------------------------------------------ probability pieces

def clip(p):
    return min(1 - CLIP, max(CLIP, p))


def logit(p):
    p = clip(p)
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + math.exp(-x)) if x >= 0 else math.exp(x) / (1 + math.exp(x))


def t_cdf(x, df):
    """Student-t CDF for an integer df (Abramowitz and Stegun 26.7.3 and 26.7.4); no third-party dependency."""
    if df < 1 or int(df) != df:
        raise ValueError('integer df required')
    theta = math.atan(abs(x) / math.sqrt(df))
    c, s = math.cos(theta), math.sin(theta)
    if df % 2:
        a = 2 * theta / math.pi
        if df > 1:
            term, total = c, c
            for k in range(3, df - 1, 2):
                term *= (k - 1) / k * c * c
                total += term
            a += 2 / math.pi * s * total
    else:
        term, total = 1.0, 1.0
        for k in range(2, df - 1, 2):
            term *= (k - 1) / k * c * c
            total += term
        a = s * total
    return 0.5 + 0.5 * a if x >= 0 else 0.5 - 0.5 * a


def z_score(s, k, sigma_h, hours):
    sd = sigma_h * math.sqrt(hours)
    return (math.log(s / k) - 0.5 * sd * sd) / sd


def ewma_table(series, half_life):
    """Sorted bar keys and the EWMA variance of hourly log returns after each bar (mean zero, RiskMetrics style).
    Gaps are skipped, never filled. Seeded with the mean square of the first 168 returns."""
    lam = 0.5 ** (1 / half_life)
    keys = sorted(series)
    rets = [(k, math.log(series[k] / series[k - 3600])) for k in keys if k - 3600 in series]
    if len(rets) < 168:
        return [], []
    var = sum(r * r for _, r in rets[:168]) / 168
    out_k, out_v = [], []
    for k, r in rets:
        var = lam * var + (1 - lam) * r * r
        out_k.append(k)
        out_v.append(var)
    return out_k, out_v


def ewma_vol_at(table, t):
    """Hourly vol from returns up to the bar ending at t (the same last return as v1's vol_at)."""
    keys, vals = table
    i = bisect.bisect_right(keys, t - 3600) - 1
    if i < 0 or keys[i] < t - 3600 * 24:  # more than a day stale: unusable
        return None
    return math.sqrt(vals[i])


def fit_logistic(xs, ys, ridge=RIDGE, iters=60):
    """Maximum likelihood weights for p = sigmoid(w . x) (x includes its own constant), Newton steps with a small
    ridge so separable data cannot diverge."""
    n = len(xs[0])
    w = [0.0] * n
    for _ in range(iters):
        g = [ridge * wi for wi in w]
        h = [[ridge if i == j else 0.0 for j in range(n)] for i in range(n)]
        for x, y in zip(xs, ys):
            p = sigmoid(sum(a * b for a, b in zip(w, x)))
            for i in range(n):
                g[i] += (p - y) * x[i]
                for j in range(n):
                    h[i][j] += p * (1 - p) * x[i] * x[j]
        step = solve(h, g)
        w = [a - b for a, b in zip(w, step)]
        if max(abs(v) for v in step) < 1e-9:
            break
    return w


def solve(a, b):
    """Gaussian elimination with partial pivoting for the small Newton systems."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-300:
            raise ValueError('singular system')
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


# ------------------------------------------------------------------ candidates
# Each candidate: fit(train records) -> params; predict(params, record) -> probability. Records carry only inputs
# known at the snapshot (spot, threshold, hours, vols, crowd price) plus y and day for scoring.

def _brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def _grid(values, predict):
    def fit(train):
        ys = [r['y'] for r in train]
        return min(values, key=lambda v: (_brier([predict(v, r) for r in train], ys), values.index(v)))
    return fit


def p_v1(_, r):
    return model_p(r['s'], r['k'], r['vol720'], r['hours'])


def p_ewma(hl, r):
    return model_p(r['s'], r['k'], r['ewma'][str(hl)], r['hours'])


def p_student(df, r):
    return t_cdf(z_score(r['s'], r['k'], r['vol720'], r['hours']) * math.sqrt(df / (df - 2)), df)


def x_recal(r):
    return [1.0, logit(p_v1(None, r))]


def x_crowd_plus(r):
    return [1.0, logit(r['crowd']), logit(p_v1(None, r))]


CANDIDATES = {
    'v1': (lambda train: None, p_v1),
    'ewma': (_grid(list(HALF_LIVES), p_ewma), p_ewma),
    'student_t': (_grid(list(DFS), p_student), p_student),
    'recal': (lambda train: fit_logistic([x_recal(r) for r in train], [r['y'] for r in train]),
              lambda w, r: sigmoid(sum(a * b for a, b in zip(w, x_recal(r))))),
    'crowd_plus': (lambda train: fit_logistic([x_crowd_plus(r) for r in train], [r['y'] for r in train]),
                   lambda w, r: sigmoid(sum(a * b for a, b in zip(w, x_crowd_plus(r))))),
}


def month_cv(name, dev):
    """Out-of-month predictions on the first half: each month is predicted by a fit on the other months."""
    fit, predict = CANDIDATES[name]
    out = {}
    for month in sorted({r['day'][:7] for r in dev}):
        train = [r for r in dev if r['day'][:7] != month]
        params = fit(train)
        for r in dev:
            if r['day'][:7] == month:
                out[r['id']] = predict(params, r)
    return [out[r['id']] for r in dev]


def select(dev):
    """(chosen baseline, its dev scores). Only first-half records are passed in; ties go to the earlier name."""
    ys = [r['y'] for r in dev]
    scores = {name: _brier(month_cv(name, dev), ys) for name in BASELINES + ('crowd_plus',)}
    scores['crowd'] = _brier([r['crowd'] for r in dev], ys)
    chosen = min(BASELINES, key=lambda n: (scores[n], BASELINES.index(n)))
    return chosen, scores


# ------------------------------------------------------------------ scoring

def auc(ps, ys):
    pos, neg = [p for p, y in zip(ps, ys) if y], [p for p, y in zip(ps, ys) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def log_loss(ps, ys):
    return -sum(math.log(clip(p)) if y else math.log(1 - clip(p)) for p, y in zip(ps, ys)) / len(ys)


def boot_diff(recs, a, b):
    """95% interval of mean Brier(a) - Brier(b), resampling whole days (thresholds on one day move together)."""
    by = defaultdict(lambda: [0.0, 0])
    for r in recs:
        cell = by[r['day']]
        cell[0] += (r[a] - r['y']) ** 2 - (r[b] - r['y']) ** 2
        cell[1] += 1
    days = sorted(by)
    rng, stats = random.Random(SEED), []
    for _ in range(BOOT):
        s = n = 0
        for _ in days:
            x, m = by[days[rng.randrange(len(days))]]
            s += x
            n += m
        stats.append(s / n)
    stats.sort()
    return [stats[int(0.025 * BOOT)], stats[int(0.975 * BOOT) - 1]]


def score(recs, arms):
    ys = [r['y'] for r in recs]
    out = {'n': len(recs), 'days': len({r['day'] for r in recs}), 'yes_rate': sum(ys) / len(ys) if ys else None}
    for arm in arms:
        ps = [r[arm] for r in recs]
        out[arm] = {'brier': _brier(ps, ys), 'log_loss': log_loss(ps, ys), 'auc': auc(ps, ys)}
    for arm in arms:
        if arm != 'crowd':
            out[arm]['brier_minus_crowd'] = out[arm]['brier'] - out['crowd']['brier']
            out[arm]['ci95_minus_crowd'] = boot_diff(recs, arm, 'crowd')
        if arm not in ('crowd', 'v1'):
            out[arm]['brier_minus_v1'] = out[arm]['brier'] - out['v1']['brier']
            out[arm]['ci95_minus_v1'] = boot_diff(recs, arm, 'v1')
    return out


# ------------------------------------------------------------------ records

def records(source, hours, label):
    """Contested markets with a crowd price, spot and 720 h vol at the snapshot, as in the v1 comparison. The
    contested rule uses the crowd and v1 only, so no candidate can change which markets are scored."""
    source = Path(source)
    sample = {r['id']: r for r in lines(source / 'sample.jsonl')}
    crowd = {}
    for f in crowd_files(source):
        for r in lines(f):
            if r['id'] in sample:
                crowd.setdefault(r['id'], r)
    series = hourly(source)
    tables = {hl: ewma_table(series, hl) for hl in HALF_LIVES}
    out, missing = [], defaultdict(int)
    for mid, r in sorted(sample.items()):
        c = ((crowd.get(mid) or {}).get('crowd') or {}).get(label)
        t = r['close_ts'] - hours * 3600
        s, sig = spot_at(series, t), vol_at(series, t)
        if c is None:
            missing['no_crowd_price'] += 1
            continue
        if not s or not sig:
            missing['no_spot_or_vol'] += 1
            continue
        ew = {str(hl): ewma_vol_at(tables[hl], t) for hl in HALF_LIVES}
        if any(v is None or v <= 0 for v in ew.values()):
            missing['no_ewma'] += 1
            continue
        rec = {'id': mid, 'day': r['day'], 'y': int(bool(r['y'])), 'crowd': c, 's': s, 'k': r['threshold'],
               'hours': hours, 'vol720': sig, 'ewma': ew}
        v1 = p_v1(None, rec)
        if CONTESTED[0] <= c <= CONTESTED[1] or CONTESTED[0] <= v1 <= CONTESTED[1]:
            out.append(rec)
        else:
            missing['not_contested'] += 1
    return out, dict(missing)


def source_hashes(source):
    source = Path(source)
    files = ['sample.jsonl', 'btc_1h.json'] + [f.name for f in crowd_files(source)]
    return {name: digest((source / name).read_bytes()) for name in files}


# ------------------------------------------------------------------ protocol and run

def protocol(hashes):
    return {
        'format': FORMAT, 'warning': WARNING,
        'question': 'Can a Python-only probability from price, volatility and horizon match or beat the Polymarket crowd '
                    'on daily Bitcoin threshold markets, and does it add anything on top of the crowd?',
        'source': {'root': SOURCE, 'sha256': hashes,
                   'disclosure': 'The v1 crowd comparison already reported the crowd, the untuned 720 h model and '
                                 'their average on both halves. No fitted candidate here was scored on the second '
                                 'half before this run.'},
        'markets': 'contested at the snapshot: crowd or the v1 model between 0.05 and 0.95; same filters as v1',
        'split': {'first_half': f'day before {SPLIT_DAY} (choose and fit)', 'second_half': f'day on or after {SPLIT_DAY} (score once)'},
        'horizons_hours': HORIZONS, 'primary': PRIMARY,
        'candidates': {
            'v1': 'driftless lognormal, vol = stdev of the previous 720 hourly log returns (no fit)',
            'ewma': f'driftless lognormal, EWMA hourly variance; half-life chosen from {list(HALF_LIVES)} hours',
            'student_t': f'unit-variance Student-t on the v1 z-score; df chosen from {list(DFS)}',
            'recal': 'logistic recalibration: sigmoid(a + b logit(v1))',
            'crowd_plus': 'feature test, not a baseline: sigmoid(a + b logit(crowd) + c logit(v1))'},
        'fit': f'grid by Brier, logistic by maximum likelihood (ridge {RIDGE}); probabilities clipped to [{CLIP}, {1 - CLIP}]',
        'selection': 'first half only: leave-one-month-out Brier; the lowest of v1, ewma, student_t and recal is the '
                     'baseline (ties to the earlier name); then it and crowd_plus are fit on the whole first half',
        'metrics': 'Brier (primary), log loss, ranking AUC; differences with a 95% bootstrap interval resampling '
                   f'whole days ({BOOT} draws, seed {SEED})',
        'claims': {
            'baseline_beats_v1': 'second half, primary horizon: Brier(baseline) - Brier(v1) interval entirely below 0',
            'baseline_beats_crowd': 'second half, primary horizon: Brier(baseline) - Brier(crowd) interval entirely below 0',
            'model_adds_to_crowd': 'second half, primary horizon: Brier(crowd_plus) - Brier(crowd) interval entirely below 0'},
        'secondary': 'the 6 h horizon is reported with the same procedure; no claim',
        'one_look': 'the second half is scored once; results.json blocks a rerun',
        'scope': 'Bitcoin daily thresholds only. Python only: no model output, no news.'}


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


def evaluate(recs):
    """Choose and fit on the first half, then predict the second half once."""
    dev = [r for r in recs if r['day'] < SPLIT_DAY]
    test = [r for r in recs if r['day'] >= SPLIT_DAY]
    if not dev or not test:
        raise ValueError('Both halves need records')
    chosen, dev_scores = select(dev)
    params = {name: CANDIDATES[name][0](dev) for name in ('v1', chosen, 'crowd_plus')}
    scored = []
    for r in test:
        row = {'id': r['id'], 'day': r['day'], 'y': r['y'], 'crowd': r['crowd']}
        for name in ('v1', chosen, 'crowd_plus'):
            row['baseline' if name == chosen else name] = CANDIDATES[name][1](params[name], r)
        if chosen == 'v1':
            row['v1'] = row['baseline']
        scored.append(row)
    test_scores = score(scored, ('crowd', 'v1', 'baseline', 'crowd_plus'))
    claims = {'baseline_beats_v1': chosen != 'v1' and test_scores['baseline']['ci95_minus_v1'][1] < 0,
              'baseline_beats_crowd': test_scores['baseline']['ci95_minus_crowd'][1] < 0,
              'model_adds_to_crowd': test_scores['crowd_plus']['ci95_minus_crowd'][1] < 0}
    return {'first_half': {'n': len(dev), 'days': len({r['day'] for r in dev}), 'cv_brier': dev_scores},
            'chosen': chosen, 'params': {k: v for k, v in params.items() if v is not None},
            'second_half': test_scores, 'claims': claims}, scored


def run(root, source=SOURCE, publish=None, clock=None):
    root = Path(root)
    path = root / 'protocol.json'
    p = protocol(source_hashes(source))
    if not path.exists() or json.loads(path.read_text(encoding='utf-8')) != p:
        raise ValueError('Freeze this protocol first; a changed protocol or source needs a new --root')
    if (root / 'results.json').exists():
        raise ValueError('Already run: the second half is scored once')
    report = {'format': FORMAT, 'warning': WARNING, 'protocol_sha256': digest(path.read_bytes()),
              'code_sha256': digest(Path(__file__).read_bytes()), 'horizons': {}}
    saved = {}
    for label, hours in HORIZONS.items():
        recs, missing = records(source, hours, label)
        result, scored = evaluate(recs)
        report['horizons'][label] = {'excluded': missing, **result}
        saved[label] = scored
    report['claims'] = report['horizons'][PRIMARY]['claims']
    report['run_at_utc'] = (clock or (lambda: datetime.now(timezone.utc)))().isoformat()
    dump(root / 'results.json', saved)
    dump(root / 'report.json', report)
    if publish:
        dump(Path(publish), report)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=['freeze', 'run'])
    ap.add_argument('--root', type=Path, default=Path('data/btc_baseline_v1'))
    ap.add_argument('--publish', type=Path)
    a = ap.parse_args()
    if a.action == 'freeze':
        print(freeze(a.root, protocol(source_hashes(SOURCE)), a.publish))
        return
    out = run(a.root, publish=a.publish)
    for label, h in out['horizons'].items():
        s = h['second_half']
        print(label, 'chosen', h['chosen'], 'params', h['params'], 'n', s['n'], 'days', s['days'])
        for arm in ('crowd', 'v1', 'baseline', 'crowd_plus'):
            print('  ', arm, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s[arm].items()})
    print('claims', out['claims'])


if __name__ == '__main__':
    main()
