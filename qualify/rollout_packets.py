"""Local source-grounded release decisions. Archive texts are reused; questions are new.
The augmentation split is authored, not issuer evidence. Gold concerns the supplied complete packet only.
"""
import hashlib,json,random
from pathlib import Path
CACHE=Path('data/replay_20261116/news_gated.jsonl');A='LIVE';N='NOT_LIVE';U='NOT_ESTABLISHED'
# The three selectors are title prefixes, not inferred event types. IDs are shuffled before generation.
ARCH=[
 ('streaming','Phantom Blade Zero','GeForce NOW','Can a subscriber mark this game playable on that service now, rather than on a future roadmap?',('Slice Into GFN','A New Dawn in the Cloud','NVIDIA Unveils Minerva'),A,(0,)),
 ('streaming','Warhammer 40,000: Dawn of War IV','GeForce NOW','Does the supplied game notice establish that streaming access has actually opened?',('A New Dawn in the Cloud','Slice Into GFN','Football Manager and other'),U,(0,)),
 ('retail','RTX Spark laptops','Dell','Can a procurement researcher list Dell as an available supplier for this laptop family?',('NVIDIA RTX Spark Windows','NVIDIA Unveils Minerva','Apple unveils new iMac'),A,(0,)),
 ('retail','Apple Home Collection','retail stores','Can the buyer treat the collection as already on sale in retail stores rather than merely unveiled?',('Introducing Apple Home Collection','Apple HomeView available today','Apple announces the next generation'),U,(0,)),
 ('launch','NVIDIA Minerva','','Has the vendor reported launching the platform, irrespective of how many customers adopted it?',('NVIDIA Unveils Minerva','NVIDIA and Meta to Expand','Introducing Apple Home Collection'),A,(0,)),
 ('launch','Muse','production endpoints','Does this partnership notice report a production endpoint rollout for Muse?',('NVIDIA and Meta to Expand','NVIDIA Unveils Minerva','NVIDIA RTX Spark Windows'),U,(0,)),
 ('streaming','Football Manager 27 Touch','Apple Arcade','Can the catalog editor list this named game as a current addition, separate from the other holiday games?',('Football Manager and other','Slice Into GFN','A New Dawn in the Cloud'),A,(0,)),
 ('retail','iMac','retail stores','Do the notices actually confirm that customers can buy the new iMac in retail stores?',('Apple unveils new iMac','Apple introduces the new 14-inch','iPhone Duo arrives at Apple Store'),U,(0,)),
 ('retail','iPhone Duo','Apple Store locations worldwide','For a retail availability ledger, is the physical-store debut supported by an actual arrival report?',('iPhone Duo arrives at Apple Store','Open up to new possibilities','Apple HomeView available today'),A,(0,)),
 ('retail','iPhone Duo','Apple Store locations worldwide','At the snapshot shown here, is a physical-store debut evidenced by more than an opportunity to reserve?',('Open up to new possibilities','Introducing Apple Home Collection','Apple announces the next generation'),U,(0,)),
 ('launch','Apple HomeView','','Can this product be marked available to purchase rather than only introduced?',('Apple HomeView available today','Meet Apple HomeView','Apple unveils the new Apple TV'),A,(0,)),
 ('launch','HomePod mini 2','','Is the new speaker documented as already available, as distinct from Apple having announced it?',('Apple announces the next generation','Meet Apple HomeView','Apple HomeView available today'),U,(0,))]
