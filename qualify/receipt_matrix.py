"""Authored metamorphic screen on the twelve existing scope-card packets, not new field data."""
from copy import deepcopy
from decimal import Decimal
import hashlib, json
from pathlib import Path
from qualify.scope_card_lab import cases as originals
V,C,W,U='VERIFIED','CONFLICT','WITHDRAWN','UNINTERPRETED'

def frozen_base(c):
 k=c['id']; rule=c['docs'][0]['id']; signed='Ledger'
 if k=='unreconciled': return C, ['Flash','Ledger']
 if k=='withdrawn': return W,['Dashboard','Ledger']
 if k in ('no-month','cash-language','uptime-language'):return U,['Ledger']
 return V, [rule,signed]+(['Flash'] if k=='revised' else [])

def packets():
 out=[]
 for n,base in enumerate(originals()):
  k=base['id'];cash='cash' in base['family']; rule=base['docs'][0]['id'];entity=base['q'].split()[1];btag,brefs=frozen_base(base)
  def add(tag,docs,status,refs,needle=''):
   out.append({'id':k+'/'+tag,'q':base['q'],'documents':docs,'status':status,'sources':sorted(refs),'needle':needle,'verdict':base['gold']['verdict'].lower() if status==V else None})
  dd=deepcopy(base['docs']); t=dd[0]['pages'][0]; t=t.replace('at least','no less than').replace('strictly above','more than').replace('million dollars','million USD').replace(' percent','%'); assert t!=dd[0]['pages'][0];dd[0]['pages'][0]=t; add('wording',dd,btag,brefs)
  dd=deepcopy(base['docs']);t=dd[0]['pages'][0]; t=t.replace(entity,entity+'-West') if n%3==0 else t.replace('quarter end','year end').replace('entire month','entire week') if n%3==1 else t.replace('million dollars','million euros') if cash else t.replace('uptime','network utilization');assert t!=dd[0]['pages'][0];dd[0]['pages'][0]=t;add('scope',dd,U,[rule],t)
  dd=deepcopy(base['docs']); extra=dd[2]; t=extra['pages'][0]
  if k=='withdrawn' or n%3==2:
   # Missing target, absent replacement or unresolved qualifier: never a verified claim.
   txt='[1] Pending: the reported figure is under review; no final disposition is supplied.'; exp=U;refs=[extra['id']]
  elif n%3==0:
   txt=f'[1] Notice: Ledger.pdf is withdrawn without replacement for {entity}.';exp=W if btag==V else U;refs=[extra['id'],'Ledger'] if exp==W else [extra['id']]
  else:
   # An additional signed record. Only two genuinely conflicting same-scope values count.
   txt=(f'The signed quarter-end ledger records 19 million dollars of unrestricted cash for {entity}.' if cash else f'The signed full-month uptime ledger for {entity} shows 92 percent.')
   if k=='unreconciled':exp=C;refs=None  # any two incompatible signed records, checked by independent span oracle
   elif btag==V:exp=C;refs=['Ledger',extra['id']]
   else:exp=U;refs=['Ledger']
  extra['pages'][0]=t+'\n\n'+txt;add('correction',dd,exp,refs or [],txt if k!='unreconciled' and (exp!=U or refs==[extra['id']]) else '')
 return out

def words(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
def digest(x):return hashlib.sha256(x).hexdigest()
def seal(p,x):
 with Path(p).open('xb') as f:f.write(words(x))

def prepare(root):
 root=Path(root);root.mkdir(exist_ok=False);rows=packets();assert len(rows)==len({r['id'] for r in rows})==36
 seal(root/'labels.json',rows);proto={'version':1,'preimplementation_labels':digest(words(rows)),'labels_code':digest(Path(__file__).read_bytes()),'coverage':'36 related variants, 12 parent clusters; each variant uses original page strings. The 12 originals are calibration only. No model, no external access.','gates':['no false world claims across 36','unchanged verified verdict and normalized value for all seven previously resolved wording pairs','all scope shifts abstain','never VERIFIED on correction injection; require indexed notice plus target, incompatible same-scope pair or exact blocker','at least 32/36 exact status + cited sources, 10/12 per family','no invented spans after invalid proposals; no-source is an input failure, not a fake fifth evidence state.'],'notes':'Authored, templated and correlated; not a general extractor. No rerun after inspecting results.'};seal(root/'labels-protocol.json',proto);print('labels',digest(words(rows)),'by status', {s:sum(r['status']==s for r in rows) for s in (V,C,W,U)})
