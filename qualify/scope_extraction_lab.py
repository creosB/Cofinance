"""Local source extraction screen. It checks fields and provenance, not only the derived answer."""
import argparse, hashlib, json, random, re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from core.documents import select_excerpts
from qualify import scope_extraction as E,scope_packets as P
from qualify.local_simulation import install_local_only
POLICY='All packets, no retries. Exact end-to-end match requires correct subject, measure, period, unit, operator, threshold, observed value, source document identities and verdict. A resolved card must carry an exact page span from each of the fully interpreted served excerpts. If any intended evidence is missing, abstain and charge to coverage. Nonempty card on an indeterminate packet, incorrect card or broken receipt is unsafe. Research-only pass: zero unsafe answers on every split, both reused originals exactly right, >=80% exact on decidable new controlled and >=50% exact on decidable prose challenge. No live adoption regardless. If pass, test whether supplying a checked card actually helps synthesis; if fail, inspect errors before further synthesis. Built and judged by same authors, not an independent validation.'
def encode(x):return json.dumps(x,ensure_ascii=True,sort_keys=True).encode()
def sha(b):return hashlib.sha256(b).hexdigest()
def save(p,x):
    with p.open('xb') as f:f.write(encode(x))
def read(p):return json.loads(p.read_bytes())
def protocol():
    import core.documents as D
    return {'policy':POLICY,'cases':sha(encode(P.cases())),'code':{Path(x).name:sha(Path(x).read_bytes()) for x in [__file__,E.__file__,P.__file__,D.__file__]},'baseline':'Untagged numeric shortcut: use the first numeric rule from the policy/covenant source and the largest number in the other served excerpts as an observation. Always treats equality as sufficient. Same served evidence, no gold or target outcome; a deliberately simple diagnostic comparator, NOT the live Lead.'}
def baseline(ex):
    nums=lambda t:[Decimal(x) for x in re.findall(r'(?<![\w.])\d+(?:\.\d+)?(?![\w])',t)];rr=[v for e in ex if e['name'].lower() in ('covenant.pdf','policy.pdf') for v in nums(e['text'])];aa=[v for e in ex if e['name'].lower() not in ('covenant.pdf','policy.pdf') for v in nums(e['text'])];return None if not rr or not aa else 'met' if max(aa)>=rr[0] else 'missed'
def evidence_ok(res,ex):
    # Independent structural comparison: require a valid slice for every receipt, not just a hash string supplied by the extractor.
    pages={(e['name'],e['page']):e['text'] for e in ex}; ss=res['sources']
    if len(pages)!=len(ex) or {(s['witness']['document'],s['witness']['page']) for s in ss}!=set(pages):return False
    for s in ss:
        w=s['witness'];t=pages[w['document'],w['page']];l,h=w['start'],w['end'];
        if not 0<=l<h<=len(t) or w['quote']!=t[l:h] or w['excerpt_sha256']!=sha(t.encode()):return False
    c=res['card'];return all(any(s['witness']['document']==c[file] and s['role']==role and s['value']==c[val] and (role!='rule' or s['op']==c['op']) and all(s[k]==c[k] for k in ('subject','metric','period','unit')) for s in ss) for file,role,val in [('actual_file','signed','actual'),('rule_file','rule','threshold')])
def classify(res,gold,ex):
    if res['status']!='resolved':return 'missing' if gold else 'safe_abstain'
    return 'exact' if res['card']==gold and evidence_ok(res,ex) else 'unsafe'
def audit_rows(rows):
    out={};
    for split in ('design','controlled','challenge'):
        s=[r for r in rows if r['split']==split];cs=Counter(r['grade'] for r in s);g=sum(r['gold'] is not None for r in s);o={k:cs[k] for k in ('exact','missing','unsafe','safe_abstain')};o.update(n=len(s),decidable=g,indeterminate=len(s)-g,baseline_verdict_correct=sum(r['baseline']==(r['gold']['verdict'] if r['gold'] else None) for r in s),baseline_direction_on_indeterminate=sum(r['baseline'] is not None and r['gold'] is None for r in s),candidate_verdict_correct=sum((r['result']['card']['verdict'] if r['result']['card'] else None)==(r['gold']['verdict'] if r['gold'] else None) for r in s));out[split]=o
    return out
def passed(s):return all(v['unsafe']==0 for v in s.values()) and s['design']['decidable']==s['design']['exact']==2 and all(s[k]['decidable']>0 and s[k]['exact']/s[k]['decidable']>=p for k,p in [('controlled',.8),('challenge',.5)])
def paired_interval(rows,split):
    # Resample paired packet-level verdict-correct differences. Related authored variations are not iid field observations.
    d=[int((r['result']['card']['verdict'] if r['result']['card'] else None)==(r['gold']['verdict'] if r['gold'] else None))-int(r['baseline']==(r['gold']['verdict'] if r['gold'] else None)) for r in rows if r['split']==split];rng=random.Random(771);v=sorted(sum(rng.choices(d,k=len(d)))/len(d) for _ in range(2000));return [round(v[49],3),round(v[1949],3)]
def run(root):
    if read(root/'protocol.json')!=protocol():raise ValueError('Frozen rules or labels changed')
    save(root/'started',True);rows=[]
    for c in P.cases():
        ex=select_excerpts(c['documents'],c['q']);intended={(d['name'],i+1,t) for d in c['documents'] for i,t in enumerate(d['pages'])};served={(e['name'],e['page'],e['text']) for e in ex};complete=intended==served
        res=E.analyze(c['q'],ex) if complete else {'status':'abstain','reason':'incomplete_retrieval','sources':[],'card':None};rows.append(dict(case=c['case'],split=c['split'],result=res,gold=c['gold'],grade=classify(res,c['gold'],ex),baseline=baseline(ex) if complete else None,complete=complete))
    ss=audit_rows(rows);rep=dict(protocol=sha(encode(protocol())),splits=ss,pass_to_synthesis=passed(ss),incomplete=sum(not x['complete'] for x in rows),paired_verdict_delta_interval={k:paired_interval(rows,k) for k in ('controlled','challenge')},production_change=False);save(root/'rows.json',rows);save(root/'report.json',rep);return rep
if __name__=='__main__':
    install_local_only();a=argparse.ArgumentParser();a.add_argument('mode',choices=['freeze','run']);a.add_argument('--root',type=Path,required=True);args=a.parse_args();root=args.root.resolve();data=Path('data').resolve();
    if root==data or not root.is_relative_to(data):raise ValueError('New private run directory required')
    if args.mode=='freeze':root.mkdir();save(root/'protocol.json',protocol());print(sha((root/'protocol.json').read_bytes()))
    else:print(json.dumps(run(root),indent=2))
