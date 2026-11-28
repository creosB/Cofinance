import gc
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
import weakref
from pathlib import Path
from unittest import mock

import numpy as np

from core import gliner_onnx as go
from core import hymemory_port as hp
from core import local_models as lm
from core import process_guard as pg
from core import router_worker as rw

REPO = Path(__file__).resolve().parents[1]


class _FakeProcess:
    def __init__(self, *, stdout_lines=()):
        self.alive = True
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("".join(stdout_lines))
        self.terminated = 0

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated += 1
        self.alive = False

    def kill(self):
        self.alive = False

    def wait(self, timeout=None):
        self.alive = False
        return 0


class _FakeEncoding:
    def __init__(self, ids):
        self.ids = ids


class _FakeTokenizer:
    def __init__(self):
        self._vocab = {
            "[PAD]": 0,
            go.SEP_STRUCT: 1,
            go.SEP_TEXT: 2,
            go.P_TOKEN: 3,
            go.E_TOKEN: 4,
            go.L_TOKEN: 5,
            go.DESC_TOKEN: 6,
        }

    def token_to_id(self, tok):
        return self._vocab.get(tok)

    def encode(self, piece, add_special_tokens=False):
        return _FakeEncoding([10 + (len(piece) % 50)])


def _fake_heads():
    return {k: np.zeros((2, 2), dtype=np.float32) for k in go.HEAD_KEYS}


