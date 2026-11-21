"""Document research lab only. Positive verification is scoped to full parsed served excerpts.

This is deliberately a small assertion language, not a general natural-language fact checker.
It returns evidence witnesses, never authorizes chat output or loosens the production wording guard.
Uninterpreted source clauses, unsupported answer clauses, ambiguity and absent evidence fail closed.
"""
from __future__ import annotations
import hashlib
import operator
import re
from collections import defaultdict
from dataclasses import dataclass

SUB=r'(?P<subject>[a-z][a-z-]{0,40})'; NUM=r'(?P<num>0|[1-9]\d{0,11})'; VERB=r'(?P<measure>accepted|dispatched|approved|submitted|inspected|shipped|received|processed|opened|closed|resolved|released|tested|rejected|booked|fulfilled)';UNIT=r'(?P<unit>units|parcels|requests|samples|cases|batches|orders|tickets)'; PERIOD=r'(?P<period>during the period|in (?:January|February|March|April|May|June|July|August|September|October|November|December|Q[1-4])|on (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))'
OPERATORS={'at least':operator.ge,'at most':operator.le,'more than':operator.gt,'less than':operator.lt}
OP=r'(?P<op>at least|at most|more than|less than)'
FACT=[f'{SUB} {VERB} {NUM} {UNIT} {PERIOD}', f'{NUM} {UNIT} were {VERB} by {SUB} {PERIOD}']
RULE=rf'{SUB} (?:requires|earns the full rebate only for) {OP} {NUM} {VERB} {UNIT} {PERIOD}'
VERDICT=rf'{SUB} (?P<verdict>met|missed) the threshold for {VERB} {UNIT} {PERIOD}'
# These complete non-numeric sentences carry no qualifier of a captured value in the design packet.
NOTES={'dispatch is recorded before inspection','the remainder awaited inspection'}
CITE=re.compile(r'^\[([^\[\]:\n]{1,96}\.pdf):([1-9]\d{0,4})\]\s+',re.I)
SEPARATOR=re.compile(r'\.(?=\s|$)|[;\n]'); PREFIX=re.compile(r'^(?:thus|therefore|accordingly),\s+',re.I)
@dataclass(frozen=True)
class Key:
 subject:str;measure:str;unit:str;period:str

def normalize(s):return ' '.join(s.split()).casefold()
def statements(text):
 # Periods within a source filename are not sentence separators. Keep original offsets for receipts.
 start=0
 for match in SEPARATOR.finditer(text):
  piece=text[start:match.start()];a=start+len(piece)-len(piece.lstrip());s=piece.strip();
  if s:yield a,a+len(s),s
  start=match.end()
 piece=text[start:];a=start+len(piece)-len(piece.lstrip());s=piece.strip()
 if s:yield a,a+len(s),s

def parse(s,which):
 for kind,pat in [*(('fact',p) for p in FACT),('rule',RULE),('verdict',VERDICT)]:
  if kind=='verdict' and which=='source':continue
  m=re.fullmatch(pat,normalize(s),re.I)
  if m:
   z=m.groupdict();k=Key(z['subject'],z['measure'],z['unit'],z['period']);return kind,k,(int(z['num']) if 'num' in z else None),z.get('op'),z.get('verdict')
 return None

def assess(excerpts,draft):
 def result(status,reason,witnesses=()):return dict(status=status,reason=reason,witnesses=list(witnesses))
 if not isinstance(draft,str) or not draft.strip() or len(draft)>8000:return result('abstain','unsupported_draft')
 facts=defaultdict(list); rules=defaultdict(list); sources=set()
 for e in excerpts:
  name=e.get('name'); page=e.get('page'); text=e.get('text');
  if not name or type(page) is not int or page<1 or not isinstance(text,str) or not text.strip() or len(text)>=900 or text.endswith(('...', '…')):return result('abstain','unusable_excerpt')
  ref=(name.casefold(),page)
  if ref in sources:return result('abstain','ambiguous_source_reference')
  sources.add(ref);h=hashlib.sha256(text.encode()).hexdigest()
  for a,b,s in statements(text):
   if normalize(s) in NOTES:continue
   f=parse(s,'source')
   if f is None:return result('abstain','uninterpreted_source_clause')
   kind,k,v,op,_=f;w=dict(document=name,page=page,start=a,end=b,quote=text[a:b],excerpt_sha256=h,subject=k.subject,measure=k.measure,unit=k.unit,period=k.period,value=v,role=kind,operator=op)
   (facts if kind=='fact' else rules)[k].append((v,op,ref,w))
 if not sources:return result('abstain','no_served_sources')
 claims=list(statements(draft));all_w=[]
 if not claims:return result('abstain','no_claim')
 for _,__,s in claims:
  s=PREFIX.sub('',s,count=1);cm=CITE.match(s);ref=None
  if cm:ref=(cm[1].casefold(),int(cm[2]));s=s[cm.end():]
  f=parse(s,'draft')
  if f is None:return result('abstain','uninterpreted_answer_clause')
  kind,k,v,op,verdict=f
  def lookup(d):
   vals=d.get(k,[])
   if len({(x[0],x[1]) for x in vals})>1:return 'conflicting_sources',[]
   if ref is not None:vals=[x for x in vals if x[2]==ref]
   if not vals:return ('unbound_citation' if ref is not None else 'no_matching_tuple'),[]
   return None,vals
  if kind in ('fact','rule'):
   why,entries=lookup(facts if kind=='fact' else rules)
   if why:return result('abstain',why)
   if (v,op)!=entries[0][:2]:return result('contradicted','bound_value_or_operator_mismatch',[x[3] for x in entries])
   all_w.extend(x[3] for x in entries)
  else:
   # A decision needs both independent premises. An unverified document list cannot substitute.
   if ref is not None:return result('abstain','single_citation_for_derived_claim_unsupported')
   why,actual=lookup(facts);why2,threshold=lookup(rules)
   if why or why2:return result('abstain',why or why2)
   observed=actual[0][0];limit,relation=threshold[0][:2];correct=OPERATORS[relation](observed,limit);ws=[x[3] for x in actual+threshold]
   if (verdict=='met')!=correct:return result('contradicted','comparison_reversed',ws)
   all_w.extend(ws)
 return result('verified','all_clauses_bound',all_w)
