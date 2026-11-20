import random,unittest
from qualify.surprise_scope import checked

class TestStrictScope(unittest.TestCase):
    def test_assertion_not_just_matching_numbers(self):
        ok='Arc Vale Q3 operating margin was reported at 15%; analysts expected Q3 operating margin of 12% for Arc Vale.';self.assertEqual(checked(ok,'Arc Vale')[0],'UP')
        for text in (ok.replace('was reported','was never reported'),ok+' The report is hypothetical.',ok.replace('Q3 operating','Q3 potential operating',1),ok.replace('for Arc Vale','for Peer Corp'),ok.replace('Arc Vale Q3','Peer Corp Q3'),ok.replace('of 12%','of roughly 12%')):
            self.assertEqual(checked(text,'Arc Vale')[0],'ABSTAIN',text)
    def test_accounting_basis_must_match(self):
        a='Arc Vale: Q1 non-GAAP earnings per share came in at $1.25; consensus for Q1 non-GAAP earnings per share stood at $1.10';self.assertEqual(checked(a,'Arc Vale')[0],'UP');self.assertEqual(checked(a.replace('consensus for Q1 non-GAAP','consensus for Q1 GAAP'),'Arc Vale'),('ABSTAIN','unlike_scope'));self.assertEqual(checked(a.replace('consensus for Q1 non-GAAP','consensus for Q1'),'Arc Vale')[0],'ABSTAIN')
    def test_binding_cannot_be_inferred_from_unrelated_context(self):
        t='Q4 expense growth came in at 4%; analysts expected Q4 expense growth of 3%';self.assertEqual(checked(t,'Arc Vale')[1],'unbound_issuer');self.assertEqual(checked('Arc Vale: '+t,'Arc Vale')[0],'DOWN');self.assertEqual(checked('Another: '+t,'Arc Vale')[0],'ABSTAIN');self.assertEqual(checked('Arc Vale: '+t,None)[0],'ABSTAIN')
    def test_distractors_duplicate_mixed_currency_and_flat(self):
        t='Vale: Q4 loss per share came in at $0.20; analysts expected Q4 loss per share of $0.20';self.assertEqual(checked(t,'Vale'),('ABSTAIN','checked_verified_equal'));self.assertEqual(checked(t+'; last year Q4 loss per share was $3.50','Vale')[1],'checked_verified_equal');self.assertEqual(checked(t+'; analysts expected Q4 loss per share of $0.10','Vale')[1],'duplicate_role');self.assertEqual(checked(t.replace('$0.20','EUR 0.20',1),'Vale')[0],'ABSTAIN')
    def test_metamorphic_arithmetic_independent_of_format_order_and_basis(self):
        rng=random.Random(117);families=[('operating margin',1),('customer churn',-1),('expense growth',-1),('revenue growth',1)]
        for _ in range(256):
            m,s=rng.choice(families);x,y=rng.randrange(1,20),rng.randrange(1,20);b=rng.choice(['','adjusted ','unadjusted ']);q='Q'+str(rng.randrange(1,5)); issuer=rng.choice(['Nova','Orchid Labs']); ac=f'{q} {b}{m} came in at {100*x} basis points';ex=f'analysts expected {q} {b}{m} of {y}%';expected='UP' if s*(x-y)>0 else 'DOWN' if s*(x-y)<0 else 'ABSTAIN'
            for cs in ((ac,ex),(ex,ac)):self.assertEqual(checked(issuer+': '+'; '.join(cs),issuer)[0],expected)
            self.assertEqual(checked(issuer+': '+ac+'; '+ex+' for Someone Else',issuer)[0],'ABSTAIN')
