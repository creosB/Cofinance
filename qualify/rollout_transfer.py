"""Blind-before-prediction wording transfer. Separately worded is not independent human field validation."""
import argparse,json,random,sys,time
from pathlib import Path
from qualify import rollout_screen as S,rollout_packets as P,rollout_evidence as V
NAMES=['Tern Studio','Maple Echo','Larch Lens','Ivy Signal','Otter Suite','Plover Desk','Ash Reader','Orca Kit','Beech Works','Aster Flow','Pine Room','Quartz View'];REGIONS=['Belgium','Japan','Peru','Spain','Austria','Kenya','Canada','Italy','Greece','Chile','Norway','France'];FAMS=['catalog','desktop app','online service'];AUTHOR='lfm3-1.7b-q4km'
AUTHOR_PROMPT='Write the text of three fictional, short, separately sourced issuer bulletins. Write idiomatically as a newsroom editor; do not quote these instructions, reference exhibit IDs, label statuses, or explain a solution. Use the named product in every bulletin. Each bulletin is one or two complete sentences, no more than 30 words. Do not contradict the assigned facts, invent dates, or claim availability in unspecified markets. Return a JSON object with only the requested three bulletin texts.'
TASK={'LIVE':'The first bulletin reports that ordinary customers in the named market already have access to this product. It must describe actual present access, not a promise.', 'NOT_LIVE':'The first bulletin explicitly reports that ordinary customers in the named market cannot yet access this product. It must establish nonavailability, not infer it from silence.', 'NOT_ESTABLISHED':'The first bulletin describes interest, an advance announcement or a plan in the named market, without stating whether ordinary customers there already have access.'};AS={'type':'object','additionalProperties':False,'required':['first','second','third'],'properties':{k:{'type':'string'} for k in ('first','second','third')}}
def read(p):return json.loads(p.read_bytes())
def seeds():
 tags=[V.YES,V.NO,V.UNK,V.NO,V.UNK,V.YES,V.UNK,V.YES,V.NO,V.YES,V.NO,V.UNK];return [dict(id=f'r{i:02d}',subject=n,region=g,tag=t,category=FAMS[i%3]) for i,(n,g,t) in enumerate(zip(NAMES,REGIONS,tags))]
def spec():return {'source':P.digest(Path(__file__).read_bytes()),'frozen_verifier':P.digest(Path(V.__file__).read_bytes()),'first_protocol':S.read(Path('data/rollout_stage_v1/protocol.json')),'seeds':seeds(),'author':AUTHOR,'author_prompt':AUTHOR_PROMPT,'author_targets':TASK,'author_schema':AS,'rule':'All 12 generation attempts retained; no rewrites or retries. Masked semantic review chooses the warranted label and decisive IDs BEFORE rules or direct Q&A see the packets. If ambiguous, use explicit EXCLUDE with a reason. Report every exclusion and both nominal and accepted denominators. At least 9 usable and 2 of each stance required. Advance toward independently labeled field cases only if code clearly beats direct on joint by at least 2, false LIVE not worse, answerable misses not worse, and joint rate >= 2/3. Otherwise no integration; an inadequate sample or >1 invalid direct replies makes the result inconclusive. New author wording, same local model family; not field validation.'}
def keys_ok(keys,pack):
 if set(keys)!={r['id'] for r in pack}:raise ValueError('Every generated packet needs an explicit review')
 for k,v in keys.items():
  if set(v)!={'verdict','sources','note'} or (v['verdict'] not in S.TAGS and v['verdict']!='EXCLUDE') or not isinstance(v['note'],str) or not v['note'].strip():raise ValueError(k)
  if not isinstance(v['sources'],list) or sorted(set(v['sources']))!=v['sources'] or not set(v['sources'])<=set('ABC') or (v['verdict']!='EXCLUDE' and not v['sources']):raise ValueError(k)
def author(root):
 P.write(root/'author.started',True);out=[];mr=Path('data/local_models');alias='notice-author'
 with S.T.narrator(mr,root/'author.log',alias=alias,backend=S.T.default_backend(mr),model_id=AUTHOR) as (local,meta):
  P.write(root/'author-runtime.json',meta)
  for i,z in enumerate(seeds()):
   sub,g=z['subject'],z['region'];p=f'Product: {sub}. Target: ordinary customers in {g}. Category: {z["category"]}. '+TASK[z['tag']]+' The second bulletin reports an earlier prototype presentation for the same product without a public access claim. The third is about a technical update to the product documentation, not its availability. Do not refer to any other bulletin.';err=None;docs=[];ids=list('ABC');random.Random(658+i).shuffle(ids)
   try:
    b=S.T.request_body(alias,AUTHOR_PROMPT,p,max_tokens=260,response_format={'type':'json_schema','json_schema':{'name':'notices','strict':True,'schema':AS}});rr=local.post(b);ch=rr['choices'][0];texts=json.loads(ch['message']['content']);assert ch['finish_reason']!='length' and set(texts)==set(('first','second','third'));docs=sorted([{'id':ids[j],'title':'Issuer notice','body':texts[k]} for j,k in enumerate(('first','second','third'))],key=lambda d:d['id']);assert all(isinstance(d['body'],str) and 8<len(d['body'])<=900 for d in docs)
   except Exception as e:err=type(e).__name__+': '+str(e)[:200];docs=[]
   out.append({'id':z['id'],'subject':sub,'scope':g,'question':f'Can a release tracker record {sub} as publicly available to ordinary customers in {g} on this evidence?','documents':docs,'author_error':err});print('authored',i+1,'/ 12',flush=True)
 P.write(root/'packets.json',out)
