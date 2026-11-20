"""Experimental local walk-forward price model and coverage-controlled news overlay."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import random
import numpy as np
from qualify import local_simulation as S

SPEC = {'version': 'regime-news-v1', 'universe': list(S.ASSETS), 'price_fold_years': list(range(2019, 2027)),
        'training': 'Every fifth SPY origin from index 60; last 1260 SPY sessions before fold. All exits strictly before cutoff. Pooled, class-balanced ridge logistic, penalty 0.08, no tuning.',
        'features': 'Past 1,5,20-session moves divided by trailing daily vol and sqrt(h); 5/20 relative SPY; SPY trend; log fast/slow vol; interactions of 5-session return with trend and vol; crypto flag. Training-only scaling and winsorization.',
        'target': 'Next SPY-session open to fifth SPY-session close; known panel, no futures invented. BTC uses same SPY dates.',
        'last_history_exit_before': '2026-10-27', 'news_model_cutoff': '2026-10-27', 'last_news_origin': '2026-11-11',
        'overlay': 'Fixed plus 0.35 times sum of unique signed impacts clipped to +/-0.75 divided by n+2. No news means price only; no backfitting.',
        'coverage_control': 'Same price and 0.35*0.75*n/(n+2), no sign or story identity. Same-count pair AUC and per-asset whole-story permutations.',
        'metrics': 'Rank AUC and balanced accuracy at fixed zero (ties half credit, no abstention) for every arm; live-call coverage and all-case balanced accuracy with abstentions counted as misses reported separately.',
        'bootstrap': '1000 seeded circular moving-block draws of whole origins, 8 non-overlap origins for prices and 5 adjacent origins for news. Differences paired. Descriptive, not a guarantee.',
        'price_gate': 'AUC >= .52, BA >= .50, AUC-minus-chance bootstrap lower > 0, majority of annual AUCs > .50.',
        'news_gate': 'Hybrid AUC difference lower > 0 vs BOTH price-only and count overlay, nonnegative BA change vs price, and hybrid AUC >= legacy sum on BOTH known splits.',
        'interpretation': 'Development only, no promotion. Historical cache is retrospective and universe is survivor-selected. News outcomes are explicitly reused. Outputs are ranking scores, not probabilities.', 'seed': 412}
CACHE = Path('data/local_news_simulation_v1/cases.jsonl')
ARMS = ('price','hybrid','blind','count','headline','momentum','constant')


def features(c, market, is_crypto=False):
    c = np.asarray(c, dtype=float)[-61:]; m = np.asarray(market, dtype=float)[-61:]
    if len(c) != 61 or len(m) != 61 or not np.all(np.isfinite(c)) or not np.all(np.isfinite(m)) or min(c) <= 0 or min(m) <= 0:
        return None
    r, s = np.diff(np.log(c)), np.diff(np.log(m)); vol = max(.002, np.std(r[-20:])); mv = max(.002, np.std(s[-20:]))
    def z(x, h, v): return float(np.clip(x[-h:].sum() / (v * h**.5), -5, 5))
    r1, r5, r20 = [z(r,h,vol) for h in (1,5,20)]; m5,m20 = z(s,5,mv),z(s,20,mv)
    vr = float(np.clip(np.log(max(.002,np.std(r[-5:])) / max(.002,np.std(r))), -2, 2))
    return [r1,r5,r20,r5-m5,r20-m20,m20,vr,r5*np.tanh(m20),r5*vr,float(is_crypto)]


def example(t, i, days, close, opens):
    if i < 60 or i + 5 >= len(days): return None,'history_or_horizon'
    x = features(close[t][i-60:i+1],close['SPY'][i-60:i+1],t=='BTC')
    if x is None: return None,'missing_features'
    en,ex = opens[t][i+1],close[t][i+5]
    if not (np.isfinite(en) and np.isfinite(ex) and en>0 and ex>0): return None,'missing_target'
    if en == ex: return None,'flat'
    return {'asset':t,'origin':days[i],'exit':days[i+5],'entry':days[i+1],'i':i,'x':x,'y':bool(ex>en),
            'ret':float(ex/en-1),'momentum':float(close[t][i]/close[t][i-20]-1),'constant':1.},None


def fit(rows):
    x=np.array([r['x'] for r in rows]); y=np.array([r['y'] for r in rows],float)
    if len(y)<2 or y.min()==y.max(): raise ValueError('Need both classes in matured training rows')
    mu=x.mean(axis=0); sd=np.maximum(x.std(axis=0),1e-6)
    a=np.column_stack([np.ones(len(x)),np.clip((x-mu)/sd,-4,4)])
    w=np.where(y==1,.5/y.mean(),.5/(1-y.mean()))/len(y)
    b=np.zeros(a.shape[1]); penalty=np.diag([0.]+[.08]*len(mu))
    for _ in range(30):
        p=1/(1+np.exp(-np.clip(a@b,-30,30))); g=a.T@(w*(p-y))+penalty@b
        h=a.T@((w*p*(1-p))[:,None]*a)+penalty
        step=np.linalg.solve(h,g); b-=step
        if np.max(np.abs(step))<1e-8: break
    return mu,sd,b


def predict(model, x):
    mu,sd,b=model
    return float(b[0]+np.clip((np.array(x)-mu)/sd,-4,4)@b[1:])


def train_before(rows, cutoff, first_i):
    return [r for r in rows if r['exit'] < cutoff and r['i'] >= first_i-1260 and r['origin'] < cutoff]


def overlays(price, scores):
    n=len(scores); signed=sum(max(-.75,min(.75,v)) for v in scores)
    return price + .35*signed/(n+2), price+.35*.75*n/(n+2)


def metric(a,y):
    a,y=np.asarray(a,float),np.asarray(y,bool); pos=y.sum(); neg=len(y)-pos
    if not pos or not neg: return np.array([np.nan,np.nan])
    order=np.argsort(a,kind='stable'); s=a[order]; yy=y[order]
    starts=np.r_[0,np.flatnonzero(s[1:]!=s[:-1])+1]; sizes=np.diff(np.r_[starts,len(s)])
    p=np.add.reduceat(yy.astype(int),starts); n=sizes-p
    auc=(p*(np.cumsum(n)-.5*n)).sum()/(pos*neg)
    vote=(a>0).astype(float)+.5*(a==0); ba=(vote[y].mean()+1-vote[~y].mean())/2
    return np.array([auc,ba])


def named(v): return {k:(float(x) if np.isfinite(x) else None) for k,x in zip(('auc','ba'),v)}

def score(rows, arms): return {a:named(metric([r[a] for r in rows],[r['y'] for r in rows])) for a in arms}


def block_indices(groups, width, rng):
    starts=rng.integers(len(groups),size=(len(groups)+width-1)//width)
    which=[(s+j)%len(groups) for s in starts for j in range(width)][:len(groups)]
    return np.concatenate([groups[i] for i in which])


def paired(rows, candidate, refs, width):
    dates=sorted({r['origin'] for r in rows}); ix=defaultdict(list)
    for i,r in enumerate(rows): ix[r['origin']].append(i)
    groups=[np.array(ix[d]) for d in dates]; y=np.array([r['y'] for r in rows]); rng=np.random.default_rng(SPEC['seed'])
    vals={a:np.array([r[a] for r in rows]) for a in (candidate,*refs)}; diff={a:[] for a in refs}
    for _ in range(1000):
        q=block_indices(groups,width,rng); ca=metric(vals[candidate][q],y[q]);
        for a in refs: diff[a].append(ca-metric(vals[a][q],y[q]))
    ans={}
    for a in refs:
        v=np.array(diff[a]); v=v[np.isfinite(v).all(axis=1)]
        if not len(v): raise ValueError('No two-class bootstrap draws')
        lo,hi=np.quantile(v,[.025,.975],axis=0)
        ans[a]={'point':named(metric(vals[candidate],y)-metric(vals[a],y)),'lo':named(lo),'hi':named(hi),'valid_draws':len(v)}
    return ans


def same_count(rows, arm):
    wins=0.; denom=0
    for n in {r['count'] for r in rows}:
        sub=[r for r in rows if r['count']==n]; up=sum(r['y'] for r in sub); pairs=up*(len(sub)-up)
        if pairs: wins+=pairs*score(sub,[arm])[arm]['auc']; denom+=pairs
    return {'auc': wins/denom if denom else None,'pairs':denom}


def shuffled_stories(stories, rng):
    shuffled={}
    for asset in sorted({k[0] for k in stories}):
        keys=sorted(k for k in stories if k[0]==asset); values=[stories[k] for k in keys]; rng.shuffle(values)
        shuffled.update(zip(keys,values))
    return shuffled


def attach(c, raw, judgments, cutoff):
    out={}; missing=0
    for n in raw.get('news_served') or []:
        at=n.get('available_at')
        if not S.finite(at) or not cutoff-7*86400 <= at <= cutoff: raise ValueError('News outside decision cutoff')
        k=(c['asset'],n['title']); id_=k[0]+'|'+k[1]
        if id_ not in judgments: missing+=1
        out[k]=judgments.get(id_,0.)
    return out,missing


def source_files():
    return [Path(__file__),Path(S.__file__),CACHE,*[Path(v) for v in S.FILES.values()],*[Path(S.PRICE_ROOT)/S.price_name(t) for t in (*S.ASSETS,'SPY')]]


def snapshot(): return {'rules':SPEC,'sha256':{p.as_posix() if not p.is_absolute() else p.name:S.digest(p.read_bytes()) for p in source_files()}}


def freeze(root):
    s=snapshot(); root.mkdir(parents=True,exist_ok=False); S.write_new(root/'protocol.json',S.encoded(s)); return S.digest((root/'protocol.json').read_bytes())


def take(root):
    frozen=json.loads((root/'protocol.json').read_bytes());
    if frozen!=snapshot(): raise ValueError('Frozen protocol, implementation or inputs changed')
    S.write_new(root/'started.json',S.encoded({'protocol':S.digest((root/'protocol.json').read_bytes())})); return frozen


def analyze():
    prices={t:S.price_rows(Path(S.PRICE_ROOT)/S.price_name(t)) for t in (*S.ASSETS,'SPY')}; days=sorted(prices['SPY']); di={d:i for i,d in enumerate(days)}
    cl={t:np.array([series.get(d,{}).get('close',np.nan) for d in days]) for t,series in prices.items()}; op={t:np.array([series.get(d,{}).get('open',np.nan) for d in days]) for t,series in prices.items()}
    cuts=Counter(); past=[]; end=SPEC['last_history_exit_before']
    for i in range(60,len(days)-5,5):
        if days[i+5]>=end: break
        for t in S.ASSETS:
            c,reason=example(t,i,days,cl,op)
            if reason: cuts[reason]+=1
            else: past.append(c)
    hist=[]; folds={}
    for yr in SPEC['price_fold_years']:
        test=[r for r in past if r['origin'][:4]==str(yr)]; cutoff=next(d for d in days if d[:4]==str(yr)); train=train_before(past,cutoff,di[cutoff]); model=fit(train)
        test=[{**r,'price':predict(model,r['x'])} for r in test]; hist.extend(test)
        folds[str(yr)]={'train':len(train),'test':len(test),'last_training_exit':max(r['exit'] for r in train),'test_start':min(r['origin'] for r in test),'test_last_exit':max(r['exit'] for r in test),'metrics':score(test,['price','momentum','constant'])}
    hscore=score(hist,['price','momentum','constant']); hdiff=paired(hist,'price',['constant','momentum'],8)
    cutoff=SPEC['news_model_cutoff']; news_train=train_before(past,cutoff,di[cutoff]); model=fit(news_train)
    cache=S.rows(CACHE); raw={r['id']:r for group in ('dev','window') for r in S.rows(Path(S.FILES[group]))}; judged,failed=S.judgments(*[Path(S.FILES[k+'_judgments']) for k in ('dev','window')]); news=[]; stories={}; missing=0; seen=set()
    for c in cache:
        if not cutoff<=c['origin']<=SPEC['last_news_origin'] or c['id'] in seen: raise ValueError('Unexpected cached panel membership')
        seen.add(c['id']); t=c['asset']; r,reason=example(t,di[c['origin']],days,cl,op)
        if reason or r['entry']!=c['entry'] or r['exit']!=c['exit'] or abs(r['ret']-c['return'])>1e-10: raise ValueError('Cached target mismatch')
        at=(datetime.fromisoformat(r['origin']).replace(tzinfo=timezone.utc)+timedelta(days=1)).timestamp(); st,miss=attach(c,raw[c['id']],judged,at); stories.update(st); missing+=miss
        p=predict(model,r['x']); h,b=overlays(p,st.values()); news.append({**r,'id':c['id'],'part':c['part'],'keys':list(st),'price':p,'hybrid':h,'blind':b,'headline':c['headline'],'count':len(st)})
    if len(news)!=96 or len({r['origin'] for r in news})!=12: raise ValueError('Incomplete or changed 96-case panel')
    sc=score(news,ARMS); dif=paired(news,'hybrid',['price','blind','headline'],5); parts={p:score([r for r in news if r['part']==p],ARMS) for p in sorted({r['part'] for r in news})}
    rng=random.Random(SPEC['seed']); y=[r['y'] for r in news]; sh=[]
    for _ in range(2000):
        mapping=shuffled_stories(stories,rng); a=[overlays(r['price'],[mapping[k] for k in r['keys']])[0] for r in news]; sh.append(float(metric(a,y)[0]))
    y=np.array(y); legacy=np.array([r['headline'] for r in news]); voted=np.abs(legacy)>=.5
    keep=[r for r in news if r['keys']]; removed=[]
    for r in news:
        vals=[stories[k] for k in r['keys']];
        if vals: vals.pop(max(range(len(vals)),key=lambda j:abs(vals[j])))
        removed.append({**r,'hybrid':overlays(r['price'],vals)[0]})
    pg=hscore['price']['auc']>=.52 and hscore['price']['ba']>=.5 and hdiff['constant']['lo']['auc']>0 and sum(f['metrics']['price']['auc']>.5 for f in folds.values())>len(folds)/2
    ng=all(dif[a]['lo']['auc']>0 for a in ('price','blind')) and dif['price']['point']['ba']>=0 and all(p['hybrid']['auc']>=p['headline']['auc'] for p in parts.values())
    return {'experimental':True,'price':{'n':len(hist),'origins':len({r['origin'] for r in hist}),'excluded_on_full_grid':dict(cuts),'folds':folds,'metrics':hscore,'paired':hdiff,'gate':bool(pg)},
            'news':{'n':len(news),'origins':len({r['origin'] for r in news}),'unique_asset_titles':len(stories),'failed_cached_judgments':len(failed),'missing_served':missing,'matches_to_cached_targets':len(news),'model_train':len(news_train),'last_training_exit':max(r['exit'] for r in news_train),'metrics':sc,'paired':dif,'parts':parts,'same_count':{a:same_count(news,a) for a in ('hybrid','blind','price','headline')},'news_only':score(keep,ARMS),'drop_strongest':score(removed,['hybrid']),'legacy_live_coverage':float(voted.mean()),'legacy_live_all_case_ba':float(.5*((legacy[y]>=.5).mean()+(legacy[~y]<=-.5).mean())), 'permuted_median_auc':float(np.median(sh)),'permuted_at_least_actual':float(np.mean(np.array(sh)>=sc['hybrid']['auc'])), 'gate':bool(ng)},'promotion':False}


def main():
    S.install_local_only(); p=argparse.ArgumentParser(); p.add_argument('action',choices=('freeze','run')); p.add_argument('--root',type=Path,required=True); a=p.parse_args(); root=a.root.resolve(); data=Path('data').resolve()
    if root==data or not root.is_relative_to(data): raise ValueError('Use a new private data subfolder')
    if a.action=='freeze': print(freeze(root)); return
    old=take(root); result=analyze()
    if old!=snapshot(): raise ValueError('Inputs changed during experiment')
    result['protocol_sha256']=S.digest((root/'protocol.json').read_bytes()); S.write_new(root/'report.json',S.encoded(result)); print(json.dumps(result,indent=2))


if __name__=='__main__': main()
