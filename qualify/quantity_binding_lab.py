"""Research-only, deterministic comparison of document guards. No model, URL, or trading action."""
import argparse, hashlib, json, sys
from collections import Counter, defaultdict
from pathlib import Path
from qualify.local_simulation import install_local_only

# Expected results concern support in the served packet, not whether a fact is true in the world.
FRESH=[('Orchid','Cedar','inspected','shipped','parcels','in April','in March',43,59,50),('Linden','Spruce','approved','submitted','requests','in June','in May',64,80,64),('Willow','Birch','processed','received','samples','on Monday','on Friday',47,65,45),('Pearl','Quartz','resolved','opened','cases','in Q2','in Q1',32,43,34),('Harbor','Delta','released','tested','batches','in August','in July',72,76,72),('Copper','Silver','accepted','dispatched','units','on Thursday','on Tuesday',49,66,45)]
POLICY='Fixed document route, same real Document Reader excerpts and same unedited candidate draft in each arm. Existing Team Lead + deterministic draft injection is the baseline; no live inference or routing quality claim. New comparison reads only served excerpts. Three states: verified, contradicted, abstain. An abstention is never a true positive. Show positive coverage, invalid accepted, abstentions and per-family counts relative to an authored key; do not call abstention a proved contradiction. Design packet is reused and separate. Research criterion: pass all four original controls, no false accepts per fresh split; >=90% supported coverage on controlled forms; report challenge positive coverage including unsupported phrasings. All three excerpts must be served, retain every row including failures. A pass is not approval to ship. No tuned reruns under same protocol.'
def doc(name,text):return {'id':name,'name':name+'.pdf','pages':[text]}
def row(pid,split,question,docs,tag,draft,expected):return dict(packet=pid,split=split,question=question,docs=docs,tag=tag,draft=draft,supported=expected)
def build():
 from qualify.conclusion_packets import cases
 old=next(c for c in cases() if c['case']=='rebate');good='East accepted 96 units during the period. East dispatched 105 units during the period.';out=[row('rebate','design',old['question'],old['documents'],t,d,y) for t,d,y in [('correct',good,True),('connector','Thus, '+good,True),('swapped',good.replace('96','TMP').replace('105','96').replace('TMP','105'),False),('absent',good.replace('96','88'),False)]]
 for idx,(a,b,m,n,unit,scope,prior,x,y,k) in enumerate(FRESH):
  op=['at least','at most','more than','at least','less than','at most'][idx]; met=[x>=k,x<=k,x>k,x>=k,x<k,x<=k][idx]; word='met' if met else 'missed';bad='missed' if met else 'met'
  q=f'Did {a} meet the signed threshold for {m} {unit} {scope}, and what do the movement and review records report?'; first=f'{a} {m} {x} {unit} {scope}';second=f'{a} {n} {y} {unit} {scope}';other=f'{b} {m} {y} {unit} {scope}'; previous=f'{a} {m} {y} {unit} {prior}'; rule=f'{a} requires {op} {k} {m} {unit} {scope}.';docs=[doc('Review',first+'. '+other+'. '+previous+'.'),doc('Movement',second+'.'),doc('Terms',rule)];result=lambda w:f'{a} {w} the threshold for {m} {unit} {scope}.'
  variants=[('correct',first+'. '+second+'.',True),('connector','Therefore, '+first+'. '+second+'.',True),('passive',f'{x} {unit} were {m} by {a} {scope}.',True),('right_source','[Review.pdf:1] '+first+'.',True),('derived',result(word),True),('swap',f'{a} {m} {y} {unit} {scope}. {a} {n} {x} {unit} {scope}.',False),('wrong_subject',f'{b} {m} {x} {unit} {scope}.',False),('wrong_period',f'{a} {m} {x} {unit} {prior}.',False),('wrong_source','[Movement.pdf:1] '+first+'.',False),('wrong_page','[Review.pdf:2] '+first+'.',False),('wrong_unit',f'{a} {m} {x} boxes {scope}.',False),('inverted_verdict',result(bad),False),('unseen_number',first.replace(str(x),str(x+1000))+'.',False),('unsupported_tail',first+'. An auditor confirmed fraud.',False)]
  for tag,d,y0 in variants:out.append(row(a,'controlled',q,docs,tag,d,y0))
 # Separately authored renderings: mixture of valid paraphrases and unsafe near matches. No key passed to verifier.
 sets=[('Granite','What can the signed registers establish about Granite receipts in May?',[doc('Receipts','Granite received 38 parcels in May.'),doc('Sorting','Granite inspected 34 parcels in May.'),doc('Terms','Granite requires at least 36 inspected parcels in May.')],[('plain','Granite received 38 parcels in May.',True),('plain_prose','Of the May parcels, 38 were received by Granite.',True),('two_page_prose','Granite took in 38 parcels in May but only inspected 34.',True),('made_up','Granite inspected 38 parcels in May.',False),('negated','Granite did not receive 38 parcels in May.',False),('non_strict','Granite met the threshold for inspected parcels in May.',False)]),('Maple','What remains supported about Maple cases in June after the audit correction?',[doc('Summary','Maple closed 18 cases in June.'),doc('Correction','The summary was withdrawn. Maple closed 14 cases in June.'),doc('Intake','Maple opened 22 cases in June.')],[('corrected','Maple closed 14 cases in June.',True),('stale','Maple closed 18 cases in June.',False),('unaffected','Maple opened 22 cases in June.',True),('swap','Maple closed 22 cases in June.',False)]),('Ochre','What conclusions are supported by the Ochre July handling records?',[doc('Handling','During July, the handling register lists 27 requests as approved for Ochre.'),doc('Submissions','Ochre submitted 31 requests in July.'),doc('Rules','Ochre requires at least 25 approved requests in July.')],[('table_in_prose','Ochre approved 27 requests in July.',True),('qualified_derived','Ochre met the threshold for approved requests in July.',True),('wrong_source','[Submissions.pdf:1] Ochre approved 27 requests in July.',False),('hypothetical','Ochre approved 31 requests in July.',False)]),('Mica','Did Mica meet the strict target for shipped batches in April?',[doc('A','Mica shipped 20 batches in April.'),doc('B','Mica tested 24 batches in April.'),doc('Terms','Mica requires more than 20 shipped batches in April.')],[('strict','Mica missed the threshold for shipped batches in April.',True),('equal_not_greater','Mica met the threshold for shipped batches in April.',False),('joint','Thus, Mica shipped 20 batches in April. Mica tested 24 batches in April.',True),('swap','Mica shipped 24 batches in April.',False)])]
 for pid,q,ds,vs in sets:
  for tag,d,y in vs:out.append(row(pid,'challenge',q,ds,tag,d,y))
 return out

