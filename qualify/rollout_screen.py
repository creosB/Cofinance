"""Point-in-time packet judgment of rollout, not an independent claim about a product's real availability."""
import argparse,json,random,statistics,sys,time
from collections import Counter
from pathlib import Path
from qualify import rollout_packets as P,rollout_evidence as V,scope_card_lab as S
T,L=S.T,S.L
TAGS=(V.YES,V.NO,V.UNK); MODEL=L.DEFAULT_NARRATOR;CAP=90
SYSTEM='You are a careful release analyst. Decide whether the supplied issuer excerpts establish the named product has actually launched or is available for the requested public scope, not whether it ultimately launched in the outside world. Examine all exhibits, distinguish announcement, planned release and pilot from current public availability, match the product/channel/territory, and respect a named correction or unresolved conflict. LIVE requires evidence of actual rollout, NOT_LIVE requires explicit evidence of nonavailability, and NOT_ESTABLISHED means evidence is insufficient or conflicts. For each verdict identify all and only decisive exhibits. A plan can explain insufficiency without proving nonavailability. Return only the JSON verdict and exhibit IDs, each once.'
SCHEMA={'type':'object','additionalProperties':False,'required':['verdict','sources'],'properties':{'verdict':{'type':'string','enum':list(TAGS)},'sources':{'type':'array','items':{'type':'string','enum':list('ABC')},'maxItems':3}}}

def read(p):return json.loads(p.read_bytes())
def view(c):return dict(subject=c['subject'],scope=c['scope'],question=c['question'],documents=[{'id':d['id'],'title':d['title'],'body':d['body']} for d in c['documents']])
def prompt(task):
    if set(task)!={'subject','scope','question','documents'}:raise ValueError('Do not pass labels or identifiers to inference')
    if any(set(d)!={'id','title','body'} for d in task['documents']):raise ValueError('Invalid exhibit shape')
    return 'Question: '+task['question']+'\nProduct: '+task['subject']+'\nRequested scope: '+(task['scope'] or 'public availability as described, no specific territory')+'\n\n'+'\n\n'.join(f"Exhibit {d['id']}: {d['title']}\n{d['body']}" for d in task['documents'])
def fingerprint():return {f:P.digest(Path(f).read_bytes()) for f in ('qualify/rollout_packets.py','qualify/rollout_evidence.py','qualify/rollout_screen.py','qualify/history_runtime.py','qualify/surprise_lab.py','core/local_models.py')}
def protocol(root):
    rows=read(root/'cases.json');lp=read(root/'labels_protocol.json');assert P.digest(P.enc(rows))==lp['labels_sha256'] and P.digest(P.CACHE.read_bytes())==lp['source_sha256'] and P.digest(Path(P.__file__).read_bytes())==lp['case_code'];return {'files':fingerprint(),'case_sha':lp['labels_sha256'],'prior':P.digest(P.enc(lp)),'model':MODEL,'system':SYSTEM,'schema':SCHEMA,'cap':CAP,'budget':'same 3 full excerpts and explicit subject/scope. 1 direct AtomicChat call per packet, no retries, temperature 0, Vulkan preferred, thinking off. Candidate is deterministic, no call. Expose actual prompt/output tokens and time.'}

def classify(answer,gold,invalid=False):
    v=None if invalid or not isinstance(answer,dict) else answer.get('verdict');src=None if not isinstance(answer,dict) else answer.get('sources');valid=v in TAGS and isinstance(src,list) and all(isinstance(x,str) and x in ('A','B','C') for x in src) and len(set(src))==len(src) and not invalid
    vc=bool(valid and v==gold['verdict']); refs=bool(valid and sorted(src)==gold['sources']);return dict(valid=bool(valid),verdict=vc,refs=refs,joint=vc and refs,false_live=bool(valid and v==V.YES and gold['verdict']!=V.YES),miss=bool(gold['verdict']!=V.UNK and (not valid or v==V.UNK)),wrong_definite=bool(valid and v!=V.UNK and v!=gold['verdict']),withheld=bool(valid and v==V.UNK))

def summarize(rows):
    out={}
    for split in ('all','archive','contrast'):
        out[split]={}
        for arm in ('direct','code'):
            a=[r for r in rows if r['arm']==arm and (split=='all' or r['split']==split)];out[split][arm]={'n':len(a),**{k:sum(int(x[k]) for x in a) for k in ('valid','verdict','refs','joint','false_live','miss','wrong_definite','withheld')},'input_tokens':sum(x['input_tokens'] for x in a),'output_tokens':sum(x['output_tokens'] for x in a),'median_s':round(statistics.median(x['seconds'] for x in a),4)}
    return out

