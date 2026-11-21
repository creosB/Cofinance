"""No-model, one-look packet experiment. Labels are sealed separately before implementation."""
import argparse,itertools,json,re
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from qualify import receipt_contract as R,receipt_matrix as M,scope_extraction as OLD
from qualify.scope_packets import cases as old_sources
from qualify.local_simulation import install_local_only

def rd(p):return json.loads(Path(p).read_bytes())
def old(q,docs):return OLD.analyze(q,[{'name':d['name'],'page':i,'text':t} for d in docs for i,t in enumerate(d['pages'],1)])
def config():
 fs=['qualify/receipt_contract.py','qualify/receipt_matrix.py','qualify/receipt_screen.py','qualify/scope_extraction.py','qualify/scope_card_lab.py'];return {f:M.digest(Path(f).read_bytes()) for f in fs}
def freeze(root):
 meta=rd(root/'labels-protocol.json');assert meta['labels_code']==config()['qualify/receipt_matrix.py'] and M.digest((root/'labels.json').read_bytes())==meta['preimplementation_labels'];M.seal(root/'protocol.json',{'code':config(),'labels_protocol':M.digest((root/'labels-protocol.json').read_bytes()),'scoring':'Exact state, required document identities, designated blocker or injected quote, plus normalized comparison and exact receipts. Count missing, extra and wrong identities as errors, even if an alternative source also justifies abstention. A previously conflicted case allows two authentic incompatible observations.'});print('protocol',M.digest((root/'protocol.json').read_bytes()))

def source_audit(src,docs):
 ds=[d for d in docs if d['id']==src['doc_id'] and d['name']==src['document']];assert len(ds)==1
 assert type(src['page']) is int and 1<=src['page']<=len(ds[0]['pages']);p=ds[0]['pages'][src['page']-1];assert type(src['start']) is type(src['end']) is int and 0<=src['start']<src['end']<=len(p);assert p[src['start']:src['end']]==src['quote'] and M.digest(p.encode())==src['page_sha256'];return 1

def run(root):
 proto=rd(root/'protocol.json');meta=rd(root/'labels-protocol.json');rows=rd(root/'labels.json');assert config()==proto['code'] and M.digest((root/'labels-protocol.json').read_bytes())==proto['labels_protocol'] and M.digest(M.words(rows))==meta['preimplementation_labels'];M.seal(root/'started.json',{'one_look':True});base={x['id']:x for x in M.originals()};old_cards={k:old(x['q'],x['docs'])['card'] for k,x in base.items()};assert sum(c is not None for c in old_cards.values())==7
 checks=[];parents=[];nsp=0;nd=0;nperm=0;failures=[]
 for k,p in base.items():
  v=R.evaluate(p['q'],p['docs']);parents.append({'id':k,'status':v['result']['status'],'value':v['result'].get('value'),'old_resolved':old_cards[k] is not None})
 for x in rows:
  key,kind=x['id'].split('/');q,docs=x['q'],x['documents'];v=R.evaluate(q,docs);res=v['result'];tag=res['status'];sp=v['rendered']['citations'];assert not v['contract_failures'];nsp+=sum(source_audit(z,docs) for z in sp)
  for perm in itertools.permutations(docs):assert R.evaluate(q,perm)==v;nperm+=1
  ids=sorted(s['doc_id'] for s in sp);refs_ok=ids==x['sources'];exact_quote=not x['needle'] or x['needle'] in [s['quote'] for s in sp];value_ok=True
  if tag=='VERIFIED':
   g=old_cards[key];value_ok=bool(g and res['verdict']==x['verdict'] and (Decimal(res['value']),Decimal(res['threshold']),res['operator'])==(Decimal(g['actual']),Decimal(g['threshold']),g['op']))
  if not x['sources'] and x['id']=='unreconciled/correction':
   f=[re.fullmatch(r'The signed quarter-end ledger records (\d+) million dollars of unrestricted cash for Alder\.',s['quote']) for s in sp];refs_ok=len(f)==2 and all(f) and f[0][1]!=f[1][1]
  passed=tag==x['status'] and refs_ok and exact_quote and value_ok;unsafe=tag!='UNINTERPRETED' and not passed
  checks.append({'id':x['id'],'type':kind,'expected':x['status'],'observed':tag,'exact':passed,'false_world':unsafe,'refs_ok':refs_ok,'blocker_ok':exact_quote,'value_ok':value_ok,'reason':res.get('reason_code'),'ids':ids,'old_flat_status':old(q,docs)['status'],'result':res})
  for mal in [dict(res,extra_claim='invented'),{'status':'WITHDRAWN'},dict(res,status='zz')]:
   z=R.evaluate(q,docs,proposal=mal);assert z['result']['status']=='UNINTERPRETED' and z['result']['reason_code']=='CONTRACT_REJECTED' and len(z['contract_failures'])==1 and len(z['rendered']['citations'])==1;source_audit(z['result']['blocker_span'],docs);failures.extend(z['contract_failures']); nd+=1
 group=lambda tests:{t:sum(test(row) for row in checks if row['type']==t) for t,test in tests.items()};exact={t:sum(r['exact'] for r in checks if r['type']==t) for t in ['wording','scope','correction']};nfalse=sum(r['false_world'] for r in checks);kept=sum(r['type']=='wording' and r['observed']=='VERIFIED' and r['value_ok'] for r in checks);scope=sum(r['type']=='scope' and r['observed']=='UNINTERPRETED' for r in checks);badcorrect=sum(r['type']=='correction' and r['observed']=='VERIFIED' for r in checks)
 saved=next(x for x in old_sources() if x['case']=='withdrawn-observation');prior=old(saved['q'],saved['documents']);replay=R.evaluate(saved['q'],saved['documents']);assert all(source_audit(s,saved['documents']) for s in replay['rendered']['citations']);p=deepcopy(replay['result']);p.pop('target_span',None);down=R.evaluate(saved['q'],saved['documents'],proposal=p)
 report={'experimental':True,'parents':12,'variants':36,'baseline_parents':parents,'observed':dict(Counter(z['observed'] for z in checks)),'exact_by_type':exact,'exact':sum(exact.values()),'false_world':nfalse,'wording_verified_preserved':kept,'scope_abstained':scope,'correction_false_verified':badcorrect,'valid_spans':nsp,'order_checks':nperm,'injected_contract_downgrades':nd,'mismatches':[{k:r[k] for k in ('id','expected','observed','ids','reason','refs_ok','blocker_ok','value_ok')} for r in checks if not r['exact']],'passes_lab_gate':not nfalse and kept==7 and scope==12 and not badcorrect and sum(exact.values())>=32 and min(exact.values())>=10 and nd==108,'saved_withdrawal':{'old':prior['reason'],'old_docs':[f['witness']['document'] for f in prior['sources']],'new':replay,'without_target':down},'protocol_sha256':M.digest((root/'protocol.json').read_bytes())};M.seal(root/'cases.json',checks);M.seal(root/'contract_failures.json',failures);M.seal(root/'report.json',report);print(json.dumps({k:v for k,v in report.items() if k not in ('saved_withdrawal','baseline_parents')},indent=1));print('saved case:',replay['result']['status'],[s['document'] for s in replay['rendered']['citations']],'without target:',down['result']['status'],down['result']['blocker_span']['document'])
if __name__=='__main__':
 install_local_only();p=argparse.ArgumentParser();p.add_argument('action',choices=['freeze','run']);p.add_argument('root',type=Path);a=p.parse_args();(freeze if a.action=='freeze' else run)(a.root)
