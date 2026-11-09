"""Retrospective, price-only forecast experiment. NOT the production research pipeline or a trading policy.
Prepare freezes a protocol and separates model inputs from outcomes. Predict reads inputs only; evaluate is
separate and refuses a changed feature/protocol hash. No fitting or threshold selection occurs here.

python -m qualify.historical prepare --root data/history_benchmark_20261109
python -m qualify.historical evaluate --root data/history_benchmark_20261109
"""
from __future__ import annotations
import argparse
import calendar
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ASSETS=('AAPL','NVDA','MSFT','BTC-USD','ETH-USD')
ANCHOR=date(2026,11,9)
END=date(2026,11,7)  # exclusive; last completed US session is Nov 6
SYSTEM=('You are a benchmark-only directional forecaster. This is anonymized historical price data, not a live asset. '
        'Using only the supplied past observations, predict whether the exit close will be above or below the next '
        'session opening price, five US-market sessions after the observation. Reply with exactly UP, DOWN, or ABSTAIN. '
        'Do not give explanations, numbers or hidden reasoning.')


def digest(raw):return hashlib.sha256(raw).hexdigest()
def dump(path,obj):path.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def lines(path):return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
def write_lines(path,rows):path.write_text(''.join(json.dumps(r,allow_nan=False)+'\n' for r in rows),encoding='utf-8')
def months_before(d,n):
    y,m=divmod(d.year*12+d.month-1-n,12);m+=1
    return date(y,m,min(d.day,calendar.monthrange(y,m)[1]))
def finite(x):return isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x)


def bars(raw):
    r=raw['chart']['result'][0]; q=r['indicators']['quote'][0]
    out={}
    for i,t in enumerate(r.get('timestamp',[])):
        d=datetime.fromtimestamp(t,timezone.utc).date()
        if d>=END:continue  # provider may return a bar beyond the requested end
        values={k:(q.get(k) or [])[i] for k in ('open','high','low','close')}
        if not all(finite(v) and v>0 for v in values.values()):continue
        out[d.isoformat()]={**values,'timestamp':t}
    return out


def feature(past):
    if len(past)<61:raise ValueError('Need 61 completed observations')
    closes=[b['close'] for b in past]
    c=closes[-1]
    ret=lambda n:round(100*(c/closes[-n-1]-1),4)
    logret=[math.log(b/a) for a,b in zip(closes[-21:],closes[-20:])]
    return {'past_return_1_session_pct':ret(1),'past_return_5_sessions_pct':ret(5),
            'past_return_20_sessions_pct':ret(20),'past_return_60_sessions_pct':ret(60),
            'past_20_session_daily_volatility_pct':round(statistics.stdev(logret)*100,4),
            'drawdown_from_past_60_session_high_pct':round((1-c/max(b['high'] for b in past[-60:]))*100,4),
            'past_20_closes_rebased_to_100':[round(v/closes[-20]*100,3) for v in closes[-20:]]}