class TestGlobalModelOwnerAndLeaks(unittest.TestCase):
    def setUp(self):
        self.lock_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.lock_tmp.cleanup)
        self.env_patch = mock.patch.dict(
            os.environ,
            {"COFINANCE_LOCK_DIR": self.lock_tmp.name, "COFINANCE_SKIP_MEM_CHECK": "0"},
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_memory_headroom_checks_both_available_ram_and_commit_percent(self):
        with mock.patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 900, "commit_pct": 50}):
            with self.assertRaisesRegex(RuntimeError, "900 MB free"):
                pg.require_memory_headroom(1400, "narrator", error_type=RuntimeError)

        with mock.patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 4096, "commit_pct": 88}):
            with self.assertRaisesRegex(RuntimeError, "88% committed"):
                pg.require_memory_headroom(1400, "narrator", max_commit_pct=85, error_type=RuntimeError)

        with mock.patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 4096, "commit_pct": 62}):
            pg.require_memory_headroom(1400, "narrator", max_commit_pct=85, error_type=RuntimeError)

    def test_global_slot_lock_blocks_cross_process_duplicate_across_roots(self):
        code = (
            "import os, sys, time; "
            "from core.process_guard import global_slot_lock; "
            "lock = global_slot_lock('narrator', 'test narrator', error_type=RuntimeError).acquire(); "
            "print('LOCKED', flush=True); "
            "sys.stdin.readline(); "
            "lock.release()"
        )
        env = dict(os.environ, PYTHONPATH=str(REPO), COFINANCE_LOCK_DIR=self.lock_tmp.name)
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", code],
            cwd=str(REPO),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            line = child.stdout.readline().strip()
            if line != "LOCKED":
                self.fail(f"Child did not lock (got {line!r}): {child.stderr.read()}")
            with self.assertRaisesRegex(RuntimeError, "already"):
                pg.global_slot_lock("narrator", "test narrator", error_type=RuntimeError).acquire()
            if os.name == "nt":
                mutex_name = pg.global_slot_lock("narrator", "test narrator", error_type=RuntimeError).mutex_name
                with tempfile.TemporaryDirectory() as other_dir:
                    with self.assertRaisesRegex(RuntimeError, "already"):
                        pg.ProcessSlotLock(
                            Path(other_dir) / "different.lock",
                            "test narrator",
                            error_type=RuntimeError,
                            mutex_name=mutex_name,
                        ).acquire()
        finally:
            try:
                child.stdin.write("done\n")
                child.stdin.flush()
            except OSError:
                pass
            child.wait(timeout=5)
            child.stdin.close()
            child.stdout.close()
            child.stderr.close()

        after = pg.global_slot_lock("narrator", "test narrator", error_type=RuntimeError).acquire()
        after.release()

    def test_router_global_lock_blocks_second_root_until_first_closes(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2, \
             mock.patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 6000, "commit_pct": 40}):
            p1 = _FakeProcess(stdout_lines=['{"ready": true, "backend": "standalone_fp32"}\n'])
            r1 = rw.RouterProcess(REPO, Path(d1))
            r2 = rw.RouterProcess(REPO, Path(d2))
            with mock.patch.object(rw, "spawn_managed", return_value=p1):
                r1.start()
                try:
                    with self.assertRaisesRegex(lm.ModelError, "already"):
                        r2.start()
                finally:
                    r1.close()
            p2 = _FakeProcess(stdout_lines=['{"ready": true, "backend": "standalone_fp32"}\n'])
            with mock.patch.object(rw, "spawn_managed", return_value=p2):
                r2.start()
                r2.close()

    def test_local_models_unload_releases_global_lock_and_clears_loaded_model(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2, \
             mock.patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 6000, "commit_pct": 40}), \
             mock.patch.object(lm.LocalModels, "verify"), \
             mock.patch.object(lm, "local_json", return_value={"status": "ok"}):
            for root in (d1, d2):
                exe = Path(root) / lm.BACKENDS["vulkan"]["dir"] / "llama-server.exe"
                exe.parent.mkdir(parents=True, exist_ok=True)
                exe.write_bytes(b"")
            m1 = lm.LocalModels(Path(d1), REPO)
            m2 = lm.LocalModels(Path(d2), REPO)
            with mock.patch.object(lm, "spawn_managed", side_effect=lambda *a, **k: _FakeProcess()):
                m1.start("vulkan", False, [])
                self.assertIsNotNone(m1._server_lock)
                self.assertIsNotNone(m1.loaded_model)
                with self.assertRaisesRegex(lm.ModelError, "already"):
                    m2.start("vulkan", False, [])
                m1.unload(keep_choice=True)
                self.assertIsNone(m1._server_lock)
                self.assertIsNone(m1.loaded_model)
                m2.start("vulkan", False, [])
                m2.close()
                self.assertIsNone(m2._server_lock)

    def test_thinker_global_lock_blocks_second_root_and_releases_on_stop_or_failure(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2, \
             mock.patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 6000, "commit_pct": 40}), \
             mock.patch.object(lm.LocalModels, "verify"), \
             mock.patch.object(lm.LocalModels, "installed_backends", return_value=["vulkan"]), \
             mock.patch.object(lm.LocalModels, "think_qualified", return_value=True), \
             mock.patch.object(lm, "local_json", return_value={"status": "ok"}):
            for root in (d1, d2):
                exe = Path(root) / lm.BACKENDS["vulkan"]["dir"] / "llama-server.exe"
                exe.parent.mkdir(parents=True, exist_ok=True)
                exe.write_bytes(b"")
            m1 = lm.LocalModels(Path(d1), REPO)
            m2 = lm.LocalModels(Path(d2), REPO)
            with mock.patch.object(lm, "spawn_managed", side_effect=lambda *a, **k: _FakeProcess()):
                m1.load_thinker(backend="vulkan")
                self.assertIsNotNone(m1._thinker_lock)
                with self.assertRaisesRegex(lm.ModelError, "already"):
                    m2.load_thinker(backend="vulkan")
                m1.stop_thinker()
                self.assertIsNone(m1._thinker_lock)
                m2.load_thinker(backend="vulkan")
                m2.stop_thinker()
                self.assertIsNone(m2._thinker_lock)

    def test_hymemory_trims_embedder_closes_log_handles_and_enforces_global_lock(self):
        cmd = hp.embedder_command(Path("llama-server.exe"), Path("embed.gguf"), 51234)
        self.assertEqual(cmd[cmd.index("--parallel") + 1], "1")
        self.assertEqual(cmd[cmd.index("-c") + 1], "2048")
        self.assertEqual(cmd[cmd.index("-b") + 1], "2048")
        self.assertEqual(cmd[cmd.index("-ub") + 1], "2048")
        self.assertEqual(cmd[cmd.index("--threads") + 1], "4")
        self.assertIn("--no-webui", cmd)
        self.assertNotIn("8192", cmd)

        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2, \
             mock.patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 6000, "commit_pct": 40}):
            opened_logs = []

            def fake_spawn(command, *, error_type=OSError, **options):
                for key in ("stdout", "stderr"):
                    stream = options.get(key)
                    if hasattr(stream, "closed"):
                        opened_logs.append(stream)
                if "hymemory_worker.py" in " ".join(command):
                    return _FakeProcess(stdout_lines=['{"ok": true, "ready": true}\n'])
                return _FakeProcess()

            class _OkResp:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *a): return False

            h1 = hp.HyMemoryLite(REPO, Path(d1) / "data", store=Path(d1) / "store")
            h2 = hp.HyMemoryLite(REPO, Path(d2) / "data", store=Path(d2) / "store")
            with mock.patch.object(hp.HyMemoryLite, "available", return_value=True), \
                 mock.patch.object(hp, "spawn_managed", side_effect=fake_spawn), \
                 mock.patch("urllib.request.urlopen", return_value=_OkResp()):
                h1.start()
                try:
                    self.assertEqual(len(opened_logs), 2)
                    self.assertTrue(all(f.closed for f in opened_logs))
                    with self.assertRaisesRegex(hp.HyMemoryError, "already"):
                        h2.start()
                finally:
                    h1.close()
                self.assertIsNone(h1._embedder_lock)
                h2.start()
                h2.close()

    def test_standalone_gliner_has_no_lru_cache_reference_cycle_and_close_clears_weights(self):
        runner = go.StandaloneGlinerOnnx(object(), _FakeTokenizer(), _fake_heads())
        self.assertEqual(runner._ids_for_token("nvidia"), (16,))
        ref = weakref.ref(runner)
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            del runner
            self.assertIsNone(ref())
        finally:
            if was_enabled:
                gc.enable()

        runner2 = go.StandaloneGlinerOnnx(object(), _FakeTokenizer(), _fake_heads())
        runner2._ids_for_token("apple")
        runner2.close()
        self.assertIsNone(runner2.session)
        self.assertEqual(runner2.heads, {})
        self.assertEqual(runner2._encode_piece.cache_info().currsize, 0)

    def test_router_idle_auto_unload_releases_process_and_global_slot_lock(self):
        with tempfile.TemporaryDirectory() as d1, \
             mock.patch("core.process_guard.memory_status", return_value={"avail_phys_mb": 6000, "commit_pct": 40}):
            closed_events = []
            proc = _FakeProcess(stdout_lines=['{"ready": true, "backend": "standalone_fp32"}\n'])
            router = rw.RouterProcess(REPO, Path(d1), idle_timeout_s=0.12, on_close=lambda: closed_events.append("closed"))
            with mock.patch.object(rw, "spawn_managed", return_value=proc):
                router.start()
                try:
                    self.assertIsNotNone(router.process)
                    self.assertIsNotNone(router._slot_lock)
                    deadline = time.monotonic() + 2.0
                    while not closed_events and time.monotonic() < deadline:
                        time.sleep(0.03)
                    self.assertIsNone(router.process)
                    self.assertIsNone(router._slot_lock)
                    self.assertEqual(proc.terminated, 1)
                    self.assertEqual(closed_events, ["closed"])
                finally:
                    router.close()

            proc2 = _FakeProcess(stdout_lines=['{"ready": true, "backend": "standalone_fp32"}\n'])
            router2 = rw.RouterProcess(REPO, Path(d1), idle_timeout_s=0.05)
            with mock.patch.object(rw, "spawn_managed", return_value=proc2):
                router2.start()
                with router2.lock:
                    router2._last_used = 0.0
                    router2._maybe_idle_close()
                    self.assertIsNotNone(router2.process)
                router2.close()

    def test_chat_service_unloads_router_before_loading_deep_thinker(self):
        from core.chat_service import ChatService
        from core.gliner_decide import RoutedQuery

        order = []

        class FakeExtractor:
            process = _FakeProcess()

            def close(self):
                order.append("unload_router")
                self.process = None

        class FakeModels:
            state = {"selected": "lfm3-1.7b-atomic-ad-q4km", "phase": "ready", "router": "loaded"}
            lock = mock.MagicMock()
            thread = None
            busy = False
            model_id = "fixture/lfm3"
            narrator_id = "lfm3-1.7b-atomic-ad-q4km"
            active_backend = "vulkan"

            def __init__(self):
                import threading
                self.lock = threading.RLock()

            def is_loaded(self, model=None):
                return True

            def qualified(self, backend=None, model=None):
                return True

            def think_qualified(self, backend=None, model=None):
                return True

            def thinker_loaded(self):
                return False

            def load(self):
                pass

            def load_thinker(self):
                order.append("load_thinker")

            def stop_thinker(self):
                pass

            def update(self, **kwargs):
                self.state.update(kwargs)

            def complete(self, *args, **kwargs):
                return "NVIDIA Corporation (NVDA) trades at 150.00 USD."

            def close(self):
                pass

        class FakeTools:
            def __init__(self):
                self.snapshots = {}
                self.charts = {}
                self.evidence = []

            def get_snapshot(self, ticker):
                snap = {"ticker": ticker, "name": "NVIDIA Corporation", "currency": "USD", "status": "available", "price": 150.0}
                self.snapshots[ticker] = snap
                return snap

            def plot_history(self, ticker):
                return ""

        with tempfile.TemporaryDirectory() as tmp:
            svc = ChatService(
                REPO,
                root=Path(tmp),
                models=FakeModels(),
                market_factory=lambda cancel: FakeTools(),
                router_factory=lambda: type("R", (), {"route": lambda self, q: RoutedQuery("thesis_review", 0.9, "long_term", ["NVDA"])})(),
                crowd_odds=lambda tickers: None,
            )
            svc.extractor = FakeExtractor()
            try:
                sess = svc.store.create_session("a" * 32)
                job = svc.submit(sess["id"], "Give me the NVDA thesis", "b" * 32, "lfm3-1.7b-atomic-ad-q4km", think=True)
                svc.threads[job["id"]].join(timeout=10)
                self.assertEqual(order, ["unload_router", "load_thinker"])
            finally:
                svc.close()

    def test_spawn_managed_contains_mixed_win32_and_python_children(self):
        if os.name != "nt":
            self.skipTest("Windows Job Object containment check")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p_win32 = pg.spawn_managed(
            ["cmd.exe", "/c", "ping -n 3 127.0.0.1 >nul"],
            error_type=RuntimeError,
            creationflags=flags,
        )
        p_py = None
        try:
            p_py = pg.spawn_managed(
                [sys.executable, "-E", "-B", "-c", "import time; time.sleep(2)"],
                error_type=RuntimeError,
                creationflags=flags,
            )
            self.assertIsNone(p_py.poll())
        finally:
            for proc in (p_py, p_win32):
                if proc is not None:
                    proc.terminate()
                    proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
