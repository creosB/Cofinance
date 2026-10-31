import unittest
from core.gliner_decide import GlinerDecideClassifier, GlinerQueryRouter
from core.signals import Action, GateConfig, Item, SignalGate


class StubGlinerBackend:
    def __init__(self, cls_map=None, ner_map=None):
        self.cls_map = cls_map or {}
        self.ner_map = ner_map or {}

    def classify_text(self, text, schema, *, include_confidence=False):
        for key, val in self.cls_map.items():
            if key in text:
                return val
        return {
            "materiality": {"label": "noise", "confidence": 0.90},
            "urgency": {"label": "none", "confidence": 0.92},
            "direction": {"label": "neutral", "confidence": 0.70},
            "intent": {"label": "price_snapshot", "confidence": 0.88},
            "horizon": {"label": "swing_week", "confidence": 0.75},
        }

    def extract_entities(self, text, entity_types):
        for key, val in self.ner_map.items():
            if key in text:
                return {"entities": val}
        return {"entities": {}}


class TestGlinerDecide(unittest.TestCase):
    def test_signal_classifier_drives_gate_alert_and_ignore(self):
        stub = StubGlinerBackend(
            cls_map={
                "CEO resigns": {
                    "materiality": {"label": "material", "confidence": 0.93},
                    "urgency": {"label": "immediate", "confidence": 0.84},
                    "direction": {"label": "bearish", "confidence": 0.91},
                },
                "guidance raised": {
                    "materiality": {"label": "material", "confidence": 0.78},
                    "urgency": {"label": "digest", "confidence": 0.80},
                    "direction": {"label": "bullish", "confidence": 0.86},
                },
            }
        )
        clf = GlinerDecideClassifier(stub)
        gate = SignalGate(clf, GateConfig(), clock=lambda: 1000.0)
        outcomes = gate.process(
            [
                Item("ACME", "rss", "https://x/1", "ACME CEO resigns amid probe"),
                Item("NVDA", "rss", "https://x/2", "NVDA guidance raised for Q4"),
                Item("MSFT", "rss", "https://x/3", "MSFT sponsors local marathon"),
            ]
        )
        self.assertEqual([o.action for o in outcomes], [Action.ALERT, Action.DIGEST, Action.IGNORE])
        self.assertEqual(outcomes[0].judgment.direction, "bearish")
        self.assertEqual(outcomes[1].judgment.direction, "bullish")

    def test_query_router_combines_cashtags_and_company_names(self):
        stub = StubGlinerBackend(
            cls_map={
                "Tesla": {
                    "intent": {"label": "compare_assets", "confidence": 0.91},
                    "horizon": {"label": "swing_week", "confidence": 0.80},
                }
            },
            ner_map={"Tesla": {"company": ["Tesla", "Nvidia"]}},
        )
        router = GlinerQueryRouter(stub, universe={"TSLA", "NVDA", "BTC", "SOL"})
        routed = router.route("How does Tesla compare to Nvidia and $sol this week?")
        self.assertEqual(routed.intent, "compare_assets")
        self.assertEqual(routed.tickers, ["SOL", "TSLA", "NVDA"])

    def test_missing_or_unknown_head_fails_closed(self):
        # Regression: a missing materiality head used to become material_p = 1.0 (fail-open).
        bad_outputs = [
            {"urgency": {"label": "immediate", "confidence": 0.95},
             "direction": {"label": "bearish", "confidence": 0.9}},                       # no materiality
            {"materiality": {"label": "maybe", "confidence": 0.99},
             "urgency": {"label": "immediate", "confidence": 0.95},
             "direction": {"label": "bearish", "confidence": 0.9}},                       # unknown label
            {"materiality": [{"label": "material", "confidence": 0.9}],
             "urgency": {"label": "immediate", "confidence": 0.95},
             "direction": {"label": "bearish", "confidence": 0.9}},                       # multi-label list
            {},
        ]
        for i, out in enumerate(bad_outputs):
            stub = StubGlinerBackend(cls_map={"halted": out})
            gate = SignalGate(GlinerDecideClassifier(stub), GateConfig(), clock=lambda: 1000.0)
            o = gate.process([Item("ACME", "rss", f"https://x/{i}", "ACME halted")])[0]
            self.assertEqual(o.action, Action.DEFERRED, out)
            self.assertTrue(o.reason.startswith("classifier_error:GlinerOutputError"), o.reason)


if __name__ == "__main__":
    unittest.main()
