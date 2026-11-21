"""Research only. Retrieval ranks reading order; it does not define what a packet-wide verdict covers."""
import argparse, hashlib, json, time
from collections import Counter
from pathlib import Path
from core import documents as D
from qualify import receipt_contract as R
from qualify.local_simulation import install_local_only

def enc(v): return json.dumps(v,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode()
def sha(b):return hashlib.sha256(b).hexdigest()
def put(p,v):
    with p.open('xb') as f:f.write(enc(v))
def read(p):return json.loads(p.read_bytes())
def control(code):return {'checked':False,'reason':code,'evidence':None}
DEFAULT_PAGES=24; DEFAULT_CHARS=120000

def inventory(stored):
    """Snapshot from the complete supplied collection, never from the search results."""
    rows=[];ids=set();names=set()
    for d in stored:
        ident,name=d['id'],d['name']; pp=D.evidence_pages(d)
        if not ident or not name or ident.casefold() in ids or name.casefold() in names or d.get('page_count',len(pp))!=len(pp):raise ValueError('ambiguous packet inventory')
        ids.add(ident.casefold());names.add(name.casefold());rows.append((ident,name,tuple(sha(p.encode('utf8')) for p in pp)))
    return tuple(sorted(rows))

def assess(q,expected,stored,page_budget=DEFAULT_PAGES,char_budget=DEFAULT_CHARS):
    if not expected or not stored:return control('NO_PACKET')
    if sum(len(p) for _,_,p in expected)>page_budget:return control('PAGE_BUDGET')
    if len(stored)!=len(expected):return control('SCOPE_INCOMPLETE')
    seen=set();available=0
    for d in stored:
        pp=d.get('source_pages');ident=d.get('id');
        if not isinstance(pp,(list,tuple)) or any(not isinstance(p,str) for p in pp):return control('SOURCE_UNAVAILABLE')
        if ident in seen:return control('SCOPE_INCOMPLETE')
        seen.add(ident);available+=sum(len(p) for p in pp)
    if available>char_budget:return control('CHAR_BUDGET')
    try:
        if inventory(stored)!=expected:return control('SCOPE_INCOMPLETE')
        result=R.evaluate(q,[{'id':d['id'],'name':d['name'],'pages':D.evidence_pages(d)} for d in stored])
    except (D.DocumentError,R.InputError,ValueError,KeyError):return control('SOURCE_UNAVAILABLE')
    return {'checked':True,'reason':'FULL_SUPPLIED_PACKET','evidence':result}

def page(i,text):return {'id':i,'name':i+'.pdf','pages':[D.normalize(text)],'source_pages':[text],'page_count':1}

def design():
    rows=[]; names=['Mangrove','Iris','Reed','Olive','Aspen','Hornbeam','Mallow','Holly','Buckeye','Spruce','Larch','Sorrel']
    for idx,entity in enumerate(names):
        cash=idx<6;kind=idx%6; question=f'Did {entity} meet its '+('quarter-end unrestricted cash floor?' if cash else 'full-month uptime commitment?');v=(49 if kind==4 else 52) if cash else (97 if kind==4 else 99)
        def obs(value,e=entity):return f'The signed quarter-end ledger records {value} million dollars of unrestricted cash for {e}.' if cash else f'The signed full-month uptime ledger for {e} shows {value} percent.'
        policy=f'{entity} must hold at least 50 million dollars of unrestricted cash at quarter end.' if cash else f'{entity} requires strictly above 98 percent uptime for the entire month.'
        docs=[page('Rule',policy),page('Ledger',obs(v,entity if kind!=5 else 'Ridge')),page('RuleCopy',policy)]
        if kind==0: fourth=obs(v,'Ridge');gold='VERIFIED:met';sources={'Ledger'}
        elif kind==1: fourth=f'[4] Notice: Ledger.pdf is withdrawn without replacement for {entity}.';gold='WITHDRAWN';sources={'Fourth','Ledger'}
        elif kind==2: fourth=obs(v-2);gold='CONFLICT';sources={'Fourth','Ledger'}
        elif kind==3: fourth='[3] Approval pending; the final audited figure has not been released.';gold='UNINTERPRETED';sources={'Fourth'}
        elif kind==4: fourth=(f'The signed quarter-end reconciliation supersedes the ledger and records 51 million dollars of unrestricted cash for {entity}.' if cash else f'The dashboard for {entity} showed 99.9 percent uptime for the first week only.');gold='VERIFIED:met' if cash else 'VERIFIED:missed';sources={'Fourth','Ledger'} if cash else {'Ledger'}
        else: fourth=(f'The preliminary quarter-end total cash figure for {entity} was 55 million dollars.' if cash else f'The dashboard for {entity} showed 99.9 percent uptime for the first week only.');gold='UNINTERPRETED';sources=set()
        docs.append(page('Fourth',fourth));rows.append(dict(id=entity,q=question,kind=['unrelated','withdrawal','conflict','pending','replacement_or_week','missing_scope'][kind],docs=docs,gold=gold,required=sorted(sources)))
    return rows

def name(r):
    if not r['checked']:return 'NOT_EVALUATED'
    z=r['evidence']['result'];return z['status']+(':'+z['verdict'] if z['status']=='VERIFIED' else '')
def result(root):
    protocol=read(root/'protocol.json');cases=design();assert protocol['case_sha']==sha(enc(cases)) and protocol['source_sha']==fingerprint();put(root/'started.json',{'one_look':True});out=[]
    for c in cases:
        ds=c['docs'];q=c['q'];manifest=inventory(ds);es=D.select_excerpts(ds,q);selected={e['document_id'] for e in es};short=[d for d in ds if d['id'] in selected];assert len(es)==len(short)==3 and len(ds)==4
        # Deliberately unsafe comparison baseline only: treating the three returned pages as the whole collection.
        naive=assess(q,inventory(short),short);gate=assess(q,manifest,short);full=assess(q,manifest,ds)
        for arm,r in [('retrieved_only',naive),('require_coverage',gate),('whole_supplied_packet',full)]:
            pred=name(r);cit=r['evidence']['rendered']['citations'] if r['checked'] else [];src={e['doc_id'] for e in cit};expected=c['gold'];joint=pred==expected and set(c['required'])<=src
            for e in cit:
                d=next(d for d in ds if d['id']==e['doc_id']);p=d['source_pages'][e['page']-1];assert e['quote']==p[e['start']:e['end']] and e['page_sha256']==sha(p.encode('utf8'))
            out.append(dict(id=c['id'],kind=c['kind'],arm=arm,selected=sorted(selected),expected=expected,actual=pred,reason=r['reason'],joint=joint,unsupported_verified=pred.startswith('VERIFIED') and not joint,qualified_world=pred in ('CONFLICT','WITHDRAWN') or pred.startswith('VERIFIED'),sources=sorted(src)))
    counts={}
    for arm in ('retrieved_only','require_coverage','whole_supplied_packet'):
        a=[r for r in out if r['arm']==arm];counts[arm]={k:sum(r[k] for r in a) for k in ('joint','unsupported_verified','qualified_world')};counts[arm]['not_evaluated']=sum(r['actual']=='NOT_EVALUATED' for r in a)
    f=counts['whole_supplied_packet'];rep=dict(experimental=True,cases=12,clusters=2,counts=counts,passes_probe=(f['joint']>=9 and f['unsupported_verified']==0 and counts['require_coverage']['not_evaluated']==12),protocol=sha((root/'protocol.json').read_bytes()));put(root/'rows.json',out);put(root/'report.json',rep);print(json.dumps(rep,indent=2));print('retrieval misses',[(r['id'],r['expected'],r['actual'],r['selected']) for r in out if r['arm']=='retrieved_only' and not r['joint']])

def fingerprint():return {p:sha(Path(p).read_bytes()) for p in ('qualify/packet_boundary_lab.py','qualify/receipt_contract.py','qualify/scope_extraction.py','core/documents.py')}
def freeze(root):
    root.mkdir(exist_ok=False);p={'case_sha':sha(enc(design())),'source_sha':fingerprint(),'frozen_gate':'Full packet >=9/12 correct status + required source IDs, zero unsupported VERIFIED, incomplete selections never presented as packet-level. Include correctly withheld separately from decisive world claims. Authored related stress controls, not general accuracy; three search hits vs all four pages adds evidence/compute. No model, network or cached user data.'};put(root/'protocol.json',p);print('protocol',sha((root/'protocol.json').read_bytes()))
if __name__=='__main__':
    install_local_only();ap=argparse.ArgumentParser();ap.add_argument('action',choices=['freeze','run']);ap.add_argument('root',type=Path);a=ap.parse_args();(freeze if a.action=='freeze' else result)(a.root)
