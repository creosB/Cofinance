import ast
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from qualify import regime_news as R


class TestRegimeNews(unittest.TestCase):
    def panel(self):
        d=[f'd{i:03}' for i in range(90)]; p=np.exp(np.linspace(4.,4.3,90)+.015*np.sin(np.arange(90)/3)); other=p*np.exp(.02*np.cos(np.arange(90)))
        return d, {'NVDA':p.copy(),'SPY':other.copy()}, {'NVDA':p.copy()*.99,'SPY':other.copy()*.99}

    def test_features_see_nothing_after_origin(self):
        d,cl,op=self.panel(); c,e=R.example('NVDA',63,d,cl,op); self.assertIsNone(e)
        self.assertEqual(c['entry'],d[64]); self.assertEqual(c['exit'],d[68]); self.assertAlmostEqual(c['ret'],cl['NVDA'][68]/op['NVDA'][64]-1)
        for x in cl.values(): x[64:]*=11
        for x in op.values(): x[64:]*=2
        changed,_=R.example('NVDA',63,d,cl,op); self.assertEqual(c['x'],changed['x']); self.assertNotEqual(c['ret'],changed['ret'])
        self.assertTrue(all(np.isfinite(c['x'])))

    def test_missing_and_flat(self):
        d,cl,op=self.panel(); cl['NVDA'][68]=op['NVDA'][64]; self.assertEqual(R.example('NVDA',63,d,cl,op)[1],'flat')
        cl['NVDA'][68]=np.nan; self.assertEqual(R.example('NVDA',63,d,cl,op)[1],'missing_target')
        cl['NVDA'][63]=np.nan; self.assertEqual(R.example('NVDA',63,d,cl,op)[1],'missing_features')

    def test_embargo_and_trailing_window(self):
        data=[{'origin':'2018','exit':s,'i':i} for s,i in [('2019-01-01',101),('2019-01-02',101),('2018-12-31',100),('2018-11-30',99)]]
        self.assertEqual(R.train_before(data,'2019-01-01',1360),[data[2]])

    def test_logistic_learns_without_reading_test_statistics(self):
        rows=[{'x':[x,1.],'y':x>0} for x in [-2.,-1.,1.,2.]*10]; m=R.fit(rows)
        self.assertGreater(R.predict(m,[1.,1.]),0); self.assertLess(R.predict(m,[-1.,1.]),0)
        self.assertEqual(m[0].tolist(),[0.,1.]); self.assertAlmostEqual(R.predict(m,[1000.,1.]),R.predict(m,[10000.,1.]))
        with self.assertRaises(ValueError): R.fit([{'x':[1.],'y':True}]*3)

    def test_balanced_loss_removes_intercept_only_base_rate(self):
        x=[{'x':[0.],'y':i!=0} for i in range(20)]; self.assertAlmostEqual(R.predict(R.fit(x),[0.]),0,places=8)

    def test_overlay_is_bounded_and_count_control_sign_blind(self):
        p=.1; self.assertEqual(R.overlays(p,[]),(p,p)); pos,blind=R.overlays(p,[100.]); neg,blind2=R.overlays(p,[-100.]); self.assertAlmostEqual(pos,.1875); self.assertAlmostEqual(neg,.0125); self.assertEqual(blind,blind2)
        mix,b=R.overlays(p,[100.,-100.]); self.assertAlmostEqual(mix,p); self.assertGreater(b,p)

    def test_attached_titles_dedup_and_time_checks(self):
        now=1_000_000; r={'asset':'A'}; n={'title':'t','available_at':now}; raws={'news_served':[n,n,{'title':'other','available_at':now}]}
        a,missing=R.attach(r,raws,{'A|t':.4},now); self.assertEqual(a,{('A','t'):.4,('A','other'):0}); self.assertEqual(missing,1)
        for t in (now+1,now-7*86400-1,None):
            with self.assertRaises(ValueError): R.attach(r,{'news_served':[{**n,'available_at':t}]},{},now)

    def test_shuffle_keeps_scores_within_asset_and_same_title_shared(self):
        a={('A','x'):1,('A','y'):2,('A','z'):3,('B','x'):99}; m=R.shuffled_stories(a,random.Random(1)); self.assertEqual(sorted(m[k] for k in m if k[0]=='A'),[1,2,3]); self.assertEqual(m['B','x'],99); self.assertNotEqual(a,m); self.assertEqual(a['A','x'],1)

    def test_metrics_bruteforce_with_ties(self):
        rng=random.Random(5)
        for _ in range(30):
            val=[rng.randrange(-2,3) for i in range(24)]; y=[i%3==0 for i in range(24)]; p=[a for a,v in zip(val,y) if v]; n=[a for a,v in zip(val,y) if not v]
            auc=sum((a>b)+.5*(a==b) for a in p for b in n)/(len(p)*len(n)); ba=.5*(sum((a>0)+.5*(a==0) for a in p)/len(p)+sum((b<0)+.5*(b==0) for b in n)/len(n)); np.testing.assert_allclose(R.metric(val,y),[auc,ba])
        self.assertIsNone(R.named(R.metric([1],[True]))['auc'])

    def test_blocks_do_not_split_cross_section(self):
        groups=[np.arange(3*i,3*i+3) for i in range(8)]; q=R.block_indices(groups,3,np.random.default_rng(19)).reshape(-1,3); self.assertEqual(len(q),8)
        for j,v in enumerate(q):
            np.testing.assert_array_equal(v,groups[v[0]//3]);
            if j%3: self.assertEqual(v[0]//3,(q[j-1,0]//3+1)%8)

    def test_same_count_removes_count_confound(self):
        rows=[{'count':0,'s':0,'y':v} for v in [1,0,0]]+[{'count':1,'s':1,'y':v} for v in [1,1,0]]; self.assertGreater(R.score(rows,['s'])['s']['auc'],.5); self.assertEqual(R.same_count(rows,'s'),{'auc':.5,'pairs':4})

    def test_freeze_refuses_change_and_second_read(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'freeze'; v={'rules':'x'}
            with patch.object(R,'snapshot',return_value=v): R.freeze(p)
            with patch.object(R,'snapshot',return_value={'rules':'changed'}), self.assertRaises(ValueError): R.take(p)
            with patch.object(R,'snapshot',return_value=v):
                self.assertEqual(R.take(p),v)
                with self.assertRaises(FileExistsError): R.take(p)
                with self.assertRaises(FileExistsError): R.freeze(p)

    def test_cli_installs_local_guard_before_any_run(self):
        src=Path(R.__file__).read_text(encoding='utf-8'); tree=ast.parse(src); main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        self.assertEqual(ast.unparse(main.body[0]),'S.install_local_only()')