def reader_input(root):
 p=read(root/'packets.json');k=read(root/'reader-input.json');keys_ok(k,p);P.write(root/'key.json',k);P.write(root/'key-receipt.json',{'packets':P.digest(P.enc(p)),'key':P.digest(P.enc(k)),'protocol':P.digest(P.enc(spec()))})
def decision(keys,stats):
 n=stats['code']['n'];ca,da=stats['code'],stats['direct'];eligible=n>=9 and n==da['n'] and min(sum(v['verdict']==tag for v in keys.values()) for tag in S.TAGS)>=2 and n-da['valid']<=1 and n==ca['valid'];go=ca['joint']>=da['joint']+2 and 3*ca['joint']>=2*n and ca['miss']<=da['miss'] and ca['false_live']<=da['false_live'];return ('FIELD_RETEST_ONLY' if go else 'NO_INTEGRATION') if eligible else 'INCONCLUSIVE'

def retest(root):
 pack=read(root/'packets.json');keys=read(root/'key.json');keys_ok(keys,pack);receipt=read(root/'key-receipt.json');assert receipt=={'packets':P.digest(P.enc(pack)),'key':P.digest(P.enc(keys)),'protocol':P.digest(P.enc(spec()))};P.write(root/'started.json',True);out=[];mr=Path('data/local_models');alias='rollout-transfer';order=list(pack);random.Random(851).shuffle(order)
 with S.T.narrator(mr,root/'direct.log',alias=alias,backend=S.T.default_backend(mr),model_id=S.MODEL) as (local,meta):
  P.write(root/'direct-runtime.json',meta)
  for c in order:
   gold=keys[c['id']];
   if gold['verdict']=='EXCLUDE':continue
   assert not c['author_error']; t={k:c[k] for k in ('subject','scope','documents','question')}
   for arm in ('code','direct'):
    ans=None;it=ot=0;err=None;finish=None;spans=[];start=time.monotonic()
    try:
     if arm=='code':ans=V.evaluate(t);spans=ans['receipts'];ans={k:ans[k] for k in ('verdict','sources')}
     else:
      b=S.T.request_body(alias,S.SYSTEM,S.prompt(t),max_tokens=S.CAP,response_format={'type':'json_schema','json_schema':{'name':'rollout','strict':True,'schema':S.SCHEMA}});z=local.post(b);ch=z['choices'][0];finish=ch['finish_reason'];it,ot=z['usage']['prompt_tokens'],z['usage']['completion_tokens'];ans=json.loads(ch['message']['content'])
    except Exception as e:err=type(e).__name__+': '+str(e)[:160]
    out.append(dict(case=c['id'],arm=arm,split='transfer',family=c['id'],answer=ans,receipts=spans,finish=finish,error=err,input_tokens=it,output_tokens=ot,seconds=round(time.monotonic()-start,4))|S.classify(ans,gold,err is not None or finish=='length' or ot>S.CAP))
   print('tested',len(out)//2,'of',len(pack),flush=True)
 P.write(root/'outputs.json',out);stats={}
 for a in ('direct','code'):
  rr=[x for x in out if x['arm']==a];stats[a]={k:sum(x[k] for x in rr) for k in ('joint','verdict','valid','refs','miss','false_live','wrong_definite','withheld','input_tokens','output_tokens')};stats[a]['n']=len(rr)
 rep={'experimental':True,'attempted':len(pack),'reviewed_usable':stats['code']['n'],'counts':stats,'decision':decision(keys,stats),'protocol_sha':P.digest(P.enc(spec()))};P.write(root/'report.json',rep);print(json.dumps(rep,indent=1))
if __name__=='__main__':
 sys.addaudithook(S.S.S.local_only);ap=argparse.ArgumentParser();ap.add_argument('action',choices=['freeze','author','seal-key','test']);ap.add_argument('root',type=Path);a=ap.parse_args();p=a.root
 if a.action=='freeze':p.mkdir(exist_ok=False);P.write(p/'protocol.json',spec());print('frozen',P.digest(P.enc(spec())))
 else:
  assert read(p/'protocol.json')==spec();{'author':author,'seal-key':reader_input,'test':retest}[a.action](p)
