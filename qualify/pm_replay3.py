"""Polymarket replay v3, forward-recorded. EXPERIMENTAL: not a trading signal or financial advice. Cofinance never trades.

Same question, arms, prompts and readout as v2 (qualify/pm_replay.py, left untouched so its sealed record keeps its own
rules). v2's general market listing reached about one usable market per record. v3 lists Gamma events by tag, which
reaches the daily and weekly price markets of our assets, and accounts for their correlation in the verdict.

    python -m qualify.pm_replay3 freeze --root data/pm_replay3 --publish docs/benchmarks/pm-replay-protocol-v3.json
    python -m qualify.pm_replay3 record --root data/pm_replay3 --publish docs/benchmarks/pm-replay-ledger-v3.jsonl
    python -m qualify.pm_replay3 score  --root data/pm_replay3
"""
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from . import pm_replay as V2
from .historical import digest, dump
from .pm_replay import (ALIASES, ARMS, BOOTSTRAP, END_HOURS, MAX_FAILURE_RATE, MAX_MARKETS, MIN_EVENTS, MIN_GAP_DAYS,
                        PRICE_BAND, SEED, SETTLE_DAYS, SYSTEM_BLIND, SYSTEM_SHOWN, WARNING, asset_of, auc, daily_bars,
                        first_token_logprobs, ledger, outcome, price_facts, prompt_for, readout, when)

FORMAT = 'cofinance-pm-replay/3'
TAGS = ('bitcoin', 'ethereum', 'stocks')   # Gamma event tags that reach the five assets (probed 2026-11-18)
PAGE, MAX_OFFSET = 20, 1000                # pages above 50 rows are refused; offset cap per tag
MIN_EVENT_VOLUME = 10_000                  # event lifetime USD volume
MIN_CLUSTERS = 20                          # (record, asset) clusters: same-asset events in one record share a price path
SUPERSEDES = {'format': 'cofinance-pm-replay/2', 'sha256': 'f4e924ab4990835b59b7651b7141ff7c5cc8b9dab8e7b7628ee6851b523abb53',
              'reason': 'the general market listing reached about one usable market per record (record 1: 1 of 99); '
                        'v2 keeps its own root, rules and record'}


def protocol(narrator_id, narrator_sha):
    p = V2.protocol(narrator_id, narrator_sha)
    p.update(format=FORMAT, supersedes=SUPERSEDES)
    p['selection'] = {
        'source': f'Gamma events endpoint, read-only, at record time T: tags {list(TAGS)}, active, not closed, end 1 to 8 '
                  f'days after T, pages of {PAGE}, at most offset {MAX_OFFSET} per tag (truncation recorded), deduplicated '
                  'by event id',
        'asset': 'the event title names exactly one tracked asset by a whole-word alias; zero or two assets: dropped',
        'not_sports': 'events whose tags include sports are dropped',
        'min_event_volume_usd': MIN_EVENT_VOLUME,
        'market': 'an active, not closed market of that event with current prices, scheduled end within the window and '
                  f'first-outcome price in {list(PRICE_BAND)}',
        'one_per_event': 'the market whose first-outcome price is closest to 0.5 at T; ties: lowest market id',
        'cap': f'at most {MAX_MARKETS}; above that a seeded sample (seed {SEED} + record day ordinal) of the id-sorted list',
        'cadence': f'records at least {MIN_GAP_DAYS} days apart; an event already recorded is never recorded again'}
    p['metrics']['primary'] = ('paired Brier difference team_blind minus crowd, one market per event; 95% percentile '
                               f'interval from {BOOTSTRAP} bootstrap resamples of (record, asset) clusters (seed {SEED})')
    p['verdict'] = {**p['verdict'], 'min_clusters': MIN_CLUSTERS,
                    'team_beats_crowd': 'team_blind arm not void AND at least min_events scored AND at least min_clusters '
                                        'AND mean difference below 0 AND cluster interval upper bound below 0'}
    return p


def freeze(root, narrator_id, narrator_sha, publish=None):
    root, p = Path(root), protocol(narrator_id, narrator_sha)
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


# ------------------------------------------------------------------ listing and selection

