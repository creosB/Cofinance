"""Bitcoin: a simple volatility model vs the Polymarket crowd. EXPERIMENTAL, read-only, Cofinance never trades.

Markets: Polymarket daily "Bitcoin above ___ on <date>?" thresholds. They settle on the Binance BTC/USDT 1-minute
close at 12:00 ET. For each market, 24 h (primary) and 6 h (secondary) before that noon:
  crowd  recorded CLOB price of "Yes": last point at or before the snapshot, at most 1 h old
  model  driftless lognormal P(S_T > K): spot = Yahoo BTC-USD hourly close at the snapshot,
         volatility = stdev of the previous 720 hourly log returns, scaled by sqrt(hours to close)
  blend  the average of crowd and model
Nothing is fitted. The model and the claim rule are frozen in protocol.json before any price is downloaded.

python -m qualify.btc_crowd collect  --root data/btc_crowd_20261110
python -m qualify.btc_crowd history  --root data/btc_crowd_20261110 [--shard i --shards n]
python -m qualify.btc_crowd spot     --root data/btc_crowd_20261110
python -m qualify.btc_crowd evaluate --root data/btc_crowd_20261110
"""
from __future__ import annotations
import argparse
import json
import math
import random
import re
import statistics
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from core.polymarket import GAMMA, fetch, parse_market, price_history, resolved_yes
from .historical import digest, dump, lines, write_lines

FORMAT = 'cofinance-btc-crowd/1'
WARNING = 'Experimental. Read-only comparison with recorded crowd prices. Not a trading signal or financial advice.'
FIRST_DAY, LAST_DAY = date(2025, 11, 1), date(2026, 11, 8)
HORIZONS = {'24h': 24, '6h': 6}
PRIMARY = '24h'
VOL_HOURS = 720
MAX_STALE_S = 3600
MIN_MARKET_VOLUME = 5000
CONTESTED = (0.05, 0.95)
SPLIT_DAY = '2026-05-01'
BOOT, SEED = 2000, 20261110
CLIP = 0.001
CLAIM = ('An arm beats the crowd only if, at 24h on contested markets, the 95% date-clustered bootstrap interval of '
         '(arm Brier minus crowd Brier) is entirely below zero AND the point difference is below zero in both halves.')
MODEL = ('Driftless lognormal. p = Phi((ln(S/K) - s^2/2) / s), s = stdev(720 previous hourly log returns) * sqrt(hours). '
         'S = Yahoo BTC-USD close of the hour ending at the snapshot.')
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october',
          'november', 'december']
YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD'


def _nth_sunday(y, m, n):
    first = date(y, m, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def noon_et(d):
    """12:00 America/New_York as UTC. US daylight time runs from the second Sunday of March to the first Sunday
    of November (both switches happen at 02:00, before noon)."""
    dst = _nth_sunday(d.year, 3, 2) <= d < _nth_sunday(d.year, 11, 1)
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc) + timedelta(hours=4 if dst else 5)


def threshold(raw):
    """Dollar threshold from the group label ("54,000", "110k") or the question ("... above $54,000 on ...")."""
    for text, pattern in ((raw.get('groupItemTitle'), r'(\d[\d,]*(?:\.\d+)?)\s*([kK])?'),
                          (raw.get('question'), r'\$\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?')):
        m = re.search(pattern, str(text or ''))
        if not m:
            continue
        try:
            k = float(m.group(1).replace(',', '')) * (1000 if m.group(2) else 1)
        except ValueError:
            continue
        if 1000 <= k <= 10_000_000:
            return k
    return None


def retry(fn, tries=4):
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def event_for(d, getter=fetch):
    """The day's "Bitcoin above ___ on <date>?" event, or None. Slugs carry the year on newer events."""
    name = f'bitcoin-above-on-{MONTHS[d.month - 1]}-{d.day}'
    for slug in (f'{name}-{d.year}', name):
        found = getter(GAMMA + '/events?' + urlencode({'slug': slug}))
        for e in found if isinstance(found, list) else []:
            if isinstance(e, dict) and str(e.get('endDate') or '')[:10] == d.isoformat():
                return e
    return None


