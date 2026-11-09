"""Historical strategy lab. EXPERIMENTAL; not a trading system or financial advice.

Same target as the historical benchmark (next open to fifth-session close, every fifth SPY session), but on long
history with time splits so improvements can be real:
  dev         entry 2015..2023  tune freely (rules scored on 2018+, models walk-forward by year)
  validation  entry 2024-01-01..2025-04-30  check selections, keep peeks rare
  test        entry 2025-05-01..2026-11-06  locked: only `run --final`, every peek appended to test_peeks.jsonl
Rows that straddle a split boundary are dropped (embargo). Models only ever train on rows that exited
before the first row they predict.

python -m qualify.lab collect --root data/lab_20261109
python -m qualify.lab build   --root data/lab_20261109
python -m qualify.lab run     --root data/lab_20261109 [--final]
"""
from __future__ import annotations
import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from .historical import ASSETS, END, bars, digest, dump, feature, lines, write_lines

FORMAT='cofinance-lab/1'
FETCH_START=datetime(2014,9,1,tzinfo=timezone.utc)
DEV_END='2024-01-01'
TEST_START='2025-05-01'
DEV_SCORED='2018-01-01'
FEE=.001
GATE={'balanced_accuracy_min':.55,'beat_best_baseline_by':.03,'p_value_max':.05,'must_pass':['validation','test']}
BASELINES=('always_up','momentum20')
FEATS=('ret1','ret5','ret20','ret60','vol20','dd60','ma20_gap','ma50_gap','spy_ret5','spy_ret20','spy_vol20','is_crypto')
RULES={
    'always_up':lambda x:'UP',
    'momentum5':lambda x:'UP' if x['ret5']>=0 else 'DOWN',
    'momentum20':lambda x:'UP' if x['ret20']>=0 else 'DOWN',
    'momentum60':lambda x:'UP' if x['ret60']>=0 else 'DOWN',
    'reversal5':lambda x:'DOWN' if x['ret5']>=0 else 'UP',
    'trend_ma50':lambda x:'UP' if x['ma50_gap']>=0 else 'DOWN',
    'trend_ma50_long_cash':lambda x:'UP' if x['ma50_gap']>=0 else 'ABSTAIN',
    'dual_momentum':lambda x:'UP' if x['ret20']>0 and x['ret60']>0 else 'DOWN' if x['ret20']<0 and x['ret60']<0 else 'ABSTAIN',
    'market_trend':lambda x:'UP' if x['spy_ret20']>=0 else 'DOWN',
}
MODELS=('logreg','gbm')


def collect(root):
    from core.market import fetch_json
    root.mkdir(parents=True,exist_ok=True)
    start=int(FETCH_START.timestamp());end=int(datetime.combine(END,datetime.min.time(),timezone.utc).timestamp())
    for s in (*ASSETS,'SPY'):
        path=root/(s+'.json')
        if path.exists():continue
        raw=fetch_json(f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(s)}?period1={start}&period2={end}&interval=1d')
        if not (raw.get('chart') or {}).get('result'):raise ValueError('Missing source result: '+s)
        path.write_text(json.dumps(raw),encoding='utf-8')
    receipt={'downloaded_at':datetime.now(timezone.utc).isoformat(),
        'sources':{s:digest((root/(s+'.json')).read_bytes()) for s in (*ASSETS,'SPY')}}
    dump(root/'collection.json',receipt);return receipt


def split_of(entry,exit_):
    if exit_<DEV_END:return 'dev'
    if entry>=DEV_END and exit_<TEST_START:return 'validation'
    if entry>=TEST_START:return 'test'
    return None  # straddles a boundary: embargoed


def extras(past,spy):
    f=feature(past);g=feature(spy);closes=[b['close'] for b in past];c=closes[-1]
    return {'ret1':f['past_return_1_session_pct'],'ret5':f['past_return_5_sessions_pct'],'ret20':f['past_return_20_sessions_pct'],
        'ret60':f['past_return_60_sessions_pct'],'vol20':f['past_20_session_daily_volatility_pct'],
        'dd60':f['drawdown_from_past_60_session_high_pct'],
        'ma20_gap':round(100*(c/statistics.mean(closes[-20:])-1),4),'ma50_gap':round(100*(c/statistics.mean(closes[-50:])-1),4),
        'spy_ret5':g['past_return_5_sessions_pct'],'spy_ret20':g['past_return_20_sessions_pct'],
        'spy_vol20':g['past_20_session_daily_volatility_pct']}