def open_events(now, getter):
    """Every event under the frozen tags ending in the window, deduplicated. (events, truncated tags)"""
    stamp = lambda t: t.strftime('%Y-%m-%dT%H:%M:%SZ')
    base = {'active': 'true', 'closed': 'false', 'end_date_min': stamp(now + timedelta(hours=END_HOURS[0])),
            'end_date_max': stamp(now + timedelta(hours=END_HOURS[1])), 'limit': PAGE}
    seen, truncated = {}, []
    for tag in TAGS:
        offset = 0
        while True:
            page = getter({**base, 'tag_slug': tag, 'offset': offset})
            for e in page:
                seen.setdefault(str(e.get('id')), e)
            offset += len(page)
            if len(page) < PAGE:
                break
            if offset >= MAX_OFFSET:
                truncated.append(tag)
                break
    return list(seen.values()), truncated


def select(events, now, seen_events=()):
    from core.polymarket import parse_market
    rows = []
    for e in events:
        event, title = str(e.get('id') or ''), str(e.get('title') or '')
        tags = e.get('tags') if isinstance(e.get('tags'), list) else []
        slugs = {str(t.get('slug') or '').lower() for t in tags if isinstance(t, dict)}
        if not event or event in seen_events or 'sports' in slugs:
            continue
        try:
            volume = float(e.get('volume') or 0)
        except (TypeError, ValueError):
            volume = 0.0
        asset = asset_of(title)
        if volume < MIN_EVENT_VOLUME or asset is None:
            continue
        best = None
        for raw in e.get('markets') or []:
            m = parse_market({**raw, 'events': [{'id': event, 'title': title, 'tags': tags}]}) if isinstance(raw, dict) else None
            if not m or m['closed'] or not m['active'] or not m['prices'] or not m['id'].isdigit():
                continue
            end = when(m['end'] or e.get('endDate'))
            hours = (end - now).total_seconds() / 3600 if end else None
            p = m['prices'][0]
            if hours is None or not END_HOURS[0] <= hours <= END_HOURS[1] or not PRICE_BAND[0] <= p <= PRICE_BAND[1]:
                continue
            if best is None or (abs(p - .5), int(m['id'])) < (abs(best['crowd'] - .5), int(best['id'])):
                best = {**m, 'end': m['end'] or e.get('endDate'), 'asset': asset, 'crowd': p, 'event': event,
                        'event_title': title[:300], 'event_volume': volume}
        if best:
            rows.append(best)
    rows.sort(key=lambda r: int(r['id']))
    if len(rows) > MAX_MARKETS:
        rows = sorted(random.Random(SEED + now.date().toordinal()).sample(rows, MAX_MARKETS), key=lambda r: int(r['id']))
    return rows


# ------------------------------------------------------------------ verdict with clusters

def paired(rows, arm):
    clusters = {}
    for r in rows:
        clusters.setdefault(r['cluster'], []).append((r[arm] - r['y']) ** 2 - (r['crowd'] - r['y']) ** 2)
    groups, rng, boots = list(clusters.values()), random.Random(SEED), []
    for _ in range(BOOTSTRAP):
        pick = [rng.choice(groups) for _ in groups]
        flat = [d for g in pick for d in g]
        boots.append(sum(flat) / len(flat))
    boots.sort()
    diffs = [d for g in groups for d in g]
    return {'mean': sum(diffs) / len(diffs), 'clusters': len(groups),
            'interval_95': [boots[int(.025 * BOOTSTRAP)], boots[int(.975 * BOOTSTRAP) - 1]]}


