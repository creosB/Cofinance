import json
import tempfile
import unittest
from pathlib import Path

from qualify import restriction_link as RL


class TestRestrictionLink(unittest.TestCase):
    def setUp(self):
        self.cases = RL.F.read(RL.FIXTURE)

    def test_fixture_matches_frozen_24_packet_design_and_omits_subject(self):
        RL.check_cases(self.cases)
        self.assertEqual(len(self.cases), 24)
        for c in self.cases:
            rendered = RL.render_packet(c)
            for n in c['notices']:
                self.assertIn(n['title'], rendered)
            for sp in RL.index_body_spans(c['notices']):
                self.assertIn(f"[{sp['id']}] {sp['text']}", rendered)
            for lk in c['gold_links']:
                self.assertIn(lk[0], c['gold_claims'])
                self.assertIn(lk[1], c['gold_restrictions'])

    def test_check_cases_rejects_subject_repetition_and_overlap(self):
        bad_subj = json.loads(json.dumps(self.cases))
        pos = next(c for c in bad_subj if c['gold_restrictions'])
        sp_idx = int(pos['gold_restrictions'][0][1:]) - 1
        pos['notices'][0]['body'][sp_idx] += f" for {pos['subject']}"
        with self.assertRaises(ValueError):
            RL.check_cases(bad_subj)

        bad_overlap = json.loads(json.dumps(self.cases))
        pos2 = next(c for c in bad_overlap if c['gold_restrictions'])
        pos2['gold_restrictions'] = [pos2['gold_claims'][0]]
        with self.assertRaises(ValueError):
            RL.check_cases(bad_overlap)

    def test_decoders_reject_titles_duplicates_cross_role_and_undeclared_links(self):
        body_ids = ['b001', 'b002', 'b003']
        with self.assertRaises(ValueError):
            RL.decode_direct('{"claims":["t001"],"restrictions":[],"links":[]}', body_ids)
        with self.assertRaises(ValueError):
            RL.decode_direct('{"claims":["b001","b001"],"restrictions":[],"links":[]}', body_ids)
        with self.assertRaises(ValueError):
            RL.decode_direct('{"claims":["b001"],"restrictions":["b001"],"links":[]}', body_ids)
        with self.assertRaises(ValueError):
            RL.decode_direct('{"claims":["b001"],"restrictions":["b002"],"links":[{"claim_span":"b001","restriction_span":"b003"}]}', body_ids)
        with self.assertRaises(ValueError):
            RL.decode_direct('{"claims":["b001"],"restrictions":["b002"],"links":[{"claim_span":"b001","restriction_span":"b002"},{"claim_span":"b001","restriction_span":"b002"}]}', body_ids)
        with self.assertRaises(ValueError):
            RL.decode_stage2('{"restrictions":["b001"],"links":[]}', ['b001'], body_ids)
        with self.assertRaises(ValueError):
            RL.decode_stage2('{"restrictions":["b002"],"links":[{"claim_span":"b003","restriction_span":"b002"}]}', ['b001'], body_ids)
        ok = RL.decode_stage2('{"restrictions":["b002"],"links":[{"claim_span":"b001","restriction_span":"b002"}]}', ['b001'], body_ids)
        self.assertEqual(ok['links'], [['b001', 'b002']])

    def test_scoring_separates_claims_restrictions_links_misattribution_and_clean_controls(self):
        two_pos = next(c for c in self.cases if c['structure'] == 'two_claim' and c['gold_restrictions'])
        gold_c = two_pos['gold_links'][0][0]
        other_c = next(x for x in two_pos['gold_claims'] if x != gold_c)
        gold_r = two_pos['gold_restrictions'][0]

        wrong_out = dict(
            case=two_pos['id'],
            arm='direct',
            pred=dict(claims=two_pos['gold_claims'], restrictions=[gold_r], links=[[other_c, gold_r]]),
            error=None,
        )
        scored_wrong = RL.score_row(two_pos, wrong_out)
        self.assertEqual(scored_wrong['claim_tp'], 2)
        self.assertEqual(scored_wrong['restr_tp'], 1)
        self.assertEqual(scored_wrong['link_tp'], 0)
        self.assertFalse(scored_wrong['exact_link'])
        self.assertTrue(scored_wrong['wrong_target'])

        both_out = dict(
            case=two_pos['id'],
            arm='direct',
            pred=dict(claims=two_pos['gold_claims'], restrictions=[gold_r], links=[[gold_c, gold_r], [other_c, gold_r]]),
            error=None,
        )
        scored_both = RL.score_row(two_pos, both_out)
        self.assertFalse(scored_both['exact_link'])
        self.assertTrue(scored_both['wrong_target'])

        ctrl = next(c for c in self.cases if not c['gold_restrictions'])
        sel_all = RL.score_row(ctrl, dict(case=ctrl['id'], arm='select_all', pred=RL.select_all_baseline(ctrl), error=None))
        self.assertTrue(sel_all['clean_any_link'])
        self.assertGreater(sel_all['clean_pred_links'], 0)

        malformed = RL.score_row(ctrl, dict(case=ctrl['id'], arm='direct', pred=None, error='ValueError: bad'))
        self.assertTrue(malformed['malformed'])
        self.assertFalse(malformed['exact_link'])

    def test_gate_enforces_all_conditions_and_blocks_misattribution_or_clean_links(self):
        def build_rows(two_stage_fn, direct_fn):
            rows = []
            for c in self.cases:
                rows.append(RL.score_row(c, dict(case=c['id'], arm='lexical', pred=RL.lexical_baseline(c), error=None)))
                rows.append(RL.score_row(c, dict(case=c['id'], arm='select_all', pred=RL.select_all_baseline(c), error=None)))
                rows.append(RL.score_row(c, dict(case=c['id'], arm='direct', pred=direct_fn(c), error=None)))
                rows.append(RL.score_row(c, dict(case=c['id'], arm='two_stage', pred=two_stage_fn(c), error=None)))
            return rows

        perfect = lambda c: dict(claims=c['gold_claims'], restrictions=c['gold_restrictions'], links=c['gold_links'])
        claims_only = lambda c: dict(claims=c['gold_claims'], restrictions=[], links=[])

        pass_diag = RL.diagnose(build_rows(perfect, claims_only))
        self.assertEqual(pass_diag['decision'], 'INDEPENDENT_RETEST_ONLY')

        tie_diag = RL.diagnose(build_rows(perfect, perfect))
        self.assertEqual(tie_diag['decision'], 'NO_PROMOTION')

        two_pos_id = next(c['id'] for c in self.cases if c['structure'] == 'two_claim' and c['gold_restrictions'])

        def one_wrong_target(c):
            if c['id'] != two_pos_id:
                return perfect(c)
            gc = c['gold_links'][0][0]
            oc = next(x for x in c['gold_claims'] if x != gc)
            gr = c['gold_restrictions'][0]
            return dict(claims=c['gold_claims'], restrictions=[gr], links=[[gc, gr], [oc, gr]])

        self.assertEqual(RL.diagnose(build_rows(one_wrong_target, claims_only))['decision'], 'NO_PROMOTION')

        ctrl_id = next(c['id'] for c in self.cases if not c['gold_restrictions'])

        def one_clean_link(c):
            if c['id'] != ctrl_id:
                return perfect(c)
            return dict(claims=['b001'], restrictions=['b002'], links=[['b001', 'b002']])

        self.assertEqual(RL.diagnose(build_rows(one_clean_link, claims_only))['decision'], 'NO_PROMOTION')

    def test_freeze_seal_and_one_look_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run1'
            RL.freeze(root)
            RL.seal(root)
            RL.P.write(root / 'started.json', True)
            with self.assertRaises(ValueError):
                RL.run(root)


if __name__ == '__main__':
    unittest.main()
