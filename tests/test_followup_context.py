import unittest

from core.gliner_decide import GlinerQueryRouter
from core.orchestrator import DataAnalystAgent, NewsResearcherAgent, TeamLeadOrchestrator, first_paragraph
from tests.test_orchestrator import StubGliner, StubNewsStore, StubTools


class RecordingLLM:
    def __init__(self):
        self.prompts = []

    def complete(self, system, prompt, *, max_tokens=256):
        self.prompts.append((system, prompt))
        if "[Data Analyst]" in system:
            return "NVDA trades at 138.5 with a P/E of 34.2.\n\nNVDA last price: 138.5; P/E: 34.2."
        if "[News Researcher]" in system:
            return "Guidance was raised by 12% in a bullish catalyst."
        return "At 138.5, the main risk is execution on the raised guidance."


def orchestrator(llm):
    tools = StubTools()
    return TeamLeadOrchestrator(GlinerQueryRouter(StubGliner(), {"NVDA"}), DataAnalystAgent(llm, tools),
                                NewsResearcherAgent(llm, StubNewsStore()), llm, tools)


class TestFollowUpContext(unittest.TestCase):
    def test_lead_sees_the_question_and_previous_answer(self):
        llm = RecordingLLM()
        orchestrator(llm).run("What is the main risk for NVDA?", context="Earlier answer about NVDA at 138.5.")
        lead_prompt = llm.prompts[-1][1]
        self.assertIn("User question: What is the main risk for NVDA?", lead_prompt)
        self.assertIn("Conversation memory, earlier accepted answers (context only): Earlier answer", lead_prompt)

    def test_context_numbers_are_not_allowed_back_out(self):
        class Echo(RecordingLLM):
            def complete(self, system, prompt, *, max_tokens=256):
                if "[Data Analyst]" in system or "[News Researcher]" in system:
                    return super().complete(system, prompt, max_tokens=max_tokens)
                return "Earlier the price was 777.7."
        result = orchestrator(Echo()).run("Review NVDA", context="Previously NVDA was 777.7.")
        self.assertFalse(result.faithful_synthesis)
        self.assertNotIn("777.7", result.final_synthesis)

    def test_specialist_keeps_only_its_first_block(self):
        llm = RecordingLLM()
        result = orchestrator(llm).run("Review NVDA")
        self.assertEqual(result.data_report.summary, "NVDA trades at 138.5 with a P/E of 34.2.")
        self.assertEqual(first_paragraph("a\n\nb"), "a")

    def test_first_turn_has_no_context_line(self):
        llm = RecordingLLM()
        orchestrator(llm).run("Review NVDA")
        self.assertNotIn("Conversation memory", llm.prompts[-1][1])


if __name__ == "__main__":
    unittest.main()
