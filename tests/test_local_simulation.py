import json
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from qualify import local_simulation as S


def panel():
    return [{'id': f'{d}:{a}', 'origin': d, 'asset': a, 'part': 'dev' if d < 'd3' else 'reused_window',
             'y': (i + j) % 2 == 0, 'headline': (i * 3 + j - 3) / 3, 'gate': j - 1,
             'momentum20': (i - j) / 10, 'news_count': 1, 'always_up': 1, 'impacts': [(i * 3 + j - 3) / 3]}
            for i, d in enumerate(('d1', 'd2', 'd3')) for j, a in enumerate(('A', 'B', 'C'))]


class TestLocalSimulation(unittest.TestCase):
    def test_audit_guard_blocks_network_dns_and_commands(self):
        for event in ('socket.__new__', 'socket.connect', 'socket.sendto', 'socket.getaddrinfo',
                      'subprocess.Popen', 'os.system', 'os.posix_spawn'):
            with self.assertRaisesRegex(S.LocalOnlyError, 'Use the existing cache'):
                S.deny_network(event, ())
        S.deny_network('open', ())

    def test_installed_guard_in_child_without_any_network_attempt(self):
        # Synthetic audit events exercise the installed hook without making DNS/socket requests even if broken.
        script = "from qualify.local_simulation import install_local_only; import sys; install_local_only(); sys.audit('socket.getaddrinfo', 'example.invalid', 443, 0, 0, 0)"
        p = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('LocalOnlyError', p.stderr)
        script = "from qualify.local_simulation import install_local_only; import sys; install_local_only(); sys.audit('subprocess.Popen', 'no-command', [], None, None)"
        p = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('LocalOnlyError', p.stderr)

    def test_protocol_hashes_inputs_and_refuses_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            source, root = Path(d) / 'input.json', Path(d) / 'run'
            source.write_text('{}')
            h = S.freeze(root, [str(source)])
            self.assertEqual(h, S.digest((root / 'protocol.json').read_bytes()))
            self.assertEqual(S.verify(root)['spec'], S.SPEC)
            with self.assertRaises(ValueError):
                S.freeze(root, [str(source)])
            source.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, 'Cached input changed'):
                S.verify(root)
            source.write_text('{}')
            with patch.object(S, 'SPEC', {**S.SPEC, 'seed': 123}):
                with self.assertRaises(ValueError):
                    S.verify(root)

    def test_changed_code_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'run'
            S.freeze(root, [])
            path = root / 'protocol.json'
            value = json.loads(path.read_text()); value['code_sha256'] = 'bad'
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'code changed'):
                S.verify(root)

    def test_failed_and_missing_judgments_are_zero_not_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'j.jsonl'
            p.write_text('\n'.join(json.dumps(r) for r in [
                {'id': 'A|x', 'ok': True, 'label': 'UP', 'impact': .9},
                {'id': 'A|y', 'ok': False, 'label': 'UP', 'impact': .99},
                {'id': 'A|z', 'ok': True, 'label': 'ERROR', 'impact': .99}]))
            values, failed = S.judgments(p)
            self.assertEqual(values, {'A|x': .9, 'A|y': 0., 'A|z': 0.})
            self.assertEqual(failed, {'A|y', 'A|z'})
            p2 = Path(d) / 'j2.jsonl'
            p2.write_text(json.dumps({'id': 'A|x', 'ok': True, 'label': 'UP', 'impact': .1}))
            with self.assertRaises(ValueError):
                S.judgments(p, p2)

    def fixture(self):
        days = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(35)]
        bars = {d: {'open': 100., 'close': 100. + i} for i, d in enumerate(days)}
        r = {'id': '2026-10-27:NVDA', 'origin': '2026-10-27', 'symbol': 'NVDA',
             'news_served': [{'title': 'x', 'available_at': 1793145599., 'direction': 'bullish'},
                             {'title': 'missing', 'available_at': 1793145599., 'direction': 'bearish'}]}
        return r, bars, days

    def test_case_target_cutoff_and_missing_scores(self):
        r, bars, days = self.fixture()
        c, reason = S.build_case(r, bars, days, {'NVDA|x': .75})
        self.assertIsNone(reason)
        self.assertEqual(c['entry'], '2026-10-28')
        self.assertEqual(c['exit'], '2026-11-01')
        self.assertAlmostEqual(c['return'], bars['2026-11-01']['close'] / 100 - 1)
        self.assertEqual(c['impacts'], [.75, 0.])
        self.assertEqual(c['missing_judgments'], 1)
        self.assertEqual(c['gate'], 0)
        for when in (1793145601., 1793145600. - 7 * 86400 - 1, None):
            r['news_served'][0]['available_at'] = when
            with self.assertRaisesRegex(ValueError, 'cutoff'):
                S.build_case(r, bars, days, {})

    def test_missing_bars_and_flat_outcomes_are_accounted(self):
        r, bars, days = self.fixture()
        del bars['2026-11-01']
        self.assertEqual(S.build_case(r, bars, days, {})[1], 'missing_price_bar')
        bars['2026-11-01'] = {'open': 100, 'close': 100}
        self.assertEqual(S.build_case(r, bars, days, {})[1], 'flat_outcome')
        r['exit_date'] = '2026-11-02'
        with self.assertRaises(ValueError):
            S.build_case(r, bars, days, {})

    def test_auc_ties_reversal_and_single_class(self):
        rows = [{'headline': 1, 'y': True}, {'headline': 1, 'y': False}]
        self.assertEqual(S.pair_auc(rows)['auc'], .5)
        rows = [{'headline': s, 'y': y} for s, y in [(3, True), (2, True), (1, False), (0, False)]]
        self.assertEqual(S.pair_auc(rows)['auc'], 1)
        self.assertEqual(S.pair_auc([{**r, 'headline': -r['headline']} for r in rows])['auc'], 0)
        self.assertIsNone(S.pair_auc(rows[:2])['auc'])

    def test_conditional_auc_detects_group_confounding(self):
        rows = [{'headline': 1, 'y': True, 'origin': 'a'}, {'headline': 1, 'y': False, 'origin': 'a'},
                {'headline': 9, 'y': True, 'origin': 'b'}, {'headline': 9, 'y': True, 'origin': 'b'}]
        self.assertGreater(S.pair_auc(rows)['auc'], .5)
        self.assertEqual(S.pair_auc(rows, group='origin'), {'auc': .5, 'comparable_pairs': 1})

    def test_balanced_accuracy_counts_abstentions_as_misses(self):
        c = panel()[:2]
        c[0].update(headline=.5, y=True); c[1].update(headline=0, y=False)
        m = S.metrics(c)
        self.assertEqual(m['balanced_accuracy'], .5)
        self.assertEqual(m['coverage'], .5)
        c[1]['headline'] = -.5
        self.assertEqual(S.metrics(c)['balanced_accuracy'], 1.)
        self.assertEqual(S.metrics(c, 'always_up')['balanced_accuracy'], .5)

    def test_asset_shuffle_preserves_whole_paths_and_outcomes(self):
        source = panel()
        out = S.shuffled(source, 'asset', random.Random(3))
        paths = {tuple(c['headline'] for c in source if c['asset'] == a) for a in ('A', 'B', 'C')}
        newpaths = {tuple(c['headline'] for c in out if c['asset'] == a) for a in ('A', 'B', 'C')}
        self.assertEqual(paths, newpaths)
        self.assertEqual([c['y'] for c in out], [c['y'] for c in source])
        self.assertNotEqual([c['headline'] for c in out], [c['headline'] for c in source])
        with self.assertRaises(ValueError):
            S.shuffled(source[:-1], 'asset', random.Random(1))

    def test_circular_shift_preserves_vectors_and_orders(self):
        source = panel(); out = S.shuffled(source, 'date', shift=1)
        self.assertEqual([c['headline'] for c in out[:3]], [c['headline'] for c in source[3:6]])
        self.assertEqual([c['headline'] for c in out[-3:]], [c['headline'] for c in source[:3]])
        self.assertEqual([c['y'] for c in out], [c['y'] for c in source])
        with self.assertRaises(ValueError):
            S.shuffled(source, 'date', shift=0)

    def test_stress_does_not_invent_outcomes_or_mutate_inputs(self):
        source = panel(); original = json.dumps(source)
        self.assertEqual(S.pair_auc(S.stress(source, 'no_news'))['auc'], .5)
        self.assertAlmostEqual(S.pair_auc(S.stress(source, 'reverse_sign'))['auc'], 1 - S.pair_auc(source)['auc'])
        doubled = S.stress(source, 'duplicate_all')
        self.assertEqual([c['headline'] for c in doubled], [2 * c['headline'] for c in source])
        self.assertEqual(S.pair_auc(doubled), S.pair_auc(source))
        self.assertTrue(all(c['headline'] == 0 for c in S.stress(source, 'drop_strongest')))
        self.assertEqual(json.dumps(source), original)

    def test_bootstrap_is_paired_and_deterministic(self):
        rows = panel()
        for r in rows:
            r['gate'] = r['headline']
        a = S.paired_bootstrap(rows, 'gate', 30)
        self.assertEqual(a, {'low': 0, 'median': 0, 'high': 0, 'draws': 30})
        self.assertEqual(a, S.paired_bootstrap(rows, 'gate', 30))

    def test_analysis_reports_all_fixed_arms_and_stresses(self):
        a = S.analyze(panel(), permutations=20, bootstrap=20)
        self.assertEqual(set(a['arms']), set(S.SPEC['arms']))
        self.assertEqual(set(a['stress']), set(S.SPEC['stress']))
        self.assertEqual(a['asset_path_shuffle']['draws'], 20)
        self.assertEqual(len(a['circular_date_shift']['aucs']), 2)
        self.assertEqual(set(a['parts']), {'dev', 'reused_window'})

    def test_run_is_one_look_and_verifies_inputs_again(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'run'; S.freeze(root, [])
            with patch.object(S, 'load_cases', return_value=(panel(), {})), patch.object(S, 'analyze', return_value={'fake': True}):
                r = S.run(root)
            self.assertFalse(r['production_changed'])
            self.assertEqual(r['cases'], 9)
            self.assertTrue((root / 'report.json').exists())
            with self.assertRaises(ValueError):
                S.run(root)
