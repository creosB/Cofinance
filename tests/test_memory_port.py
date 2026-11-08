import tempfile
import unittest
import uuid
from pathlib import Path

from core.memory import LocalKeywordMemory, MemoryHit, context_from, query_terms
from tests.test_chat import service, wait


class TestLocalKeywordMemory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.memory = LocalKeywordMemory(Path(self.temp.name) / 'memory_v2.db')

    def tearDown(self):
        self.temp.cleanup()

    def test_only_chat_namespace_is_writable(self):
        for namespace in ('news', 'watchlist', ''):
            with self.assertRaises(ValueError):
                self.memory.remember_turn(namespace, 's1', 'q', 'a', run_id='r1')
            with self.assertRaises(ValueError):
                self.memory.recall(namespace, 's1', 'guidance')

    def test_recall_is_scoped_to_one_conversation(self):
        self.memory.remember_turn('chat', 's1', 'NVDA guidance risk', 'Guidance depends on data center demand.', run_id='r1')
        self.memory.remember_turn('chat', 's2', 'NVDA guidance risk', 'Other chat answer.', run_id='r2')
        hits = self.memory.recall('chat', 's1', 'guidance risk again')
        self.assertEqual([h.run_id for h in hits], ['r1'])

    def test_same_run_is_stored_once(self):
        one = self.memory.remember_turn('chat', 's1', 'q guidance', 'answer guidance', run_id='r1')
        two = self.memory.remember_turn('chat', 's1', 'q guidance', 'answer guidance', run_id='r1')
        self.assertEqual(one, two)
        self.assertEqual(self.memory.count('chat', 's1'), 1)

    def test_latest_run_can_be_excluded(self):
        self.memory.remember_turn('chat', 's1', 'NVDA guidance', 'first guidance answer', run_id='r1')
        self.memory.remember_turn('chat', 's1', 'NVDA guidance price', 'second guidance answer', run_id='r2')
        hits = self.memory.recall('chat', 's1', 'guidance', exclude_run='r2')
        self.assertEqual([h.run_id for h in hits], ['r1'])

    def test_empty_answers_are_not_remembered(self):
        with self.assertRaises(ValueError):
            self.memory.remember_turn('chat', 's1', 'q', '   ', run_id='r1')

    def test_stopwords_only_query_recalls_nothing(self):
        self.memory.remember_turn('chat', 's1', 'NVDA guidance', 'answer', run_id='r1')
        self.assertEqual(query_terms('what did you say before?'), [])
        self.assertEqual(self.memory.recall('chat', 's1', 'what did you say before?'), [])

    def test_backfill_indexes_saved_research_turns_idempotently(self):
        session = {'id': 's1', 'messages': [
            {'role': 'user', 'content': 'Review NVDA guidance'},
            {'role': 'assistant', 'content': 'Guidance answer.', 'run_id': 'r1', 'payload': {'kind': 'research', 'tickers': ['NVDA']}},
            {'role': 'user', 'content': 'Which asset?'},
            {'role': 'assistant', 'content': 'Name a ticker.', 'run_id': 'r2', 'payload': {'kind': 'clarification'}}]}
        self.assertEqual(self.memory.backfill('chat', session), 1)
        self.memory.backfill('chat', session)
        self.assertEqual(self.memory.count('chat', 's1'), 1)

    def test_context_is_bounded_and_skips_duplicate_previous_answer(self):
        hit = MemoryHit('m', 's1', 'Earlier question', 'Same answer', 'r1', 0.0, 1.0)
        self.assertEqual(context_from('Same answer', [hit]), 'Same answer')
        long = MemoryHit('m', 's1', 'q', 'x ' * 2000, 'r1', 0.0, 1.0)
        self.assertLessEqual(len(context_from('', [long])), 1400)


class TestChatServiceRecall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = service(Path(self.temp.name))
        self.session = self.service.store.create_session(uuid.uuid4().hex)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def ask(self, query):
        job = self.service.submit(self.session['id'], query, uuid.uuid4().hex, 'facts')
        wait(self.service, job)
        return job, self.service.store.session(self.session['id'])['messages'][-1]

    def test_third_turn_recalls_the_first_not_the_latest(self):
        first, _ = self.ask('Review NVDA guidance risk')
        self.ask('What about its price?')
        _, third = self.ask('Back to the guidance risk for NVDA')
        memory = third['payload']['memory']
        self.assertTrue(memory['used_previous'])
        self.assertEqual([r['run_id'] for r in memory['recalled']], [first['id']])

    def test_slow_memory_write_does_not_hold_the_run_slot(self):
        # Live bug 2026-11-08: the first Hy-Memory write starts the embedder (seconds), and the next question got a 409.
        import threading
        started, release = threading.Event(), threading.Event()
        class Slow:
            backend = 'slow fake'
            def count(self, *a): return 1
            def recall(self, *a, **k): return []
            def backfill(self, *a): return 0
            def remember_turn(self, *a, **k):
                started.set(); release.wait(5); return 'x'
        self.service.memory = Slow()
        try:
            job = self.service.submit(self.session['id'], 'Review NVDA guidance risk', uuid.uuid4().hex, 'facts')
            self.assertTrue(started.wait(5))
            self.assertIsNone(self.service.active)
            self.assertNotIn(self.service.store.job(job['id'])['status'], {'queued', 'running', 'cancelling'})
            nxt = self.service.submit(self.session['id'], 'What about its price?', uuid.uuid4().hex, 'facts')  # no 409
        finally:
            release.set()
        wait(self.service, nxt)

    def test_other_asset_turns_are_not_context(self):
        self.ask('Review NVDA guidance risk')
        _, tesla = self.ask('Review TSLA guidance risk')
        memory = tesla['payload']['memory']
        self.assertFalse(memory['used_previous'])
        self.assertEqual(memory['recalled'], [])
        self.assertEqual(tesla['payload']['tickers'], ['TSLA'])

    def test_other_conversation_recalls_nothing(self):
        self.ask('Review NVDA guidance risk')
        other = self.service.store.create_session(uuid.uuid4().hex)
        job = self.service.submit(other['id'], 'NVDA guidance risk', uuid.uuid4().hex, 'facts')
        wait(self.service, job)
        message = self.service.store.session(other['id'])['messages'][-1]
        self.assertEqual(message['payload']['memory']['recalled'], [])
        self.assertFalse(message['payload']['memory']['used_previous'])


if __name__ == '__main__':
    unittest.main()