def encode(v):return json.dumps(v,sort_keys=True,ensure_ascii=True,indent=2).encode()
def store(p,v):
 with p.open('xb') as f:f.write(encode(v))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def protocol():
 import core.orchestrator as O,core.documents as D,core.numeric_guard as N,qualify.conclusion_packets as P,qualify.quantity_binding as B
 return dict(policy=POLICY,hashes={Path(x).name:digest(Path(x)) for x in (__file__,P.__file__,B.__file__,O.__file__,D.__file__,N.__file__)},cases_sha256=hashlib.sha256(encode(build())).hexdigest())
def check_root(root):
 if root==Path('data').resolve() or not root.is_relative_to(Path('data').resolve()): raise ValueError('Use a new private subfolder')
def baseline(q,docs,draft):
 from core import orchestrator as O
 class Router:
  def route(self,question):return O.RoutedQuery('document_question',1.,'unknown',[])
 class Replay:
  def complete(self,*args,**kwargs):return draft
 result=O.TeamLeadOrchestrator(Router(),None,None,Replay(),None).run(q,documents=docs)
 return bool(result.faithful_synthesis)
def totals(rows):
 out={}
 for split in ('design','controlled','challenge'):
  cases=[r for r in rows if r['split']==split];d={'n':len(cases),'packets':len({r['packet'] for r in cases}),'pos':sum(r['supported'] for r in cases),'neg':sum(not r['supported'] for r in cases)}
  for arm in ('live','candidate'):
   d[arm]={k:sum(bool(r['supported'])==gold and ((r[arm]=='verified')==pos) for r in cases) for k,gold,pos in [('tp',True,True),('fn',True,False),('fp',False,True),('tn',False,False)]}; d[arm]['status_counts']=dict(Counter(r[arm] for r in cases))
  out[split]=d
 return out
def run(root):
 from core.documents import select_excerpts
 from qualify.quantity_binding import assess
 if json.loads((root/'protocol.json').read_bytes())!=protocol():raise ValueError('Frozen experiment changed')
 store(root/'started',True);rs=[]
 for c in build():
  ex=select_excerpts(c['docs'],c['question']); seen={e['text'] for e in ex}; expected={d['pages'][0] for d in c['docs']}
  if expected!=seen:raise ValueError('Incomplete packet: '+c['packet']+' '+str(expected-seen))
  new=assess(ex,c['draft']); liv=baseline(c['question'],c['docs'],c['draft']);rs.append({k:c[k] for k in ('packet','tag','split','draft','supported')}|{'live':'verified' if liv else 'blocked','candidate':new['status'],'reason':new['reason'],'witnesses':new['witnesses']})
 ts=totals(rs);checks=ts['design']['candidate']['tp']==2 and ts['design']['candidate']['fp']==0 and all(ts[s]['candidate']['fp']==0 for s in ('controlled','challenge')) and ts['controlled']['candidate']['tp']>=.9*ts['controlled']['pos'];store(root/'rows.json',rs);report=dict(protocol=digest(root/'protocol.json'),totals=ts,lab_gate=checks,production_change=False);store(root/'report.json',report);return report
if __name__=='__main__':
 install_local_only();p=argparse.ArgumentParser();p.add_argument('mode',choices=['freeze','run']);p.add_argument('--root',type=Path,required=True);a=p.parse_args();root=a.root.resolve();check_root(root)
 if a.mode=='freeze':root.mkdir();store(root/'protocol.json',protocol());print(digest(root/'protocol.json'))
 else:print(json.dumps(run(root),indent=1))
