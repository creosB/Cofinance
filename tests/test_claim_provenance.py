import unittest

from core.claim_provenance import check_claim_provenance
from core.gliner_decide import RoutedQuery
from core.orchestrator import (
    DataAnalystAgent,
    NewsResearcherAgent,
    TeamLeadOrchestrator,
)


class _ScriptLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system_prompt, user_prompt, max_tokens=256, **kwargs):
        self.prompts.append(user_prompt)
        return self.replies.pop(0) if self.replies else ""


class _StubRouter:
    def __init__(self, tickers, intent="price_snapshot"):
        self._tickers = list(tickers)
        self._intent = intent

    def route(self, query):
        return RoutedQuery(
            intent=self._intent,
            intent_confidence=0.95,
            tickers=list(self._tickers),
            horizon="intraday",
        )


class _StubTools:
    def __init__(self, snaps):
        self.snaps = snaps

    def get_snapshot(self, ticker):
        return dict(self.snaps[ticker])

    def plot_history(self, ticker):
        return f"chart:{ticker}"


class _EmptyNews:
    def get_gated_signals(self, ticker, limit=5):
        return []


SAMPLE_FACTS = {
    "NVDA": {
        "name": "NVIDIA Corporation",
        "ticker": "NVDA",
        "currency": "USD",
        "price": 135.0,
        "previous_session_close": 137.9,
        "change_from_previous_session_pct": -2.1,
        "high_52_week": 150.0,
        "low_52_week": 95.0,
        "all_time_high": 190.0,
        "drawdown_from_all_time_high_pct": 28.95,
        "drawdown_from_52_week_high_pct": 10.0,
        "annualized_daily_log_volatility_pct": 44.5,
        "pe_ratio": 42.25,
        "forward_pe_ratio": 31.5,
        "eps": 3.2,
        "market_cap": 3_300_000_000_000,
        "revenue_ttm": 130_500_000_000,
        "volume": 52_400_000,
        "source": "Yahoo Finance",
    },
    "AAPL": {
        "name": "Apple Inc.",
        "ticker": "AAPL",
        "currency": "USD",
        "price": 225.0,
        "previous_session_close": 225.0,
        "change_from_previous_session_pct": 0.0,
        "high_52_week": 240.0,
        "low_52_week": 170.0,
        "all_time_high": 240.0,
        "pe_ratio": 31.5,
        "forward_pe_ratio": 28.0,
        "market_cap": 3_420_000_000_000,
        "revenue_ttm": 391_000_000_000,
        "source": "Yahoo Finance",
    },
}