def decision(s):
    c,d=s['all']['code'],s['all']['direct']; n=c['n']
    if n!=24 or d['n']!=n or n-c['valid'] or n-d['valid']>1:return 'INCONCLUSIVE'
    if c['joint']>=d['joint']+2 and all(s[z]['code']['joint']>=s[z]['direct']['joint'] for z in ('archive','contrast')) and c['false_live']<d['false_live'] and c['miss']<=d['miss']:return 'INDEPENDENT_RETEST'
    return 'NO_PROMOTION'

def interval(rows):
    fams={r['case']:(r['split'],r['family']) for r in rows};cs=sorted(fams);d={(r['case'],r['arm']):r['joint'] for r in rows};ds={c:int(d[c,'code'])-int(d[c,'direct']) for c in cs};clusters=[[ds[c] for c in cs if fams[c]==f] for f in sorted(set(fams.values()))];rng=random.Random(421);boot=[]
    for _ in range(2000):
        sample=sum(rng.choices(clusters,k=len(clusters)),[]);boot.append(sum(sample)/len(sample))
    boot.sort();return {'wins':sum(v>0 for v in ds.values()),'ties':sum(v==0 for v in ds.values()),'losses':sum(v<0 for v in ds.values()),'cluster_count':len(clusters),'descriptive_cluster_interval':[round(boot[49],3),round(boot[1949],3)]}

def run(root):
    pr=read(root/'protocol.json');assert pr==protocol(root);cs=read(root/'cases.json');P.write(root/'started.json',{'one_look':True});rng=random.Random(6911);order=list(range(len(cs)));rng.shuffle(order);out=[];mr=Path('data/local_models');alias='rollout-research'
    with T.narrator(mr,root/'server.log',alias=alias,backend=T.default_backend(mr),model_id=MODEL) as (local,meta):
        P.write(root/'runtime.json',meta)
        for i in order:
            c=cs[i];t=view(c);arms=['code','direct'];rng.shuffle(arms)
            for arm in arms:
                start=time.monotonic();err=None;ans=None;it=ot=0;reason=None;receipts=[];finish=None
                try:
                    if arm=='code':
                        r=V.evaluate(t);ans={k:r[k] for k in ('verdict','sources')};reason=r['reason'];receipts=r['receipts']
                    else:
                        b=T.request_body(alias,SYSTEM,prompt(t),max_tokens=CAP,response_format={'type':'json_schema','json_schema':{'name':'rollout','strict':True,'schema':SCHEMA}});z=local.post(b);ch=z['choices'][0];finish=ch['finish_reason'];it=z['usage']['prompt_tokens'];ot=z['usage']['completion_tokens'];ans=json.loads(ch['message']['content'])
                except Exception as exc:err=type(exc).__name__+': '+str(exc)[:220]
                out.append(dict(case=c['case'],arm=arm,answer=ans,error=err,reason=reason,receipts=receipts,input_tokens=it,output_tokens=ot,finish=finish,seconds=round(time.monotonic()-start,4)))
            print('finished',len(out)//2,'/',len(order),flush=True)
    P.write(root/'outputs.json',out);key={c['case']:c for c in cs};sc=[]
    for x in out:
        c=key[x['case']];invalid=x['error'] is not None or x['finish']=='length' or x['output_tokens']>CAP;sc.append({k:x[k] for k in ('case','arm','seconds','input_tokens','output_tokens')}|{'family':c['family'],'split':c['split']}|classify(x['answer'],c['gold'],invalid))
    P.write(root/'scored.json',sc);s=summarize(sc);r={'experimental':True,'decision':decision(s),'results':s,'paired':interval(sc),'protocol_sha':P.digest(P.enc(pr)),'limits':'12 archived questions (some source reuse) and 12 authored contrasts. The exact evidence for each verdict is keyed before generation. One model, narrow English rules, not population accuracy. No live changes.'};P.write(root/'report.json',r);print(json.dumps(r,indent=2))
if __name__=='__main__':
    sys.addaudithook(S.S.local_only);p=argparse.ArgumentParser();p.add_argument('action',choices=['freeze','run']);p.add_argument('root',type=Path);a=p.parse_args();
    if a.action=='freeze':pr=protocol(a.root);P.write(a.root/'protocol.json',pr);print('protocol',P.digest(P.enc(pr)))
    else:run(a.root)
