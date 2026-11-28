import builtins
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest import mock

from core import ops, router_worker
from core.chat_service import ChatService
from core.local_models import ModelError
from core.memory import HybridMemory
from tests.test_chat import FakeMarket, wait
from tests.test_open_chat import RecordingNarratorModels

REPO = Path(__file__).resolve().parents[1]


class MarketFixture(FakeMarket):
    def get_snapshot(self, ticker):
        snap = super().get_snapshot(ticker)
        snap.update(ticker=ticker, price=195.25, currency='USD', status='available', quote_type='EQUITY',
                    quote_status={'state': 'last_session', 'as_of': '2026-11-27T21:00:00+00:00'})
        return snap


class TestModelFreeTurns(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.models = RecordingNarratorModels()
        self.market = mock.Mock(side_effect=MarketFixture)
        self.router = mock.Mock(side_effect=ModelError('normal router path'))
        self.odds = mock.Mock(side_effect=AssertionError('Exact turns must not request unrelated odds'))
        self.svc = ChatService(REPO, self.root, models=self.models, market_factory=self.market,
                               router_factory=self.router, crowd_odds=self.odds)
        self.addCleanup(self.svc.close)
        self.semantic = mock.Mock(backend='fixture-semantic')
        self.svc.memory = HybridMemory(self.svc.keyword_memory, self.semantic)
        self.session = self.svc.store.create_session(uuid.uuid4().hex)['id']

    def ask(self, text):
        job = self.svc.submit(self.session, text, uuid.uuid4().hex, self.models.narrator_id)
        done = wait(self.svc, job)
        trace = json.loads((self.root / 'traces' / f"{job['id']}.json").read_text(encoding='utf-8'))
        return done, trace, self.svc.store.session(self.session)['messages'][-1]

    def assert_no_inference(self, trace):
        self.router.assert_not_called()
        self.assertEqual(self.models.load_calls, 0)
        self.assertEqual(self.models.complete_calls, [])
        self.assertEqual(self.models.think_load_calls, 0)
        self.semantic.recall.assert_not_called()
        self.semantic.remember_turn.assert_not_called()
        self.odds.assert_not_called()
        self.assertFalse(any(s['kind'] == 'model' or s['name'] == 'Load cached routing model' for s in trace['spans']))

    def test_greeting_bypasses_all_runtime_and_market_acquisition(self):
        done, trace, response = self.ask('Hi!')
        self.assertEqual(done['status'], 'complete')
        self.assertEqual(response['payload']['kind'], 'conversation')
        self.assertTrue(response['content'].startswith('Hi!'))
        self.assert_no_inference(trace)
        self.market.assert_not_called()

    def test_explicit_quote_reports_provider_value_currency_and_freshness_without_any_model(self):
        done, trace, response = self.ask('Quote $NVDA')
        self.assertEqual(done['status'], 'complete', done)
        self.assertEqual(trace['summary']['flow'], 'python_market_answer')
        self.assertEqual(response['payload']['tickers'], ['NVDA'])
        self.assertEqual(response['payload']['snapshots']['NVDA']['currency'], 'USD')
        self.assertIn('195.25 USD', response['content'])
        self.assertIn('Last regular-session quote, not live', response['content'])
        self.assertIn('2026-11-27T21:00:00+00:00', response['content'])
        self.assertEqual(self.svc.store.session(self.session)['tickers'], ['NVDA'])
        self.assertEqual(len(self.svc.ledger.items(self.session)), 1)
        self.assert_no_inference(trace)
        self.market.assert_called_once()

    def test_incomplete_or_ambiguous_requests_keep_the_normal_router(self):
        for prompt in ('Hi, compare AAPL and NVDA', 'Quote THY', 'Quote NVDA and AAPL',
                       'Quote NVDA in London', 'Quote nvda', 'What is a P/E ratio?'):
            with self.subTest(prompt=prompt):
                self.router.reset_mock()
                done, _, _ = self.ask(prompt)
                self.router.assert_called_once_with()
                self.assertEqual(done['status'], 'error', done)
        self.market.assert_not_called()


class TestStandaloneOnly(unittest.TestCase):
    def test_default_and_legacy_auto_fail_closed_without_importing_pytorch(self):
        real_import = builtins.__import__

        def reject_fallback(name, *args, **kwargs):
            if name.split('.')[0] in {'torch', 'gliner2'}:
                raise AssertionError('A missing standalone cache must not import a larger backend')
            return real_import(name, *args, **kwargs)

        with mock.patch('builtins.__import__', side_effect=reject_fallback):
            for mode in ('required', 'auto', ''):
                for invalid in (None, ValueError('broken cache')):
                    with self.subTest(mode=mode, invalid=invalid), \
                         mock.patch('core.gliner_onnx.load_standalone_onnx', return_value=invalid,
                                    side_effect=invalid if isinstance(invalid, Exception) else None):
                        with self.assertRaisesRegex(ModelError, 'no PyTorch fallback'):
                            router_worker._load_worker_model(Path('.'), mode)

    def test_worker_start_ignores_legacy_pytorch_override_and_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = router_worker.RouterProcess(REPO, Path(temp))
            process = mock.Mock(stdin=io.StringIO(), stdout=io.StringIO(), stderr=None)
            process.poll.return_value = 1
            with mock.patch.dict(os.environ, {'COFINANCE_ROUTER_ONNX': 'off'}), \
                 mock.patch.object(router_worker, 'spawn_managed', return_value=process) as start, \
                 mock.patch.object(worker, 'receive', return_value={'ready': False}):
                with self.assertRaisesRegex(ModelError, 'no PyTorch fallback'):
                    worker.start()
            self.assertEqual(start.call_args.kwargs['env']['COFINANCE_ROUTER_ONNX'], 'required')
            self.assertIsNone(worker.process)

    def test_preflight_uses_the_standalone_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            model_dir = data / 'local_models'
            model_dir.mkdir()
            artifacts = ops.standalone_paths(model_dir)
            artifacts[0].write_bytes(b'fixture')
            with mock.patch.object(ops, 'verify_standalone_onnx_cache', return_value=artifacts) as verify:
                report = ops.run_preflight(REPO, data, check_port=False)
            verify.assert_called_once()
            self.assertEqual(next(c for c in report['checks'] if c['id'] == 'router_onnx_cache')['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
