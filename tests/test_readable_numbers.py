"""Readable numbers (2026-11-08): Python formats, the model copies, the guard checks the scale."""
import unittest

from core.numeric_guard import numbers_in, score_numbers_faithful
from core.orchestrator import DataAnalystAgent, EventBus, TeamLeadOrchestrator, display_value, market_fact_lines, market_fact_numbers
from core.gliner_decide import RoutedQuery

SNAP = {"ticker": "NVDA", "name": "NVIDIA Corporation", "currency": "USD", "status": "available", "source": "fixture",
        "price": 186.64, "volume": 179308722, "pe_ratio": 23.595449, "market_cap": 4506796080000, "revenue_ttm": 302970000000,
        "eps": 7.91, "all_time_high": 243.37, "drawdown_from_all_time_high_pct": 23.3102}


class Tools:
    charts = {}
    def get_snapshot(self, ticker):
        return dict(SNAP)
    def plot_history(self, ticker):
        return ""


class Says:
    def __init__(self, text):
        self.text, self.prompts = text, []
    def complete(self, system, prompt, **kw):
        self.prompts.append(prompt)
        return self.text


class TestScaleAwareGuard(unittest.TestCase):
    def test_scale_words_are_part_of_the_number(self):
        self.assertEqual(numbers_in("cap 4.51 trillion, volume 179.31 million, P/E 23.60, 12%"), [4.51e12, 179.31e6, 23.6, 12.0])
        self.assertEqual(numbers_in("the M6 chip and 14-inch model"), [14.0])  # names with digits are not scaled

    def test_wrong_scale_is_rejected(self):
        allowed = market_fact_numbers("NVDA", SNAP)
        self.assertTrue(score_numbers_faithful("Market cap is 4.51 trillion USD.", allowed))
        self.assertFalse(score_numbers_faithful("Market cap is 4.51 billion USD.", allowed))
        self.assertFalse(score_numbers_faithful("Market cap is 4.51 USD.", allowed))
        self.assertFalse(score_numbers_faithful("Market cap is 4,506,796,080,000 USD.", allowed))  # not what was shown

    def test_python_formats_and_never_the_model(self):
        self.assertEqual(display_value("market_cap", 4506796080000), ("4.51 trillion", 4.51e12))
        self.assertEqual(display_value("revenue_ttm", 302970000000)[0], "302.97 billion")
        self.assertEqual(display_value("volume", 179308722)[0], "179.31 million")
        self.assertEqual(display_value("volume", 950000)[0], "950,000")  # small counts stay exact
        self.assertEqual(display_value("pe_ratio", 23.595449), ("23.60", 23.6))
        self.assertEqual(display_value("price", 186.64)[0], "186.64")  # prices as the source wrote them
        lines = market_fact_lines("NVDA", SNAP)
        self.assertIn("Market cap: 4.51 trillion USD", lines)
        self.assertIn("Session volume: 179.31 million", lines)


class TestAnalystUsesReadableFacts(unittest.TestCase):
    def test_readable_narration_passes_and_wrong_scale_falls_back(self):
        good = DataAnalystAgent(Says("NVIDIA trades at 186.64 USD with a market cap of 4.51 trillion USD and a P/E of 23.60."), Tools())
        report = good.run(["NVDA"], "equity", EventBus(), "s")
        self.assertTrue(report.faithful_numbers)
        bad = DataAnalystAgent(Says("NVIDIA trades at 186.64 USD with a market cap of 4.51 billion USD."), Tools())
        report = bad.run(["NVDA"], "equity", EventBus(), "s")
        self.assertFalse(report.faithful_numbers)
        self.assertIn(4.51e9, report.validation["rejected_numbers"])


class TestDirection(unittest.TestCase):
    UP = {**SNAP, "previous_session_close": 181.6, "change_from_previous_session_pct": 2.7753}

    def test_the_live_wrong_direction_is_rejected_and_the_right_one_passes(self):
        from core.claims import direction_conflicts
        live = "NVDA thesis: last price 186.64 USD (down from 181.6 USD), one-year high 243.37 USD."
        self.assertEqual(direction_conflicts(live, [181.6], "up"), ["down"])
        self.assertEqual(direction_conflicts("NVDA rose 2.78 percent from 181.6 USD to 186.64 USD.", [181.6], "up"), [])
        self.assertEqual(direction_conflicts("It fell since the previous session close.", [181.6], "up"), ["fell"])
        self.assertEqual(direction_conflicts("Shares are up from the previous close.", [181.6], "down"), ["up"])
        self.assertEqual(direction_conflicts("The one-year low is 164.27 USD and the price is lower than the high.", [181.6], "up"), [])  # not about the previous close

    def test_change_is_a_word_and_a_size(self):
        self.assertEqual(display_value("change_from_previous_session_pct", 2.7753), ("up 2.78", 2.78))
        self.assertEqual(display_value("change_from_previous_session_pct", -1.2), ("down 1.20", 1.2))
        self.assertIn("Change since previous session close (percent): up 2.78", market_fact_lines("NVDA", self.UP))

    def test_analyst_with_the_wrong_direction_falls_back(self):
        class UpTools(Tools):
            def get_snapshot(s, ticker):
                return dict(TestDirection.UP)
        bad = DataAnalystAgent(Says("NVIDIA last price 186.64 USD, down from 181.6 USD."), UpTools()).run(["NVDA"], "equity", EventBus(), "s")
        self.assertFalse(bad.faithful_numbers)
        self.assertEqual(bad.validation["direction_conflicts"], ["down"])
        good = DataAnalystAgent(Says("NVIDIA last price 186.64 USD, up 2.78 percent from 181.6 USD."), UpTools()).run(["NVDA"], "equity", EventBus(), "s")
        self.assertTrue(good.faithful_numbers)


