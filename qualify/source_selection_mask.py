"""Output-format sensitivity on reused cases. Not an independent retest; original one-look result stays sealed."""
import argparse,json,random,sys,time
from pathlib import Path
from qualify import source_selection as F
P=F.P;S=F.S;CAP=192
APPEND=' Encode your selections using the required boolean field for each span ID: true selects that span, false omits it. Do not omit fields.'
def unique(pairs):
 if len(pairs)!=len({k for k,v in pairs}):raise ValueError('Duplicate JSON fields are not a valid selection')
 return dict(pairs)
def decode(text,ids):
 d=json.loads(text,object_pairs_hook=unique);wanted=set(ids)
 if not isinstance(d,dict) or set(d)!=wanted or any(type(v) is not bool for v in d.values()):raise ValueError('Every real span needs exactly one boolean decision')
 return sorted(k for k,v in d.items() if v)
def schema(ids):return {'type':'object','additionalProperties':False,'required':ids,'properties':{k:{'type':'boolean'} for k in ids}}
def plan(parent):
 return {'previous_protocol':P.digest((parent/'protocol.json').read_bytes()),'cases_sha':P.digest((parent/'cases.json').read_bytes()),'anchor_outputs_sha':P.digest((parent/'outputs.json').read_bytes()),'current_files':F.fingerprint(),'format_code':P.digest(Path(__file__).read_bytes()),'source_only_prompt':F.SYSTEM+APPEND,'cap':CAP,'decision_rule':F.RULE,'reason':'Post hoc format sensitivity. Fix duplicate span IDs by giving each visible span a required boolean. Same archival material, authored tasks, fixed model and hints; original answers already seen. No tuning or independent validation claim.'}
def summary(sc):return {z:{a:F.agg([x for x in sc if x['arm']==a and (z=='all' or x['split']==z)]) for a in F.ARMS} for z in ('all','issuer','contrast')}
def run(root,parent):
 spec=F.read(root/'protocol.json');assert P.enc(spec)==P.enc(plan(parent));cs=F.read(parent/'cases.json');pout=F.read(parent/'outputs.json');control={(x['case'],x['arm']):x for x in pout if x['arm'] in ('rule','lexical')};P.write(root/'started.json',True);rng=random.Random(7901);rng.shuffle(cs);out=[];alias='selection-mask';mr=Path('data/local_models')
 with S.T.narrator(mr,root/'server.log',alias=alias,model_id=S.MODEL,backend=S.T.default_backend(mr)) as (local,meta):
  P.write(root/'runtime.json',meta)
  for c in cs:
   q=F.view(c);sp=F.C.index(q['documents']);ids=[x['id'] for x in sp];h=F.rule(q,sp);assert h==control[c['id'],'rule']['ids'];arms=list(F.ARMS);rng.shuffle(arms)
   for arm in arms:
    if arm in ('rule','lexical'):out.append(control[c['id'],arm]);continue
    error=None;end=None;it=ot=0;pred=None;raw=None;start=time.monotonic()
    try:
     b=S.T.request_body(alias,F.SYSTEM+APPEND,F.body(q,sp,h if arm=='hinted' else None),max_tokens=CAP,response_format={'type':'json_schema','json_schema':{'name':'span_switches','strict':True,'schema':schema(ids)}});z=local.post(b);ch=z['choices'][0];end=ch['finish_reason'];it=z['usage']['prompt_tokens'];ot=z['usage']['completion_tokens'];raw=ch['message']['content'];pred=decode(raw,ids)
    except Exception as e:error=type(e).__name__+': '+str(e)[:200]
    out.append(dict(case=c['id'],arm=arm,ids=pred,error=error,finish=end,input_tokens=it,output_tokens=ot,seconds=round(time.monotonic()-start,4),response=raw))
   print('completed mask packet',len(out)//4,'/',len(cs),flush=True)
 P.write(root/'outputs.json',out);lookup={x['id']:x for x in cs};sc=[]
 for x in out:
  c=lookup[x['case']];avail={v['id'] for v in F.C.index(F.view(c)['documents'])};bad=x['error'] is not None or x['finish']=='length' or x['output_tokens']>CAP
  sc.append({k:x[k] for k in ('case','arm','input_tokens','output_tokens','seconds')}|{'split':c['split'],'family':c['family']}|F.grade(x['ids'],avail,c['facets'],c['counter_facets'],bad))
 P.write(root/'scores.json',sc);s=summary(sc);report={'experimental':True,'same_cases_after_format_failure':True,'results':s,'decision':{a:F.gate(s,a) for a in ('rule','hinted')},'paired':{a:F.paired(sc,a) for a in ('rule','hinted','lexical')},'sha_protocol':P.digest(P.enc(spec))};P.write(root/'report.json',report);print('gate',report['decision'])
if __name__=='__main__':
 sys.addaudithook(F.local_only);p=argparse.ArgumentParser();p.add_argument('action',choices=['freeze','run']);p.add_argument('parent',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
 if a.action=='freeze':a.out.mkdir(exist_ok=False);pr=plan(a.parent);P.write(a.out/'protocol.json',pr);print('protocol',P.digest(P.enc(pr)))
 else:run(a.out,a.parent)
