"""One-look, local document-route go/no-go. No changes to product behavior."""
import argparse,collections,hashlib,itertools,json,random,secrets,statistics,sys
from pathlib import Path
from qualify import conclusion_first_lab as C,decision_gate_cases as Q

ARMS=('direct','current'); STANCES=('S','C','U','A'); K={'SUPPORTED':'S','CONTRADICTED':'C','NOT ESTABLISHED':'U'}
POLICY={'research_only':True,'unit':'12 new practical analyst questions on cached issuer excerpts, 4 mutually related families; authored proxies, not nominated by a user. 4 supported, 4 contradicted, 4 not established. These precede any outputs. Facts are evaluated relative to the supplied record.','arms':'Pinned AtomicChat thinking off: strong direct Q&A and actual production document Lead/guards with normal fallback; same selected excerpts in the same order, fixed document route (not retrieval/routing/debate in general). No new prompt alternatives. Direct uses the published strong three-sentence prompt, both receive the same request to cite exactly the decisive evidence.','budget':'512 maximum declared completion tokens per method/case including retries. At most 4096 actual prompt+output tokens per case incl retries (audit, exceeding invalidates budget comparison). Go also requires aggregate tokens and median elapsed time <=1.5x direct. Matched ceilings, not identical actual compute.','rubric':'Blind reader records S (claim supported), C (contradicted), U (not established), A (no discernible decision); fact=1 only if material reasoning is correct and enough to distinguish the key confound, not just no false tokens. Grade stance by meaning not a magic word: no because evidence is missing is U, no because the opposite is documented is C. Cite letters explicitly attributed to the conclusion or a unique source/title or uniquely identifying directly attributed quote; no credit for merely displaying all retrieved excerpts or a UI bibliography. Extra irrelevant references lose exact-source credit. Blind reader transcribes the relied-on source letters, not the desired key; keys never enter generation. A valid joint answer needs correct stance, fact=1 and exactly the frozen decisive sources. A non-answer cannot earn joint or verdict credit; on answerable packets U/A is an unnecessary abstention. A wrong definite S/C stance counts as a confident mistake, not calibrated probability.','grading':'Mask method identity with unpredictable per-answer IDs. Reader independently records perceived S/C/U/A, material grounding and citations before mapping is read. Include 6 fixed positive, wrong, extra-citation and abstention calibration answers; require all stance/source control bits and >=5/6 rationale bits. One assistant reviewer also designed the key; style may reveal methods. No claim of independent human validation.','gate':'Decision on expanding this document workflow only. GO if at least 2 additional joint successes over direct, net improvement in >=2 of 4 families, no more confident mistakes or unnecessary abstentions, and within cost rule. Otherwise NO-GO: stop expanding debate; keep tested deterministic evidence tools and direct baseline. INCONCLUSIVE if lost case, wrong cache, runtime/arm exception, invalid budget or failed grading controls. No retuning or reuse as confirmation.','source_crosscheck':'Snapshot fields and hashes, not independent confirmation of the underlying company claims. Previously used sources, exact questions not in earlier local protocols/inputs/blind files. No return outcomes or private user messages used.'}

def dump(p,obj):
 with p.open('xb') as f:f.write(Q.encode(obj))
def read(p):return json.loads(p.read_bytes())
def sha(b):return hashlib.sha256(b).hexdigest()
def rid():return secrets.token_hex(6)
def protocol():
 files=[Path(__file__),Path(Q.__file__),Path(C.__file__),Path('core/documents.py'),Path('core/orchestrator.py')];return {'policy':POLICY,'files':{p.name:sha(p.read_bytes()) for p in files},'cache':sha(Q.CACHE.read_bytes()),'packets':sha(Q.encode(Q.build()))}
def freeze(root):
 root.mkdir(parents=True,exist_ok=False);pr=protocol();dump(root/'protocol.json',pr);rows=Q.build();dump(root/'questions.json',[c for c,_,_ in rows]);dump(root/'rubric-key.json',{c['case']:k for c,k,_ in rows});dump(root/'sources.json',{c['case']:r for c,_,r in rows});print('protocol',sha(Q.encode(pr)),flush=True)
def verify(root):
 if read(root/'protocol.json')!=protocol():raise ValueError('Changed protocol or source; use fresh folder')
 rows=Q.build();expected=[[c for c,k,r in rows],{c['case']:k for c,k,r in rows},{c['case']:r for c,k,r in rows}];
 if any(read(root/p)!=v for p,v in zip(('questions.json','rubric-key.json','sources.json'),expected)):raise ValueError('packet/key change')
def blindview(item):
 if set(item)!={'case','question','documents'}:raise ValueError('grading key cannot enter a model call')
 C.docs_view(item);return item

def controls(key):
 # Labels are separate and are not placed into the anonymous grading sheet.
 def refs(case):return key[case]['citations']
 def cite(letters):return ' ('+', '.join('Exhibit '+a for a in letters)+').'
 a=refs('milestones');b=refs('homeview');q=refs('surprise');p=refs('periods');w=next(x for x in 'ABC' if x not in q)
 return [('milestones','Yes. Pre-orders opened on October 16 before the worldwide Apple Store debut on October 23'+cite(a),['S',1,a]),('homeview','Yes, there was no notice of availability at all'+cite(b),['S',0,b]),('surprise','The results notice gives neither earnings nor a comparable analyst expectation, so a beat is not established'+cite(q),['U',1,q]),('surprise','The notice lacks an earnings figure or consensus; there is no basis to establish a surprise'+cite(q+[w]),['U',1,sorted(q+[w])]),('periods','I cannot decide from these notices.', ['U',0,[]]),('periods','There are three exhibits to read.', ['A',0,[]])]
