import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.market_contract import (
    AssessmentStatus,
    ContractArchetype,
    OHLCBar,
    evaluate_barrier_touch,
    parse_market_definition,
)
from core.polymarket import parse_market


def _bars(start_utc: datetime, count: int, *, provider="binance", symbol="ETH/USDT", interval_s=60, high="2100", low="2000", gap_at=None, hit_at=None, hit_high=None, hit_low=None):
    out = []
    for i in range(count):
        if gap_at is not None and i == gap_at:
            continue
        ts = start_utc + timedelta(seconds=i * interval_s)
        hi = Decimal(hit_high) if (hit_at == i and hit_high is not None) else Decimal(high)
        lo = Decimal(hit_low) if (hit_at == i and hit_low is not None) else Decimal(low)
        op = (hi + lo) / Decimal("2")
        cl = op
        out.append(OHLCBar(ts, op, hi, lo, cl, provider, symbol, interval_s))
    return out


class TestMarketContractDefinition(unittest.TestCase):
    def test_polymarket_parse_market_preserves_description_and_resolution_source(self):
        raw = {
            "id": "m1",
            "question": "Will Bitcoin be above $68,000 on May 24, 2026?",
            "description": "Resolves Yes if Binance BTC/USDT 1-minute candle Close at 12:00 PM ET on May 24, 2026 is strictly higher than $68,000.",
            "resolutionSource": "https://www.binance.com/en/trade/BTC_USDT",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["t1", "t2"]',
            "outcomePrices": '["0.55", "0.45"]',
        }
        parsed = parse_market(raw)
        self.assertIn("Binance BTC/USDT", parsed["description"])
        self.assertEqual(parsed["resolution_source"], "https://www.binance.com/en/trade/BTC_USDT")

    def test_expiry_threshold_crypto_dst_summer_and_winter_and_exact_comparator(self):
        summer = {
            "id": "btc-summer",
            "question": "Will Bitcoin be above $68,000 on May 24, 2026?",
            "description": "Resolves Yes if the Binance BTC/USDT 1-minute candle Close at 12:00 PM ET on May 24, 2026 is strictly higher than $68,000.",
            "resolution_source": "Binance BTC/USDT",
        }
        now = datetime(2026, 5, 24, 15, 0, tzinfo=timezone.utc)
        res = parse_market_definition(summer, now=now)
        self.assertEqual(res.status, AssessmentStatus.SUPPORTED_READY)
        d = res.definition
        self.assertEqual((d.asset, d.archetype, d.source_provider, d.source_symbol), ("BTC", ContractArchetype.EXPIRY_THRESHOLD, "binance", "BTC/USDT"))
        self.assertEqual((d.metric, d.comparator, d.strike, d.interval_s), ("close", ">", Decimal("68000"), 60))
        self.assertEqual(d.settlement_at, datetime(2026, 5, 24, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(res.time_left_s, 3600.0)

        winter = {
            "id": "btc-winter",
            "question": "Will Bitcoin be above $28,000.01 on January 15, 2026?",
            "description": "Resolves Yes if the Coinbase BTC-USD 1-minute candle Close on January 15, 2026 at 12:00 PM ET is equal to or greater than $28,000.01.",
            "resolution_source": "Coinbase BTC-USD",
        }
        res_w = parse_market_definition(winter, now=datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(res_w.status, AssessmentStatus.SUPPORTED_READY)
        self.assertEqual(res_w.definition.source_provider, "coinbase")
        self.assertEqual(res_w.definition.comparator, ">=")
        self.assertEqual(res_w.definition.strike, Decimal("28000.01"))
        self.assertEqual(res_w.definition.settlement_at, datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc))

    def test_equity_official_close_uses_explicit_provider_and_4pm_et(self):
        aapl = {
            "id": "aapl-close",
            "question": "Will AAPL close above $265 on April 28, 2026?",
            "description": "Resolves Yes if Apple official closing price on Yahoo Finance on April 28, 2026 is equal to or greater than $265.",
            "resolution_source": "https://finance.yahoo.com/quote/AAPL/history",
        }
        res = parse_market_definition(aapl, now=datetime(2026, 4, 28, 19, 0, tzinfo=timezone.utc))
        self.assertEqual(res.status, AssessmentStatus.SUPPORTED_READY)
        d = res.definition
        self.assertEqual((d.asset, d.source_provider, d.source_symbol, d.metric), ("AAPL", "yahoo_finance", "AAPL", "official_close"))
        self.assertEqual(d.settlement_at, datetime(2026, 4, 28, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(res.time_left_s, 3600.0)

    def test_expiry_range_requires_explicit_boundary_tie_rule(self):
        with_rule = {
            "id": "eth-range",
            "question": "Will the price of Ethereum be between $1,500 and $1,600 on June 30, 2026?",
            "description": "Resolves using the final Binance ETH/USDT 1-minute candle Close at 12:00 PM ET on June 30, 2026. A price exactly between brackets goes to the higher bracket.",
            "resolution_source": "Binance ETH/USDT",
        }
        res = parse_market_definition(with_rule, now=datetime(2026, 6, 30, 14, 0, tzinfo=timezone.utc))
        self.assertEqual(res.status, AssessmentStatus.SUPPORTED_READY)
        d = res.definition
        self.assertEqual(d.archetype, ContractArchetype.EXPIRY_RANGE)
        self.assertEqual((d.lower, d.upper, d.lower_inclusive, d.upper_inclusive), (Decimal("1500"), Decimal("1600"), True, False))
        self.assertEqual(d.bucket_tie_rule, "exact_boundary_to_higher_bracket")

        missing_rule = dict(with_rule, description="Resolves using the final Binance ETH/USDT 1-minute candle Close at 12:00 PM ET on June 30, 2026.")
        bad = parse_market_definition(missing_rule)
        self.assertEqual(bad.status, AssessmentStatus.UNSUPPORTED_ABSTAIN)
        self.assertIn("missing_range_boundary_rule", bad.reasons)

    def test_barrier_touch_distinguishes_hit_complete_no_hit_and_incomplete_coverage(self):
        start_utc = datetime(2026, 5, 26, 4, 0, tzinfo=timezone.utc)
        now_utc = start_utc + timedelta(minutes=5)
        market = {
            "id": "eth-touch",
            "question": "Will Ethereum reach $2,150 on May 26, 2026?",
            "description": "Resolves Yes if any Binance ETH/USDT 1-minute candle between 12:00 AM ET on May 26, 2026 and 11:59 PM ET on May 26, 2026 has a High equal to or greater than $2,150.",
            "resolution_source": "Binance ETH/USDT",
        }
        no_bars = parse_market_definition(market, now=now_utc)
        self.assertEqual(no_bars.status, AssessmentStatus.SUPPORTED_INCOMPLETE_DATA)
        self.assertIsNone(no_bars.barrier_touched)
        self.assertEqual(no_bars.definition.archetype, ContractArchetype.BARRIER_TOUCH)
        self.assertEqual((no_bars.definition.direction, no_bars.definition.metric, no_bars.definition.comparator), ("up", "high", ">="))

        complete_no_hit = parse_market_definition(market, now=now_utc, bars=_bars(start_utc, 6, high="2149.99"))
        self.assertEqual(complete_no_hit.status, AssessmentStatus.SUPPORTED_READY)
        self.assertFalse(complete_no_hit.barrier_touched)

        gapped_no_hit = parse_market_definition(market, now=now_utc, bars=_bars(start_utc, 6, high="2149.99", gap_at=3))
        self.assertEqual(gapped_no_hit.status, AssessmentStatus.SUPPORTED_INCOMPLETE_DATA)
        self.assertIsNone(gapped_no_hit.barrier_touched)
        self.assertIn("ohlc_gap", gapped_no_hit.reasons)

        gapped_with_hit = parse_market_definition(market, now=now_utc, bars=_bars(start_utc, 6, high="2149.99", gap_at=4, hit_at=2, hit_high="2150.00"))
        self.assertEqual(gapped_with_hit.status, AssessmentStatus.SUPPORTED_READY)
        self.assertTrue(gapped_with_hit.barrier_touched)

        wrong_provider = parse_market_definition(market, now=now_utc, bars=_bars(start_utc, 6, provider="coinbase", high="2200"))
        self.assertEqual(wrong_provider.status, AssessmentStatus.SUPPORTED_INCOMPLETE_DATA)
        self.assertIsNone(wrong_provider.barrier_touched)
        self.assertIn("wrong_provider", wrong_provider.reasons)

        wrong_gran = parse_market_definition(market, now=now_utc, bars=_bars(start_utc, 6, interval_s=300, high="2200"))
        self.assertEqual(wrong_gran.status, AssessmentStatus.SUPPORTED_INCOMPLETE_DATA)
        self.assertIn("wrong_granularity", wrong_gran.reasons)

    def test_downward_barrier_touch_from_market_creation(self):
        start_utc = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
        now_utc = start_utc + timedelta(minutes=3)
        market = {
            "id": "btc-dip",
            "question": "Will Bitcoin dip to $56k before May 25, 2026?",
            "description": "Resolves Yes if any Binance BTC/USDT 1-minute candle from market creation through 11:59 PM ET on May 25, 2026 records a Low equal to or lower than $56,000.",
            "start": "2026-05-20T12:00:00Z",
            "resolution_source": "Binance BTC/USDT",
        }
        hit_bars = _bars(start_utc, 4, symbol="BTC/USDT", high="60000", low="57000", hit_at=1, hit_low="55999.50")
        res = parse_market_definition(market, now=now_utc, bars=hit_bars)
        self.assertEqual(res.status, AssessmentStatus.SUPPORTED_READY)
        self.assertTrue(res.barrier_touched)
        self.assertEqual((res.definition.direction, res.definition.metric, res.definition.strike), ("down", "low", Decimal("56000")))

    def test_unsupported_and_ambiguous_contracts_always_abstain(self):
        cases = [
            (
                {"id": "1", "question": "Will Bitcoin be above $70,000 on May 24, 2026?", "end": "2026-05-24T16:00:00Z"},
                "missing_resolution_source",
            ),
            (
                {
                    "id": "2",
                    "question": "Will Bitcoin be above $70,000 on May 24, 2026?",
                    "description": "Resolves Yes if Bitcoin price at 12:00 PM ET on May 24, 2026 is strictly higher than $70,000.",
                },
                "missing_resolution_source",
            ),
            (
                {
                    "id": "3",
                    "question": "Will Bitcoin be above $70,000 on May 24, 2026?",
                    "description": "Resolves Yes if Binance BTC/USDT 1-minute candle Close on May 24, 2026 at 12:00 PM is strictly higher than $70,000.",
                    "end": "2026-05-24T16:00:00Z",
                },
                "timezone_unspecified",
            ),
            (
                {
                    "id": "4",
                    "question": "Will Nvidia have the largest market cap on June 1, 2026?",
                    "description": "Resolves Yes if Nvidia market cap on Yahoo Finance is largest at 4:00 PM ET on June 1, 2026.",
                },
                "unsupported_metric",
            ),
            (
                {
                    "id": "5",
                    "question": "Will AAPL outperform NVDA in May 2026?",
                    "description": "Resolves Yes if AAPL return exceeds NVDA on Yahoo Finance at 4:00 PM ET on May 29, 2026.",
                },
                "unsupported_relative_claim",
            ),
            (
                {
                    "id": "6",
                    "question": "Will Bitcoin reach $80,000 and close above $75,000 on May 24, 2026?",
                    "description": "Resolves Yes if Binance BTC/USDT 1-minute candle High reaches $80,000 and close above $75,000 at 12:00 PM ET on May 24, 2026.",
                },
                "unsupported_multi_condition",
            ),
            (
                {
                    "id": "7",
                    "question": "Will Ethereum reach $2,150 on May 26, 2026?",
                    "description": "Resolves Yes if Binance ETH/USDT 1-minute candle Close reaches $2,150 by 11:59 PM ET on May 26, 2026.",
                },
                "ambiguous_contract_type",
            ),
            (
                {
                    "id": "8",
                    "question": "Will Bitcoin be above $68,000 on May 24, 2026?",
                    "description": "Resolves Yes if Binance BTC/USDT 1-minute candle Close at 12:00 PM ET on May 24, 2026 is strictly higher than $68,000.",
                    "has_clarifications": True,
                },
                "unresolved_clarification",
            ),
        ]
        for market, expected_reason in cases:
            res = parse_market_definition(market, clarification_loaded=False if market.get("has_clarifications") else True)
            self.assertEqual(res.status, AssessmentStatus.UNSUPPORTED_ABSTAIN, (market["id"], res))
            self.assertIn(expected_reason, res.reasons, (market["id"], res))

    def test_evaluate_barrier_touch_rejects_corrupt_ohlc_and_late_start(self):
        start_utc = datetime(2026, 5, 26, 4, 0, tzinfo=timezone.utc)
        market = {
            "id": "eth-touch-bad",
            "question": "Will Ethereum reach $2,150 on May 26, 2026?",
            "description": "Resolves Yes if any Binance ETH/USDT 1-minute candle between 12:00 AM ET on May 26, 2026 and 11:59 PM ET on May 26, 2026 has a High equal to or greater than $2,150.",
        }
        defn = parse_market_definition(market).definition
        corrupt = [OHLCBar(start_utc, Decimal("2100"), Decimal("2050"), Decimal("2090"), Decimal("2080"), "binance", "ETH/USDT", 60)]
        touched, reasons = evaluate_barrier_touch(defn, corrupt, now=start_utc + timedelta(minutes=1))
        self.assertIsNone(touched)
        self.assertIn("invalid_ohlc", reasons)

        late_start = _bars(start_utc + timedelta(minutes=2), 4, high="2100")
        touched_late, reasons_late = evaluate_barrier_touch(defn, late_start, now=start_utc + timedelta(minutes=5))
        self.assertIsNone(touched_late)
        self.assertIn("coverage_incomplete", reasons_late)


if __name__ == "__main__":
    unittest.main()