def score(rows):
    """rows: one per event with y, crowd, team_blind, team_shown, failed_blind, failed_shown and cluster."""
    rows = [{**r, 'coin': .5, 'y': float(bool(r['y']))} for r in rows]
    n = len(rows)
    report = {'format': FORMAT, 'warning': WARNING, 'events': n, 'clusters': len({r['cluster'] for r in rows})}
    if not n:
        return {**report, 'verdict': 'not scored yet'}
    ys = [r['y'] for r in rows]
    report['brier'] = {a: sum((r[a] - r['y']) ** 2 for r in rows) / n for a in ARMS}
    report['auc'] = {a: auc([r[a] for r in rows], ys) for a in ARMS}
    failures = {a: sum(bool(r.get('failed_' + a[5:])) for r in rows) / n for a in ('team_blind', 'team_shown')}
    report['failure_rate'], report['void'] = failures, {a: f > MAX_FAILURE_RATE for a, f in failures.items()}
    report['vs_crowd'] = {a: paired(rows, a) for a in ('team_blind', 'team_shown')}
    report['echo_rate_shown'] = sum(abs(r['team_shown'] - r['crowd']) < .02 for r in rows) / n
    primary = report['vs_crowd']['team_blind']
    if n < MIN_EVENTS or report['clusters'] < MIN_CLUSTERS:
        report['verdict'] = 'not scored yet'
    elif not report['void']['team_blind'] and primary['mean'] < 0 and primary['interval_95'][1] < 0:
        report['verdict'] = 'team beats crowd'
    else:
        report['verdict'] = 'no edge demonstrated'
    return report


# ------------------------------------------------------------------ record and score

def gamma_events(params):
    from core.polymarket import GAMMA, fetch
    page = fetch(GAMMA + '/events?' + urlencode(params))
    time.sleep(.12)
    return page if isinstance(page, list) else []


def record(root, *, narrator, narrator_id, narrator_sha, clock=time.time, getter=gamma_events, prices=daily_bars,
           news_db=Path('data/news.db'), publish=None):
    from core.orchestrator import news_digest
    from .forward import last_collection, news_for
    root = Path(root)
    protocol_sha = freeze(root, narrator_id, narrator_sha)
    entries, prev = ledger(root)
    now = clock()
    now_dt = datetime.fromtimestamp(now, timezone.utc)
    if entries and now - entries[-1]['sealed_at_epoch'] < MIN_GAP_DAYS * 86400:
        raise ValueError(f'The last record is under {MIN_GAP_DAYS} days old')
    seen = {c['event'] for e in entries for c in e['cases']}
    events, truncated = open_events(now_dt, getter)
    chosen = select(events, now_dt, seen)
    if not chosen:
        raise ValueError('No open market met the frozen selection rules')
    facts = {a: price_facts(prices(a, now)) for a in sorted({m['asset'] for m in chosen})}
    cases = []
    for m in chosen:
        ticker = m['asset'].removesuffix('-USD')
        news = news_for(news_db, ticker, now)
        cases.append({'id': m['id'], 'event': m['event'], 'event_title': m['event_title'], 'event_volume': m['event_volume'],
                      'asset': m['asset'], 'question': m['question'], 'outcomes': m['outcomes'], 'end': m['end'],
                      'crowd': m['crowd'], 'prices': facts[m['asset']], 'news_rows': len(news),
                      'digest': news_digest({ticker: news}), 'errors': {}, 'top_logprobs': {}})
    with narrator() as (call, meta):
        if meta.get('model_sha256') != narrator_sha:
            raise ValueError('The narrator weights differ from the frozen protocol')
        for c in cases:
            for arm, system, shown in (('team_blind', SYSTEM_BLIND, False), ('team_shown', SYSTEM_SHOWN, True)):
                try:
                    tops = first_token_logprobs(call(system, prompt_for(c, shown), max_tokens=1, top_logprobs=20,
                                                     return_choice=True, allow_length=True, timeout=60))
                except Exception as exc:
                    tops, c['errors'][arm] = [], f'{type(exc).__name__}: {exc}'[:200]
                p, failed = readout(tops)
                c[arm], c['failed_' + arm.removeprefix('team_')] = round(p, 6), failed
                c['top_logprobs'][arm] = [[t, lp] for t, lp in tops]
    sealed = clock()
    if sealed < now:
        raise ValueError('Clock moved backwards')
    entry = {'format': FORMAT, 'record': len(entries) + 1, 'data_as_of_utc': now_dt.isoformat(),
             'sealed_at_utc': datetime.fromtimestamp(sealed, timezone.utc).isoformat(), 'sealed_at_epoch': sealed,
             'protocol_sha256': protocol_sha, 'model': meta, 'news_last_collection_utc': last_collection(news_db),
             'events_listed': len(events), 'truncated_tags': truncated,
             'events_sha256': digest(json.dumps(events, sort_keys=True).encode('utf-8')), 'cases': cases, 'prev_sha256': prev}
    line = json.dumps(entry, sort_keys=True, allow_nan=False).encode('utf-8')
    with (root / 'ledger.jsonl').open('ab') as fh:
        fh.write(line + b'\n')
    sha = digest(line)
    dump(root / 'head.json', {'records': entry['record'], 'sha256': sha})
    public = {'record': entry['record'], 'sealed_at_utc': entry['sealed_at_utc'], 'ledger_sha256': sha,
              'protocol_sha256': protocol_sha, 'warning': WARNING,
              'markets': [{k: c[k] for k in ('id', 'asset', 'end', 'crowd', 'team_blind', 'team_shown')} for c in cases]}
    if publish:
        Path(publish).parent.mkdir(parents=True, exist_ok=True)
        with Path(publish).open('a', encoding='utf-8', newline='\n') as fh:
            fh.write(json.dumps(public, sort_keys=True) + '\n')
    return public


