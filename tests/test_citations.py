"""Citations: each number in an answer points at the tool value that backs it, decided by Python."""
import unittest

from core.citations import cite
from core.numeric_guard import number_spans, numbers_in

SNAP = {'NVDA': {'ticker': 'NVDA', 'source': 'Yahoo Finance chart API', 'price_as_of': '2026-11-13T21:00:00Z',
                 'fetched_at': '2026-11-14T08:00:00Z', 'status': 'available', 'currency': 'USD', 'price': 186.26,
                 'change_from_previous_session_pct': -2.104, 'market_cap': 4.512e12, 'volume': 179308722,
                 'high_52_week': 212.19, 'all_time_high': 212.19}}
EVIDENCE = [{'title': 'NVDA market data', 'source': 'Yahoo Finance', 'as_of': '2026-11-13'},
            {'title': 'Nvidia ships 3 new chips', 'source': 'Reuters', 'as_of': '2026-11-12'}]


class TestCitations(unittest.TestCase):
    def test_each_number_points_at_its_field(self):
        answer = 'NVDA last traded at 186.26, down 2.10% on the day, with a market cap of 4.51 trillion.'
        got = {c['text']: [r['field'] for r in c['refs']] for c in cite(answer, SNAP, EVIDENCE)}
        self.assertEqual(got, {'186.26': ['price'], '2.10%': ['change_from_previous_session_pct'],
                               '4.51 trillion': ['market_cap']})

    def test_spans_are_exact_slices_of_the_answer(self):
        answer = 'Volume was 179.31 million. Price: 186.26.'
        for c in cite(answer, SNAP, EVIDENCE):
            self.assertEqual(answer[c['start']:c['end']], c['text'])
        self.assertEqual([c['text'] for c in cite(answer, SNAP, EVIDENCE)], ['179.31 million', '186.26'])

    def test_wrong_scale_and_unknown_numbers_get_no_citation(self):
        self.assertEqual(cite('Market cap 4.51 billion, target 250.', SNAP, EVIDENCE), [])

    def test_equal_values_cite_every_field_they_could_be(self):
        refs = cite('The high was 212.19.', SNAP, EVIDENCE)[0]['refs']
        self.assertEqual({r['field'] for r in refs}, {'high_52_week', 'all_time_high'})
        self.assertTrue(all(r['source'] == 'Yahoo Finance chart API' and r['as_of'].startswith('2026-11-13') for r in refs))

    def test_evidence_title_backs_a_number_no_fact_explains(self):
        refs = cite('Nvidia shipped 3 new chips.', SNAP, EVIDENCE)[0]['refs']
        self.assertEqual(refs, [{'kind': 'evidence', 'index': 1, 'title': 'Nvidia ships 3 new chips', 'source': 'Reuters',
                                 'as_of': '2026-11-12'}])

    def test_unavailable_snapshots_back_nothing(self):
        snap = {'NVDA': {**SNAP['NVDA'], 'status': 'unavailable'}}
        self.assertEqual(cite('Price 186.26', snap, []), [])

    def test_spans_do_not_change_what_the_guard_reads(self):
        for text in ('a 1,234.5 b', 'up 2.10%.', 'x 4.51 trillion, y -3', 'v1.2 and 7.'):
            self.assertEqual([v for _, _, v in number_spans(text)], numbers_in(text))
            for s, e, _ in number_spans(text):
                self.assertFalse(text[s:e].endswith(('.', ',')))


if __name__ == '__main__':
    unittest.main()
