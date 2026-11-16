import json
import tempfile
import unittest
from pathlib import Path

from qualify import risk_assets as RA
from qualify.historical import ASSETS as LAB_ASSETS, lines
from tests.test_lab import raw


def case(sym, hot, big, n):
    var_b = 0.0004
    var_f = var_b * (2.0 if hot else 1.0)
    ret = 0.05 if big else 0.001
    return [{'symbol': sym, 'var_f': var_f, 'var_b': var_b, 'return': ret} for _ in range(n)]


class TestRiskAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        for i, s in enumerate(RA.ASSETS):
            (cls.root / (s + '.json')).write_text(json.dumps(raw(i)))
        cls.manifest = RA.build(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_assets_are_fixed_and_new_to_the_lab(self):
        self.assertEqual(RA.ASSETS, ('GOOGL', 'AMZN', 'JPM', 'XOM', 'KO', 'JNJ', 'WMT', 'CAT', 'SPY'))
        self.assertFalse(set(RA.ASSETS) & set(LAB_ASSETS))
        self.assertEqual(RA.MIN_AGREE, 6)

    def test_build_uses_lab_splits_and_every_asset(self):
        rows = lines(self.root / 'cases.jsonl')
        self.assertEqual({r['symbol'] for r in rows}, set(RA.ASSETS))
        self.assertTrue(all(r['x']['is_crypto'] == 0 and r['origin'] < r['entry_date'] for r in rows))
        self.assertEqual(set(self.manifest['by_split']), {'dev', 'validation', 'test'})
        with self.assertRaises(ValueError):
            RA.build(self.root)

    def test_confirmation_needs_the_pooled_pass_and_enough_agreeing_assets(self):
        agree = {s: {'agrees': True} for s in RA.ASSETS[:6]}
        agree.update({s: {'agrees': False} for s in RA.ASSETS[6:]})
        few = {s: {'agrees': i < 5} for i, s in enumerate(RA.ASSETS)}
        passed, failed = {'flag': {'passes': True}}, {'flag': {'passes': False}}
        self.assertTrue(RA.decide(passed, agree)['confirmed'])
        self.assertFalse(RA.decide(passed, few)['confirmed'])
        self.assertFalse(RA.decide(failed, agree)['confirmed'])

    def test_an_asset_agrees_only_with_flagged_cases_and_a_higher_rate(self):
        cases = (case('GOOGL', True, True, 3) + case('GOOGL', False, False, 9)
                 + case('AMZN', False, True, 4)
                 + case('JPM', True, False, 3) + case('JPM', False, True, 3))
        out = RA.per_asset(cases)
        self.assertTrue(out['GOOGL']['agrees'])
        self.assertFalse(out['AMZN']['agrees'])
        self.assertFalse(out['JPM']['agrees'])
        self.assertEqual((out['GOOGL']['flagged'], out['GOOGL']['flagged_big']), (3, 3))

    def test_one_look_and_changed_cases_are_refused(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            for name in ('cases.jsonl', 'manifest.json'):
                (root / name).write_bytes((self.root / name).read_bytes())
            p = root / 'cases.jsonl'
            good = p.read_bytes()
            p.write_bytes(good.replace(b'"split": "test"', b'"split": "dev"', 1))
            with self.assertRaises(ValueError):
                RA.evaluate(root)
            p.write_bytes(good)
            v = RA.evaluate(root)
            self.assertIn('confirmed', v['decision'])
            self.assertEqual(set(v['test_per_asset']), set(RA.ASSETS))
            with self.assertRaises(ValueError):
                RA.evaluate(root)
        finally:
            tmp.cleanup()


if __name__ == '__main__':
    unittest.main()
