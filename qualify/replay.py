"""Replay benchmark: run the real Cofinance team at a past date T, then score its call against what happened.

EXPERIMENTAL. Not a trading signal or financial advice. Cofinance never trades.

The models are local and offline. At each origin T the team's two tools answer only from data on or before T:
daily bars through T's close, and news that passed the live Clock 1 gate and was available before the end of T
(UTC). The team is not told the date; to it, this is the latest data. The router, Data Analyst, News Researcher,
Market Team Lead and every guard are the live classes, unchanged. The accepted answer then goes to one more
step, Decision, which replies UP, DOWN or ABSTAIN for the close five US sessions after the next open (same
target as the forward benchmark). Predictions are sealed before any outcome is loaded.

Window v1 is DEV, for improving the system (guidance, parallelism, structure): SPY sessions 2026-10-27 to
2026-11-06, the first sessions after LFM3's release (2026-10-27, training cutoff unpublished), whose exits
completed by 2026-11-13. Assets: the eight tickers with collected news. Daily origins overlap, so cases are
correlated. Claims need later unseen weeks replayed the same way, and the forward ledger.

Limits, stated in every report: news availability is the item's own publish, update or submit time (a day-level
time counts from the end of that day), not an archived first-seen time; the gate ran per item at that time, not
on Clock 1's real schedule; stocks get no fundamentals (no point-in-time values); prices are today's download.

python -m qualify.replay collect --root data/replay_20261116
python -m qualify.replay gate    --root data/replay_20261116
python -m qualify.replay run     --root data/replay_20261116 --variant v1
python -m qualify.replay score   --root data/replay_20261116 --variant v1
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .historical import digest, dump, lines

FORMAT = 'cofinance-replay/1'
ASSETS = ('NVDA', 'AAPL', 'MSFT', 'AMZN', 'TSLA', 'AMD', 'GOOGL', 'BTC')
CRYPTO = {'BTC'}
FIRST_ORIGIN, LAST_ORIGIN = '2026-10-27', '2026-11-06'
HORIZON = 5
NEWS_DAYS = 7
CORPORA = ('data/news_corpus_20261107', 'data/news_corpus_20261108', 'data/news_corpus_20261116')
FETCH_FROM = datetime(2014, 9, 1, tzinfo=timezone.utc)
FETCH_TO = datetime(2026, 11, 15, tzinfo=timezone.utc)
LABELS = {'UP', 'DOWN', 'ABSTAIN'}
ARMS = ('system', 'news_rule', 'momentum20', 'always_up')
WARNING = 'Experimental replay benchmark on a dev window. Not a trading signal or financial advice.'

QUERY = 'Thesis review for {t}: what do the price data and the latest news say for the coming week, and what is the main risk?'
VARIANTS = {
    'v1': {
        'decision_system': (
            'You are [Decision], the last step of a research team. You get the team\'s accepted answer and its two '
            'specialist reports about one asset, based on the latest market close. Decide whether the asset will close '
            'higher five US market sessions after the next open than at that open. Reply with exactly UP, DOWN, or '
            'ABSTAIN. Abstain when the reports give no clear reason either way. No explanation.'),
    },
}
# v2, lever 1 (structure): same as v1, but the Data Analyst's reply ends at its first blank line. Dev diagnosis on v1:
# LFM3 wrote the summary as paragraph one, then restated the facts until the 256-token cap (51 of 72 cases).
VARIANTS['v2'] = {**VARIANTS['v1'], 'analyst_stop': ['\n\n']}
# v3, lever 2 (guidance): same as v2, but Decision gets a symmetric counting rubric instead of a bare question.
# v1 and v2 began every Decision reply with UP. Written before any v3 number; judged on balanced accuracy against
# always-UP (0.500) with coverage reported, because ABSTAIN is now a real option.
VARIANTS['v3'] = {**VARIANTS['v2'], 'decision_system': (
    'You are [Decision], the last step of a research team. You get the team\'s accepted answer and its two '
    'specialist reports about one asset, based on the latest market close. Decide the direction over the next five '
    'US market sessions. Do not assume prices usually rise. Count only these signals: the last session change '
    '(up is +1, down is -1), and each accepted headline marked bullish (+1) or bearish (-1). Add them. If the total '
    'is above zero reply UP, below zero reply DOWN, zero or no signals reply ABSTAIN. Reply with exactly one word.')}


def symbol(t):
    return t + '-USD' if t in CRYPTO else t


def utc_day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ---------------------------------------------------------------- prices

def through_end(day):
    """Exclusive download end for 'bars through `day`' (YYYY-MM-DD): the next UTC midnight."""
    return datetime.combine(date.fromisoformat(day) + timedelta(days=1), datetime.min.time(), timezone.utc)


def collect(root, fetch=None, fetch_to=FETCH_TO, now=None):
    """Download daily bars for the assets and SPY into root/raw (existing files are kept, so a folder is one download).
    `fetch_to` must already have passed: otherwise a session still trading would be stored as if it had closed."""
    now = now or datetime.now(timezone.utc)
    if fetch_to > now:
        raise ValueError(f'Download end {fetch_to.isoformat()} has not passed yet; a session still trading would '
                         'be stored as a close. Collect after it, or give an earlier --through day.')
    receipt_path = root / 'collection.json'
    if receipt_path.exists():
        before = json.loads(receipt_path.read_text(encoding='utf-8')).get('fetch_to', FETCH_TO.isoformat())
        if before != fetch_to.isoformat():
            raise ValueError(f'{root} already holds a download ending {before}; use a new folder')
    if fetch is None:
        from core.market import fetch_json as fetch
    (root / 'raw').mkdir(parents=True, exist_ok=True)
    for s in [symbol(t) for t in ASSETS] + ['SPY']:
        path = root / 'raw' / (s + '.json')
        if path.exists():
            continue
        raw = fetch(f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(s)}?period1={int(FETCH_FROM.timestamp())}'
                    f'&period2={int(fetch_to.timestamp())}&interval=1d')
        if not (raw.get('chart') or {}).get('result'):
            raise ValueError('Missing source result: ' + s)
        path.write_text(json.dumps(raw), encoding='utf-8')
    try:
        raw = fetch('https://api.alternative.me/fng/?limit=500')
        (root / 'raw' / 'fng.json').write_text(json.dumps(raw), encoding='utf-8')
    except (OSError, ValueError):
        pass
    receipt = {'downloaded_at': now.isoformat(), 'fetch_to': fetch_to.isoformat(),
               'sources': {p.name: digest(p.read_bytes()) for p in sorted((root / 'raw').glob('*.json'))}}
    dump(receipt_path, receipt)
    return receipt


def parse(raw):
    r = raw['chart']['result'][0]
    q = r['indicators']['quote'][0]
    out = {}
    for i, t in enumerate(r.get('timestamp') or []):
        v = {k: ((q.get(k) or [])[i] if i < len(q.get(k) or []) else None) for k in ('open', 'high', 'low', 'close', 'volume')}
        if not all(finite(v[k]) and v[k] > 0 for k in ('open', 'high', 'low', 'close')):
            continue
        out[utc_day(t)] = {'date': utc_day(t), 'timestamp': t, **v}
    meta = r.get('meta') or {}
    return sorted(out.values(), key=lambda b: b['date']), str(meta.get('longName') or meta.get('shortName') or '')


def load_market(root):
    series, names = {}, {}
    for t in (*ASSETS, 'SPY'):
        bars, name = parse(json.loads((root / 'raw' / (symbol(t) + '.json')).read_text(encoding='utf-8')))
        series[t], names[t] = bars, name
    fng = {}
    p = root / 'raw' / 'fng.json'
    if p.exists():
        for item in (json.loads(p.read_text(encoding='utf-8')).get('data') or []):
            try:
                fng[utc_day(int(item['timestamp']))] = (int(item['value']), str(item['value_classification']))
            except (KeyError, TypeError, ValueError):
                continue
    return series, names, fng


def origins(spy_dates, first=FIRST_ORIGIN, last=LAST_ORIGIN):
    """(origin, entry, exit) SPY sessions: entry is the next session, exit the HORIZON-th session from entry."""
    out = []
    for i, d in enumerate(spy_dates):
        if first <= d <= last and i + HORIZON < len(spy_dates):
            out.append((d, spy_dates[i + 1], spy_dates[i + HORIZON]))
    return out


class ReplayMarket:
    """MarketDataTools at origin T: every value comes from bars dated on or before T."""

    def __init__(self, series, names, fng, origin):
        self.origin = origin
        self.series = {t: [b for b in bars if b['date'] <= origin] for t, bars in series.items()}
        self.names, self.fng = names, {d: v for d, v in fng.items() if d <= origin}
        self.max_date_seen = ''

    def get_snapshot(self, ticker):
        bars = self.series.get(ticker) or []
        snap = {'ticker': ticker, 'source': 'Yahoo Finance chart API (replay)', 'status': 'unavailable'}
        if not bars or bars[-1]['date'] != self.origin or len(bars) < 2:
            snap['error'] = 'Source request failed or returned invalid market data. No values were substituted.'
            return snap
        self.max_date_seen = max(self.max_date_seen, bars[-1]['date'])
        last, prev = bars[-1], bars[-2]
        price = last['close']
        year_ago = (date.fromisoformat(self.origin) - timedelta(days=365)).isoformat()
        year = [b for b in bars if b['date'] > year_ago]
        snap.update(price=round(price, 6 if price < 1 else 4), currency='USD', name=self.names.get(ticker) or ticker,
                    status='available', price_as_of=self.origin,
                    previous_session_close=round(prev['close'], 6 if prev['close'] < 1 else 4),
                    previous_session_date=prev['date'],
                    change_from_previous_session_pct=round((price / prev['close'] - 1) * 100, 4),
                    high_52_week=round(max(b['high'] for b in year), 4), low_52_week=round(min(b['low'] for b in year), 4))
        if finite(last.get('volume')) and last['volume'] > 0:
            snap['volume'] = int(last['volume'])
        high = max(max(b['high'] for b in bars), price)
        snap.update(all_time_high=round(high, 6 if high < 1 else 4), drawdown_from_all_time_high_pct=round((1 - price / high) * 100, 4),
                    history_start=bars[0]['date'][:7],
                    ath_drawdown='Highest daily high in source history since ' + bars[0]['date'][:7] + ' (split-adjusted)')
        if ticker in CRYPTO:
            snap['drawdown_from_52_week_high_pct'] = round((1 - price / snap['high_52_week']) * 100, 4)
            closes = [b['close'] for b in year]
            rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
            if len(rets) >= 20:
                snap['annualized_daily_log_volatility_pct'] = round(statistics.stdev(rets) * math.sqrt(365) * 100, 4)
            if self.origin in self.fng:
                value, label = self.fng[self.origin]
                snap.update(fear_greed_index=value, fear_greed_label=label, fear_greed_as_of=self.origin)
            else:
                snap['sentiment_status'] = 'Unavailable, no neutral score substituted'
        else:
            snap['fundamentals_as_of'] = {}
            snap['fundamentals_status'] = 'Unavailable from this source'
        return snap

    def plot_history(self, ticker):
        return 'chart://' + ticker if self.series.get(ticker) else ''

    def add_to_watchlist(self, ticker):
        raise RuntimeError('Research tools cannot write the watchlist.')


# ---------------------------------------------------------------- news

def available_at(row):
    """When an item could first have been seen: its own time; a day-level time counts from the end of that day."""
    raw = str(row.get('timestamp') or '')
    if not raw:
        return None
    ts = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    t = ts.timestamp()
    if ts.hour == ts.minute == ts.second == 0 and ts.microsecond == 0:
        t += 86400
    return t


def corpus_items(corpora=CORPORA):
    from core.signals import Item
    seen, out, undated = set(), [], 0
    for d in corpora:
        for line in (Path(d) / 'candidates.jsonl').read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            tkr = str(row.get('ticker') or '').upper()
            if tkr not in ASSETS:
                continue
            at = available_at(row)
            if at is None:
                undated += 1
                continue
            it = Item(tkr, str(row.get('source') or ''), str(row.get('url') or ''), str(row.get('title') or ''),
                      str(row.get('text') or ''), at)
            if it.key in seen:
                continue
            seen.add(it.key)
            out.append((it, str(row.get('timestamp_kind') or '')))
    return sorted(out, key=lambda x: x[0].published_at), undated


def gate_items(items, classifier):
    """The live Clock 1 pre-gate and SignalGate (rule urgency), each item judged at its own availability time."""
    from core.clock1 import Clock1Config
    from core.pregate import quarantine_reason, speculation_reason, title_key
    from core.signals import SignalGate
    cfg = Clock1Config()
    seen, last_alert, titles, rows = set(), {}, set(), []
    for it, kind in items:
        base = {'key': it.key, 'ticker': it.ticker, 'source': it.source, 'url': it.url, 'title': it.title, 'text': it.text,
                'available_at': it.published_at, 'timestamp_kind': kind}
        why = quarantine_reason(it) or speculation_reason(it)
        if why:
            rows.append({**base, 'action': 'ignore', 'reason': 'pregate:' + why})
            continue
        tk = title_key(it)
        if tk in titles:
            rows.append({**base, 'action': 'ignore', 'reason': 'duplicate_title'})
            continue
        titles.add(tk)
        gate = SignalGate(classifier, cfg.gate, seen=seen, last_alert=last_alert, clock=lambda t=it.published_at: t)
        o = gate.process([it])[0]
        j = o.judgment
        rows.append({**base, 'action': o.action.value, 'reason': o.reason,
                     **({'material': j.material, 'urgent': j.urgent, 'direction': j.direction, 'direction_p': j.direction_p} if j else {})})
    return rows


class ReplayNews:
    """NewsStorePort at cutoff: accepted rows available within NEWS_DAYS before the cutoff, alerts first, newest first."""

    def __init__(self, rows, cutoff):
        self.rows, self.cutoff = rows, cutoff
        self.served = []

    def get_gated_signals(self, ticker, limit=5):
        from core.signals import Action, Item, Judgment, Outcome
        ok = [r for r in self.rows if r['ticker'] == ticker.upper() and r['action'] in ('alert', 'digest')
              and self.cutoff - NEWS_DAYS * 86400 <= r['available_at'] <= self.cutoff]
        ok.sort(key=lambda r: (r['action'] == 'alert', r['available_at']), reverse=True)
        ok = ok[:int(limit)]
        self.served += [{'title': r['title'], 'available_at': r['available_at'], 'action': r['action'],
                         'direction': r.get('direction', 'unclear')} for r in ok]
        return [Outcome(Item(r['ticker'], r['source'], r['url'], r['title'], r['text'], r['available_at']), Action(r['action']),
                        r['reason'], Judgment(r['material'], r['urgent'], r['direction'], r['direction_p'])) for r in ok]


# ---------------------------------------------------------------- run and score

def cutoff_of(origin):
    """End of the origin day, UTC: before any next-session open."""
    return datetime.combine(date.fromisoformat(origin) + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp()


def calendar_closes(bars, spy_dates, origin, n=21):
    by = {b['date']: b['close'] for b in bars}
    ds = [d for d in spy_dates if d <= origin and d in by]
    return [by[d] for d in ds[-n:]]


def parse_label(text):
    """The label is the reply's first word (UP, DOWN or ABSTAIN); anything else is an error."""
    words = (text or '').strip().upper().split()
    first = words[0].strip('.,:;!*"\'') if words else ''
    return first if first in LABELS else 'ERROR'


