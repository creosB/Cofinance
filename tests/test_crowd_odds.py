import json
import unittest
from core.crowd_odds import history_for, lookup, select

NOW = 1_790_000_000.0  # 2026-09-21, before the fixtures' 2027-01-01 end dates


def market(q, bid, ask, last=None, end='2027-01-01T00:00:00Z', active=True, closed=False, outcomes=('Yes', 'No')):
    return {'id': q[:8], 'question': q, 'outcomes': json.dumps(list(outcomes)), 'clobTokenIds': json.dumps(['a', 'b']),
            'bestBid': bid, 'bestAsk': ask, 'lastTradePrice': last, 'endDate': end, 'active': active, 'closed': closed}


def event(title, slug, vol, markets, end='2027-01-01T00:00:00Z'):
    return {'title': title, 'slug': slug, 'volume24hr': vol, 'endDate': end, 'markets': markets}


CAL = {'study': 'test', 'horizons': {'30d': [{'lo': .4, 'hi': .5, 'n': 120, 'yes_rate': .41, 'ci95': [.33, .5]},
                                              {'lo': .9, 'hi': .95, 'n': 10, 'yes_rate': .9, 'ci95': [.6, .98]}]}}


class TestCrowdOdds(unittest.TestCase):
    def test_picks_the_even_bet_line_per_event_busiest_first(self):
        events = [event('What price will Bitcoin hit in 2026?', 'btc-2026', 5000, [
                      market('Will Bitcoin reach $150k?', .03, .04), market('Will Bitcoin reach $100k?', .44, .46),
                      market('Will Bitcoin dip to $50k?', .2, .22)]),
                  event('Bitcoin above X tomorrow?', 'btc-tmr', 90000, [market('Will Bitcoin be above $70k?', .9, .91)])]
        out = select('BTC', events, NOW, CAL)
        self.assertEqual([m['url'] for m in out], ['https://polymarket.com/event/btc-tmr', 'https://polymarket.com/event/btc-2026'])
        self.assertEqual(out[1]['question'], 'Will Bitcoin reach $100k?')
        self.assertAlmostEqual(out[1]['probability'], .45)
        self.assertEqual(out[1]['price_basis'], 'midpoint')
        self.assertEqual(out[1]['history']['n'], 120)        # 30d bucket with enough markets
        self.assertIsNone(out[0]['history'])                 # thin bucket (n=10) is never shown

    def test_filters_illiquid_ended_closed_wide_and_unrelated(self):
        events = [event('Bitcoin thin', 'thin', 10, [market('Will Bitcoin x?', .4, .5)]),
                  event('Bitcoin ended', 'ended', 9000, [market('Will Bitcoin y?', .4, .5, end='2020-01-01T00:00:00Z')]),
                  event('Bitcoin closed', 'closed', 9000, [market('Will Bitcoin z?', .4, .5, closed=True)]),
                  event('Bitcoin wide', 'wide', 9000, [market('Will Bitcoin w?', .1, .6)]),
                  event('Celebrity divorce?', 'gossip', 9000, [market('Will X divorce?', .4, .5)]),
                  event('Bitcoin bad slug', 'Bad Slug/../x', 9000, [market('Will Bitcoin v?', .4, .5)])]
        self.assertEqual(select('BTC', events, NOW, None), [])

    def test_last_trade_used_only_without_a_wide_book(self):
        out = select('NVDA', [event('Nvidia market cap?', 'nv', 5000, [market('Will Nvidia be largest?', None, None, last=.62)])], NOW)
        self.assertEqual((out[0]['probability'], out[0]['price_basis']), (.62, 'last trade'))

    def test_lookup_never_raises_and_reports_errors(self):
        def boom(term): raise TimeoutError('slow')
        r = lookup(['BTC', 'ETH', 'SOL', 'XRP'], search=boom, now=NOW)
        self.assertEqual(r['markets'], [])
        self.assertEqual(r['errors'], ['BTC: TimeoutError', 'ETH: TimeoutError', 'SOL: TimeoutError'])  # at most three assets
        self.assertIn('not advice', r['note'])

    def test_history_horizon_follows_time_to_close(self):
        cal = {'horizons': {'1d': [{'lo': .4, 'hi': .5, 'n': 50, 'yes_rate': .42}], '7d': [], '30d': []}}
        self.assertEqual(history_for(cal, .45, 2)['horizon'], '1d')
        self.assertIsNone(history_for(cal, .45, 10))

    def test_chat_service_with_fake_tools_never_goes_online(self):
        import tempfile
        from core.chat_service import ChatService
        with tempfile.TemporaryDirectory() as d:
            service = ChatService('.', d, market_factory=object)
            self.assertIsNone(service.crowd_odds)
            service.close()


if __name__ == '__main__':
    unittest.main()
