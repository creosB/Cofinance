import json
import tempfile
import unittest
from pathlib import Path

from core.signals import Judgment
from qualify import d1_eval as d1


class _StubClf:
    def __init__(self, table):
        self.table = table

    def judge(self, item):
        entry = self.table[item.title]
        if isinstance(entry, Exception):
            raise entry
        return entry


class TestD1Eval(unittest.TestCase):
    def test_roc_auc_balanced_accuracy_and_errors_in_denominator(self):
        self.assertEqual(d1.roc_auc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]), 1.0)
        self.assertEqual(d1.roc_auc([0.5, 0.5, 0.5, 0.5], [True, True, False, False]), 0.5)
        self.assertEqual(d1.balanced_accuracy([True, False, False, False], [True, True, False, False]), 0.75)

        rows = [
            {"id": "1", "ticker": "A", "title": "t1", "material": True, "urgent": True},
            {"id": "2", "ticker": "A", "title": "t2", "material": True, "urgent": False},
            {"id": "3", "ticker": "A", "title": "t3", "material": False, "urgent": False, "trap": "denial"},
            {"id": "4", "ticker": "A", "title": "t4", "material": False, "urgent": False},
        ]
        preds = [
            {"id": "1", "material_p": 0.9, "urgent_p": 0.8, "ms": 100.0},
            {"id": "2", "error": "TimeoutError", "ms": 200.0},
            {"id": "3", "material_p": 0.1, "urgent_p": 0.1, "ms": 100.0},
            {"id": "4", "material_p": 0.2, "urgent_p": 0.1, "ms": 100.0},
        ]
        sc = d1.score_predictions(rows, preds)
        self.assertEqual(sc["errors"], 1)
        self.assertEqual(sc["accuracy"], 0.75)
        self.assertEqual(sc["balanced_accuracy"], 0.75)
        self.assertEqual(sc["trap_accuracy"], 1.0)

    def test_freeze_one_look_lock_cascade_and_adoption_gates(self):
        rows = [
            {"id": f"r{i}", "ticker": "ACME", "title": f"title {i}", "material": i % 2 == 0, "urgent": i == 0}
            for i in range(8)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_path = root / "headlines.jsonl"
            data_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            run_dir = root / "run1"

            plan = d1.freeze(run_dir, data_path, dtype="float32")
            self.assertEqual(d1.verify_protocol(run_dir)["protocol_sha256"], plan["protocol_sha256"])

            with self.assertRaises(FileExistsError):
                d1.freeze(run_dir, data_path)

            gliner_table = {
                r["title"]: Judgment(
                    material=0.6 if r["material"] else 0.45,
                    urgent=0.2,
                    direction="neutral",
                    direction_p=0.5,
                )
                for r in rows
            }
            d1_table = {
                r["title"]: Judgment(
                    material=0.95 if r["material"] else 0.05,
                    urgent=0.9 if r["urgent"] else 0.05,
                    direction="bullish" if r["material"] else "neutral",
                    direction_p=0.9,
                )
                for r in rows
            }

            report = d1.run_frozen(
                run_dir,
                gliner_factory=lambda: _StubClf(gliner_table),
                d1_factory=lambda dt: _StubClf(d1_table),
            )
            self.assertEqual(report["arms"]["gliner"]["errors"], 0)
            self.assertEqual(report["arms"]["d1"]["material_auc"], 1.0)
            self.assertEqual(report["arms"]["cascade_gliner_d1"]["routes"]["escalated_d1"], 8)
            self.assertIn("first_half", report["splits"]["d1"])
            self.assertIn("second_half", report["splits"]["d1"])

            with self.assertRaises(FileExistsError):
                d1.run_frozen(
                    run_dir,
                    gliner_factory=lambda: _StubClf(gliner_table),
                    d1_factory=lambda dt: _StubClf(d1_table),
                )

            proto_file = run_dir / "protocol.json"
            tampered_proto = json.loads(proto_file.read_text(encoding="utf-8"))
            tampered_proto["n_rows"] = 999
            proto_file.write_text(json.dumps(tampered_proto), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Protocol SHA-256"):
                d1.verify_protocol(run_dir)
            proto_file.write_text(json.dumps(plan), encoding="utf-8")

            data_path.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Dataset SHA-256"):
                d1.verify_protocol(run_dir)

            base = {"errors": 0, "false_alert_rate": 0.0, "balanced_accuracy": 0.8, "macro_f1": 0.8, "p50_ms": 500.0}
            good_fast = {"errors": 0, "false_alert_rate": 0.0, "balanced_accuracy": 0.9, "macro_f1": 0.9, "p50_ms": 1200.0}
            good_slow = {**good_fast, "p50_ms": 18000.0}
            paired_gain = {"material_auc": {"diff": 0.12, "ci95": [0.02, 0.22]}}
            self.assertTrue(d1.decide_adoption(base, good_fast, paired_gain)["adopt"])
            self.assertFalse(d1.decide_adoption(base, good_slow, paired_gain)["adopt"])
            self.assertEqual(d1.decide_adoption(base, good_slow, paired_gain)["verdict"], "REJECT_TOO_SLOW_ON_CPU")
            regressed = {**good_fast, "errors": 1}
            self.assertEqual(d1.decide_adoption(base, regressed, paired_gain)["verdict"], "REJECT_REGRESSION")


if __name__ == "__main__":
    unittest.main()
