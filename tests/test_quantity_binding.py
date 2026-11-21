import hashlib,inspect,itertools,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from qualify import quantity_binding as Q, quantity_binding_lab as L

def ex(name,text,page=1):return dict(name=name+'.pdf',page=page,text=text)
class QuantityBinding(unittest.TestCase):
 def setUp(self):
  self.x=ex('Count','Aster booked 15 orders in July. Iris booked 31 orders in July. Aster booked 31 orders in June.')
  self.y=ex('Load','Aster fulfilled 31 orders in July.');self.limit=ex('Policy','Aster requires at least 15 booked orders in July.');self.docs=[self.x,self.y,self.limit];self.right='Aster booked 15 orders in July.'
 def check(self,t,status,docs=None):
  result=Q.assess(self.docs if docs is None else docs,t);self.assertEqual(result['status'],status,(t,result));return result
 def test_all_bindings_and_source_receipts(self):
  for t in (self.right,'Thus, '+self.right,'[Count.pdf:1] '+self.right,'15 orders were booked by Aster in July.','Therefore, '+self.right+' Aster fulfilled 31 orders in July.'):
   r=self.check(t,'verified');w=r['witnesses'][0];self.assertEqual((w['subject'],w['measure'],w['value'],w['unit'],w['period']),('aster','booked',15,'orders','in july'));self.assertEqual(w['document'],'Count.pdf');self.assertEqual(w['page'],1);self.assertEqual(w['quote'],self.x['text'][w['start']:w['end']]);self.assertEqual(w['excerpt_sha256'],hashlib.sha256(self.x['text'].encode()).hexdigest())
 def test_subject_measure_period_not_bags_of_words_or_numbers(self):
  for t in ('Aster booked 31 orders in July.','Iris booked 15 orders in July.','Aster fulfilled 15 orders in July.','Aster booked 15 orders in June.'): self.check(t,'contradicted')
  for t in ('Aster booked 15 parcels in July.','Orion booked 15 orders in July.','Aster booked 15 orders in May.'):self.check(t,'abstain')
 def test_wrong_citation_and_source_identity(self):
  for cite in ('[Load.pdf:1] ','[Count.pdf:2] ','[Absent.pdf:1] '):self.check(cite+self.right,'abstain')
  self.check(self.right,'abstain',self.docs+[dict(self.x,name='COUNT.pdf')]);self.check(self.right,'verified',[dict(self.x,page=2),self.x]);self.check('[Count.pdf:2] '+self.right,'verified',[dict(self.x,page=2)])
 def test_every_answer_and_every_source_clause_accounted_for(self):
  for t in (self.right+' Aster won a contract.', 'Probably, '+self.right,'Aster never booked 15 orders in July.','If '+self.right,'Aster booked 1.5 orders in July.','', '...'):self.check(t,'abstain')
  for text in (self.x['text']+' The booked amount is a hypothetical.', self.x['text']+' Disregard the earlier table.',self.x['text']+' ' * 900,self.x['text']+'…'):self.check(self.right,'abstain',[dict(self.x,text=text)])
 def test_conflict_including_explicit_citation_and_whole_packet_unknown(self):
  self.check('[Count.pdf:1] '+self.right,'abstain',self.docs+[ex('Amend','Aster booked 16 orders in July.')]);self.check(self.right,'abstain',[])
 def test_derived_requires_both_roles_and_uses_source_operator(self):
  t='Aster met the threshold for booked orders in July.';r=self.check(t,'verified');self.assertEqual({w['document'] for w in r['witnesses']},{'Count.pdf','Policy.pdf'}); self.assertEqual({w['role'] for w in r['witnesses']},{'fact','rule'})
  self.check(t,'abstain',[self.x]);self.check(t,'abstain',[self.limit]);self.check('[Count.pdf:1] '+t,'abstain');self.check(t,'contradicted',[self.x,ex('Policy','Aster requires more than 15 booked orders in July.')]);self.check('Aster requires at most 15 booked orders in July.','contradicted')
 def test_comparisons_oracle_and_order_independence(self):
  for op in ('at least','at most','more than','less than'):
   for x,k in itertools.product((0,2,4),repeat=2):
    truth={'at least':x>=k,'at most':x<=k,'more than':x>k,'less than':x<k}[op];docs=[ex('Sheet',f'Aster booked {x} orders in July.'),ex('Contract',f'Aster requires {op} {k} booked orders in July.')]
    for order in (docs,docs[::-1]):
     for v,expected in [('met',truth),('missed',not truth)]:self.check(f'Aster {v} the threshold for booked orders in July.','verified' if expected else 'contradicted',order)
 def test_live_baseline_uses_real_guard_and_never_imports_lab(self):
  from core import orchestrator as O
  self.assertNotIn('quantity_binding',inspect.getsource(O));cases=[x for x in L.build() if x['split']=='design']; by={c['tag']:L.baseline(c['question'],c['docs'],c['draft']) for c in cases};self.assertEqual(by,dict(correct=True,connector=False,swapped=True,absent=False))
 def test_protocol_and_one_look(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);L.store(root/'protocol.json',{'wrong':1});
   with self.assertRaises(ValueError):L.run(root)
   with self.assertRaises(FileExistsError):L.store(root/'protocol.json',{})
   (root/'protocol.json').unlink();L.store(root/'protocol.json',L.protocol());L.store(root/'started',True)
   with self.assertRaises(FileExistsError):L.run(root)
 def test_counts_do_not_drop_unknown_or_credit_it_as_positive(self):
  rows=[{'split':'controlled','packet':'a','supported':True,'live':'verified','candidate':'abstain'},{'split':'controlled','packet':'a','supported':False,'live':'verified','candidate':'abstain'}];d=L.totals(rows)['controlled'];self.assertEqual([d['candidate'][k] for k in ('tp','fn','fp','tn')],[0,1,0,1]);self.assertEqual(d['candidate']['status_counts'],{'abstain':2})
if __name__=='__main__':unittest.main()
