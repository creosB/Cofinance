import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core import router_worker
from core.chat_service import ChatService, _NoTrace

REPO = Path(__file__).resolve().parents[1]


class FakeRouter:
    """Stands in for the GLiNER subprocess: counts real starts, takes a moment to load."""
    starts = 0
    fail = False

    def __init__(self, repo, logs, cancel=None):
        self.cancel, self.process = cancel, None

    def start(self):
        if self.process:
            return
        time.sleep(0.2)
        if FakeRouter.fail:
            raise RuntimeError("load failed")
        FakeRouter.starts += 1
        self.process = mock.Mock(poll=lambda: None)

    def close(self):
        self.process = None


class TestRouterWarmup(unittest.TestCase):
    def setUp(self):
        FakeRouter.starts, FakeRouter.fail = 0, False
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(router_worker, "RouterProcess", FakeRouter)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = ChatService(REPO, self.tmp.name, market_factory=object)
        self.service.gliner_path = lambda: Path(self.tmp.name)  # an existing path stands in for the pinned cache

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_warmup_loads_once_and_a_racing_chat_reuses_it(self):
        warm = self.service.warm_router()
        got = []
        chat = threading.Thread(target=lambda: got.append(self.service.local_extractor(_NoTrace(), threading.Event())))
        chat.start(); warm.join(5); chat.join(5)
        self.assertEqual(FakeRouter.starts, 1)  # never two GLiNER processes
        self.assertIs(got[0], self.service.extractor)
        self.assertEqual(self.service.warm["state"], "loaded")
        self.assertEqual(self.service.models.state["router"], "loaded")

    def test_failed_start_never_stays_loading(self):
        FakeRouter.fail = True
        self.service.warm_router().join(5)
        self.assertEqual(self.service.warm["state"], "failed")
        self.assertEqual(self.service.models.state["router"], "cached")
        FakeRouter.fail = False
        self.service.local_extractor(_NoTrace(), threading.Event())  # the first chat still loads it, as before
        self.assertEqual(FakeRouter.starts, 1)

    def test_switch_off_and_test_routers_skip_the_warmup(self):
        with mock.patch.dict(os.environ, {"COFINANCE_ROUTER_WARMUP": "off"}):
            self.assertIsNone(self.service.warm_router())
        self.service.router_factory = object
        self.assertIsNone(self.service.warm_router())
        self.assertEqual(FakeRouter.starts, 0)
        self.assertEqual(self.service.warm["state"], "skipped")

    def test_dashboard_main_keeps_both_runtimes_cold(self):
        from ui import dashboard

        service, server = mock.Mock(), mock.Mock(server_port=8878)
        with mock.patch("sys.argv", ["dashboard"]), \
             mock.patch.object(dashboard, "DIST", mock.MagicMock()), \
             mock.patch("core.chat_service.ChatService", return_value=service), \
             mock.patch.object(dashboard, "create_server", return_value=server):
            dashboard.main()
        server.serve_forever.assert_called_once_with()
        service.warm_router.assert_not_called()
        service.warm_narrator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
