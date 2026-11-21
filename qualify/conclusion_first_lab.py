"""Experimental fixed document-route comparison; synthetic case keys never enter generation."""
import argparse,hashlib,json,random,secrets,statistics,sys,time
from pathlib import Path
from core import orchestrator as O, local_models as L
from qualify import conclusion_packets as P,history_runtime as T,surprise_lab as S
ARMS=('direct','current','first','critic'); MODEL=Path('data/local_models'); BASE='lfm3-1.7b-q4km'; DIR='Answer the question from the supplied evidence only, in at most three short sentences. Address the strongest relevant limitation or contrary evidence, distinguish plans from observed facts, and say when the answer is not established.'
FIRST='For document questions, answer the question in the first sentence: state the supported conclusion, or exactly what cannot be established. Then explain why using the decisive evidence and the strongest conflicting evidence, naming the document when useful. Do not attribute document facts to other specialists. Do not discuss investment risk unless asked. A page quotation alone is not a conclusion.'
CRIT='Identify the strongest counterevidence or missing comparison relevant to the question. Cite the exact relevant words from the packet, or say none. Do not add facts. At most two short sentences.'
RUBRIC='Score three independent binary items: supported=all material factual assertions and attributions in the answer follow from the packet, with at least one substantive supported fact; counter=the answer explicitly addresses the key opposing or limiting evidence specified in the key and why it matters (a bare list is not enough); conclusion=explicitly communicates the correct warranted answer, including uncertainty when appropriate, using any phrasing or sentence order. An incorrect or unresolved final verdict gets no conclusion point. Do not require the word yes/no or a named page. Do not award points for agreeing with the key if the candidate only copies unrelated material. An empty/failed answer scores zero.'
CONTROLS=[('vote','No. The draft described approval, but the governing rule gives priority to the signed final minutes, which record deferral.',[1,1,1]),('vote','Yes. A court certified unanimous approval.',[0,0,0]),('vote','The draft minutes described approval.',[1,0,0]),('vote','No. A court annulled the vote.',[0,0,1]),('staff','It is not established for both sites. North cleared the on-duty threshold, but South lists assignments, not a confirmed on-duty count.',[1,1,1]),('staff','Yes. Both site managers independently verified attendance.',[0,0,0]),('staff','The signed North record confirms the North on-duty count.',[1,0,0]),('staff','It cannot be established for both sites. A judge sealed the South records.',[0,0,1])]
POLICY='No promotion. Next-stage candidate only if, under both graders separately, first improves explicit conclusions over current without reducing supported-claims totals, and is not worse than direct on total points. Local second grader eligible only if >=20/24 control bits and >=6/8 in each facet. Report per-facet intergrader agreement, coverage and disagreements; do not silently average when reliability fails. Report paired resampling over four case families for descriptive uncertainty, no broad advantage claim. Fixed-route, synthetic test, not retrieval quality. Comparing output ceilings is not compute matching.'
enc=lambda v:json.dumps(v,sort_keys=True,ensure_ascii=True,indent=2).encode(); sha=lambda b:hashlib.sha256(b).hexdigest(); read=lambda p:json.loads(p.read_bytes()); save=lambda p,x:p.open('xb').write(enc(x)); rnd=random.SystemRandom()
def protocol():
 paths=[Path(__file__),Path(P.__file__),Path(O.__file__),Path(T.__file__),Path('core/documents.py'),Path('core/numeric_guard.py')];return dict(version=1,code={p.name:sha(p.read_bytes()) for p in paths},cases=P.cases(),models=[L.DEFAULT_NARRATOR,BASE],direct=DIR,critic=CRIT,first=FIRST,rubric=RUBRIC,controls=CONTROLS,policy=POLICY,arms=ARMS,cap=512)
def verify(root):
 if enc(read(root/'protocol.json'))!=enc(protocol()):raise ValueError('Sealed rules or code changed')
def docs_view(c):
 if set(c)!={'case','question','documents'}:raise ValueError('Only the blind packet enters generation')
 rep=O.DocumentReaderAgent().run(c['question'],c['documents'],O.EventBus(),'isolated');shown=[e['text'] for e in rep.raw_facts['excerpts']];expected=[p for d in c['documents'] for p in d['pages']];
 if sorted(shown)!=sorted(expected):raise ValueError('Packet evidence is incomplete for '+c['case'])
 return rep.summary
