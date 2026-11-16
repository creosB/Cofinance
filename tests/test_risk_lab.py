import json
import math
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from qualify.historical import digest, write_lines
from qualify.risk_lab import MIN_PAST, block_bootstrap_p, evaluate, flag_test, forecasts, qlike, run


def make_rows(seed, clustered, weeks=400, symbols=('AAA', 'BBB'), inverted=False):
    """Weekly cases. Clustered: risk switches between calm and wild every 20 weeks and vol20 sees it.
    Constant: one risk level, vol20 is only a noisy estimate of it. Inverted: vol20 sees the opposite regime."""
    rnd = random.Random(seed)
    rows = []
    start = date(2015, 1, 5)
    for sym in symbols:
        for w in range(weeks):
            calm = (w // 20) % 2 == 0
            sigma = (0.01 if calm else 0.04) if clustered else 0.025
            shown = (0.04 if calm else 0.01) if inverted else sigma
            seen = shown * (1 + rnd.gauss(0, 0.05 if clustered else 0.3))
            d0 = start + timedelta(days=7 * w)
            split = 'dev' if w < 240 else 'validation' if w < 320 else 'test'
            rows.append({'id': f'{sym}:{d0}', 'symbol': sym, 'origin': str(d0),
                         'entry_date': str(d0 + timedelta(days=1)), 'exit_date': str(d0 + timedelta(days=5)),
                         'split': split, 'x': {'vol20': abs(seen) / math.sqrt(5) * 100},
                         'return': rnd.gauss(0, sigma)})
    return rows


def make_root(rows):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    write_lines(root / 'cases.jsonl', rows)
    (root / 'manifest.json').write_text(json.dumps({'cases_sha256': digest((root / 'cases.jsonl').read_bytes())}))
    return tmp, root


class TestRiskLab(unittest.TestCase):
    def test_forecasts_use_only_earlier_cases(self):
        rows = make_rows(1, clustered=True)
        before = {c['id']: c for c in forecasts(rows)[0]}
        changed = sorted((r for r in rows if r['symbol'] == 'AAA'), key=lambda r: r['entry_date'])[150]
        changed['return'] = 5.0
        after = {c['id']: c for c in forecasts(rows)[0]}
        for cid, c in before.items():
            if c['symbol'] == 'AAA' and c['entry_date'] <= changed['exit_date']:
                self.assertEqual((c['var_f'], c['var_b']), (after[cid]['var_f'], after[cid]['var_b']), cid)
        later = [cid for cid, c in before.items() if c['symbol'] == 'AAA' and c['entry_date'] > changed['exit_date']]
        self.assertTrue(later and all(before[cid]['var_b'] != after[cid]['var_b'] for cid in later))

    def test_cases_without_history_are_skipped_and_counted(self):
        cases, skipped = forecasts(make_rows(2, clustered=True))
        self.assertEqual(skipped, {'AAA': MIN_PAST, 'BBB': MIN_PAST})
        self.assertEqual(len(cases), 2 * (400 - MIN_PAST))

    def test_true_variance_scores_better_than_a_wrong_one(self):
        rnd = random.Random(3)
        draws = [rnd.gauss(0, 0.02) for _ in range(5000)]
        right = sum(qlike(0.02 ** 2, r) for r in draws)
        self.assertLess(right, sum(qlike(0.04 ** 2, r) for r in draws))
        self.assertLess(right, sum(qlike(0.01 ** 2, r) for r in draws))

    def test_bootstrap_p_value(self):
        rnd = random.Random(4)
        self.assertLess(block_bootstrap_p([0.3 + rnd.gauss(0, 0.5) for _ in range(120)]), 0.01)
        noise = [rnd.gauss(0, 0.5) for _ in range(120)]
        noise = [v - sum(noise) / len(noise) for v in noise]
        self.assertGreater(block_bootstrap_p(noise), 0.2)
        self.assertEqual(block_bootstrap_p(noise), block_bootstrap_p(noise))
        self.assertIsNone(block_bootstrap_p([1.0] * 10))

    def test_clustered_risk_passes_and_constant_risk_does_not(self):
        clustered, _ = evaluate(make_rows(5, clustered=True), ('validation',))
        constant, _ = evaluate(make_rows(5, clustered=False), ('validation',))
        self.assertTrue(clustered['validation']['passes'])
        self.assertGreater(clustered['validation']['mean_weekly_gain'], 0)
        self.assertFalse(constant['validation']['passes'])

    def test_test_split_opens_once_and_only_after_validation_passes(self):
        tmp, root = make_root(make_rows(6, clustered=True))
        try:
            with self.assertRaises(ValueError):
                run(root, final=True)
            rep = run(root)
            self.assertNotIn('test', rep['splits'])
            self.assertFalse((root / 'risk_peeks.jsonl').exists())
            report = json.loads((root / 'risk_report.json').read_text())
            report['code_sha256'] = 'edited'
            (root / 'risk_report.json').write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                run(root, final=True)
            run(root)
            rep = run(root, final=True)
            self.assertIn('test', rep['splits'])
            self.assertIn('claim', rep)
            for name in ('risk_peeks.jsonl', 'test_peeks.jsonl'):
                self.assertEqual(len((root / name).read_text().splitlines()), 1, name)
            with self.assertRaises(ValueError):
                run(root, final=True)
        finally:
            tmp.cleanup()

    def test_flag_against_the_data_keeps_the_test_locked(self):
        tmp, root = make_root(make_rows(7, clustered=True, inverted=True))
        try:
            rep = run(root)
            self.assertLess(rep['splits']['validation']['flag']['difference'], 0)
            with self.assertRaises(ValueError):
                run(root, final=True)
            self.assertFalse((root / 'risk_final.json').exists())
            self.assertFalse((root / 'test_peeks.jsonl').exists())
        finally:
            tmp.cleanup()

    def test_flag_finds_clustered_risk_and_not_inverted_or_constant(self):
        hit = evaluate(make_rows(9, clustered=True), ('validation',))[0]['validation']['flag']
        self.assertTrue(hit['passes'])
        self.assertGreater(hit['elevated_rate'], hit['other_rate'])
        for rows in (make_rows(9, clustered=True, inverted=True), make_rows(9, clustered=False)):
            self.assertFalse(evaluate(rows, ('validation',))[0]['validation']['flag']['passes'])

    def test_flag_counts_weeks_not_assets(self):
        week = [{'id': f'{s}', 'symbol': s, 'origin': '2024-02-05', 'entry_date': '2024-02-06', 'split': 'dev',
                 'return': 0.05, 'var_f': 0.0009, 'var_b': 0.0001} for s in 'ABC']
        f = flag_test(week)
        self.assertEqual((f['elevated_cases'], f['other_cases'], f['passes']), (3, 0, False))
        self.assertNotIn('p_value', f)

    def test_changed_cases_are_refused(self):
        tmp, root = make_root(make_rows(8, clustered=True))
        try:
            p = root / 'cases.jsonl'
            p.write_bytes(p.read_bytes().replace(b'"split": "test"', b'"split": "dev"', 1))
            with self.assertRaises(ValueError):
                run(root)
        finally:
            tmp.cleanup()


if __name__ == '__main__':
    unittest.main()
