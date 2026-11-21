"""Research-only, local text-page receipts. Public interface: evaluate(question, documents, proposal=...).

Offsets are half-open Unicode code-point offsets into the exact supplied page, not PDF bytes or OCR coordinates.
External proposals are data: validate the whole packet and rederive its output. The renderer only receives the
validator's constructed variant. No evidence-bearing status exists for a request with no authentic block.
"""
from dataclasses import dataclass,asdict
from decimal import Decimal
from hashlib import sha256
import json,re
from typing import ClassVar,Literal
from qualify import scope_extraction as E

class InputError(ValueError): pass
@dataclass(frozen=True,slots=True)
class Span:
    doc_id:str; document:str; page:int; start:int; end:int; quote:str; page_sha256:str; kind:str
@dataclass(frozen=True,slots=True)
class Verified:
    status:ClassVar[Literal['VERIFIED']]='VERIFIED'
    subject:str;metric:str;period:str;unit:str;value:str;threshold:str;operator:str;verdict:Literal['met','missed'];rule:Span;observation:Span;superseded:tuple[Span,...]=()
@dataclass(frozen=True,slots=True)
class Conflict:
    status:ClassVar[Literal['CONFLICT']]='CONFLICT'
    span_a:Span;span_b:Span
@dataclass(frozen=True,slots=True)
class Withdrawn:
    status:ClassVar[Literal['WITHDRAWN']]='WITHDRAWN'
    withdrawal_span:Span;target_span:Span
@dataclass(frozen=True,slots=True)
class Uninterpreted:
    status:ClassVar[Literal['UNINTERPRETED']]='UNINTERPRETED'
    blocker_span:Span;reason_code:str
Result=Verified|Conflict|Withdrawn|Uninterpreted

def indexed(docs):
    out=[];ids=set();names=set()
    for d in docs:
        i,n,pp=d.get('id'),d.get('name'),d.get('pages')
        if not isinstance(i,str) or not i.strip() or not isinstance(n,str) or not n.lower().endswith('.pdf') or not isinstance(pp,(list,tuple)) or not pp:raise InputError('invalid document identity or pages')
        if i.casefold() in ids or n.casefold() in names:raise InputError('duplicate document identity or ambiguous filename')
        ids.add(i.casefold());names.add(n.casefold())
        for page,raw in enumerate(pp,1):
            if not isinstance(raw,str) or not raw.strip():raise InputError('missing source text, no authentic blocker for this page')
            digest=sha256(raw.encode()).hexdigest();pending=None
            def block(a,b,k):return Span(i,n,page,a,b,raw[a:b],digest,k)
            for m in re.finditer(r'[^\r\n]+|\r\n|\r|\n',raw):
                if m.group(0) in ('\r','\n','\r\n'):continue
                v=m.group().strip()
                if not v:continue
                a=m.start()+len(m.group())-len(m.group().lstrip());b=m.end()-len(m.group())+len(m.group().rstrip());k='footnote' if re.match(r'^(?:\[\d+\]|\* |†)',v) else 'header' if v.startswith('#') or v.endswith(':') else 'table_row' if '|' in v or '\t' in v else 'paragraph'
                if pending:
                    gap=raw[pending[1]:a]; blank=len(re.findall(r'\r\n|\n|\r',gap))>1
                    if pending[2]==k=='paragraph' and not blank:pending=(pending[0],b,k);continue
                    out.append(block(*pending));pending=None
                pending=(a,b,k)
            if pending:out.append(block(*pending))
    if not out:raise InputError('no indexed text; no evidence result can be constructed')
    return tuple(sorted(out,key=lambda s:(s.doc_id.casefold(),s.page,s.start)))

def fields(f):return tuple(f[k] for k in ('kind','subject','metric','period','unit'))
def fact(b):
    t=b.quote
    if b.kind=='footnote':t=re.sub(r'^(?:\[\d+\]|\*|†)\s*','',t)
    return E.parse(dict(text=t,page=b.page,name=b.document))

def notice(b):
    s=' '.join(b.quote.split());s=re.sub(r'^\[\d+\]\s*','',s)
    m=re.fullmatch(r'Notice: ([A-Za-z][A-Za-z0-9_-]*\.pdf) is withdrawn without replacement for ([A-Za-z-]+)\.',s,re.I)
    if m:return m[1].casefold(),m[2].casefold(),'explicit'
    m=re.fullmatch(r'The signed full-month uptime ledger for ([A-Za-z-]+) was withdrawn without replacement\.',s,re.I)
    if m:return None,m[1].casefold(),'full_month_uptime'
    return None

