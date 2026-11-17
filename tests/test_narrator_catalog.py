"""Two pinned narrators: each is verified and qualified on its own, the choice drives every call, and headline
judgments from different weights are never summed together."""
import hashlib
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from core import headline_signal as H
from core import local_models as L
from core.local_models import ATOMIC_ID, BACKENDS, MODEL_ID, MODEL_SHA, LocalModels, ModelError, server_flags
from core.news_store import NewsStore
from core.signals import Action, Item, Judgment, Outcome
from tests.test_headline_signal import NOW, FakeRuntime

REPO = Path(__file__).resolve().parents[1]
SMALL = dict(L.EXTRA_NARRATORS[ATOMIC_ID], size=32)


def passing(models, model, backend):
    return {"passed": True, "model": model, "fingerprint": models.fingerprint(backend, model)}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for b in BACKENDS.values():
            (self.root / b["archive"]).write_bytes(b"placeholder")
        self.models = LocalModels(self.root, REPO)

    def tearDown(self):
        self.tmp.cleanup()

    def test_base_fingerprint_is_unchanged_so_existing_qualifications_stay_valid(self):
        for backend in BACKENDS:
            h = hashlib.sha256("|".join([MODEL_SHA, BACKENDS[backend]["sha"], " ".join(server_flags(backend)),
                                          "greedy-thinking-off-v1"]).encode())
            for name in ("qualify/cases.jsonl", "qualify/gates_narrator.json", "qualify/scorers.py"):
                h.update((REPO / name).read_bytes())
            self.assertEqual(self.models.fingerprint(backend, MODEL_ID), h.hexdigest())
            self.assertEqual(self.models.fingerprint(backend), h.hexdigest())

    def test_one_narrators_report_never_qualifies_another(self):
        legacy = {"passed": True, "fingerprint": self.models.fingerprint("vulkan", MODEL_ID)}  # old reports name no model
        self.models.update(qualifications={"vulkan": legacy})
        self.assertTrue(self.models.qualified(model=MODEL_ID))
        self.assertFalse(self.models.qualified(model=ATOMIC_ID))
        self.assertNotEqual(self.models.fingerprint("vulkan", MODEL_ID), self.models.fingerprint("vulkan", ATOMIC_ID))
        with self.assertRaises(ModelError):
            self.models.select(ATOMIC_ID)

    @mock.patch.dict(L.EXTRA_NARRATORS, {ATOMIC_ID: SMALL})
    def test_choice_drives_file_alias_trace_id_and_survives_restart(self):
        (self.root / SMALL["filename"]).write_bytes(b"x" * 32)
        self.models.update(qualifications={f"{ATOMIC_ID}:vulkan": passing(self.models, ATOMIC_ID, "vulkan")})
        self.models.select(ATOMIC_ID)
        self.assertEqual(self.models.narrator_id, ATOMIC_ID)
        self.assertEqual(self.models.model_path.name, SMALL["filename"])
        self.assertEqual(self.models.model_id, "AtomicChat/LFM3-1.7B-Uncensored-GGUF:AD-Q4_K_M@3ad712542eb9")
        snap = self.models.snapshot()
        listed = {n["id"]: n for n in snap["narrators"]}
        self.assertEqual(set(listed), {MODEL_ID, ATOMIC_ID})
        self.assertTrue(listed[ATOMIC_ID]["qualified"] and listed[ATOMIC_ID]["active"] and listed[ATOMIC_ID]["downloaded"])
        self.assertFalse(listed[MODEL_ID]["qualified"] or listed[MODEL_ID]["active"])
        self.assertEqual((snap["id"], snap["quantization"], snap["can_select"]), (ATOMIC_ID, "AD-Q4_K_M", True))
        again = LocalModels(self.root, REPO)
        self.assertEqual(again.state["selected"], ATOMIC_ID)
        self.assertIsNone(again.process)

    def test_switching_narrator_stops_the_loaded_one_and_loads_the_choice(self):
        self.models.update(qualifications={f"{m}:vulkan": passing(self.models, m, "vulkan") for m in (MODEL_ID, ATOMIC_ID)})
        proc = mock.Mock()
        proc.poll.return_value = None
        self.models.process, self.models.ready, self.models.loaded_model = proc, True, MODEL_ID
        self.models.select(ATOMIC_ID)
        proc.terminate.assert_called_once()
        self.assertIsNone(self.models.loaded_model)
        self.assertFalse(self.models.snapshot()["loaded"])
        with mock.patch.object(self.models, "start") as start:
            self.models.load()
        self.assertEqual(start.call_args.kwargs["model"], ATOMIC_ID)

    def test_qualifying_one_narrator_keeps_the_others_reports(self):
        legacy = {b: {"passed": True, "fingerprint": self.models.fingerprint(b, MODEL_ID)} for b in BACKENDS}
        self.models.update(qualifications=legacy)
        fake = {"n": 7, "summary": {"narration": {"pass_rate": 1.0}, "number_faithfulness": {"pass_rate": 1.0}}}
        loaded = []

        def load(for_qualification=False, backend=None, model=None):
            loaded.append((backend, model))
            self.models.loaded_model = model
            self.models.update(backend=backend)

        with mock.patch.object(self.models, "load", side_effect=load), \
                mock.patch("qualify.run.run_cases", side_effect=lambda *a, **k: json.loads(json.dumps(fake))):
            self.models.qualify(ATOMIC_ID)
            self.assertEqual({m for _, m in loaded}, {ATOMIC_ID})
            self.assertTrue(all(self.models.qualified(b, MODEL_ID) for b in BACKENDS), "base reports were dropped")
            self.assertTrue(all(self.models.qualified(b, ATOMIC_ID) for b in BACKENDS))
            self.models.qualify(MODEL_ID)
        keys = set(self.models.state["qualifications"])
        self.assertEqual(keys, {f"{m}:{b}" for m in (MODEL_ID, ATOMIC_ID) for b in BACKENDS})
        self.assertTrue(all(self.models.qualified(b, m) for m in (MODEL_ID, ATOMIC_ID) for b in BACKENDS))
        self.assertEqual(set(json.loads((self.root / "qualification.json").read_text())), keys)

    def test_model_actions_validate_the_narrator(self):
        with self.assertRaises(ValueError):
            self.models.launch_task("load", ATOMIC_ID)
        with self.assertRaises(ValueError):
            self.models.launch_task("download", "hosted-gpt")
        with self.assertRaises(ValueError):
            self.models.select("hosted-gpt")


class JudgeSeparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "news.db"
        self.store = NewsStore(str(self.db), clock=lambda: NOW)
        for title in ("A", "B"):
            item = Item("NVDA", "official_feed", "https://example.com/" + title, title, "body", NOW - 60)
            with self.store.transaction():
                self.store.record([Outcome(item, Action.DIGEST, "test", Judgment(0.9, 0.1, "bullish", 0.8))], NOW)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_each_judge_keeps_its_own_judgments(self):
        atomic = H.version_for(ATOMIC_ID)
        self.assertEqual(H.version_for(MODEL_ID), H.VERSION)
        H.judge_pending(self.store.conn, FakeRuntime({"A": (.8, .1, .1), "B": (.8, .1, .1)}), model="base", clock=lambda: NOW)
        self.assertIsNone(H.card(self.db, {"NVDA": ["A", "B"]}, version=atomic), "base judgments leaked into the new judge")
        res = H.judge_pending(self.store.conn, FakeRuntime({"A": (.1, .8, .1), "B": (.1, .1, .8)}), model="atomic",
                              clock=lambda: NOW, version=atomic)
        self.assertEqual((res["pending"], res["judged"], res["version"]), (2, 2, atomic))
        base, new = H.card(self.db, {"NVDA": ["A", "B"]}), H.card(self.db, {"NVDA": ["A", "B"]}, version=atomic)
        self.assertGreater(base["assets"][0]["score"], 0.5)
        self.assertLess(new["assets"][0]["score"], 0)
        self.assertEqual((new["judge"], new["dev_auc"]), ("LFM3 1.7B Uncensored (AtomicChat)", 0.625))
        self.assertIn("not been confirmed", new["note"])
        self.assertEqual((base["judge"], base["dev_auc"]), ("LFM3 1.7B", H.DEV_AUC))
        with self.assertRaises(ValueError):
            H.version_for("hosted-gpt")

    def test_collection_judges_with_the_selected_narrator(self):
        from core.chat_service import ChatService
        runtime = FakeRuntime({"A": (.6, .2, .2), "B": (.2, .6, .2)})
        proc = mock.Mock()
        proc.poll.return_value = None
        models = mock.Mock(narrator_id=ATOMIC_ID, model_id="atomic", active_backend="vulkan", ready=True, process=proc, busy=False)
        models.qualified.return_value = True
        models.choice.side_effect = runtime
        service = ChatService.__new__(ChatService)
        service.models, service.root, service.news_progress = models, Path(self.tmp.name), lambda stage: None
        result = service.judge_headlines(self.store, lambda: None)
        self.assertEqual((result["status"], result["version"], result["judged"]), ("complete", H.version_for(ATOMIC_ID), 2))
        versions = {r[0] for r in self.store.conn.execute("SELECT version FROM headline_judgments")}
        self.assertEqual(versions, {H.version_for(ATOMIC_ID)})


