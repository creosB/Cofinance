"""Prediction-market calibration lab. EXPERIMENTAL, read-only, no trading, not advice.

Question: when Polymarket priced a resolved Yes/No market at p, how often did YES actually happen?
That measures crowd accuracy, tests the favorite-longshot bias (the old bot's Strategy B bet on 1-3c tails)
and produces the calibration table Cofinance suggestions can cite.

python -m qualify.pm_lab collect  --root data/pm_lab_20261110   # resolved market metadata + frozen sample
python -m qualify.pm_lab history  --root data/pm_lab_20261110   # one price-history request per sampled market
python -m qualify.pm_lab evaluate --root data/pm_lab_20261110
"""
from __future__ import annotations
import argparse
import json
import math
import random
import re
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from core.polymarket import list_markets, parse_market, price_history, resolved_yes
from .historical import digest, dump, lines

FORMAT='cofinance-pm-lab/1'
WINDOW=('2025-11-01T00:00:00+00:00','2026-11-01T00:00:00+00:00')  # actual close time range
MIN_VOLUME=50000
DAYS=100
MAX_OFFSET=2000
SAMPLE=2000
SEED=20261110
HORIZONS={'30d':30,'7d':7,'1d':1}
WINDOWS=((14,0),(31,18))  # days before close: [14d..0] covers 1d/7d, [31d..18d] covers 30d
FRESH_S=86400
FIDELITY_MIN=180
BUCKETS=(0,.02,.05,.10,.20,.30,.40,.50,.60,.70,.80,.90,.95,.98,1.0)
CATEGORIES=(('crypto',r'\b(bitcoin|btc|ethereum|eth|solana|xrp|dogecoin|crypto|bnb|microstrategy)\b'),
    ('macro',r'\b(fed|interest rates?|bps|cpi|inflation|recession|gdp|unemployment|jobs report|powell|tariffs?|treasury)\b'),
    ('politics',r'\b(election|president|presidential|senate|house|governor|prime minister|parliament|party|mayor|nominee|nomination|vote|trump|congress)\b'),
    ('sports',r'\b(nba|nfl|mlb|nhl|uefa|premier league|champions league|la liga|serie a|super bowl|world cup|grand prix|f1|ufc|tennis|atp|wta|vs\.?|beat|match|game \d|series)\b'))
WARNING='Experimental. Read-only historical measurement of crowd prices. Not a trading signal or financial advice.'


def when(value):
    if not value:return None
    s=str(value).strip().replace(' ','T').replace('Z','+00:00')
    if re.search(r'[+-]\d\d$',s):s+=':00'
    try:d=datetime.fromisoformat(s)
    except ValueError:return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def category(text):
    t=(text or '').lower()
    for name,pattern in CATEGORIES:
        if re.search(pattern,t):return name
    return 'other'


def retry(fn,*a,**k):
    for attempt in range(4):
        try:return fn(*a,**k)
        except Exception:
            if attempt==3:raise
            time.sleep(1.5*2**attempt)


