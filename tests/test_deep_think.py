"""Deep think chat switch (2026-11-19): a second server for the Market Team Lead's answer only, with its own
qualification. The qualified server, its flags and every existing fingerprint must stay exactly as they were."""
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from core import local_models as lm
from core.chat_service import ChatService
from core.gliner_decide import GlinerQueryRouter
from core.local_models import BACKENDS, THINK_BUDGET, LocalModels, ModelError
from qualify.history_runtime import THINK_FORMAT, thinking_flags
from tests.test_chat import FakeMarket, wait
from tests.test_orchestrator import StubGliner

REPO = Path(__file__).resolve().parents[1]
MODEL = lm.DEFAULT_NARRATOR


class Alive:
    def __init__(self, log=None):
        self.log = log

    def poll(self):
        return None

    def terminate(self):
        if self.log is not None:
            self.log.append('terminated')

    def wait(self, timeout=None):
        return 0


class TestFlagsAndFingerprints(unittest.TestCase):
    def test_flags_match_the_think_lab_exactly(self):
        for b in BACKENDS:
            self.assertEqual(lm.thinking_server_flags(b), thinking_flags(lm.server_flags(b), THINK_BUDGET))
        self.assertEqual((lm.THINK_FORMAT, THINK_BUDGET), (THINK_FORMAT, 384))  # docs/benchmarks/think-lab-protocol-v1.json

    def test_the_qualified_server_is_unchanged(self):
        for b in BACKENDS:
            flags = lm.server_flags(b)
            self.assertEqual(flags[flags.index('--reasoning') + 1], 'off')
            self.assertNotIn('--reasoning-budget', flags)
            self.assertTrue(lm.runtime_fingerprint(b, MODEL).endswith('greedy-thinking-off-v1'))
            self.assertEqual(lm.server_command('x.exe', 'm.gguf', 'a', 1, 'k', b)[9:-3], flags)
            self.assertEqual(lm.server_command('x.exe', 'm.gguf', 'a', 1, 'k', b, flags=lm.thinking_server_flags(b))[9:-3],
                             lm.thinking_server_flags(b))

    def test_one_report_never_qualifies_the_other(self):
        with tempfile.TemporaryDirectory() as root:
            m = LocalModels(Path(root), REPO)
            normal, think = m.fingerprint('vulkan', MODEL), m.think_fingerprint('vulkan', MODEL)
            self.assertNotEqual(normal, think)
            m.state['think_qualifications'] = {'x': {'passed': True, 'fingerprint': normal, 'model': MODEL}}
            self.assertFalse(m.think_qualified(model=MODEL))
            m.state['qualifications'] = {'y': {'passed': True, 'fingerprint': think, 'model': MODEL}}
            self.assertFalse(m.qualified(model=MODEL))
            m.state['think_qualifications'] = {'x': {'passed': True, 'fingerprint': think, 'model': MODEL}}
            self.assertTrue(m.think_qualified('vulkan', MODEL))
            self.assertFalse(m.think_qualified('cpu', MODEL))
            self.assertFalse(m.think_qualified(model=lm.MODEL_ID))  # another narrator's weights
            m.state['think_qualifications']['x']['passed'] = False
            self.assertFalse(m.think_qualified(model=MODEL))