class ModelActionAPITests(unittest.TestCase):
    def setUp(self):
        import http.client
        from tests.test_chat import service
        from ui.dashboard import create_server
        self.http = http.client
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = service(root)
        dist = root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text('<div id="root"></div>')
        self.server = create_server(port=0, dist=dist, trace_dir=root / "traces", corpus_dir=root / "missing", service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        self.token = self.request("/api/state")[1]["csrf"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.service.close()
        self.temp.cleanup()

    def request(self, path, body=None):
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {} if body is None else {"Origin": self.origin, "Content-Type": "application/json", "X-Cofinance-CSRF": getattr(self, "token", "")}
        conn.request("GET" if body is None else "POST", path, body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        value = json.loads(response.read())
        conn.close()
        return response.status, value

    def test_model_action_passes_the_narrator_through_and_rejects_bad_fields(self):
        with mock.patch.object(self.service.models, "launch_task", return_value={"ok": True}) as launch:
            self.assertEqual(self.request("/api/models/action", {"action": "qualify", "model": ATOMIC_ID})[0], 202)
            self.assertEqual(launch.call_args.args, ("qualify", ATOMIC_ID))
            self.assertEqual(self.request("/api/models/action", {"action": "qualify"})[0], 202)
            self.assertEqual(launch.call_args.args, ("qualify", None))
            self.assertEqual(self.request("/api/models/action", {"action": "qualify", "extra": 1})[0], 400)
            self.assertEqual(self.request("/api/models/action", {"model": ATOMIC_ID})[0], 400)
        self.assertEqual(self.request("/api/models/action", {"action": "load", "model": ATOMIC_ID})[0], 400)
        self.assertEqual(self.request("/api/models/select", {"model": ATOMIC_ID})[0], 409, "unqualified narrator selected")
        state = self.request("/api/state")[1]
        models = state.get("models") or state["workspace"]["models"]
        self.assertEqual({n["id"] for n in models["narrators"]}, {MODEL_ID, ATOMIC_ID})

    def test_chat_accepts_only_catalog_narrators(self):
        status, session = self.request("/api/sessions", {"request_id": uuid.uuid4().hex})
        body = {"session_id": session["id"], "query": "NVDA", "request_id": uuid.uuid4().hex, "model": "hosted-gpt"}
        self.assertEqual(self.request("/api/chat", body)[0], 400)


if __name__ == "__main__":
    unittest.main()
