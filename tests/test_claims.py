import unittest

from core.claims import unsupported_terms
from core.orchestrator import DataAnalystAgent, EventBus, NewsResearcherAgent, clean_narration
from core.signals import Action, Item, Judgment, Outcome

EVIDENCE = ("NVIDIA Corporation (NVDA)\nLast price: 186.64 USD\nSession volume: 179,308,722\n"
            "- NVDA: How Companies Are Using NVIDIA DSX to Break the Power Bottleneck (source: nvidia newsroom; digest; bullish)\n"
            "- NVDA: NVIDIA Unveils Minerva to Bring End-to-End Observability for AI Factories (source: nvidia newsroom; digest; bullish)")


class TestWordingCheck(unittest.TestCase):
    def test_supported_paraphrase_passes(self):
        self.assertEqual(unsupported_terms("Headlines are bullish: NVIDIA's DSX and Minerva target AI factories. "
                                           "The last price is 186.64 USD.", EVIDENCE), [])

    def test_invented_names_and_claims_are_caught(self):
        for text, term in (("NVIDIA's new DSX chips break the bottleneck.", "chips"),
                           ("NVDA trades at 186.64 USD with high volume.", "high volume"),
                           ("Microsoft joined the Minerva launch.", "Microsoft"),
                           ("NVIDIA beat earnings.", "earnings")):
            with self.subTest(text=text):
                self.assertIn(term, unsupported_terms(text, EVIDENCE))

    def test_plural_claim_words_are_caught(self):
        self.assertIn("upgrade", unsupported_terms("Two upgrades lifted sentiment.", EVIDENCE))

    def test_negated_claims_are_allowed(self):
        self.assertEqual(unsupported_terms("No earnings data is available from this source.", EVIDENCE), [])

    def test_plural_and_possessive_forms_match(self):
        self.assertEqual(unsupported_terms("Minerva's observability targets AI Factory buyers.", EVIDENCE + " buyer"), [])

    def test_role_tag_and_markdown_are_removed(self):
        self.assertEqual(clean_narration("\U0001F4CA [Data Analyst] **NVDA** at 186.64.\n\nRepeat."), "NVDA at 186.64.")


class TestFinanceShorthand(unittest.TestCase):
    """Live 2026-11-08: five of five 'latest Apple news' answers fell back on 'Q4' and 'earnings' for this headline."""
    EVIDENCE = ("Accepted headlines from the local news store:\n"
                "- AAPL: Apple introduces the new 14-inch MacBook Pro with M6 (source: apple newsroom; digest; bullish)\n"
                "- AAPL: Apple reports fourth quarter results (source: apple newsroom; digest; neutral)")

    def test_the_live_apple_narration_now_passes(self):
        live = ("Apple\u2019s recent coverage highlights a new 14\u2011inch MacBook Pro with M6 and reports Q4 results. "
                "The tone is product\u2011focused, with only neutral notes on earnings.")
        self.assertEqual(unsupported_terms(live, self.EVIDENCE), [])

    def test_shorthand_never_stretches_to_other_claims(self):
        self.assertEqual(unsupported_terms("Apple reported Q3 results.", self.EVIDENCE), ["Q3"])  # wrong quarter
        self.assertIn("revenue", unsupported_terms("Apple grew revenue in the quarter.", self.EVIDENCE))
        self.assertIn("beat", unsupported_terms("Apple results beat expectations.", self.EVIDENCE))
        self.assertIn("guidance", unsupported_terms("Apple raised guidance.", self.EVIDENCE))
        nvda = "- NVDA: Nvidia announces new GeForce drivers (source: nvidia newsroom; digest; neutral)"
        self.assertIn("earnings", unsupported_terms("Nvidia earnings are coming.", nvda))  # no results headline
        self.assertIn("Q4", unsupported_terms("Nvidia Q4 looks strong.", nvda))

    def test_written_out_quarter_matches_shorthand_evidence(self):
        ev = "- AAPL: Apple Q4 results announced (source: apple newsroom; digest; neutral)"
        self.assertEqual(unsupported_terms("Apple shared its Q4 earnings.", ev), [])


class TestPipelineUsesWordingCheck(unittest.TestCase):
    def test_news_narration_with_invented_category_falls_back(self):
        class Store:
            def get_gated_signals(self, ticker, limit=5):
                return [Outcome(Item(ticker, "nvidia_newsroom", "https://nvidianews.nvidia.com/x",
                                     "How Companies Are Using NVIDIA DSX to Break the Power Bottleneck"),
                                Action.DIGEST, "material_not_urgent", Judgment(0.7, 0.4, "bullish", 0.6))]
        class Narrator:
            def complete(self, *a, **k):
                return "New DSX chips break the power bottleneck."
        report = NewsResearcherAgent(Narrator(), Store()).run(["NVDA"], EventBus(), "t")
        self.assertFalse(report.faithful_numbers)
        self.assertEqual(report.validation["validation_reason"], "unsupported_terms")
        self.assertIn("chips", report.validation["unsupported_terms"])
        self.assertIn("How Companies Are Using NVIDIA DSX", report.summary)

    def test_clean_data_narration_still_passes(self):
        class Tools:
            def get_snapshot(self, ticker):
                return {"price": 186.64, "currency": "USD", "name": "NVIDIA Corporation", "status": "available"}
            def plot_history(self, ticker):
                return ""
        class Narrator:
            def complete(self, *a, **k):
                return "\U0001F4CA [Data Analyst] NVIDIA Corporation (NVDA) last traded at 186.64 USD."
        report = DataAnalystAgent(Narrator(), Tools()).run(["NVDA"], "fundamental_equity", EventBus(), "t")
        self.assertTrue(report.faithful_numbers, report.validation)
        self.assertEqual(report.validation["unsupported_terms"], [])


if __name__ == "__main__":
    unittest.main()
