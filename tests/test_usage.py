import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from core.local_models import LocalModels, ModelError, Cancelled, DEFAULT_NARRATOR
from core.run_trace import TraceRecorder, TracedLLM, safe
from core.usage import counts, capture_usage, record_usage, summarize
from core.chat_store import ChatStore
from tests.test_chat import REPO, service, wait
from tests.test_orchestrator import Stub1BLLM
from ui.workbench import read_trace


def response(i=101, o=17, reason='stop', content='A response'):
    return {'choices': [{'finish_reason': reason, 'message': {'content': content}}],
            'usage': {'prompt_tokens': i, 'completion_tokens': o, 'total_tokens': i + o}}


def ready(root):
    m = LocalModels(root / 'models', REPO)
    m.ready = True
    m.process = Mock(poll=lambda: None)
    m.loaded_model = DEFAULT_NARRATOR
    m.url = 'http://127.0.0.1:9999'
    return m


class TestUsage(unittest.TestCase):
    def test_numeric_allowlist_never_loosens_secret_redaction(self):
        for value in (None, [], {}, {'input': True, 'output': 2, 'total': 3},
                      {'input': -1, 'output': 2, 'total': 1}, {'input': '1', 'output': 2, 'total': 3},
                      {'input': 1.0, 'output': 2, 'total': 3}, {'input': 1, 'output': 2, 'total': 4},
                      {'input': 1, 'output': float('nan'), 'total': 1}):
            self.assertIsNone(counts(value), value)
        self.assertEqual(counts({'input': 3, 'output': 2, 'total': 5, 'api_key': 'secret'}),
                         {'input': 3, 'output': 2, 'total': 5})
        self.assertEqual(safe({'access_token': 'secret', 'token_count': 'secret'}),
                         {'access_token': '[redacted]', 'token_count': '[redacted]'})

    def test_runtime_receipts_survive_rejected_output_cancel_and_api_redaction(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); m = ready(root); t = TraceRecorder('Usage fixture', mode='fixture', directory=root/'traces')
            llm = TracedLLM(m, t, 'Data Analyst')
            with patch('core.local_models.local_json', return_value=response()):
                self.assertEqual(llm.complete('private system', 'facts'), 'A response')
            with patch('core.local_models.local_json', return_value=response(80, 30, reason='length')):
                with self.assertRaises(ModelError): llm.complete('system', 'facts')
            def cancel(*a, **k):
                m.cancel.set(); return response(7, 3)
            with patch('core.local_models.local_json', side_effect=cancel):
                with self.assertRaises(Cancelled): llm.complete('system', 'facts')
            m.cancel.clear()
            t.finish()
            raw = read_trace(root/'traces', t.run_id)
            self.assertEqual(raw['usage']['input'], 188)
            self.assertEqual(raw['usage']['output'], 50)
            self.assertEqual(raw['usage']['total'], 238)
            self.assertEqual(raw['usage']['attempts'], 3)
            self.assertEqual(raw['usage']['reported'], 3)
            self.assertTrue(raw['usage']['complete'])
            self.assertEqual(raw['spans'][1]['usage']['total'], 110)
            self.assertNotIn('private system', json.dumps(raw))

    def test_unknown_timeout_does_not_reuse_previous_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            m = ready(Path(d)); t = TraceRecorder('Unknown'); llm = TracedLLM(m, t, 'Market Team Lead')
            with patch('core.local_models.local_json', side_effect=[response(), TimeoutError(), {'choices': response()['choices']}]):
                llm.complete('s', 'p')
                with self.assertRaises(TimeoutError): llm.complete('s', 'p')
                llm.complete('s', 'p')
            t.finish(); u = t.data['usage']
            self.assertEqual((u['attempts'], u['reported'], u['total']), (3, 1, 118))
            self.assertFalse(u['complete'])
            self.assertIsNone(t.data['spans'][1]['usage'])

    def test_non_narrator_calls_and_disabled_backend_are_not_counted(self):
        t = TraceRecorder('No narrator')
        t.call('Classify heads', 'model', lambda: 'labels', actor='Routing executive')
        TracedLLM(type('Disabled', (), {'disabled': True})(), t, 'Data Analyst').complete('s', 'p')
        t.finish()
        self.assertEqual((t.data['usage']['total'], t.data['usage']['attempts']), (0, 0))
        self.assertTrue(t.data['usage']['complete'])
        self.assertFalse(summarize([], dropped=1)['complete'])

    def test_receipts_are_isolated_between_threads_and_nested_captures(self):
        barrier = threading.Barrier(2); got = {}
        def run(n):
            with capture_usage() as receipts:
                record_usage(response(n, 1)['usage']); barrier.wait()
                got[n] = receipts
        a = threading.Thread(target=run, args=(11,)); b = threading.Thread(target=run, args=(23,))
        a.start(); b.start(); a.join(); b.join()
        self.assertEqual(got[11], [{'input': 11, 'output': 1, 'total': 12}])
        self.assertEqual(got[23], [{'input': 23, 'output': 1, 'total': 24}])
        with capture_usage() as outer:
            with capture_usage() as inner: record_usage(response()['usage'])
            record_usage(response(2, 3)['usage'])
        self.assertEqual(len(inner), 1); self.assertEqual(outer[0]['total'], 5)

    def test_receipts_are_not_shared_between_roles_and_retries_count(self):
        class Backend:
            model_id = 'fixture'
            def complete(self, *a, **k):
                record_usage(response(4, 2)['usage']); return 'text'
        t = TraceRecorder('Retries')
        for role in ('Data Analyst', 'Market Team Lead', 'Market Team Lead'):
            TracedLLM(Backend(), t, role).complete('s', 'p')
        t.finish(); u = t.data['usage']
        self.assertEqual(u['total'], 18)
        self.assertEqual([(r['actor'], r['attempts'], r['total']) for r in u['roles']],
                         [('Data Analyst', 1, 6), ('Market Team Lead', 2, 12)])

    def test_uninstrumented_backend_is_unknown_not_zero(self):
        t = TraceRecorder('Old backend')
        TracedLLM(Stub1BLLM(), t, 'Data Analyst').complete('s', 'p'); t.finish()
        self.assertEqual(t.data['usage']['reported'], 0)
        self.assertFalse(t.data['usage']['complete'])

    def test_chat_saves_exact_counts_across_reload_and_idempotent_retry(self):
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'COFINANCE_MEMORY': 'keyword'}):
            root = Path(d); s = service(root); m = s.models
            m.state['selected'] = DEFAULT_NARRATOR
            m.ready = True; m.loaded_model = DEFAULT_NARRATOR; m.process = Mock(poll=lambda: None)
            m.url = 'http://127.0.0.1:9999'
            stub = Stub1BLLM(hallucinate_lead=True)
            def runtime(url, body, **kw):
                text = stub.complete(body['messages'][0]['content'], body['messages'][1]['content'])
                return response(101, 17, content=text)
            session = s.store.create_session(uuid.uuid4().hex); request = uuid.uuid4().hex
            try:
                with patch.object(m, 'load'), patch.object(m, 'qualified', return_value=True), patch('core.local_models.local_json', side_effect=runtime):
                    job = s.submit(session['id'], 'Review NVDA', request, DEFAULT_NARRATOR); wait(s, job)
                    self.assertEqual(s.submit(session['id'], 'Review NVDA', request, DEFAULT_NARRATOR)['id'], job['id'])
                stored = ChatStore(root/'chat_v2.db').session(session['id'])
                u = stored['messages'][-1]['payload']['usage']
                self.assertGreaterEqual(len(stub.calls), 3)  # analyst + rejected lead + retry
                self.assertEqual(u['total'], len(stub.calls)*118)
                self.assertEqual(u['attempts'], len(stub.calls)); self.assertTrue(u['complete'])
                self.assertEqual(read_trace(root/'traces', job['id'])['usage'], u)
                self.assertEqual(len(stored['messages']), 2)
            finally:
                m.process = None; s.close()

    def test_tool_only_chat_persists_zero_without_inventing_legacy_counts(self):
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'COFINANCE_MEMORY': 'keyword'}):
            s = service(Path(d)); session = s.store.create_session(uuid.uuid4().hex)
            try:
                job = s.submit(session['id'], 'Review NVDA', uuid.uuid4().hex, 'facts'); wait(s, job)
                u = s.store.session(session['id'])['messages'][-1]['payload']['usage']
                self.assertEqual(u['attempts'], 0); self.assertEqual(u['total'], 0); self.assertTrue(u['complete'])
            finally: s.close()

if __name__ == '__main__': unittest.main()
