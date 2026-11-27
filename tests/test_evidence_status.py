import unittest
from datetime import datetime, timedelta, timezone

from core.citations import shown_facts
from core.claim_provenance import check_claim_provenance
from core.evidence_status import claims_current_price, decision_assessment, previous_close_request, quote_status
from core.market import MarketTools, listing_timezone, resolve_yahoo_symbol
from core.orchestrator import FACT_LABELS

OBSERVED = datetime(2026, 11, 27, 14, 0, tzinfo=timezone.utc)


def provider_meta(minutes_old, *, start=-240, end=60, delay=None):
    time = lambda minutes: (OBSERVED + timedelta(minutes=minutes)).timestamp()
    out = {'regularMarketTime': None if minutes_old is None else time(-minutes_old)}
    if start is not None:
        out['currentTradingPeriod'] = {'regular': {'start': time(start), 'end': time(end)}}
    if delay is not None:
        out['exchangeDataDelayedBy'] = delay
    return out


class TestQuoteClock(unittest.TestCase):
    def test_freshness_is_from_source_clock_not_fetch_success(self):
        for name, meta, continuous, expected in [
            ('missing', provider_meta(None), False, 'timestamp_unknown'),
            ('future', provider_meta(-1), False, 'timestamp_invalid'),
            ('fresh regular', provider_meta(3, delay=0), False, 'recent'),
            ('delayed regular', provider_meta(17, delay=15), False, 'delayed'),
            ('aged despite successful fetch', provider_meta(90, delay=15), False, 'stale'),
            ('previous session during open', provider_meta(270), False, 'stale'),
            ('last regular session', provider_meta(18, start=-390, end=-15), False, 'last_session'),
            ('no period', provider_meta(2, start=None), False, 'last_known'),
            ('continuous', provider_meta(2, start=None), True, 'recent'),
            ('very old', provider_meta(8000, start=None), False, 'stale'),
        ]:
            with self.subTest(name=name):
                r = quote_status(meta, OBSERVED, continuous=continuous)
                self.assertEqual(r['state'], expected)
                self.assertEqual(r['observed_at'], OBSERVED.isoformat())

    def test_provider_early_close_does_not_assume_a_normal_close(self):
        end = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
        start = end - timedelta(hours=3, minutes=30)
        seen = end + timedelta(minutes=12)
        meta = {'regularMarketTime': (end - timedelta(seconds=30)).timestamp(),
                'currentTradingPeriod': {'regular': {'start': start.timestamp(), 'end': end.timestamp()}}}
        shortened = quote_status(meta, seen)
        self.assertEqual((shortened['state'], shortened['session']), ('last_session', 'outside_regular'))
        self.assertEqual((seen - datetime.fromisoformat(shortened['as_of'])).total_seconds(), 750)
        meta['currentTradingPeriod']['regular']['end'] = (end + timedelta(hours=3)).timestamp()
        self.assertEqual(quote_status(meta, seen)['state'], 'stale')

    def test_next_session_metadata_on_dst_weekend_goes_stale_only_at_the_open(self):
        stamp = datetime(2026, 3, 6, 20, 59, 30, tzinfo=timezone.utc)
        opens = datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc)
        closes = datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc)
        meta = {'regularMarketTime': stamp.timestamp(), 'exchangeDataDelayedBy': 0,
                'currentTradingPeriod': {'regular': {'start': opens.timestamp(), 'end': closes.timestamp()}}}
        for seen, state in [(datetime(2026, 3, 8, 23, tzinfo=timezone.utc), 'last_known'),
                            (opens - timedelta(seconds=1), 'last_known'), (opens, 'stale')]:
            self.assertEqual(quote_status(meta, seen)['state'], state)

    def test_delayed_quote_within_five_minutes_of_completed_period_boundary(self):
        seen = OBSERVED + timedelta(minutes=10)
        meta = provider_meta(5, start=-210, end=0, delay=15)
        current = quote_status(meta, seen)
        self.assertEqual(current['state'], 'last_session')
        self.assertEqual(current['provider_delay_minutes'], 15)
        meta['regularMarketTime'] -= 1
        self.assertEqual(quote_status(meta, seen)['state'], 'last_known')

    def test_current_price_claim_does_not_pass_on_stale_quote(self):
        facts = {'VOD.L': {'price': 80.12, 'currency': 'GBp', 'status': 'available',
                           'quote_status': quote_status(provider_meta(90), OBSERVED)}}
        bad = check_claim_provenance('VOD.L is trading at 80.12 GBp now.', facts)
        self.assertEqual([c['kind'] for c in bad], ['quote_freshness'])
        self.assertEqual(check_claim_provenance('VOD.L last traded at 80.12 GBp.', facts), [])
        self.assertTrue(claims_current_price('The current quote is 80.12 GBp.'))
        self.assertFalse(claims_current_price('This quote is not live.'))
        for mixed in ('VOD.L is trading at 80.12 GBp now as of yesterday.',
                      'VOD.L is currently trading at 80.12 GBp (delayed).',
                      'Current price: 80.12 GBp (not live).',
                      'VOD.L is at 80.12 now.', 'It is now 80.12.', '80.12 is the current quote.'):
            with self.subTest(mixed=mixed):
                self.assertEqual([c['kind'] for c in check_claim_provenance(mixed, facts)], ['quote_freshness'])

    def test_market_tool_rejects_a_different_instrument_from_the_provider(self):
        def fetch(url):
            return {'chart': {'result': [{'meta': {'symbol': 'IBM', 'regularMarketPrice': 100, 'currency': 'USD'}}]}}
        tools = MarketTools(fetch=fetch)
        snap = tools.get_snapshot('VOD.L')
        self.assertEqual(snap['status'], 'unavailable')
        self.assertNotIn('price', snap)
        self.assertNotIn('VOD.L', tools.charts)

    def test_future_quote_is_not_exposed_as_a_number(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).timestamp()
        data = {'chart': {'result': [{'meta': {'symbol': 'VOD.L', 'regularMarketPrice': 100,
                                                'regularMarketTime': future, 'currency': 'GBp'}}]}}
        snap = MarketTools(fetch=lambda _: data).get_snapshot('VOD.L')
        self.assertEqual(snap['quote_status']['state'], 'timestamp_invalid')
        self.assertEqual(snap['status'], 'unavailable')
        self.assertNotIn('price', snap)

    def test_exchange_day_does_not_use_utc_midnight(self):
        instant = datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc)
        self.assertNotEqual(instant.date(), instant.astimezone(listing_timezone({'gmtoffset': 32400})).date())
        self.assertEqual(instant.astimezone(listing_timezone({'gmtoffset': 32400})).day, 27)

    def test_explicit_listing_is_not_replaced_when_market_conflicts(self):
        rows = [{'symbol': '7203.T', 'quoteType': 'EQUITY', 'exchange': 'JPX', 'shortname': 'Toyota Motor', 'score': 80000},
                {'symbol': 'FAKE.IS', 'quoteType': 'EQUITY', 'exchange': 'IST', 'shortname': 'Other issuer', 'score': 70000}]
        fake = lambda url: {'quotes': rows}
        self.assertIsNone(resolve_yahoo_symbol('7203.T', fake, context_query='7203.T, the Turkish stock'))
        self.assertEqual(resolve_yahoo_symbol('7203.T', fake, context_query='7203.T in Japan')['symbol'], '7203.T')

    def test_negated_index_cue_does_not_cancel_an_etf_constraint(self):
        rows = [{'symbol': '^NDX', 'quoteType': 'INDEX', 'exchange': 'NMS', 'shortname': 'Nasdaq Hundred index', 'score': 90000},
                {'symbol': 'QQQ', 'quoteType': 'ETF', 'exchange': 'NGM', 'shortname': 'Nasdaq Hundred ETF', 'score': 70000}]
        result = resolve_yahoo_symbol('Nasdaq Hundred', lambda _: {'quotes': rows},
                                      context_query='Nasdaq Hundred ETF, not the index')
        self.assertEqual(result['symbol'], 'QQQ')

    def test_decision_status_is_not_a_trade_prediction(self):
        snaps = {'VOD.L': {'quote_status': {'state': 'last_session'}},
                 'IBM': {'quote_status': {'state': 'recent'}}}
        verdict = decision_assessment(['VOD.L', 'IBM'], snaps, news_items=3, news_complete=False)
        self.assertEqual(verdict['status'], 'insufficient_evidence')
        self.assertEqual(verdict['quote_states'], {'VOD.L': 'last_session', 'IBM': 'recent'})
        self.assertIn('not establish whether to buy', verdict['text'])
        self.assertIn('Local news coverage is incomplete', verdict['text'])
        self.assertIn('VOD.L: last regular-session quote', verdict['text'])

    def test_previous_close_request_matches_natural_phrasings_and_rejects_unrelated_queries(self):
        positives = (
            'What was its previous close?',
            'What did THY close at yesterday?',
            'Previous close for NVDA',
            "Yesterday's close?",
            "What was yesterday's closing price?",
            'What did it close at in the last session?',
            'Prior session close for VOD.L',
            'What did THYAO.IS close at yesterday?',
        )
        for query in positives:
            with self.subTest(positive=query):
                self.assertTrue(previous_close_request(query))
        negatives = (
            'What is the previous quarter close?',
            "How did yesterday's news affect NVDA?",
            'Should I buy NVDA? What was its previous close?',
            'How much did it move from yesterday?',
            'What is NVDA trading at and what was its previous close?',
        )
        for query in negatives:
            with self.subTest(negative=query):
                self.assertFalse(previous_close_request(query))

    def test_shown_facts_cites_only_canonical_fact_labels_including_change_pct(self):
        snap = {
            'price': 303.5, 'previous_session_close': 298.25, 'change_from_previous_session_pct': 1.76,
            'unmapped_internal_metric': 42.0, 'source': 'Yahoo Finance', 'price_as_of': '2026-11-26',
        }
        facts = shown_facts({'THYAO.IS': snap})
        fields = {f['field'] for f in facts}
        self.assertEqual(fields, {'price', 'previous_session_close', 'change_from_previous_session_pct'})
        self.assertTrue(fields.issubset({k for k, _ in FACT_LABELS}))
