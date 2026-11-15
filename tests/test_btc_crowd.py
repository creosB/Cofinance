import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from qualify import btc_crowd as bc


def market(mid, k, win, volume=50000, settled=True):
    prices = (['1', '0'] if win else ['0', '1']) if settled else ['0.5', '0.5']
    return {'id': mid, 'question': f'Will the price of Bitcoin be above ${k:,} on the day?', 'groupItemTitle': f'{k:,}',
            'outcomes': '["Yes", "No"]', 'clobTokenIds': json.dumps([('win-' if win else 'lose-') + mid, 'n' + mid]),
            'outcomePrices': json.dumps(prices), 'closed': True, 'volumeNum': volume}


def event(d, eid):
    return {'id': eid, 'endDate': bc.noon_et(d).strftime('%Y-%m-%dT%H:%M:%SZ'), 'markets': [
        market(eid + 'a', 80000, True), market(eid + 'b', 125000, False),
        market(eid + 'c', 90000, True, volume=100), market(eid + 'd', 100000, True, settled=False)]}


class TestParts(unittest.TestCase):
    def test_noon_et_follows_us_daylight_time(self):
        utc = lambda d: bc.noon_et(d).hour
        self.assertEqual(utc(date(2026, 7, 1)), 16)
        self.assertEqual(utc(date(2026, 1, 15)), 17)
        self.assertEqual(utc(date(2026, 3, 8)), 16)    # second Sunday of March: already daylight time at noon
        self.assertEqual(utc(date(2026, 3, 7)), 17)
        self.assertEqual(utc(date(2026, 11, 1)), 17)   # first Sunday of November: back to standard time
        self.assertEqual(utc(date(2026, 10, 31)), 16)

    def test_threshold_parsing(self):
        self.assertEqual(bc.threshold({'groupItemTitle': '54,000'}), 54000)
        self.assertEqual(bc.threshold({'groupItemTitle': '110k'}), 110000)
        self.assertEqual(bc.threshold({'question': 'Will the price of Bitcoin be above $105,000 on June 27?'}), 105000)
        self.assertIsNone(bc.threshold({'groupItemTitle': 'Yes', 'question': 'Will Bitcoin go up on June 27?'}))

    def test_model_and_staleness(self):
        self.assertAlmostEqual(bc.model_p(100, 100, 0.0001, 24), 0.5, places=3)
        self.assertGreater(bc.model_p(110, 100, 0.005, 24), 0.99)
        self.assertLess(bc.model_p(90, 100, 0.005, 24), 0.01)
        pts = [(100, 0.2), (200, 0.4)]
        self.assertEqual(bc.price_at(pts, 250), 0.4)
        self.assertIsNone(bc.price_at(pts, 200 + bc.MAX_STALE_S + 1))
        self.assertIsNone(bc.price_at(pts, 50))


class TestEndToEnd(unittest.TestCase):
    def test_frozen_pipeline_without_network(self):
        d1, d2 = date(2025, 12, 15), date(2026, 6, 27)

        def getter(url):
            slug = parse_qs(urlsplit(url).query)['slug'][0]
            if slug == 'bitcoin-above-on-december-15':      # older events have no year in the slug
                return [event(d1, 'e1')]
            if slug == 'bitcoin-above-on-june-27-2026':
                return [event(d2, 'e2')]
            return []

        def fetcher(token, start, end, fidelity):
            return [(t, 0.9 if token.startswith('win-') else 0.1) for t in range(start, end, 600)]

        def yahoo(url):
            q = parse_qs(urlsplit(url).query)
            ts = list(range(int(q['period1'][0]), int(q['period2'][0]), 3600))
            closes = [100000 * (1.005 if (t // 3600) % 2 else 1.0) for t in ts]
            return {'chart': {'result': [{'timestamp': ts, 'indicators': {'quote': [{'close': closes}]}}]}}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = bc.collect(root, getter=getter, pause=0)
            self.assertEqual((p['markets'], p['days_with_event']), (4, 2))
            self.assertEqual((p['excluded']['low_volume'], p['excluded']['not_cleanly_resolved']), (2, 2))
            with self.assertRaises(ValueError):
                bc.collect(root, getter=getter, pause=0)       # frozen: never overwritten
            bc.history(root, fetcher=fetcher, pause=0)
            bc.spot(root, getter=yahoo)
            rep = bc.evaluate(root)
            self.assertEqual(rep['coverage'], {'markets': 4, 'with_history': 4, 'history_errors': 0})
            self.assertEqual(rep['spot_proxy_check']['yahoo_matches_settlement'], 1.0)
            h = rep['horizons']['24h']
            self.assertEqual((h['all']['n'], h['contested']['n'], h['all']['days']), (4, 4, 2))
            self.assertAlmostEqual(h['all']['crowd']['brier'], 0.01)
            self.assertLess(h['all']['model']['brier'], h['all']['crowd']['brier'])
            self.assertEqual((h['by_half_contested']['first']['n'], h['by_half_contested']['second']['n']), (2, 2))
            self.assertTrue(rep['claims']['model_beats_crowd'])
            (root / 'sample.jsonl').write_text('{}\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                bc.evaluate(root)                               # tampered sample is refused


if __name__ == '__main__':
    unittest.main()