class TestFollowUpPrompt(unittest.TestCase):
    def lead(self, reply):
        class Router:
            def route(self, q):
                return RoutedQuery("thesis_review", 0.9, "swing_week", ["NVDA"])
        class News:
            def get_gated_signals(self, *a, **k):
                return []
        from core.orchestrator import NewsResearcherAgent
        llm = Says(reply)
        return TeamLeadOrchestrator(Router(), DataAnalystAgent(Says("NVIDIA trades at 186.64 USD."), Tools()),
                                    NewsResearcherAgent(Says(""), News()), llm, Tools()), llm

    def test_follow_up_instruction_comes_last_and_restatements_are_counted(self):
        orch, llm = self.lead("The earlier view: NVIDIA trades at 186.64 USD.")
        result = orch.run("Back to NVDA, summarize your view", context="Earlier: NVIDIA trades at 186.64 USD.")
        self.assertTrue(llm.prompts[-1].rstrip().endswith("only ones the question asks for."))
        gate = [e for e in result.events if e.event_type == "GATE_CHECK" and e.agent == "Market Team Lead"][-1]
        self.assertEqual((gate.payload["follow_up"], gate.payload["restated_numbers"]), (True, 1))

    def test_first_turn_has_no_follow_up_task(self):
        orch, llm = self.lead("NVIDIA trades at 186.64 USD.")
        result = orch.run("Give me an NVDA thesis")
        self.assertNotIn("this is a follow-up", llm.prompts[-1])
        gate = [e for e in result.events if e.event_type == "GATE_CHECK" and e.agent == "Market Team Lead"][-1]
        self.assertNotIn("restated_numbers", gate.payload)


class Sequence:
    """Lead narrator that answers with scripted replies in order and records every prompt."""
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []
    def complete(self, system, prompt, **kw):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else ""


class TestStrictFollowUps(unittest.TestCase):
    ANALYST = "NVIDIA trades at 186.64 USD with a market cap of 4.51 trillion USD, a P/E of 23.60 and EPS of 7.91 USD."
    EARLIER = "Earlier: NVIDIA trades at 186.64 USD, market cap 4.51 trillion USD, P/E 23.60, EPS 7.91 USD."
    RESTATES = "NVIDIA trades at 186.64 USD with a market cap of 4.51 trillion USD, P/E 23.60 and EPS 7.91 USD."

    def run_lead(self, *replies, context=EARLIER):
        class Router:
            def route(self, q):
                return RoutedQuery("price_snapshot", 0.9, "swing_week", ["NVDA"])
        lead = Sequence(*replies)
        orch = TeamLeadOrchestrator(Router(), DataAnalystAgent(Says(self.ANALYST), Tools()), None, lead, Tools())
        result = orch.run("Back to NVDA, what is your view?", context=context)
        gate = [e for e in result.events if e.event_type == "GATE_CHECK" and e.agent == "Market Team Lead"][-1].payload
        return result, gate, lead

    def test_a_restating_follow_up_is_retried_once_and_the_shorter_answer_is_used(self):
        result, gate, lead = self.run_lead(self.RESTATES, "The view is unchanged: NVIDIA still trades at 186.64 USD.")
        self.assertEqual(len(lead.prompts), 2)
        self.assertTrue(lead.prompts[1].endswith(TeamLeadOrchestrator.STRICT_FOLLOW_UP))
        self.assertEqual(result.final_synthesis, "The view is unchanged: NVIDIA still trades at 186.64 USD.")
        self.assertEqual(gate["follow_up_retry"], {"first_restated": 4, "retry_restated": 1, "retry_passed_guards": True, "used": "retry"})
        self.assertTrue(result.faithful_synthesis)

    def test_a_retry_that_fails_a_guard_never_replaces_the_faithful_first_answer(self):
        result, gate, _ = self.run_lead(self.RESTATES, "NVIDIA trades at 999 USD.")  # invented number
        self.assertEqual(result.final_synthesis, self.RESTATES)
        self.assertEqual((gate["follow_up_retry"]["used"], gate["follow_up_retry"]["retry_passed_guards"]), ("first", False))
        self.assertEqual(gate["follow_up_retry"]["retry_reason"], "unsupported_numeric_tokens")

    def test_a_retry_that_is_not_shorter_is_not_used(self):
        result, gate, _ = self.run_lead(self.RESTATES, self.RESTATES.replace("NVIDIA", "Nvidia"))
        self.assertEqual(gate["follow_up_retry"]["used"], "first")

    def test_a_short_follow_up_and_a_first_turn_are_never_retried(self):
        _, gate, lead = self.run_lead("NVIDIA still trades at 186.64 USD.")
        self.assertEqual((len(lead.prompts), "follow_up_retry" in gate), (1, False))
        _, gate, lead = self.run_lead(self.RESTATES, context="")
        self.assertEqual((len(lead.prompts), "follow_up_retry" in gate), (1, False))


class TestNoStdlibShadowing(unittest.TestCase):
    def test_no_core_module_shadows_the_standard_library(self):
        """core/hymemory_worker.py and core/router_worker.py run as scripts, so core/ is first on their import path.
        A core/numbers.py (2026-11-08) replaced the stdlib numbers module there and numpy crashed the worker."""
        import sys
        from pathlib import Path
        core = Path(__file__).resolve().parents[1] / "core"
        # core/trace.py predates this check; the stdlib trace module is a debugging tool no dependency here imports.
        # Kept as a named exception (TODO: rename when the trace module is next touched), never a silent pass.
        known = {"trace"}
        clash = sorted(p.stem for p in core.glob("*.py") if p.stem in sys.stdlib_module_names and p.stem not in known)
        self.assertEqual(clash, [])


if __name__ == "__main__":
    unittest.main()