def build(root):
    if (root/'cases.jsonl').exists():raise ValueError('Cases already built: use a new root')
    series={s:bars(json.loads((root/(s+'.json')).read_text(encoding='utf-8'))) for s in (*ASSETS,'SPY')}
    cal=sorted(series['SPY']);rows=[];dropped=defaultdict(int)
    for i in range(60,len(cal)-5,5):
        origin,entry,exit_=cal[i],cal[i+1],cal[i+5];dates=cal[i-60:i+1]
        split=split_of(entry,exit_)
        if not split:dropped['embargo']+=1;continue
        spy=[series['SPY'][d] for d in dates]
        for a in ASSETS:
            if any(d not in series[a] for d in dates+[entry,exit_]):dropped['missing_'+a]+=1;continue
            x=extras([series[a][d] for d in dates],spy);x['is_crypto']=int(a.endswith('-USD'))
            rows.append({'id':f'{a}:{origin}','symbol':a,'origin':origin,'entry_date':entry,'exit_date':exit_,'split':split,'x':x,
                         'return':series[a][exit_]['close']/series[a][entry]['open']-1})
    write_lines(root/'cases.jsonl',rows)
    manifest={'format':FORMAT,'cases_sha256':digest((root/'cases.jsonl').read_bytes()),'n':len(rows),
        'by_split':{s:sum(r['split']==s for r in rows) for s in ('dev','validation','test')},'dropped':dict(dropped),
        'splits':{'dev_exit_before':DEV_END,'validation':[DEV_END,TEST_START],'test_entry_from':TEST_START,'dev_scored_from':DEV_SCORED},
        'gate':GATE,'first_origin':rows[0]['origin'],'last_exit':max(r['exit_date'] for r in rows)}
    dump(root/'manifest.json',manifest);return manifest


def folds(rows,final=False):
    """(train, target) pairs. Train rows always exit before the earliest target entry."""
    dev=[r for r in rows if r['split']=='dev'];out=[]
    for year in range(int(DEV_SCORED[:4]),int(DEV_END[:4])):
        cut=f'{year}-01-01';target=[r for r in dev if r['entry_date'][:4]==str(year)]
        out.append(([r for r in dev if r['exit_date']<cut],target))
    out.append((dev,[r for r in rows if r['split']=='validation']))
    if final:out.append(([r for r in rows if r['split'] in ('dev','validation')],[r for r in rows if r['split']=='test']))
    return out


def fit_predict(kind,train,target):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if not target:return {}
    X=np.array([[r['x'][k] for k in FEATS] for r in train],dtype=float);y=np.array([r['return']>0 for r in train])
    model=(make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=2000)) if kind=='logreg' else
           HistGradientBoostingClassifier(max_depth=3,max_iter=150,learning_rate=.05,random_state=0))
    model.fit(X,y)
    p=model.predict_proba(np.array([[r['x'][k] for k in FEATS] for r in target],dtype=float))[:,1]
    return {r['id']:'UP' if v>=.5 else 'DOWN' for r,v in zip(target,p)}


def metrics(rows,labels):
    from scipy.stats import binomtest
    if not rows:return {'n':0}
    lab=lambda r:labels.get(r['id'],'ERROR')
    truth=lambda r:'UP' if r['return']>0 else 'DOWN' if r['return']<0 else 'FLAT'
    covered=[r for r in rows if lab(r) in ('UP','DOWN')];hits=sum(lab(r)==truth(r) for r in covered);recalls=[]
    for side in ('UP','DOWN'):
        group=[r for r in rows if truth(r)==side]
        if group:recalls.append(sum(lab(r)==side for r in group)/len(group))
    by=defaultdict(list)
    for r in rows:by[r['origin']].append((1-FEE)*(1+r['return'])*(1-FEE)-1 if lab(r)=='UP' else 0.)
    wealth=peak=1.;dd=0.
    for o in sorted(by):wealth*=1+statistics.mean(by[o]);peak=max(peak,wealth);dd=max(dd,1-wealth/peak)
    return {'n':len(rows),'accuracy_all':hits/len(rows),'coverage':len(covered)/len(rows),
        'accuracy_covered':hits/len(covered) if covered else None,
        'balanced_accuracy':statistics.mean(recalls) if len(recalls)==2 else None,
        'p_value_covered_gt_50':binomtest(hits,len(covered),.5,alternative='greater').pvalue if covered else None,
        'up_rate':sum(truth(r)=='UP' for r in rows)/len(rows),
        'illustrative_long_cash_net_return':wealth-1,'illustrative_weekly_max_drawdown':dd}


