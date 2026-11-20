"""Experimental: can a grounded, asset-specific materiality review improve real cached forecasts?"""
import argparse, json, random, re, sys
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
from core import local_models as L, headline_signal as H
from qualify import local_simulation as S, regime_news as R, news_followthrough as N, history_runtime as T, surprise_lab as B

NAMES={'AAPL':('Apple','iPhone','MacBook','iPad'),'NVDA':('Nvidia','GeForce','CUDA','Blackwell'),'MSFT':('Microsoft','Azure','Windows','Xbox'),'AMZN':('Amazon','AWS'),'TSLA':('Tesla','Cybertruck'),'AMD':('AMD','Ryzen','Radeon'),'GOOGL':('Alphabet','Google','YouTube','Android'),'BTC':('Bitcoin','BTC')}
CATEGORIES=('OPERATIONS','LEGAL','CAPITAL','NONE')
SYSTEM=('Review this one headline for materiality to the named asset, not price direction. OPERATIONS: a specific reported change in sales, production, contract, supply, demand or operations directly involving that asset. LEGAL: a concrete legal or regulatory action. CAPITAL: a concrete financing, buyback, acquisition or disposition. NONE: opinion, prediction, generic technology commentary, promotional aspiration, or no clear direct event for this asset. If one category fits, quote a short exact contiguous phrase from the headline containing the event. If NONE, leave quote empty. Return only the two JSON fields category and quote.')
SCHEMA={'type':'json_schema','json_schema':{'name':'materiality','strict':True,'schema':{'type':'object','properties':{'category':{'type':'string','enum':list(CATEGORIES)},'quote':{'type':'string'}},'required':['category','quote'],'additionalProperties':False}}}
SPEC={'v':'quote-veto-1','input':'Only asset and title; cached AtomicChat signed impact held fixed. One review per true asset/title plus deterministic alternative absent from explicit aliases. No direction asked. Swap is a diagnostic, not an assumed gold negative.', 'arms':'incumbent sum; uniform .75 all; literal alias 1 vs .5; quote-confirmed primary 1 vs .5 (nonempty verbatim 3+ words including a content word, supported category, title names target or predeclared product alias). A failed review gets .5, never dropped.', 'budget':'Per date: k = current method calls at abs(score)>=.5, capped to the common nonzero pool. Each arm chooses k highest absolute scores, asset ticker tie break.', 'gate':'Primary AUC over current paired 95% block lower >0, both old split AUC differences >0, AUC better than alias-only, correct calls at equal budgets strictly higher, all-case BA at unchanged live threshold non-decreasing. No promotion from this post hoc replay.', 'uncertainty':'1000 whole-cross-section circular moving-block draws of length 5. 2000 within-asset shuffles permute observed verifier weights across unique stories and reuse assignment for repeated exposures.', 'scope':'Known 96 cases only. No new prices, no new labels, no reopen of sealed windows. Precision of real materiality labels not measured.', 'aliases':NAMES,'prompt':SYSTEM,'schema':SCHEMA,'seed':521}


def named(t,title):return any(re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)',title,re.I) for a in set((t,)+NAMES[t]))

def assess(t,title,d):
    if not isinstance(d,dict) or set(d)!={'category','quote'} or d['category'] not in CATEGORIES or not isinstance(d['quote'],str):raise ValueError('Invalid review schema')
    q=d['quote'].strip(); ws=re.findall(r'\b\w+\b',q)
    if d['category']=='NONE':return False,'none'
    if not q or q not in title or len(ws)<3 or not any(len(w)>3 for w in ws):return False,'unverified_quote'
    if not named(t,title):return False,'no_explicit_asset'
    return True,'accepted'

def other(t,title):
    options=[u for u in sorted(NAMES) if u!=t and not named(u,title)]; return options[int(S.digest((t+'|'+title).encode())[:8],16)%len(options)] if options else None

def key(t,title):return S.digest(json.dumps([t,title],ensure_ascii=True).encode())

def sourcefiles():return [Path(__file__),Path(B.__file__),Path(T.__file__),Path(L.__file__),Path(S.__file__),Path(R.__file__),Path(N.__file__),Path(H.__file__),R.CACHE,*[Path(v) for v in S.FILES.values()]]