def collect(root,lister=list_markets):
    root.mkdir(parents=True,exist_ok=True)
    if (root/'protocol.json').exists():raise ValueError('Frozen sample exists: use a new root')
    lo,hi=when(WINDOW[0]),when(WINDOW[1]);seen=set();eligible=[];counts=defaultdict(int)
    # Gamma pages are 100 rows and refuse offsets past roughly 2,000, so listing a whole year is impossible.
    # Instead: a seeded random sample of end-date days, every qualifying market on each of those days.
    all_days=[datetime.fromtimestamp(lo+86400*i,timezone.utc).date().isoformat() for i in range(int((hi-lo)//86400))]
    days=sorted(random.Random(SEED).sample(all_days,min(DAYS,len(all_days))))
    for day in days:
        offset=0
        while offset<MAX_OFFSET:
            page=retry(lister,closed='true',limit=100,offset=offset,volume_num_min=MIN_VOLUME,
                       end_date_min=day+'T00:00:00Z',end_date_max=day+'T23:59:59Z')
            for raw in page:
                m=parse_market(raw)
                if not m:counts['not_two_outcome']+=1;continue
                if m['id'] in seen:continue
                seen.add(m['id']);anchor=when(m['closed_time']) or when(m['end'])
                if not anchor or not lo<=anchor<hi:counts['outside_window']+=1;continue
                y=resolved_yes(m)
                if y is None:counts['not_cleanly_resolved']+=1;continue
                eligible.append({'id':m['id'],'question':m['question'],'outcome':m['outcomes'][0],'yes_no':m['yes_no'],
                    'event_id':m['event_id'],'event_title':m['event_title'],'yes_token':m['yes_token'],'anchor':anchor,
                    'start':when(m['start']),'volume':m['volume'],'y':int(y),'day':day,
                    'category':category(m['question']+' '+m['event_title'])})
            offset+=len(page);time.sleep(.15)
            if len(page)<100:break
        else:counts['day_truncated_at_offset_cap']+=1
    eligible.sort(key=lambda r:r['id'])
    sample=sorted(random.Random(SEED).sample(eligible,min(SAMPLE,len(eligible))),key=lambda r:r['anchor'])
    (root/'sample.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in sample),encoding='utf-8')
    protocol={'format':FORMAT,'frozen_at':datetime.now(timezone.utc).isoformat(),'window_close_time':WINDOW,
        'min_volume_usd':MIN_VOLUME,'sampled_end_days':days,'sample_size':len(sample),'seed':SEED,'eligible':len(eligible),
        'excluded':dict(counts),'scored_outcome':'first listed outcome of any two-outcome market (Yes, Up, a team...)',
        'horizons_days':HORIZONS,'history_windows_days':WINDOWS,'fresh_seconds':FRESH_S,'fidelity_minutes':FIDELITY_MIN,'buckets':BUCKETS,
        'sample_sha256':digest((root/'sample.jsonl').read_bytes()),
        'notes':['Price history is the recorded market price, not an executable ask; buying costs are understated.',
                 'Horizons are measured back from the actual close time.',
                 'Markets in one event (e.g. one Fed decision) are correlated; event counts are reported.',
                 'Only cleanly settled binary Yes/No markets (final prices 1/0) are included.']}
    dump(root/'protocol.json',protocol);return {k:protocol[k] for k in ('eligible','sample_size','excluded')}


def history_files(root):
    return sorted(root.glob('histories*.jsonl'))


def history(root,fetcher=price_history,pause=.12,shard=0,shards=1):
    """Resumable. With shards>1, worker `shard` takes every shards-th market and writes its own file; markets already
    present in any history file are skipped, so stopping and restarting with a different split is safe."""
    p=json.loads((root/'protocol.json').read_text(encoding='utf-8'))
    if digest((root/'sample.jsonl').read_bytes())!=p['sample_sha256']:raise ValueError('Changed sample')
    if not 0<=shard<shards:raise ValueError('Invalid shard')
    sample=lines(root/'sample.jsonl');ids={m['id'] for m in sample};errors=0
    done={r['id'] for f in history_files(root) for r in lines(f) if r['id'] in ids}
    path=root/('histories.jsonl' if shards==1 else f'histories.{shard}.jsonl')
    status=root/('history_status.json' if shards==1 else f'history_status.{shard}.json')
    with path.open('a',encoding='utf-8') as fh:
        for i,m in enumerate(sample):
            if i%shards!=shard or m['id'] in done:continue
            pts=[];err=None
            for start,end in WINDOWS:  # the CLOB refuses ranges longer than about 14 days
                try:pts+=retry(fetcher,m['yes_token'],int(m['anchor']-start*86400),int(m['anchor']-end*86400),fidelity=FIDELITY_MIN)
                except Exception as exc:err=type(exc).__name__
            if err:errors+=1
            pts=sorted(set(pts))
            fh.write(json.dumps({'id':m['id'],'points':[[t,round(v,4)] for t,v in pts],'error':err})+'\n');fh.flush()
            if i%50<shards:dump(status,{'shard':shard,'shards':shards,'position':i+1,'of':len(sample),'errors':errors})
            time.sleep(pause)
    dump(status,{'shard':shard,'shards':shards,'position':len(sample),'of':len(sample),'errors':errors,'finished':True})


def price_at(points,ts):
    best=None
    for t,v in points:
        if t<=ts:best=(t,v)
        else:break
    return best[1] if best and ts-best[0]<=FRESH_S else None


def wilson(k,n,z=1.96):
    if not n:return (None,None)
    ph=k/n;d=1+z*z/n;c=(ph+z*z/(2*n))/d;h=z*math.sqrt(ph*(1-ph)/n+z*z/(4*n*n))/d
    return (max(0.,c-h),min(1.,c+h))


def bucket_of(p):
    for lo,hi in zip(BUCKETS,BUCKETS[1:]):
        if lo<=p<hi or (hi==1.0 and p==1.0):return f'{lo:.2f}-{hi:.2f}'


def score(rows):
    if not rows:return {'n':0}
    clip=lambda p:min(max(p,1e-4),1-1e-4)
    base=statistics.mean(r['y'] for r in rows)
    brier=statistics.mean((r['p']-r['y'])**2 for r in rows)
    ref=statistics.mean((base-r['y'])**2 for r in rows)
    buckets=defaultdict(list)
    for r in rows:buckets[bucket_of(r['p'])].append(r)
    table=[]
    for name in sorted(buckets):
        g=buckets[name];k=sum(r['y'] for r in g);mp=statistics.mean(r['p'] for r in g);lo,hi=wilson(k,len(g))
        table.append({'bucket':name,'n':len(g),'events':len({r['event_id'] or r['id'] for r in g}),'mean_price':mp,
            'yes_rate':k/len(g),'yes_rate_ci95':[lo,hi],
            'buy_yes_return_per_dollar':(k/len(g))/mp-1 if mp>0 else None,
            'buy_no_return_per_dollar':(1-k/len(g))/(1-mp)-1 if mp<1 else None})
    return {'n':len(rows),'events':len({r['event_id'] or r['id'] for r in rows}),'yes_base_rate':base,'brier':brier,
        'brier_base_rate_reference':ref,'brier_skill':1-brier/ref if ref else None,
        'log_loss':statistics.mean(-(r['y']*math.log(clip(r['p']))+(1-r['y'])*math.log(1-clip(r['p']))) for r in rows),
        'calibration':table}


def evaluate(root):
    p=json.loads((root/'protocol.json').read_text(encoding='utf-8'))
    if digest((root/'sample.jsonl').read_bytes())!=p['sample_sha256']:raise ValueError('Changed sample')
    sample={m['id']:m for m in lines(root/'sample.jsonl')}
    hist={h['id']:h for f in history_files(root) for h in lines(f) if h['id'] in sample}  # rows from any other run are ignored
    mid=statistics.median(m['anchor'] for m in sample.values());out={}
    for name,days in HORIZONS.items():
        rows=[]
        for mid_id,m in sample.items():
            h=hist.get(mid_id)
            if not h:continue  # a failed window just leaves its horizon without a fresh price
            price=price_at(h['points'],m['anchor']-days*86400)
            if price is None:continue
            rows.append({'id':mid_id,'event_id':m['event_id'],'p':price,'y':m['y'],'category':m['category'],
                         'half':'first' if m['anchor']<mid else 'second'})
        cats=sorted({r['category'] for r in rows})
        out[name]={'all':score(rows),'by_category':{c:score([r for r in rows if r['category']==c]) for c in cats},
            'by_half':{h:score([r for r in rows if r['half']==h]) for h in ('first','second')}}
    coverage={'sampled':len(sample),'history_errors':sum(1 for h in hist.values() if h.get('error')),
              'missing_history':len(set(sample)-set(hist))}
    report={'format':FORMAT,'warning':WARNING,'protocol_sha256':digest((root/'protocol.json').read_bytes()),
        'coverage':coverage,'horizons':out,'notes':p['notes']}
    dump(root/'report.json',report);return report


def table(report):
    out=[f"coverage {report['coverage']}"]
    for name,h in report['horizons'].items():
        a=h['all']
        if not a['n']:out.append(f'{name}: no rows');continue
        out.append(f"\n{name}: n={a['n']} events={a['events']} brier={a['brier']:.4f} skill={a['brier_skill']:.3f} base_yes={a['yes_base_rate']:.3f}")
        for b in a['calibration']:
            out.append(f"  {b['bucket']:>10} n={b['n']:4d} price={b['mean_price']:.3f} yes={b['yes_rate']:.3f} "
                       f"ci=[{b['yes_rate_ci95'][0]:.3f},{b['yes_rate_ci95'][1]:.3f}] buyYES={b['buy_yes_return_per_dollar'] if b['buy_yes_return_per_dollar'] is None else round(b['buy_yes_return_per_dollar'],3)}")
        out.append('  by category: '+', '.join(f"{c} n={s['n']} brier={s['brier']:.3f}" for c,s in h['by_category'].items() if s['n']))
        out.append('  by half: '+', '.join(f"{k} n={s['n']} brier={s.get('brier',0):.3f}" for k,s in h['by_half'].items() if s['n']))
    return '\n'.join(out)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['collect','history','evaluate'])
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--shard',type=int,default=0)
    ap.add_argument('--shards',type=int,default=1);a=ap.parse_args()
    if a.action=='collect':print(json.dumps(collect(a.root),indent=2))
    elif a.action=='history':
        history(a.root,shard=a.shard,shards=a.shards)
        name='history_status.json' if a.shards==1 else f'history_status.{a.shard}.json'
        print((a.root/name).read_text())
    else:print(table(evaluate(a.root)))
if __name__=='__main__':main()