class TestClaimProvenance(unittest.TestCase):
    def test_metric_misbinding_cases_rejected(self):
        cases = [
            ("NVDA has an all-time high of $150.", "all_time_high"),
            ("NVDA last price is $190.", "price"),
            ("NVDA current price is $137.9.", "price"),
            ("NVDA annualized volatility is 28.95%.", "volatility"),
            ("NVDA forward P/E is 42.25.", "forward_pe"),
            ("NVDA trailing P/E is 31.5.", "trailing_pe"),
            ("NVDA market cap is 130.5 billion USD.", "market_cap"),
            ("NVDA 52-week high is $95.", "52_week_high"),
            ("NVDA 52-week low is $150.", "52_week_low"),
        ]
        for text, cue in cases:
            with self.subTest(text=text):
                conflicts = check_claim_provenance(text, {"NVDA": SAMPLE_FACTS["NVDA"]})
                self.assertEqual(len(conflicts), 1, f"Expected 1 conflict for {text!r}, got {conflicts}")
                self.assertEqual(conflicts[0]["kind"], "metric_misbinding")
                self.assertEqual(conflicts[0]["claim"]["cue"], cue)

    def test_duplicate_values_across_fields_or_assets_accepted(self):
        ok_texts = [
            "AAPL last price is $225 and previous session close is $225.",
            "AAPL 52-week high is $240 and all-time high is $240.",
            "AAPL trailing P/E is 31.5.",
            "NVDA forward P/E is 31.5.",
            "NVDA trades at $135, down 2.1% from its previous close of $137.9, with a 52-week high of $150 and an all-time high of $190.",
            "NVDA market cap is 3.3 trillion USD and revenue is 130.5 billion USD.",
            "NVDA trades between $95 and $150.",
            "NVDA is at $135 P/E 42.25.",
        ]
        for text in ok_texts:
            with self.subTest(text=text):
                conflicts = check_claim_provenance(text, SAMPLE_FACTS)
                self.assertEqual(conflicts, [], f"Unexpected false positive for {text!r}: {conflicts}")

    def test_cross_asset_misattribution_caught_while_multi_asset_comparison_passes(self):
        bad = check_claim_provenance("AAPL trades at $135.", SAMPLE_FACTS)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["kind"], "asset_misattribution")
        self.assertEqual(bad[0]["claim"]["asset"], "AAPL")
        self.assertIn("asset", bad[0]["violations"])

        bad_by_name = check_claim_provenance("Apple last price is $135 USD.", SAMPLE_FACTS)
        self.assertEqual(len(bad_by_name), 1)
        self.assertEqual(bad_by_name[0]["kind"], "asset_misattribution")

        good_pair = check_claim_provenance(
            "NVDA last price is $135 USD while AAPL last price is $225 USD.",
            SAMPLE_FACTS,
        )
        self.assertEqual(good_pair, [])

        swapped_pair = check_claim_provenance(
            "NVDA last price is $225 USD while AAPL last price is $135 USD.",
            SAMPLE_FACTS,
        )
        self.assertEqual(len(swapped_pair), 2)
        self.assertTrue(all(c["kind"] == "asset_misattribution" for c in swapped_pair))

    def test_unit_mismatch_caught(self):
        bad_pct = check_claim_provenance("NVDA is at 135%.", {"NVDA": SAMPLE_FACTS["NVDA"]})
        self.assertEqual(len(bad_pct), 1)
        self.assertIn("unit", bad_pct[0]["violations"])

        bad_usd_pe = check_claim_provenance("NVDA trailing P/E is $42.25.", {"NVDA": SAMPLE_FACTS["NVDA"]})
        self.assertEqual(len(bad_usd_pe), 1)
        self.assertEqual(bad_usd_pe[0]["kind"], "unit_mismatch")

        bad_vol_pct = check_claim_provenance("NVDA session volume was 52.4 million percent.", {"NVDA": SAMPLE_FACTS["NVDA"]})
        self.assertEqual(len(bad_vol_pct), 1)
        self.assertEqual(bad_vol_pct[0]["kind"], "unit_mismatch")

    def test_protected_spans_and_other_numbers_exempt_non_market_quotes(self):
        text = 'Quoted notice: "all-time high of $150".'
        idx = text.index("$150")
        self.assertEqual(
            check_claim_provenance(text, {"NVDA": SAMPLE_FACTS["NVDA"]}, protected_spans=[(idx, idx + 4)]),
            [],
        )
        self.assertEqual(
            check_claim_provenance("Headline mentions 135% growth.", {"NVDA": SAMPLE_FACTS["NVDA"]}, other_numbers=[135.0]),
            [],
        )

    def test_data_analyst_and_team_lead_retry_on_claim_provenance_conflict(self):
        tools = _StubTools({"NVDA": SAMPLE_FACTS["NVDA"]})
        data_llm = _ScriptLLM(["NVIDIA has an all-time high of 150 USD."])
        data_agent = DataAnalystAgent(data_llm, tools)
        lead_llm = _ScriptLLM([
            "NVDA last price is 190 USD.",
            "NVDA last price is 135 USD and its all-time high in source history is 190 USD.",
        ])
        orch = TeamLeadOrchestrator(
            router=_StubRouter(["NVDA"]),
            data_agent=data_agent,
            news_agent=NewsResearcherAgent(None, _EmptyNews()),
            lead_llm=lead_llm,
            tools=tools,
        )
        res = orch.run("What is NVDA's price and all-time high?")
        self.assertFalse(res.data_report.faithful_numbers)
        self.assertEqual(res.data_report.validation.get("validation_reason"), "claim_provenance")
        self.assertTrue(res.data_report.validation.get("provenance_conflicts"))
        self.assertTrue(res.faithful_synthesis)
        self.assertIn("135 USD", res.final_synthesis)
        self.assertEqual(len(lead_llm.prompts), 2)
        self.assertIn("wrong asset, metric, or unit", lead_llm.prompts[1])
        gate_events = [e for e in res.events if e.event_type == "GATE_CHECK"]
        self.assertEqual(gate_events[-1].payload["guard_retry"]["reason"], "claim_provenance")
        self.assertEqual(gate_events[-1].payload["guard_retry"]["used"], "retry")
        self.assertTrue(any("190" in item for item in gate_events[-1].payload["guard_retry"]["rejected"]))

    def test_drawdown_clause_with_direct_currency_high_accepted_while_misbindings_still_rejected(self):
        good = (
            "NVDA has a drawdown from its all-time high of 190 USD of 28.95 percent, "
            "and a drawdown from its 52-week high of $150 of 10 percent."
        )
        self.assertEqual(check_claim_provenance(good, {"NVDA": SAMPLE_FACTS["NVDA"]}), [])

        bad_pct = check_claim_provenance("NVDA has a drawdown from its all-time high of 190 percent.", {"NVDA": SAMPLE_FACTS["NVDA"]})
        self.assertEqual(len(bad_pct), 1)
        self.assertIn("metric", bad_pct[0]["violations"])
        self.assertIn("unit", bad_pct[0]["violations"])

        bad_usd = check_claim_provenance("NVDA has a drawdown from its all-time high of 28.95 USD.", {"NVDA": SAMPLE_FACTS["NVDA"]})
        self.assertEqual(len(bad_usd), 1)
        self.assertIn("unit", bad_usd[0]["violations"])

        bad_wrong_high = check_claim_provenance("NVDA has a drawdown from its all-time high of $150.", {"NVDA": SAMPLE_FACTS["NVDA"]})
        self.assertEqual(len(bad_wrong_high), 1)
        self.assertEqual(bad_wrong_high[0]["kind"], "metric_misbinding")

    def test_data_analyst_scales_token_budget_for_multi_asset_summaries(self):
        seen_budgets = []

        class BudgetLLM:
            def complete(self, system, prompt, *, max_tokens=256, **kwargs):
                seen_budgets.append(max_tokens)
                return "NVDA trades at 135 USD and AAPL trades at 225 USD."

        tools = _StubTools(SAMPLE_FACTS)
        agent = DataAnalystAgent(BudgetLLM(), tools)
        from core.orchestrator import EventBus
        agent.run(["NVDA"], "fundamental_equity", EventBus(), "s1")
        agent.run(["NVDA", "AAPL"], "fundamental_equity", EventBus(), "s2")
        self.assertEqual(seen_budgets, [256, 384])

        lead_budgets = []

        class LeadBudgetLLM:
            def complete(self, system, prompt, *, max_tokens=256, **kwargs):
                lead_budgets.append(max_tokens)
                return "NVDA trades at 135 USD and AAPL trades at 225 USD."

        orch = TeamLeadOrchestrator(
            router=_StubRouter(["NVDA", "AAPL"], intent="compare_assets"),
            data_agent=DataAnalystAgent(BudgetLLM(), tools),
            news_agent=NewsResearcherAgent(None, _EmptyNews()),
            lead_llm=LeadBudgetLLM(),
            tools=tools,
        )
        res = orch.run("Compare NVDA and AAPL")
        self.assertTrue(res.faithful_synthesis)
        self.assertEqual(lead_budgets, [352])

    def test_asset_name_numbers_allowed_for_futures_and_indices(self):
        from core.orchestrator import EventBus, market_fact_numbers
        gold_snap = {
            "ticker": "GC=F",
            "name": "Gold Dec 26",
            "currency": "USD",
            "status": "available",
            "price": 2650.5,
            "previous_session_close": 2640.0,
            "change_from_previous_session_pct": 0.3977,
        }
        self.assertIn(26.0, market_fact_numbers("GC=F", gold_snap))
        tools = _StubTools({"GC=F": gold_snap})
        llm = _ScriptLLM(["Gold Dec 26 (GC=F) last traded at 2650.5 USD, up 0.40 percent from its previous session close of 2640 USD."])
        rep = DataAnalystAgent(llm, tools).run(["GC=F"], "fundamental_equity", EventBus(), "s3")
        self.assertTrue(rep.faithful_numbers, rep.validation)

    def test_drawdown_from_that_high_and_try_currency_pass_claim_provenance(self):
        facts = {
            "THYAO.IS": {
                "ticker": "THYAO.IS",
                "name": "Türk Hava Yollari Anonim Ortakligi",
                "currency": "TRY",
                "status": "available",
                "price": 312.0,
                "previous_session_close": 309.75,
                "change_from_previous_session_pct": 0.7264,
                "high_52_week": 355.5,
                "low_52_week": 242.2,
                "all_time_high": 355.5,
                "drawdown_from_all_time_high_pct": 12.2363,
                "pe_ratio": 30.48,
            }
        }
        good = (
            "THYAO.IS last traded at 312.0 TRY, up 0.73% from the previous session close of 309.75 TRY, "
            "and the all-time high in source history is 355.5 TRY, a drawdown from that high of 12.24%."
        )
        self.assertEqual(check_claim_provenance(good, facts), [])
        bad_unit = "THYAO.IS trailing P/E is 30.48 TRY."
        self.assertTrue(check_claim_provenance(bad_unit, facts))


if __name__ == "__main__":
    unittest.main()
