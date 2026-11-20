from pathlib import Path
from unittest.mock import patch
import ast,unittest
from qualify import headline_evidence_lab as E

class TestHeadlineEvidence(unittest.TestCase):
    def test_asset_boundaries_and_negative_control(self):
        self.assertFalse(E.named('AMD','CAMDENCE')); self.assertTrue(E.named('AMD','New Ryzen CPU')); t='Apple and Microsoft sign new agreement'; self.assertTrue(E.named('AAPL',t)); u=E.other('AAPL',t); self.assertNotIn(u,('AAPL','MSFT'));self.assertFalse(E.named(u,t));self.assertEqual(u,E.other('AAPL',t))
    def test_verbatim_quote_required_and_not_sufficient(self):
        t='Nvidia signs new supply agreement'; d={'category':'OPERATIONS','quote':'signs new supply agreement'}; self.assertEqual(E.assess('NVDA',t,d),(True,'accepted'));self.assertEqual(E.assess('AAPL',t,d),(False,'no_explicit_asset'));self.assertEqual(E.assess('NVDA',t,{**d,'quote':'signs new contracts'}),(False,'unverified_quote'));self.assertEqual(E.assess('NVDA',t,{**d,'quote':'supply agreement'}),(False,'unverified_quote'));self.assertEqual(E.assess('NVDA',t,{**d,'category':'NONE'})[0],False)
    def test_invalid_schema_refused(self):
        for x in (None, {},{'category':'UP','quote':'x'},{'category':'NONE','quote':10}):
            with self.assertRaises(ValueError):E.assess('AMD','test',x)
    def test_weights_keep_sign_and_no_news_neutral(self):
        ts=['AMD wins supply deal','Unnamed report shocks readers']; j={'AMD|'+ts[0]:.7,'AMD|'+ts[1]:-.6}; rev={E.key('AMD',t):{'accepted':i==0} for i,t in enumerate(ts)}; f=E.synthetic_features('AMD',ts,j,rev);self.assertAlmostEqual(f['old'],.1);self.assertAlmostEqual(f['review'],.4);self.assertAlmostEqual(f['uniform'],.075);self.assertAlmostEqual(f['literal'],.4);self.assertEqual(E.synthetic_features('AMD',[],{},{}),{k:0 for k in f}); self.assertEqual(E.weight(False),.5)
    def test_selection_blind_and_deterministic(self):
        rr=[{'asset':k,'review':n,'y':False} for k,n in [('B',2),('A',-2),('C',-1)]];self.assertEqual(E.top(rr,'review',1),{'A':False});self.assertEqual(E.top(rr[::-1],'review',1),{'A':False});self.assertEqual(E.top([{**r,'y':True} for r in rr],'review',1),{'A':False})
    def test_same_number_each_day_with_abstention(self):
        r=[{'origin':'a','asset':t,'old':x,'review':z,'y':v} for t,x,z,v in [('a',1.,.1,False),('b',.2,.9,True),('c',0.,0.,False)]]; r+=[{'origin':'b','asset':'x','old':.1,'review':.7,'y':True}];d=E.matched(r);self.assertEqual((d['calls_each'],d['correct_current'],d['correct_review']),(1,0,1)); self.assertEqual(d['called_accuracy_delta'],1.)
    def test_no_real_calls_without_installing_guard(self):
        m=next(n for n in ast.parse(Path(E.__file__).read_text(encoding='utf8')).body if isinstance(n,ast.FunctionDef) and n.name=='main');self.assertEqual(ast.unparse(m.body[0]),'sys.addaudithook(B.local_only)')
