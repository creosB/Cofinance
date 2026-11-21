import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from qualify import scope_card_lab as C
class ScopeCard(unittest.TestCase):
    def setUp(self):
        self.ref={'Rule.pdf':'C','One.pdf':'A','Two.pdf':'B'};self.gold={'verdict':'MET','rule':'Rule.pdf','observations':['One.pdf']};self.good={'verdict':'MET','rule':'C','primary':'A','other':'none'}
    def test_strict_pair_scores_and_missing_input(self):
        self.assertTrue(C.classify(self.good,self.gold,self.ref)['joint'])
        for change in ({'rule':'A'},{'primary':'B'},{'primary':'none','other':'A'},{'other':'A'},{'other':'B'}):
            r=C.classify(self.good|change,self.gold,self.ref);self.assertTrue(r['verdict']);self.assertFalse(r['joint'])
        for bad in (None,{},self.good|{'verdict':'?'},self.good|{'rule':None}):self.assertFalse(C.classify(bad,self.gold,self.ref)['valid'])
    def test_conflicting_answers_require_both_refs_and_allow_order_swap(self):
        g=self.gold|{'verdict':'UNKNOWN','observations':['One.pdf','Two.pdf']};a=self.good|{'verdict':'UNKNOWN','other':'B'};self.assertTrue(C.classify(a,g,self.ref)['joint']);self.assertTrue(C.classify(a|{'primary':'B','other':'A'},g,self.ref)['joint']);self.assertFalse(C.classify(a|{'other':'none'},g,self.ref)['joint']);self.assertTrue(C.classify(self.good,g,self.ref)['bad_definite']);self.assertTrue(C.classify(a,self.gold,self.ref)['abstain']);self.assertFalse(C.classify(a,self.gold,self.ref)['bad_definite'])
    def test_gold_excluded_and_evidence_order_independent(self):
        c=C.cases()[0];
        with self.assertRaises(ValueError):C.packet(c)
        b={k:c[k] for k in ('id','q','docs')};a=C.packet(b);self.assertEqual(a,C.packet(b|{'docs':b['docs'][::-1]}));self.assertTrue(all(d['pages'][0] in a[0] for d in b['docs']));self.assertTrue(a[-1]);self.assertNotIn('Checked card',a[0]);self.assertEqual(a[3]['rule'],a[2]['Covenant.pdf'])
    def test_absent_card_never_invents_a_source(self):
        c=next(x for x in C.cases() if x['id']=='unreconciled');a=C.packet({k:c[k] for k in ('id','q','docs')});self.assertFalse(a[-1]);self.assertEqual(a[3],dict(verdict='UNKNOWN',rule='none',primary='none',other='none'));self.assertIn('not itself a verdict',a[1]);self.assertTrue(C.classify(a[3],c['gold'],a[2])['verdict']);self.assertFalse(C.classify(a[3],c['gold'],a[2])['joint'])
    def test_freeze_and_one_look(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);C.X.save(r/'protocol.json',{});
            with self.assertRaises(ValueError):C.run(r)
            (r/'protocol.json').unlink();C.X.save(r/'protocol.json',C.pr());C.X.save(r/'started',True)
            with self.assertRaises(FileExistsError):C.run(r)
if __name__=='__main__':unittest.main()
