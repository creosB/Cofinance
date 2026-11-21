"""Research benchmark definitions. Input is the existing, unmodified archived newsroom corpus.
Claims are about what the supplied excerpts warrant, not about independently verified events.
"""
import hashlib,json,random
from pathlib import Path
CACHE=Path('data/replay_20261116/news_gated.jsonl')
# Stable title prefixes, matched uniquely against the snapshot rather than relying on row order.
TITLES={'reserve':'Open up to new possibilities with iPhone Duo','launch':'iPhone Duo arrives at Apple Store locations worldwide','apps':'Developers bring incredible new app experiences to iPhone Duo','homeintro':'Meet Apple HomeView','homesale':'Apple HomeView available today','kit':'Introducing Apple Home Collection','race':'Formula 1','sign':'Apple TV introduces new Sign Language','siri':'Siri AI is available in more languages','health':'Redesigned Health app expands language support','dynamo':'NVIDIA Dynamo Recipes Bring Korea','fusion':'NVIDIA, Rebellions and SK Telecom','korea':'Korea\u2019s AI Ecosystem Advances','oracle':'NVIDIA and Oracle Expand','meta':'NVIDIA and Meta to Expand','nvcall':'NVIDIA Sets Conference Call','aaplreport':'Apple reports fourth quarter results','imac':'Apple unveils new iMac','homepod':'Apple announces the next generation of HomePod mini'}
# name, family, question, three source aliases, expected verdict, decisive aliases, minimal facts/confound.
SPEC=[
 ('milestones','rollout','For the launch timeline, can I list a pre-order opening before the worldwide Apple Store debut of iPhone Duo? Give the two distinct milestones.','reserve launch apps','SUPPORTED','reserve launch','Pre-order Oct 16 versus retail debut Oct 23; an app announcement is not the retail date.'),
 ('homeview','rollout','Can I describe Apple HomeView as still announcement-only, with no notice in the packet saying customers can buy it?','homeintro homesale kit','CONTRADICTED','homesale','The later availability excerpt explicitly says customers can purchase HomeView; a related home accessories lineup is not purchase evidence.'),
 ('sellthrough','rollout','Can I put a number on iPhone Duo retail sell-through from the worldwide debut and pre-order releases?','reserve launch apps','NOT ESTABLISHED','reserve launch','Pre-orders and retail availability do not give sold units or paid orders. No quantity can be inferred.'),
 ('coverage','scope','Our distribution brief treats the US Grand Prix broadcast restriction and the sign-language feature as the same coverage limitation. Is that accurate?','race sign siri','CONTRADICTED','race sign','The race notice describes the US stream; the accessibility notice limits the feature to supported titles. Geography and title eligibility differ.'),
 ('localization','scope','Can I credit both Siri AI and the redesigned Health app with expanded language support, without claiming that they gained the same languages?','siri health sign','SUPPORTED','siri health','Both releases describe expanded languages; only the Siri excerpt lists the languages. Do not transfer the list to Health.'),
 ('eligibility','scope','Does this packet establish that the US Grand Prix broadcast itself offers sign-language interpretation?','race sign health','NOT ESTABLISHED','race sign','The broadcast is on Apple TV; the feature is for supported titles. No excerpt names that race as supported.'),
 ('koreastack','deployment','For our Korea ecosystem map, can I separate the Dynamo deployment recipes for Korean models from the NVLink Fusion infrastructure collaboration? Name an example from each.','dynamo fusion korea','SUPPORTED','dynamo fusion','Released recipes for named Korean models versus NVIDIA, Rebellions and SK Telecom collaborating on infrastructure; not proof the planned infrastructure is running.'),
 ('stage','deployment','Can I write that Hyundai Motor Group and AIM Intelligence were both merely exploring blueprints, with neither described as integrating a product?','korea fusion dynamo','CONTRADICTED','korea','The excerpt says Hyundai is exploring; AIM Intelligence is integrating Starfort. Do not equate integration with a measured production result.'),
 ('capacity','deployment','Can I book the announced Oracle and NVIDIA multi-gigawatt expansion as already commissioned capacity?','oracle meta fusion','NOT ESTABLISHED','oracle','The release says plans to expand. No commissioning, installed capacity or completion is documented.'),
 ('calendar','reporting','Do the issuer notices support two different calendar entries: Apple announced its quarterly results, while NVIDIA scheduled a call to discuss its quarter?','aaplreport nvcall imac','SUPPORTED','aaplreport nvcall','Apple announces reported results; NVIDIA notice announces a future call in the wording of the notice. Do not claim the call never occurred.'),
 ('periods','reporting','The brief says the Apple fiscal quarter in these notices ended later than the NVIDIA fiscal quarter. Is that ordering right?','aaplreport nvcall homepod','CONTRADICTED','aaplreport nvcall','Apple ended Sep 26 2026, earlier than NVIDIA Oct 25 2026. The scheduled call date is not the period end.'),
 ('surprise','reporting','Does the Apple results excerpt establish that reported earnings exceeded analyst consensus for that quarter?','aaplreport imac nvcall','NOT ESTABLISHED','aaplreport','It announces results but gives neither actual earnings nor a comparable consensus estimate. A separate issuer call or product announcement is irrelevant.')]
PREFIX='Use only the archived newsroom excerpts, at the time of each notice. '
SUFFIX=' Give a short decision and why. Cite the decisive exhibits by letter next to the claims; do not list unrelated exhibits. Distinguish what is stated from what is unknown.'
def encode(obj):return json.dumps(obj,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()
def build():
 rows=[json.loads(l) for l in CACHE.read_text(encoding='utf8').splitlines() if l.strip()]; by={}
 for a,t in TITLES.items():
  hits={r['title']:r for r in rows if 'newsroom' in r['source'] and r['title'].startswith(t)}
  if len(hits)!=1:raise ValueError('Nonunique source alias '+a)
  r=next(iter(hits.values()));by[a]={k:r[k] for k in ('title','text','source','url')}
 out=[]
 for name,fam,q,srcs,gold,need,why in SPEC:
  aliases=srcs.split();random.Random('cofinance-decision-v1-'+name).shuffle(aliases);docs=[]; receipts={}; refs={}
  for letter,a in zip('ABC',aliases):
   r=by[a];raw='Archived newsroom excerpt. '+r['title']+'\n'+r['text'].strip();refs[a]=letter;docname=f'Exhibit {letter}.pdf';docs.append({'id':letter,'name':docname,'pages':[raw]});receipts[letter]=dict(**r,sha256=hashlib.sha256(raw.encode()).hexdigest())
  out.append(({'case':name,'question':PREFIX+q+SUFFIX,'documents':docs},{'family':fam,'verdict':gold,'citations':sorted(refs[a] for a in need.split()),'reason':why},receipts))
 return out
