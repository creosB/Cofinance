"""Experimental: does observed, market-adjusted follow-through improve cached headline calls?"""
import argparse
import bisect
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import numpy as np
from qualify import regime_news as R, local_simulation as S

SPEC={'version':'market-followthrough-v1','panel':'same 96 previously inspected cases, 12 origins, no new outcomes',
      'event_clock':'First SPY session strictly after index-available UTC day. Open-to-close only. Never use a bar dated after the decision origin. This is delayed follow-through, not causal news attribution or a release surprise.',
      'benchmark':'Fit asset on SPY open-to-close log returns, preceding 60 sessions only, intercept, beta capped [-3,3]; scale abnormal return by trailing residual RMS floored at 0.003. No tradeable intraday timing assumed. BTC uses UTC bars mapped onto SPY dates.',
      'candidate':'Group distinct titles by asset and first eligible realized session; pool pending titles together. One mean signed judgment per group, so different weekend stories cannot multiply the same Monday move. Multiply each group by 1 + 0.5*tanh(sign(judgment)*abnormal_z). No available reaction: weight 1. Sum groups. No price-regime fit or outcomes in predictor.',
      'controls':['headline','grouped','response','count','constant'], 'decision':'Screen only. Candidate AUC must improve vs headline and grouped with paired 95% lower > 0, improve vs headline on both old splits, not lose all-case BA vs legacy or more than 5 percentage points of call coverage. No promotion from reused data.',
      'calls':'Same +/-0.5 cutoff for headline/grouped/candidate; abstention counts as a miss for all-case BA. Also report full-coverage sign score (half credit on exact ties) separately.', 'bootstrap':'1000 circular five-origin blocks, all assets together, paired, seed 412. Small dependent sample.'}


def aligned(t, day, origin, days, oc):
    j=bisect.bisect_right(days,day)
    if j>=len(days) or days[j]>origin: return None,'not_yet_observable'
    if j<60: return None,'insufficient_history'
    y,x=oc[t][j-60:j],oc['SPY'][j-60:j]; a,m=oc[t][j],oc['SPY'][j]
    if not np.isfinite(np.r_[x,y,a,m]).all(): return None,'missing_bar'
    xc=x-x.mean(); yc=y-y.mean(); vx=np.dot(xc,xc)
    beta=float(np.clip(np.dot(xc,yc)/vx if vx>1e-12 else 0.,-3,3)); alpha=float(y.mean()-beta*x.mean())
    scale=max(.003,float(np.sqrt(np.mean((y-alpha-beta*x)**2)))); return (a-alpha-beta*m)/scale,'observed'


def blind_predict(t, origin, records, scores, days, oc):
    cutoff=(datetime.fromisoformat(origin).replace(tzinfo=timezone.utc)+timedelta(days=1)).timestamp()
    unique={}; groups=defaultdict(list); missing=0
    for r in records:
        ts=r.get('available_at'); title=r['title']; key=t+'|'+title
        if not S.finite(ts) or not cutoff-7*86400<=ts<=cutoff: raise ValueError('Served news outside the prediction cutoff')
        unique[title]=min(ts,unique.get(title,ts))
    known=days[:bisect.bisect_right(days,origin)]; anchors={}
    for title,ts in sorted(unique.items()):
        d=datetime.fromtimestamp(ts,timezone.utc).date().isoformat(); j=bisect.bisect_right(known,d); session=known[j] if j<len(known) else 'pending'; anchors[session]=d; key=t+'|'+title; missing+=key not in scores; groups[session].append(scores.get(key,0.))
    candidate=grouped=response=0.; counts=Counter()
    for session,values in sorted(groups.items()):
        z,why=aligned(t,anchors[session],origin,known,oc); counts[why]+=1; q=float(np.mean(values)); grouped+=q
        candidate+=q*(1. if z is None else 1.+.5*np.tanh(np.sign(q)*z)); response+=0. if z is None else np.tanh(z)
    return {'candidate':float(candidate),'grouped':grouped,'response':float(response),'count':len(unique),'constant':1.,'missing':missing},counts


