import unittest
from datetime import datetime, timedelta, timezone

from core.claim_provenance import check_claim_provenance
from core.evidence_status import claims_current_price, decision_assessment, quote_status
from core.market import MarketTools, listing_timezone, resolve_yahoo_symbol

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

    def test_current_price_claim_does_not_pass_on_stale_quote(self):
        facts = {'VOD.L': {'price': 80.12, 'currency': 'GBp', 'status': 'available',
                           'quote_status': quote_status(provider_meta(90), OBSERVED)}}
        bad = check_claim_provenance('VOD.L is trading at 80.12 GBp now.', facts)
        self.assertEqual([c['kind'] for c in bad], ['quote_freshness'])
        self.assertEqual(check_claim_provenance('VOD.L last traded at 80.12 GBp.', facts), [])
        self.assertTrue(claims_current_price('The current quote is 80.12 GBp.'))
        self.assertFalse(claims_current_price('This quote is not live.'))

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
