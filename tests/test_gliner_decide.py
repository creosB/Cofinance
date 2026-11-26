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

    def test_judge_batch_batches_calls_isolates_malformed_items_and_preserves_gate_order(self):
        class BatchStub(StubGlinerBackend):
            def __init__(self, cls_map=None, fail_batch=False):
                super().__init__(cls_map=cls_map)
                self.batch_calls = []
                self.single_calls = []
                self.fail_batch = fail_batch

            def classify_text(self, text, schema, *, include_confidence=False):
                self.single_calls.append(text)
                return super().classify_text(text, schema, include_confidence=include_confidence)

            def batch_classify_text(self, texts, schema, *, batch_size=6, include_confidence=False):
                self.batch_calls.append(list(texts))
                if self.fail_batch:
                    raise RuntimeError("batch endpoint failed")
                return [super(BatchStub, self).classify_text(t, schema, include_confidence=include_confidence) for t in texts]

        cls_map = {
            "CEO resigns": {
                "materiality": {"label": "material", "confidence": 0.93},
                "urgency": {"label": "immediate", "confidence": 0.84},
                "direction": {"label": "bearish", "confidence": 0.91},
            },
            "bad schema": {},
            "guidance raised": {
                "materiality": {"label": "material", "confidence": 0.78},
                "urgency": {"label": "digest", "confidence": 0.80},
                "direction": {"label": "bullish", "confidence": 0.86},
            },
        }
        stub = BatchStub(cls_map=cls_map)
        clf = GlinerDecideClassifier(stub)
        gate = SignalGate(clf, GateConfig(max_judgments=3), clock=lambda: 1000.0)
        items = [
            Item("ACME", "rss", "https://x/1", "ACME CEO resigns"),
            Item("NVDA", "rss", "https://x/2", "NVDA bad schema"),
            Item("MSFT", "rss", "https://x/3", "MSFT guidance raised"),
            Item("AAPL", "rss", "https://x/4", "AAPL over budget"),
        ]
        outcomes = gate.process(items)
        self.assertEqual([o.action for o in outcomes], [Action.ALERT, Action.DEFERRED, Action.DIGEST, Action.DEFERRED])
        self.assertEqual(outcomes[1].reason, "classifier_error:GlinerOutputError")
        self.assertEqual(outcomes[3].reason, "judgment_budget")
        self.assertEqual(len(stub.batch_calls), 1)
        self.assertEqual(len(stub.batch_calls[0]), 3)
        self.assertEqual(stub.single_calls, [])

        fallback_stub = BatchStub(cls_map=cls_map, fail_batch=True)
        fallback_outcomes = SignalGate(GlinerDecideClassifier(fallback_stub), GateConfig(), clock=lambda: 1000.0).process(items[:2])
        self.assertEqual([o.action for o in fallback_outcomes], [Action.ALERT, Action.DEFERRED])
        self.assertEqual(len(fallback_stub.single_calls), 2)

    def test_batch_gate_flushes_before_intra_call_duplicate_and_preserves_trace_links(self):
        from core.collector import FetchResult
        from core.run_trace import TraceRecorder, run_traced_clock1

        class BatchStub(StubGlinerBackend):
            def __init__(self):
                super().__init__(cls_map={
                    "CEO resigns": {
                        "materiality": {"label": "material", "confidence": 0.93},
                        "urgency": {"label": "immediate", "confidence": 0.84},
                        "direction": {"label": "bearish", "confidence": 0.91},
                    },
                })
                self.batch_calls = []

            def batch_classify_text(self, texts, schema, *, batch_size=6, include_confidence=False):
                self.batch_calls.append(list(texts))
                return [self.classify_text(t, schema, include_confidence=include_confidence) for t in texts]

        stub = BatchStub()
        clf = GlinerDecideClassifier(stub)
        dup = Item("ACME", "rss", "https://x/dup", "ACME CEO resigns")
        other = Item("NVDA", "rss", "https://x/other", "NVDA routine update")
        outs = SignalGate(clf, GateConfig(), clock=lambda: 1000.0).process([dup, other, dup])
        self.assertEqual([(o.action, o.reason) for o in outs], [
            (Action.ALERT, "material_and_urgent"),
            (Action.IGNORE, "below_digest_threshold"),
            (Action.IGNORE, "duplicate"),
        ])
        self.assertEqual(len(stub.batch_calls), 1)
        self.assertEqual(len(stub.batch_calls[0]), 2)

        trace = TraceRecorder("clock1-batch-test")
        items = [
            Item("NVDA", "rss", "https://x/n1", "NVDA CEO resigns"),
            Item("NVDA", "rss", "https://x/n2", "NVDA routine note"),
        ]
        run_traced_clock1(
            ["NVDA"],
            lambda tkr: FetchResult(items=items, source_status={"rss": "ok"}),
            GlinerDecideClassifier(stub),
            trace,
            clock=lambda: 1000.0,
        )
        names = [sp["name"] for sp in trace.data["spans"]]
        self.assertIn("Classify heads (batch)", names)
        decisions = [sp for sp in trace.data["spans"] if sp["name"] == "Judge headline"]
        self.assertEqual(len(decisions), 2)
        gates = [sp for sp in trace.data["spans"] if sp["kind"] == "gate" and sp["name"].startswith("Signal gate:")]
        self.assertEqual(len(gates), 2)
        decision_ids = {sp["id"] for sp in decisions}
        self.assertTrue(all(g.get("parent_id") in decision_ids for g in gates))


if __name__ == "__main__":
    unittest.main()
