import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qualify import gdelt as G


def article(**kw):
    return {'title': 'Nvidia launches a chip', 'url': 'https://example.org/Story?utm_source=x',
            'seendate': '20261027T000000Z', 'language': 'English', 'domain': 'example.org', **kw}


class TestGdelt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'run'
        self.spec = G.protocol('20261027000000', '20261028000000', ['NVDA'])
        self.a, self.b = self.spec['start'], self.spec['end_exclusive']

    def norm(self, row):
        return G.normalize(row, 'NVDA', self.a, self.b, self.spec, 'raw/0001.json', 'hash', 'observed')

    def freeze(self):
        G.freeze(self.root, self.spec, now=self.b)

    def run_collection(self, articles=None, fetch=None):
        self.freeze()
        get = fetch or (lambda _: (200, {}, json.dumps({'articles': articles or []}).encode()))
        return G.collect(self.root, get=get, sleep=lambda _: None, clock=lambda: self.b)

    def test_protocol_rejects_unknown_duplicate_and_future(self):
        for tickers in ([], ['NVDA', 'NVDA'], ['BAD']):
            with self.assertRaises(ValueError):
                G.protocol('20261027000000', '20261028000000', tickers)
        with self.assertRaises(ValueError):
            G.freeze(self.root, self.spec, now=self.b - 1)
        for start, end in [('20261028000000', '20261027000000'), ('20260901000000', '20261028000000'),
                           ('20261027000100', '20261028000000')]:
            with self.assertRaises(ValueError):
                G.protocol(start, end, ['NVDA'])

    def test_exact_midnight_is_seen_time_not_publication(self):
        row = self.norm(article())
        self.assertEqual(row['available_at'], self.a)
        self.assertEqual(row['timestamp_kind'], 'gdelt_seen')
        self.assertEqual(row['published_at'], '')
        self.assertEqual(row['text'], '')
        self.assertEqual(row['url'], 'https://example.org/Story')
        self.assertEqual(row['snapshot_sha256'], 'hash')

    def test_cutoffs_are_half_open_and_no_time_substitution(self):
        for when in ('20261026T235959Z', '20261028T000000Z', '', None, 'invalid'):
            with self.assertRaises(ValueError):
                self.norm(article(seendate=when))
        self.assertEqual(self.norm(article(seendate='20261027T235959Z'))['available_at'], self.b - 1)

    def test_irrelevant_language_url_title_refused(self):
        for kw in ({'title': 'Nvidian unrelated'}, {'title': ''}, {'language': 'French'},
                   {'url': 'file:///secret'}, {'url': 'https://user:pass@example.org/'}, {'title': 'x' * 2001}):
            with self.assertRaises(ValueError):
                self.norm(article(**kw))

    def test_bad_transport_is_not_empty_success(self):
        for status, body in [(429, b'{"articles": []}'), (500, b'{}'), (200, b'<html>error'), (200, b'{}'),
                             (200, b'{"articles": {}}'), (200, b'x' * (G.MAX_BYTES + 1))]:
            with self.assertRaises(ValueError):
                G.parse_response(status, body)
        self.assertEqual(G.parse_response(200, b'{"articles": []}'), [])
        with self.assertRaises(ValueError):
            G.fetch('https://example.org/?query=private')
        with self.assertRaises(ValueError):
            G.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_earliest_duplicate_and_ticker_identity(self):
        one = self.norm(article())
        later = {**one, 'available_at': self.a + 10, 'title': 'Nvidia later amended headline'}
        copy = {**one, 'url': 'https://other.org/story'}
        peer = {**one, 'ticker': 'AMD'}
        kept = G.dedupe([later, copy, one, peer])
        self.assertEqual(len(kept), 2)
        self.assertEqual({r['ticker'] for r in kept}, {'AMD', 'NVDA'})
        self.assertTrue(all(r['available_at'] == self.a for r in kept))

    def test_cap_is_split_and_parent_not_counted(self):
        calls = []
        def get(url):
            calls.append(url)
            rows = [article()] * G.LIMIT if len(calls) == 1 else []
            return 200, {}, json.dumps({'articles': rows}).encode()
        m = self.run_collection(fetch=get)
        self.assertEqual(len(calls), 3)
        self.assertEqual(m['capped_intervals'], 1)
        self.assertEqual(m['rows'], 0)
        self.assertEqual(m['state'], 'complete')
        self.assertIn('enddatetime=20261027120000', calls[1])
        self.assertIn('startdatetime=20261027120000', calls[2])

    def test_minimum_cap_is_incomplete_not_a_sample(self):
        self.spec = G.protocol('20261027000000', '20261027001500', ['NVDA'])
        m = self.run_collection([article()] * G.LIMIT)
        self.assertEqual(m['state'], 'incomplete')
        self.assertEqual(m['rows'], 0)
        self.assertEqual(m['gaps'][0]['reason'], 'minimum_interval_capped')
        with self.assertRaises(ValueError):
            G.load_complete(self.root)

    def test_budget_or_retries_exhausted_cannot_enter_evaluation(self):
        self.spec['request_budget'] = 2
        calls = []
        def get(url):
            calls.append(url)
            return 429, {}, b'Too Many Requests'
        m = self.run_collection(fetch=get)
        self.assertEqual(len(calls), 2)
        self.assertEqual(m['state'], 'incomplete')
        self.assertTrue(m['gaps'])
        with self.assertRaises(ValueError):
            G.load_complete(self.root)

    def test_receipts_hashes_and_seal_refuse_overwrite(self):
        m = self.run_collection([article(), article(), article(title='Other business')])
        self.assertEqual(m['rows'], 1)
        self.assertEqual(m['duplicates_removed'], 1)
        self.assertEqual(m['rejected']['no_asset_in_title'], 1)
        rows = G.load_complete(self.root)
        self.assertEqual(len(rows), 1)
        with self.assertRaises(ValueError):
            G.collect(self.root)
        with self.assertRaises(ValueError):
            G.freeze(self.root, self.spec, now=self.b)
        for name in ('protocol.json', 'candidates.jsonl', 'raw/0001.json'):
            p = self.root / name
            original = p.read_bytes()
            p.write_bytes(original + b' ')
            with self.assertRaises(ValueError):
                G.load_complete(self.root)
            p.write_bytes(original)
        self.assertEqual(G.load_complete(self.root), rows)

    def test_code_change_after_freeze_refused(self):
        self.spec['code_sha256'] = 'bad'
        self.freeze()
        with self.assertRaises(ValueError):
            G.collect(self.root, get=lambda _: self.fail('must not fetch'))

    def test_retry_delays_and_errors_retained(self):
        responses = iter([(429, {}, b'limit'), (200, {}, b'{"articles": []}')])
        sleeps = []
        self.freeze()
        m = G.collect(self.root, get=lambda _: next(responses), sleep=sleeps.append, clock=lambda: self.b)
        self.assertEqual(sleeps, [20])
        self.assertEqual(m['state'], 'complete')
        self.assertEqual(m['requests'], 2)
        self.assertIn('error', json.loads((self.root / 'raw/0001.json').read_text()))

    def test_statechart_matches_document(self):
        self.assertIn(G.statechart(), Path('docs/statecharts.md').read_text(encoding='utf-8'))

    def test_random_illegal_moves_and_real_state(self):
        model = {'empty': {'frozen'}, 'frozen': {'collecting'}, 'collecting': {'complete', 'incomplete'},
                 'complete': set(), 'incomplete': set()}
        rng, seen = random.Random(17), set()
        for _ in range(50):
            current = 'empty'
            for _ in range(20):
                dest = rng.choice(list(model))
                with patch.object(G, 'stage', return_value=current):
                    if dest in model[current]:
                        G.require_move(self.root, dest)
                        seen.add((current, dest)); current = dest
                    else:
                        with self.assertRaises(ValueError):
                            G.require_move(self.root, dest)
        self.assertEqual(seen, {(a, b) for a, bs in model.items() for b in bs})
        self.assertEqual(G.stage(self.root), 'empty')
        self.freeze()
        self.assertEqual(G.stage(self.root), 'frozen')
        G.save_new(self.root / 'started.json', {})
        self.assertEqual(G.stage(self.root), 'collecting')
        G.save_new(self.root / 'manifest.json', {'state': 'incomplete'})
        self.assertEqual(G.stage(self.root), 'incomplete')
