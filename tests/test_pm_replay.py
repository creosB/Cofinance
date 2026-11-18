"""The frozen Polymarket replay rules: selection, readout, outcome, verdict and the freeze itself."""
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qualify import pm_replay as R

NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)


def market(i, question='Will Bitcoin be above $90,000 on November 27?', p=.4, hours=72, event=None, **over):
    return {'id': str(i), 'question': question, 'active': True, 'closed': False, 'prices': [p, 1 - p], 'volume': 60_000,
            'end': (NOW + timedelta(hours=hours)).isoformat(), 'event_id': event or f'e{i}', 'tags': [], 'category': '',
            **over}


class SelectionTests(unittest.TestCase):
    def test_each_rule_drops_what_it_names(self):
        kept = market(1)
        dropped = [market(2, closed=True), market(3, active=False), market(4, volume=49_999), market(5, hours=23),
                   market(6, hours=8 * 24 + 1), market(7, question='Will the Fed cut rates?'),
                   market(8, question='Will Bitcoin outperform Ethereum?'), market(9, p=.01), market(10, p=.99),
                   market(11, question='Who wins the Apple Cup?', tags=['sports']),
                   market(12, question='Apple Cup winner?', category='sports'), market(13, prices=None)]
        self.assertEqual([m['id'] for m in R.select([kept, *dropped], NOW)], ['1'])
        self.assertEqual(R.select([kept], NOW)[0]['asset'], 'BTC-USD')

    def test_aliases_are_whole_words(self):
        self.assertEqual(R.asset_of('Will NVIDIA close above $200?'), 'NVDA')
        self.assertEqual(R.asset_of('ETH above 3k?'), 'ETH-USD')
        self.assertIsNone(R.asset_of('Will the ethics bill pass?'))
        self.assertIsNone(R.asset_of('Pineapple prices?'))

    def test_one_market_per_event_closest_to_even_odds_ties_to_lowest_id(self):
        rows = R.select([market(5, p=.2, event='e'), market(4, p=.6, event='e'), market(3, p=.4, event='e')], NOW)
        self.assertEqual([r['id'] for r in rows], ['3'])  # .4 and .6 tie on distance, lowest id wins
        self.assertEqual(R.select([market(1, event='e')], NOW, seen_events={'e'}), [])

    def test_cap_is_a_seeded_sample_reproducible_for_the_day(self):
        many = [market(i) for i in range(1, 100)]
        a, b = R.select(many, NOW), R.select(list(reversed(many)), NOW)
        self.assertEqual(len(a), R.MAX_MARKETS)
        self.assertEqual([r['id'] for r in a], [r['id'] for r in b])
        self.assertNotEqual([r['id'] for r in a], [r['id'] for r in R.select(many, NOW + timedelta(days=1))])
        self.assertNotEqual([r['id'] for r in a], [str(i) for i in range(1, 41)])


class ReadoutAndOutcomeTests(unittest.TestCase):
    def test_probability_from_yes_and_no_logprobs(self):
        p, failed = R.readout([('YES', math.log(.3)), ('NO', math.log(.6)), (' yes', math.log(.05))])
        self.assertAlmostEqual(p, .35 / .95)  # distinct tokens for one answer are summed
        self.assertFalse(failed)
        self.assertEqual(R.readout([('YES', -.1)]), (1.0, False))
        self.assertEqual(R.readout([('MAYBE', -.1)]), (.5, True))
        self.assertEqual(R.readout(None), (.5, True))

    def test_split_yes_token_is_counted(self):
        # 11/18 probe: AtomicChat's first token is 'Y' (YES = 'Y' + 'ES'); NO is one token
        p, failed = R.readout([('Y', math.log(.9)), ('Yes', math.log(.02)), ('NO', math.log(.08)), ('{"', -6), ('Yo', -7)])
        self.assertAlmostEqual(p, .92 / 1.0)
        self.assertFalse(failed)
        self.assertAlmostEqual(R.readout([('N', math.log(.5)), ('No', math.log(.3)), ('Y', math.log(.2))])[0], .2)
        self.assertEqual(R.readout([('Yo', -.1), ('{Y', -.2)]), (.5, True))  # not a start of YES or NO

    def test_outcome_needs_clean_settlement_in_time(self):
        end = '2026-11-27T17:00:00Z'
        settled = {'closed': True, 'prices': [1.0, 0.0], 'closed_time': '2026-11-27T18:00:00Z'}
        self.assertIs(R.outcome(settled, end), True)
        self.assertIs(R.outcome({**settled, 'prices': [0.0, 1.0]}, end), False)
        self.assertIsNone(R.outcome({**settled, 'prices': [.5, .5]}, end))
        self.assertIsNone(R.outcome({**settled, 'closed_time': '2026-12-05T18:00:00Z'}, end))
        self.assertIsNone(R.outcome({**settled, 'closed': False}, end))