def score_ledger(root, *, getter=None, clock=time.time):
    from core.polymarket import GAMMA, fetch, parse_market
    getter = getter or (lambda market_id: fetch(f'{GAMMA}/markets/{market_id}'))
    root = Path(root)
    entries, head = ledger(root)
    if not entries:
        raise ValueError('No records yet')
    protocol_sha = digest((root / 'protocol.json').read_bytes())
    if any(e['protocol_sha256'] != protocol_sha for e in entries):
        raise ValueError('Ledger protocol mismatch')
    now, rows, pending, excluded = clock(), [], [], []
    for e in entries:
        for c in e['cases']:
            try:
                y = outcome(parse_market(getter(c['id'])) or {}, c['end'])
            except Exception:
                y = None  # a failed lookup is retried on the next score, never counted as an outcome
            if y is None:
                end = when(c['end'])
                (pending if end and now < end.timestamp() + SETTLE_DAYS * 86400 else excluded).append(c['id'])
                continue
            rows.append({'id': c['id'], 'event': c['event'], 'cluster': f"{e['record']}:{c['asset']}", 'y': y,
                         'crowd': c['crowd'], 'team_blind': c['team_blind'], 'team_shown': c['team_shown'],
                         'failed_blind': c['failed_blind'], 'failed_shown': c['failed_shown']})
    report = {**score(rows), 'records': len(entries), 'ledger_head_sha256': head, 'protocol_sha256': protocol_sha,
              'pending': pending, 'excluded': excluded,
              'scored_at_utc': datetime.fromtimestamp(now, timezone.utc).isoformat()}
    dump(root / 'report.json', report)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=['freeze', 'record', 'score'])
    ap.add_argument('--root', type=Path, default=Path('data/pm_replay3'))
    ap.add_argument('--model-root', type=Path, default=Path('data/local_models'))
    ap.add_argument('--news-db', type=Path, default=Path('data/news.db'))
    ap.add_argument('--publish', type=Path, help='freeze: protocol copy; record: public ledger line')
    a = ap.parse_args()
    from core.local_models import DEFAULT_NARRATOR, LocalModels, narrator_spec
    sha = narrator_spec(DEFAULT_NARRATOR)['sha']
    if a.action == 'freeze':
        print(freeze(a.root, DEFAULT_NARRATOR, sha, a.publish))
    elif a.action == 'record':
        from .history_runtime import default_backend, narrator
        backend = default_backend(a.model_root)
        if not LocalModels(a.model_root, Path(__file__).resolve().parents[1]).qualified(backend, DEFAULT_NARRATOR):
            raise SystemExit(f'{DEFAULT_NARRATOR} is not qualified on {backend}; qualify it in Models first')
        out = record(a.root, narrator=lambda: narrator(a.model_root, a.root / 'model-runtime.server.log', alias='pm-replay3',
                                                       backend=backend, model_id=DEFAULT_NARRATOR),
                     narrator_id=DEFAULT_NARRATOR, narrator_sha=sha, news_db=a.news_db, publish=a.publish)
        print(json.dumps(out, indent=2))
    else:
        out = score_ledger(a.root)
        print(json.dumps({k: v for k, v in out.items() if k not in ('pending', 'excluded')}, indent=2))


if __name__ == '__main__':
    main()
