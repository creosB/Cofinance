"""Isolate economic-surprise reasoning from market prediction; never changes live decisions."""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
import json, random, re, statistics, time
from core import headline_signal as H, local_models as L
from qualify import surprise_cases as F, surprise_review as V, local_simulation as S, history_runtime as T

ARMS=('live','semantic','rules','evidence','rules_router','evidence_router')
REPORT='news_gated.jsonl'
CACHE=Path('data/replay_20261116')/REPORT
SPEC={'id':'surprise-v1','seed':17029,'arms':ARMS,'gold':'Explicit financial surprise only; UP better than consensus, DOWN worse, ABSTAIN equal/missing/conflicting/unlike. Not market return. Live prompt deliberately has a different target.', 'fixtures':'8 families, 2 controlled renderings x 8 conditions per family, plus 3 manually written challenge items per family. Challenge is unseen by inference, not an externally blind set. No tuning after predictions.','primary':'evidence_router vs semantic and rules_router on identical items. Controls: live prompt, rules, evidence only. Router uses verified arithmetic when available and semantic model otherwise. Routes and malformed outputs counted.', 'gate':'Both panels macro recall >= .80, both sides correct >= .75, neutral recall >= .80, no failed model calls; to prefer extra reviewer, paired family-bootstrap delta macro recall vs rules_router lower >0 on challenge. Semantic qualification only, never promote forecasts.','uncertainty':'Resample entire metric families 2000 times, paired seed 17029. Only 8 clusters. Per-case accuracy, macro recall, pair exact, abstention and errors in denominator, controls by class/condition.','coverage':'Probe only title applicability of the strict evidence rule on all unique (ticker,title) in frozen news_gated.jsonl and on replay keys in the already scored case file. Not factual precision or forecast performance. No external source.', 'model':L.DEFAULT_NARRATOR,'temperature':0,'thinking':False,'no_downloads':True}


def files():return [Path(__file__),Path(F.__file__),Path(V.__file__),Path(H.__file__),Path(T.__file__),Path(L.__file__),Path(S.__file__),CACHE,Path('data/local_news_simulation_v1/cases.jsonl'),Path(S.FILES['dev']),Path(S.FILES['window'])]

def manifest(root):return {'rules':SPEC,'runtime_tag':L.RUNTIME_TAG,'model_revision':L.narrator_spec(L.DEFAULT_NARRATOR)['revision'],'weight_sha256':L.narrator_spec(L.DEFAULT_NARRATOR)['sha'],'hashes':{str(p.resolve().relative_to(Path.cwd()).as_posix()):S.digest(p.read_bytes()) for p in files()},'blind_sha':S.digest((root/'blind.json').read_bytes()),'key_sha':S.digest((root/'key.json').read_bytes())}

def freeze(root):
    blind,key=F.corpus(); root.mkdir(parents=True,exist_ok=False); S.write_new(root/'blind.json',S.encoded(blind)); S.write_new(root/'key.json',S.encoded(key)); S.write_new(root/'protocol.json',S.encoded(manifest(root))); return S.digest((root/'protocol.json').read_bytes())

def verify(root):
    if S.encoded(manifest(root))!=(root/'protocol.json').read_bytes():raise ValueError('Frozen code, cases, cache or protocol changed. Use a new run.')

def result(f):
    t=time.perf_counter()
    try:
        v=f(); err=None
    except Exception as exc: v=None; err=type(exc).__name__
    return v,err,round(time.perf_counter()-t,3)

