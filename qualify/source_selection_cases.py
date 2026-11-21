"""Source selection only. Local archived issuer statements are not independently verified events."""
from pathlib import Path
import json,hashlib,re,random
from qualify import rollout_packets as P,rollout_evidence as V
# query, primary subject, geographic/semantic qualifier, 3 archive title prefixes, minimal evidence facets.
# Each facet can have equivalent documentary witnesses. The first archive notice is not always decisive after shuffling.
ARCH=[
 ('What does the packet actually say about new training resources in the Cosmos platform? Select the statement naming the kinds of resources.', 'Cosmos','training tools',('NVIDIA Cosmos Opens','How Companies Are Using NVIDIA DSX','Apple Creator Studio adds'),[['0:body:has expanded the Cosmos platform']]),
 ('Which passage describes what organizations are building on DSX to do, rather than the general power problem?', 'DSX','energy infrastructure',('How Companies Are Using NVIDIA DSX','Cities Worldwide Build','NVIDIA Cosmos Opens'),[['0:body:Companies around the world are building']]),
 ('Identify the statement that ties a named bank to a particular foundation model and its technology stack.', 'Revolut','technology stack',('Leading European Financial Services','NVIDIA Cosmos Opens','From Chips to Skills'),[['0:body:Revolut is rebuilding']]),
 ('For the technical brief, find the evidence for the timing of Aerial being open sourced and its connection to Sionna.', 'Aerial','wireless software',('How NVIDIA Open Source Tools Are Helping','NVIDIA Expands Open Source Commitment','How Companies Are Using NVIDIA DSX'),[['0:body:A year ago, NVIDIA open sourced its Aerial']]),
 ('Where does the company specify both the timing and the rendering feature of the Hogwarts Legacy upgrade?', 'Hogwarts Legacy','GeForce NOW',('‘Hogwarts Legacy’ Gets','Cloud Deployment Incoming','How NVIDIA Open Source Tools Are Helping'),[['0:body:Hogwarts Legacy receives an NVIDIA DLSS']]),
 ('Find the issuer claim about suppliers already providing components for both named phone models, not a claim that either is in stores.', 'iPhone Duo','components',('Apple announces American Manufacturing','Apple gives iPad mini','Apple Creator Studio adds'),[['0:body:are supplying critical materials']]),
 ('Select the passage that describes the display and chip in the new iPad mini; a product announcement without the hardware details is insufficient.', 'iPad mini','OLED',('Apple gives iPad mini','Apple unveils groundbreaking MacBook','Apple Creator Studio adds'),[['0:body:featuring an OLED display']]),
 ('Find the passages that identify both an updated creative-app collection and the specific technology in a different computer’s display.', 'Apple Creator Studio','',('Apple Creator Studio adds','Apple unveils groundbreaking MacBook','Apple unveils the 2026 Designers'),[['0:body:Apple introduces updates to Apple Creator Studio','0:title:Apple Creator Studio adds powerful new AI features'],['1:body:featuring breakthrough tandem OLED']])]