# Separate controls hold a named place/product fixed while switching authority, scope, timing or event.
CHALLENGE=[
 ('explicit','Alder Relay','Ireland','The public rollout note says Alder Relay is now available in Ireland.','Alder Relay was announced for France.','Beta participants were thanked for feedback.',A,(0,)),
 ('explicit','Alder Relay','Ireland','Alder Relay is not yet available in Ireland.','Alder Relay is now available in France.','The service has a new landing page.',N,(0,)),
 ('limited','Cedar Maps','Sweden','Cedar Maps is now available in Norway.','Cedar Maps was announced for Sweden.','The invitations were issued to the regional press.',U,(1,)),
 ('limited','Cedar Maps','Sweden','Cedar Maps is now available in Norway.','Cedar Maps is not yet available in Sweden.','The invitations were issued to the regional press.',N,(1,)),
 ('correction','Fern Desk','Italy','Fern Desk is now available in Italy.','Correction to the launch bulletin: Fern Desk has not launched in Italy.','The product team presented its annual roadmap.',N,(0,1)),
 ('correction','Fern Desk','Italy','Fern Desk is now available in Italy.','A separate bulletin says Fern Desk has not launched in Italy.','The product team presented its annual roadmap.',U,(0,1)),
 ('limited','Lime Board','general public','Lime Board has entered a closed pilot.','Lime Board is not yet available to the general public.','Participants can send feedback from the website.',N,(1,)),
 ('limited','Lime Board','general public','Lime Board has entered a closed pilot.','Lime Board is expected to reach the general public next season.','Participants can send feedback from the website.',U,(0,1)),
 ('explicit','Myrtle Studio','Finland','Myrtle Studio is now available in Finland.','Myrtle Studio was announced for Finland.','A partner briefing covered the installation process.',A,(0,)),
 ('explicit','Sage Console','Portugal','Sage Console can now be purchased in Portugal.','Sage Console was announced for Spain.','Developer previews are offered separately.',A,(0,)),
 ('correction','Elm Panel','Brazil','Elm Panel is now available in Brazil.','The reported launch date for Elm Panel is under review.','The launch team has not released a reconciliation.',U,(0,1)),
 ('limited','Birch Cloud','Canada','Birch Cloud is now available in Canada for invited testers only.','The question concerns the public release, not private testing.','A partner described a later release as a possibility.',U,(0,))]

def enc(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
def digest(x):return hashlib.sha256(x).hexdigest()
def write(p,x):
 with Path(p).open('xb') as f:f.write(enc(x))
def build():
 rows=[json.loads(x) for x in CACHE.read_text(encoding='utf8').splitlines() if x.strip()];by={r['title']:r for r in rows if 'newsroom' in r['source'].lower()};out=[]
 def add(split,idx,fam,sub,scope,q,docs,g,inds):
  ids=list('ABC');rng=random.Random(int(digest((split+str(idx)).encode())[:12],16));rng.shuffle(ids);packet=[dict(id=ids[i],**d) for i,d in enumerate(docs)];packet.sort(key=lambda d:d['id']);out.append(dict(case=f'{split}{idx:02d}',split=split,family=fam,subject=sub,scope=scope,question=q,documents=packet,gold={'verdict':g,'sources':sorted(ids[i] for i in inds)}))
 for i,(fam,sub,scope,q,sel,g,inds) in enumerate(ARCH):
  docs=[]
  for prefix in sel:
   matches=[r for t,r in by.items() if t.startswith(prefix)];assert len(matches)==1,(prefix,len(matches));r=matches[0];docs.append(dict(title=r['title'],body=r['text'],url=r['url']))
  add('archive',i,fam,sub,scope,q,docs,g,inds)
 for i,(fam,sub,scope,x,y,z,g,inds) in enumerate(CHALLENGE):
  q=f'Does this packet establish that {sub} completed its public rollout for {scope}?';docs=[dict(title=t,body=b,url='') for t,b in zip(('Launch bulletin','Service update','Context note'),(x,y,z))];add('contrast',i,fam,sub,scope,q,docs,g,inds)
 return out

def freeze_labels(root):
 root=Path(root);root.mkdir(exist_ok=False);cs=build();assert len(cs)==24 and len({c['case'] for c in cs})==24;write(root/'cases.json',cs);p={'labels_sha256':digest(enc(cs)),'case_code':digest(Path(__file__).read_bytes()),'source_sha256':digest(CACHE.read_bytes()),'gates':'Primary: exact verdict and minimal decisive exhibit set, including sources explaining uncertainty. Track false LIVE and missing determinate cases separately. Compare rules vs strong direct Q&A on identical task, metadata and exhibits. Advance only to an independent test if >=2 more joint correct out of 24, not worse on either split, strictly fewer false LIVE, no more missed determinate cases. Tie or valid miss: no promotion. If generation invalid >1/24 answers or any input missing: inconclusive. No production adoption.','limits':'Local one-look, author-built questions and narrow grammar. Prior cached notices reused; synthetic controls and two related software/product families are not unseen field data. Positive words alone are not proof of completed scope. Sources refer to excerpts, not external truth.'};write(root/'labels_protocol.json',p);print('labels',p['labels_sha256'])