def evaluate_blind(row,call):
    if set(row)!={'id','asset','title'}:raise ValueError('Only blind fields allowed at prediction')
    sym,title=row['asset'],row['title']; text=H.prompt(sym,title)
    def live():
        x=H.judge(lambda sys,p,**kw:call(sys,p,return_choice=True,**kw),sym,title)
        if x['label'] not in H.LABELS:raise ValueError('Invalid live label')
        return x['label']
    def semantic():
        l=H.parse_label(call(V.DIRECT,text,max_tokens=24,response_format=H.SCHEMA));
        if l not in H.LABELS:raise ValueError('Invalid semantic label')
        return l
    def select():
        cs='\n'.join(f'{i}: {c}' for i,c in enumerate(V.clauses(title))); return V.pick(call(V.SELECT,f'Asset: {sym}\nClauses:\n{cs}',max_tokens=36,response_format=V.SCHEMA))
    fun={'live':live,'semantic':semantic,'select':select}; order=list(fun); random.Random(int(row['id'],16)).shuffle(order); outs={a:result(fun[a]) for a in order}
    l,d,sel=[outs[k][0] for k in ('live','semantic','select')]; rule,why=V.review(title); ev,reason=V.review(title,sel) if sel is not None else ('ERROR','selection_failed'); d=d or 'ERROR'
    router,route=V.routed(d,ev,reason); no_sel,route0=V.routed(d,rule,why)
    return {'id':row['id'],'labels':dict(zip(ARMS,(l or 'ERROR',d,rule,ev,no_sel,router))),'selection':sel,'rules_reason':why,'evidence_reason':reason,'route':route,'rules_route':route0,'errors':{k:v[1] for k,v in outs.items() if v[1]},'seconds':{k:v[2] for k,v in outs.items()}}

def metrics(rows,arm):
    cm=Counter((g['expected'],r['labels'][arm]) for g,r in rows); true={v:sum(x for (a,b),x in cm.items() if a==v) for v in H.LABELS}; recall={v:cm[v,v]/true[v] if true[v] else 0. for v in H.LABELS}; total=len(rows); ps=defaultdict(dict)
    for g,r in rows:
        if g['case'] in ('beat','miss'): ps[g['family'],g['style']][g['case']]=r['labels'][arm]==g['expected']
    pairs=sum(len(x)==2 and all(x.values()) for x in ps.values()); pred=Counter(r['labels'][arm] for g,r in rows); return {'n':total,'correct':sum(cm[v,v] for v in H.LABELS),'accuracy':sum(cm[v,v] for v in H.LABELS)/total,'macro_recall':sum(recall.values())/3,'recall':recall,'correct_pairs':pairs,'pairs':len(ps),'pair_rate':pairs/len(ps) if ps else None,'predicted':dict(pred),'confusion':{a:{b:cm[a,b] for b in (*H.LABELS,'ERROR')} for a in H.LABELS}}

def paired(rows,a,b):
    fam=sorted({g['family'] for g,r in rows}); rng=random.Random(SPEC['seed']); parts={f:[(g,r) for g,r in rows if g['family']==f] for f in fam}; vals=[]
    for _ in range(2000):
        bag=[r for f in rng.choices(fam,k=len(fam)) for r in parts[f]]; vals.append(metrics(bag,a)['macro_recall']-metrics(bag,b)['macro_recall'])
    return {'delta':metrics(rows,a)['macro_recall']-metrics(rows,b)['macro_recall'],'interval':S.quantiles(vals)}

def coverage():
    gated={(r['ticker'],r['title']) for r in S.rows(CACHE)}; ids={r['id'] for r in S.rows(Path('data/local_news_simulation_v1/cases.jsonl'))}; served={(r['symbol'],n['title']) for k in ('dev','window') for r in S.rows(Path(S.FILES[k])) if r['id'] in ids for n in r.get('news_served',[])}
    def counts(keys):
        out=Counter(V.review(title)[1] for _,title in keys); return {'asset_titles':len(keys),'fully_verified':sum(v for k,v in out.items() if k.startswith('verified_')),'reasons':dict(out),'contains_estimate_marker':sum(bool(V.EST.search(t)) for _,t in keys)}
    return {'gated_cache':counts(gated),'originally_scored':counts(served),'from_both':len(served&gated)}

