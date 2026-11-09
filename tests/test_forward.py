import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from qualify.forward import NEWS_SYSTEM, chain, record, score

UTC=timezone.utc
def ts(y,m,d,h=0,mi=0):return datetime(y,m,d,h,mi,tzinfo=UTC).timestamp()
MON=ts(2026,11,9,6,40)  # Monday morning, before the US open and after crypto's 00:00 UTC bar


def raw_for(symbol,start,end):
    crypto=symbol.endswith('-USD');day=datetime(2026,4,1,tzinfo=UTC);rows=[];i=0
    while day<datetime(2026,12,31,tzinfo=UTC):
        if crypto or day.weekday()<5:
            t=(day+(timedelta(0) if crypto else timedelta(hours=14,minutes=30))).timestamp()
            if start<=t<=end:
                c=100+i*.1;rows.append((int(t),c-.05,c+.2,c-.2,c))
            i+=1
        day+=timedelta(days=1)
    return {'chart':{'result':[{'timestamp':[r[0] for r in rows],'indicators':{'quote':[{
        'open':[r[1] for r in rows],'high':[r[2] for r in rows],'low':[r[3] for r in rows],'close':[r[4] for r in rows]}]}}]}}


@contextmanager
def fake(seen):
    def call(system,prompt,**kw):
        seen.append((system,prompt));return 'UP' if system==NEWS_SYSTEM else 'up, probably'
    yield call,{'id':'fake'}


class TestForward(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'fw';self.seen=[]
    def tearDown(self):self.tmp.cleanup()
    def rec(self,at,**kw):
        return record(self.root,narrator=lambda:fake(self.seen),clock=lambda:at,fetcher=raw_for,news_db=kw.pop('db',None),**kw)

    def test_partial_same_day_bars_never_feed_a_forecast(self):
        out=self.rec(ts(2026,11,9,16))  # US session open: today's SPY bar exists but is incomplete
        self.assertEqual(out['origin'],'2026-11-06')

    def test_one_open_batch_at_a_time(self):
        self.rec(MON)
        with self.assertRaises(ValueError):self.rec(MON+86400)
        self.assertEqual(self.rec(ts(2026,11,16,6,40))['batch'],2)
        self.assertEqual(len(chain(self.root)[0]),2)

    def test_scores_only_after_horizon_with_entry_after_seal(self):
        self.rec(MON)
        early=score(self.root,clock=lambda:ts(2026,11,12,12),fetcher=raw_for)
        self.assertEqual(early['pending_batches'],[1]);self.assertEqual(early['arms']['always_up']['n'],0)
        rep=score(self.root,clock=lambda:ts(2026,11,14,12),fetcher=raw_for)
        self.assertEqual(rep['batches_matured'],1);self.assertFalse(rep['claim_allowed'])
        by={r['symbol']:r for r in rep['cases']}
        self.assertEqual(by['AAPL']['entry_date'],'2026-11-09');self.assertEqual(by['BTC-USD']['entry_date'],'2026-11-10')
        self.assertEqual(by['AAPL']['exit_date'],'2026-11-13')
        self.assertEqual(rep['arms']['always_up']['accuracy_all'],1)
        self.assertEqual(rep['arms']['lfm3_price']['errors'],5)  # malformed output stays in the denominator

    def test_tampered_ledger_is_rejected(self):
        self.rec(MON);p=self.root/'ledger.jsonl'
        p.write_bytes(p.read_bytes().replace(b'"always_up": "UP"',b'"always_up": "DOWN"',1))
        with self.assertRaises(ValueError):score(self.root,clock=lambda:ts(2026,11,14,12),fetcher=raw_for)

    def test_changed_protocol_needs_new_root(self):
        self.rec(MON);p=self.root/'protocol.json';d=json.loads(p.read_text());d['news_limit']=9;p.write_text(json.dumps(d))
        with self.assertRaises(ValueError):self.rec(ts(2026,11,16,6,40))

    def test_news_is_point_in_time_and_only_the_news_arm_sees_tickers(self):
        db=Path(self.tmp.name)/'news.db';con=sqlite3.connect(db)
        con.execute('CREATE TABLE signals (namespace TEXT, key TEXT, ticker TEXT, source TEXT, url TEXT, title TEXT, body TEXT, '
                    'published_at REAL, action TEXT, reason TEXT, material REAL, urgent REAL, direction TEXT, direction_p REAL, '
                    'created_at REAL, expires_at REAL)')
        con.execute('CREATE TABLE runs (namespace TEXT, started_at REAL, summary TEXT)')
        rows=[('a','bullish',MON-3600,MON-3600),('b','bullish',MON-7200,MON-7200),('c','bearish',MON-9000,MON-9000),
              ('late','bearish',MON-60,MON+60),('old','bearish',MON-9*86400,MON-3600),('dropped','bearish',MON-60,MON-60)]
        for key,direction,pub,created in rows:
            action='drop' if key=='dropped' else 'digest'
            con.execute('INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('news',key,'AAPL','apple','u','title '+key,'',pub,
                        action,'',.6,.1,direction,.8,created,MON+86400))
        con.execute("INSERT INTO runs VALUES ('news',?,'{}')",(MON-3600,));con.commit();con.close()
        out=self.rec(MON,db=db)
        self.assertEqual(out['forecasts']['AAPL']['news_signal'],'UP');self.assertEqual(out['news_rows']['AAPL'],3)
        self.assertEqual(out['forecasts']['MSFT']['news_signal'],'ABSTAIN')
        price=[p for s,p in self.seen if s!=NEWS_SYSTEM];news=[p for s,p in self.seen if s==NEWS_SYSTEM]
        self.assertFalse(any('AAPL' in p or '2026-' in p or 'ticker' in p for p in price))
        self.assertTrue(any('AAPL' in p and 'title a' in p and 'late' not in p for p in news))

if __name__=='__main__':unittest.main()
