"""Research only. Stage decisions from issuer excerpts, not real-world confirmation or a language-model confidence score."""
import hashlib,re
from dataclasses import dataclass

YES='LIVE';NO='NOT_LIVE';UNK='NOT_ESTABLISHED'
@dataclass(frozen=True)
class Proof:
    doc:str; field:str; start:int; end:int; quote:str; digest:str; stage:str; scoped:bool
    def export(self):return dict(source=self.doc,field=self.field,start=self.start,end=self.end,quote=self.quote,sha256=self.digest,stage=self.stage)

def norm(s):return re.sub(r'\s+',' ',s.translate(str.maketrans({'’':"'",'‘':"'",'“':'"','”':'"'}))).strip().strip('"').strip().casefold()
def token(s):return r'(?<![\w-])'+re.escape(norm(s))+r'(?![\w-])'
def parts(s):
    # Offsets always refer to the original field, never to case-folded text.
    for m in re.finditer(r'[^.!?\n]+(?:[.!?]+|(?=\n|$))',s):
        t=m.group(); l=len(t)-len(t.lstrip()); rr=len(t.rstrip());
        if rr>l:yield m.start()+l,m.start()+rr,t[l:rr]

def enum_scope(t):return {v.strip(' .,:;') for v in re.split(r',\s*|\s+and\s+',t) if v.strip(' .,:;')}

def stage(line,sub,scope):
    t=norm(line).rstrip('.!?');subject=token(sub);scope=norm(scope);scopehit=not scope or bool(re.search(token(scope),t));present=bool(re.search(subject,t));
    if not present:return None
    correction=False
    for pre in ('correction to the launch bulletin: ','a separate bulletin says ','the public rollout note says '):
        if t.startswith(pre):correction=pre.startswith('correction');t=t[len(pre):];break
    if re.search(r'\b(under review|under investigation|in dispute|withdrawn|rescinded|retracted|correction|pending verification|if|unless|reportedly|unconfirmed)\b',t):return 'block',scopehit
    suffix=r'(?: (?:in|to|for) (?:the )?(?P<where>[a-z0-9][\w ,.-]*))?'; neg=re.fullmatch(subject+r' (?:is not yet available|has not launched|is not available|is not live)'+suffix,t)
    if neg:
        located=(not scope and not neg['where']) or (scope and neg['where'] and scope in enum_scope(neg['where']));return (('corrected_no' if correction else 'no') if located else 'elsewhere'),bool(located)
    if correction:return 'block',scopehit
    if re.search(r'\b(only|pilot|testers?|beta|preview|invitation.only|pre[- ]?order|expected|plans?|will|would|may|coming|aims?|intends?|not|next season)\b',t):return 'limited',scopehit
    place=r'(?P<where>[a-z0-9][\w ,.-]*)';p1=re.fullmatch(subject+r' (?:is|are) now available (?:in|from|to) '+place,t);p2=re.fullmatch(subject+r' can now be purchased in '+place,t)
    if p1 or p2:
        loc=(p1 or p2)['where'];ok=not scope or scope in enum_scope(loc);return ('yes' if ok else 'elsewhere'),bool(ok)
    if not scope and (re.fullmatch(r'launched today, '+subject+r' is (?:a|an) .+',t) or re.fullmatch(r'customers can now purchase '+subject+r'(?: and .+)?',t)):return 'yes',True
    if scope:
        where=token(scope)
        if re.fullmatch(r'(?:on [^,]+, (?:[a-z]+ \d+, )?)?(?:the highly anticipated )?'+subject+r' made its debut at '+where,t):return 'yes',True
        if re.fullmatch(r"(?:[\w'-]+(?: [\w'-]+){0,3}['’]s )?"+subject+r' launches on '+where+r' today(?:, bringing .+)?',t):return 'yes',True
        if re.fullmatch(r'today '+where+r' welcomes '+subject+r'(?:, plus .+)?',t):return 'yes',True
    if re.search(r'\b(announc\w*|unveil\w*|introduc\w*|partner\w*|launch\w*|available|release\w*|welcomes|arriv\w*|debut\w*)\b',t):return 'mention',scopehit
    return 'mention',scopehit

def evaluate(task):
    if set(task)!={'subject','scope','question','documents'}:raise ValueError('The evaluator takes evidence and a question only; never a key, split or fixture ID.')
    sub,sc=task['subject'],task['scope'];docs=task['documents'];ids=[d['id'] for d in docs];assert len(ids)==len(set(ids)) and all(set(d)=={'id','title','body'} for d in docs);events=[]
    for d in docs:
        found=[]
        for field in ('body','title'):
            s=d[field];digest=hashlib.sha256(s.encode()).hexdigest()
            for a,b,q in parts(s):
                obs=stage(q,sub,sc)
                if obs:found.append(Proof(d['id'],field,a,b,q,digest,*obs))
        # A qualification in the same notice can invalidate a locally positive clause even without repeating the product name.
        if found:
            for a,b,q in parts(d['body']):
                if not re.search(token(sub),norm(q)) and re.search(r'\b(?:(?:this|that|the) (?:launch|rollout|release|availability) (?:has been|is|was) (?:withdrawn|retracted|suspended|under review|provisional)|(?:it|the service|the product) is not (?:yet )?(?:available|live)|(?:access|availability) (?:remains|is) (?:limited|restricted) to|(?:invited testers|invited customers|beta users) only)\b',norm(q)):
                    found.append(Proof(d['id'],'body',a,b,q,hashlib.sha256(d['body'].encode()).hexdigest(),'block',True))
        events.extend(found)
    def emit(tag,ev,why):
        selected={}
        for x in ev:selected.setdefault(x.doc,x)
        refs=[selected[i].export() for i in sorted(selected)]; assert all(next(d for d in docs if d['id']==z['source'])[z['field']][z['start']:z['end']]==z['quote'] for z in refs)
        return {'verdict':tag,'sources':sorted(selected),'reason':why,'receipts':refs}
    pos=[e for e in events if e.stage=='yes'];neg=[e for e in events if e.stage=='no'];rev=[e for e in events if e.stage=='corrected_no'];dead=[e for e in events if e.stage=='block'];
    if dead:return emit(UNK,pos+neg+rev+dead,'unresolved_qualification')
    if rev:
        old=[e for e in pos if next(d['title'] for d in docs if d['id']==e.doc).casefold()=='launch bulletin'];unique=sorted({e.doc for e in old});valid=len(rev)==1 and len(unique)==1 and rev[0].doc!=unique[0] and {e.doc for e in pos}==set(unique)
        return emit(NO if valid else UNK,old+rev if valid else pos+rev,'named_correction' if valid else 'unlinked_correction')
    if pos and neg:return emit(UNK,pos+neg,'conflicting_reports')
    if pos:return emit(YES,pos,'explicit_current_scope')
    if neg:return emit(NO,neg,'explicit_not_live')
    maybe=[e for e in events if e.stage in ('mention','limited')];exact=[e for e in maybe if e.scoped];pilot=[e for e in maybe if 'closed pilot' in norm(e.quote)];return emit(UNK,(exact+pilot) if exact else maybe or events,'no_verified_completion')
