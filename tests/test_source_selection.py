import unittest,hashlib,itertools
from copy import deepcopy
from qualify import source_selection as S,source_selection_cases as C

def tq():return {'subject':'Polar Pad','scope':'Poland','question':'Who can use it?','documents':[{'id':'A','title':'Product note','body':' Polar Pad is now available in Poland. Availability is limited to invited users. '},{'id':'B','title':'Office','body':'Support moved offices.'}]}
class Selector(unittest.TestCase):
 def test_code_points_to_original_complete_span(self):
  q=tq();sp=C.index(q['documents']);ids=S.rule(q,sp);self.assertTrue(ids);lookup={x['id']:x for x in sp};
  for i in ids:
   x=lookup[i];doc=next(d for d in q['documents'] if d['id']==x['doc']);self.assertEqual(doc[x['field']][x['start']:x['end']],x['text']);self.assertEqual(x['sha'],hashlib.sha256(doc[x['field']].encode()).hexdigest());self.assertNotIn('verdict',x)
 def test_no_annotation_or_truncation(self):
  q=tq();sp=C.index(q['documents']);self.assertIn('Support moved offices.',S.body(q,sp,['AS1']));self.assertTrue(S.body(q,sp,['AS1']).startswith(S.body(q,sp)));
  for broken in (sp[:-1],[x for x in sp if x['id']!='AS2'],[dict(x,text='altered') for x in sp]):
   with self.assertRaises(ValueError):S.body(q,broken,['AS1'])
  for hint in (['Z99'],['AS1','AS1']):
   with self.assertRaises(ValueError):S.body(q,sp,hint)
  for fn in (S.body,S.rule):
   with self.assertRaises(ValueError):fn(dict(q,gold='x'),sp)
 def test_equivalent_witnesses_and_extra_cost(self):
  ids=set('abcd');key=[['a','b'],['c']];good=S.grade(['b','c'],ids,key,[1]);self.assertTrue(good['exact']);self.assertEqual((good['tp'],good['fp'],good['fn'],good['counter_missed']),(2,0,0,0));bad=S.grade(['a','b','c'],ids,key,[1]);self.assertTrue(bad['all_facts']);self.assertFalse(bad['exact']);self.assertEqual(bad['fp'],1);miss=S.grade(['a','d'],ids,key,[1]);self.assertEqual((miss['fp'],miss['fn'],miss['counter_missed']),(1,1,1))
 def test_silence_and_invalid_do_not_free_ride(self):
  self.assertTrue(S.grade([],{'x'},[],[])['exact']);self.assertFalse(S.grade([],{'x'},[['x']],[])['exact']);self.assertTrue(S.grade([],{'x'},[['x']],[])['empty_miss']);
  for sel,invalid in ((['unknown'],False),(['x','x'],False),(None,False),([],True)):
   d=S.grade(sel,{'x'},[],[],invalid);self.assertFalse(d['valid']);self.assertFalse(d['exact']);self.assertFalse(d['all_facts'])
 def test_gate_requirements(self):
  a={'n':18,'valid':18,'exact':12,'counter_missed':1,'empty_miss':1};r={s:{arm:dict(a) for arm in S.ARMS} for s in ['issuer','contrast','all']};r['all']['direct']['exact']=10;self.assertEqual(S.gate(r,'rule'),'RETEST_ONLY');
  for scope,arm,k,v,expect in [('all','rule','exact',11,'NO_PROMOTION'),('all','rule','counter_missed',2,'NO_PROMOTION'),('all','rule','empty_miss',2,'NO_PROMOTION'),('issuer','rule','exact',11,'NO_PROMOTION'),('all','direct','valid',16,'INCONCLUSIVE')]:
   d=deepcopy(r);d[scope][arm][k]=v;self.assertEqual(S.gate(d,'rule'),expect)
 def test_order_not_a_rank_and_lexical_has_no_key(self):
  q=tq();s=C.index(q['documents']);ref=S.rule(q,s);h=S.lexical(q,s);self.assertEqual(len(h),1)
  for p in itertools.permutations(q['documents']):
   t=dict(q,documents=list(p));ss=C.index(t['documents']);self.assertEqual(S.rule(t,ss),ref);self.assertEqual(S.lexical(t,ss),h)
 def test_total_boolean_interface(self):
  from qualify import source_selection_mask as M
  ids=['AS1','BS2'];self.assertEqual(M.decode('{"BS2":false,"AS1":true}',ids),['AS1']);self.assertEqual(M.schema(ids)['required'],ids)
  for x in ('{"AS1":true}', '{"AS1":true,"BS2":false,"CS1":true}', '{"AS1":true,"AS1":false,"BS2":true}', '{"AS1":0,"BS2":false}', '{"AS1":"false","BS2":false}', '["AS1","BS2"]'):
   with self.assertRaises(ValueError):M.decode(x,ids)
 def test_bootstrap_not_pseudoreplicating_arms(self):
  rows=[dict(case=str(i),split='issuer',family=str(i//2),arm=a,exact=(a=='rule' and i==0) or i==2) for i,a in itertools.product(range(4),('rule','direct'))];r=S.paired(rows,'rule');self.assertEqual((r['wins'],r['ties'],r['losses'],r['clusters']),(1,3,0,2))
