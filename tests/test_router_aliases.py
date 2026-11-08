import unittest

from core.gliner_decide import GlinerQueryRouter


class NoEntities:
    """Reproduces the 2026-11-08 live case: intent classified, NER returned no spans."""
    def classify_text(self, text, schema, include_confidence=False):
        return {'intent': {'label': 'news_catalysts', 'confidence': 0.61}, 'horizon': {'label': 'intraday', 'confidence': 0.45}}

    def extract_entities(self, text, labels):
        return {'entities': {'company': [], 'cryptocurrency': [], 'ticker': []}}


class TestRouterAliasBackstop(unittest.TestCase):
    def router(self):
        return GlinerQueryRouter(NoEntities(), {'AAPL', 'NVDA', 'TSLA', 'AMD'})

    def test_company_name_resolves_when_ner_finds_nothing(self):
        self.assertEqual(self.router().route('What is the latest Apple news?').tickers, ['AAPL'])

    def test_explicit_ticker_keeps_order_and_no_duplicates(self):
        self.assertEqual(self.router().route('Compare NVDA with Nvidia and Apple').tickers, ['NVDA', 'AAPL'])

    def test_alias_must_be_a_whole_word(self):
        self.assertEqual(self.router().route('Is pineapple demand a theme?').tickers, [])
        self.assertEqual(self.router().route('Any news on teslas factory?').tickers, [])

    def test_alias_outside_universe_is_ignored(self):
        self.assertEqual(self.router().route('What about Microsoft?').tickers, [])


if __name__ == '__main__':
    unittest.main()