AGENTS = ('Data Analyst', 'News Researcher', 'Market Team Lead', 'Decision')


class Recorder:
    """Wraps the narrator and logs each call (agent, ok or error, start of the reply), so fallbacks are visible."""

    def __init__(self, llm):
        self.llm, self.calls = llm, []

    def complete(self, system, prompt, *, max_tokens=256, **kw):
        agent = next((a for a in AGENTS if f'[{a}]' in system), 'other')
        try:
            text = self.llm.complete(system, prompt, max_tokens=max_tokens, **kw)
            self.calls.append({'agent': agent, 'ok': True, 'text': (text or '')[:160]})
            return text
        except Exception as exc:
            self.calls.append({'agent': agent, 'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:120]})
            raise


def decision_prompt(query, res):
    return (f'Question: {query}\n\nTeam answer: {res.final_synthesis}\n\n'
            f'Data Analyst report: {res.data_report.summary if res.data_report else "Not requested."}\n\n'
            f'News Researcher report: {res.news_report.summary if res.news_report else "Not requested."}')


def run(root, variant, *, router, llm, clock=time.time, limit=None):
    """Appends one prediction per case to predictions_<variant>.jsonl (resumable). Writes the seal when complete."""
    from core.orchestrator import DataAnalystAgent, NewsResearcherAgent, TeamLeadOrchestrator
    cfg = VARIANTS[variant]
    series, names, fng = load_market(root)
    news_rows = lines(root / 'news_gated.jsonl')
    spy_dates = [b['date'] for b in series['SPY']]
    path = root / f'predictions_{variant}.jsonl'
    seal = root / f'predictions_{variant}.seal.json'
    if seal.exists():
        raise ValueError('Predictions already sealed: score them, or use a new variant')
    done = {r['id'] for r in lines(path)} if path.exists() else set()
    made = 0
    for origin, entry, exit_ in origins(spy_dates):
        cutoff = cutoff_of(origin)
        for t in ASSETS:
            cid = f'{origin}:{t}'
            if cid in done:
                continue
            if limit is not None and made >= limit:
                return {'stopped_after': made}
            market, news = ReplayMarket(series, names, fng, origin), ReplayNews(news_rows, cutoff)
            rec_llm = Recorder(llm)
            analyst = DataAnalystAgent(rec_llm, market, stop=cfg.get('analyst_stop'))
            orch = TeamLeadOrchestrator(router, analyst, NewsResearcherAgent(rec_llm, news), rec_llm, market)
            query = QUERY.format(t=t)
            t0 = clock()
            rec = {'id': cid, 'symbol': t, 'origin': origin, 'entry_date': entry, 'exit_date': exit_, 'variant': variant, 'label': 'ERROR'}
            try:
                res = orch.run(query, session_id=cid, fallback_tickers=[t])
                gate = next((e.payload for e in reversed(res.events) if e.event_type == 'GATE_CHECK'), {})
                rec.update(route={'intent': res.routed.intent, 'tickers': res.routed.tickers, 'delegated': res.delegated_agents},
                           data_summary=res.data_report.summary if res.data_report else None,
                           data_faithful=res.data_report.faithful_numbers if res.data_report else None,
                           news_summary=res.news_report.summary if res.news_report else None,
                           news_faithful=res.news_report.faithful_numbers if res.news_report else None,
                           answer=res.final_synthesis, answer_faithful=res.faithful_synthesis,
                           guard_reason=gate.get('validation_reason'))
                try:
                    raw = rec_llm.complete(cfg['decision_system'], decision_prompt(query, res),
                                           max_tokens=cfg.get('decision_tokens', 24), allow_length=True)
                    rec.update(decision_raw=(raw or '')[:120], label=parse_label(raw))
                except Exception as exc:
                    rec.update(error='decision: ' + f'{type(exc).__name__}: {exc}'[:280])
            except Exception as exc:
                rec.update(error='team: ' + f'{type(exc).__name__}: {exc}'[:280])
            rec['calls'] = rec_llm.calls
            closes = calendar_closes(series[t], spy_dates, origin)
            up = sum(n['direction'] == 'bullish' for n in news.served)
            down = sum(n['direction'] == 'bearish' for n in news.served)
            rec.update(news_served=news.served, news_rows=len(news.served),
                       baselines={'always_up': 'UP', 'news_rule': 'UP' if up > down else 'DOWN' if down > up else 'ABSTAIN',
                                  'momentum20': ('UP' if closes[-1] >= closes[0] else 'DOWN') if len(closes) == 21 else 'ERROR'},
                       audit={'max_bar_date': market.max_date_seen, 'max_news_available_at': max([n['available_at'] for n in news.served], default=None),
                              'cutoff': cutoff, 'leak': bool(market.max_date_seen > origin or any(n['available_at'] > cutoff for n in news.served))},
                       seconds=round(clock() - t0, 1))
            with path.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(rec, allow_nan=False) + '\n')
            made += 1
    rows = lines(path)
    dump(seal, {'variant': variant, 'cases': len(rows), 'predictions_sha256': digest(path.read_bytes()),
                'code_sha256': digest(Path(__file__).read_bytes()), 'sealed_at': datetime.now(timezone.utc).isoformat()})
    return {'made': made, 'sealed': len(rows)}


def arm_metrics(rows, arm):
    lab = lambda r: r['label'] if arm == 'system' else r['baselines'][arm]
    truth = lambda r: 'UP' if r['return'] > 0 else 'DOWN' if r['return'] < 0 else 'FLAT'
    if not rows:
        return {'n': 0}
    covered = [r for r in rows if lab(r) in ('UP', 'DOWN')]
    recalls = []
    for side in ('UP', 'DOWN'):
        group = [r for r in rows if truth(r) == side]
        if group:
            recalls.append(sum(lab(r) == side for r in group) / len(group))
    return {'n': len(rows), 'accuracy_all': round(sum(lab(r) == truth(r) for r in rows) / len(rows), 4),
            'coverage': round(len(covered) / len(rows), 4),
            'accuracy_covered': round(sum(lab(r) == truth(r) for r in covered) / len(covered), 4) if covered else None,
            'balanced_accuracy': round(statistics.mean(recalls), 4) if len(recalls) == 2 else None,
            'labels': {k: sum(lab(r) == k for r in rows) for k in ('UP', 'DOWN', 'ABSTAIN', 'ERROR')}}


def route_counts(preds):
    counts = defaultdict(int)
    for p in preds:
        counts[str((p.get('route') or {}).get('intent'))] += 1
    return dict(sorted(counts.items()))


def score(root, variant):
    path, seal = root / f'predictions_{variant}.jsonl', root / f'predictions_{variant}.seal.json'
    if not seal.exists():
        raise ValueError('Predictions are not sealed yet: finish the run first')
    if json.loads(seal.read_text(encoding='utf-8'))['predictions_sha256'] != digest(path.read_bytes()):
        raise ValueError('Predictions changed after the seal')
    series, _, _ = load_market(root)
    preds, rows, missing = lines(path), [], []
    for p in preds:
        by = {b['date']: b for b in series[p['symbol']]}
        enter, leave = by.get(p['entry_date']), by.get(p['exit_date'])
        if not enter or not leave:
            missing.append(p['id'])
            continue
        rows.append({**p, 'return': leave['close'] / enter['open'] - 1})
    report = {'format': FORMAT, 'warning': WARNING, 'variant': variant, 'window': [FIRST_ORIGIN, LAST_ORIGIN], 'window_kind': 'dev',
              'cases': len(preds), 'scored': len(rows), 'missing_outcomes': missing,
              'leaks': sum(p.get('audit', {}).get('leak', False) for p in preds),
              'up_rate': round(sum(r['return'] > 0 for r in rows) / len(rows), 4) if rows else None,
              'arms': {a: arm_metrics(rows, a) for a in ARMS},
              'by_asset': {t: {a: arm_metrics([r for r in rows if r['symbol'] == t], a) for a in ('system', 'momentum20', 'always_up')} for t in ASSETS},
              'pipeline': {'routes': route_counts(preds),
                           'answer_faithful': sum(bool(p.get('answer_faithful')) for p in preds),
                           'data_faithful': sum(bool(p.get('data_faithful')) for p in preds),
                           'news_faithful': sum(bool(p.get('news_faithful')) for p in preds),
                           'call_errors': {a: sum(1 for p in preds for c in p.get('calls', []) if c['agent'] == a and not c['ok']) for a in AGENTS},
                           'with_news': sum(p.get('news_rows', 0) > 0 for p in preds),
                           'errors': sum(p['label'] == 'ERROR' for p in preds),
                           'median_seconds': statistics.median([p['seconds'] for p in preds]) if preds else None},
              'limits': ['dev window: daily origins overlap, cases are correlated', 'news availability is the item time, not an archived first-seen time',
                         'no point-in-time fundamentals for stocks', 'prices are a current download', 'LFM3 training cutoff unpublished (released 2026-10-27)']}
    dump(root / f'report_{variant}.json', report)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['collect', 'gate', 'run', 'score'])
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--variant', default='v1', choices=sorted(VARIANTS))
    ap.add_argument('--model-root', type=Path, default=Path('data/local_models'))
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--through', help='collect: last day to include, YYYY-MM-DD (default: the replay download)')
    a = ap.parse_args()
    if a.action == 'collect':
        print(json.dumps(collect(a.root, fetch_to=through_end(a.through) if a.through else FETCH_TO), indent=2))
        return
    if a.action == 'score':
        rep = score(a.root, a.variant)
        print(json.dumps({k: v for k, v in rep.items() if k != 'by_asset'}, indent=2))
        return
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    from qualify.decide_eval import build_arm
    clf = build_arm('gliner')
    if a.action == 'gate':
        items, undated = corpus_items()
        rows = gate_items(items, clf)
        from .historical import write_lines
        write_lines(a.root / 'news_gated.jsonl', rows)
        summary = {'items': len(rows), 'undated_skipped': undated,
                   'actions': {k: sum(r['action'] == k for r in rows) for k in ('alert', 'digest', 'ignore', 'deferred')},
                   'by_ticker_accepted': {t: sum(r['ticker'] == t and r['action'] in ('alert', 'digest') for r in rows) for t in ASSETS},
                   'news_gated_sha256': digest((a.root / 'news_gated.jsonl').read_bytes())}
        dump(a.root / 'gate_summary.json', summary)
        print(json.dumps(summary, indent=2))
        return
    if a.action == 'run':
        from core.gliner_decide import DEFAULT_ALIASES, GlinerQueryRouter
        from .history_runtime import narrator
        router = GlinerQueryRouter(clf.backend, set(DEFAULT_ALIASES.values()) | set(ASSETS))
        with narrator(a.model_root, a.root / 'model-runtime.log', alias='lfm3-replay') as (call, meta):
            class LLM:
                def complete(self, system, prompt, *, max_tokens=256, **kw):
                    return call(system, prompt, max_tokens=max_tokens, **kw)
            dump(a.root / f'model_{a.variant}.json', meta)
            print(json.dumps(run(a.root, a.variant, router=router, llm=LLM(), limit=a.limit)))


if __name__ == '__main__':
    main()