class Route:
 def route(self,q):return O.RoutedQuery('document_question',1.,'unknown',[])
def run_one(c,arm,post):
 packet=docs_view(c); q=c['question']; calls=[];error=None;fallback=None;secs=time.monotonic();alias='conclusion-screen'
 def gen(s,p,limit):
  if limit+sum(k['cap'] for k in calls)>512:raise ValueError('Declared token budget exceeded')
  d=post(T.request_body(alias,s,p,max_tokens=limit));u=d['usage'];ch=d['choices'][0];calls.append(dict(cap=limit,input=u['prompt_tokens'],output=u['completion_tokens'],finish=ch['finish_reason']));return ch['message']['content']
 try:
  if arm=='direct':answer=gen(DIR,'Question: '+q+'\n'+packet,512)
  elif arm in ARMS:
   note=gen(CRIT,'Question: '+q+'\n'+packet,64) if arm=='critic' else None
   class Lead:
    def complete(self,system,prompt,max_tokens):
     if arm=='first':prompt+='\n\n'+FIRST
     if note is not None:prompt+='\nReviewer note (not evidence; check against excerpts): '+note
     return gen(system,prompt,min(max_tokens,224 if arm=='critic' else 256))
   out=O.TeamLeadOrchestrator(Route(),None,None,Lead(),None).run(q,documents=c['documents']);answer=out.final_synthesis;fallback=not out.faithful_synthesis
  else:raise ValueError('Unknown arm')
 except Exception as e:answer='';error=type(e).__name__+': '+str(e)
 return answer,dict(calls=calls,error=error,fallback=fallback,seconds=round(time.monotonic()-secs,3))
