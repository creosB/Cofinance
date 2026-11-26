import json
import tempfile
import unittest
from pathlib import Path

from qualify import isolated_restriction_link as IRL
from qualify import restriction_link as RL


class TestIsolatedRestrictionLink(unittest.TestCase):
    def test_structural_isolation_forbids_cross_notice_links_and_enforces_schema(self):
        cases = json.loads(RL.FIXTURE.read_text(encoding="utf-8"))
        two_claim_pos = next(c for c in cases if c["structure"] == "two_claim" and c["gold_restrictions"])
        by_notice = IRL.group_spans_by_notice(two_claim_pos)
        self.assertEqual(len(by_notice), 2)

        n0_ids = [sp["id"] for sp in by_notice[0]]
        n1_ids = [sp["id"] for sp in by_notice[1]]
        roles_0 = {sid: ("claim" if sid in two_claim_pos["gold_claims"] else ("restriction" if sid in two_claim_pos["gold_restrictions"] else "none")) for sid in n0_ids}
        roles_1 = {sid: ("claim" if sid in two_claim_pos["gold_claims"] else ("restriction" if sid in two_claim_pos["gold_restrictions"] else "none")) for sid in n1_ids}

        assembled = IRL.assemble_isolated_packet([roles_0, roles_1], by_notice)
        self.assertEqual(assembled["links"], two_claim_pos["gold_links"])

        for c_id, r_id in assembled["links"]:
            same_notice = any(
                c_id in {s["id"] for s in grp} and r_id in {s["id"] for s in grp}
                for grp in by_notice
            )
            self.assertTrue(same_notice)

        with self.assertRaisesRegex(ValueError, "non-claim"):
            IRL.decode_full_packet(
                json.dumps({
                    n0_ids[0]: {"role": "none", "target": "none"},
                    n0_ids[1]: {"role": "restriction", "target": n0_ids[0]},
                }),
                n0_ids[:2],
            )

    def test_freeze_one_look_and_end_to_end_fake_eval(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "iso1"
            plan = IRL.freeze(run_dir)
            self.assertEqual(plan["n_cases"], 24)

            with self.assertRaises(FileExistsError):
                IRL.freeze(run_dir)

            proto_path = run_dir / "protocol.json"
            tampered = json.loads(proto_path.read_text(encoding="utf-8"))
            tampered["n_cases"] = 99
            proto_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Protocol SHA-256"):
                IRL.verify_protocol(run_dir)
            proto_path.write_text(json.dumps(plan), encoding="utf-8")

            cases = json.loads(RL.FIXTURE.read_text(encoding="utf-8"))
            gold_by_span = {}
            target_by_restr = {}
            for c in cases:
                for sp in RL.index_body_spans(c["notices"]):
                    role = "claim" if sp["id"] in c["gold_claims"] else ("restriction" if sp["id"] in c["gold_restrictions"] else "none")
                    gold_by_span[(c["question"], sp["text"])] = role
                for cid, rid in c["gold_links"]:
                    target_by_restr[(c["question"], rid)] = cid

            def fake_post(body):
                schema = body["response_format"]["json_schema"]["schema"]
                name = body["response_format"]["json_schema"]["name"]
                user = body["messages"][1]["content"]
                q = user.splitlines()[0].split("Question: ", 1)[1]
                spans = {}
                for line in user.splitlines():
                    if line.startswith("[b") and "] " in line:
                        sid, txt = line[1:].split("] ", 1)
                        spans[sid] = txt
                if name == "isolated_notice_mask":
                    ans = {sid: gold_by_span.get((q, spans[sid]), "none") for sid in schema["required"]}
                else:
                    ans = {}
                    for sid in schema["required"]:
                        role = gold_by_span.get((q, spans[sid]), "none")
                        ans[sid] = {"role": role, "target": target_by_restr.get((q, sid), "none") if role == "restriction" else "none"}
                return {
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ans)}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20},
                }

            rep = IRL.run_eval(run_dir, post_fn=fake_post)
            self.assertEqual(rep["decisions"]["isolated_notice_mask"], "CANDIDATE_FOR_FRESH_UNSEEN_TEST")
            self.assertEqual(rep["results"]["all"]["isolated_notice_mask"]["exact_link_hits"], 24)
            self.assertEqual(rep["results"]["all"]["isolated_notice_mask"]["wrong_target_hits"], 0)

            with self.assertRaises(FileExistsError):
                IRL.run_eval(run_dir, post_fn=fake_post)

    def test_decide_gates(self):
        base = {
            "malformed": 0,
            "wrong_target_hits": 0,
            "exact_link_accuracy": 0.875,
            "restriction": {"recall": 0.75},
            "clean_any_link_hits": 0,
        }
        self.assertEqual(IRL.decide(base), "CANDIDATE_FOR_FRESH_UNSEEN_TEST")
        self.assertEqual(IRL.decide({**base, "malformed": 1}), "INCONCLUSIVE_MALFORMED")
        self.assertEqual(IRL.decide({**base, "wrong_target_hits": 1}), "REJECT_CROSS_NOTICE_LEAK")
        self.assertEqual(IRL.decide({**base, "clean_any_link_hits": 2}), "REJECT_LOW_ACCURACY_OR_NOISE")
        self.assertEqual(IRL.decide({**base, "restriction": {"recall": 0.5}}), "REJECT_LOW_ACCURACY_OR_NOISE")

    def test_decoder_rejects_untyped_values_and_counts_malformed_in_denominators(self):
        with self.assertRaisesRegex(ValueError, "Invalid role"):
            IRL.decode_isolated_notice(json.dumps({"b001": ["claim"]}), ["b001"])
        with self.assertRaisesRegex(ValueError, "Invalid target"):
            IRL.decode_full_packet(
                json.dumps({"b001": {"role": "restriction", "target": ["b001"]}}),
                ["b001"],
            )
        with self.assertRaisesRegex(ValueError, "do not match body spans"):
            IRL.decode_isolated_notice(json.dumps({"b001": "claim", "b999": "none"}), ["b001"])

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "iso_bad"
            IRL.freeze(run_dir)

            def broken_post(body):
                name = body["response_format"]["json_schema"]["name"]
                if name == "full_packet_mask":
                    return {
                        "choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    }
                req = body["response_format"]["json_schema"]["schema"]["required"]
                return {
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({s: "none" for s in req})}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }

            rep = IRL.run_eval(run_dir, post_fn=broken_post)
            full_all = rep["results"]["all"]["full_packet_mask"]
            self.assertEqual(full_all["n"], 24)
            self.assertEqual(full_all["malformed"], 24)
            self.assertEqual(full_all["exact_link_accuracy"], 0.0)
            self.assertEqual(rep["decisions"]["full_packet_mask"], "INCONCLUSIVE_MALFORMED")


if __name__ == "__main__":
    unittest.main()
