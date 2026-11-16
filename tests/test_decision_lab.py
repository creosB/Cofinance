import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qualify import decision_lab as D
from qualify.historical import digest, dump, lines, write_lines


def row(symbol='NVDA', origin='2026-10-27'):
    return {'id': f'{origin}:{symbol}', 'symbol': symbol, 'origin': origin,
            'answer': 'Accepted summary.', 'data_summary': 'Price fell 2%.', 'news_summary': None,
            'label': 'UP', 'return': 99999, 'baselines': {'momentum20': 'HIDDEN_BASELINE'},
            'audit': {'leak': False}, 'news_served': [{'title': 'HIDDEN_RAW_NEWS'}]}


class TestDecisionLab(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / 'screen'
        self.rows = [row('NVDA', '2026-10-27'), row('NVDA', '2026-10-28'), row('NVDA', '2026-11-06'),
                     row('BTC', '2026-10-27'), row('BTC', '2026-11-06')]
        self.source = self.root / 'predictions_v2.jsonl'
        write_lines(self.source, self.rows)
        dump(self.root / 'predictions_v2.seal.json',
             {'cases': len(self.rows), 'predictions_sha256': digest(self.source.read_bytes())})

    def tearDown(self):
        self.tmp.cleanup()

    def run_screen(self, call=None):
        return D.run(self.root, self.out, 'v2', 'contract-v1', 'smoke',
                     call or (lambda *a, **kw: '{"label":"DOWN"}'), {'test': True})

    def test_prompt_matches_original_and_excludes_hidden_fields(self):
        text = D.cached_prompt(self.rows[0])
        self.assertEqual(text, 'Question: ' + D.R.QUERY.format(t='NVDA') +
                         '\n\nTeam answer: Accepted summary.\n\nData Analyst report: Price fell 2%.' +
                         '\n\nNews Researcher report: Not requested.')
        for hidden in ('99999', 'HIDDEN_BASELINE', 'HIDDEN_RAW_NEWS', '2026-10-27'):
            self.assertNotIn(hidden, text, 'Only exact model-visible upstream reports may enter Decision.')

    def test_sample_selection_is_outcome_blind_first_and_last_per_asset(self):
        selected = D.select_cases(self.rows, 'smoke')
        self.assertEqual([r['id'] for r in selected],
                         ['2026-10-27:BTC', '2026-11-06:BTC', '2026-10-27:NVDA', '2026-11-06:NVDA'])
        changed = [{**r, 'return': -123, 'label': 'DOWN'} for r in reversed(self.rows)]
        self.assertEqual([r['id'] for r in selected], [r['id'] for r in D.select_cases(changed, 'smoke')])
        self.assertEqual(len(D.select_cases(self.rows, 'full')), len(self.rows))

    def test_structured_parser_rejects_prose_extra_keys_and_unknown_labels(self):
        for label in ('UP', 'DOWN', 'ABSTAIN'):
            self.assertEqual(D.parse_json_label(json.dumps({'label': label})), label)
        for text in ('UP', '{"label":"UP","reason":"x"}', '{"label":"maybe"}', '[]', '{}', '{"label":null}'):
            self.assertEqual(D.parse_json_label(text), 'ERROR')

    def test_changed_source_is_refused_before_model_call(self):
        self.source.write_text(self.source.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'Source predictions changed'):
            self.run_screen(lambda *a, **kw: self.fail('Unsealed source reached the model'))
        self.assertFalse(self.out.exists())

    def test_run_never_loads_outcomes_and_plan_exists_before_call(self):
        seen = []
        def call(system, prompt, **kw):
            self.assertTrue((self.out / 'plan.json').exists())
            self.assertEqual(kw['response_format'], D.SCHEMA)
            self.assertEqual(kw['max_tokens'], 24)
            self.assertNotIn('allow_length', kw)
            seen.append(prompt)
            return '{"label":"DOWN"}'
        with patch.object(D.R, 'load_market', side_effect=AssertionError('Outcomes entered prediction')):
            self.assertEqual(self.run_screen(call)['cases'], 4)
        self.assertEqual(len(seen), 4)
        D.verified(self.root, self.out)

    def test_failures_remain_in_the_denominator(self):
        def broken(*a, **kw):
            raise ValueError('Incomplete forecast')
        self.run_screen(broken)
        preds = lines(self.out / 'predictions.jsonl')
        self.assertEqual(len(preds), 4)
        self.assertTrue(all(p['label'] == 'ERROR' for p in preds))
        self.assertTrue(all('Incomplete forecast' in p['error'] for p in preds))

    def test_existing_screen_is_never_overwritten(self):
        self.run_screen()
        before = (self.out / 'predictions.jsonl').read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_screen()
        self.assertEqual((self.out / 'predictions.jsonl').read_bytes(), before)

    def test_score_requires_seal_before_loading_outcomes(self):
        with patch.object(D.R, 'load_market', side_effect=AssertionError('Outcomes loaded too soon')):
            with self.assertRaises(FileNotFoundError):
                D.score(self.root, self.out)

    def test_modified_plan_or_predictions_are_rejected(self):
        self.run_screen()
        path = self.out / 'predictions.jsonl'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'changed after sealing'):
            D.verified(self.root, self.out)

    def test_split_and_metrics_count_errors_abstentions_and_missing_outcomes(self):
        rows = self.rows
        for r in rows:
            r.update(entry_date='2026-10-28', exit_date='2026-11-03',
                     baselines={'momentum20': 'DOWN', 'always_up': 'UP', 'news_rule': 'ABSTAIN'})
        write_lines(self.source, rows)
        dump(self.root / 'predictions_v2.seal.json', {'cases': len(rows), 'predictions_sha256': digest(self.source.read_bytes())})
        answers = iter(['{"label":"DOWN"}', '{"label":"ABSTAIN"}', 'bad', '{"label":"UP"}'])
        self.run_screen(lambda *a, **kw: next(answers))
        bars = [{'date': '2026-10-28', 'open': 100, 'close': 100}, {'date': '2026-11-03', 'open': 90, 'close': 90}]
        with patch.object(D.R, 'load_market', return_value=({'NVDA': bars, 'BTC': bars}, {}, {})):
            report = D.score(self.root, self.out)
        self.assertEqual(report['cases'], 4)
        self.assertEqual(report['arms']['candidate']['accuracy_all'], .25)
        self.assertEqual(report['arms']['candidate']['coverage'], .5)
        self.assertEqual(report['arms']['candidate']['labels'], {'UP': 1, 'DOWN': 1, 'ABSTAIN': 1, 'ERROR': 1})
        self.assertEqual(report['paired_vs_source']['clusters'], 2)
        with patch.object(D.R, 'load_market', return_value=({'NVDA': bars, 'BTC': []}, {}, {})):
            missing = D.score(self.root, self.out)
        self.assertEqual(len(missing['missing_outcomes']), 2)
        self.assertEqual(missing['scored'], 2)


if __name__ == '__main__':
    unittest.main()
