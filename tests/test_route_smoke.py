import unittest

from core.gliner_decide import DEFAULT_ALIASES, GlinerQueryRouter
from qualify.route_smoke import QUERIES, run


class Oracle:
    """Stub backend that answers every smoke query correctly (from the expected labels)."""

    def __init__(self, wrong_intent_for=None):
        self.by_query = {q["query"]: q for q in QUERIES}
        self.wrong = wrong_intent_for

    def classify_text(self, text, schema, *, include_confidence=False):
        q = self.by_query[text]
        intent = "price_snapshot" if text == self.wrong else q["intent"]
        return {"intent": {"label": intent, "confidence": 0.9}, "horizon": {"label": "swing_week", "confidence": 0.8}}

    def extract_entities(self, text, entity_types):
        names = {v: k for k, v in DEFAULT_ALIASES.items()}
        q = self.by_query[text]
        return {"entities": {"company": [names[t] for t in q["tickers"] if t not in text]}}


class TestRouteSmoke(unittest.TestCase):
    def test_ten_queries_with_expected_labels(self):
        self.assertEqual(len(QUERIES), 10)
        universe = set(DEFAULT_ALIASES.values())
        for q in QUERIES:
            self.assertTrue(set(q["tickers"]) <= universe, q)

    def test_run_scores_intent_and_tickers(self):
        router = GlinerQueryRouter(Oracle(), universe=set(DEFAULT_ALIASES.values()))
        r = run(router, QUERIES, clock=iter(range(100)).__next__)
        self.assertEqual((r["passed"], r["n"]), (10, 10))
        self.assertEqual(r["p50_ms"], 1000.0)

    def test_wrong_intent_is_a_miss(self):
        bad = QUERIES[1]["query"]
        router = GlinerQueryRouter(Oracle(wrong_intent_for=bad), universe=set(DEFAULT_ALIASES.values()))
        r = run(router, QUERIES)
        self.assertEqual((r["passed"], r["intent_correct"], r["tickers_correct"]), (9, 9, 10))


if __name__ == "__main__":
    unittest.main()
