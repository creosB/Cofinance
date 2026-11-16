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


if __name__ == '__main__':
    unittest.main()