class TestComplete(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.m = LocalModels(Path(self.temp.name), REPO)
        self.m.process, self.m.ready, self.m.loaded_model, self.m.url, self.m.key = Alive(), True, MODEL, 'http://main', 'mk'
        self.m.state['backend'] = 'vulkan'
        self.calls = []

    def tearDown(self):
        self.m.process, self.m.thinker = None, None
        self.temp.cleanup()

    def fake(self, url, body=None, key='', timeout=90):
        self.calls.append((url, body, key, timeout))
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Answer.', 'reasoning_content': 'hidden'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}

    def qualify(self):
        self.m.state['think_qualifications'] = {'x': {'passed': True, 'fingerprint': self.m.think_fingerprint('vulkan', MODEL), 'model': MODEL}}

    def test_normal_answers_never_think(self):
        with patch('core.local_models.local_json', self.fake):
            self.assertEqual(self.m.complete('s', 'p', max_tokens=256), 'Answer.')
        url, body, key, _ = self.calls[0]
        self.assertEqual((url, key), ('http://main/v1/chat/completions', 'mk'))
        self.assertEqual((body['chat_template_kwargs'], body['max_tokens']), ({'enable_thinking': False}, 256))

    def test_deep_think_needs_its_own_server_and_qualification(self):
        with patch('core.local_models.local_json', self.fake):
            with self.assertRaises(ModelError):
                self.m.complete('s', 'p', think=True)  # no deep-think server
            self.m.thinker = {'process': Alive(), 'url': 'http://think', 'key': 'tk', 'model': MODEL, 'backend': 'vulkan'}
            with self.assertRaises(ModelError):
                self.m.complete('s', 'p', think=True)  # server up, qualification missing
            self.assertEqual(self.calls, [])
            self.qualify()
            self.assertEqual(self.m.complete('s', 'p', max_tokens=256, think=True), 'Answer.')  # reasoning never returned
        url, body, key, timeout = self.calls[-1]
        self.assertEqual((url, key, timeout), ('http://think/v1/chat/completions', 'tk', 180))
        self.assertEqual((body['chat_template_kwargs'], body['max_tokens']), ({'enable_thinking': True}, 256 + THINK_BUDGET))

    def test_unloading_the_narrator_stops_deep_think(self):
        log = []
        self.m.thinker = {'process': Alive(log), 'url': 'http://think', 'key': 'tk', 'model': MODEL, 'backend': 'vulkan'}
        self.m.process = None
        self.m.unload()
        self.assertEqual(log, ['terminated'])
        self.assertIsNone(self.m.thinker)

    def test_thinker_never_starts_without_qualification(self):
        # Everything else is in place (loaded narrator, verified files, a server executable), so only the missing
        # qualification can stop the process from being launched.
        runtime = Path(self.temp.name) / 'runtime'
        runtime.mkdir()
        (runtime / 'llama-server.exe').write_bytes(b'')
        with patch('core.local_models.subprocess.Popen') as popen, patch.object(LocalModels, 'verify'), \
                patch.object(LocalModels, 'backend_dir', return_value=runtime), patch.object(LocalModels, 'installed_backends', return_value=['vulkan']):
            with self.assertRaises(ModelError) as caught:
                self.m.load_thinker()
        popen.assert_not_called()
        self.assertIn('qualification', str(caught.exception))

    def test_stopping_the_narrator_stops_deep_think(self):
        log = []
        self.m.thinker = {'process': Alive(log), 'url': 'http://think', 'key': 'tk', 'model': MODEL, 'backend': 'vulkan'}
        self.m.stop_process()
        self.assertIsNone(self.m.thinker)
        self.assertEqual(log, ['terminated'])

    def test_qualifying_deep_think_needs_a_qualified_narrator_and_the_view_gets_a_summary(self):
        with self.assertRaises(ModelError):
            self.m.qualify_thinking(MODEL)
        with self.assertRaises(ValueError):
            self.m.launch_task('think_everything')
        self.qualify()
        snap = self.m.snapshot()
        self.assertEqual((snap['think']['budget'], snap['think']['qualified']), (THINK_BUDGET, True))
        self.assertNotIn('think_qualifications', snap)


class FakeModels(LocalModels):
    """A qualified narrator that records which calls asked to think."""
    think_ok = True
    start_fails = False

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.thinks, self.started = [], 0

    def qualified(self, backend=None, model=None):
        return True

    def think_qualified(self, backend=None, model=None):
        return self.think_ok

    def load(self, *a, **k):
        self.ready, self.loaded_model = True, MODEL

    def load_thinker(self, *a, **k):
        if self.start_fails:
            raise ModelError('fixture: deep think runtime exited')
        self.started += 1

    def complete(self, system, prompt, *, max_tokens=256, stop=None, think=False):
        self.thinks.append(think)
        return ''


class TestChatSwitch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.models = FakeModels(root / 'local_models', REPO)
        self.service = ChatService(REPO, root, models=self.models, market_factory=FakeMarket,
                                   router_factory=lambda: GlinerQueryRouter(StubGliner(), {'NVDA', 'TSLA', 'BTC', 'AMD'}))
        self.models.state['selected'] = MODEL
        self.sid = self.service.store.create_session(uuid.uuid4().hex)['id']

    def tearDown(self):
        self.models.process = None
        self.service.close()
        self.temp.cleanup()

    def ask(self, think, model=MODEL):
        job = self.service.submit(self.sid, 'Review the NVDA thesis', uuid.uuid4().hex, model, think=think)
        wait(self.service, job)
        return self.service.store.session(self.sid)['messages'][-1]['payload']

    def test_only_the_lead_answer_thinks(self):
        payload = self.ask(True)
        self.assertEqual(payload['deep_think'], {'requested': True, 'used': True})
        self.assertEqual(self.models.started, 1)
        self.assertIn(True, self.models.thinks)
        self.assertIn(False, self.models.thinks)  # the Data Analyst and News Researcher never think

    def test_off_by_default_and_never_starts_the_thinker(self):
        payload = self.ask(False)
        self.assertEqual(payload['deep_think'], {'requested': False, 'used': False})
        self.assertEqual((self.models.started, True in self.models.thinks), (0, False))

    def test_an_answer_without_deep_think_frees_a_running_thinker(self):
        log = []
        self.models.thinker = {'process': Alive(log), 'url': 'http://think', 'key': 'tk', 'model': MODEL, 'backend': 'vulkan'}
        self.ask(False)
        self.assertEqual(log, ['terminated'])
        self.assertIsNone(self.models.thinker)

    def test_a_failed_start_answers_without_it_and_says_so(self):
        self.models.start_fails = True
        payload = self.ask(True)
        self.assertFalse(payload['deep_think']['used'])
        self.assertIn('without it', payload['deep_think']['note'])
        self.assertNotIn(True, self.models.thinks)

    def test_refused_without_qualification_a_narrator_or_a_real_boolean(self):
        self.models.think_ok = False
        with self.assertRaises(ModelError):
            self.service.submit(self.sid, 'NVDA', uuid.uuid4().hex, MODEL, think=True)
        self.models.think_ok = True
        for bad in ('yes', 1, None):
            with self.assertRaises(ValueError):
                self.service.submit(self.sid, 'NVDA', uuid.uuid4().hex, MODEL, think=bad)  # a real narrator, so only the type is wrong
        self.models.state['selected'] = 'facts'
        with self.assertRaises(ValueError):
            self.service.submit(self.sid, 'NVDA', uuid.uuid4().hex, 'facts', think=True)
        with self.assertRaises(ValueError):
            self.service.submit(self.sid, 'NVDA', uuid.uuid4().hex, 'facts', think='yes')
        self.assertEqual(self.service.store.session(self.sid)['messages'], [])


class TestAPI(unittest.TestCase):
    def setUp(self):
        import threading
        from ui.dashboard import create_server
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.models = FakeModels(root / 'local_models', REPO)
        self.models.state['selected'] = MODEL
        self.service = ChatService(REPO, root, models=self.models, market_factory=FakeMarket,
                                   router_factory=lambda: GlinerQueryRouter(StubGliner(), {'NVDA', 'TSLA', 'BTC', 'AMD'}))
        dist = root / 'dist'
        dist.mkdir()
        (dist / 'index.html').write_text('<div id="root"></div>')
        self.server = create_server(port=0, dist=dist, trace_dir=root / 'traces', corpus_dir=root / 'missing', service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.token = self.call('GET', '/api/state')[1]['csrf']
        self.sid = self.service.store.create_session(uuid.uuid4().hex)['id']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.models.process = None
        self.service.close()
        self.temp.cleanup()

    def call(self, method, path, body=None):
        import http.client
        import json
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=60)
        headers = {} if method == 'GET' else {'Origin': self.origin, 'X-Cofinance-CSRF': getattr(self, 'token', ''), 'Content-Type': 'application/json'}
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        status, value = response.status, json.loads(response.read() or b'{}')
        conn.close()
        return status, value

    def chat(self, **extra):
        return self.call('POST', '/api/chat', {'session_id': self.sid, 'query': 'Review NVDA', 'request_id': uuid.uuid4().hex, 'model': MODEL, **extra})

    def test_the_chat_route_takes_an_optional_boolean_think_and_nothing_else(self):
        status, job = self.chat()  # old clients send no flag
        self.assertEqual(status, 202)
        wait(self.service, job)
        status, job = self.chat(think=True)
        self.assertEqual(status, 202)
        wait(self.service, job)
        self.assertEqual(self.chat(think='yes')[0], 400)
        self.assertEqual(self.chat(thinking=True)[0], 400)
        self.models.think_ok = False
        status, body = self.chat(think=True)
        self.assertEqual(status, 409)
        self.assertIn('qualification', body['error'])

    def test_the_state_the_screen_reads_carries_a_think_summary_and_no_report_bodies(self):
        status, state = self.call('GET', '/api/state')
        self.assertEqual(status, 200)
        think = state['models']['think']
        self.assertEqual((think['budget'], think['qualified'], think['loaded']), (THINK_BUDGET, True, False))
        self.assertNotIn('think_qualifications', state['models'])


if __name__ == '__main__':
    unittest.main()
