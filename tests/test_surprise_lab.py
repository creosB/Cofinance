from pathlib import Path
import tempfile, unittest
from unittest.mock import patch
from collections import Counter
from qualify import surprise_cases as F, surprise_review as V, surprise_lab as B


class TestSurprise(unittest.TestCase):
    def test_blind_keys_and_pair_coverage(self):
        rows,key=F.corpus(); self.assertEqual(len(rows),152); self.assertEqual(len(key),152); self.assertEqual(set(r['id'] for r in rows),set(key)); self.assertTrue(all(set(r)=={'asset','title','id'} for r in rows)); self.assertEqual((rows,key),F.corpus()); self.assertEqual(Counter(x['split'] for x in key.values()),{'controlled':128,'challenge':24}); pairs=Counter((v['family'],v['style']) for v in key.values() if v['case'] in ('beat','miss')); self.assertEqual(set(pairs.values()),{2}); self.assertEqual(len(pairs),24)
    def test_counterfactual_changes_expected_not_actual(self):
        rows,key=F.corpus(); p={}
        for r in rows:
            g=key[r['id']];
            if g['style']=='report_first' and g['case'] in ('beat','miss'):p[g['family'],g['case']]=V.clauses(r['title'])
        for f in {k[0] for k in p}:
            a,b=p[f,'beat'],p[f,'miss']; self.assertEqual(a[0],b[0]); self.assertNotEqual(a[1],b[1]); self.assertEqual(a[2:],b[2:])
    def test_blind_runner_refuses_gold_before_calls(self):
        with self.assertRaises(ValueError): B.evaluate_blind({'title':'x','id':'1','asset':'A','expected':'UP'},lambda *a,**k:self.fail())
    def test_verified_sign_and_exact_tie(self):
        t='Q1 loss per share was reported at $0.10; analysts expected Q1 loss per share of $0.20.'; self.assertEqual(V.review(t),('UP','verified_difference')); self.assertEqual(V.review(t.replace('$0.10','$0.30'))[0],'DOWN'); self.assertEqual(V.review(t.replace('$0.10','$0.20')),('ABSTAIN','verified_equal'))
    def test_mixed_units_signs_and_growth(self):
        t='Q1 operating margin was reported at 1250 basis points; analysts expected Q1 operating margin of 13%.'; self.assertEqual(V.review(t)[0],'DOWN'); self.assertEqual(V.review(t.replace('1250','1300'))[1],'verified_equal'); self.assertEqual(V.review(t.replace('13%','$13'))[0],'ABSTAIN'); self.assertEqual(V.review(t.replace('1250 basis points','$12').replace('13%','$13'))[0],'ABSTAIN')
        t='Q4 free cash flow was reported at negative $1.2 billion; consensus for Q4 free cash flow stood at negative $900 million.'; self.assertEqual(V.review(t)[0],'DOWN')
    def test_guard_comparability(self):
        t='Q1 customer churn was reported at 4%; consensus for Q1 customer churn stood at 5%'; self.assertEqual(V.review(t)[0],'UP');
        for alt in ('last year Q1 customer churn was 5%', 'consensus for Q2 customer churn stood at 5%', 'consensus for Q1 expense growth stood at 5%', 'consensus for Q1 customer churn stood at 2% to 5%'):
            self.assertEqual(V.review(t.split(';')[0]+'; '+alt)[0],'ABSTAIN')
        self.assertEqual(V.review(t+'; consensus for Q1 customer churn stood at 2%')[0],'ABSTAIN')
    def test_no_reversed_or_invented_evidence(self):
        t='Q1 expense growth came in at 4%; consensus for Q1 expense growth stood at 5%'; self.assertEqual(V.review(t,{'actual':0,'expected':1})[0],'UP');
        for d in ({'actual':1,'expected':0},{'actual':0,'expected':8},{'actual':-1,'expected':-1}):self.assertEqual(V.review(t,d)[1],'not_corroborated')
        for s in ('{}','{"actual":false,"expected":0}','{"actual":0,"expected":94}','{"actual":0,"expected":1,"label":"UP"}'):
            with self.assertRaises(ValueError):V.pick(s)
    def test_fallback_does_not_rebrand_unsupported_as_verified(self):
        self.assertEqual(V.routed('DOWN','ABSTAIN','incomparable'),('DOWN','semantic_fallback')); self.assertEqual(V.routed('DOWN','UP','verified_difference'),('UP','verified')); self.assertEqual(V.routed('UP','ABSTAIN','verified_equal'),('ABSTAIN','verified'))
    def test_all_failures_count_and_pairs_require_both(self):
        ss=[({'expected':v,'case':c,'family':'m','style':'s'},{'labels':{'a':a}}) for v,c,a in [('UP','beat','ERROR'),('DOWN','miss','DOWN'),('ABSTAIN','equal','ABSTAIN')]]; m=B.metrics(ss,'a'); self.assertEqual(m['correct'],2); self.assertEqual(m['recall']['UP'],0); self.assertEqual(m['confusion']['UP']['ERROR'],1); self.assertEqual(m['pairs'],1); self.assertEqual(m['correct_pairs'],0); self.assertAlmostEqual(m['macro_recall'],2/3)
    def test_selector_failure_not_silent_success(self):
        r={'id':'da1','title':'Q1 revenue growth came in at 5%; analysts expected Q1 revenue growth of 4%','asset':'A'}
        def call(sys,p,**kw):
            if sys==V.SELECT: return '{"actual":true,"expected":1}'
            if sys==V.DIRECT: return '{"label":"DOWN"}'
            raise ValueError('synthetic failure')
        out=B.evaluate_blind(r,call); self.assertEqual(out['labels']['semantic'],'DOWN'); self.assertEqual(out['labels']['evidence'],'ERROR'); self.assertEqual(out['labels']['live'],'ERROR'); self.assertEqual(set(out['errors']),{'live','select'}); self.assertEqual(out['labels']['rules_router'],'UP'); self.assertEqual(out['labels']['evidence_router'],'DOWN')
    def test_freeze_detects_modified_blind_and_key(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(B,'files',return_value=[]):
            root=Path(temp)/'run'; B.freeze(root); B.verify(root)
            for name in ('key.json','blind.json'):
                p=root/name; was=p.read_bytes();p.write_bytes(was+b' ')
                with self.assertRaises(ValueError):B.verify(root)
                p.write_bytes(was)
            with self.assertRaises(FileExistsError):B.freeze(root)
    def test_local_inference_guard(self):
        for host in ('127.0.0.1','::1'):B.local_only('socket.connect',(None,(host,60))); B.local_only('socket.getaddrinfo',(host,80))
        for e,aa in [('socket.connect',(None,('203.0.113.1',10))),('socket.getaddrinfo',('example.invalid',10)),('socket.bind',(None,('0.0.0.0',0))),('subprocess.Popen',('cmd.exe',[],None,None)),('os.system',('echo test',))]:
            with self.assertRaises(PermissionError):B.local_only(e,aa)
        exe=str(Path('data/local_models/test/llama-server.exe').resolve()); B.local_only('subprocess.Popen',(None,'"'+exe+'" --host 127.0.0.1',None,None))
