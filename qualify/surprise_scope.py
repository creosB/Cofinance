"""Lab-only closed grammar. Unrecognized or qualified assertions do not fall back to an LLM.

Not a general news parser: only the full approved grammar can produce an arithmetic finding.
Uses the existing v1 numeric parser after validating every clause and both referring entities.
"""
import re
from qualify import surprise_review as V

MET='(?:'+ '|'.join(re.escape(k) for k in sorted(V.METRICS,key=len,reverse=True))+')'
VALUE=r'(?:negative |positive |[-+])?\$?\d+(?:\.\d+)?(?:%| (?:basis points?|percent|million|billion))?'

def checked(title,issuer):
    if not isinstance(issuer,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9 &.-]{1,65}',issuer):return 'ABSTAIN','unbound_issuer'
    if not isinstance(title,str) or not title.strip():return 'ABSTAIN','unrecognized_scope'
    who=re.escape(issuer); p=re.match(rf'^\s*{who}\s*:\s*',title,re.I); anchored=bool(p); body=title[p.end():] if p else title
    measure=rf'(?P<q>Q[1-4]) (?:(?P<basis>unadjusted|adjusted|GAAP|non-GAAP) )?(?P<m>{MET})'
    rep=rf'(?:(?P<issuer>{who}) )?{measure} (?:was reported at|came in at) (?P<amount>{VALUE})(?: for (?P<forissuer>{who}))?'
    est=rf'(?:(?:analysts expected {measure} of)|(?:consensus for (?P<q2>Q[1-4]) (?:(?P<basis2>unadjusted|adjusted|GAAP|non-GAAP) )?(?P<m2>{MET}) stood at)) (?P<amount>{VALUE})(?: for (?P<forissuer>{who}))?'
    previous=rf'last year Q[1-4] {MET} was {VALUE}'
    slots={}
    for c in V.clauses(body):
        if re.fullmatch(previous,c,re.I):continue
        a=re.fullmatch(rep,c,re.I); e=re.fullmatch(est,c,re.I)
        if not a and not e:return 'ABSTAIN','unrecognized_scope'
        k='actual' if a else 'expected'; v=(a or e).groupdict()
        if k in slots:return 'ABSTAIN','duplicate_role'
        # Unscoped issuer references are allowed only under an exact asset heading.
        if not anchored and not (v.get('issuer') or v.get('forissuer')):return 'ABSTAIN','unbound_issuer'
        slots[k]=tuple((v.get(x) or v.get(x+'2') or '').lower() for x in ('q','m','basis'))+(v['amount'],)
    if set(slots)!={'actual','expected'}:return 'ABSTAIN','missing_role'
    a,e=slots['actual'],slots['expected'];
    if a[:3]!=e[:3]:return 'ABSTAIN','unlike_scope'
    # Canonicalization only after issuer, period, metric, basis and assertion checks.
    qa,ma,_,x=a;qe,me,_,y=e
    result,reason=V.review(f'{qa} {ma} was reported at {x}; analysts expected {qe} {me} of {y}')
    return result,'checked_'+reason
