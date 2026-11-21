"""Full-packet counterevidence retrieval screen. Research only; nothing here issues a verdict."""
import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

from qualify import source_selection as F, source_selection_mask as M

P, S = F.P, F.S
FIXTURE = Path('qualify/counterevidence_cases.json')
ARMS = ('seed', 'direct', 'second', 'all')
KINDS = ('restriction', 'retraction', 'conflict')
FOCUS = ('You are the second reader in an evidence search. Examine the complete packet, not just the starting passages. '
         'Select ONLY additional material passages that narrow, withdraw, or contradict an answer to the exact question. '
         'Resolve what a later or earlier passage refers to even when the subject is not repeated. Check the territory, '
         'population, time and event. Similar language about another thing is not a qualification. The starting passages '
         'may be wrong or incomplete; do not select a passage merely because it is nearby. If there is no relevant '
         'counterevidence, select nothing. Do not give a verdict.' + M.APPEND)
GATE = ('Exactly 20 packets: nine matched caveat/control pairs (three families per kind) and two no-evidence controls. '
        'Three positive packets carry two distinct caveats; rest carry one. At least six critical passages omit the named '
        'subject. All invalid responses remain in denominators; more than one invalid in either model arm or any invalid '
        'deterministic row = INCONCLUSIVE. Advance ONLY to independent retest if second finds more critical facets than '
        'both direct and seed, is no worse than direct on each kind, gains >=2 unnamed caveats over seed, exact >= direct, '
        'micro precision >=0.75, extra spans <= direct+1 and at most one control packet with any extra span. Otherwise '
        'NO_PROMOTION. One model call per model arm, same pinned model, cap and boolean format; no retries. All-span '
        'arm quantifies indiscriminate retrieval. No integration, automatic verdict or source filtering.')


def fingerprint():
    files = set(S.fingerprint()) | {str(Path(m.__file__).resolve().relative_to(Path.cwd()).as_posix()) for m in (F, M, F.C)} | {'qualify/counterevidence.py'}
    return {f: P.digest(Path(f).read_bytes().replace(b'\r\n', b'\n')) for f in sorted(files)}


def protocol():
    return dict(files=fingerprint(), gate=GATE, arms=list(ARMS), model=S.MODEL, cap=M.CAP,
                direct=F.SYSTEM+M.APPEND, second=FOCUS, seed=41917, interval='Paired family bootstrap; 2000 draws, seed 153. Descriptive only.')


def check_cases(cases):
    """Keys refer to disjoint original spans; labels never enter either prompt."""
    if len(cases) != 20 or len({c['id'] for c in cases}) != 20:
        raise ValueError('Expected 20 unique packets')
    groups = {}; sizes = []; unnamed = 0; nil = 0
    for c in cases:
        q = F.view(c); docs = q['documents']; spans = F.C.index(docs); lookup = {s['id']:s for s in spans}
        if len({d['id'] for d in docs}) != len(docs) or not all(d['title'] and d['body'] for d in docs):
            raise ValueError('Missing exhibit')
        F.body(q, spans)
        facets = c['facets']; crit = c['counter_facets']; flat = [i for f in facets for i in f]
        if (any(not f for f in facets) or len(flat) != len(set(flat)) or not set(flat) <= lookup.keys()
                or sorted(set(crit)) != crit or any(not 0 <= i < len(facets) for i in crit)):
            raise ValueError('Unknown, duplicate or ambiguous key')
        if c['kind'] == 'empty':
            if facets or crit: raise ValueError('No-evidence control must have an empty key')
            nil += 1; continue
        if c['kind'] not in KINDS or len(facets)-len(crit) != 1: raise ValueError('Expected a primary facet')
        groups.setdefault((c['kind'],c['family']),[]).append(bool(crit))
        if crit: sizes.append(len(crit))
        unnamed += sum(all(c['subject'].casefold() not in lookup[j]['text'].casefold() for j in facets[i]) for i in crit)
    if (nil != 2 or Counter(k for k,f in groups) != dict.fromkeys(KINDS,3) or any(sorted(v) != [False,True] for v in groups.values())
            or sorted(sizes) != [1]*6+[2]*3 or unnamed < 6): raise ValueError('Fixture does not meet the frozen design')


def envelope(q, spans, arm, seeds):
    if arm not in ('second','direct'): raise ValueError('Not a model arm')
    # F.body checks the exact full index and forbids hidden labels/unknown highlights.
    text = F.body(q, spans, seeds if arm == 'second' else None)
    return (FOCUS if arm == 'second' else F.SYSTEM+M.APPEND), text


def combine(seeds, picks, available):
    if not F.valid(seeds,available) or not F.valid(picks,available): raise ValueError('Invalid suggestions')
    return sorted(set(seeds) | set(picks))


def gate(s):
    a,d,b = (s['all'][k] for k in ('second','direct','seed'))
    if any(s['all'][k]['n'] != 20 or 20-s['all'][k]['valid'] > int(k in ('second','direct')) for k in ARMS): return 'INCONCLUSIVE'
    passed = (a['counter_hits'] > max(d['counter_hits'], b['counter_hits']) and a['unnamed_hits'] >= b['unnamed_hits']+2
              and a['exact'] >= d['exact'] and 4*a['tp'] >= 3*(a['tp']+a['fp']) and a['tp'] > 0 and a['fp'] <= d['fp']+1
              and a['noisy_controls'] <= 1 and all(s[z]['second']['counter_hits'] >= s[z]['direct']['counter_hits'] for z in KINDS))
    return 'INDEPENDENT_RETEST_ONLY' if passed else 'NO_PROMOTION'


