import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from qualify import conclusion_first_lab as M,conclusion_packets as P
class TestDocumentScreen(unittest.TestCase):
 def setUp(self):self.full=P.cases()[0];self.c=P.generation_view(self.full)
 def test_all_new_packets_show_all_three_full_sources(self):
  cs=P.cases();self.assertEqual(len(cs),8);self.assertEqual(len({c['family'] for c in cs}),4)
  for c in cs:
   s=M.docs_view(P.generation_view(c));self.assertTrue(all(d['pages'][0] in s for d in c['documents']));self.assertEqual(len(c['documents']),3)
 def test_gold_and_missing_evidence_refused(self):
  with self.assertRaises(ValueError):M.docs_view(self.full)
  bad=dict(self.c,documents=self.c['documents']+[{'id':'z','name':'z.pdf','pages':['A remote observation.']}])
  with self.assertRaises(ValueError):M.docs_view(bad)
 def test_controls_reject_all_positive_and_failures(self):
  key={str(i):row[2] for i,row in enumerate(M.CONTROLS)};self.assertTrue(M.control_score(key,key)['passes']);self.assertFalse(M.control_score({k:[1,1,1] for k in key},key)['passes']);self.assertFalse(M.control_score({},key)['passes']);g={k:v.copy() for k,v in key.items()};
  for k in ('0','1','2'):g[k][0]=1-g[k][0]
  self.assertFalse(M.control_score(g,key)['passes']);g={k:v.copy() for k,v in key.items()};
  for i in range(5):g[str(i)][i%3]=1-g[str(i)][i%3]
  self.assertFalse(M.control_score(g,key)['passes'])
 def test_grade_type_rejects_booleans_and_wrong_width(self):
  for v in (None,{},[1,2,0],[True,0,1],[0,1]):self.assertFalse(M.items_valid(v))
 def test_current_first_and_critic_really_use_production_and_preserve_evidence(self):
  for arm in M.ARMS:
   sent=[]
   def post(b):
    sent.append(b);return {'usage':{'prompt_tokens':10,'completion_tokens':4},'choices':[{'finish_reason':'stop','message':{'content':'East accepted 96 units during the period.'}}]}
   answer,receipt=M.run_one(self.c,arm,post);self.assertFalse(receipt['error']);self.assertTrue(answer);self.assertEqual(sum(x['cap'] for x in receipt['calls']),sum(b['max_tokens'] for b in sent));self.assertLessEqual(sum(b['max_tokens'] for b in sent),512)
   prompts=[b['messages'][-1]['content'] for b in sent];self.assertTrue(all(self.full['gold'] not in p and self.full['counter'] not in p for p in prompts));self.assertEqual(arm=='first',any(M.FIRST in p for p in prompts));self.assertEqual(arm=='critic',any('Reviewer note (not evidence' in p for p in prompts));self.assertEqual(arm=='direct',receipt['fallback'] is None);self.assertTrue(all(all(d['pages'][0] in p for d in self.c['documents']) for p in prompts))
 def test_refuses_protocol_edits_and_overwrites(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);p=root/'protocol.json';M.save(p,M.protocol());M.verify(root)
   with self.assertRaises(FileExistsError):M.save(p,{})
   p.write_text('{}');
   with self.assertRaises(ValueError):M.verify(root)
 def test_paired_summary_and_no_advance_with_unreliable_grader(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);M.save(r/'protocol.json',M.protocol());bl=[];mapping={};grades={};controls={str(i):row[2] for i,row in enumerate(M.CONTROLS)}
   for c in P.cases():
    for arm in M.ARMS:
     i=c['case']+'-'+arm;bl.append(dict(id=i,case=c['case'],answer='fixture answer'));mapping[i]=dict(arm=arm,calls=[],seconds=1,error=None,fallback=False);grades[i]=[1,1,int(arm=='first')]
   for name,v in [('blind',bl),('mapping',mapping),('reader-grades',grades),('local-grades',{'grades':{**grades,**{k:[1,1,1] for k in controls}},'controls':controls,'errors':{}})]:M.save(r/(name+'.json'),v)
   a=M.summary(r);self.assertEqual(a['scores']['blind_reader']['first'],[8,8,8]);self.assertEqual(a['interval_delta_points_per_case']['blind_reader']['current'],[1,1]);self.assertFalse(a['advance'])
if __name__=='__main__':unittest.main()