class VerdictTests(unittest.TestCase):
    @staticmethod
    def rows(n, team_gap):
        # crowd always 0.6 on outcomes that happen 60% of the time; the team is closer by team_gap
        return [{'y': i % 5 < 3, 'crowd': .6, 'team_blind': (.6 + team_gap) if i % 5 < 3 else (.6 - team_gap),
                 'team_shown': .6, 'failed_blind': False, 'failed_shown': False} for i in range(n)]

    def test_no_claim_before_the_minimum_whatever_the_numbers(self):
        self.assertEqual(R.score(self.rows(R.MIN_EVENTS - 1, .3))['verdict'], 'not scored yet')
        self.assertEqual(R.score([])['verdict'], 'not scored yet')

    def test_a_clearly_better_team_beats_the_crowd_and_a_copy_does_not(self):
        better = R.score(self.rows(R.MIN_EVENTS, .3))
        self.assertEqual(better['verdict'], 'team beats crowd')
        self.assertLess(better['vs_crowd']['team_blind']['interval_95'][1], 0)
        same = R.score([{**r, 'team_blind': .6} for r in self.rows(R.MIN_EVENTS, 0)])
        self.assertEqual(same['verdict'], 'no edge demonstrated')
        self.assertEqual(same['echo_rate_shown'], 1.0)
        self.assertEqual(same['brier']['coin'], .25)

    def test_a_negative_mean_is_not_enough_when_the_interval_crosses_zero(self):
        rows = self.rows(R.MIN_EVENTS, 0)  # team equals the crowd ...
        rows[0]['team_blind'] = rows[1]['team_blind'] = .9  # ... except two lucky calls on outcomes that happened
        rows[3]['team_blind'] = .8  # and one bad call on an outcome that did not
        report = R.score(rows)
        primary = report['vs_crowd']['team_blind']
        self.assertLess(primary['mean'], 0)
        self.assertGreaterEqual(primary['interval_95'][1], 0)
        self.assertEqual(report['verdict'], 'no edge demonstrated')

    def test_too_many_readout_failures_void_the_arm(self):
        rows = self.rows(R.MIN_EVENTS, .3)
        for r in rows[:11]:
            r['failed_blind'] = True
        report = R.score(rows)
        self.assertTrue(report['void']['team_blind'])
        self.assertEqual(report['verdict'], 'no edge demonstrated')

    def test_auc_is_ranking_only(self):
        self.assertEqual(R.auc([.9, .8, .1], [True, True, False]), 1.0)
        self.assertEqual(R.auc([.5, .5], [True, False]), .5)
        self.assertIsNone(R.auc([.5], [True]))