def prepare(root):
    if (root/'protocol.json').exists():raise ValueError('Frozen protocol exists. Use a new directory, never overwrite it.')
    series={a:bars(json.loads((root/(a+'.json')).read_text())) for a in (*ASSETS,'SPY')}
    cal=sorted(series['SPY'])
    eligible=[i for i,d in enumerate(cal) if date.fromisoformat(d)>=months_before(ANCHOR,6) and i>=60 and i+5<len(cal)]
    if not eligible:raise ValueError('No complete historical windows')
    features,outcomes=[],[]
    missing=[]
    for i in range(eligible[0],eligible[-1]+1,5):
        if i+5>=len(cal):break
        origin,entry,exit_=cal[i],cal[i+1],cal[i+5]
        as_of=datetime.combine(date.fromisoformat(origin)+timedelta(days=1),datetime.min.time(),timezone.utc).timestamp()
        for a in ASSETS:
            dates=cal[:i+1]
            if any(d not in series[a] for d in dates[-61:]+[entry,exit_]):
                missing.append({'symbol':a,'origin':origin,'reason':'missing required bar'});continue
            past=[series[a][d] for d in dates[-61:]]
            assert max(b['timestamp'] for b in past)<as_of
            enter,leave=series[a][entry],series[a][exit_]
            assert enter['timestamp']>=as_of  # daily BTC opens at 00 UTC; shares open later
            f=feature(past); id_=f'case{len(features)+1:04d}'
            features.append({'id':id_,'features':f})
            outcomes.append({'id':id_,'symbol':a,'origin':origin,'as_of_utc':datetime.fromtimestamp(as_of,timezone.utc).isoformat(),
                'entry_date':entry,'exit_date':exit_,'entry_open':enter['open'],'exit_close':leave['close'],
                'return':leave['close']/enter['open']-1,'momentum20':'UP' if f['past_return_20_sessions_pct']>=0 else 'DOWN'})
    write_lines(root/'features.jsonl',features);write_lines(root/'outcomes.jsonl',outcomes)
    protocol={'format':'cofinance-historical/1','frozen_at':datetime.now(timezone.utc).isoformat(),
        'assets':ASSETS,'anchor':ANCHOR.isoformat(),'cutoff_exclusive':END.isoformat(),
        'horizon':'next session open through fifth following US-market session close',
        'sampling':'every fifth SPY session, no overlapping holdings; crypto aligned to the US session calendar',
        'model_scope':'LFM3 benchmark-only forecast prompt, NOT the production research pipeline',
        'inputs':'completed past OHLC-derived features only; no asset/date names, future rows, current fundamentals, news or memory',
        'decoding':{'temperature':0,'thinking':False,'max_tokens':16},'system':SYSTEM,
        'features_sha256':digest((root/'features.jsonl').read_bytes()),'outcomes_sha256':digest((root/'outcomes.jsonl').read_bytes()),
        'source_sha256':{a:digest((root/(a+'.json')).read_bytes()) for a in (*ASSETS,'SPY')},
        'n':len(features),'excluded':missing,'fee_bps_per_side':10,
        'limitations':['Retrospective data downloaded now, possibly revised or split-adjusted, not an immutable point-in-time feed.',
            'Unknown model training cutoff: anonymization reduces, but does not prove absence of memorization.',
            'Handpicked fixed universe, survivorship/selection bias; small dependent panel, no statistical significance claim.',
            'Price-only experimental forecasts cannot validate the full news/fundamental research product.',
            'No price targets or probability forecasts: direction accuracy is not price-error or calibration accuracy.',
            'No optimization or future holdout tuning. Nested 1/3/6-month windows are not independent tests.']}
    dump(root/'protocol.json',protocol)
    return {'cases':len(features),'first':outcomes[0]['origin'],'last':outcomes[-1]['origin'],'excluded':missing}


def predict(root,call,model_meta):
    # Deliberately never opens outcomes or raw price files.
    raw=(root/'features.jsonl').read_bytes();pr=(root/'protocol.json').read_bytes();p=json.loads(pr)
    if digest(raw)!=p['features_sha256']:raise ValueError('Changed frozen features')
    if p['system']!=SYSTEM:raise ValueError('Changed prediction protocol')
    path=root/'predictions.jsonl'
    if path.exists():raise ValueError('Predictions already exist; no silent rerun or cherry-picking')
    dump(root/'prediction_receipt.json',{'features_sha256':digest(raw),'protocol_sha256':digest(pr),
         'started_at':datetime.now(timezone.utc).isoformat(),'model':model_meta})
    with path.open('x',encoding='utf-8') as f:
        for row in [json.loads(l) for l in raw.decode().splitlines()]:
            try:
                text=call(SYSTEM,json.dumps(row['features'],sort_keys=True),max_tokens=16)
                label=text.strip().upper()
                error=None if label in {'UP','DOWN','ABSTAIN'} else 'invalid_label'
            except Exception as exc:text='';label='ERROR';error=type(exc).__name__
            f.write(json.dumps({'id':row['id'],'label':label if not error else 'ERROR','error':error,'output':text[:200]})+'\n');f.flush()
    dump(root/'prediction_seal.json',{'predictions_sha256':digest(path.read_bytes()),'n':len(lines(path)),
         'finished_at':datetime.now(timezone.utc).isoformat()})


