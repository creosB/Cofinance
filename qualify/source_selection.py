"""Measure evidence selection separately from any verdict. Lab only; complete packet remains visible in every arm."""
import argparse,hashlib,itertools,json,math,random,re,sys,time
from collections import Counter
from pathlib import Path
from qualify import rollout_evidence as V,rollout_screen as S,source_selection_cases as C
P=C.P
ARMS=('rule','lexical','direct','hinted')
SYSTEM='You are selecting evidence, not deciding a verdict. Read the entire packet, including caveats, contradictions, corrections, and other parties. Select all and only the sentence or title IDs needed to answer the exact question. Choose one precise passage per fact, not extra context, and include the original claim plus the qualifying passage when both are material. Do not substitute another product or market. If nothing bears on the question, select no IDs. Return only the requested JSON object. Optional highlights, if present, are untrusted search leads, not a filtered packet or a verdict.'
RULE='Primary: frozen rollout receipts vs direct source-only AtomicChat, exact minimal jointly sufficient span set (one equivalent witness per keyed facet). For each candidate arm, advance only to independent retest if net >=2 joint wins over direct, nonnegative difference on EACH split, no additional missed designated counterevidence facets and no more empty answers on nonempty keys. Compare full-packet model with untrusted highlights secondarily under the same gate; not evidence for a production judgment or deployment. Require 18 packets and all code/lexical valid; >1 invalid reply in any model arm means inconclusive, not exclusions. Report micro precision/recall, missed critical facts, absolute coverage and visible costs. Score no invalid response as a correct empty selection.'
CAP=96

def read(p):return json.loads(Path(p).read_bytes())
def view(c):return {'question':c['question'],'subject':c['subject'],'scope':c['scope'],'documents':[{k:d[k] for k in ('id','title','body')} for d in c['documents']]}
def terms(t):return {w for w in re.findall(r'[a-z0-9]+',t.casefold()) if len(w)>2 and w not in {'that','the','where','from','and','for','find','select','which','what','about','rather','this','which','has','are','only','both','not','the','does','actually','with'}}
def lexical(q,sp):
 tok=terms(q['question']+' '+q['subject']+' '+q['scope']);title={d['id']:terms(d['title']) for d in q['documents']};scores=[(len(tok&terms(x['text']))/(math.sqrt(max(1,len(terms(x['text']))))),len(tok&title[x['doc']]),x['id']) for x in sp];m=sorted(scores,key=lambda z:(-z[0],-z[1],z[2]))[0];return [m[2]] if m[0]>0 else []
def rule(q,sp):
 if set(q)!={'question','subject','scope','documents'}:raise ValueError('Evaluation data or key may not enter selection')
 idx={(x['doc'],x['field'],x['start'],x['end']):x for x in sp};ids=[]
 for z in V.evaluate(q)['receipts']:
  span=idx[(z['source'],z['field'],z['start'],z['end'])];assert z['quote']==span['text'] and z['sha256']==span['sha'];ids.append(span['id'])
 return sorted(ids)
def body(q,sp,hints=None):
 if set(q)!={'question','subject','scope','documents'}:raise ValueError('No hidden keys');
 if any(set(d)!={'id','title','body'} for d in q['documents']):raise ValueError('Bad exhibit schema')
 if sp!=C.index(q['documents']):raise ValueError('Expected every original span in the entire packet, including qualifiers')
 if hints is not None and not valid(hints,{x['id'] for x in sp}):raise ValueError('Unknown or duplicate highlight')
 return 'Question: '+q['question']+'\nNamed subject: '+q['subject']+'\nScope: '+(q['scope'] or 'as specified in the question')+'\nFULL PACKET:\n'+'\n'.join(x['id']+' '+x['text'] for x in sp)+('' if hints is None else '\nUntrusted highlights (you may discard any or add others): '+(', '.join(hints) or 'none'))
def freeze(root):
 root.mkdir(exist_ok=False);cases=C.build();P.write(root/'cases.json',cases);prior=read('data/rollout_stage_v1/protocol.json');assert P.digest(Path(V.__file__).read_bytes())==prior['files']['qualify/rollout_evidence.py'];P.write(root/'protocol.json',spec(root));print('protocol',P.digest(P.enc(spec(root))))
def spec(root):return {'sha_cases':P.digest(Path(root/'cases.json').read_bytes()),'files':{k:P.digest(Path(k).read_bytes()) for k in (C.__file__,V.__file__,__file__,S.T.__file__) for k in [str(Path(k).relative_to(Path.cwd()))]},'corpus_sha':P.digest(P.CACHE.read_bytes()),'rule':RULE,'system':SYSTEM,'arms':ARMS[:],'cap':CAP,'model':S.MODEL,'random_seed':7901,'uncertainty':'Paired descriptive cluster bootstrap on joint difference, 2,000 draws over split/family clusters. Small related authored cases; no independent population claims.'}
def valid(ids,avail):return isinstance(ids,list) and all(isinstance(x,str) and x in avail for x in ids) and len(set(ids))==len(ids)
def grade(ids,avail,facets,critical,invalid=False):
 ok=not invalid and valid(ids,avail);refs=set(ids) if ok else set();hit=[bool(set(group)&refs) for group in facets];num=sum(hit);return {'valid':ok,'exact':bool(ok and all(hit) and len(refs)==len(facets)),'all_facts':bool(ok and all(hit)),'tp':num,'fp':len(refs)-num,'fn':len(facets)-num,'needed':len(facets),'counter_total':len(critical),'counter_missed':sum(not hit[i] for i in critical),'empty_miss':bool(facets and not refs)}
