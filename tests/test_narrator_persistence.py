"""The narrator choice survives a restart, but a restart never loads a model or bypasses qualification."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import local_models
from core.local_models import MODEL_ID, LocalModels

REPO = Path(__file__).resolve().parents[1]
SIZE = 64


@patch.object(local_models, "MODEL_SIZE", SIZE)
class TestNarratorPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def qualified_manager(self):
        (self.root / local_models.FILENAME).write_bytes(b"x" * SIZE)
        models = LocalModels(self.root, REPO)
        models.update(qualification={"passed": True, "fingerprint": models.fingerprint()})
        return models

    def restart(self):
        return LocalModels(self.root, REPO)

    def test_selected_narrator_is_restored_after_shutdown(self):
        models = self.qualified_manager()
        models.select(MODEL_ID)
        models.close()  # dashboard shutdown keeps the remembered choice
        again = self.restart()
        self.assertEqual(again.state["selected"], MODEL_ID)
        self.assertIn("Restored", again.state["selection_note"])

    def test_restore_never_starts_inference(self):
        models = self.qualified_manager()
        models.select(MODEL_ID)
        again = self.restart()
        self.assertIsNone(again.process)
        self.assertFalse(again.ready)
        self.assertFalse(again.snapshot()["loaded"])

    def test_user_unload_switches_back_to_tool_only_for_next_start(self):
        models = self.qualified_manager()
        models.select(MODEL_ID)
        models.unload()
        self.assertEqual(self.restart().state["selected"], "facts")

    def test_choosing_tool_only_is_remembered_too(self):
        models = self.qualified_manager()
        models.select(MODEL_ID)
        models.select("facts")
        again = self.restart()
        self.assertEqual(again.state["selected"], "facts")
        self.assertEqual(again.state["selection_note"], "")

    def test_stale_qualification_is_not_restored_and_says_why(self):
        models = self.qualified_manager()
        models.select(MODEL_ID)
        models.update(qualification={"passed": True, "fingerprint": "changed-scorer"})
        again = self.restart()
        self.assertEqual(again.state["selected"], "facts")
        self.assertIn("out of date", again.state["selection_note"])

    def test_missing_or_wrong_size_model_is_not_restored(self):
        models = self.qualified_manager()
        models.select(MODEL_ID)
        (self.root / local_models.FILENAME).write_bytes(b"x" * (SIZE - 1))
        again = self.restart()
        self.assertEqual(again.state["selected"], "facts")
        self.assertIn("missing", again.state["selection_note"])

    def test_legacy_or_tampered_state_falls_back_to_tool_only(self):
        self.qualified_manager()
        path = self.root / "state.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved.pop("preferred", None)
        saved["selected"] = MODEL_ID  # old files only had the live selection
        path.write_text(json.dumps(saved), encoding="utf-8")
        self.assertEqual(self.restart().state["selected"], "facts")
        saved["preferred"] = "hosted-gpt"
        path.write_text(json.dumps(saved), encoding="utf-8")
        again = self.restart()
        self.assertEqual(again.state["selected"], "facts")
        self.assertEqual(again.state["preferred"], "facts")


if __name__ == "__main__":
    unittest.main()
