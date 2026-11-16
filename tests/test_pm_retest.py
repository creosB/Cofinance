"""Polymarket bucket retest: the statistics, the freeze, and the single look, all offline."""
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qualify import pm_lab, pm_retest
from qualify.pm_retest import PREREG, evaluate, freeze, one_per_event, tail, verdict

H1, H2 = PREREG['tests']['H1'], PREREG['tests']['H2']


def rows(n, price, wins, event=None):
    return [{'id': f'm{i:04d}', 'event_id': event if event else f'e{i}', 'p': price, 'y': int(i < wins)} for i in range(n)]


class TestStatistics(unittest.TestCase):
    def test_tail_matches_brute_force(self):
        ps = [.05, .2, .5, .9]
        pmf = {}
        for combo in itertools.product((0, 1), repeat=len(ps)):
            q = 1.0
            for won, p in zip(combo, ps):
                q *= p if won else 1 - p
            pmf[sum(combo)] = pmf.get(sum(combo), 0) + q
        for k in range(len(ps) + 2):
            self.assertAlmostEqual(tail(ps, k, 'fewer'), sum(v for j, v in pmf.items() if j <= k))
            self.assertAlmostEqual(tail(ps, k, 'more'), sum(v for j, v in pmf.items() if j >= k))
        self.assertEqual(tail([], 0, 'more'), 1.0)

    def test_h1_needs_few_enough_wins(self):
        self.assertEqual(verdict(rows(100, .05, 0), H1)['verdict'], 'confirmed')     # 5 expected, 0 seen
        self.assertEqual(verdict(rows(100, .05, 4), H1)['verdict'], 'not confirmed')
        self.assertEqual(verdict(rows(100, .05, 12), H1)['verdict'], 'not confirmed')  # the wrong direction never confirms

    def test_h2_needs_enough_wins(self):
        self.assertEqual(verdict(rows(100, .25, 40), H2)['verdict'], 'confirmed')
        self.assertEqual(verdict(rows(100, .25, 27), H2)['verdict'], 'not confirmed')
        self.assertEqual(verdict(rows(100, .25, 10), H2)['verdict'], 'not confirmed')

    def test_only_the_bucket_counts(self):
        mixed = rows(100, .05, 0) + rows(100, .5, 100)
        for r in mixed[100:]:
            r['id'] += 'x'
        self.assertEqual(verdict(mixed, H1)['per_event']['n'], 100)
        self.assertEqual(verdict(rows(100, .20, 0), H1)['per_event']['n'], 0)     # 0.20 belongs to H2's side of the edge
        self.assertEqual(verdict(rows(10, .20, 0), H2)['per_event']['n'], 10)
        self.assertEqual(verdict(rows(10, .30, 0), H2)['per_event']['n'], 0)

    def test_markets_of_one_event_count_once(self):
        one_event = rows(100, .05, 0, event='fed-decision')   # 100 thresholds of one event, all lost
        v = verdict(one_event, H1)
        self.assertEqual(v['per_event']['n'], 1)
        self.assertEqual(v['verdict'], 'not confirmed')
        self.assertLess(v['all_markets']['p_value'], .025)     # counting them all would have fooled us
        self.assertEqual([r['id'] for r in one_per_event(one_event)], ['m0000'])

    def test_empty_bucket_never_confirms(self):
        self.assertEqual(verdict([], H1)['verdict'], 'not confirmed')


def fake_market(group, day, wins_left):
    i = f'{group}-{day}'
    close = f'{day} 12:00:00+00'
    return {'id': i, 'question': 'Will it?', 'outcomes': '["Yes", "No"]',
            'outcomePrices': json.dumps(['1', '0'] if wins_left else ['0', '1']), 'clobTokenIds': json.dumps([f'y{i}', f'n{i}']),
            'closed': True, 'closedTime': close, 'endDate': day + 'T12:00:00Z', 'volumeNum': 60000,
            'events': [{'id': f'ev-{i}', 'title': 'Event ' + i}]}


