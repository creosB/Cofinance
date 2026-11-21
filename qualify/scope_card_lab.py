"""Local paired decision-and-citation screen. A deterministically checked card is not a fourth source."""
import argparse,hashlib,json,random,sys,time
from pathlib import Path
from core import local_models as L
from core.documents import select_excerpts
from qualify import scope_extraction as E,history_runtime as T,surprise_lab as S,scope_extraction_lab as X
PROMPT='Use only the three source excerpts for the named subject, period and measure. Answer MET, MISSED or UNKNOWN. A plan, wrong scope, missing rule, unreconciled contradiction or withdrawal cannot establish success or failure. Select the source ID for the rule, then the decisive observation. Use other only for a material conflicting observation or withdrawal that makes the verdict UNKNOWN; do not cite a routine distractor or a predecessor explicitly superseded by the decisive signed record. If both observations give only the wrong time window, cite them both. If a checked card is available it summarizes these excerpts, not a fourth source. If no card is available, reason from the excerpts. Return JSON only.'
POLICY='Each arm sees the same three labeled excerpts and question; only the card supplement differs. Same model/runtime, 72 maximum output tokens, one call per packet per arm, random order, record tokens/time/errors. Card-only: directly return certified verdict and controlling sources when resolved, otherwise withhold without inferred citations. Report verdict correctness, verdict plus correct rule and exact minimal observation set (UNKNOWN requires supporting references too), definite errors and abstentions, not free-text quality. Incorrect cited answer does not earn a joint point. If card augmentation improves joint count vs direct with no increase in unwarranted definite answers, treat as a development signal only. Prefer the deterministic result on resolved packets unless model beats card-only on that subset. If tied or worse do not promote. No UI or production change.'
KEYS=('rule','primary','other');OPT=['A','B','C','none'];schema={'type':'object','properties':{'verdict':{'type':'string','enum':['MET','MISSED','UNKNOWN']},**{k:{'type':'string','enum':OPT} for k in KEYS}},'required':['verdict',*KEYS],'additionalProperties':False}
def cases():
    out=[]
    def add(i,fam,entity,rule,primary,extra,v,refs):
        cash=fam.startswith('cash'); names=('Covenant','Ledger','Flash') if cash else ('Policy','Ledger','Dashboard');docs=[{'id':name,'name':name+'.pdf','pages':[txt]} for name,txt in zip(names,[rule,primary,extra])];q=f'Did {entity} meet its '+('quarter-end unrestricted cash floor?' if cash else 'full-month uptime commitment?');out.append(dict(id=i,family=fam,q=q,docs=docs,gold={'verdict':v,'rule':names[0]+'.pdf','observations':[names[n]+'.pdf' for n in refs]}))
    for e,x,k,op in [('Opal',63,63,'at least'),('Topaz',27,27,'strictly above'),('Rowan',44,48,'at least')]:
        add(e,'cash-boundary',e,f'{e} must hold {op} {k} million dollars of unrestricted cash at quarter end.',f'The signed quarter-end ledger records {x} million dollars of unrestricted cash for {e}.',f'The preliminary quarter-end total cash figure for {e} was {k+8} million dollars.','MET' if e=='Opal' else 'MISSED',[1])
    for e,x,k,op in [('Bay','99.6','99.6','strictly above'),('Aster','98.95','98.9','strictly above'),('Sumac','97.7','97.7','at least')]:
        add(e,'uptime-boundary',e,f'{e} requires {op} {k} percent uptime for the entire month.',f'The signed full-month uptime ledger for {e} shows {x} percent.',f'The dashboard for {e} showed 99.99 percent uptime for the first week only.','MISSED' if e=='Bay' else 'MET',[1])
    add('revised','cash-correction','Slate','Slate must hold at least 67 million dollars of unrestricted cash at quarter end.','The signed quarter-end reconciliation supersedes the flash and records 66 million dollars of unrestricted cash for Slate.','The signed quarter-end ledger records 73 million dollars of unrestricted cash for Slate.','MISSED',[1]);add('unreconciled','cash-correction','Alder','Alder must hold at least 24 million dollars of unrestricted cash at quarter end.','The signed quarter-end ledger records 22 million dollars of unrestricted cash for Alder.','The signed quarter-end ledger records 26 million dollars of unrestricted cash for Alder.','UNKNOWN',[1,2]);add('no-month','uptime-scope','Juniper','Juniper requires at least 98 percent uptime for the entire month.','The dashboard for Juniper showed 99.1 percent uptime for the first week only.','The dashboard for Juniper showed 99.8 percent uptime for the first week only.','UNKNOWN',[1,2]);add('withdrawn','uptime-scope','Fern','Fern requires strictly above 99 percent uptime for the entire month.','The signed full-month uptime ledger for Fern shows 99.4 percent.','The signed full-month uptime ledger for Fern was withdrawn without replacement.','UNKNOWN',[1,2]);add('cash-language','cash-prose','Coral','Coral must hold at least 35 million dollars of unrestricted cash at quarter end.','The signed quarter-end statement for Coral lists cash free of restrictions at 36 million US dollars.','The preliminary quarter-end total cash figure for Coral was 41 million dollars.','MET',[1]);add('uptime-language','uptime-prose','Laurel','Laurel requires strictly above 99 percent uptime for the entire month.','Laurel: 99% uptime (signed ledger; reporting window: the full month).','The dashboard for Laurel showed 99.9 percent uptime for the first week only.','MISSED',[1]);return out