def report(root):
    verify(root); key=json.loads((root/'key.json').read_bytes()); preds=S.rows(root/'predictions.jsonl'); ids=[r['id'] for r in preds]
    if len(ids)!=len(set(ids)) or set(ids)!=set(key):raise ValueError('Not exactly one prediction per frozen case')
    rows=[(key[r['id']],r) for r in preds]; arms={}; gaps={}; by_condition={}; errs=Counter(k for _,r in rows for k in r['errors'])
    for split in ('controlled','challenge'):
        sub=[(g,r) for g,r in rows if g['split']==split]; arms[split]={k:metrics(sub,k) for k in ARMS}; gaps[split]={k:paired(sub,'evidence_router',k) for k in ('semantic','rules_router')}; by_condition[split]={}
        for cond in sorted({g['case'] for g,r in sub}):
            ss=[(g,r) for g,r in sub if g['case']==cond]; by_condition[split][cond]={k:sum(r['labels'][k]==g['expected'] for g,r in ss) for k in ARMS}; by_condition[split][cond]['n']=len(ss)
    def passed(m):return m['macro_recall']>=.8 and m['pair_rate']>=.75 and m['recall']['ABSTAIN']>=.8
    semantic=all(passed(ms['evidence_router']) for ms in arms.values()) and not errs
    return {'experimental':True,'n':len(rows),'families':len({g['family'] for g,r in rows}),'metrics':arms,'paired_vs_router':gaps,'by_condition_correct':by_condition,'route_by_split':{s:dict(Counter(r['route'] for g,r in rows if g['split']==s)) for s in arms},'call_errors':dict(errs),'calls':len(rows)*3,'median_seconds_per_call':{k:statistics.median(r['seconds'][k] for g,r in rows) for k in ('live','semantic','select')},'cache_coverage':coverage(),'semantic_router_gate':bool(semantic),'extra_reviewer_gate':bool(semantic and gaps['challenge']['rules_router']['interval']['low']>0),'market_accuracy_tested':False,'live_change':False,'protocol_sha256':S.digest((root/'protocol.json').read_bytes()),'runtime':json.loads((root/'runtime.json').read_bytes())}

def local_only(event,args):
    if event=='socket.connect' or event=='socket.bind':
        addr=args[1]; host=addr[0] if isinstance(addr,tuple) else None
        if host not in ('127.0.0.1','::1'): raise PermissionError('Benchmark permits only numeric loopback sockets')
    if event in ('socket.getaddrinfo','socket.gethostbyname') and args[0] not in ('127.0.0.1','::1'): raise PermissionError('No external DNS in local benchmark')
    if event=='subprocess.Popen':
        candidate=args[0]; cmd=args[1]
        if candidate is None:
            if isinstance(cmd,(tuple,list)): candidate=cmd[0] if cmd else ''
            else:
                m=re.match(r'^\s*(?:"([^"]+)"|(\S+))',cmd or ''); candidate=(m.group(1) or m.group(2)) if m else ''
        exe=Path(candidate).resolve(); base=Path('data/local_models').resolve()
        if exe.name.lower()!='llama-server.exe' or not exe.is_relative_to(base): raise PermissionError('Only managed llama-server may start')
    if event=='os.system':raise PermissionError('No shell in local benchmark')


def main():
    import sys
    sys.addaudithook(local_only)
    p=argparse.ArgumentParser(); p.add_argument('action',choices=('freeze','run')); p.add_argument('--root',required=True,type=Path); args=p.parse_args(); root=args.root.resolve(); data=Path('data').resolve()
    if root==data or not root.is_relative_to(data):raise ValueError('Only new private data subfolders')
    if args.action=='freeze':print(freeze(root));return
    verify(root); S.write_new(root/'started',b'1'); blind=json.loads((root/'blind.json').read_bytes()); model=Path('data/local_models');
    with T.narrator(model,root/'local.server.log',alias='surprise-eval',backend=T.default_backend(model),model_id=L.DEFAULT_NARRATOR) as (call,meta), (root/'predictions.jsonl').open('x',encoding='utf8') as stream:
        S.write_new(root/'runtime.json',S.encoded(meta))
        for i,r in enumerate(blind,1):
            v=evaluate_blind(r,call);stream.write(json.dumps(v)+'\n');stream.flush()
            if i%20==0:print('cases',i,'/',len(blind),flush=True)
    rep=report(root); S.write_new(root/'report.json',S.encoded(rep)); print('sealed',rep['n'],'cases',rep['calls'],'calls',rep['call_errors'],flush=True)

if __name__=='__main__':main()