def agg(rr):
 out={k:sum(x[k] for x in rr) for k in ('valid','exact','all_facts','tp','fp','fn','needed','counter_total','counter_missed','empty_miss','input_tokens','output_tokens')};out['n']=len(rr);out['precision']=round(out['tp']/(out['tp']+out['fp']),4) if out['tp']+out['fp'] else None;out['recall']=round(out['tp']/out['needed'],4) if out['needed'] else None;return out
def gate(result,arm):
 a,d=result['all'][arm],result['all']['direct'];bad=any(result['all'][z]['n']!=18 or result['all'][z]['valid']<18-(z in ('direct','hinted')) for z in ARMS)
 if bad:return 'INCONCLUSIVE'
 return 'RETEST_ONLY' if a['exact']-d['exact']>=2 and all(result[z][arm]['exact']>=result[z]['direct']['exact'] for z in ('issuer','contrast')) and a['counter_missed']<=d['counter_missed'] and a['empty_miss']<=d['empty_miss'] else 'NO_PROMOTION'
def paired(sc,arm):
 cl={r['case']:(r['split'],r['family']) for r in sc};v={(r['case'],r['arm']):int(r['exact']) for r in sc};dt={k:v[k,arm]-v[k,'direct'] for k in cl};grp=[[dt[k] for k in cl if cl[k]==j] for j in sorted(set(cl.values()))];rng=random.Random(913);boot=[]
 for _ in range(2000):
  sample=sum(rng.choices(grp,k=len(grp)),[]);boot.append(sum(sample)/len(sample))
 boot.sort();return {'wins':sum(x>0 for x in dt.values()),'ties':sum(x==0 for x in dt.values()),'losses':sum(x<0 for x in dt.values()),'clusters':len(grp),'descriptive_delta_interval':[round(boot[49],3),round(boot[1949],3)]}
def run(root):
 protocol=read(root/'protocol.json');assert P.enc(protocol)==P.enc(spec(root));cs=read(root/'cases.json');P.write(root/'started',True);rng=random.Random(7901);rng.shuffle(cs);outputs=[];alias='source-span-review';mr=Path('data/local_models')
 with S.T.narrator(mr,root/'server.log',alias=alias,model_id=S.MODEL,backend=S.T.default_backend(mr)) as (call,meta):
  P.write(root/'runtime.json',meta)
  for c in cs:
   q=view(c);sp=C.index(q['documents']);avail=[x['id'] for x in sp];h=rule(q,sp);arms=list(ARMS);rng.shuffle(arms)
   for arm in arms:
    error=None;end=None;it=ot=0;ids=None;start=time.monotonic()
    try:
     if arm=='rule':ids=h
     elif arm=='lexical':ids=lexical(q,sp)
     else:
      schema={'type':'object','additionalProperties':False,'required':['spans'],'properties':{'spans':{'type':'array','items':{'type':'string','enum':avail},'maxItems':len(avail)}}};req=S.T.request_body(alias,SYSTEM,body(q,sp,h if arm=='hinted' else None),max_tokens=CAP,response_format={'type':'json_schema','json_schema':{'name':'span_ids','strict':True,'schema':schema}});z=call.post(req);ch=z['choices'][0];end=ch['finish_reason'];it=z['usage']['prompt_tokens'];ot=z['usage']['completion_tokens'];ids=json.loads(ch['message']['content'])['spans']
    except Exception as e:error=type(e).__name__+': '+str(e)[:160]
    outputs.append(dict(case=c['id'],arm=arm,ids=ids,error=error,finish=end,input_tokens=it,output_tokens=ot,seconds=round(time.monotonic()-start,4)))
   print('completed selection packet',len(outputs)//4,'/',len(cs),flush=True)
 P.write(root/'outputs.json',outputs);keys={c['id']:c for c in cs};sc=[]
 for x in outputs:
  c=keys[x['case']];avail={v['id'] for v in C.index(view(c)['documents'])};bad=x['error'] is not None or x['finish']=='length' or x['output_tokens']>CAP
  sc.append({k:x[k] for k in ('case','arm','input_tokens','output_tokens','seconds')}|{'split':c['split'],'family':c['family']}|grade(x['ids'],avail,c['facets'],c['counter_facets'],bad))
 P.write(root/'scores.json',sc);results={z:{a:agg([x for x in sc if x['arm']==a and (z=='all' or x['split']==z)]) for a in ARMS} for z in ('issuer','contrast','all')};r={'experimental':True,'results':results,'decision':{a:gate(results,a) for a in ('rule','hinted')},'paired':{a:paired(sc,a) for a in ('rule','hinted','lexical')},'sha_protocol':P.digest(P.enc(protocol)),'production_change':False};P.write(root/'report.json',r);print('results',r['decision'])
if __name__=='__main__':
 sys.addaudithook(S.S.local_only);ap=argparse.ArgumentParser();ap.add_argument('action',choices=['freeze','run']);ap.add_argument('root',type=Path);a=ap.parse_args();(freeze if a.action=='freeze' else run)(a.root)