def spec(root):return {'rules':SPEC,'weights':L.narrator_spec(L.DEFAULT_NARRATOR)['sha'],'code_and_inputs':{p.resolve().relative_to(Path.cwd()).as_posix():S.digest(p.read_bytes()) for p in sourcefiles()},'batch':S.digest((root/'batch.json').read_bytes())}

def blind():
    roster={c['id'] for c in S.rows(R.CACHE)}; raw={r['id']:r for k in ('dev','window') for r in S.rows(Path(S.FILES[k])) if r['id'] in roster}; assert len(raw)==len(roster)==96
    ks=sorted({(r['symbol'],n['title']) for r in raw.values() for n in (r.get('news_served') or [])}); tasks=set(ks)
    for t,title in ks:
        u=other(t,title)
        if u:tasks.add((u,title))
    jobs=[{'asset':t,'title':tt,'id':key(t,tt)} for t,tt in sorted(tasks)];random.Random(SPEC['seed']).shuffle(jobs);return jobs

def freeze(root):
    jobs=blind();root.mkdir(parents=True,exist_ok=False);S.write_new(root/'batch.json',S.encoded(jobs));S.write_new(root/'protocol.json',S.encoded(spec(root)));return S.digest((root/'protocol.json').read_bytes())

def decision(rows,arm):return N.decisions(rows,arm)

def top(sub,arm,k):return {r['asset']:r[arm]>0 for r in sorted(sub,key=lambda rr:(-abs(rr[arm]),rr['asset']))[:k]}

def matched(rows):
    daily=[]
    for d in sorted({r['origin'] for r in rows}):
        p=[r for r in rows if r['origin']==d]; pool=[r for r in p if r['old']!=0 and r['review']!=0]; k=min(sum(abs(r['old'])>=H.CALL_LINE for r in p),len(pool)); a,b=(top(pool,arm,k) for arm in ('old','review')); y={r['asset']:r['y'] for r in p}; daily.append([k,sum(y[t]==v for t,v in a.items()),sum(y[t]==v for t,v in b.items())]);
    vec=np.array(daily);total,old,new=map(int,vec.sum(axis=0));rng=np.random.default_rng(521);groups=[np.array([i]) for i in range(len(vec))]; vals=[]
    for _ in range(1000):
        count,lo,hi=vec[R.block_indices(groups,min(len(vec),5),rng)].sum(axis=0)
        if count: vals.append((hi-lo)/count)
    return {'calls_each':total,'correct_current':old,'correct_review':new,'called_accuracy_delta':(new-old)/total if total else None,'interval':S.quantiles(vals)}

def weight(accepted):return 1. if accepted else .5

def synthetic_features(asset,titles,judged,review):
    impacts=[judged.get(asset+'|'+title,0.) for title in titles]; return {'old':sum(impacts),'uniform':sum(.75*i for i in impacts),'literal':sum(weight(named(asset,t))*i for t,i in zip(titles,impacts)),'review':sum(weight(review[key(asset,t)]['accepted'])*i for t,i in zip(titles,impacts))}