def decisions(rows, key):
    s=np.array([r[key] for r in rows]); y=np.array([r['y'] for r in rows],bool); up=s>=.5; dn=s<=-.5
    return {'up':int(up.sum()),'down':int(dn.sum()),'abstain':int((~(up|dn)).sum()),'coverage':float((up|dn).mean()),'all_case_ba':float(.5*(up[y].mean()+dn[~y].mean())) if y.any() and (~y).any() else None}


def score_slice(rows):
    keys=('candidate','grouped','response','headline','count','constant'); sc=R.score(rows,keys)
    for k in keys: sc[k]['calls']=decisions(rows,k)
    return sc


def snapshot():
    paths=[Path(__file__), *R.source_files()]; return {'rules':SPEC,'hashes':{p.as_posix() if not p.is_absolute() else p.name:S.digest(p.read_bytes()) for p in paths}}


def experiment():
    bars={t:S.price_rows(Path(S.PRICE_ROOT)/S.price_name(t)) for t in (*S.ASSETS,'SPY')}; days=sorted(bars['SPY']); oc={t:np.array([np.log(b[d]['close']/b[d]['open']) if d in b else np.nan for d in days]) for t,b in bars.items()}
    cached=S.rows(R.CACHE); raw={r['id']:r for group in ('dev','window') for r in S.rows(Path(S.FILES[group]))}; scored,failed=S.judgments(*[Path(S.FILES[g+'_judgments']) for g in ('dev','window')]); cases=[]; accounting=Counter()
    for c in cached:
        s,n=blind_predict(c['asset'],c['origin'],raw[c['id']].get('news_served') or [],scored,days,oc); accounting.update(n)
        cases.append({**s,'id':c['id'],'origin':c['origin'],'part':c['part'],'y':c['y'],'headline':c['headline']})
    if len({r['id'] for r in cases})!=len(cases) or len(cases)!=96 or len({r['origin'] for r in cases})!=12: raise ValueError('Unexpected panel')
    stats=score_slice(cases); parts={p:score_slice([r for r in cases if r['part']==p]) for p in sorted({r['part'] for r in cases})}; d=R.paired(cases,'candidate',['headline','grouped','response','count'],5)
    a=stats['candidate']['calls']; b=stats['headline']['calls']; gate=all(d[k]['lo']['auc']>0 for k in ('headline','grouped')) and all(p['candidate']['auc']>p['headline']['auc'] for p in parts.values()) and a['all_case_ba']>=b['all_case_ba'] and a['coverage']>=b['coverage']-.05
    return {'experimental':True,'cases':len(cases),'origins':12,'news_cases':sum(r['count']>0 for r in cases),'group_occurrences':dict(accounting),'missing_served_judgments':sum(r['missing'] for r in cases),'failed_stored_judgments':len(failed),'metrics':stats,'paired':d,'parts':parts,'news_only':score_slice([r for r in cases if r['count']]),'gate':bool(gate),'promoted':False}


def main():
    S.install_local_only(); ap=argparse.ArgumentParser(); ap.add_argument('action',choices=('freeze','run')); ap.add_argument('--root',type=Path,required=True); args=ap.parse_args(); root=args.root.resolve(); data=Path('data').resolve()
    if root==data or not root.is_relative_to(data): raise ValueError('Use a new private data subfolder')
    s=snapshot(); p=root/'protocol.json'
    if args.action=='freeze':
        root.mkdir(parents=True,exist_ok=False); S.write_new(p,S.encoded(s)); print(S.digest(p.read_bytes())); return
    if s!=json.loads(p.read_bytes()): raise ValueError('Refuse changed inputs or protocol')
    sha=S.digest(p.read_bytes()); S.write_new(root/'started.json',S.encoded({'protocol':sha})); r=experiment()
    if s!=snapshot(): raise ValueError('Inputs changed during run')
    r['protocol_sha256']=sha; S.write_new(root/'report.json',S.encoded(r)); print(json.dumps(r,indent=2))


if __name__=='__main__': main()
