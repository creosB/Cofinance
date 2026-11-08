import csv
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from qualify.label_store import LabelStore, clean_label
from qualify.news_dataset import FIELDS, labelled_rows

CORPUS = Path(__file__).resolve().parents[1] / 'data' / 'news_corpus_20261107'
GOOD = {'material': True, 'urgent': False, 'direction': 'neutral', 'trap': 'none', 'event_group': 'aapl-designers-2026', 'notes': 'Showcase, no financial effect.'}


@unittest.skipUnless((CORPUS / 'candidates.jsonl').is_file(), 'local corpus not present')
class TestLabelStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / 'corpus'
        shutil.copytree(CORPUS, self.folder, ignore=shutil.ignore_patterns('label.before-ui.csv', '.label-*'))
        self.store = LabelStore(self.folder)
        self.first = self.store.snapshot()['items'][0]

    def tearDown(self):
        self.temp.cleanup()

    def rows(self):
        text = (self.folder / 'label.csv').read_bytes().decode('utf-8-sig')
        return list(csv.DictReader(io.StringIO(text, newline='')))

    def test_save_keeps_evidence_and_passes_finalize_rules(self):
        before = self.rows()
        snap = self.store.save({'id': self.first['id'], **GOOD})
        after = self.rows()
        self.assertEqual(snap['labelled'], 1)
        self.assertTrue((self.folder / 'label.before-ui.csv').is_file())
        for old, new in zip(before, after):
            self.assertEqual([old[k] for k in FIELDS[:7]], [new[k] for k in FIELDS[:7]])
        candidates = [json.loads(l) for l in (self.folder / 'candidates.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        row = next(r for r in candidates if r['id'] == self.first['id'])
        result = labelled_rows([row], [next(a for a in after if a['id'] == row['id'])])
        self.assertEqual(result[0]['material'], True)
        self.assertIsNone(result[0]['trap'])

    def test_invalid_labels_are_rejected_and_nothing_is_written(self):
        before = (self.folder / 'label.csv').read_bytes()
        for bad in ({**GOOD, 'urgent': True, 'material': False}, {**GOOD, 'direction': 'up'},
                    {**GOOD, 'trap': 'rumour'}, {**GOOD, 'event_group': 'has space'}, {**GOOD, 'material': 'maybe'}):
            with self.assertRaises(ValueError):
                self.store.save({'id': self.first['id'], **bad})
        self.assertEqual((self.folder / 'label.csv').read_bytes(), before)

    def test_unknown_id_is_rejected(self):
        with self.assertRaises(KeyError):
            self.store.save({'id': 'nope', **GOOD})

    def test_tampered_corpus_is_read_only(self):
        path = self.folder / 'candidates.jsonl'
        path.write_bytes(path.read_bytes() + b'\n{"id":"x"}\n')
        with self.assertRaises(ValueError):
            self.store.snapshot()
        with self.assertRaises(ValueError):
            self.store.save({'id': self.first['id'], **GOOD})

    def test_snapshot_is_blind(self):
        text = json.dumps(self.store.snapshot()).lower()
        for word in ('materiality_score', 'confidence', 'signal gate', '"action"'):
            self.assertNotIn(word, text)

    def test_clean_label_accepts_strings(self):
        self.assertEqual(clean_label({**GOOD, 'material': 'TRUE', 'urgent': 'false'})['material'], 'true')


if __name__ == '__main__':
    unittest.main()
