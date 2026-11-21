import copy,itertools,tempfile,unittest
from pathlib import Path
from qualify import scope_extraction as E,scope_extraction_lab as L

def doc(n,s):return dict(name=n+'.pdf',page=1,text=s)
class ScopeCheck(unittest.TestCase):
    def setUp(self):
        self.q='Did Arrow meet its quarter-end unrestricted cash floor?';self.rule=doc('Covenant','Arrow must hold at least 22 million dollars of unrestricted cash at quarter end.');self.obs=doc('Ledger','The signed quarter-end ledger records 22 million dollars of unrestricted cash for Arrow.');self.flash=doc('Flash','The preliminary quarter-end total cash figure for Arrow was 39 million dollars.');self.ds=[self.rule,self.obs,self.flash]
    def test_threshold_operators_and_exact_decimals(self):
        for op,x,truth in [('strictly above','22','missed'),('at least','22','met'),('strictly above','22.0001','met'),('at least','21.9999','missed')]:
            ds=[dict(self.rule,text=self.rule['text'].replace('at least',op)),dict(self.obs,text=self.obs['text'].replace('22',x)),self.flash];self.assertEqual(E.analyze(self.q,ds)['card']['verdict'],truth)
    def test_cash_scope_issuer_observation_authority(self):
        for edited in (self.obs['text'].replace('unrestricted cash','total cash'),self.obs['text'].replace('unrestricted cash','restricted cash'),self.obs['text'].replace('Arrow','Elsewhere'),self.obs['text'].replace('dollars','euros'),self.obs['text'].replace('quarter-end','prior-quarter'),self.obs['text'].replace('signed','forecast')):
            self.assertIsNone(E.analyze(self.q,[self.rule,dict(self.obs,text=edited),self.flash])['card'])
        self.assertIsNone(E.analyze(self.q,[self.rule,dict(self.flash,text=self.flash['text'].replace('total cash','unrestricted cash'))])['card'])
    def test_week_and_month_are_not_exchangeable(self):
        q='Did Kite meet its full-month uptime commitment?';policy=doc('Policy','Kite requires at least 96 percent uptime for the entire month.');week=doc('Dash','The dashboard for Kite showed 98 percent uptime for the first week only.');self.assertIsNone(E.analyze(q,[policy,week])['card']);act=doc('Obs','The signed full-month uptime ledger for Kite shows 95.9 percent.');r=E.analyze(q,[policy,week,act]);self.assertEqual((r['card']['verdict'],r['card']['actual']),('missed','95.9'));self.assertIsNone(E.analyze(q,[policy,dict(act,text=act['text'].replace('95.9','105'))])['card']);self.assertIsNone(E.analyze(q,[policy,dict(act,text=act['text'].replace('Kite','Birch'))])['card'])
    def test_signed_conflicts_and_explicit_supersession(self):
        a=doc('Flash',self.obs['text'].replace('22','19'));ds=[self.rule,self.obs,a];self.assertIsNone(E.analyze(self.q,ds)['card']);b=doc('Final','The signed quarter-end reconciliation supersedes the flash and records 24 million dollars of unrestricted cash for Arrow.');
        for order in itertools.permutations([self.rule,b,a]):self.assertEqual(E.analyze(self.q,order)['card']['actual'],'24')
        for wrong in [dict(a,name='Wrong.pdf'),dict(a,text=a['text'].replace('Arrow','Jet')),dict(a,text='The monthly uptime commitment was strictly above 99 percent for the entire month.')]:self.assertIsNone(E.analyze(self.q,[self.rule,b,wrong])['card'])
        self.assertIsNone(E.analyze(self.q,[self.rule,b,a,self.obs])['card']);self.assertIsNone(E.analyze(self.q,[self.rule,b,a,dict(b,name='Other.pdf')])['card'])
    def test_conflicting_rules_and_unresolved_preliminary(self):
        self.assertIsNone(E.analyze(self.q,self.ds+[dict(self.rule,name='Rule2.pdf',text=self.rule['text'].replace('at least','more than'))])['card']);self.assertIsNone(E.analyze(self.q,[self.rule,self.obs,dict(self.flash,text=self.flash['text'].replace('total cash','unrestricted cash'))])['card'])
    def test_whole_excerpt_not_substring(self):
        for t in (self.obs['text']+' This is conditional.', 'If confirmed, '+self.obs['text'],self.obs['text']+'...',self.obs['text']+' '*900,self.obs['text'].replace('22','-22'),self.obs['text'].replace('22','22,000'),self.obs['text'].replace('22','22 billion')):self.assertIsNone(E.analyze(self.q,[self.rule,dict(self.obs,text=t)])['card'])
        self.assertIsNone(E.analyze(self.q,self.ds+[dict(self.obs,name='LEDGER.PDF')])['card']);self.assertIsNone(E.analyze('What is tomorrow’s price?',self.ds)['card'])
    def test_witness_and_tamper_checks(self):
        ds=[self.rule,dict(self.obs,text='  '+self.obs['text']+' '),self.flash];r=E.analyze(self.q,ds);self.assertTrue(L.evidence_ok(r,ds));self.assertEqual(r['sources'][1]['witness']['start'],2);self.assertEqual(L.classify(r,r['card'].copy(),ds),'exact');self.assertEqual(L.classify(r,None,ds),'unsafe')
        for k,v in [('quote','altered'),('end',1),('start',0),('excerpt_sha256','x')]:
            z=copy.deepcopy(r);z['sources'][1]['witness'][k]=v;self.assertFalse(L.evidence_ok(z,ds))
        z=copy.deepcopy(r);z['card']['actual_file']='Flash.pdf';self.assertFalse(L.evidence_ok(z,ds));self.assertEqual(L.classify(z,r['card'],ds),'unsafe')
    def test_frozen_rule_and_one_look(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);L.save(p/'protocol.json',{});
            with self.assertRaises(ValueError):L.run(p)
            (p/'protocol.json').unlink();L.save(p/'protocol.json',L.protocol());L.save(p/'started',True)
            with self.assertRaises(FileExistsError):L.run(p)
    def test_gate_rejects_unsafe_and_low_coverage(self):
        scores={k:dict(exact=n,decidable=n,unsafe=0) for k,n in [('design',2),('controlled',10),('challenge',4)]};self.assertTrue(L.passed(scores));scores['controlled']['unsafe']=1;self.assertFalse(L.passed(scores));scores['controlled']['unsafe']=0;scores['challenge']['exact']=1;self.assertFalse(L.passed(scores));scores['challenge']['exact']=2;scores['controlled']['exact']=7;self.assertFalse(L.passed(scores))
if __name__=='__main__':unittest.main()