def vs_baseline(arms):
    """One-sided binomial p-value of all-case hits against the best baseline's accuracy (markets drift up,
    so beating 50% proves nothing)."""
    from scipy.stats import binomtest
    best=max(arms[b]['accuracy_all'] for b in BASELINES)
    for m in arms.values():
        hits=round(m['accuracy_all']*m['n'])
        m['p_value_vs_best_baseline']=binomtest(hits,m['n'],min(best,.999),alternative='greater').pvalue if m['n'] else None
    return best


def gate(split_arms,arm):
    m=split_arms[arm];best=max(split_arms[b]['accuracy_all'] for b in BASELINES)
    return bool(m['n'] and (m['balanced_accuracy'] or 0)>=GATE['balanced_accuracy_min'] and
        m['accuracy_all']>=best+GATE['beat_best_baseline_by'] and (m['p_value_vs_best_baseline'] or 1)<=GATE['p_value_max'])


def run(root,final=False):
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if digest((root/'cases.jsonl').read_bytes())!=manifest['cases_sha256']:raise ValueError('Changed cases')
    rows=lines(root/'cases.jsonl')
    labels={name:{r['id']:rule(r['x']) for r in rows} for name,rule in RULES.items()}
    for kind in MODELS:
        labels[kind]={}
        for train,target in folds(rows,final):labels[kind].update(fit_predict(kind,train,target))
    splits={'dev_2018_2023':[r for r in rows if r['split']=='dev' and r['entry_date']>=DEV_SCORED],
            'validation':[r for r in rows if r['split']=='validation']}
    if final:splits['test']=[r for r in rows if r['split']=='test']
    report={'format':FORMAT,'warning':'Experimental lab result. Not a trading signal or financial advice.',
        'cases_sha256':manifest['cases_sha256'],'code_sha256':digest(Path(__file__).read_bytes()),'gate':GATE,'splits':{}}
    for name,subset in splits.items():
        arms={a:metrics(subset,labels[a]) for a in labels};vs_baseline(arms)
        report['splits'][name]={'from':min(r['entry_date'] for r in subset),'through':max(r['exit_date'] for r in subset),
            'arms':arms,'gate_pass':{a:gate(arms,a) for a in arms if a not in BASELINES}}
    if final:
        report['promotable']=[a for a in labels if a not in BASELINES and all(report['splits'][s]['gate_pass'][a] for s in GATE['must_pass'])]
        with (root/'test_peeks.jsonl').open('a',encoding='utf-8') as fh:
            fh.write(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'code_sha256':report['code_sha256'],
                'arms':sorted(labels),'promotable':report['promotable']})+'\n')
        report['test_peeks']=len(lines(root/'test_peeks.jsonl'))
    dump(root/('report_final.json' if final else 'report.json'),report);return report


def table(report):
    out=[]
    for name,s in report['splits'].items():
        out.append(f"\n{name} {s['from']}..{s['through']} n={next(iter(s['arms'].values()))['n']}")
        for a,m in sorted(s['arms'].items(),key=lambda kv:-(kv[1]['balanced_accuracy'] or 0)):
            out.append(f"  {a:22s} acc {m['accuracy_all']:.3f} bal {m['balanced_accuracy'] or 0:.3f} cov {m['coverage']:.2f} "
                       f"p_vs_base {m['p_value_vs_best_baseline'] if m['p_value_vs_best_baseline'] is not None else 1:.3f} "
                       f"ret {m['illustrative_long_cash_net_return']:+.1%} dd {m['illustrative_weekly_max_drawdown']:.1%}"
                       f"{'  GATE' if s['gate_pass'].get(a) else ''}")
    return '\n'.join(out)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['collect','build','run'])
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--final',action='store_true');a=ap.parse_args()
    if a.action=='collect':print(json.dumps(collect(a.root),indent=2))
    elif a.action=='build':print(json.dumps(build(a.root),indent=2))
    else:
        rep=run(a.root,a.final);print(table(rep))
        if a.final:print('promotable:',rep['promotable'],'test peeks:',rep['test_peeks'])
if __name__=='__main__':main()
