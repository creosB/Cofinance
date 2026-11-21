import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from qualify import packet_boundary_lab as B

class ScopeBoundary(unittest.TestCase):
 def setUp(self):
  self.q='Did Ash meet its full-month uptime commitment?';self.r=B.page('Policy','Ash requires at least 99 percent uptime for the entire month.');self.o=B.page('Ledger','The signed full-month uptime ledger for Ash shows 99 percent.');self.l=B.page('Addendum','Notice: Ledger.pdf is withdrawn without replacement for Ash.');self.docs=[self.r,self.o,self.l];self.m=B.inventory(self.docs)
 def test_part_is_not_the_whole(self):
  v=B.assess(self.q,self.m,self.docs[:2]);self.assertEqual(v,B.control('SCOPE_INCOMPLETE'));self.assertEqual(B.name(B.assess(self.q,B.inventory(self.docs[:2]),self.docs[:2])),'VERIFIED:met');self.assertEqual(B.name(B.assess(self.q,self.m,self.docs)),'WITHDRAWN')
 def test_digest_not_just_count_and_names(self):
  d=copy.deepcopy(self.docs);d[-1]=B.page('Addendum','The dashboard for Ash showed 99 percent uptime for the first week only.');v=B.assess(self.q,self.m,d);self.assertFalse(v['checked']);self.assertIsNone(v['evidence']);self.assertEqual(v['reason'],'SCOPE_INCOMPLETE')
 def test_missing_original_and_duplicate_cannot_certify(self):
  d=copy.deepcopy(self.docs);del d[-1]['source_pages'];self.assertEqual(B.assess(self.q,self.m,d),B.control('SOURCE_UNAVAILABLE'));self.assertFalse(B.assess(self.q,self.m,[self.r,self.o,self.o])['checked']);
  with self.assertRaises(ValueError):B.inventory([self.o,self.o])
 def test_budget_is_not_an_evidence_claim(self):
  with patch.object(B.R,'evaluate',side_effect=AssertionError('verdict ran on an incomplete request')):
   self.assertEqual(B.assess(self.q,self.m,self.docs,page_budget=2),B.control('PAGE_BUDGET')); self.assertEqual(B.assess(self.q,self.m,self.docs,char_budget=1),B.control('CHAR_BUDGET')); self.assertEqual(B.assess(self.q,[],[]),B.control('NO_PACKET'))
 def test_page_count_and_order_invariance(self):
  self.assertEqual(B.inventory(self.docs[::-1]),self.m);self.assertEqual(B.assess(self.q,self.m,self.docs[::-1]),B.assess(self.q,self.m,self.docs));d=copy.deepcopy(self.docs);d[0]['page_count']=2;self.assertFalse(B.assess(self.q,self.m,d)['checked'])
 def test_labels_independent_of_order_and_one_look(self):
  cs=B.design();self.assertEqual(len(cs),len(set(c['id'] for c in cs)));self.assertEqual(len(cs),12);self.assertTrue(all(len(c['docs'])==4 for c in cs));self.assertTrue(any(c['gold']=='WITHDRAWN' for c in cs));self.assertTrue(any(c['gold']=='CONFLICT' for c in cs));
  with tempfile.TemporaryDirectory() as d:
   r=Path(d)/'x';B.freeze(r);B.put(r/'started.json',{});
   with self.assertRaises(FileExistsError):B.result(r)
 def test_full_content_order_matters_within_a_file(self):
  x=B.page('Book','Header');x['pages']+=['Footer'];x['source_pages']+=['Footer'];x['page_count']=2;m=B.inventory([x]);y=copy.deepcopy(x);y['source_pages'].reverse();y['pages'].reverse();self.assertEqual(B.assess(self.q,m,[y]),B.control('SCOPE_INCOMPLETE'))
