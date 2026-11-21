import unittest
from copy import deepcopy
from qualify import rollout_transfer as T
class Transfer(unittest.TestCase):
 def test_every_notice_reviewed_before_scores(self):
  p=[{'id':'one'},{'id':'two'}];g={'one':{'verdict':'LIVE','sources':['B'],'note':'observed release'},'two':{'verdict':'EXCLUDE','sources':[],'note':'ambiguous'}};T.keys_ok(g,p)
  for bad in ({'one':g['one']},{**g,'one':{**g['one'],'sources':['B','B']}},{**g,'one':{**g['one'],'sources':[]}},{**g,'two':{**g['two'],'note':''}}):
   with self.assertRaises(ValueError):T.keys_ok(bad,p)
 def test_coverage_cost_and_inconclusive(self):
  k={x['id']:{'verdict':x['tag']} for x in T.seeds()}; stats={'code':{'n':12,'valid':12,'joint':9,'miss':1,'false_live':0},'direct':{'n':12,'valid':12,'joint':7,'miss':2,'false_live':1}};self.assertEqual(T.decision(k,stats),'FIELD_RETEST_ONLY')
  for arm,field,val,result in [('code','joint',8,'NO_INTEGRATION'),('code','miss',3,'NO_INTEGRATION'),('code','false_live',2,'NO_INTEGRATION'),('direct','valid',10,'INCONCLUSIVE'),('direct','n',11,'INCONCLUSIVE')]:
   s=deepcopy(stats);s[arm][field]=val;self.assertEqual(T.decision(k,s),result)
  sparse={i:{'verdict':T.V.NO} for i in k};self.assertEqual(T.decision(sparse,stats),'INCONCLUSIVE')
 def test_separate_author_and_unchanged_method(self):
  from unittest.mock import patch
  from pathlib import Path
  src=Path(T.V.__file__).read_bytes();self.assertEqual(T.P.digest(src.replace(b'\r\n',b'\n')),'eeb8146ffff614854e7545e1277355fc647425bbda2de9c14ba50ac56679fe24')
  frozen={'files':{'qualify/rollout_evidence.py':T.P.digest(src)}}
  with patch.object(T.S,'read',return_value=frozen) as read:p=T.spec()
  read.assert_called_once_with(Path('data/rollout_stage_v1/protocol.json'));self.assertNotEqual(T.AUTHOR,T.S.MODEL);self.assertEqual(p['frozen_verifier'],p['first_protocol']['files']['qualify/rollout_evidence.py']);self.assertEqual(len({c['id'] for c in T.seeds()}),12);self.assertNotIn('regex',p['author_prompt'].lower())
