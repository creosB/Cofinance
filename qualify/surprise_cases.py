"""Synthetic semantic fixtures. Expectation is about financial surprise, not future prices."""
from hashlib import sha256
from random import Random

# Each tuple declares the actual, the expectation it beats, and the expectation it misses.
# Fixed fixture facts; the scoring key is not inferred with the candidate's arithmetic.
FACTS = [
 ('revenue growth','10%','5%','15%'), ('customer churn','4%','6%','2%'),
 ('operating margin','18%','16%','20%'), ('expense growth','7%','9%','5%'),
 ('earnings per share','$1.20','$0.90','$1.50'), ('loss per share','$0.40','$0.60','$0.20'),
 ('unit sales','120 million','100 million','140 million'), ('free cash flow','$90 million','$70 million','$110 million')]
# Independently worded, locked challenge with positive/negative pairs and insufficient evidence.
# No examples from this table appear in the prompts.
CHALLENGE = [
 ('revenue growth', 'Sales expanded 10% in the quarter, above the Street\'s 8% consensus.', 'Sales expanded 10% in the quarter, below the Street\'s 12% consensus.', 'Sales expanded 10% in the quarter after growing 8% a year ago.'),
 ('customer churn','Q2: 3 of every 100 subscribers left; analysts had pencilled in 5 departures per 100.','Q2: 3 of every 100 subscribers left; analysts had pencilled in 2 departures per 100.','Q2: 3 of every 100 subscribers left; management targets 2 per 100 next year.'),
 ('operating margin','Operating margin came in at 1,850 basis points against a consensus of 18% for the quarter.','Operating margin came in at 1,850 basis points against a consensus of 19% for the quarter.','Operating margin was 18.5% for the quarter; consensus also called for 18.5%.'),
 ('expense growth','Consensus had operating costs rising 9% this quarter. The reported increase was 7%.','Consensus had operating costs rising 5% this quarter. The reported increase was 7%.','Operating costs rose 7% this quarter. The Street had expected revenue to rise 9%.'),
 ('earnings per share','Analysts expected a quarterly loss of $0.05 a share. The company broke even.','Analysts expected quarterly earnings of $0.05 a share. The company broke even.','The company broke even per share, the same result analysts had pencilled in.'),
 ('loss per share','Per-share loss narrowed to $0.40. Analysts were looking for a $0.60 loss this quarter.','Per-share loss narrowed to $0.40. Analysts were looking for a $0.20 loss this quarter.','Per-share loss narrowed to $0.40 from $0.60 a year ago; no consensus was given.'),
 ('unit sales','The Street wanted 120m devices this quarter. Shipments reached 125m.','The Street wanted 130m devices this quarter. Shipments reached 125m.','The Street wanted 120m devices next quarter. Shipments reached 125m this quarter.'),
 ('free cash flow','Quarterly free cash flow was negative $10m; the consensus forecast had been negative $20m.','Quarterly free cash flow was negative $10m; the consensus forecast had been positive $5m.','Quarterly free cash flow was negative $10m. A survey expected positive $5m in operating profit.')]


def corpus():
    blind=[]; key={}
    def add(fam,style,condition,expected,text,split):
        title='Northline: '+text; id_=sha256(title.encode()).hexdigest()[:20]
        if id_ in key: raise ValueError('Duplicate blind item')
        blind.append({'id':id_,'asset':'NLINE','title':title}); key[id_]={'family':fam,'style':style,'case':condition,'expected':expected,'split':split}
    for i,(m,x,beat,miss) in enumerate(FACTS):
        for style in ('report_first','forecast_first'):
            actual=(f'Q2 {m} was reported at {x}' if style=='report_first' else f'Q2 {m} came in at {x}')
            for condition,gold,n in [('beat','UP',beat),('miss','DOWN',miss),('equal','ABSTAIN',x),('no_consensus','ABSTAIN',None),('wrong_period','ABSTAIN',beat),('wrong_measure','ABSTAIN',miss),('historical_only','ABSTAIN',miss),('conflicting','ABSTAIN',beat)]:
                metric=FACTS[(i+1)%len(FACTS)][0] if condition=='wrong_measure' else m; period='Q3' if condition=='wrong_period' else 'Q2'
                fc=(f'analysts expected {period} {metric} of {n}' if style=='report_first' else f'consensus for {period} {metric} stood at {n}')
                if condition=='historical_only': fc=f'last year Q2 {m} was {n}'
                if condition=='no_consensus': fc='no analyst consensus was supplied'
                if condition=='conflicting': fc+=f'; a separate consensus for Q2 {m} stood at {miss}'
                # An unrelated historical number stays fixed within each beat/miss pair.
                clauses=([actual,fc] if style=='report_first' else [fc,actual])+[f'last year Q2 {m} was {x}']
                add(m,style,condition,gold,'; '.join(clauses)+'.','controlled')
    for fam,better,worse,neutral in CHALLENGE:
        for condition,expected,txt in [('beat','UP',better),('miss','DOWN',worse),('neutral','ABSTAIN',neutral)]: add(fam,'challenge',condition,expected,txt,'challenge')
    Random(17029).shuffle(blind); return blind,key
