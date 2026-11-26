import json
import tempfile
import unittest
from pathlib import Path

from qualify import spec_lab as sl


class TestSpecLab(unittest.TestCase):
    def test_freeze_one_look_parity_drift_and_speedup_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases_path = Path(tmp) / "cases.jsonl"
            cases = [
                {"id": f"c{i}", "category": "narration", "system": "sys", "prompt": f"p{i}"}
                for i in range(6)
            ]
            cases_path.write_text("\n".join(json.dumps(c) for c in cases) + "\n", encoding="utf-8")
            run_dir = Path(tmp) / "run1"
            plan = sl.freeze(run_dir, cases_path=cases_path)
            self.assertEqual(plan["n_cases"], 6)

            with self.assertRaises(FileExistsError):
                sl.freeze(run_dir, cases_path=cases_path)

            proto_path = run_dir / "protocol.json"
            tampered = json.loads(proto_path.read_text(encoding="utf-8"))
            tampered["n_cases"] = 99
            proto_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Protocol SHA-256"):
                sl.verify_protocol(run_dir, cases_path=cases_path)
            proto_path.write_text(json.dumps(plan), encoding="utf-8")

            def fake_runner(rows, *, extra_flags, thinking, max_tokens):
                out = []
                is_n8 = "8" in extra_flags
                is_default = bool(extra_flags) and not is_n8
                for idx, row in enumerate(rows):
                    eval_ms = 100.0
                    content = f"ans-{row['id']}"
                    if is_default:
                        eval_ms = 70.0
                    elif is_n8:
                        eval_ms = 60.0
                        if idx == 0:
                            content = "drifted"
                    out.append({
                        "id": row["id"],
                        "category": row["category"],
                        "wall_ms": eval_ms + 10.0,
                        "prompt_ms": 10.0,
                        "predicted_ms": eval_ms,
                        "predicted_n": 50,
                        "predicted_per_second": 500.0,
                        "draft_n": 20 if extra_flags else 0,
                        "draft_n_accepted": 18 if extra_flags else 0,
                        "completion_tokens": 50,
                        "content": content,
                        "reasoning_content": "thought" if thinking else "",
                        "error": None,
                    })
                return out

            rep = sl.run_eval(run_dir, cases_path=cases_path, runner=fake_runner)
            self.assertEqual(rep["decisions"]["standard"]["ngram_mod_default"], "CANDIDATE_FOR_QUALIFICATION")
            self.assertEqual(rep["decisions"]["standard"]["ngram_mod_n8"], "REJECT_PARITY_DRIFT")
            self.assertEqual(rep["results"]["standard"]["ngram_mod_n8"]["divergent_ids"], ["c0"])

            with self.assertRaises(FileExistsError):
                sl.run_eval(run_dir, cases_path=cases_path, runner=fake_runner)

    def test_decide_arm_enforces_errors_split_halves_and_ci_threshold(self):
        good = {
            "n": 6,
            "baseline_errors": 0,
            "candidate_errors": 0,
            "exact_content_matches": 6,
            "exact_reasoning_matches": 6,
            "eval_speedup": {"point": 1.25, "ci_lo": 1.15, "ci_hi": 1.35},
            "split_halves_eval_speedup": {"half_1": 1.2, "half_2": 1.3},
        }
        self.assertEqual(sl.decide_arm(good), "CANDIDATE_FOR_QUALIFICATION")
        self.assertEqual(sl.decide_arm({**good, "candidate_errors": 1}), "REJECT_ERRORS")
        self.assertEqual(sl.decide_arm({**good, "exact_reasoning_matches": 5}), "REJECT_PARITY_DRIFT")
        self.assertEqual(sl.decide_arm({**good, "eval_speedup": {"point": 1.12, "ci_lo": 1.05, "ci_hi": 1.2}}), "REJECT_INSUFFICIENT_GAIN")
        self.assertEqual(sl.decide_arm({**good, "split_halves_eval_speedup": {"half_1": 1.3, "half_2": 0.98}}), "REJECT_INSUFFICIENT_GAIN")


if __name__ == "__main__":
    unittest.main()