def collect(root, getter=fetch, pause=0.1):
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'protocol.json').exists():
        raise ValueError('Frozen sample exists: use a new root')
    rows, counts, days_with_event, d = [], defaultdict(int), 0, FIRST_DAY
    while d <= LAST_DAY:
        try:
            e, failed = retry(lambda: event_for(d, getter)), False
        except Exception:
            e, failed = None, True
        if not e:
            counts['day_fetch_error' if failed else 'day_without_event'] += 1
        else:
            days_with_event += 1
            for raw in e.get('markets') or []:
                m, k = parse_market(raw), threshold(raw if isinstance(raw, dict) else {})
                if not m or not m['yes_no']:
                    counts['not_yes_no'] += 1
                elif k is None:
                    counts['no_threshold'] += 1
                elif resolved_yes(m) is None:
                    counts['not_cleanly_resolved'] += 1
                elif m['volume'] < MIN_MARKET_VOLUME:
                    counts['low_volume'] += 1
                else:
                    rows.append({'id': m['id'], 'day': d.isoformat(), 'event_id': str(e.get('id') or ''), 'threshold': k,
                                 'yes_token': m['yes_token'], 'y': int(resolved_yes(m)), 'volume': round(m['volume'], 2),
                                 'close_ts': int(noon_et(d).timestamp())})
        d += timedelta(days=1)
        time.sleep(pause)
    write_lines(root / 'sample.jsonl', rows)
    protocol = {'format': FORMAT, 'warning': WARNING, 'frozen_at': datetime.now(timezone.utc).isoformat(),
                'window': [FIRST_DAY.isoformat(), LAST_DAY.isoformat()], 'horizons_hours': HORIZONS, 'primary': PRIMARY,
                'model': MODEL, 'vol_hours': VOL_HOURS, 'max_stale_s': MAX_STALE_S, 'min_market_volume_usd': MIN_MARKET_VOLUME,
                'contested': CONTESTED, 'split_day': SPLIT_DAY, 'bootstrap': {'resamples': BOOT, 'seed': SEED, 'cluster': 'day'},
                'claim': CLAIM, 'resolution': 'Polymarket settlement (Binance BTC/USDT 1-minute close at 12:00 ET)',
                'spot_source': 'Yahoo BTC-USD hourly closes, a proxy for Binance BTC/USDT; agreement with settlement is reported',
                'markets': len(rows), 'days_with_event': days_with_event, 'excluded': dict(counts),
                'sample_sha256': digest((root / 'sample.jsonl').read_bytes())}
    dump(root / 'protocol.json', protocol)
    return protocol


def price_at(points, t):
    """Last recorded price at or before t, if it is at most MAX_STALE_S old."""
    best = None
    for ts, p in points:
        if ts <= t:
            best = (ts, p)
        else:
            break
    return best[1] if best and t - best[0] <= MAX_STALE_S else None


def crowd_files(root):
    return sorted(root.glob('crowd*.jsonl'))


def history(root, shard=0, shards=1, fetcher=price_history, pause=0.05):
    rows = [r for i, r in enumerate(lines(root / 'sample.jsonl')) if i % shards == shard]
    out = root / ('crowd.jsonl' if shards == 1 else f'crowd.{shard}.jsonl')
    done = {r['id'] for r in lines(out)} if out.exists() else set()
    errors = 0
    with out.open('a', encoding='utf-8') as fh:
        for r in rows:
            if r['id'] in done:
                continue
            try:
                pts, err = retry(lambda: fetcher(r['yes_token'], r['close_ts'] - 27 * 3600, r['close_ts'] - 5 * 3600, 10)), None
            except Exception as e:
                pts, err = [], type(e).__name__
                errors += 1
            snap = {h: price_at(pts, r['close_ts'] - hrs * 3600) for h, hrs in HORIZONS.items()}
            fh.write(json.dumps({'id': r['id'], 'crowd': snap, 'points': len(pts), 'error': err}) + '\n')
            fh.flush()
            time.sleep(pause)
    status = {'shard': shard, 'of': len(rows), 'errors': errors, 'finished': True}
    dump(root / ('history_status.json' if shards == 1 else f'history_status.{shard}.json'), status)
    return status