def decide(q,bs):
    target=E.context(q);unk=lambda b,why:Uninterpreted(b,why)
    if target is None:return unk(bs[0],'QUESTION_SCOPE')
    parsed=[];notices=[];unknown=[]
    for b in bs:
        f=fact(b);w=notice(b)
        if f:parsed.append((b,f))
        elif w:notices.append((b,w))
        else:unknown.append(b)
    # Resolve the question's rule before using a ledger, notice or footnote from an unrelated scope.
    rules=[(b,f) for b,f in parsed if f['role']=='rule' and fields(f)==target]
    if not rules:
        suspect=[b for b,f in parsed if f['role']=='rule'] or [b for b in unknown if re.search(r'\b(must|requires|commitment)\b',b.quote,re.I)]
        return unk((suspect or unknown or list(bs))[0],'RULE_SCOPE')
    if unknown:
        ordered=sorted(unknown,key=lambda b:(b.kind!='footnote',b.doc_id.casefold(),b.page,b.start));return unk(ordered[0],'UNPARSED_BLOCK')
    same=[(b,f) for b,f in parsed if fields(f)==target];supers=[(b,f) for b,f in same if f['replaces']];inactive=set();prior=[]
    if len(supers)>1:return unk(supers[-1][0],'AMBIGUOUS_CORRECTION')
    for b,f in supers:
        old=[(a,h) for a,h in parsed if a.document.casefold()==f['replaces'].casefold()+'.pdf' and a!=b and h['role'] in ('signed','preliminary') and fields(h)==target]
        if len(old)!=1:return unk(b,'UNLINKED_CORRECTION')
        inactive.add(old[0][0]);prior.append(old[0][0])
    signed=[(b,f) for b,f in same if f['role']=='signed' and b not in inactive];prelim=[(b,f) for b,f in same if f['role']=='preliminary' and b not in inactive]
    if notices:
        b,(name,subject,mode)=notices[0]
        if len(notices)!=1 or subject!=target[1] or (mode=='full_month_uptime' and target[0]!='uptime'):return unk(b,'UNLINKED_NOTICE')
        refs=[a for a,h in signed if (name is None or a.document.casefold()==name) and a!=b]
        if len(refs)!=1 or len(signed)!=1:return unk(b,'UNLINKED_NOTICE')
        return Withdrawn(b,refs[0])
    for group in (rules,signed):
        for i,(a,f) in enumerate(group):
            for b,h in group[i+1:]:
                if (Decimal(f['value']),f['op'])!=(Decimal(h['value']),h['op']):return Conflict(a,b)
    if not signed:
        possible=[(b,f) for b,f in parsed if f['role']!='rule'];rank=lambda pair:(-sum(x==y for x,y in zip(fields(pair[1]),target)),{'signed':0,'preliminary':1,'dashboard':2}.get(pair[1]['role'],3),pair[0].doc_id.casefold(),pair[0].page,pair[0].start)
        blocker=min(possible,key=rank)[0] if possible else rules[0][0];return unk(blocker,'NO_MATCHING_OBSERVATION')
    a,v=signed[0];r,k=rules[0]
    for b,f in prelim:
        if Decimal(f['value'])!=Decimal(v['value']):return unk(b,'UNRECONCILED_PRELIMINARY')
    av,rv=Decimal(v['value']),Decimal(k['value']);met=av>=rv if k['op']=='ge' else av>rv
    return Verified(target[1],target[2],target[3],target[4],str(av),str(rv),k['op'],'met' if met else 'missed',r,a,tuple(prior))

def wire(obj):return json.loads(json.dumps({'status':obj.status,**asdict(obj)},ensure_ascii=False,allow_nan=False))
def anchor(obj):
    if isinstance(obj,Verified):return obj.observation
    if isinstance(obj,Conflict):return obj.span_b
    if isinstance(obj,Withdrawn):return obj.withdrawal_span
    return obj.blocker_span

