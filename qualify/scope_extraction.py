"""Research-only source extraction for two narrowly defined questions. No model or app imports.

Each complete served excerpt must be interpretable. A packet is an as-of snapshot: explicit
other dates not modeled by the grammar are not silently merged. No claim about omitted pages.
"""
import hashlib
import re
from decimal import Decimal

ENT=r'(?P<ent>[a-z][a-z-]{0,39})'; N=r'(?P<n>0|[1-9][0-9]{0,11})(?P<frac>\.[0-9]{1,6})?'; OP=r'(?P<op>at least|no less than|strictly above|more than|exceed)';MONEY=r'(?P<metric>unrestricted|total|restricted) cash'; CASH_UNIT=r'(?:million (?:us )?dollars|million usd)'; PERCENT=r'(?: ?%| percent)'
# Each pattern matches the whole statement, never a matching phrase surrounded by an unknown qualifier.
PATTERNS=[('cash','rule','quarter_end',rf'{ENT} must hold {OP} {N} {CASH_UNIT} of {MONEY} at quarter end'),('cash','preliminary','quarter_end',rf'the preliminary quarter-end {MONEY} figure for {ENT} was {N} {CASH_UNIT}'),('cash','signed','quarter_end',rf'the signed quarter-end ledger records {N} {CASH_UNIT} of {MONEY} for {ENT}'),('cash','signed','quarter_end',rf'the signed quarter-end reconciliation supersedes the (?P<replaces>[a-z][a-z-]{{0,32}}) and records {N} {CASH_UNIT} of {MONEY} for {ENT}'),('cash','signed','quarter_end',rf'{ENT} *\| *quarter end *\| *{MONEY} *\| *usd million *\| *{N} *\| *signed actual'),('uptime','rule','full_month',rf'{ENT} requires {OP} {N}{PERCENT} uptime for the entire month'),('uptime','rule','full_month',rf'for {ENT}, full-month uptime must {OP} {N}{PERCENT}'),('uptime','rule','full_month',rf'the monthly uptime commitment was {OP} {N}{PERCENT} for the entire month'),('uptime','signed','full_month',rf'the signed full-month uptime ledger for {ENT} shows (?:exactly )?{N}{PERCENT}'),('uptime','signed','full_month',rf'the signed full-month uptime ledger shows (?:exactly )?{N}{PERCENT} for that month'),('uptime','signed','full_month',rf'{ENT} had a signed full-month uptime of {N}{PERCENT}'),('uptime','dashboard','first_week',rf'the dashboard for {ENT} showed {N}{PERCENT} uptime for the first week only'),('uptime','dashboard','first_week',rf'the dashboard showed {N}{PERCENT} uptime for the first week only')]
RULES=[(kind,role,period,re.compile(pattern,re.I)) for kind,role,period,pattern in PATTERNS];Q_CASH=re.compile(r'did ([a-z][a-z-]{0,39}) meet its quarter-end unrestricted cash floor\?',re.I);Q_UP=re.compile(r'did ([a-z][a-z-]{0,39}) meet its full-month uptime commitment\?',re.I)

def context(q):
    q=' '.join(q.strip().lower().split());m=Q_CASH.fullmatch(q)
    if m:return ('cash',m[1],'unrestricted_cash','quarter_end','USD_million')
    m=Q_UP.fullmatch(q)
    if m:return ('uptime',m[1],'uptime','full_month','percent')
    if q=='did the service exceed its monthly uptime commitment?':return ('uptime','service','uptime','full_month','percent')
    return None

def witness(e):
    t=e['text'];left=len(t)-len(t.lstrip());end=len(t.rstrip());return dict(document=e['name'],page=e['page'],start=left,end=end,quote=t[left:end],excerpt_sha256=hashlib.sha256(t.encode()).hexdigest())

def parse(e):
    w=witness(e);text=' '.join(w['quote'].lower().split()).removesuffix('.')
    for k,role,period,pattern in RULES:
        m=pattern.fullmatch(text)
        if not m:continue
        d=m.groupdict();v=Decimal(d['n']+(d.get('frac') or ''))
        if k=='uptime' and v>100:return None
        op=d.get('op');return dict(kind=k,role=role,period=period,subject=d.get('ent') or 'service',metric=d['metric']+'_cash' if k=='cash' else 'uptime',unit='USD_million' if k=='cash' else 'percent',value=str(v),op=('ge' if op in ('at least','no less than') else 'gt') if op else None,replaces=d.get('replaces'),witness=w)
    return None

def analyze(q,excerpts):
    facts=[]
    def stop(why):return dict(status='abstain',reason=why,card=None,sources=facts)
    target=context(q)
    if not target:return stop('unknown_question_scope')
    if not excerpts:return stop('missing_sources')
    ids=set()
    for e in excerpts:
        t=e.get('text');n=e.get('name');p=e.get('page')
        if not isinstance(n,str) or not n.lower().endswith('.pdf') or type(p) is not int or p<1 or not isinstance(t,str) or not t.strip() or len(t)>=900 or t.rstrip().endswith(('…','...')):return stop('incomplete_source')
        ident=(n.casefold(),p)
        if ident in ids:return stop('ambiguous_source')
        ids.add(ident);f=parse(e)
        if f is None:return stop('uninterpreted_source')
        facts.append(f)
    replacements=[f for f in facts if f['replaces']];inactive=set()
    if len(replacements)>1:return stop('ambiguous_supersession')
    for f in replacements:
        old=[o for o in facts if o['witness']['document'].lower()==f['replaces']+'.pdf'];
        if len(old)!=1 or old[0] is f or old[0]['role'] not in ('preliminary','signed') or (old[0]['kind'],old[0]['subject'],old[0]['period'])!=(f['kind'],f['subject'],f['period']):return stop('unresolved_supersession')
        # A corrected excerpt replaces its named predecessor only; an outstanding conflicting signed observation blocks the result.
        inactive.add((old[0]['witness']['document'].casefold(),old[0]['witness']['page']))
    relevant=[f for f in facts if (f['kind'],f['subject'],f['metric'],f['period'],f['unit'])==target and (f['witness']['document'].casefold(),f['witness']['page']) not in inactive];rules=[f for f in relevant if f['role']=='rule'];actual=[f for f in relevant if f['role']=='signed'];prelim=[f for f in relevant if f['role']=='preliminary']
    if not rules or not actual:return stop('missing_matching_rule_or_signed_observation')
    if len({(Decimal(f['value']),f['op']) for f in rules})!=1 or len({Decimal(f['value']) for f in actual})!=1:return stop('unresolved_conflict')
    if any(Decimal(f['value'])!=Decimal(actual[0]['value']) for f in prelim):return stop('unresolved_preliminary_conflict')
    r=min(rules,key=lambda x:(x['witness']['document'],x['witness']['page']));a=min(actual,key=lambda x:(x['witness']['document'],x['witness']['page']));av,rv=Decimal(a['value']),Decimal(r['value']);met=av>=rv if r['op']=='ge' else av>rv
    return dict(status='resolved',reason='source_bound',card=dict(kind=target[0],subject=target[1],metric=target[2],period=target[3],unit=target[4],threshold=r['value'],op=r['op'],actual=a['value'],verdict='met' if met else 'missed',rule_file=r['witness']['document'],actual_file=a['witness']['document']),sources=facts)
