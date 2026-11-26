"""/api/events: a read-only change stream for the chat view (replaces fixed 1.2 s polling while connected)."""
import http.client
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from ui import dashboard


class Store:
    def __init__(self):
        self.sessions = {'s1': {'id': 's1', 'turns': 0}}

    def session(self, sid):
        if sid not in self.sessions:
            raise KeyError(sid)
        return self.sessions[sid]


class Service:
    closed = False

    def __init__(self):
        self.store, self.revision = Store(), 0

    def snapshot(self):
        return {'revision': self.revision}


class TestEventStream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.service = Service()
        patches = [mock.patch.object(dashboard, 'EVENT_TICK_S', 0.05), mock.patch.object(dashboard, 'EVENT_LIFE_S', 1.5)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.server = dashboard.create_server(port=0, trace_dir=root / 'traces', corpus_dir=root / 'none', service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.service.closed = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def open(self, path, **headers):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        c.request('GET', path, headers={'Host': f'127.0.0.1:{self.port}', **headers})
        return c.getresponse()

    def test_sends_a_change_on_connect_and_after_each_update(self):
        r = self.open('/api/events?session_id=s1')
        self.assertEqual(r.status, 200)
        self.assertTrue(r.getheader('Content-Type').startswith('text/event-stream'))
        threading.Timer(0.3, lambda: setattr(self.service, 'revision', 1)).start()
        threading.Timer(0.6, lambda: self.service.store.sessions['s1'].update(turns=1)).start()
        body = r.read().decode()             # the stream ends after EVENT_LIFE_S
        self.assertTrue(body.startswith('retry: 3000'))
        self.assertEqual(body.count('event: change'), 3)   # connect, state change, conversation change
        self.assertNotIn('revision', body)   # fingerprints only, never the data

    def test_unknown_session_still_streams_state_changes(self):
        r = self.open('/api/events?session_id=nope')
        threading.Timer(0.3, lambda: setattr(self.service, 'revision', 5)).start()
        self.assertEqual(r.read().decode().count('event: change'), 2)

    def test_rejects_other_sites_and_extra_parameters(self):
        self.assertEqual(self.open('/api/events', **{'Sec-Fetch-Site': 'cross-site'}).status, 403)
        self.assertEqual(self.open('/api/events', Origin='http://evil.example').status, 403)
        self.assertEqual(self.open('/api/events?session_id=s1&x=1').status, 400)

    def test_stops_when_the_service_closes(self):
        r = self.open('/api/events')
        threading.Timer(0.2, lambda: setattr(self.service, 'closed', True)).start()
        started = time.monotonic()
        r.read()
        self.assertLess(time.monotonic() - started, 1.2)


class TestEventDrivenSSE(unittest.TestCase):
    def setUp(self):
        from core.chat_service import ChatService
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = ChatService(Path(__file__).resolve().parents[1], root=self.root)
        patches = [
            mock.patch.object(dashboard, 'EVENT_TICK_S', 1.5),
            mock.patch.object(dashboard, 'EVENT_KEEPALIVE_S', 2.0),
            mock.patch.object(dashboard, 'EVENT_LIFE_S', 4.0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.server = dashboard.create_server(
            port=0,
            trace_dir=self.root / 'traces',
            corpus_dir=self.root / 'none',
            service=self.service,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.service.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def _read_event(self, resp):
        lines = []
        while True:
            raw = resp.readline()
            if not raw:
                return ''.join(lines)
            line = raw.decode('utf-8')
            lines.append(line)
            if line == '\n' and any(x.startswith('event: change') for x in lines):
                return ''.join(lines)

    def test_store_and_model_updates_wake_stream_immediately_without_polling(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request('GET', '/api/events', headers={'Host': f'127.0.0.1:{self.port}'})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)

        first = self._read_event(resp)
        self.assertIn('event: change', first)

        t0 = time.monotonic()
        self.service.store.add_watch('NVDA')
        second = self._read_event(resp)
        elapsed_store = time.monotonic() - t0
        self.assertIn('event: change', second)
        self.assertNotEqual(first, second)
        self.assertLess(elapsed_store, 0.25)

        t1 = time.monotonic()
        self.service.models.update(stage='Model check')
        third = self._read_event(resp)
        elapsed_model = time.monotonic() - t1
        self.assertIn('event: change', third)
        self.assertNotEqual(second, third)
        self.assertLess(elapsed_model, 0.25)

        t2 = time.monotonic()
        self.service.close()
        rest = resp.read()
        elapsed_close = time.monotonic() - t2
        self.assertEqual(rest, b'')
        self.assertLess(elapsed_close, 0.25)

    def test_close_wakes_stream_before_slow_model_shutdown(self):
        orig_close = self.service.models.close
        self.service.models.close = lambda: (time.sleep(0.5), orig_close())
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request('GET', '/api/events', headers={'Host': f'127.0.0.1:{self.port}'})
        resp = conn.getresponse()
        self._read_event(resp)

        t0 = time.monotonic()
        closer = threading.Thread(target=self.service.close, daemon=True)
        closer.start()
        rest = resp.read()
        elapsed = time.monotonic() - t0
        closer.join(timeout=2)
        self.assertEqual(rest, b'')
        self.assertLess(elapsed, 0.25)

    def test_no_lost_wakeup_when_mutation_occurs_during_event_mark(self):
        orig_snapshot = self.service.snapshot
        injected = {'done': False}

        def racing_snapshot():
            snap = orig_snapshot()
            if not injected['done']:
                injected['done'] = True
                self.service.store.add_watch('AAPL')
            return snap

        self.service.snapshot = racing_snapshot
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request('GET', '/api/events', headers={'Host': f'127.0.0.1:{self.port}'})
        resp = conn.getresponse()

        first = self._read_event(resp)
        t0 = time.monotonic()
        second = self._read_event(resp)
        elapsed = time.monotonic() - t0
        self.assertIn('event: change', first)
        self.assertIn('event: change', second)
        self.assertNotEqual(first, second)
        self.assertLess(elapsed, 0.25)


if __name__ == '__main__':
    unittest.main()
