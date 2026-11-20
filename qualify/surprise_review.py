"""A research-only evidence checker. It models explicit comparator semantics, not share prices."""
from decimal import Decimal
import json
import re

DIRECT = ('Classify the explicit financial surprise in this fictional headline, not the future price. UP is a result better than the stated pre-report analyst expectation; DOWN is worse. Higher loss, churn or costs is worse. Compare the same company, period and measure, respecting signs and units. Historical numbers or targets for another period are not an expectation. If equal, missing, contradictory or incomparable, ABSTAIN. Return only JSON with label UP, DOWN or ABSTAIN.')
SELECT = ('Review this fictional headline for evidence, not sentiment. Select the clause number containing the reported result and the clause number containing the matching prior analyst expectation. Do not select a historical result, later target or unrelated number. If there is no unambiguous matching pair, use -1 for both. Return JSON with integer fields actual and expected. Do not do arithmetic.')
SCHEMA={'type':'json_schema','json_schema':{'name':'evidence','strict':True,'schema':{'type':'object','properties':{k:{'type':'integer','minimum':-1,'maximum':20} for k in ('actual','expected')},'required':['actual','expected'],'additionalProperties':False}}}
METRICS={'revenue growth':(1,r'revenue growth|sales (?:expanded|growth)'), 'customer churn':(-1,r'customer churn|subscriber churn'), 'operating margin':(1,r'operating margin'), 'expense growth':(-1,r'expense growth|operating costs'), 'earnings per share':(1,r'earnings per share|\beps\b'), 'loss per share':(-1,r'loss per share|per.share loss'), 'unit sales':(1,r'unit sales|devices|shipments'), 'free cash flow':(1,r'free cash flow')}
EST=re.compile(r'\b(?:analysts? expected|consensus|pencilled in|analysts? (?:forecast|were looking for)|street (?:wanted|had expected))\b',re.I)
ACT=re.compile(r'\b(?:reported|came in at|was|were|expanded|reached|rose|narrowed|broke even)\b',re.I)
SKIP=re.compile(r'\b(?:last year|a year ago|prior year|next year|no (?:analyst )?consensus|no estimate)\b',re.I)
NUM=re.compile(r'(?<![\w.])(?P<sign>negative\s+|positive\s+|[-+])?\s*(?P<dollar>\$)?\s*(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>basis points?|bps\b|percent\b|%|million\b|billion\b|[mb]\b)?',re.I)


def clauses(title):
    return [s.strip(' .') for s in re.split(r';\s*|(?<=[.!?])\s+|\s+(?:versus|against)\s+|,\s+(?=(?:above|below)\b)',title) if s.strip(' .')]


def observation(text):
    if SKIP.search(text): return None
    q=re.findall(r'\bQ[1-4]\b|\b(?:this|next) quarter\b',text,re.I); q={s.lower() for s in q}
    if len(q)>1: return None
    mts=[m for m,(_,rgx) in METRICS.items() if re.search(rgx,text,re.I)]
    if len(mts)>1:return None
    v=[m for m in NUM.finditer(text) if m['n']];
    if len(v)!=1: return None
    g=v[0]; n=Decimal(g['n'].replace(',','')); sc=(g['scale'] or '').lower(); unit='dollar' if g['dollar'] else 'number'
    if sc in {'%','percent','bps','basis point','basis points'}:
        if g['dollar']:return None
        unit='rate'; n/=100 if sc in ('%','percent') else 10000
    if sc in ('million','m'): n*=10**6
    if sc in ('billion','b'): n*=10**9
    if (g['sign'] or '').strip().lower() in ('negative','-'): n=-n
    return {'metric':mts[0] if mts else None,'period':next(iter(q),None),'value':n,'unit':unit,'est':bool(EST.search(text)),'actual':bool(ACT.search(text))}


def review(title, selection=None):
    cs=clauses(title); obs=[observation(c) for c in cs]
    a=[i for i,v in enumerate(obs) if v and v['actual'] and not v['est']]; e=[i for i,v in enumerate(obs) if v and v['est']]
    # Do not let any selector discard conflicting explicit numbers. This conservative checker also declines ranges, mixtures and unknown units.
    if len(a)!=1 or len(e)!=1:return 'ABSTAIN','nonunique_or_missing'
    i,j=a[0],e[0]
    if selection is not None:
        if not isinstance(selection,dict) or set(selection)!= {'actual','expected'} or any(type(v)!=int for v in selection.values()): raise ValueError('Malformed evidence indices')
        if (selection['actual'],selection['expected'])!=(i,j):return 'ABSTAIN','not_corroborated'
    x,y=obs[i],obs[j]; m=x['metric'];
    if not m or y['metric']!=m or not x['period'] or x['period']!=y['period'] or x['unit']!=y['unit']:return 'ABSTAIN','incomparable'
    required='dollar' if m in ('earnings per share','loss per share','free cash flow') else 'number' if m=='unit sales' else 'rate'
    if x['unit']!=required or (m=='loss per share' and min(x['value'],y['value'])<0): return 'ABSTAIN','unsupported_units_or_sign'
    gap=(x['value']-y['value'])*METRICS[m][0]; return ('UP' if gap>0 else 'DOWN' if gap<0 else 'ABSTAIN'),'verified_equal' if gap==0 else 'verified_difference'


def pick(text):
    d=json.loads(text)
    if not isinstance(d,dict) or set(d)!={'actual','expected'} or any(type(v)!=int or v< -1 or v>20 for v in d.values()): raise ValueError('Malformed evidence selection')
    return d


def routed(direct,rule,why):
    # Deliberate ablation: do not claim fallback outputs have verified numbers.
    return (rule,'verified') if why.startswith('verified_') else (direct,'semantic_fallback')
