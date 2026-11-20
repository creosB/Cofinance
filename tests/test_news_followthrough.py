from datetime import date,datetime,timedelta,timezone
import ast
from pathlib import Path
import unittest
import numpy as np
from qualify import news_followthrough as F


def fixture():
    ds=[date(2025,1,1)+timedelta(days=i) for i in range(200)]; ds=[d.isoformat() for d in ds if d.weekday()<5]; x=.01*np.sin(np.arange(len(ds))/2.); y=.001+1.5*x
    return ds,{'SPY':x.copy(),'A':y.copy()}

def rec(title,d): return {'title':title,'available_at':datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()+3600}

class TestFollowthrough(unittest.TestCase):
    def test_event_bar_excluded_from_fit_and_market_removed(self):
        ds,oc=fixture(); j=80; z,why=F.aligned('A',ds[j-1],ds[j],ds,oc); self.assertEqual(why,'observed'); self.assertAlmostEqual(z,0.,places=8); oc['A'][j]+=.009
        self.assertAlmostEqual(F.aligned('A',ds[j-1],ds[j],ds,oc)[0],3.,places=8)

    def test_origin_and_same_day_not_observable(self):
        ds,oc=fixture(); z,why=F.aligned('A',ds[80],ds[80],ds,oc); self.assertIsNone(z); self.assertEqual(why,'not_yet_observable'); self.assertEqual(F.aligned('A',ds[1],ds[80],ds,oc)[1],'insufficient_history')
        oc['A'][79]=np.nan; self.assertEqual(F.aligned('A',ds[79],ds[80],ds,oc)[1],'missing_bar')

    def test_known_news_directions_boost_or_dampen_not_flip(self):
        ds,oc=fixture(); j=80; oc['A'][j]+=.009; records=[rec('x',ds[j-1])]; vals,_=F.blind_predict('A',ds[j],records,{'A|x':.6},ds,oc); self.assertAlmostEqual(vals['candidate'],.6*(1+.5*np.tanh(3))); minus,_=F.blind_predict('A',ds[j],records,{'A|x':-.6},ds,oc); self.assertAlmostEqual(minus['candidate'],-.6*(1-.5*np.tanh(3)))

    def test_weekend_cluster_dedup_and_permutation_invariance(self):
        ds,oc=fixture(); monday=next(d for d in ds[66:] if date.fromisoformat(d).weekday()==0); base=date.fromisoformat(monday); a=rec('a',(base-timedelta(days=2)).isoformat()); b=rec('b',(base-timedelta(days=1)).isoformat()); scores={'A|a':.2,'A|b':.8}; result,n=F.blind_predict('A',monday,[a,b,b],scores,ds,oc); self.assertEqual(n,{'observed':1}); self.assertAlmostEqual(result['grouped'],.5); self.assertEqual(result['count'],2); self.assertEqual(result,F.blind_predict('A',monday,[b,a],scores,ds,oc)[0])

    def test_pending_no_signal_and_future_price_invariance(self):
        ds,oc=fixture(); origin=ds[80]; recs=[rec('x',origin)]; scores={'A|x':.7}; before,n=F.blind_predict('A',origin,recs,scores,ds,oc); self.assertEqual(n,{'not_yet_observable':1}); self.assertEqual(before['candidate'],.7); self.assertEqual(before['response'],0.); oc['A'][81:]=999; self.assertEqual(before,F.blind_predict('A',origin,recs,scores,ds,oc)[0]); self.assertEqual(before,F.blind_predict('A',origin,recs,scores,ds[:81],oc)[0])

    def test_cutoffs_missing_and_no_news(self):
        ds,oc=fixture(); origin=ds[80]; empty,_=F.blind_predict('A',origin,[],{},ds,oc); self.assertEqual(empty['candidate'],0.); self.assertEqual(F.blind_predict('A',origin,[rec('x',ds[79])],{},ds,oc)[0]['missing'],1)
        for d in (ds[81],ds[60]):
            with self.assertRaises(ValueError): F.blind_predict('A',origin,[rec('x',d)],{},ds,oc)

    def test_live_call_thresholds_and_abstention_cost(self):
        rows=[{'y':True,'s':.5},{'y':False,'s':-.5},{'y':True,'s':.4},{'y':False,'s':.6}]; r=F.decisions(rows,'s'); self.assertEqual((r['up'],r['down'],r['abstain']),(2,1,1)); self.assertEqual(r['coverage'],.75); self.assertEqual(r['all_case_ba'],.5)

    def test_runner_guard(self):
        funcs=ast.parse(Path(F.__file__).read_text(encoding='utf8')).body; main=next(f for f in funcs if isinstance(f,ast.FunctionDef) and f.name=='main'); self.assertEqual(ast.unparse(main.body[0]),'S.install_local_only()')