def metrics(rows,predictions,arm):
    labels={r['id']:(predictions[r['id']]['label'] if arm=='lfm3' else 'UP' if arm=='always_up' else r['momentum20']) for r in rows}
    hit=lambda r:labels[r['id']]==('UP' if r['return']>0 else 'DOWN' if r['return']<0 else 'FLAT')
    covered=[r for r in rows if labels[r['id']] in {'UP','DOWN'}]
    acc=sum(hit(r) for r in rows)/len(rows) if rows else None
    per_side=[]
    for side,cond in [('UP',lambda r:r['return']>0),('DOWN',lambda r:r['return']<0)]:
        group=[r for r in rows if cond(r)]
        per_side.append(sum(hit(r) for r in group)/len(group) if group else None)
    by_origin=defaultdict(list)
    fee=.001
    for r in rows:
        # Illustrative weekly long/cash, cash for DOWN/ABSTAIN/error; NOT shorts, no execution claim.
        ret=(1-fee)*(1+r['return'])*(1-fee)-1 if labels[r['id']]=='UP' else 0
        by_origin[r['origin']].append(ret)
    wealth,peak,dd=1.,1.,0.
    for origin in sorted(by_origin):
        wealth*=1+statistics.mean(by_origin[origin]);peak=max(peak,wealth);dd=max(dd,1-wealth/peak)
    return {'n':len(rows),'directional_accuracy_all':acc,'coverage':len(covered)/len(rows) if rows else None,
            'directional_accuracy_covered':sum(hit(r) for r in covered)/len(covered) if covered else None,
            'balanced_accuracy':statistics.mean(per_side) if all(v is not None for v in per_side) else None,
            'errors':sum(labels[r['id']]=='ERROR' for r in rows),
            'abstentions':sum(labels[r['id']]=='ABSTAIN' for r in rows),
            'illustrative_long_cash_net_return':wealth-1,'illustrative_weekly_max_drawdown':dd,
            'up_actual':sum(r['return']>0 for r in rows),'down_actual':sum(r['return']<0 for r in rows)}


def evaluate(root):
    p=json.loads((root/'protocol.json').read_text());receipt=json.loads((root/'prediction_receipt.json').read_text())
    seal=json.loads((root/'prediction_seal.json').read_text())
    for path,key in [('features.jsonl','features_sha256'),('outcomes.jsonl','outcomes_sha256')]:
        if digest((root/path).read_bytes())!=p[key]:raise ValueError('Changed frozen '+path)
    if receipt['protocol_sha256']!=digest((root/'protocol.json').read_bytes()):raise ValueError('Changed protocol')
    if seal['predictions_sha256']!=digest((root/'predictions.jsonl').read_bytes()):raise ValueError('Changed predictions')
    outcomes=lines(root/'outcomes.jsonl');preds=lines(root/'predictions.jsonl');by_id={r['id']:r for r in preds}
    if len(by_id)!=len(preds) or set(by_id)!={r['id'] for r in outcomes}:raise ValueError('Missing or duplicate forecasts')
    windows={}
    for months in (1,3,6):
        cut=months_before(ANCHOR,months).isoformat()
        rows=[r for r in outcomes if r['entry_date']>=cut]
        windows[str(months)]={'from':cut,'through':max(r['exit_date'] for r in rows) if rows else None,
            'arms':{a:metrics(rows,by_id,a) for a in ('lfm3','always_up','momentum20')},
            'by_asset':{a:metrics([r for r in rows if r['symbol']==a],by_id,'lfm3') for a in ASSETS}}
    report={'protocol_sha256':digest((root/'protocol.json').read_bytes()),'prediction_seal':seal,
            'model':receipt['model'],'scope':p['model_scope'],'windows_months':windows,'limitations':p['limitations'],
            'decision':'Exploratory result only. No live model, prompt, trading or selection change authorized by this score.'}
    dump(root/'report.json',report);return report


def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','evaluate']);ap.add_argument('--root',type=Path,required=True)
    a=ap.parse_args();print(json.dumps(prepare(a.root) if a.action=='prepare' else evaluate(a.root),indent=2))
if __name__=='__main__':main()
