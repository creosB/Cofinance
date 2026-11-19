"""Bounded, auditable GDELT DOC news collection for research, never the live store.

No accuracy claim. seendate is an index timestamp, not original publication or a
proof of when today's headline text first existed. No publisher pages are fetched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .news_dataset import canonical_url, title_key

ENDPOINT = 'https://api.gdeltproject.org/api/v2/doc/doc'
FORMAT = 'cofinance-gdelt/1'
MAX_BYTES = 2 * 1024 * 1024
LIMIT = 250
QUERIES = {
    'NVDA': 'Nvidia', 'AAPL': '(Apple OR iPhone)', 'MSFT': 'Microsoft',
    'AMZN': '(Amazon OR AWS)', 'TSLA': 'Tesla', 'AMD': '(AMD OR "Advanced Micro Devices")',
    'GOOGL': '(Google OR Alphabet)', 'BTC': '(Bitcoin OR BTC)',
}
ALIASES = {
    'NVDA': r'\b(nvidia|nvda)\b', 'AAPL': r'\b(apple|iphone|aapl)\b',
    'MSFT': r'\b(microsoft|msft)\b', 'AMZN': r'\b(amazon|aws|amzn)\b',
    'TSLA': r'\b(tesla|tsla)\b', 'AMD': r'\b(amd|advanced micro devices)\b',
    'GOOGL': r'\b(google|alphabet|googl)\b', 'BTC': r'\b(bitcoin|btc)\b',
}
# A single run is immutable. Failed runs are terminal; corrected inputs use a new folder.
NEXT = {'empty': {'frozen': 'rules saved'}, 'frozen': {'collecting': 'collection starts'},
        'collecting': {'complete': 'all intervals resolved', 'incomplete': 'gap or request limit'},
        'complete': {}, 'incomplete': {}}


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2).encode()


def save_new(path, value):
    with path.open('xb') as f:
        f.write(encoded(value))


def stage(root):
    if (root / 'manifest.json').exists():
        return json.loads((root / 'manifest.json').read_text())['state']
    if (root / 'started.json').exists():
        return 'collecting'
    if (root / 'protocol.json').exists():
        return 'frozen'
    return 'empty'


def require_move(root, destination):
    if destination not in NEXT[stage(root)]:
        raise ValueError('Illegal collection move: ' + stage(root) + ' -> ' + destination)


def statechart():
    out = ['stateDiagram-v2', '    [*] --> empty']
    for before, afters in NEXT.items():
        for after, label in afters.items():
            out.append(f'    {before} --> {after}: {label}')
        if not afters:
            out.append(f'    {before} --> [*]')
    return '\n'.join(out)


def stamp(value):
    return int(datetime.strptime(value, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp())


def fmt(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y%m%d%H%M%S')


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def protocol(start, end, tickers, budget=96):
    a, b = stamp(start), stamp(end)
    if a >= b or b - a > 31 * 86400 or a % 900 or b % 900:
        raise ValueError('Use an increasing window of at most 31 days on 15-minute boundaries')
    if not tickers or len(set(tickers)) != len(tickers) or any(t not in QUERIES for t in tickers):
        raise ValueError('Unknown, empty or duplicate ticker set')
    if not 1 <= budget <= 512:
        raise ValueError('Request budget must be 1..512')
    return {'format': FORMAT, 'start': a, 'end_exclusive': b, 'tickers': list(tickers),
            'queries': {t: QUERIES[t] + ' sourcelang:english' for t in tickers},
            'aliases': {t: ALIASES[t] for t in tickers}, 'maxrecords': LIMIT, 'sort': 'datedesc',
            'initial_interval_seconds': 7 * 86400, 'minimum_interval_seconds': 900,
            'request_budget': budget, 'seconds_between_requests': 10, 'retries': 2,
            'retry_delays': [20, 60], 'timestamp_kind': 'gdelt_seen',
            'text_policy': 'API headline only; no current article body backdated into replay',
            'dedupe': 'earliest seen per ticker, canonical URL or normalized title',
            'capped_policy': 'bisect; unresolved caps or errors make collection incomplete',
            'code_sha256': sha(Path(__file__).read_bytes())}


def freeze(root, spec, now=None):
    now = time.time() if now is None else now
    if spec['end_exclusive'] > now:
        raise ValueError('Collection end is in the future')
    require_move(root, 'frozen')
    root.mkdir(parents=True, exist_ok=True)
    save_new(root / 'protocol.json', spec)
    return sha(encoded(spec))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('GDELT redirects are not followed')


def fetch(url):
    if not url.startswith(ENDPOINT + '?'):
        raise ValueError('Only the fixed GDELT endpoint can be fetched')
    op = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(url, headers={'User-Agent': 'Cofinance-research/0.1', 'Accept': 'application/json'})
    try:
        response = op.open(req, timeout=25)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError('GDELT response exceeds byte limit')
        return response.code, dict(response.headers), body


def parse_response(status, body):
    if status != 200:
        raise ValueError('HTTP ' + str(status))
    if len(body) > MAX_BYTES:
        raise ValueError('GDELT response exceeds byte limit')
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get('articles'), list):
        raise ValueError('Missing article list, not an empty successful response')
    if len(data['articles']) > LIMIT:
        raise ValueError('Response exceeds requested record cap')
    return data['articles']


def normalize(article, ticker, start, end, spec, snapshot, body_hash, observed):
    if not isinstance(article, dict):
        raise ValueError('invalid_article')
    try:
        dt = datetime.strptime(article['seendate'], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, KeyError):
        raise ValueError('missing_or_invalid_seen_time') from None
    at = int(dt.timestamp())
    if not start <= at < end:
        raise ValueError('outside_interval')
    title = article.get('title')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 2000:
        raise ValueError('invalid_title')
    if str(article.get('language', '')).lower() != 'english':
        raise ValueError('not_english')
    if not re.search(spec['aliases'][ticker], title, re.I):
        raise ValueError('no_asset_in_title')
    try:
        url = canonical_url(article.get('url', ''))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid_url') from None
    return {'ticker': ticker, 'title': title.strip(), 'text': '', 'url': url,
            'source': 'gdelt:' + str(article.get('domain') or ''), 'source_kind': 'news_index',
            'published_at': '', 'updated_at': '', 'timestamp': iso(at), 'timestamp_kind': 'gdelt_seen',
            'available_at': at, 'observed_at': observed, 'snapshot': snapshot, 'snapshot_sha256': body_hash,
            'id': sha(ticker + '|' + url + '|' + title_key(title))[:24]}


def dedupe(records):
    urls, titles, kept = set(), set(), []
    for row in sorted(records, key=lambda r: (r['available_at'], r['ticker'], r['url'], r['title'])):
        u, t = (row['ticker'], row['url']), (row['ticker'], title_key(row['title']))
        if u not in urls and t not in titles:
            kept.append(row)
        urls.add(u)
        titles.add(t)
    return kept


def collect(root, get=fetch, sleep=time.sleep, clock=time.time):
    require_move(root, 'collecting')
    spec = json.loads((root / 'protocol.json').read_bytes())
    if spec['code_sha256'] != sha(Path(__file__).read_bytes()):
        raise ValueError('Collector changed after freeze; start a new run')
    save_new(root / 'started.json', {'at': iso(clock())})
    (root / 'raw').mkdir()
    todo = [(t, a, min(a + spec['initial_interval_seconds'], spec['end_exclusive']))
            for a in range(spec['start'], spec['end_exclusive'], spec['initial_interval_seconds']) for t in spec['tickers']]
    records, receipts, gaps, rejected = [], [], [], Counter()
    requests, capped = 0, 0
    while todo:
        ticker, a, b = todo.pop(0)
        articles = None
        for attempt in range(spec['retries'] + 1):
            if requests >= spec['request_budget']:
                break
            if requests:
                sleep(spec['seconds_between_requests'] if attempt == 0 else spec['retry_delays'][attempt - 1])
            params = {'query': spec['queries'][ticker], 'mode': 'artlist', 'format': 'json',
                      'maxrecords': LIMIT, 'sort': spec['sort'], 'startdatetime': fmt(a), 'enddatetime': fmt(b)}
            url = ENDPOINT + '?' + urlencode(params)
            requests += 1
            p = root / 'raw' / f'{requests:04d}.json'
            rec = {'ticker': ticker, 'start': a, 'end_exclusive': b, 'url': url, 'at': iso(clock())}
            try:
                status, headers, body = get(url)
                rec.update(status=status, headers=headers, body=body.decode('utf-8', 'replace'), body_sha256=sha(body))
                articles = parse_response(status, body)
                rec['rows'] = len(articles)
            except (OSError, ValueError, TypeError) as exc:
                rec['error'] = f'{type(exc).__name__}: {exc}'[:400]
            save_new(p, rec)
            receipts.append({'path': str(p.relative_to(root)), 'sha256': sha(p.read_bytes())})
            if articles is not None:
                break
        if articles is None:
            gaps.append({'ticker': ticker, 'start': a, 'end': b, 'reason': 'request_failed_or_budget'})
            # A rate-limited source is not hammered across the remaining intervals.
            gaps.extend({'ticker': t, 'start': x, 'end': y, 'reason': 'not_attempted_after_failure'} for t, x, y in todo)
            break
        if len(articles) == LIMIT:
            capped += 1
            if b - a < 2 * spec['minimum_interval_seconds']:
                gaps.append({'ticker': ticker, 'start': a, 'end': b, 'reason': 'minimum_interval_capped'})
                continue
            mid = (a + (b - a) // 2) // 900 * 900
            todo[0:0] = [(ticker, a, mid), (ticker, mid, b)]
            continue
        for article in articles:
            try:
                records.append(normalize(article, ticker, a, b, spec, str(p.relative_to(root)), rec['body_sha256'], rec['at']))
            except ValueError as exc:
                rejected[str(exc)] += 1
        print(ticker, fmt(a), fmt(b), 'returned', len(articles), 'kept_total', len(records), flush=True)
    kept = dedupe(records)
    data = ''.join(json.dumps(r, sort_keys=True, ensure_ascii=True) + '\n' for r in kept).encode()
    with (root / 'candidates.jsonl').open('xb') as f:
        f.write(data)
    state = 'incomplete' if gaps else 'complete'
    require_move(root, state)
    result = {'format': FORMAT, 'state': state, 'protocol_sha256': sha((root / 'protocol.json').read_bytes()),
              'candidate_sha256': sha(data), 'receipts': receipts, 'requests': requests, 'capped_intervals': capped,
              'gaps': gaps, 'rejected': dict(rejected), 'before_dedupe': len(records), 'rows': len(kept),
              'duplicates_removed': len(records) - len(kept), 'by_ticker': dict(Counter(r['ticker'] for r in kept)),
              'by_source': dict(Counter(r['source'] for r in kept)), 'finished_at': iso(clock()),
              'warning': 'Not a complete web archive. Index coverage, changed titles and syndication remain limitations. No accuracy claim.'}
    save_new(root / 'manifest.json', result)
    return result


def load_complete(root):
    if stage(root) != 'complete':
        raise ValueError('Only a complete collection can enter evaluation')
    m = json.loads((root / 'manifest.json').read_bytes())
    for p, expected in [('protocol.json', m['protocol_sha256']), ('candidates.jsonl', m['candidate_sha256']),
                        *((r['path'], r['sha256']) for r in m['receipts'])]:
        path = (root / p).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path.read_bytes()) != expected:
            raise ValueError('Collection hash mismatch: ' + p)
    return [json.loads(line) for line in (root / 'candidates.jsonl').read_text().splitlines()]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['freeze', 'collect', 'verify'])
    ap.add_argument('--root', required=True)
    ap.add_argument('--start', default='20261020000000')
    ap.add_argument('--end', default='20261112000000')
    ap.add_argument('--tickers', nargs='+', default=list(QUERIES))
    ap.add_argument('--budget', type=int, default=96)
    a = ap.parse_args()
    root = Path(a.root).resolve()
    private = Path('data').resolve()
    if root == private or not root.is_relative_to(private):
        raise ValueError('Use a folder under ignored data/')
    if a.action == 'freeze':
        print(freeze(root, protocol(a.start, a.end, a.tickers, a.budget)))
    elif a.action == 'collect':
        result = collect(root)
        print(json.dumps({k: v for k, v in result.items() if k not in ('receipts', 'by_source')}, indent=2))
        return 0 if result['state'] == 'complete' else 2
    else:
        print('Verified rows:', len(load_complete(root)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
