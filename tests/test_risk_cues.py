"""Closed risk list (2026-11-08). Phrases below are copied from real Lead answers that invented risks."""
import unittest

from core.gliner_decide import RoutedQuery
from core.orchestrator import DataAnalystAgent, EventBus, NewsResearcherAgent, TeamLeadOrchestrator
from core.risk_cues import cue_block, risk_cues, unsupported_risks

NVDA = {"ticker": "NVDA", "name": "NVIDIA Corporation", "currency": "USD", "status": "available", "price": 186.64,
        "previous_session_close": 181.6, "change_from_previous_session_pct": 2.7753, "all_time_high": 243.37,
        "drawdown_from_all_time_high_pct": 23.3102, "fundamentals_status": "Available", "fundamentals_as_of": {"pe_ratio": {}}}
NEWS = {"NVDA": [{"title": "NVIDIA unveils Minerva observability", "source": "nvidia_newsroom", "direction": "bullish"}]}


class TestCues(unittest.TestCase):
    def test_cues_come_from_facts_only(self):
        texts = [c["text"] for c in risk_cues({"NVDA": NVDA}, NEWS)]
        self.assertEqual(texts, ["NVDA trades well below its all-time high",
                                 "the news comes only from company announcements, which are issuer claims"])
        down = {**NVDA, "change_from_previous_session_pct": -1.2, "fundamentals_as_of": {}}
        texts = [c["text"] for c in risk_cues({"NVDA": down}, {})]
        self.assertIn("NVDA is down since the previous session close", texts)
        self.assertIn("fundamentals for NVDA are not available from the source", texts)
        btc = {"ticker": "BTC", "price": 1.0, "fear_greed_index": 81, "annualized_daily_log_volatility_pct": 72.0}
        texts = [c["text"] for c in risk_cues({"BTC": btc}, {})]
        self.assertIn("crypto market sentiment may be overheated (Fear and Greed index)", texts)
        self.assertIn("BTC has large daily price swings (annualized volatility)", texts)
        bear = {"AAPL": [{"title": "Apple halts iPhone production in China", "source": "hn", "direction": "bearish"}]}
        self.assertEqual([c["text"] for c in risk_cues({}, bear)], ["a bearish headline: Apple halts iPhone production in China"])

    def test_cue_text_adds_no_numbers(self):
        block = cue_block(risk_cues({"NVDA": {**NVDA, "fundamentals_as_of": {}}}, NEWS))
        self.assertFalse(any(ch.isdigit() for ch in block))
        self.assertIn("do not identify a specific one", cue_block([]))


class TestUnsupportedRisks(unittest.TestCase):
    CUES = risk_cues({"NVDA": NVDA}, NEWS)

    def test_real_invented_risks_are_caught(self):
        for text in ("The main risk supported by the data/news sources is potential supply-chain or earnings volatility despite the bullish hardware narrative.",
                     "Main risk: execution uncertainty amid rapid product rollout and partnership dependencies.",
                     "Key risks include a potential pullback toward the 164 low and elevated short-interest pressure.",
                     "while execution uncertainty amid rapid rollout and partnership dependencies remains the main risk."):
            self.assertTrue(unsupported_risks(text, self.CUES), text)

    def test_a_supported_half_cannot_carry_an_invented_half(self):
        bad = unsupported_risks("Main risk: drawdown from its all-time high and potential execution gaps despite the narrative.", self.CUES)
        self.assertEqual(bad, ["execution gaps"])  # leading filler ("potential") is dropped from the fed-back text

    def test_supported_and_negated_risks_pass(self):
        for text in ("The main risk highlighted is the drawdown from its all-time high.",
                     "Main risk: NVDA trades well below its all-time high.",
                     "A risk is that the news comes only from company announcements.",
                     "The reports do not identify a specific risk.",
                     "NVIDIA trades at 186.64 USD with bullish headlines."):  # no risk statement at all
            self.assertEqual(unsupported_risks(text, self.CUES), [], text)

    def test_talk_about_the_list_is_not_a_risk(self):
        self.assertEqual(unsupported_risks("The main risk matches the listed one: NVDA trades well below its all-time high.", self.CUES), [])
        self.assertEqual(unsupported_risks("The main risk is execution gaps, which matches the listed ones.", self.CUES), ["execution gaps"])

    def test_a_risk_from_another_asset_is_not_supported(self):
        apple = risk_cues({}, {"AAPL": [{"title": "Apple reports fourth quarter results", "source": "apple_newsroom", "direction": "neutral"}]})
        self.assertTrue(unsupported_risks("The main risk is lack of fundamental data.", apple))  # live: NVDA's risk leaked into Apple


