import itertools,hashlib,tempfile,unittest
from copy import deepcopy
from pathlib import Path
from qualify import rollout_evidence as V,rollout_screen as S,rollout_packets as P

def task(*docs,sub='Nacre',scope='Iceland'):
 return dict(subject=sub,scope=scope,question='Is this generally available?',documents=[{'id':i,'title':t,'body':b} for i,t,b in docs])
POS=('A','Launch bulletin','Nacre is now available in Iceland.');NEG=('B','Notice','Nacre is not yet available in Iceland.');OTHER=('C','Other','Zinc is now available in Iceland.');COR=('B','Correction','Correction to the launch bulletin: Nacre has not launched in Iceland.')
class Rollout(unittest.TestCase):
 def test_scope_identity_and_full_predicate(self):
  for q,tag in [(task(POS),V.YES),(task(NEG),V.NO),(task(POS,scope='Poland'),V.UNK),(task(('A','Note','Nacre Plus is now available in Iceland.')),V.UNK),(task(('A','Note','If Nacre is now available in Iceland, customers should receive an invitation.')),V.UNK),(task(('A','Note','Nacre is now available in Iceland for invited testers only.')),V.UNK),(task(('A','Note','Nacre is expected to launch in Iceland.')),V.UNK),(task(('A','Note','Nacre is now available in Iceland. Availability is limited to invited users.')),V.UNK)]:
   with self.subTest(q=q):self.assertEqual(V.evaluate(q)['verdict'],tag)
 def test_conflict_and_named_correction(self):
  a=V.evaluate(task(POS,NEG,OTHER));self.assertEqual((a['verdict'],a['sources']),(V.UNK,['A','B']));a=V.evaluate(task(POS,COR,OTHER));self.assertEqual((a['verdict'],a['sources']),(V.NO,['A','B']));self.assertEqual(V.evaluate(task(('A','Different bulletin',POS[2]),COR))['verdict'],V.UNK);self.assertEqual(V.evaluate(task(POS,('B','Notice','A correction to the launch date of Nacre is pending.')))['verdict'],V.UNK)
 def test_endpoints_bounded_and_receipts_verbatim(self):
  ds=(('A','Notice','  Welcome!  Nacre is now available in Iceland.  A separate schedule exists.'),OTHER);q=task(*ds);a=V.evaluate(q);self.assertEqual(a['sources'],['A']);self.assertEqual(len(a['receipts']),1);r=a['receipts'][0];src=ds[0][2]; self.assertEqual(src[r['start']:r['end']],r['quote']);self.assertEqual(r['quote'],'Nacre is now available in Iceland.');self.assertEqual(hashlib.sha256(src.encode()).hexdigest(),r['sha256']);self.assertEqual(V.evaluate(task(*ds[::-1])),a)
 def test_no_unrelated_scope_or_invented_citations(self):
  a=V.evaluate(task(('A','Notice','Nacre is now available in France.'),('B','Notice','Nacre was announced for Iceland.'),OTHER));self.assertEqual((a['verdict'],a['sources']),(V.UNK,['B'])); a=V.evaluate(task(OTHER));self.assertEqual((a['verdict'],a['sources'],a['receipts']),(V.UNK,[],[]));
 def test_labels_never_in_model_or_rule_call(self):
  p=task(POS,NEG,OTHER);p['gold']={'verdict':'NOT_LIVE'}
  for fn in (S.prompt,V.evaluate):
   with self.assertRaises(ValueError):fn(p)
  p=task(POS);p['documents'][0]['key']='gold';
  with self.assertRaises(ValueError):S.prompt(p)
  with self.assertRaises(AssertionError):V.evaluate(p)
 def test_metrics_count_invalid_partial_extra_and_silent(self):
  g={'verdict':V.NO,'sources':['B']};ans=lambda tag,ids:dict(verdict=tag,sources=ids);self.assertTrue(S.classify(ans(V.NO,['B']),g)['joint']);self.assertFalse(S.classify(ans(V.NO,['A','B']),g)['joint']);self.assertTrue(S.classify(ans(V.YES,['B']),g)['false_live']);self.assertTrue(S.classify(ans(V.UNK,[]),g)['miss']);self.assertTrue(S.classify(None,g)['miss']);self.assertFalse(S.classify(ans(V.NO,['B','B']),g)['valid']);self.assertFalse(S.classify(ans(V.NO,['B']),g,True)['joint']);self.assertFalse(S.classify(ans(V.UNK,[]),dict(verdict=V.UNK,sources=['B']))['joint'])
 def test_gate_is_joint_and_needs_no_safety_coverage_tradeoff(self):
  a={'n':24,'valid':24,'joint':13,'miss':1,'false_live':1};b=dict(a,joint=11,false_live=3);base={'all':{'code':a,'direct':b},'contrast':{'code':{'joint':7},'direct':{'joint':6}},'archive':{'code':{'joint':6},'direct':{'joint':5}}};self.assertEqual(S.decision(base),'INDEPENDENT_RETEST');
  for branch,arm,field,val,result in [('all','code','joint',12,'NO_PROMOTION'),('all','code','miss',2,'NO_PROMOTION'),('all','direct','false_live',1,'NO_PROMOTION'),('archive','code','joint',4,'NO_PROMOTION'),('all','direct','valid',22,'INCONCLUSIVE')]:
   tmp=deepcopy(base);tmp[branch][arm][field]=val;self.assertEqual(S.decision(tmp),result)
 def test_deterministic_exhibit_shuffle_and_no_rewrite(self):
  import json
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   cache=Path(tmp)/'notices.jsonl'; names=sorted({prefix for row in P.ARCH for prefix in row[4]}); cache.write_text('\n'.join(json.dumps({'source':'fixture newsroom','title':name+' fixture','text':'Local synthetic notice.','url':'urn:fixture:'+str(i)}) for i,name in enumerate(names)),encoding='utf8')
   with patch.object(P,'CACHE',cache):
    cs=P.build();self.assertEqual(len(cs),24);self.assertEqual(P.enc(cs),P.enc(P.build()));self.assertTrue(all(len({d['id'] for d in c['documents']})==3 for c in cs))
   p=Path(tmp)/'test.json';P.write(p,{'a':1});
   with self.assertRaises(FileExistsError):P.write(p,{'a':2})
 def test_paired_ties_count_and_clusters_do_not_split_twins(self):
  r=[dict(case=f'q{i}',split='example',family='a' if i<2 else 'b',arm=arm,joint=(arm=='code' and i==0) or i==2) for i,arm in itertools.product(range(3),('direct','code'))];ds=S.interval(r);self.assertEqual((ds['wins'],ds['ties'],ds['losses'],ds['cluster_count']),(1,2,0,2))