def dec(v,m):return v if v in m else None
def packet(c):
    if set(c)!={'id','q','docs'}:raise ValueError('Do not pass scoring keys to generation')
    ex=select_excerpts(c['docs'],c['q']); target={(d['name'],d['pages'][0]) for d in c['docs']};assert {(e['name'],e['text']) for e in ex}==target and len(ex)==3
    ex=sorted(ex,key=lambda e:hashlib.sha256((c['id']+e['name']).encode()).digest());ref={e['name']:chr(65+i) for i,e in enumerate(ex)};raw=c['q']+'\n'+'\n'.join(ref[e['name']]+' ['+e['name']+', page '+str(e['page'])+']: '+e['text'] for e in ex);f=E.analyze(c['q'],ex);card=f['card'];only={'verdict':'UNKNOWN','rule':'none','primary':'none','other':'none'} if card is None else {'verdict':card['verdict'].upper(),'rule':ref[card['rule_file']],'primary':ref[card['actual_file']],'other':'none'};addon='No certified card for this packet. This is not itself a verdict.' if not card else json.dumps({k:v for k,v in card.items() if k not in ('actual_file','rule_file')}|{'rule_ref':only['rule'],'actual_ref':only['primary']},sort_keys=True);return raw,addon,ref,only,bool(card)
def classify(pred,gold,ref):
    if not pred or set(pred)!=set(schema['required']) or pred['verdict'] not in ('MET','MISSED','UNKNOWN') or any(pred[k] not in OPT for k in KEYS):return dict(valid=False,verdict=False,joint=False,bad_definite=False,abstain=False)
    v=pred['verdict'];vs=v==gold['verdict'];refs=[pred[k] for k in ('primary','other') if pred[k]!='none'];ok=(pred['rule']==ref[gold['rule']] and len(refs)==len(set(refs)) and pred['primary']!='none' and set(refs)=={ref[n] for n in gold['observations']});return dict(valid=True,verdict=vs,joint=vs and ok,bad_definite=v!='UNKNOWN' and not vs,abstain=v=='UNKNOWN')
def summary(rows):
    d={}
    for a in ('direct','assisted','card_only'):
        rr=[r for r in rows if r['arm']==a];d[a]={k:sum(r[k] for r in rr) for k in ('valid','verdict','joint','bad_definite','abstain','input_tokens','output_tokens')};d[a]['resolved_joint']=sum(r['joint'] for r in rr if r['card_available']);d[a]['no_card_joint']=sum(r['joint'] for r in rr if not r['card_available']);d[a]['errors']=sum(bool(r['error']) for r in rr);d[a]['truncated']=sum(r['finish']=='length' for r in rr);d[a]['seconds']=round(sum(r['seconds'] for r in rr),3)
    return d
def pr():return dict(policy=POLICY,prompt=PROMPT,schema=schema,labels=X.sha(X.encode(cases())),model=L.DEFAULT_NARRATOR,cap=72,code={Path(x).name:X.sha(Path(x).read_bytes()) for x in [__file__,E.__file__,T.__file__,X.__file__]})
def run(root):
    if X.read(root/'protocol.json')!=pr():raise ValueError('Frozen synthesis protocol changed')
    X.save(root/'started',True);rng=random.Random(619);cs=cases();rng.shuffle(cs);blind=[];scores=[];modelroot=Path('data/local_models');alias='scope-card-screen'
    with T.narrator(modelroot,root/'server.log',alias=alias,model_id=L.DEFAULT_NARRATOR,backend=T.default_backend(modelroot)) as (call,meta):
        X.save(root/'runtime.json',meta)
        for c in cs:
            raw,addon,ref,only,has=packet({k:c[k] for k in ('id','q','docs')});arms=['direct','assisted'];rng.shuffle(arms)
            for arm in arms+['card_only']:
                error=None;tk=[0,0];finish=None; start=time.monotonic();ans=None
                try:
                    if arm=='card_only':ans=only
                    else:
                        b=T.request_body(alias,PROMPT,raw+('\nChecked card: '+addon if arm=='assisted' else ''),max_tokens=72,response_format={'type':'json_schema','json_schema':{'name':'decision','strict':True,'schema':schema}});d=call.post(b);ch=d['choices'][0];u=d['usage'];tk=[u['prompt_tokens'],u['completion_tokens']];finish=ch['finish_reason'];ans=json.loads(ch['message']['content'])
                except Exception as err:error=str(err)
                secs=time.monotonic()-start;blind.append(dict(id=c['id'],arm=arm,response=ans,ref=ref,card_available=has,error=error,finish=finish,seconds=round(secs,3),input_tokens=tk[0],output_tokens=tk[1]));
            print('finished packet',c['id'],flush=True)
    X.save(root/'outputs.json',blind);key={c['id']:c for c in cs}
    for b in blind:scores.append({k:v for k,v in b.items() if k not in ('response','ref')}|classify(b['response'],key[b['id']]['gold'],b['ref']))
    totals=summary(scores);out=dict(n=len(cs),resolved=sum(b['arm']=='card_only' and b['card_available'] for b in scores),arms=totals,protocol=X.sha(X.encode(pr())),production_change=False);X.save(root/'scores.json',scores);X.save(root/'report.json',out);return out
if __name__=='__main__':
    sys.addaudithook(S.local_only);ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['freeze','run']);ap.add_argument('--root',type=Path,required=True);a=ap.parse_args();r=a.root.resolve();data=Path('data').resolve();assert r!=data and r.is_relative_to(data)
    if a.mode=='freeze':
        for c in cases():packet({k:c[k] for k in ('id','q','docs')})
        r.mkdir();X.save(r/'protocol.json',pr());print(X.sha(X.encode(pr())))
    else:print(json.dumps(run(r),indent=1))
