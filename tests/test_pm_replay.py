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
        self.assertAlmostEqual(p, 1 / 3)
        self.assertFalse(failed)
        self.assertEqual(R.readout([('YES', -.1)]), (1.0, False))
        self.assertEqual(R.readout([('MAYBE', -.1)]), (.5, True))
        self.assertEqual(R.readout(None), (.5, True))

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


if __name__ == '__main__':
    unittest.main()