# Separate multifacet challenges. An unmentioned or currently unknowable outcome is not automatically negative evidence.
CONTRAST=[
 ('qualification','Aster Mill','Chile','Which passages are needed to report the expansion and the exception to its public scope?', [('City note','Aster Mill opened enrollment across Chile. Accounts remain invitation-only outside Santiago.'),('Style guide','Aster Mill has a new icon.'),('Support hours','The support inbox opens on Mondays.')], [['0:body:opened enrollment'],['0:body:Accounts remain invitation-only']], [1]),
 ('qualification','Aster Mill','Chile','Select only evidence about who can enroll, not changes to the icon or support hours.', [('City note','Aster Mill opened enrollment across Chile. Staff changed the icon overnight.'),('Style guide','Aster Mill has a new icon.'),('Support hours','The support inbox opens on Mondays.')], [['0:body:opened enrollment']], []),
 ('withdrawal','Ember Panel','Iceland','Which passages show the originally announced date and whether that announcement still stands?', [('Release note','Ember Panel public access begins in July in Iceland.'),('Erratum','The July Iceland public-access note has been withdrawn pending sign-off.'),('Billing','Ember Panel invoices go out on the fifth.')],[['0:body:public access begins'],['1:body:has been withdrawn']],[1]),
 ('withdrawal','Ember Panel','Iceland','Find the public access timing in the packet; a retraction about billing does not qualify.', [('Release note','Ember Panel public access begins in July in Iceland.'),('Erratum','The May billing note has been withdrawn pending sign-off.'),('Billing','Ember Panel invoices go out on the fifth.')],[['0:body:public access begins']],[]),
 ('conflict','Heron Desk','Italy','Select both sides if accounts of public access in Italy conflict.', [('Retail note','Heron Desk is on sale to Italian residents.'),('Operations note','In Italy the storefront is still closed to the public.'),('Forecast','The team expects interest to grow.')],[['0:body:on sale'],['1:body:storefront is still closed']],[1]),
 ('conflict','Heron Desk','Italy','Identify evidence for Italian availability, not notices from a different market.', [('Retail note','Heron Desk is on sale to Italian residents.'),('Operations note','In Latvia the storefront is still closed to the public.'),('Forecast','The team expects interest to grow.')],[['0:body:on sale']],[]),
 ('empty','Juniper Wave','Portugal','Find any statement that actually addresses public availability of Juniper Wave in Portugal. If there is none, select nothing.', [('Index','The Juniper Wave manual has a new index.'),('Maintenance','Editors fixed a typo in the changelog.'),('Portugal','Support agents in Portugal moved offices.')],[],[]),
 ('empty','Juniper Wave','Portugal','Find any statement that actually addresses public availability of Juniper Wave in Portugal. If there is none, select nothing.', [('Index','The Juniper Wave manual has a new index.'),('Maintenance','Residents in Portugal can already use Juniper Wave without an invitation.'),('Portugal','Support agents in Portugal moved offices.')],[['1:body:can already use']],[]),
 ('scope','Cobalt Room','Greece','Select both the access report and the restriction on the population counted in it.', [('Access','Cobalt Room opened its Greek service on Monday.'),('Panel composition','The access list referred only to registered trial participants.'),('Training','A training seminar took place in Greece.')],[['0:body:opened its Greek service'],['1:body:only to registered trial']],[1]),
 ('scope','Cobalt Room','Greece','Find the Greek service opening notice; exclude unrelated trial data about another product.', [('Access','Cobalt Room opened its Greek service on Monday.'),('Panel composition','The access list for Wren Clock referred only to registered trial participants.'),('Training','A training seminar took place in Greece.')],[['0:body:opened its Greek service']],[])]

def enc(x):return P.enc(x)
def sha(x):return hashlib.sha256(x).hexdigest()
def index(docs):
 out=[]
 for d in docs:
  for field in ('title','body'):
   text=d[field];h=sha(text.encode());
   for j,(a,b,q) in enumerate(V.parts(text),1):out.append(dict(id=f"{d['id']}{'T' if field=='title' else 'S'}{j}",doc=d['id'],field=field,start=a,end=b,text=q,sha=h))
 return out

def build():
 rows=[json.loads(l) for l in P.CACHE.read_text(encoding='utf8').splitlines() if l.strip()];pool={r['title']:r for r in rows if 'newsroom' in r['source'].lower()};cs=[]
 def add(case,split,fam,subject,scope,question,src,facets,counters):
  letters=list('ABC');random.Random(int(sha(case.encode())[:12],16)).shuffle(letters);docs=sorted([dict(id=l,**d) for l,d in zip(letters,src)],key=lambda d:d['id']);sp=index(docs);groups=[]
  for facet in facets:
   items=[]
   for opt in facet:
    n,field,needle=opt.split(':',2);found=[s['id'] for s in sp if s['doc']==letters[int(n)] and s['field']==field and needle.casefold() in s['text'].casefold()];assert len(found)==1,(case,opt,found);items+=found
   groups.append(sorted(set(items)))
  assert len(set(x for group in groups for x in group))==sum(map(len,groups));cs.append(dict(id=case,split=split,family=fam,subject=subject,scope=scope,question=question,documents=docs,facets=groups,counter_facets=counters))
 for i,(query,sub,scope,sel,gold) in enumerate(ARCH):
  ds=[]
  for s in sel:
   hits=[r for t,r in pool.items() if t.startswith(s)];assert len(hits)==1,(s,len(hits));r=hits[0];ds.append(dict(title=r['title'],body=r['text'],url=r['url']))
  add('issuer'+str(i),'issuer','source_detail' if i<4 else 'product_detail',sub,scope,query,ds,gold,[])
 for i,(fam,sub,scope,q,ds,gs,co) in enumerate(CONTRAST):add('contrast'+str(i),'contrast',fam,sub,scope,q,[dict(title=t,body=b,url='') for t,b in ds],gs,co)
 return cs
