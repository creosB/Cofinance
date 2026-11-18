"""Deep think dev screen: runtime switch, frozen rules, one look. No model, no network."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from core.local_models import server_flags
from qualify import history_runtime as HR
from qualify import think_lab as T


class TestThinkingSwitch(unittest.TestCase):
    def test_default_flags_command_and_body_are_the_qualified_ones(self):
        flags = server_flags('vulkan')
        self.assertEqual(HR.thinking_flags(flags, None), flags)
        cmd = ['exe', '--model', 'm', *flags, '--no-webui', '--api-key', 'k']
        self.assertEqual(HR.thinking_command(cmd, flags, None), cmd)
        body = HR.request_body('a', 's', 'p', max_tokens=24)
        self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': False})
        self.assertEqual(set(body), {'model', 'messages', 'temperature', 'max_tokens', 'chat_template_kwargs'})

    def test_thinking_turns_reasoning_on_with_a_budget_in_place(self):
        flags = server_flags('vulkan')
        on = HR.thinking_flags(flags, 384)
        self.assertNotIn('off', on[on.index('--reasoning') + 1])
        self.assertEqual(on[on.index('--reasoning') + 1], 'on')
        self.assertEqual(on[on.index('--reasoning-budget') + 1], '384')
        self.assertEqual(on[on.index('--reasoning-format') + 1], 'deepseek')
        cmd = ['exe', '--model', 'm', *flags, '--no-webui', '--api-key', 'k']
        out = HR.thinking_command(cmd, flags, 384)
        self.assertEqual(out[:3], ['exe', '--model', 'm'])
        self.assertEqual(out[-3:], ['--no-webui', '--api-key', 'k'])
        self.assertEqual(out.count('--reasoning'), 1)
        self.assertEqual(HR.request_body('a', 's', 'p', thinking=True)['chat_template_kwargs'], {'enable_thinking': True})
        with self.assertRaises(ValueError):
            HR.thinking_command(['exe', '--no-webui'], flags, 384)

    def test_budget_is_added_on_top_of_each_answer_budget(self):
        seen = {}
        T.budget_call(lambda s, p, **kw: seen.update(kw))('s', 'p', max_tokens=24, timeout=30, top_logprobs=20)
        self.assertEqual(seen['max_tokens'], 24 + T.BUDGET)
        self.assertEqual(seen['timeout'], T.HEADLINE_TIMEOUT)
        self.assertEqual(seen['top_logprobs'], 20)


class TestBaseline(unittest.TestCase):
    def test_fingerprint_matches_the_live_app_rule(self):
        from types import SimpleNamespace
        from core.local_models import DEFAULT_NARRATOR, LocalModels
        fake = SimpleNamespace(repo=T.REPO, active_backend='vulkan', narrator_id=DEFAULT_NARRATOR)
        for backend in ('vulkan', 'cpu'):
            self.assertEqual(T.qualified_fingerprint(backend, DEFAULT_NARRATOR),
                             LocalModels.fingerprint(fake, backend, DEFAULT_NARRATOR))


class TestRules(unittest.TestCase):
    def think(self, auc=.6, early=.6, late=.6, failures=0):
        return {'ranking': {'auc': auc, 'early': early, 'late': late}, 'failures': failures}

    def test_headline_rule_needs_every_condition(self):
        self.assertTrue(T.headline_decision(self.think(), .58))
        self.assertFalse(T.headline_decision(self.think(auc=.55), .5))      # not above 0.55
        self.assertFalse(T.headline_decision(self.think(auc=.6), .6))       # not above thinking-off
        self.assertFalse(T.headline_decision(self.think(late=.5), .5))      # a half at no skill
        self.assertFalse(T.headline_decision(self.think(early=None), .5))
        self.assertFalse(T.headline_decision(self.think(failures=3), .5))
        self.assertFalse(T.headline_decision(self.think(), None))
        self.assertTrue(T.headline_decision(self.think(failures=2), .5))

    def test_chat_rule_needs_every_gate(self):
        self.assertTrue(T.chat_decision({'a': {'passed': True}, 'b': {'passed': True}}))
        self.assertFalse(T.chat_decision({'a': {'passed': True}, 'b': {'passed': False}}))
        self.assertFalse(T.chat_decision({}))

    def test_near_certain_counts_label_mass_and_skips_failures(self):
        preds = [{'ok': True, 'logprobs': {'UP': -1e-5, 'DOWN': None, 'ABSTAIN': None}},
                 {'ok': True, 'logprobs': {'UP': -0.7, 'DOWN': -0.7, 'ABSTAIN': None}},
                 {'ok': False, 'logprobs': {'UP': 0.0}}]
        self.assertEqual(T.near_certain(preds), {'judged': 2, 'near_certain': 1, 'share': 0.5})

    def test_smoke_cost_reads_medians(self):
        rows = [{'category': 'narration', 'ms': 1000, 'completion_tokens': 30},
                {'category': 'narration', 'ms': 3000, 'completion_tokens': 50},
                {'category': 'other', 'ms': 9000, 'completion_tokens': 900}]
        self.assertEqual(T.smoke_cost({'rows': rows}), {'calls': 2, 'median_seconds': 2.0, 'median_completion_tokens': 40.0})


class TestOneLook(unittest.TestCase):
    P = {'format': T.FORMAT, 'narrator': {'id': 'n', 'model_sha256': 'sha'}, 'rules': {'chat': 'c', 'headlines': 'h'}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / 'lab'
        self.patch = patch.object(T, 'current_protocol', lambda model_root: self.P); self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def test_freeze_refuses_a_changed_protocol_and_run_refuses_without_it_or_twice(self):
        with self.assertRaisesRegex(ValueError, 'Freeze'):
            T.run(self.root, narrator=None)
        T.freeze(self.root, self.P)
        with self.assertRaisesRegex(ValueError, 'changed'):
            T.freeze(self.root, {**self.P, 'budget': 1})
        (self.root / 'smoke.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Already run'):
            T.run(self.root, narrator=None)

    def fake_run(self, meta, think_ids=('a', 'b')):
        calls = {}

        @contextmanager
        def narrator(model_root, log, **kw):
            calls['narrator'] = kw
            def call(system, prompt, **k):
                return {}
            call.post = lambda body: (calls.setdefault('bodies', []).append(body) or
                                      {'choices': [{'message': {'content': 'x'}, 'finish_reason': 'stop'}], 'usage': {'completion_tokens': 9}})
            yield call, meta

        def run_headlines(root, out, source, candidate, call, model_meta):
            calls['headline_call'] = call
            out.mkdir(parents=True)
            (out / 'predictions.jsonl').write_text(''.join(json.dumps({'id': i, 'ok': True, 'logprobs': {'UP': 0.0}}) + '\n' for i in think_ids))

        rk = {'auc': .6, 'early': .6, 'late': .6, 'origin_cluster_bootstrap_95': [.5, .7]}
        think = {'ranking': rk, 'failures': 0, 'headline_labels': {}, 'median_decision_seconds': 2.0}
        off = {'ranking': {**rk, 'auc': .58}, 'failures': 0, 'headline_labels': {}, 'median_decision_seconds': .3}
        off_dir = Path(self.tmp.name) / 'off'; off_dir.mkdir(exist_ok=True); (off_dir / 'report.json').write_text(json.dumps(off))
        cases = [{'id': 'c1', 'category': 'narration', 'prompt': 'p', 'system': 's'}]
        gates = Path(self.tmp.name) / 'gates.json'; gates.write_text(json.dumps({'narration': 0.0}))
        with patch.object(T.D, 'verified', lambda r, o: ({'ids': ['a', 'b']}, [], {}, {})), \
             patch.object(T.D, 'run_headlines', run_headlines), patch.object(T.D, 'score', lambda r, o: think), \
             patch.object(T, 'OFF_SCREEN', off_dir), patch.object(T, 'GATES', gates), \
             patch.object(T, 'load_cases', lambda p: cases), \
             patch.object(T, 'off_qualification', lambda m: {'rows': [{'category': 'narration', 'ms': 400, 'completion_tokens': 35}]}), \
             patch.object(T, 'run_cases', lambda cs, post, model, max_tokens, extra: (calls.update(max_tokens=max_tokens, extra=extra) or
                          {'summary': {'narration': {'pass_rate': 1.0}}, 'rows': [{'category': 'narration', 'ms': 8000, 'completion_tokens': 420}]})):
            T.freeze(self.root, self.P)
            report = T.run(self.root, narrator=narrator)
        return calls, report

    def test_run_turns_thinking_on_everywhere_and_applies_both_rules(self):
        calls, report = self.fake_run({'model_sha256': 'sha', 'thinking': {'budget': T.BUDGET}})
        self.assertEqual(calls['narrator']['thinking_budget'], T.BUDGET)
        self.assertEqual(calls['extra'], {'chat_template_kwargs': {'enable_thinking': True}})
        self.assertEqual(calls['max_tokens'], T.NARRATION_TOKENS + T.BUDGET)
        self.assertEqual(report['decisions'], {'chat_deep_think_switch': True, 'headline_thinking_candidate': True})
        self.assertEqual(report['smoke']['think_cost']['median_completion_tokens'], 420)
        self.assertEqual(report['smoke']['off_cost']['median_completion_tokens'], 35)
        self.assertEqual(report['headlines']['think_near_certain']['share'], 1.0)

    def test_run_refuses_a_runtime_without_the_frozen_budget_or_model(self):
        for meta in ({'model_sha256': 'sha'}, {'model_sha256': 'other', 'thinking': {'budget': T.BUDGET}}):
            with self.subTest(meta=meta), self.assertRaisesRegex(ValueError, 'frozen'):
                shutil_root = self.root
                if shutil_root.exists():
                    for f in sorted(shutil_root.rglob('*'), reverse=True):
                        f.unlink() if f.is_file() else f.rmdir()
                self.fake_run(meta)

    def test_run_refuses_a_different_headline_set(self):
        with self.assertRaisesRegex(ValueError, 'different headlines'):
            self.fake_run({'model_sha256': 'sha', 'thinking': {'budget': T.BUDGET}}, think_ids=('a', 'c'))


if __name__ == '__main__':
    unittest.main()
