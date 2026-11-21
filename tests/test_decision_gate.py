import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from qualify import decision_gate as G

def key(v='SUPPORTED',refs=('A','B')):return {'verdict':v,'citations':list(refs)}
def arm(j=5,w=1,u=2,t=100,s=5):return dict(joint=j,wrong_definite=w,unnecessary=u,total_tokens=t,median_s=s)
class DecisionGate(unittest.TestCase):
 def test_exact_grounded_joint(self):
  self.assertEqual(G.item_grade(['S',1,['A','B']],key())['joint'],1)
  for g in (['S',0,['A','B']],['S',1,['A']],['S',1,['A','B','C']],['C',1,['A','B']]):self.assertEqual(G.item_grade(g,key())['joint'],0)
 def test_unnecessary_and_wrong(self):
  for s in ['U','A']:self.assertEqual(G.item_grade([s,0,[]],key())['unnecessary'],1)
  self.assertEqual(G.item_grade(['A',1,['A','B']],key('NOT ESTABLISHED'))['stance'],0)
  self.assertEqual(G.item_grade(['U',1,['A','B']],key('NOT ESTABLISHED'))['joint'],1)
  self.assertEqual(G.item_grade(['S',1,['A','B']],key('NOT ESTABLISHED'))['wrong_definite'],1)
 def test_matching_ceiling_does_not_excuse_cost(self):
  base=arm();gain=arm(7);f={'x':1,'y':1}; self.assertEqual(G.decision(gain,base,f,True),'GO')
  for bad in [arm(6),arm(7,w=2),arm(7,u=3),arm(7,t=151),arm(7,s=7.51)]:self.assertEqual(G.decision(bad,base,f,True),'NO-GO')
  self.assertEqual(G.decision(arm(),base,{'x':0,'y':0},True),'NO-GO');self.assertEqual(G.decision(gain,base,{'only':2},True),'NO-GO');self.assertEqual(G.decision(gain,base,f,False),'INCONCLUSIVE')
 def test_blind_boundary(self):
  with patch.object(G.C,'docs_view',return_value='valid fixture') as renderer:
   with self.assertRaises(ValueError):G.blindview({'case':'x','question':'q','documents':[],'verdict':'S'})
   renderer.assert_not_called()
 def test_grades_cannot_smuggle_confidence_or_duplicates(self):
  for row in (['yes',1,[]],['S',True,[]],['S',0,['B','A']],['S',0,['A','A']],['C',1,['Z']]): self.assertFalse(G.valid(row))
 def test_file_is_sealed(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'f';G.dump(p,{'a':1});self.assertEqual(G.read(p),{'a':1});
   with self.assertRaises(FileExistsError):G.dump(p,{'a':2})
 def test_score_real_small_fixture(self):
  import itertools
  with tempfile.TemporaryDirectory() as d,patch.object(G,'verify',return_value=None):
   r=Path(d);ks={str(i):{**key(), 'family':str(i//3)} for i in range(12)}; b=[];g={};m={}
   for c,a in itertools.product(ks,G.ARMS):
    i=c+a;b.append({'id':i,'case':c});g[i]=['S',1,['A','B']];m[i]=dict(kind='run',arm=a,calls=[{'input':20,'output':10,'cap':200,'finish':'stop'}],seconds=1,error=None,fallback=False)
   for n in range(6):
    i='ctrl'+str(n);b.append({'id':i,'case':'0'});g[i]=['U',0,[]];m[i]={'kind':'control','expected':['U',0,[]]}
   for f,v in [('protocol',{}),('blind',b),('mapping',m),('rubric-key',ks),('grades',g)]:G.dump(r/(f+'.json'),v)
   from contextlib import redirect_stdout
   import io
   with redirect_stdout(io.StringIO()):G.score(r)
   x=G.read(r/'report.json');self.assertTrue(x['valid']);self.assertEqual(x['result'],'NO-GO');self.assertEqual(x['paired']['ties'],12)
   (r/'items.json').unlink();(r/'report.json').unlink();(r/'grades.json').unlink();g['ctrl4']=['S',1,[]];G.dump(r/'grades.json',g)
   with redirect_stdout(io.StringIO()):G.score(r)
   self.assertEqual(G.read(r/'report.json')['result'],'INCONCLUSIVE')
