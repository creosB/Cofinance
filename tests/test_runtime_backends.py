"""GPU first, CPU fallback, and a qualification that is bound to the exact runtime build and server flags."""
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import local_models as L
from core.local_models import BACKENDS, LocalModels, ModelError
from qualify import history_runtime

REPO = Path(__file__).resolve().parents[1]


class RuntimeBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for b in BACKENDS.values():
            (self.root / b["archive"]).write_bytes(b"placeholder")
        self.models = LocalModels(self.root, REPO)

    def tearDown(self):
        self.tmp.cleanup()

    def test_gpu_is_preferred_when_installed(self):
        self.assertEqual(self.models.installed_backends(), ["vulkan", "cpu"])
        self.assertEqual(self.models.active_backend, "vulkan")
        self.assertIn("Vulkan GPU", self.models.snapshot()["runtime"])

    def test_any_flag_change_invalidates_qualification(self):
        before = self.models.fingerprint("cpu")
        self.assertNotEqual(before, self.models.fingerprint("vulkan"))
        changed = dict(BACKENDS["cpu"], flags=BACKENDS["cpu"]["flags"][:-2])
        with mock.patch.dict(L.BACKENDS, {"cpu": changed}):
            self.assertNotEqual(self.models.fingerprint("cpu"), before)
        with mock.patch.object(L, "COMMON_FLAGS", L.COMMON_FLAGS + ("--no-op",)):
            self.assertNotEqual(self.models.fingerprint("cpu"), before)

    def test_cpu_qualification_never_qualifies_the_gpu(self):
        self.models.update(qualifications={"cpu": {"passed": True, "fingerprint": self.models.fingerprint("cpu")}})
        self.assertTrue(self.models.qualified("cpu"))
        self.assertFalse(self.models.qualified("vulkan"))
        self.assertTrue(self.models.qualified())

    def test_failed_gpu_start_falls_back_to_qualified_cpu(self):
        self.models.update(qualifications={b: {"passed": True, "fingerprint": self.models.fingerprint(b)} for b in BACKENDS})
        tried = []

        def start(backend, for_qualification, failures):
            tried.append(backend)
            if backend == "vulkan":
                raise ModelError("Local narrator exited.")
            self.models.update(backend=backend, runtime_note="; ".join(failures))

        with mock.patch.object(self.models, "start", side_effect=start):
            self.models.load()
        self.assertEqual(tried, ["vulkan", "cpu"])
        self.assertEqual(self.models.state["backend"], "cpu")
        self.assertIn("Vulkan GPU", self.models.state["runtime_note"])

    def test_unqualified_runtime_is_never_started(self):
        self.models.update(qualifications={"cpu": {"passed": True, "fingerprint": self.models.fingerprint("cpu")}})
        with mock.patch.object(self.models, "start") as start:
            self.models.load()
        self.assertEqual([c.args[0] for c in start.call_args_list], ["cpu"])

    def test_gpu_flags_pin_the_device_and_full_offload(self):
        flags = BACKENDS["vulkan"]["flags"]
        self.assertEqual(flags[flags.index("--device") + 1], "Vulkan0")
        self.assertEqual(flags[flags.index("--n-gpu-layers") + 1], "99")
        self.assertEqual(BACKENDS["cpu"]["flags"][BACKENDS["cpu"]["flags"].index("--n-gpu-layers") + 1], "0")

    def test_live_and_benchmark_share_one_command_line(self):
        live, bench = inspect.getsource(LocalModels.start), inspect.getsource(history_runtime.narrator)
        for source in (live, bench):
            self.assertIn("server_command(", source)
            self.assertNotIn("--threads", source)
        cmd = L.server_command("llama-server.exe", "m.gguf", "a", 1, "k", "vulkan")
        self.assertEqual(cmd[-2:], ["--api-key", "k"])
        for flag in (*L.COMMON_FLAGS, *BACKENDS["vulkan"]["flags"]):
            self.assertIn(flag, cmd)

    def test_benchmark_backend_override_is_checked(self):
        with mock.patch.dict("os.environ", {"COFINANCE_BACKEND": "cpu"}):
            self.assertEqual(history_runtime.default_backend(self.root), "cpu")
        with mock.patch.dict("os.environ", {"COFINANCE_BACKEND": "cuda"}):
            with self.assertRaises(ValueError):
                history_runtime.default_backend(self.root)


if __name__ == "__main__":
    unittest.main()