def spot(root, getter=None):
    if getter is None:
        from core.market import fetch_json as getter
    start = int(datetime.combine(FIRST_DAY - timedelta(days=32), datetime.min.time(), timezone.utc).timestamp())
    end = int(datetime.combine(LAST_DAY + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp())
    raw = getter(f'{YAHOO}?period1={start}&period2={end}&interval=1h')
    if not (raw.get('chart') or {}).get('result'):
        raise ValueError('Missing BTC-USD hourly result')
    (root / 'btc_1h.json').write_text(json.dumps(raw), encoding='utf-8')
    receipt = {'downloaded_at': datetime.now(timezone.utc).isoformat(), 'bars': len(hourly(root)),
               'sha256': digest((root / 'btc_1h.json').read_bytes())}
    dump(root / 'spot.json', receipt)
    return receipt


def hourly(root):
    """{bar start unix seconds: close} for whole-hour bars only."""
    res = json.loads((root / 'btc_1h.json').read_text(encoding='utf-8'))['chart']['result'][0]
    closes = res['indicators']['quote'][0]['close']
    return {int(t): float(c) for t, c in zip(res.get('timestamp') or [], closes)
            if c is not None and int(t) % 3600 == 0 and math.isfinite(float(c)) and c > 0}


def spot_at(series, t):
    return series.get(t - 3600)  # close of the hour bar that ends at t


def vol_at(series, t):
    """Hourly log-return stdev over the VOL_HOURS hours before t; None if more than 10% of returns are missing."""
    rets = []
    for i in range(VOL_HOURS):
        a, b = series.get(t - 3600 * (i + 2)), series.get(t - 3600 * (i + 1))
        if a and b:
            rets.append(math.log(b / a))
    return statistics.pstdev(rets) if len(rets) >= 0.9 * VOL_HOURS else None


def model_p(s, k, sigma_h, hours):
    sd = sigma_h * math.sqrt(hours)
    if sd <= 0:
        return 1.0 if s > k else 0.0
    z = (math.log(s / k) - 0.5 * sd * sd) / sd
    return 0.5 * math.erfc(-z / math.sqrt(2))


def _clip(p):
    return min(1 - CLIP, max(CLIP, p))


def boot_diff(recs, arm):
    """95% interval of mean(arm Brier - crowd Brier), resampling whole days (markets on one day move together)."""
    by = defaultdict(lambda: [0.0, 0])
    for r in recs:
        cell = by[r['day']]
        cell[0] += (r[arm] - r['y']) ** 2 - (r['crowd'] - r['y']) ** 2
        cell[1] += 1
    days = sorted(by)
    if len(days) < 2:
        return None
    rng, stats = random.Random(SEED), []
    for _ in range(BOOT):
        s = n = 0
        for _ in days:
            a, b = by[days[rng.randrange(len(days))]]
            s += a
            n += b
        stats.append(s / n)
    stats.sort()
    return [stats[int(0.025 * BOOT)], stats[int(0.975 * BOOT) - 1]]


def score(recs):
    if not recs:
        return {'n': 0}
    out = {'n': len(recs), 'days': len({r['day'] for r in recs}), 'yes_rate': statistics.mean(r['y'] for r in recs)}
    for arm in ('crowd', 'model', 'blend'):
        out[arm] = {'brier': statistics.mean((r[arm] - r['y']) ** 2 for r in recs),
                    'log_loss': statistics.mean(-math.log(_clip(r[arm]) if r['y'] else 1 - _clip(r[arm])) for r in recs)}
    for arm in ('model', 'blend'):
        out[arm]['brier_minus_crowd'] = out[arm]['brier'] - out['crowd']['brier']
        out[arm]['ci95_brier_minus_crowd'] = boot_diff(recs, arm)
    far = [r for r in recs if abs(r['model'] - r['crowd']) > 0.10]
    out['disagree_over_10pts'] = {'n': len(far), 'model_closer': sum(abs(r['model'] - r['y']) < abs(r['crowd'] - r['y']) for r in far)}
    return out


def evaluate(root):
    protocol = json.loads((root / 'protocol.json').read_text(encoding='utf-8'))
    if digest((root / 'sample.jsonl').read_bytes()) != protocol['sample_sha256']:
        raise ValueError('Sample changed after freezing')
    sample = {r['id']: r for r in lines(root / 'sample.jsonl')}
    crowd = {}
    for f in crowd_files(root):
        for r in lines(f):
            if r['id'] in sample:
                crowd.setdefault(r['id'], r)
    series = hourly(root)
    coverage = {'markets': len(sample), 'with_history': len(crowd),
                'history_errors': sum(1 for r in crowd.values() if r.get('error'))}
    agree = near = 0
    checked = 0
    for r in sample.values():
        s = spot_at(series, r['close_ts'])
        if s:
            checked += 1
            agree += int((s > r['threshold']) == bool(r['y']))
            near += int(abs(s / r['threshold'] - 1) < 0.002)
    report = {'format': FORMAT, 'warning': WARNING, 'protocol_sha256': digest((root / 'protocol.json').read_bytes()),
              'code_sha256': digest(Path(__file__).read_bytes()), 'claim_rule': CLAIM, 'coverage': coverage,
              'spot_proxy_check': {'markets': checked, 'yahoo_matches_settlement': agree / checked if checked else None,
                                   'within_0.2pct_of_threshold': near}, 'horizons': {}}
    for h, hrs in HORIZONS.items():
        recs, missing = [], defaultdict(int)
        for mid, r in sample.items():
            c = (crowd.get(mid) or {}).get('crowd', {}).get(h)
            t = r['close_ts'] - hrs * 3600
            s, sig = spot_at(series, t), vol_at(series, t)
            if c is None:
                missing['no_crowd_price'] += 1
                continue
            if not s or not sig:
                missing['no_spot_or_vol'] += 1
                continue
            m = model_p(s, r['threshold'], sig, hrs)
            recs.append({'day': r['day'], 'y': r['y'], 'crowd': c, 'model': m, 'blend': (c + m) / 2})
        contested = [x for x in recs if CONTESTED[0] <= x['crowd'] <= CONTESTED[1] or CONTESTED[0] <= x['model'] <= CONTESTED[1]]
        report['horizons'][h] = {'missing': dict(missing), 'all': score(recs), 'contested': score(contested),
                                 'by_half_contested': {'first': score([x for x in contested if x['day'] < SPLIT_DAY]),
                                                       'second': score([x for x in contested if x['day'] >= SPLIT_DAY])}}
    p = report['horizons'][PRIMARY]
    report['claims'] = {}
    for arm in ('model', 'blend'):
        ci = (p['contested'].get(arm) or {}).get('ci95_brier_minus_crowd')
        halves = [(p['by_half_contested'][k].get(arm) or {}).get('brier_minus_crowd') for k in ('first', 'second')]
        report['claims'][arm + '_beats_crowd'] = bool(ci and ci[1] < 0 and all(x is not None and x < 0 for x in halves))
    dump(root / 'report.json', report)
    return report


def table(report):
    out = [f"coverage {report['coverage']}  spot proxy {report['spot_proxy_check']}"]
    for h, x in report['horizons'].items():
        out.append(f"\n{h} missing {x['missing']}")
        for name in ('all', 'contested'):
            s = x[name]
            if not s['n']:
                out.append(f'  {name}: n=0')
                continue
            out.append(f"  {name}: n={s['n']} days={s['days']} yes={s['yes_rate']:.3f} disagree>10pts {s['disagree_over_10pts']}")
            for arm in ('crowd', 'model', 'blend'):
                a = s[arm]
                extra = '' if arm == 'crowd' else f"  diff {a['brier_minus_crowd']:+.4f} ci {[round(v, 4) for v in a['ci95_brier_minus_crowd'] or []]}"
                out.append(f"    {arm:6s} brier {a['brier']:.4f} logloss {a['log_loss']:.4f}{extra}")
        for half, s in x['by_half_contested'].items():
            if s['n']:
                out.append(f"  contested {half}: n={s['n']} crowd {s['crowd']['brier']:.4f} model {s['model']['brier']:.4f} blend {s['blend']['brier']:.4f}")
    out.append(f"\nclaims {report['claims']}")
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['collect', 'history', 'spot', 'evaluate'])
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--shard', type=int, default=0)
    ap.add_argument('--shards', type=int, default=1)
    a = ap.parse_args()
    if a.action == 'collect':
        p = collect(a.root)
        print(json.dumps({k: p[k] for k in ('markets', 'days_with_event', 'excluded')}, indent=2))
    elif a.action == 'history':
        print(json.dumps(history(a.root, a.shard, a.shards)))
    elif a.action == 'spot':
        print(json.dumps(spot(a.root)))
    else:
        print(table(evaluate(a.root)))


if __name__ == '__main__':
    main()