class FreezeTests(unittest.TestCase):
    def test_frozen_once_published_identically_and_changes_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root, pub = Path(t) / 'pm', Path(t) / 'pub.json'
            sha = R.freeze(root, 'narr', 'abc', pub)
            self.assertEqual(pub.read_bytes(), (root / 'protocol.json').read_bytes())
            self.assertEqual(R.freeze(root, 'narr', 'abc'), sha)
            with self.assertRaises(ValueError):
                R.freeze(root, 'narr', 'other-weights')
            saved = json.loads((root / 'protocol.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['verdict']['min_events'], R.MIN_EVENTS)
            self.assertIn('crowd', saved['narrator']['system_shown'])
            self.assertNotIn('crowd', saved['narrator']['system_blind'])


def gamma(i, question, p, hours=72, event=None, closed=False, prices=None, closed_time=None):
    """A raw Gamma market row, as the live endpoint returns it."""
    return {'id': str(i), 'question': question, 'outcomes': ['Yes', 'No'], 'clobTokenIds': [f't{i}a', f't{i}b'],
            'outcomePrices': prices or [str(p), str(round(1 - p, 4))], 'active': True, 'closed': closed,
            'endDate': (NOW + timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'volumeNum': 80_000,
            'closedTime': closed_time, 'events': [{'id': event or f'e{i}', 'title': 'Event', 'tags': []}]}


MARKETS = [gamma(1, 'Will Bitcoin be above $90,000 on November 26?', .45), gamma(2, 'Will NVIDIA close above $200?', .3),
           gamma(3, 'Will the Fed cut rates?', .5)]


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'pm'
        self.calls, self.listed = [], []
        self.addCleanup(self.tmp.cleanup)

    def lister(self, **params):
        self.listed.append(params)
        return MARKETS if params['offset'] == 0 else []

    @staticmethod
    def prices(symbol, now):
        return [{'date': f'd{i:02d}', 'timestamp': now - (40 - i) * 86400, 'open': 100 + i, 'high': 101 + i, 'low': 99 + i,
                 'close': 100 + i} for i in range(30)]

    def narrator(self, sha='abc', tokens=(('Y', math.log(.7)), ('NO', math.log(.3)))):
        from contextlib import contextmanager

        @contextmanager
        def run():
            def call(system, prompt, **kw):
                self.calls.append((system, json.loads(prompt), kw))
                return {'message': {'content': 'Y'},
                        'logprobs': {'content': [{'top_logprobs': [{'token': t, 'logprob': lp} for t, lp in tokens]}]}}
            yield call, {'id': 'narr', 'model_sha256': sha}
        return run

    def record(self, at=NOW, root=None, **kw):
        clock = iter([at.timestamp(), at.timestamp() + 5])
        args = dict(narrator=self.narrator(), narrator_id='narr', narrator_sha='abc', clock=lambda: next(clock),
                    lister=self.lister, prices=self.prices, news_db=Path(self.tmp.name) / 'none.db',
                    publish=Path(self.tmp.name) / 'public.jsonl')
        return R.record(root or self.root, **{**args, **kw})

    def test_one_record_seals_both_arms_with_the_frozen_inputs(self):
        public = self.record()
        self.assertEqual([m['id'] for m in public['markets']], ['1', '2'])  # the Fed market names no tracked asset
        self.assertEqual(self.listed[0]['volume_num_min'], R.MIN_VOLUME)
        blind = [p for s, p, _ in self.calls if s == R.SYSTEM_BLIND]
        shown = [p for s, p, _ in self.calls if s == R.SYSTEM_SHOWN]
        self.assertEqual(len(blind), 2)
        self.assertTrue(all('crowd_price' not in p for p in blind))
        self.assertEqual([p['crowd_price'] for p in shown], [.45, .3])
        self.assertEqual({(kw['max_tokens'], kw['top_logprobs']) for _, _, kw in self.calls}, {(1, 20)})
        self.assertEqual(blind[0]['prices']['return_20_sessions_pct'], round((129 / 109 - 1) * 100, 2))
        entries, _ = R.ledger(self.root)
        case = entries[0]['cases'][0]
        self.assertAlmostEqual(case['team_blind'], .7)
        self.assertFalse(case['failed_blind'])
        self.assertEqual(case['news_rows'], 0)
        self.assertEqual(len((Path(self.tmp.name) / 'public.jsonl').read_text(encoding='utf-8').splitlines()), 1)

    def test_records_need_five_days_and_the_frozen_weights(self):
        self.record()
        with self.assertRaisesRegex(ValueError, 'under 5 days'):
            self.record(at=NOW + timedelta(days=4))
        other = Path(self.tmp.name) / 'other'
        with self.assertRaisesRegex(ValueError, 'weights differ'):
            self.record(root=other, narrator=self.narrator(sha='zzz'))
        self.assertEqual(len(R.ledger(self.root)[0]), 1)
        self.assertFalse((other / 'ledger.jsonl').exists())  # nothing sealed with the wrong weights

    def test_a_failed_readout_is_recorded_not_dropped(self):
        self.record(narrator=self.narrator(tokens=(('{', -.1),)))
        case = R.ledger(self.root)[0][0]['cases'][0]
        self.assertEqual((case['team_blind'], case['failed_blind'], case['failed_shown']), (.5, True, True))

    def test_a_changed_ledger_is_refused(self):
        self.record()
        path = self.root / 'ledger.jsonl'
        path.write_bytes(path.read_bytes().replace(b'"team_blind": 0.7', b'"team_blind": 0.9'))
        with self.assertRaises(ValueError):
            R.ledger(self.root)

    def test_an_earlier_record_cannot_be_rewritten(self):
        from qualify.historical import digest
        self.root.mkdir(parents=True)
        a = json.dumps({'record': 1, 'prev_sha256': R.GENESIS}).encode()
        b = json.dumps({'record': 2, 'prev_sha256': digest(a)}).encode()
        (self.root / 'ledger.jsonl').write_bytes(a + b'\n' + b + b'\n')
        (self.root / 'head.json').write_text(json.dumps({'records': 2, 'sha256': digest(b)}), encoding='utf-8')
        self.assertEqual(len(R.ledger(self.root)[0]), 2)
        (self.root / 'ledger.jsonl').write_bytes(a.replace(b'1', b'7') + b'\n' + b + b'\n')  # head still matches b
        with self.assertRaisesRegex(ValueError, 'chain'):
            R.ledger(self.root)

    def test_score_refuses_a_changed_protocol(self):
        self.record()
        p = self.root / 'protocol.json'
        p.write_text(p.read_text(encoding='utf-8').replace('"min_events": 100', '"min_events": 10'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'protocol mismatch'):
            R.score_ledger(self.root, getter=lambda i: {}, clock=lambda: NOW.timestamp())

    def test_score_counts_settled_waits_for_open_and_excludes_late(self):
        self.record()
        end = NOW + timedelta(hours=72)
        settled = gamma(1, 'x', .5, closed=True, prices=['1', '0'], closed_time=(end + timedelta(hours=1)).isoformat())
        still_open = gamma(2, 'x', .5)
        get = lambda i: settled if i == '1' else still_open
        early = R.score_ledger(self.root, getter=get, clock=lambda: (end + timedelta(days=1)).timestamp())
        self.assertEqual((early['events'], early['pending'], early['excluded']), (1, ['2'], []))
        self.assertEqual(early['verdict'], 'not scored yet')
        late = R.score_ledger(self.root, getter=get, clock=lambda: (end + timedelta(days=8)).timestamp())
        self.assertEqual((late['pending'], late['excluded']), ([], ['2']))


if __name__ == '__main__':
    unittest.main()
