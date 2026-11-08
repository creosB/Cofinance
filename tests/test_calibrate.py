import json
import shutil
import tempfile
import unittest
from pathlib import Path

from qualify.calibrate import calibrate, choose, counts, decide, load_corpus
from qualify.news_dataset import digest, labelled_rows, split_rows

CORPUS = Path(__file__).resolve().parents[1] / 'data' / 'news_corpus_20261108'


class TestCounts(unittest.TestCase):
    def test_counts_and_decide(self):
        c = counts([True, True, False, False], [True, False, True, False])
        self.assertEqual((c['tp'], c['fp'], c['fn'], c['tn']), (1, 1, 1, 1))
        self.assertEqual(c['f1'], 0.5)
        cfg = {'digest_p': 0.5, 'alert_p': 0.8, 'alert_urgent_p': 0.7}
        self.assertEqual(decide({'material': 0.9, 'urgent': 0.9}, cfg), (True, True))
        self.assertEqual(decide({'material': 0.6, 'urgent': 0.9}, cfg), (True, False))
        self.assertEqual(decide({'material': 0.4, 'urgent': 0.99}, cfg), (False, False))

    def test_choose_finds_a_separating_threshold(self):
        train = [{'id': str(i), 'material': i >= 5, 'urgent': i >= 8} for i in range(10)]
        scores = {str(i): {'material': i / 10 + 0.02, 'urgent': i / 10 + 0.02} for i in range(10)}
        chosen, urgent = choose(train, scores)
        self.assertEqual(urgent, 2)
        self.assertTrue(0.45 < chosen['digest_p'] <= 0.52)
        self.assertGreaterEqual(chosen['alert_p'], chosen['digest_p'])


@unittest.skipUnless((CORPUS / 'label.csv').is_file(), 'local corpus not present')
class TestCalibrateRun(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.corpus = Path(self.temp.name) / 'corpus'
        shutil.copytree(CORPUS, self.corpus, ignore=shutil.ignore_patterns('label.before-ui.csv', '.label-*'))
        _, rows, annotations, corpus_sha, _, _ = load_corpus(self.corpus)
        labels = {r['id']: r for r in labelled_rows(rows, annotations)} if all(a.get('event_group') for a in annotations) else {}
        self.scores = Path(self.temp.name) / 'cal' / 'scores.jsonl'
        self.scores.parent.mkdir()
        # Synthetic scores that agree with the labels: the test checks plumbing, not GLiNER quality.
        lines = [json.dumps({'id': r['id'], 'material': 0.9 if labels.get(r['id'], {}).get('material') else 0.1,
                             'urgent': 0.9 if labels.get(r['id'], {}).get('urgent') else 0.1, 'direction': 'unclear', 'direction_p': 0.0}) for r in rows]
        self.scores.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        self.scores.with_suffix('.meta.json').write_text(json.dumps({'model': 'fixture', 'revision': 'fixture', 'network': False,
            'corpus_sha256': corpus_sha, 'scores_sha256': digest(self.scores.read_bytes())}), encoding='utf-8')
        self.ready = bool(labels)

    def tearDown(self):
        self.temp.cleanup()

    def test_unready_gate_needs_provisional_and_heldout_runs_once(self):
        if not self.ready:
            self.skipTest('corpus not fully labelled')
        out = Path(self.temp.name) / 'cal'
        report = calibrate(self.corpus, self.scores, out, provisional=True)
        self.assertIn(report['status'], {'provisional', 'final'})
        self.assertEqual(report['labels']['labeler'], json.loads((self.corpus / 'label_provenance.json').read_text())['labeler'] if (self.corpus / 'label_provenance.json').is_file() else 'human')
        self.assertEqual(report['heldout']['calibrated']['digest_vs_material']['fp'], 0)
        with self.assertRaises(ValueError):
            calibrate(self.corpus, self.scores, out, provisional=True)
        if report['status'] == 'provisional':
            with self.assertRaises(ValueError):
                calibrate(self.corpus, self.scores, Path(self.temp.name) / 'other', provisional=False)

    def test_changed_scores_are_rejected(self):
        self.scores.write_text(self.scores.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            calibrate(self.corpus, self.scores, Path(self.temp.name) / 'cal', provisional=True)


if __name__ == '__main__':
    unittest.main()
