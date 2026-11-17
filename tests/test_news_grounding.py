"""News Researcher grounding (2026-11-17). Each test names a failure seen in the frozen 35-input news diagnostic."""
import unittest

from core.claims import attribution_problems, is_refusal, unsupported_terms
from core.numeric_guard import numbers_in, score_numbers_faithful
from core.orchestrator import ISSUER_SOURCES, EventBus, NewsResearcherAgent, clean_narration, source_label
from core.signals import Action, Item, Judgment, Outcome
from core.urgency import OFFICIAL

LINK = "local-test-item"


def outcome(title, source="hackernews", ticker="NVDA", direction="bullish", action=Action.DIGEST):
    return Outcome(Item(ticker, source, LINK, title), action, "material_not_urgent", Judgment(0.7, 0.4, direction, 0.6))


class Store:
    def __init__(self, rows):
        self.rows = rows

    def get_gated_signals(self, ticker, limit=5):
        return self.rows


class Narrator:
    """Replies in order; records every prompt."""
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, system, prompt, **kw):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def run(rows, *replies, retry=True):
    llm = Narrator(*replies)
    return NewsResearcherAgent(llm, Store(rows), retry=retry).run(["NVDA"], EventBus(), "t"), llm


class TestNumberScales(unittest.TestCase):
    def test_headline_shorthand_and_scale_words_mean_the_same(self):
        # AMZN 2026-10-28: "$11.5B" in the headline, "$11.5 billion" in the summary, rejected as 11.5 vs 11.5e9
        allowed = numbers_in("US agency approves Amazon Leo's $11.5B acquisition of Globalstar")
        self.assertEqual(allowed, [11.5e9])
        self.assertTrue(score_numbers_faithful("Regulators approved the $11.5 billion deal.", allowed))
        self.assertTrue(score_numbers_faithful("Regulators approved the $11.5B deal.", allowed))
        self.assertTrue(score_numbers_faithful("Prices top $9,000.", numbers_in("RTX 5090 Street Prices Top $9k")))

    def test_the_scale_still_has_to_match(self):
        allowed = numbers_in("$11.5B acquisition")
        for wrong in ("an 11.5 deal", "an $11.5 million deal", "an $11.5T deal"):
            with self.subTest(wrong=wrong):
                self.assertFalse(score_numbers_faithful(wrong, allowed))

    def test_model_names_and_lowercase_letters_are_not_scaled(self):
        self.assertEqual(numbers_in("M6 and Q3 and RTX 5090"), [5090.0])
        self.assertEqual(numbers_in("a 5m walk"), [5.0])
        self.assertEqual(numbers_in("4B model"), [4e9])


class TestCleanNarration(unittest.TestCase):
    def test_word_counts_think_tags_and_dashes_are_removed(self):
        text = clean_narration("<think>plan</think>NVIDIA expands with Oracle \u2014 and Meta (28 words)")
        self.assertEqual(text, "NVIDIA expands with Oracle, and Meta")
        self.assertNotIn("\u2013", clean_narration("Shares \u2013 per the release \u2013 rose."))
        self.assertEqual(clean_narration("Guidance for 2025\u20132026."), "Guidance for 2025-2026.")
        self.assertEqual(clean_narration("a 14\u2011inch $11.5\u202fbillion deal"), "a 14-inch $11.5 billion deal")


class TestAttribution(unittest.TestCase):
    def test_plural_sources_need_two_independent_headlines(self):
        # TSLA 2026-10-29: one shared link narrated as "multiple outlets citing internal changes"
        title = ["Tesla caught altering specs following rear suspension collapse reports"]
        self.assertIn("Multiple outlets", attribution_problems("Multiple outlets cite changes.", title, 1))
        self.assertEqual(attribution_problems("Two outlets disagree on the size.", title * 2, 2), [])

    def test_press_words_need_a_non_issuer_source(self):
        title = ["Example Logistics says a ransomware attack disrupted deliveries"]
        self.assertIn("media", attribution_problems("The release and media coverage say so.", title, 0))
        self.assertEqual(attribution_problems("The company says an attack disrupted deliveries.", title, 0), [])

    def test_tone_and_gate_labels_need_the_headlines_own_words(self):
        title = ["NVIDIA Unveils Minerva"]
        self.assertEqual(attribution_problems("The consensus is bullish.", title, 0), ["consensus", "bullish"])
        self.assertIn("digest", attribution_problems("Covered by the digest.", title, 0))
        self.assertEqual(attribution_problems("Analysts turn bullish.", ["Analysts turn bullish on NVIDIA"], 1), [])

    def test_refusals_are_named(self):
        self.assertTrue(is_refusal("I\u2019m sorry, but I can't help with that."))
        self.assertFalse(is_refusal("Example Bank says prosecutors allege fraud; it denies wrongdoing."))