def make_id():return secrets.token_hex(6)
def generate(root):
 verify(root);save(root/'generation.started',True);plan=[];blind=[];key={};allcases=P.cases();rnd.shuffle(allcases)
 with T.narrator(MODEL,root/'generation.log',alias='conclusion-screen',backend=T.default_backend(MODEL),model_id=L.DEFAULT_NARRATOR) as (call,meta):
  save(root/'runtime.json',meta)
  for c in allcases:
   arms=list(ARMS);rnd.shuffle(arms)
   for a in arms:
    answer,metrics=run_one(P.generation_view(c),a,call.post);i=make_id();assert i not in key;blind.append(dict(id=i,case=c['case'],answer=answer));key[i]=dict(arm=a,**metrics)
   print('packets completed',len(blind)//len(ARMS),'of',len(allcases),flush=True)
 rnd.shuffle(blind);save(root/'blind.json',blind);save(root/'mapping.json',key)
SCHEMA={'type':'object','properties':{k:{'type':'integer','enum':[0,1]} for k in ('supported','counter','conclusion')},'required':['supported','counter','conclusion'],'additionalProperties':False}
def items_valid(v):return isinstance(v,list) and len(v)==3 and all(type(x) is int and x in (0,1) for x in v)
def judge(root):
 verify(root);rows=read(root/'blind.json');save(root/'judging.started',True);controls={};grades={};errors={};by={r['case']:r for r in P.cases()}
 for case,ans,gold in CONTROLS:
  i=make_id();controls[i]=gold;rows.append(dict(id=i,case=case,answer=ans))
 rnd.shuffle(rows)
 with T.narrator(MODEL,root/'judge.log',alias='separate-grader',backend=T.default_backend(MODEL),model_id=BASE) as (call,meta):
  save(root/'grader-runtime.json',meta)
  for r in rows:
   c=by[r['case']]; prompt=json.dumps(dict(question=c['question'],packet=docs_view(P.generation_view(c)),expected=c['gold'],limiting_fact=c['counter'],candidate=r['answer']),ensure_ascii=True)
   try:
    raw=call('You are a blinded grader. '+RUBRIC+' Return only the three integer fields.',prompt,max_tokens=80,response_format={'type':'json_schema','json_schema':{'name':'grades','strict':True,'schema':SCHEMA}});v=json.loads(raw);g=[v[k] for k in ('supported','counter','conclusion')];assert items_valid(g)
   except Exception as e:g=None;errors[r['id']]=str(e)
   grades[r['id']]=g
 save(root/'local-grades.json',dict(grades=grades,controls=controls,errors=errors))
def control_score(grades,controls):
 cs=[sum(items_valid(grades.get(k)) and grades[k][i]==v[i] for k,v in controls.items()) for i in range(3)];return {'by_facet':cs,'total':sum(cs),'passes':len(controls)==8 and min(cs)>=6 and sum(cs)>=20}
def summary(root):
 verify(root);a=read(root/'reader-grades.json');j=read(root/'local-grades.json');m=read(root/'mapping.json');bl=read(root/'blind.json');ids={r['id'] for r in bl}; byid={r['id']:r['case'] for r in bl};assert ids==set(a)==set(m) and len(ids)==len(ARMS)*len(P.ROWS) and all(items_valid(a[k]) for k in ids);assert set(j['grades'])==ids|set(j['controls']);cg=control_score(j['grades'],j['controls']);g={k:j['grades'][k] for k in ids};comp={};cost={}; intervals={};fam={r['case']:r['family'] for r in P.cases()};agreement=[sum(items_valid(g[k]) and a[k][i]==g[k][i] for k in ids) for i in range(3)]
 for name,grades in [('blind_reader',a),('local_base',g)]:
  pts={k:(grades[k] if items_valid(grades[k]) and not m[k]['error'] and next(r for r in bl if r['id']==k)['answer'].strip() else [0,0,0]) for k in ids};comp[name]={arm:[sum(pts[k][i] for k in ids if m[k]['arm']==arm) for i in range(3)] for arm in ARMS}; fams=sorted(set(fam.values()));keys={(byid[k],m[k]['arm']):k for k in ids}; rng=random.Random(5812);intervals[name]={}
  for ref in ('current','direct','critic'):
   diff={f:sum(sum(pts[keys[c,'first']])-sum(pts[keys[c,ref]]) for c in fam if fam[c]==f) for f in fams}; amounts=sorted(sum(diff[f] for f in rng.choices(fams,k=len(fams))) /len(fam) for _ in range(2000));intervals[name][ref]=[amounts[49],amounts[1949]]
 for arm in ARMS:
  vv=[m[k] for k in ids if m[k]['arm']==arm];cost[arm]=dict(prompt=sum(c['input'] for v in vv for c in v['calls']),output=sum(c['output'] for v in vv for c in v['calls']),seconds=round(statistics.median(v['seconds'] for v in vv),3),fallback=sum(bool(v['fallback']) for v in vv),errors=sum(bool(v['error']) for v in vv),truncated=sum(c['finish']=='length' for v in vv for c in v['calls']))
 def wins(x):return x['first'][2]>x['current'][2] and x['first'][0]>=x['current'][0] and sum(x['first'])>=sum(x['direct'])
 out=dict(n=len(fam),answer_count=len(ids),scores=comp,interval_delta_points_per_case=intervals,grader_control=cg,grader_parse_errors=len(j['errors']),agreement=agreement,cost=cost,advance=cg['passes'] and all(wins(x) for x in comp.values()) and not j['errors'] and not any(v['errors'] for v in cost.values()),production_change=False);save(root/'report.json',out);return out

def main():
 sys.addaudithook(S.local_only);p=argparse.ArgumentParser();p.add_argument('mode',choices=('freeze','run','judge','score'));p.add_argument('--root',type=Path,required=True);ns=p.parse_args();r=ns.root.resolve();
 if not r.is_relative_to(Path('data').resolve()) or r==Path('data').resolve():raise ValueError('Use a private data subfolder')
 if ns.mode=='freeze':
  for c in P.cases():docs_view(P.generation_view(c))
  r.mkdir();save(r/'protocol.json',protocol());print('protocol',sha((r/'protocol.json').read_bytes()))
 elif ns.mode=='run':generate(r)
 elif ns.mode=='judge':judge(r)
 else:print(json.dumps(summary(r),indent=2))
if __name__=='__main__':main()
