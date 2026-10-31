import unittest
from core.signals import Judgment
from qualify.decide_eval import adopt_adjudicator, ece, evaluate_classifier, load_rows
from qualify.scorers import score, sentence_count


class Table:
    def __init__(self, table):
        self.table = table

    def judge(self, item):
        v = self.table[item.title]
        if isinstance(v, Exception):
            raise v
        return v


ROWS = [
    {"id": "a", "ticker": "ACME", "title": "deal", "material": True, "urgent": True},
    {"id": "b", "ticker": "ACME", "title": "raise", "material": True},
    {"id": "c", "ticker": "ACME", "title": "charity", "material": False},
    {"id": "d", "ticker": "ACME", "title": "inject", "material": False, "trap": "injection"},
]


class TestDecideEval(unittest.TestCase):
    def test_metrics(self):
        clf = Table({"deal": Judgment(0.95, 0.9), "raise": Judgment(0.7, 0.1),
                     "charity": Judgment(0.1, 0.0), "inject": Judgment(0.9, 0.8)})
        r = evaluate_classifier(clf, ROWS, clock=iter(range(100)).__next__)
        self.assertEqual(r["accuracy"], 0.75)
        self.assertEqual(r["false_alert_rate"], 0.5)          # the injection item alerted
        self.assertEqual(r["alert_recall"], 1.0)
        self.assertEqual(r["trap_accuracy"], 0.0)
        self.assertAlmostEqual(r["brier"], (0.05**2 + 0.3**2 + 0.1**2 + 0.9**2) / 4)

    def test_errors_count_as_wrong(self):
        clf = Table({"deal": TimeoutError("x"), "raise": Judgment(0.7, 0.1),
                     "charity": Judgment(0.1, 0.0), "inject": Judgment(0.1, 0.0)})
        r = evaluate_classifier(clf, ROWS)
        self.assertEqual((r["errors"], r["accuracy"]), (1, 0.75))

    def test_ece(self):
        self.assertAlmostEqual(ece([0.9, 0.9], [True, False]), 0.4)
        self.assertIsNone(ece([], []))

    def test_adopt_rule(self):
        base = {"macro_f1": 0.70, "false_alert_rate": 0.10, "errors": 0}
        self.assertTrue(adopt_adjudicator(base, {"macro_f1": 0.76, "false_alert_rate": 0.10, "errors": 0})["adopt"])
        self.assertTrue(adopt_adjudicator(base, {"macro_f1": 0.71, "false_alert_rate": 0.06, "errors": 0})["adopt"])
        self.assertFalse(adopt_adjudicator(base, {"macro_f1": 0.72, "false_alert_rate": 0.09, "errors": 0})["adopt"])
        self.assertFalse(adopt_adjudicator(base, {"macro_f1": 0.80, "false_alert_rate": 0.12, "errors": 0})["adopt"])

    def test_seed_headlines(self):
        rows = load_rows("qualify/headlines.jsonl")
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        self.assertEqual(sum(r["material"] for r in rows) * 2, len(rows))   # balanced
        self.assertGreaterEqual(sum(1 for r in rows if r.get("trap")), 8)


class TestNarration(unittest.TestCase):
    CASE = {"check": "narrate", "allowed_numbers": [41.20, 39.80, 18.4], "max_sentences": 2}

    def test_sentence_count(self):
        self.assertEqual(sentence_count("ACME closed at 41.20. It trades at 18.4 times earnings."), 2)
        self.assertEqual(sentence_count(""), 0)

    def test_narrate(self):
        self.assertTrue(score(self.CASE, "ACME closed at 41.20, up from 39.80.")["passed"])
        self.assertFalse(score(self.CASE, "ACME rose 3.5% to 41.20.")["passed"])                     # invented number
        self.assertFalse(score(self.CASE, "ACME closed at 41.20. Before 39.80. P/E 18.4.")["passed"])  # too long
        self.assertFalse(score(self.CASE, "<think>still going")["passed"])                           # thinking never closed


if __name__ == "__main__":
    unittest.main()
