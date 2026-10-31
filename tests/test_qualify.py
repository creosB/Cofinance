import unittest
from qualify.scorers import final_answer, score, score_numbers_faithful, numbers_in
from qualify.run import run_cases, check_gates, load_cases, pct

class TestScorers(unittest.TestCase):
    def test_think_handling(self):
        self.assertEqual(final_answer("<think>hmm</think>\nready"), "ready")
        self.assertIsNone(final_answer("<think>never closes"))
        self.assertEqual(final_answer(" ready "), "ready")

    def test_set(self):
        c = {"check": "set", "expected": ["NVDA", "AMD"]}
        self.assertTrue(score(c, '["amd","NVDA"]')["passed"])
        self.assertFalse(score(c, '["NVDA"]')["passed"])
        self.assertFalse(score(c, '["NVDA","NVDA","AMD"]')["passed"])       # duplicates rejected
        self.assertFalse(score(c, 'NVDA, AMD')["passed"])

    def test_json_strict(self):
        c = {"check": "json", "expected": {"tool": "get_price", "args": {"ticker": "NVDA"}}}
        self.assertTrue(score(c, '{"tool":"get_price","args":{"ticker":"NVDA"}}')["passed"])
        self.assertFalse(score(c, 'Sure! {"tool":"get_price","args":{"ticker":"NVDA"}}')["passed"])
        self.assertFalse(score(c, '{"tool":"get_price","args":{"ticker":"NVDA"},"x":1}')["passed"])

    def test_numbers(self):
        allowed = [41.20, 39.80, 3200000, 18.4]
        self.assertTrue(score_numbers_faithful("ACME closed at 41.20 vs 39.80.", allowed))
        self.assertTrue(score_numbers_faithful("Volume was 3,200,000 shares.", allowed))
        self.assertFalse(score_numbers_faithful("ACME rose 3.5% to 41.20.", allowed))   # invented 3.5
        self.assertFalse(score_numbers_faithful("No numbers here.", allowed))
        self.assertEqual(numbers_in("up 12% to $2.1B"), [12.0, 2.1])

class TestRunner(unittest.TestCase):
    CASES = [
        {"id": "a", "category": "x", "prompt": "p", "check": "exact", "expected": "material"},
        {"id": "b", "category": "x", "prompt": "p", "check": "exact", "expected": "noise"},
        {"id": "c", "category": "y", "prompt": "p", "check": "exact", "expected": "ready"},
    ]
    def test_run_and_gates(self):
        outs = iter(["material", "<think>x</think>wrong", None])
        def call(body):
            o = next(outs)
            if o is None: raise ConnectionError("down")
            return {"choices": [{"message": {"content": o}, "finish_reason": "stop"}], "usage": {"completion_tokens": 3}}
        r = run_cases(self.CASES, call, "m")
        self.assertEqual(r["summary"]["x"]["pass_rate"], 0.5)
        self.assertEqual(r["rows"][2]["reason"], "request_error")
        g = check_gates(r, {"x": 0.5, "y": 0.5, "z": 0.1})
        self.assertEqual({k: v["passed"] for k, v in g.items()}, {"x": True, "y": False, "z": False})

    def test_truncated_output_is_not_scored_as_answer(self):
        call = lambda b: {"choices": [{"message": {"content": "ready"}, "finish_reason": "length"}]}
        r = run_cases(self.CASES[2:], call, "m")
        self.assertEqual(r["rows"][0]["reason"], "incomplete_or_no_output")

    def test_seed_cases_load_and_are_well_formed(self):
        cases = load_cases("qualify/cases.jsonl")
        self.assertGreaterEqual(len(cases), 19)
        for c in cases:
            self.assertIn(c["check"], {"exact", "set", "json", "numbers", "narrate"})
        self.assertEqual(len({c["id"] for c in cases}), len(cases))

    def test_pct(self):
        self.assertEqual(pct([1, 2, 3], .5), 2); self.assertIsNone(pct([], .5))

if __name__ == "__main__":
    unittest.main()
