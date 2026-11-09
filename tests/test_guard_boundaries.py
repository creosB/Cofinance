import unittest
from core.signals import Action, GateConfig, Item, Judgment, SignalGate
from core.risk_cues import unsupported_risks

class TestInvalidJudgments(unittest.TestCase):
    def test_bad_scores_never_alert_or_become_seen(self):
        bad=[Judgment(float('nan'),.5),Judgment(.6,float('inf')),Judgment(-.1,.5),Judgment(1.1,.5),
             Judgment(True,.5),Judgment(.6,.5,direction_p=float('nan')),Judgment(.6,.5,direction='made_up'),
             Judgment(.6,.5,alert_allowed='yes'),None,{}]
        item=Item('AAPL','rss','https://example.test/earnings','Apple reports fourth quarter results')
        for mode in ('model','rules'):
            for value in bad:
                with self.subTest(mode=mode,value=value):
                    class Judge:
                        def judge(self,it):return value
                    gate=SignalGate(Judge(),GateConfig(urgency_source=mode))
                    self.assertEqual(gate.process([item])[0].action,Action.DEFERRED)
                    self.assertNotIn(item.key,gate.seen)
                    self.assertEqual(gate.last_alert,{})

class TestRiskNegation(unittest.TestCase):
    CUES=[{'text':'below its high','anchors':['drawdown','all-time high']}]
    def test_negation_cannot_authorize_an_invented_risk(self):
        for text in ['The risk is not only drawdown but also supply-chain disruption.',
                     'This is not without execution risk.',
                     'The risk is not small: supply-chain disruption.',
                     'The reports do not identify a specific risk and execution gaps remain a concern.']:
            self.assertTrue(unsupported_risks(text,self.CUES),text)
    def test_pure_no_risk_statement_is_allowed(self):
        for text in ['The reports do not identify a specific risk.','The evidence cannot establish any risk.']:
            self.assertEqual(unsupported_risks(text,[]),[],text)
    def test_supported_risk_and_no_risk_statement_stay_allowed(self):
        self.assertEqual(unsupported_risks('The reports do not identify a specific risk. Main risk: drawdown.',self.CUES),[])

if __name__=='__main__':unittest.main()
