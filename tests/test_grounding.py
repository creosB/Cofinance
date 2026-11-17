import unittest

from qualify import grounding as G
from qualify.historical import digest, dump, write_lines
from tests.test_decision_lab import row
from tests.test_replay import make_root


class TestGrounding(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = make_root()
        self.addCleanup(self.tmp.cleanup)
        series, names, fng = G.R.load_market(self.root)
        self.snap = G.R.ReplayMarket(series, names, fng, '2026-10-29').get_snapshot('NVDA')

    def seal(self, rows):
        path = self.root / 'predictions_v2.jsonl'
        write_lines(path, rows)
        dump(self.root / 'predictions_v2.seal.json', {'cases': len(rows), 'predictions_sha256': digest(path.read_bytes())})

    def case(self, answer, news=()):
        return {**row('NVDA', '2026-10-29'), 'answer': answer, 'data_summary': None, 'news_summary': None,
                'news_served': [{'title': t, 'action': 'digest', 'direction': 'bullish'} for t in news]}

    def test_tool_values_and_headline_numbers_are_traced(self):
        self.seal([self.case(f"NVDA last price is {self.snap['price']} USD after a $3 billion order.",
                             news=['Nvidia wins a $3 billion order'])])
        rep = G.check(self.root, 'v2')
        self.assertEqual(rep['fields']['answer']['numbers'], 2)
        self.assertEqual(rep['fields']['answer']['untraced'], 0)

    def test_an_invented_number_is_reported(self):
        self.seal([self.case(f"NVDA last price is {self.snap['price']} USD; target 987.65 USD.")])
        rep = G.check(self.root, 'v2')
        self.assertEqual(rep['fields']['answer']['untraced'], 1, 'A number no tool produced must be counted.')
        self.assertIn('987.65', rep['fields']['answer']['examples'][0])

    def test_missing_texts_are_skipped_not_counted(self):
        self.seal([self.case('No numbers here.')])
        rep = G.check(self.root, 'v2')
        self.assertEqual((rep['fields']['answer']['texts'], rep['fields']['data_summary']['texts']), (1, 0))
        self.assertEqual(rep['tool_data_unavailable'].get('fundamentals'), 1)


if __name__ == '__main__':
    unittest.main()
