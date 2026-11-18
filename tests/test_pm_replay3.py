"""Polymarket replay v3: event listing by tag, event-level rules, (record, asset) cluster verdict."""
import json
import math
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qualify import pm_replay as V2
from qualify import pm_replay3 as R

NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)


def mkt(i, p, hours=72, **over):
    return {'id': str(i), 'question': f'Will it be above strike {i}?', 'outcomes': ['Yes', 'No'],
            'clobTokenIds': [f'a{i}', f'b{i}'], 'outcomePrices': [str(p), str(round(1 - p, 4))], 'active': True,
            'closed': False, 'endDate': (NOW + timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ'), **over}


def event(i, title, markets, volume=20_000, tags=('crypto',)):
    return {'id': str(i), 'title': title, 'volume': volume, 'tags': [{'slug': t} for t in tags], 'markets': markets,
            'endDate': (NOW + timedelta(hours=72)).strftime('%Y-%m-%dT%H:%M:%SZ')}


EVENTS = [event(1, 'Bitcoin above ___ on November 26?', [mkt(11, .2), mkt(12, .45), mkt(13, .8)]),
          event(2, 'Ethereum above ___ on November 26?', [mkt(21, .6), mkt(22, .99)]),
          event(3, 'Bitcoin vs Ethereum: who gains more?', [mkt(31, .5)]),
          event(4, 'Nvidia Cup final: winner?', [mkt(41, .5)], tags=('sports',)),
          event(5, 'Bitcoin above ___ on November 27?', [mkt(51, .5)], volume=9_999),
          event(6, 'Will the Fed cut rates?', [mkt(61, .5)]),
          event(7, 'Bitcoin price on December 9?', [mkt(71, .5, hours=8 * 24 + 1)]),
          event(8, 'Ethereum hit ___ this week?', [mkt(81, .5, closed=True), mkt(82, .01)])]


class SelectionTests(unittest.TestCase):
    def test_event_rules(self):
        rows = R.select(EVENTS, NOW)
        self.assertEqual([(r['id'], r['asset'], r['event']) for r in rows], [('12', 'BTC-USD', '1'), ('21', 'ETH-USD', '2')])
        self.assertEqual(rows[0]['event_volume'], 20_000)
        self.assertEqual(R.select(EVENTS, NOW, seen_events={'1'})[0]['event'], '2')

    def test_listing_pages_every_tag_and_deduplicates(self):
        calls = []

        def getter(params):
            calls.append(params)
            if params['tag_slug'] == 'stocks':
                return [event(100 + i, 'x', []) for i in range(R.PAGE)]  # never ends: truncated at the cap
            return EVENTS[:2] if params['offset'] == 0 else []
        events, truncated = R.open_events(NOW, getter)
        self.assertEqual({c['tag_slug'] for c in calls}, set(R.TAGS))
        self.assertEqual(truncated, ['stocks'])
        self.assertEqual(len(events), 2 + R.PAGE)  # bitcoin and ethereum return the same two events
        self.assertEqual({c['limit'] for c in calls}, {R.PAGE})
        self.assertEqual(max(c['offset'] for c in calls if c['tag_slug'] == 'stocks'), R.MAX_OFFSET - R.PAGE)
        self.assertEqual([c['offset'] for c in calls if c['tag_slug'] == 'bitcoin'], [0])  # a short page is the end


class ClusterVerdictTests(unittest.TestCase):
    @staticmethod
    def rows(n_clusters, per, bad_clusters, team_good=.9, team_bad=.3):
        out = []
        for c in range(n_clusters):
            for k in range(per):
                out.append({'y': True, 'crowd': .6, 'team_blind': team_bad if c < bad_clusters else team_good,
                            'team_shown': .6, 'failed_blind': False, 'failed_shown': False, 'cluster': f'1:{c}'})
        return out

    def test_needs_twenty_clusters_whatever_the_numbers(self):
        report = R.score(self.rows(5, 20, 0))
        self.assertEqual((report['events'], report['clusters'], report['verdict']), (100, 5, 'not scored yet'))
        self.assertEqual(R.score(self.rows(25, 4, 0))['verdict'], 'team beats crowd')

    def test_correlated_events_do_not_count_as_independent(self):
        rows = self.rows(20, 5, 4)  # 16 clusters right, 4 wrong, each cluster moves together
        report = R.score(rows)
        primary = report['vs_crowd']['team_blind']
        self.assertLess(primary['mean'], 0)
        self.assertGreaterEqual(primary['interval_95'][1], 0)
        self.assertEqual(report['verdict'], 'no edge demonstrated')
        self.assertLess(V2.paired([{**r, 'y': 1.0} for r in rows], 'team_blind')['interval_95'][1], 0)  # v2 would claim it

    def test_void_and_freeze(self):
        rows = self.rows(25, 4, 0)
        for r in rows[:11]:
            r['failed_blind'] = True
        self.assertEqual(R.score(rows)['verdict'], 'no edge demonstrated')
        with tempfile.TemporaryDirectory() as t:
            sha = R.freeze(Path(t) / 'p', 'narr', 'abc', Path(t) / 'pub.json')
            saved = json.loads((Path(t) / 'p' / 'protocol.json').read_text(encoding='utf-8'))
            self.assertEqual((saved['format'], saved['verdict']['min_clusters'], saved['selection']['min_event_volume_usd']),
                             ('cofinance-pm-replay/3', R.MIN_CLUSTERS, R.MIN_EVENT_VOLUME))
            self.assertEqual(saved['supersedes']['format'], 'cofinance-pm-replay/2')
            self.assertEqual(saved['narrator']['readout'], V2.protocol('narr', 'abc')['narrator']['readout'])
            self.assertEqual(R.freeze(Path(t) / 'p', 'narr', 'abc'), sha)
            with self.assertRaises(ValueError):
                R.freeze(Path(t) / 'p', 'narr', 'other')


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root, self.calls = Path(self.tmp.name) / 'pm', []

    def narrator(self, sha='abc'):
        @contextmanager
        def run():
            def call(system, prompt, **kw):
                self.calls.append((system, json.loads(prompt), kw))
                return {'logprobs': {'content': [{'top_logprobs': [{'token': 'Y', 'logprob': math.log(.8)},
                                                                   {'token': 'NO', 'logprob': math.log(.2)}]}]}}
            yield call, {'id': 'narr', 'model_sha256': sha}
        return run

    def record(self, at=NOW, **kw):
        clock = iter([at.timestamp(), at.timestamp() + 5])
        bars = lambda s, now: [{'date': f'd{i}', 'timestamp': now - (40 - i) * 86400, 'open': 1, 'high': 2 + i, 'low': 1,
                                'close': 1 + i} for i in range(30)]
        args = dict(narrator=self.narrator(), narrator_id='narr', narrator_sha='abc', clock=lambda: next(clock),
                    getter=lambda p: EVENTS if p['offset'] == 0 and p['tag_slug'] == 'bitcoin' else [], prices=bars,
                    news_db=Path(self.tmp.name) / 'none.db', publish=Path(self.tmp.name) / 'pub.jsonl')
        return R.record(self.root, **{**args, **kw})

    def test_record_then_score_with_clusters(self):
        public = self.record()
        self.assertEqual([m['id'] for m in public['markets']], ['12', '21'])
        self.assertTrue(all('crowd_price' not in p for s, p, _ in self.calls if s == R.SYSTEM_BLIND))
        self.assertEqual(sorted(p['crowd_price'] for s, p, _ in self.calls if s == R.SYSTEM_SHOWN), [.45, .6])
        entry = R.ledger(self.root)[0][0]
        self.assertEqual((entry['format'], entry['events_listed'], entry['truncated_tags']), (R.FORMAT, len(EVENTS), []))
        self.assertAlmostEqual(entry['cases'][0]['team_blind'], .8)
        with self.assertRaisesRegex(ValueError, 'under 5 days'):
            self.record(at=NOW + timedelta(days=4))
        end = NOW + timedelta(hours=72)
        settled = mkt(12, .5, closed=True, outcomePrices=['1', '0'], closedTime=(end + timedelta(hours=1)).isoformat())
        report = R.score_ledger(self.root, getter=lambda i: settled if i == '12' else mkt(21, .5),
                                clock=lambda: (end + timedelta(days=1)).timestamp())
        self.assertEqual((report['events'], report['clusters'], report['pending']), (1, 1, ['21']))

    def test_same_asset_events_in_one_record_are_one_cluster(self):
        two_btc = [EVENTS[0], event(9, 'Bitcoin above ___ on November 25?', [mkt(91, .5)])]
        self.record(getter=lambda p: two_btc if p['offset'] == 0 and p['tag_slug'] == 'bitcoin' else [])
        end = NOW + timedelta(hours=72)
        won = lambda i: mkt(i, .5, closed=True, outcomePrices=['1', '0'], closedTime=(end + timedelta(hours=1)).isoformat())
        report = R.score_ledger(self.root, getter=won, clock=lambda: (end + timedelta(days=1)).timestamp())
        self.assertEqual((report['events'], report['clusters']), (2, 1))

    def test_wrong_weights_seal_nothing(self):
        with self.assertRaisesRegex(ValueError, 'weights differ'):
            self.record(narrator=self.narrator(sha='zzz'))
        self.assertFalse((self.root / 'ledger.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
