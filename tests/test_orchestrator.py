import unittest
from core.gliner_decide import GlinerQueryRouter
from core.orchestrator import (
    DataAnalystAgent,
    EventBus,
    NewsResearcherAgent,
    TeamLeadOrchestrator,
)
from core.signals import Action, Item, Judgment, Outcome


class StubGliner:
    def classify_text(self, text, schema, *, include_confidence=False):
        if "news" in text.lower():
            return {"intent": {"label": "news_catalysts", "confidence": 0.92}, "horizon": "swing_week"}
        return {"intent": {"label": "thesis_review", "confidence": 0.94}, "horizon": "swing_week"}

    def extract_entities(self, text, entity_types):
        if "Tesla" in text:
            return {"entities": {"company": ["Tesla"]}}
        return {"entities": {}}


class StubTools:
    def __init__(self):
        self.watchlist = []
        self.charts = []

    def get_snapshot(self, ticker):
        if ticker == "BTC":
            return {"price": 62500.0, "fear_greed": 81.0, "ath_drawdown_pct": 12.0}
        return {"price": 138.5, "pe_ratio": 34.2}

    def plot_history(self, ticker):
        ref = f"chart://{ticker}"
        self.charts.append(ref)
        return ref

    def add_to_watchlist(self, ticker):
        if ticker not in self.watchlist:
            self.watchlist.append(ticker)


class StubNewsStore:
    def get_gated_signals(self, ticker, limit=5):
        return [
            Outcome(
                item=Item(ticker, "rss", "https://x/1", f"{ticker} raises Q4 revenue guidance by 12%"),
                action=Action.ALERT,
                reason="material_and_urgent",
                judgment=Judgment(material=0.92, urgent=0.81, direction="bullish", direction_p=0.88),
            )
        ]


class Stub1BLLM:
    def __init__(self, hallucinate_lead=False):
        self.hallucinate_lead = hallucinate_lead
        self.calls = []

    def complete(self, system, prompt, *, max_tokens=256):
        self.calls.append(system)
        if "📊 [Data Analyst]" in system:
            if "62500.0" in prompt:
                return "<think>crypto mode</think>📊 [Data Analyst] BTC trades at 62500.0 with Fear & Greed at 81.0 and drawdown at 12.0%."
            return "📊 [Data Analyst] Shares trade at 138.5 with a P/E of 34.2."
        if "📰 [News Researcher]" in system:
            return "📰 [News Researcher] Guidance was raised by 12% in a bullish catalyst."
        if self.hallucinate_lead:
            return "🧠 [Market Team Lead] Shares jumped 999% to 5000.0!"
        return "🧠 [Market Team Lead] At 138.5 (P/E 34.2), the 12% guidance raise supports a constructive view."


class TestMultiAgentOrchestrator(unittest.TestCase):
    def test_full_three_agent_thesis_flow_and_a2a_events(self):
        bus = EventBus()
        tools = StubTools()
        llm = Stub1BLLM()
        router = GlinerQueryRouter(StubGliner(), universe={"TSLA", "NVDA", "BTC"})
        orch = TeamLeadOrchestrator(
            router=router,
            data_agent=DataAnalystAgent(llm, tools),
            news_agent=NewsResearcherAgent(llm, StubNewsStore()),
            lead_llm=llm,
            tools=tools,
            bus=bus,
        )
        res = orch.run("Give me a full thesis review on Tesla and NVDA", session_id="s1")
        self.assertEqual(res.routed.tickers, ["NVDA", "TSLA"])
        self.assertEqual(res.risk_mode, "fundamental_equity")
        self.assertEqual(res.delegated_agents, ["Data Analyst", "News Researcher"])
        self.assertEqual(tools.watchlist, ["NVDA", "TSLA"])
        self.assertEqual(tools.charts, ["chart://NVDA", "chart://TSLA"])
        self.assertTrue(res.faithful_synthesis)
        # Verify all 3 agents ran with their own isolated system prompts
        self.assertEqual(len(llm.calls), 3)
        event_types = [e.event_type for e in res.events]
        self.assertIn("EXECUTIVE_ROUTE", event_types)
        self.assertIn("AGENT_DELEGATION", event_types)
        self.assertIn("AGENT_RESULT", event_types)
        self.assertIn("GATE_CHECK", event_types)

    def test_crypto_activates_risk_guardian_mode(self):
        tools = StubTools()
        llm = Stub1BLLM()
        router = GlinerQueryRouter(StubGliner(), universe={"BTC"})
        orch = TeamLeadOrchestrator(
            router=router,
            data_agent=DataAnalystAgent(llm, tools),
            news_agent=NewsResearcherAgent(llm, StubNewsStore()),
            lead_llm=llm,
            tools=tools,
        )
        res = orch.run("Thesis on BTC", session_id="s2")
        self.assertEqual(res.risk_mode, "risk_guardian_crypto")
        self.assertIn("62500.0", res.data_report.summary)

    def test_hallucinated_lead_number_triggers_fallback(self):
        tools = StubTools()
        llm = Stub1BLLM(hallucinate_lead=True)
        router = GlinerQueryRouter(StubGliner(), universe={"NVDA"})
        orch = TeamLeadOrchestrator(
            router=router,
            data_agent=DataAnalystAgent(llm, tools),
            news_agent=NewsResearcherAgent(llm, StubNewsStore()),
            lead_llm=llm,
            tools=tools,
        )
        res = orch.run("Review NVDA", session_id="s3")
        self.assertFalse(res.faithful_synthesis)
        self.assertNotIn("999%", res.final_synthesis)
        self.assertIn("138.5", res.final_synthesis)


if __name__ == "__main__":
    unittest.main()