class TestFreezeAndLook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        sleep = mock.patch.object(pm_lab.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def first_study(self):
        """A stand-in for the 2026-11-10 study: its own end days and one sampled market."""
        root = self.base / 'first'
        pm_lab.collect(root, lister=lambda **k: [fake_market('old', k['end_date_min'][:10], 0)] if k['offset'] == 0 else [])
        return root

    def lister(self, h1_wins, h2_wins):
        count = {'a': 0, 'b': 0}

        def call(**k):
            if k['offset']:
                return []
            day, out = k['end_date_min'][:10], []
            for group in ('a', 'b'):
                wins = h1_wins if group == 'a' else h2_wins
                out.append(fake_market(group, day, count[group] < wins))
                count[group] += 1
            return out
        return call

    def histories(self, root):
        sample = {m['yes_token']: m for m in pm_lab.lines(root / 'sample.jsonl')}

        def fetch(token, start, end, fidelity):
            m = sample[token]
            a = m['anchor']
            group_a = token.startswith('ya-')
            return [(int(a - 30 * 86400 - 3600), .05 if group_a else .5), (int(a - 86400 - 3600), .5 if group_a else .25)]
        pm_lab.history(root, fetcher=fetch, pause=0)

    def test_new_sample_uses_other_days_and_other_markets(self):
        old = self.first_study()
        old_days = set(json.loads((old / 'protocol.json').read_text())['sampled_end_days'])
        out = freeze(self.base / 'new', lister=self.lister(0, 0), original=old)
        p = json.loads((self.base / 'new' / 'protocol.json').read_text())
        self.assertFalse(old_days & set(p['sampled_end_days']))
        self.assertEqual(p['retest'], json.loads(json.dumps(PREREG)))      # tests are in the frozen protocol
        self.assertEqual(p['seed'], pm_retest.SEED)
        self.assertEqual(p['first_study']['sample_sha256'], json.loads((old / 'protocol.json').read_text())['sample_sha256'])
        self.assertEqual(out['events_shared_with_first_study'], 0)
        with self.assertRaises(ValueError):
            freeze(self.base / 'new', lister=self.lister(0, 0), original=old)   # a frozen sample is never redone

    def test_a_market_seen_before_stops_the_freeze(self):
        same = fake_market('dup', '2026-03-01', 0)      # one market the listing returns on every day, in both studies
        page = lambda **k: [same] if k['offset'] == 0 else []
        old = self.base / 'first'
        pm_lab.collect(old, lister=page)
        with self.assertRaisesRegex(ValueError, 'already in the first study'):
            freeze(self.base / 'new', lister=page, original=old)

    def test_one_look_confirms_what_the_data_shows_and_locks(self):
        old = self.first_study()
        root = self.base / 'new'
        freeze(root, lister=self.lister(0, 40), original=old)          # H1: 100 lost longshots, H2: 40 of 100 won
        with self.assertRaisesRegex(ValueError, 'no price history'):
            evaluate(root)                                                # nothing downloaded yet
        self.histories(root)
        report = evaluate(root)
        self.assertEqual(report['tests']['H1']['verdict'], 'confirmed')
        self.assertEqual(report['tests']['H2']['verdict'], 'confirmed')
        self.assertEqual(report['tests']['H1']['per_event']['n'], 100)
        self.assertTrue((root / 'retest.json').exists())
        with self.assertRaisesRegex(ValueError, 'already taken'):
            evaluate(root)

    def test_not_confirmed_when_the_data_do_not_show_it(self):
        old = self.first_study()
        root = self.base / 'new'
        freeze(root, lister=self.lister(6, 25), original=old)           # H1: 6 of 100 longshots won (5 expected), H2: 25 of 100
        self.histories(root)
        report = evaluate(root)
        self.assertEqual(report['tests']['H1']['verdict'], 'not confirmed')
        self.assertEqual(report['tests']['H2']['verdict'], 'not confirmed')

    def test_changed_tests_or_sample_are_refused(self):
        old = self.first_study()
        root = self.base / 'new'
        freeze(root, lister=self.lister(0, 40), original=old)
        self.histories(root)
        looser = json.loads(json.dumps(PREREG))
        looser['alpha_each'] = .2
        with mock.patch.object(pm_retest, 'PREREG', looser):
            with self.assertRaisesRegex(ValueError, 'tests changed'):
                evaluate(root)
        with (root / 'sample.jsonl').open('a', encoding='utf-8') as fh:
            fh.write('\n')
        with self.assertRaisesRegex(ValueError, 'sample changed'):
            evaluate(root)
        self.assertFalse((root / 'retest.json').exists())


if __name__ == '__main__':
    unittest.main()