def score(root):
    preds=S.rows(root/'reviews.jsonl'); batch=json.loads((root/'batch.json').read_bytes()); rr={r['id']:r for r in preds}
    if len(rr)!=len(preds) or set(rr)!={b['id'] for b in batch}:raise ValueError('Missing or duplicate review')
    labels,fail=S.judgments(*(Path(S.FILES[k+'_judgments']) for k in ('dev','window'))); outcomes=S.rows(R.CACHE); raw={r['id']:r for k in ('dev','window') for r in S.rows(Path(S.FILES[k]))}; records=[]; keys=set(); missing=0
    for c in outcomes:
        t=c['asset']; n=raw[c['id']].get('news_served') or []; titles=[r['title'] for r in n]; keys.update((t,tt) for tt in titles); missing+=sum(t+'|'+tt not in labels for tt in titles); f=synthetic_features(t,titles,labels,rr)
        if abs(f['old']-c['headline'])>1e-6:raise ValueError('Cached incumbent score differs')
        records.append({**f,'titles':titles,'asset':t,'origin':c['origin'],'part':c['part'],'y':c['y']})
    if len(records)!=96 or len({r['origin'] for r in records})!=12:raise ValueError('Unexpected panel')
    arms=('old','uniform','literal','review'); metrics=R.score(records,arms); dif=R.paired(records,'review',['old','literal','uniform'],5); call={k:decision(records,k) for k in arms}; parts={p:R.score([r for r in records if r['part']==p],arms) for p in sorted({r['part'] for r in records})}; budget=matched(records); rng=random.Random(SPEC['seed']); count=0; weights={k:weight(rr[key(*k)]['accepted']) for k in sorted(keys)}; y=[r['y'] for r in records]
    for _ in range(2000):
        w=R.shuffled_stories(weights,rng); vals=[sum(w[r['asset'],t]*labels.get(r['asset']+'|'+t,0.) for t in r['titles']) for r in records]; count+=R.metric(vals,y)[0]>=metrics['review']['auc']
    def diagnostics(idlist):
        ds=[rr[i] for i in idlist]; return {'n':len(ds),'errors':sum(bool(r['error']) for r in ds),'raw_positive':sum(r['category'] not in ('ERROR','NONE') for r in ds),'verified_positive':sum(r['accepted'] for r in ds),'reasons':dict(Counter(r['reason'] for r in ds))}
    gate=dif['old']['lo']['auc']>0 and all(v['review']['auc']>v['old']['auc'] for v in parts.values()) and metrics['review']['auc']>metrics['literal']['auc'] and budget['correct_review']>budget['correct_current'] and call['review']['all_case_ba']>=call['old']['all_case_ba']
    return {'experimental':True,'n':len(records),'origins':12,'news_cases':sum(bool(r['titles']) for r in records),'eligible':diagnostics([key(*k) for k in keys]),'swaps':diagnostics([key(u,t) for a,t in keys if (u:=other(a,t))]),'review_calls':len(rr),'judge_failures':len(fail),'missing_impacts':missing,'metrics':metrics,'paired':dif,'parts':parts,'live_thresholds':call,'matched':budget,'within_asset_shuffles_ge':float(count/2000),'screen_passed':bool(gate),'live_change':False,'protocol_sha256':S.digest((root/'protocol.json').read_bytes())}

def main():
    sys.addaudithook(B.local_only);ap=argparse.ArgumentParser();ap.add_argument('action',choices=('freeze','run'));ap.add_argument('--root',type=Path,required=True);args=ap.parse_args();root=args.root.resolve();data=Path('data').resolve()
    if root==data or not root.is_relative_to(data):raise ValueError('Use new private data folder')
    if args.action=='freeze':print(freeze(root));return
    if S.encoded(spec(root))!=(root/'protocol.json').read_bytes():raise ValueError('Frozen input changed')
    S.write_new(root/'started',b'1');model=Path('data/local_models');batch=json.loads((root/'batch.json').read_bytes())
    with T.narrator(model,root/'local.server.log',alias='evidence-screen',backend=T.default_backend(model),model_id=L.DEFAULT_NARRATOR) as (call,meta), (root/'reviews.jsonl').open('x',encoding='utf8') as dest:
        S.write_new(root/'runtime.json',S.encoded(meta))
        for i,r in enumerate(batch):
            sym=r['asset'];title=r['title']; d=None;error=None
            try:
                d=json.loads(call(SYSTEM,f'Target asset: {sym} ({NAMES[sym][0]})\nHeadline: {title}',max_tokens=100,response_format=SCHEMA));valid,why=assess(sym,title,d)
            except Exception as exc:valid,why=False,'failure';error=type(exc).__name__
            dest.write(json.dumps({'id':r['id'],'accepted':valid,'reason':why,'category':d['category'] if not error else 'ERROR','error':error,'quote':d['quote'] if not error else ''})+'\n');dest.flush()
            if (i+1)%20==0:print('reviews',i+1,'/',len(batch),flush=True)
    if S.encoded(spec(root))!=(root/'protocol.json').read_bytes():raise ValueError('Inputs changed during prediction')
    rep=score(root);S.write_new(root/'report.json',S.encoded(rep));print('sealed',rep['n'],rep['review_calls'],rep['screen_passed'],flush=True)

if __name__=='__main__':main()