def _render(obj):
    if isinstance(obj,Verified):msg=f'{obj.subject}: {obj.verdict}. {obj.metric} ({obj.period}) = {obj.value} {obj.unit}; required { {"gt":">","ge":">="}[obj.operator]} {obj.threshold} {obj.unit}.';refs=[obj.rule,obj.observation,*obj.superseded]
    elif isinstance(obj,Conflict):msg='These source passages disagree about the same measure and period.';refs=[obj.span_a,obj.span_b]
    elif isinstance(obj,Withdrawn):msg='This notice withdraws the identified record without replacement.';refs=[obj.withdrawal_span,obj.target_span]
    else:msg='I could not verify a conclusion from this passage.';refs=[obj.blocker_span]
    return {'message':msg,'citations':[asdict(x) for x in refs]}

def proof_ok(q,obj,bs):
    # Second pass checks the facts on the cited blocks, separately from how the candidate chose them.
    if type(obj) not in (Verified,Conflict,Withdrawn,Uninterpreted):return False
    target=E.context(q)
    if isinstance(obj,Uninterpreted):return obj.blocker_span in bs and obj.reason_code in {'QUESTION_SCOPE','RULE_SCOPE','UNPARSED_BLOCK','AMBIGUOUS_CORRECTION','UNLINKED_CORRECTION','UNLINKED_NOTICE','NO_MATCHING_OBSERVATION','UNRECONCILED_PRELIMINARY','CONTRACT_REJECTED'}
    if not target:return False
    def read(b):return fact(b) if b in bs else None
    if isinstance(obj,Conflict):
        a,b=read(obj.span_a),read(obj.span_b)
        return bool(a and b and obj.span_a!=obj.span_b and fields(a)==fields(b)==target and a['role']==b['role'] in ('rule','signed') and (Decimal(a['value']),a['op'])!=(Decimal(b['value']),b['op']))
    if isinstance(obj,Withdrawn):
        w=notice(obj.withdrawal_span) if obj.withdrawal_span in bs else None;f=read(obj.target_span)
        return bool(w and f and obj.withdrawal_span!=obj.target_span and fields(f)==target and f['role']=='signed' and w[1]==f['subject'] and ((w[0]==obj.target_span.document.casefold()) if w[0] else (w[2]=='full_month_uptime' and f['kind']=='uptime')))
    r,a=read(obj.rule),read(obj.observation)
    if not r or not a or fields(r)!=target or fields(a)!=target or r['role']!='rule' or a['role']!='signed' or obj.rule==obj.observation:return False
    if (obj.subject,obj.metric,obj.period,obj.unit)!=target[1:] or (obj.threshold,obj.operator,obj.value)!=(r['value'],r['op'],a['value']):return False
    cmp={'ge':Decimal(a['value'])>=Decimal(r['value']),'gt':Decimal(a['value'])>Decimal(r['value'])};refs=obj.superseded
    if obj.operator not in cmp or obj.verdict!=('met' if cmp[obj.operator] else 'missed'):return False
    if not a['replaces']:return not refs
    return len(refs)==1 and refs[0] in bs and refs[0].document.casefold()==a['replaces']+'.pdf' and bool(f:=read(refs[0])) and fields(f)==target and f['role'] in ('preliminary','signed')

NOT_GIVEN=object()
def evaluate(q,docs,proposal=NOT_GIVEN):
    bs=indexed(docs);trusted=decide(q,bs);expected=wire(trusted);log=[]
    raw=expected if proposal is NOT_GIVEN else proposal
    try:matches=isinstance(raw,dict) and json.dumps(raw,sort_keys=True,allow_nan=False)==json.dumps(expected,sort_keys=True,allow_nan=False) and proof_ok(q,trusted,bs)
    except (TypeError,ValueError,RecursionError):matches=False
    if not matches:
        # The blocker comes from the trusted index, never from a rejected candidate's quote or message.
        b=anchor(trusted);b=b if b in bs else bs[0];tag=raw.get('status') if isinstance(raw,dict) else None
        log.append({'code':'CONTRACT_REJECTED','proposed_tag':tag if isinstance(tag,str) and tag in ('VERIFIED','CONFLICT','WITHDRAWN','UNINTERPRETED') else 'INVALID','blocker':[b.doc_id,b.page,b.start,b.end]});trusted=Uninterpreted(b,'CONTRACT_REJECTED')
    return {'result':wire(trusted),'rendered':_render(trusted),'contract_failures':log}