class Tools:
    charts = {}
    def get_snapshot(self, ticker):
        return dict(NVDA)
    def plot_history(self, ticker):
        return ""


class Says:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []
    def complete(self, system, prompt, **kw):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class TestLeadUsesTheList(unittest.TestCase):
    def run_lead(self, *replies):
        class Router:
            def route(self, q):
                return RoutedQuery("price_snapshot", 0.9, "swing_week", ["NVDA"])
        lead = Says(*replies)
        orch = TeamLeadOrchestrator(Router(), DataAnalystAgent(Says("NVIDIA trades at 186.64 USD."), Tools()), None, lead, Tools())
        result = orch.run("Give me an NVDA thesis")
        gate = [e for e in result.events if e.event_type == "GATE_CHECK" and e.agent == "Market Team Lead"][-1].payload
        return result, gate, lead

    def test_the_prompt_lists_the_risks(self):
        _, _, lead = self.run_lead("NVIDIA trades at 186.64 USD.")
        self.assertIn("Risks the reports support (name only these, or none):\n- NVDA trades well below its all-time high", lead.prompts[0])

    def test_an_invented_risk_gets_one_strict_retry(self):
        result, gate, lead = self.run_lead("NVIDIA trades at 186.64 USD; main risk is supply-chain disruption.",
                                           "NVIDIA trades at 186.64 USD; the main risk is that it trades well below its all-time high.")
        self.assertEqual(len(lead.prompts), 2)
        self.assertIn(TeamLeadOrchestrator.STRICT_RISK, lead.prompts[1])
        self.assertTrue(lead.prompts[1].rstrip().endswith("Remove: supply-chain disruption."))  # names what was rejected
        self.assertTrue(result.faithful_synthesis)
        self.assertIn("all-time high", result.final_synthesis)
        self.assertEqual((gate["guard_retry"]["reason"], gate["guard_retry"]["used"]), ("unsupported_risk", "retry"))

    def test_a_retry_that_still_invents_falls_back_to_the_reports(self):
        result, gate, lead = self.run_lead("NVIDIA trades at 186.64 USD; main risk is supply-chain disruption.")
        self.assertEqual(len(lead.prompts), 2)  # one retry, never more
        self.assertFalse(result.faithful_synthesis)
        self.assertNotIn("supply-chain", result.final_synthesis)
        self.assertEqual((gate["guard_retry"]["used"], gate["guard_retry"]["retry_reason"]), ("fallback", "unsupported_risk"))

    def test_a_rejected_word_is_fed_back_to_the_retry(self):
        result, gate, lead = self.run_lead("NVIDIA trades at 186.64 USD after the Blackwell launch.", "NVIDIA trades at 186.64 USD.")
        self.assertIn("do not use them: Blackwell", lead.prompts[1])
        self.assertTrue(result.faithful_synthesis)
        self.assertEqual(gate["guard_retry"], {"reason": "unsupported_terms", "rejected": ["Blackwell"], "retry_passed_guards": True, "used": "retry"})

    def test_ath_is_shorthand_for_the_all_time_high(self):
        from core.claims import unsupported_terms
        # Evidence uses the real report labels (FACT_LABELS), so "Drawdown" is in it either way.
        ath = "All-time high in source history: 243.37 USD\nDrawdown from all-time high (percent): 23.31"
        one_year = "One-year high: 243.37 USD\nDrawdown from one-year high (percent): 23.31"
        self.assertEqual(unsupported_terms("Drawdown from ATH is 23.31 percent.", ath), [])
        self.assertEqual(unsupported_terms("Drawdown from ATH is 23.31 percent.", one_year), ["ATH"])


if __name__ == "__main__":
    unittest.main()