def gen(root):
 verify(root);dump(root/'started.json',True);cases=read(root/'questions.json');rng=random.SystemRandom();rng.shuffle(cases);blind=[];mapping={};key=read(root/'rubric-key.json');seen=set()
 def add(c,text,meta):
  i=rid();assert i not in seen;seen.add(i);blind.append({'id':i,'case':c,'answer':text});mapping[i]=meta
 with C.T.narrator(C.MODEL,root/'runtime.log',alias='conclusion-screen',backend=C.T.default_backend(C.MODEL),model_id=C.L.DEFAULT_NARRATOR) as (server,meta):
  dump(root/'runtime.json',meta)
  for j,c in enumerate(cases):
   arms=list(ARMS);rng.shuffle(arms);item=blindview(c)
   for arm in arms:
    answer,m=C.run_one(item,arm,server.post);add(c['case'],answer,dict(kind='run',arm=arm,**m))
   print('complete packets',j+1,'/',len(cases),flush=True)
 for case,text,expected in controls(key):add(case,text,{'kind':'control','expected':expected})
 rng.shuffle(blind);dump(root/'blind.json',blind);dump(root/'mapping.json',mapping);print('sealed',len(blind),'anonymous answers',flush=True)
def valid(g):return isinstance(g,list) and len(g)==3 and g[0] in STANCES and type(g[1]) is int and g[1] in (0,1) and isinstance(g[2],list) and g[2]==sorted(set(g[2])) and all(x in ('A','B','C') for x in g[2])
def item_grade(g,key):
 if not valid(g):raise ValueError('Invalid reader rubric')
 s,f,refs=g;gold=K[key['verdict']];v=s==gold;return dict(stance=int(v),grounded=f,joint=int(v and f==1 and refs==sorted(key['citations'])),citations=int(refs==sorted(key['citations'])),wrong_definite=int(s in ('S','C') and not v),unnecessary=int(gold in ('S','C') and s in ('U','A')),no_decision=int(s=='A'))
def decision(a,b,delta,valid_run):
 if not valid_run:return 'INCONCLUSIVE'
 if a['joint']-b['joint']>=2 and sum(x>0 for x in delta.values())>=2 and a['wrong_definite']<=b['wrong_definite'] and a['unnecessary']<=b['unnecessary'] and a['total_tokens']<=1.5*b['total_tokens'] and a['median_s']<=1.5*b['median_s']:return 'GO'
 return 'NO-GO'
def score(root):
 verify(root);bl=read(root/'blind.json');m=read(root/'mapping.json');g=read(root/'grades.json');k=read(root/'rubric-key.json');assert len(bl)==len(m)==len(g)==30 and set(m)==set(g)=={x['id'] for x in bl} and all(valid(v) for v in g.values());rr=[];cg=[]
 for x in bl:
  i=x['id'];v=m[i];gg=g[i];case=x['case'];
  if v['kind']=='control':cg.append([s==e for s,e in zip(gg,v['expected'])]);continue
  calls=v['calls'];it=item_grade(gg,k[case]); rr.append(dict(case=case,family=k[case]['family'],arm=v['arm'],**it,input=sum(c['input'] for c in calls),output=sum(c['output'] for c in calls),cap=sum(c['cap'] for c in calls),ends=sum(c['finish']=='length' for c in calls),seconds=v['seconds'],fallback=bool(v['fallback']),error=bool(v['error'])))
 assert len(cg)==6 and {(x['case'],x['arm']) for x in rr}==set(itertools.product(k,ARMS)) and len(rr)==24
 cs=list(map(sum,zip(*cg)));controls_ok=cs[0]==6 and cs[1]>=5 and cs[2]==6; ok=controls_ok and all(not x['error'] and x['cap']<=512 and x['input']+x['output']<=4096 and x['output']<=x['cap'] for x in rr)
 arms={}
 for a in ARMS:
  rows=[x for x in rr if x['arm']==a];arms[a]={s:sum(x[s] for x in rows) for s in ['joint','stance','grounded','citations','wrong_definite','unnecessary','no_decision','input','output','ends','fallback','error']};arms[a].update(total_tokens=arms[a]['input']+arms[a]['output'],median_s=statistics.median(x['seconds'] for x in rows))
 lookup={(x['case'],x['arm']):x for x in rr};diff={c:lookup[c,'current']['joint']-lookup[c,'direct']['joint'] for c in k};fam={f:sum(diff[c] for c in k if k[c]['family']==f) for f in {v['family'] for v in k.values()}};cluster=sorted(fam);samples=[sum(fam[f] for f in comb)/12 for comb in itertools.product(cluster,repeat=len(cluster))];samples.sort();out=dict(n=12,controls=cs,control_pass=controls_ok,valid=ok,arms=arms,paired={'wins':sum(v>0 for v in diff.values()),'ties':sum(v==0 for v in diff.values()),'losses':sum(v<0 for v in diff.values()),'per_family':fam,'per_case':diff,'cluster_descriptive_95':[samples[int(.025*len(samples))],samples[int(.975*len(samples))]]},result=decision(arms['current'],arms['direct'],fam,ok),grade_sha=sha((root/'grades.json').read_bytes()),protocol_sha=sha((root/'protocol.json').read_bytes()));dump(root/'items.json',rr);dump(root/'report.json',out);print(json.dumps(out,indent=1))
if __name__=='__main__':
 sys.addaudithook(C.S.local_only);p=argparse.ArgumentParser();p.add_argument('cmd',choices=('freeze','run','score'));p.add_argument('--root',type=Path,required=True);a=p.parse_args();{'freeze':freeze,'run':gen,'score':score}[a.cmd](a.root)
