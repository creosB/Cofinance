"""The chat job statechart: random operation sequences against the real store, checked step by step against NEXT."""
import random
import re
import tempfile
import unittest
import uuid
from pathlib import Path

from core.chat_store import ACTIVE, FINISHED, NEXT, STATUSES, STOPPED, ChatStore, statechart

ROOT = Path(__file__).resolve().parents[1]
OPS = ['start', 'stage', 'stop', 'cancelled', 'error', 'interrupted', 'recover', 'complete', 'degraded', 'bad']


def assistant_messages(store, session_id, run_id):
    return sum(1 for m in store.session(session_id)['messages'] if m['role'] == 'assistant' and m['run_id'] == run_id)


class TestJobStatechart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.tmp.name) / 'chat.db')

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, run, op, model):
        """Run one operation on the store and return the status the chart predicts."""
        moves = NEXT.get(model, frozenset())
        if op == 'start':
            self.store.progress(run, 'Starting', 'running')
            return 'running' if 'running' in moves else model
        if op == 'stage':
            self.store.progress(run, 'Working')
            return model
        if op == 'stop':
            self.store.progress(run, 'Stopping', 'cancelling')
            return 'cancelling' if 'cancelling' in moves else model
        if op in STOPPED:
            self.store.progress(run, 'Ended', op, 'reason')
            return op if op in moves else model
        if op == 'recover':
            self.store.recover()
            return 'interrupted' if model in ACTIVE else model
        if op in FINISHED:
            if model == 'running':
                self.store.finish(run, 'answer', {'kind': 'research'}, ['NVDA'], op)
                return op
            with self.assertRaises(ValueError):
                self.store.finish(run, 'late answer', {'kind': 'research'}, ['NVDA'], op)
            return model
        for status in ('complete', 'degraded', 'done', ''):
            with self.assertRaises(ValueError):
                self.store.progress(run, 'Skipping the answer', status)
        return model

    def test_random_sequences_follow_the_chart(self):
        rng = random.Random(20261116)
        seen = set()
        for _ in range(120):
            session = self.store.create_session(uuid.uuid4().hex)['id']
            run = uuid.uuid4().hex
            self.store.create_job(run, uuid.uuid4().hex, session, 'NVDA', 'facts')
            model, ended = 'queued', None
            for op in rng.choices(OPS, k=8):
                before = self.store.job(run)
                model = self.apply(run, op, model)
                job = self.store.job(run)
                seen.add((before['status'], job['status']))
                self.assertIn(job['status'], STATUSES)
                self.assertEqual(job['status'], model, f'{op} from {before["status"]}')
                self.assertEqual(assistant_messages(self.store, session, run), 1 if model in FINISHED else 0)
                if ended:
                    self.assertEqual(job, ended, 'An ended run changed.')
                elif job['status'] not in ACTIVE:
                    ended = job
        drawn = {(a, b) for a, moves in NEXT.items() for b in moves}
        self.assertEqual(drawn - seen, set(), 'Some drawn moves were never exercised; raise the sequence count.')

    def test_doc_diagram_matches_the_code(self):
        doc = (ROOT / 'docs/statecharts.md').read_text(encoding='utf-8')
        block = re.search(r'## Chat job.*?```mermaid\n(.*?)```', doc, re.S)
        self.assertIsNotNone(block, 'docs/statecharts.md lost its chat job diagram.')
        self.assertEqual(block.group(1), statechart(), 'Redraw docs/statecharts.md from core.chat_store.statechart().')

    def test_ui_knows_every_status(self):
        ui = (ROOT / 'frontend/src/lib/use-chat.ts').read_text(encoding='utf-8')
        listed = re.search(r"const statuses=\[([^\]]*)\]", ui)
        self.assertIsNotNone(listed)
        self.assertEqual(set(re.findall(r"'([a-z]+)'", listed.group(1))), set(STATUSES),
                         'The UI rejects a job whose status it does not list. Keep use-chat.ts statuses equal to STATUSES.')


if __name__ == '__main__':
    unittest.main()
