import os
import tempfile
import unittest
import uuid
from pathlib import Path

from core.hymemory_port import HyMemoryError, HyMemoryLite
from tests.test_chat import service, wait

REPO = Path(__file__).resolve().parents[1]


class FakeHy(HyMemoryLite):
    """Adapter logic with the worker replaced: add returns ids, search returns whatever the test plants."""
    def __init__(self, store):
        super().__init__(REPO, REPO / 'data', store=store)
        self.planted, self.n = [], 0
    def _call(self, request):
        if request['op'] == 'add':
            self.n += 1
            return {'ok': True, 'result': {'memory_id': f"m{self.n}"}}
        return {'ok': True, 'result': {'memories': {'normal': self.planted}}}


class TestHyMemoryAdapter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hy = FakeHy(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_namespace_and_idempotence(self):
        with self.assertRaises(ValueError):
            self.hy.remember_turn('news', 's1', 'q', 'a', run_id='r1')
        one = self.hy.remember_turn('chat', 's1', 'q', 'a', run_id='r1', tickers=['nvda'])
        two = self.hy.remember_turn('chat', 's1', 'q', 'a', run_id='r1')
        self.assertEqual((one, two, self.hy.n), ('m1', 'm1', 1))

    def test_recall_keeps_only_linked_ids_from_this_conversation(self):
        self.hy.remember_turn('chat', 's1', 'NVDA thesis?', 'NVDA answer', run_id='r1', tickers=['NVDA'])
        self.hy.remember_turn('chat', 's2', 'Other chat', 'Other answer', run_id='r2', tickers=['NVDA'])
        self.hy.remember_turn('chat', 's1', 'Latest', 'Latest answer', run_id='r3', tickers=['NVDA'])
        self.hy.planted = [{'memory_id': 'm2', 'score': 0.9, 'content': '[user]: Other chat\n[assistant]: Other answer'},
                           {'memory_id': 'unknown', 'score': 0.8, 'content': 'planted'},
                           {'memory_id': 'm3', 'score': 0.7, 'content': '[user]: Latest\n[assistant]: Latest answer'},
                           {'memory_id': 'm1', 'score': 0.6, 'content': '[user]: NVDA thesis?\n[assistant]: NVDA answer'}]
        hits = self.hy.recall('chat', 's1', 'back to NVDA', limit=2, exclude_run='r3')
        self.assertEqual([(h.run_id, h.answer, h.tickers) for h in hits], [('r1', 'NVDA answer', ['NVDA'])])

    def test_empty_conversation_skips_the_worker(self):
        self.hy.planted = [{'memory_id': 'm1', 'score': 0.9, 'content': 'x'}]
        self.assertEqual(self.hy.recall('chat', 'nothing-here', 'query'), [])

    def test_missing_install_is_reported_not_guessed(self):
        hy = HyMemoryLite(Path(self.temp.name), Path(self.temp.name) / 'data', store=Path(self.temp.name) / 'x')
        self.assertFalse(hy.available())
        with self.assertRaises(HyMemoryError):
            hy.start()


class Broken:
    backend = 'broken fixture'
    def count(self, *a): raise HyMemoryError('worker down')
    def recall(self, *a, **k): raise HyMemoryError('worker down')
    def remember_turn(self, *a, **k): raise HyMemoryError('worker down')
    def backfill(self, *a): raise HyMemoryError('worker down')


class TestChatFallback(unittest.TestCase):
    def test_failed_backend_falls_back_to_keyword_and_says_so(self):
        with tempfile.TemporaryDirectory() as root:
            svc = service(Path(root))
            try:
                svc.memory = Broken()
                session = svc.store.create_session(uuid.uuid4().hex)
                first = svc.submit(session['id'], 'Review NVDA guidance risk', uuid.uuid4().hex, 'facts'); wait(svc, first)
                svc.submit(session['id'], 'What about its price?', uuid.uuid4().hex, 'facts'); wait(svc, svc.store.job(first['id']))
                time_ok = wait(svc, svc.submit(session['id'], 'Back to the guidance risk for NVDA', uuid.uuid4().hex, 'facts'))
                msg = svc.store.session(session['id'])['messages'][-1]
                self.assertEqual([r['run_id'] for r in msg['payload']['memory']['recalled']], [first['id']])
                import json
                trace = json.loads((Path(root) / 'traces' / (time_ok['id'] + '.json')).read_text(encoding='utf-8'))
                names = [s['name'] for s in trace['spans']]
                self.assertIn('Recall conversation memory (keyword fallback)', names)
            finally:
                svc.memory = svc.keyword_memory
                svc.close()


@unittest.skipUnless(os.environ.get('COFINANCE_HYMEMORY_TESTS') == '1', 'set COFINANCE_HYMEMORY_TESTS=1 to start the real worker')
class TestHyMemoryLive(unittest.TestCase):
    def test_real_worker_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            hy = HyMemoryLite(REPO, REPO / 'data', store=Path(tmp))
            try:
                hy.remember_turn('chat', 's1', 'NVDA thesis?', 'Main risk: execution gaps at NVIDIA.', run_id='r1', tickers=['NVDA'])
                hy.remember_turn('chat', 's1', 'Apple news?', 'Apple opened iPhone Duo pre-orders.', run_id='r2', tickers=['AAPL'])
                hits = hy.recall('chat', 's1', 'what was the NVIDIA risk again?', limit=2, exclude_run='r2')
                self.assertEqual([h.run_id for h in hits], ['r1'])
            finally:
                hy.close()


if __name__ == '__main__':
    unittest.main()
