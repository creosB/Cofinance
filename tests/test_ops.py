import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dev
from core import ops


def _make_wal_db(path: Path, value: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS items (k TEXT PRIMARY KEY, v TEXT)")
        conn.execute("INSERT OR REPLACE INTO items (k, v) VALUES ('state', ?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _read_db_val(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT v FROM items WHERE k='state'").fetchone()[0]
    finally:
        conn.close()


class TestOpsPreflightAndRecovery(unittest.TestCase):
    def test_preflight_passes_on_fresh_empty_data_dir_and_catches_corruption(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            rep = ops.run_preflight(repo, data_dir, check_port=False)
            self.assertTrue(rep["ok"], rep)
            by_id = {c["id"]: c for c in rep["checks"]}
            self.assertEqual(by_id["frozen_qualify_hashes"]["status"], "pass")
            self.assertEqual(by_id["tzdata_america_new_york"]["status"], "pass")
            self.assertEqual(by_id["sqlite_integrity"]["status"], "warn")

            (data_dir / "chat_v2.db").write_bytes(b"not a valid sqlite database at all")
            bad = ops.run_preflight(repo, data_dir, check_port=False)
            self.assertFalse(bad["ok"])
            by_id_bad = {c["id"]: c for c in bad["checks"]}
            self.assertEqual(by_id_bad["sqlite_integrity"]["status"], "fail")

            (data_dir / "chat_v2.db").unlink()
            lm_dir = data_dir / "local_models"
            lm_dir.mkdir(parents=True, exist_ok=True)
            onnx_file, meta_file = ops.onnx_paths(lm_dir)
            onnx_file.write_bytes(b"corrupt")
            meta_file.write_text('{"format":"encoder_fp32","sha256":"bad"}', encoding="utf-8")
            bad_onnx = ops.run_preflight(repo, data_dir, check_port=False)
            self.assertFalse(bad_onnx["ok"])
            self.assertEqual({c["id"]: c for c in bad_onnx["checks"]}["router_onnx_cache"]["status"], "fail")

            fake_repo = Path(tmp) / "fake_repo"
            (fake_repo / "qualify").mkdir(parents=True)
            (fake_repo / "docs").mkdir(parents=True)
            (fake_repo / "docs" / "feature-map.json").write_text('{"features":[{"id":"x"}]}', encoding="utf-8")
            (fake_repo / "qualify" / "scorers.py").write_text("tampered", encoding="utf-8")
            bad_frozen = ops.run_preflight(fake_repo, data_dir / "empty_data", check_port=False)
            self.assertFalse(bad_frozen["ok"])
            self.assertEqual({c["id"]: c for c in bad_frozen["checks"]}["frozen_qualify_hashes"]["status"], "fail")

    def test_online_wal_backup_verify_dry_run_and_force_restore_with_safety_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_data = root / "src_data"
            src_data.mkdir()
            _make_wal_db(src_data / "chat_v2.db", "original-v1")
            _make_wal_db(src_data / "news.db", "news-v1")
            (src_data / "local_models").mkdir()
            (src_data / "local_models" / "state.json").write_text('{"selected":"atomicchat"}', encoding="utf-8")
            (src_data / "news_corpus_20261108").mkdir()
            (src_data / "news_corpus_20261108" / "label.csv").write_text("id,material\n1,1\n", encoding="utf-8")

            snap = root / "snap1"
            manifest = ops.backup_state(src_data, snap)
            self.assertEqual(manifest["format"], ops.BACKUP_FORMAT)
            rels = {f["rel"] for f in manifest["files"]}
            self.assertEqual(rels, {"chat_v2.db", "news.db", "local_models/state.json", "news_corpus_20261108/label.csv"})
            self.assertEqual(ops.verify_backup(snap)["format"], ops.BACKUP_FORMAT)

            with self.assertRaises(FileExistsError):
                ops.backup_state(src_data, snap)

            fresh_target = root / "fresh_target"
            dry = ops.restore_state(snap, fresh_target, dry_run=True)
            self.assertTrue(dry["dry_run"])
            self.assertFalse((fresh_target / "chat_v2.db").exists())

            done = ops.restore_state(snap, fresh_target)
            self.assertTrue(done["verified"])
            self.assertIsNone(done["safety_backup"])
            self.assertEqual(_read_db_val(fresh_target / "chat_v2.db"), "original-v1")

            _make_wal_db(fresh_target / "chat_v2.db", "mutated-v2")
            stale_wal = fresh_target / "chat_v2.db-wal"
            stale_wal.write_bytes(b"stale wal bytes")
            with self.assertRaises(FileExistsError):
                ops.restore_state(snap, fresh_target, force=False)

            safety_dir = root / "safety_snap"
            replaced = ops.restore_state(snap, fresh_target, force=True, safety_backup_dir=safety_dir)
            self.assertEqual(replaced["safety_backup"], str(safety_dir))
            self.assertEqual(_read_db_val(fresh_target / "chat_v2.db"), "original-v1")
            self.assertFalse(stale_wal.exists())
            self.assertEqual(_read_db_val(safety_dir / "chat_v2.db"), "mutated-v2")

    def test_verify_backup_rejects_tampering_path_traversal_and_corrupt_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_data = root / "src"
            src_data.mkdir()
            _make_wal_db(src_data / "chat_v2.db", "clean")
            (src_data / "local_models").mkdir()
            (src_data / "local_models" / "state.json").write_text('{"selected":"facts"}', encoding="utf-8")

            tampered = root / "tampered"
            ops.backup_state(src_data, tampered)
            (tampered / "local_models" / "state.json").write_text('{"selected":"other"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                ops.verify_backup(tampered)

            outside = root / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            traversal = root / "traversal"
            ops.backup_state(src_data, traversal)
            mf = json.loads((traversal / "manifest.json").read_text(encoding="utf-8"))
            mf["files"].append({
                "rel": "../outside.txt",
                "kind": "file",
                "bytes": outside.stat().st_size,
                "sha256": ops.sha256_file(outside),
            })
            (traversal / "manifest.json").write_text(json.dumps(mf), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsafe backup path"):
                ops.verify_backup(traversal)

            corrupt_sql = root / "corrupt_sql"
            ops.backup_state(src_data, corrupt_sql)
            bad_bytes = b"not sqlite"
            (corrupt_sql / "chat_v2.db").write_bytes(bad_bytes)
            mf2 = json.loads((corrupt_sql / "manifest.json").read_text(encoding="utf-8"))
            mf2["files"][0]["bytes"] = len(bad_bytes)
            mf2["files"][0]["sha256"] = ops.sha256_file(corrupt_sql / "chat_v2.db")
            (corrupt_sql / "manifest.json").write_text(json.dumps(mf2), encoding="utf-8")
            with self.assertRaises(ValueError):
                ops.verify_backup(corrupt_sql)

    def test_dev_restore_refuses_live_data_while_dashboard_is_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "snap"
            snap.mkdir()
            with mock.patch.object(dev, "dashboard_state", return_value={"active_job": None}):
                with self.assertRaises(SystemExit):
                    dev.restore_cmd(str(snap), data_dir=None, dry_run=False, force=True)


if __name__ == "__main__":
    unittest.main()
