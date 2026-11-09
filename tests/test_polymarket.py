import json
import tempfile
import unittest
from pathlib import Path
from core.polymarket import fetch, parse_market, resolved_yes
from qualify import pm_lab
from qualify.pm_lab import bucket_of, category, collect, evaluate, history, price_at, score, when, wilson


def raw(i, yes, close, q='Will it happen?', outcomes='["Yes", "No"]'):
    return {'id':str(i),'question':q,'outcomes':outcomes,'outcomePrices':json.dumps(['1','0'] if yes else ['0','1']),
            'clobTokenIds':json.dumps([f'y{i}',f'n{i}']),'closed':True,'closedTime':close,'endDate':'2026-06-01T00:00:00Z',
            'volumeNum':50000,'events':[{'id':f'e{i%3}','title':'Event'}]}


class TestClient(unittest.TestCase):
    def test_only_public_polymarket_hosts(self):
        for url in ('http://gamma-api.polymarket.com/markets','https://evil.example/markets','https://clob.polymarket.com:444/x',
                    'https://user@clob.polymarket.com/x'):
            with self.assertRaises(ValueError):fetch(url)

    def test_parse_binary_only_and_clean_resolution(self):
        m=parse_market(raw(1,True,'2026-03-01 12:00:00+00'))
        self.assertEqual((m['yes_token'],m['no_token']),('y1','n1'));self.assertTrue(resolved_yes(m))
        self.assertTrue(m['yes_no'])
        teams=parse_market(raw(2,True,'x',outcomes='["Trump", "Biden"]'))
        self.assertEqual((teams['outcomes'][0],teams['yes_no']),('Trump',False))  # first listed outcome is scored
        self.assertIsNone(parse_market(raw(4,True,'x',outcomes='["A", "B", "C"]')))
        half=dict(raw(3,True,'2026-03-01 12:00:00+00'),outcomePrices='["0.5","0.5"]')
        self.assertIsNone(resolved_yes(parse_market(half)))
        self.assertIsNone(resolved_yes(dict(m,closed=False)))


class TestLab(unittest.TestCase):
    def test_time_and_category_parsing(self):
        self.assertEqual(when('2026-09-16 20:02:43+00'),when('2026-09-16T20:02:43Z'))
        self.assertEqual(category('Will Bitcoin reach $150k?'),'crypto')
        self.assertEqual(category('Will the Fed cut rates by 25 bps?'),'macro')
        self.assertEqual(category('Who will win the presidential election?'),'politics')

    def test_price_must_be_fresh_and_before_snapshot(self):
        pts=[(100,.2),(200,.4),(100000,.9)]
        self.assertEqual(price_at(pts,250),.4)
        self.assertIsNone(price_at(pts,200+86401))  # stale
        self.assertIsNone(price_at(pts,50))         # nothing yet

    def test_calibration_buckets_and_returns(self):
        rows=[{'id':str(i),'event_id':'e','p':.04,'y':int(i==0)} for i in range(50)]
        s=score(rows);b=s['calibration'][0]
        self.assertEqual(b['bucket'],'0.02-0.05');self.assertAlmostEqual(b['yes_rate'],.02)
        self.assertAlmostEqual(b['buy_yes_return_per_dollar'],.02/.04-1)
        lo,hi=wilson(1,50);self.assertTrue(lo<.02<hi)
        self.assertEqual(bucket_of(1.0),'0.98-1.00')

    def test_end_to_end_frozen_sample_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            page=[raw(i,i%2==0,'2026-03-%02d 12:00:00+00'%(1+i%27)) for i in range(40)]+[raw(99,True,'2027-05-01 00:00:00+00')]
            from unittest import mock
            with mock.patch.object(pm_lab.time,'sleep'):  # 100 sampled days, no real paging delay in a test
                out=collect(root,lister=lambda **k:page if k['offset']==0 else [])
            self.assertEqual(out['eligible'],40);self.assertEqual(out['excluded']['outside_window'],1)
            sample={m['id']:m for m in pm_lab.lines(root/'sample.jsonl')}
            def fake(token,start,end,fidelity):
                m=sample[token[1:]];return [(int(m['anchor']-8*86400),.7 if m['y'] else .3),(int(m['anchor']-3600),.99 if m['y'] else .01)]
            history(root,fetcher=fake,pause=0)
            rep=evaluate(root)
            self.assertEqual(rep['horizons']['7d']['all']['n'],40)   # 8-day-old point is exactly 1 day stale at 7d: kept
            self.assertAlmostEqual(rep['horizons']['7d']['all']['brier'],.09)
            self.assertEqual(rep['horizons']['1d']['all']['n'],0)    # the near-close point is after the 1d snapshot: never used

if __name__=='__main__':unittest.main()