def score(c, x):
    spans = {v['id']:v for v in F.C.index(F.view(c)['documents'])}; ids=x['ids']; bad=x['error'] is not None
    g=F.grade(ids,set(spans),c['facets'],c['counter_facets'],bad); chosen=set(ids or []) if g['valid'] else set()
    unnamed=[c['facets'][i] for i in c['counter_facets'] if all(c['subject'].casefold() not in spans[k]['text'].casefold() for k in c['facets'][i])]
    return g | dict(case=c['id'],family=c['family'],split=c['kind'],arm=x['arm'],input_tokens=x['input_tokens'],output_tokens=x['output_tokens'],
                    counter_hits=g['counter_total']-g['counter_missed'],unnamed_total=len(unnamed),unnamed_hits=sum(bool(set(f)&chosen) for f in unnamed),
                    noisy_controls=int(not c['counter_facets'] and g['fp']>0),extra_count=g['fp'])


def aggregate(rows):
    return F.agg(rows) | {k:sum(r[k] for r in rows) for k in ('counter_hits','unnamed_total','unnamed_hits','noisy_controls')}


def paired(rows):
    a={r['case']:r for r in rows if r['arm']=='second'}; d={r['case']:r for r in rows if r['arm']=='direct'}
    keys=sorted({v['family'] for v in a.values()}); clusters=[]
    for f in keys:
        cs=[i for i in a if a[i]['family']==f]; clusters.append((sum(a[i]['counter_hits']-d[i]['counter_hits'] for i in cs),sum(a[i]['counter_total'] for i in cs),sum(int(a[i]['exact'])-int(d[i]['exact']) for i in cs),len(cs)))
    rng=random.Random(153); recall=[]; exact=[]
    for _ in range(2000):
        h,n,e,m=map(sum,zip(*rng.choices(clusters,k=len(clusters))));exact.append(e/m)
        if n:recall.append(h/n)
    def limits(v):
        v=sorted(v);return [round(v[int((len(v)-1)*p)],3) for p in (.025,.975)]
    return dict(families=len(keys),recall_delta_interval=limits(recall),exact_delta_interval=limits(exact))


def diagnose(rows):
    s={z:{a:aggregate([r for r in rows if r['arm']==a and (z=='all' or r['split']==z)]) for a in ARMS} for z in ('all',*KINDS,'empty')}
    return dict(experimental=True,decision=gate(s),results=s,paired=paired(rows),scope='Fresh authored paired screen, not independent field evidence, answer quality or forecasting skill.',production_change=False)


def verify_plan(root):
    if F.read(root/'protocol.json') != protocol(): raise ValueError('Protocol drift; do not open the frozen run')


def seal(root):
    verify_plan(root);cases=F.read(FIXTURE);check_cases(cases);P.write(root/'cases.json',cases);P.write(root/'seal.json',{'cases':P.digest(P.enc(cases)),'protocol':P.digest(P.enc(protocol()))})


def run(root):
    verify_plan(root);cases=F.read(root/'cases.json');binding=F.read(root/'seal.json');check_cases(cases)
    if binding != {'cases':P.digest(P.enc(cases)),'protocol':P.digest(P.enc(protocol()))}:raise ValueError('Cases/keys or protocol changed after sealing')
    P.write(root/'started.json',binding);rng=random.Random(41917);rng.shuffle(cases);modelroot=Path('data/local_models');alias='counter-reader';out=[]
    with S.T.narrator(modelroot,root/'server.log',alias=alias,model_id=S.MODEL,backend=S.T.default_backend(modelroot)) as (local,meta):
        P.write(root/'runtime.json',meta)
        for c in cases:
            q=F.view(c);sp=F.C.index(q['documents']);available=[x['id'] for x in sp];seeds=F.rule(q,sp);arms=list(ARMS);rng.shuffle(arms)
            for arm in arms:
                row=dict(case=c['id'],arm=arm,ids=None,raw=None,error=None,finish=None,input_tokens=0,output_tokens=0);start=time.monotonic()
                try:
                    if arm=='all':row['ids']=available
                    elif arm=='seed':row['ids']=seeds
                    else:
                        system,body=envelope(q,sp,arm,seeds);req=S.T.request_body(alias,system,body,max_tokens=M.CAP,response_format={'type':'json_schema','json_schema':{'name':'span_switches','strict':True,'schema':M.schema(available)}})
                        z=local.post(req);ch=z['choices'][0];row.update(raw=ch['message']['content'],finish=ch['finish_reason'],input_tokens=z['usage']['prompt_tokens'],output_tokens=z['usage']['completion_tokens'])
                        if row['finish'] != 'stop' or row['output_tokens']>M.CAP:raise ValueError('Incomplete or over-budget response')
                        picks=M.decode(row['raw'],available);row['ids']=combine(seeds,picks,available) if arm=='second' else picks
                except Exception as e:row['error']=type(e).__name__+': '+str(e)[:180]
                row['seconds']=round(time.monotonic()-start,4);out.append(row)
            print('completed',len(out)//4,'of',len(cases),flush=True)
    P.write(root/'outputs.json',out);cs={c['id']:c for c in cases};rows=[score(cs[x['case']],x) for x in out];P.write(root/'scores.json',rows)
    r=diagnose(rows)|binding;r['output_sha256']=P.digest(P.enc(out));P.write(root/'report.json',r);print(r['decision'],r['results']['all'])


if __name__=='__main__':
    sys.addaudithook(F.local_only);p=argparse.ArgumentParser();p.add_argument('action',choices=('freeze','seal','run'));p.add_argument('root',type=Path);args=p.parse_args()
    if args.action=='freeze':args.root.mkdir(exist_ok=False);P.write(args.root/'protocol.json',protocol());print('protocol',P.digest(P.enc(protocol())))
    elif args.action=='seal':seal(args.root)
    else:run(args.root)