class TestWordingEquivalents(unittest.TestCase):
    def test_seen_false_rejections_now_pass(self):
        self.assertEqual(unsupported_terms("Amazon released earnings.", "Amazon.com Announces Q3 2026 Results [pdf]"), [])
        self.assertEqual(unsupported_terms("Its CEO resigned.", "its chief executive resigned"), [])
        self.assertEqual(unsupported_terms("A model did better than GPT.", "21% better than GPT-6 Astra"), [])

    def test_escalations_are_rejected(self):
        ev = "Bitcoin Core discloses TCP out-of-memory vulnerability; China withholds import approval"
        self.assertIn("outage", unsupported_terms("The outage hit users.", ev))
        self.assertIn("ban", unsupported_terms("China imposed an import ban.", ev))
        self.assertIn("earnings", unsupported_terms("Nvidia earnings are coming.", "Nvidia announces new GeForce drivers"))


class TestNewsResearcher(unittest.TestCase):
    ROWS = [outcome("NVIDIA Unveils Minerva for AI Factories", "nvidia_newsroom"),
            outcome("Nvidia chips pile up in Hong Kong as China withholds import approval", "hackernews", direction="bearish")]

    def test_prompt_names_one_source_per_headline_without_gate_labels(self):
        report, llm = run(self.ROWS, "NVIDIA announced Minerva for AI Factories.")
        prompt = llm.prompts[0]
        self.assertIn("- NVDA, NVIDIA Newsroom, company announcement: NVIDIA Unveils Minerva", prompt)
        self.assertIn("Hacker News, shared link", prompt)
        for label in ("digest", "alert", "bullish", "bearish", "neutral"):
            self.assertNotIn(label, prompt.lower())
        self.assertTrue(report.faithful_numbers, report.validation)
        self.assertEqual(report.raw_facts["NVDA"][1]["direction"], "bearish")  # labels stay in Python's facts

    def test_one_feedback_retry_then_fallback(self):
        report, llm = run(self.ROWS, "The consensus is bullish on Minerva.", "NVIDIA announced Minerva.")
        self.assertTrue(report.faithful_numbers)
        self.assertEqual(report.summary, "NVIDIA announced Minerva.")
        self.assertEqual(report.validation["guard_retry"]["used"], "retry")
        self.assertIn("consensus", llm.prompts[1])
        report, llm = run(self.ROWS, "The consensus is bullish.", "Multiple outlets are bullish.")
        self.assertFalse(report.faithful_numbers)
        self.assertEqual(report.validation["validation_reason"], "unsupported_attribution")
        self.assertEqual(report.validation["guard_retry"]["used"], "fallback")
        self.assertEqual(len(llm.prompts), 2)

    def test_fallback_lists_sources_not_gate_labels(self):
        report, _ = run(self.ROWS, "Media say the outlook is strong.", retry=False)
        self.assertIn("NVIDIA Unveils Minerva for AI Factories (NVIDIA Newsroom, company announcement)", report.summary)
        for label in ("digest", "bullish", "bearish"):
            self.assertNotIn(label, report.summary)

    def test_each_failure_keeps_its_own_reason(self):
        self.assertEqual(run([])[0].validation["validation_reason"], "no_news")
        self.assertEqual(run(self.ROWS, "I'm sorry, I can't help with that.")[0].validation["validation_reason"], "refusal")
        self.assertEqual(run(self.ROWS, RuntimeError("down"))[0].validation["validation_reason"], "model_error")
        self.assertEqual(run(self.ROWS, "NVIDIA sold 7 chips.", "NVIDIA sold 7.")[0].validation["validation_reason"],
                         "unsupported_numeric_tokens")

    def test_every_official_feed_is_an_issuer(self):
        self.assertTrue(set(OFFICIAL) <= ISSUER_SOURCES)
        self.assertEqual(source_label("some_feed"), "some feed")


if __name__ == "__main__":
    unittest.main()
