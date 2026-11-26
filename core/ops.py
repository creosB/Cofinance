"""Reproducible preflight, online SQLite backup, and verified state recovery (stdlib only)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .router_worker import REVISION as GLINER_REVISION, onnx_paths, verify_onnx_cache

BACKUP_FORMAT = "cofinance-backup/1"
SQLITE_FILES = ("chat_v2.db", "news.db", "memory_v2.db")
ML_PACKAGES = ("torch", "transformers", "gliner2", "onnxruntime", "pypdf")
FROZEN_QUALIFY_FILES = {
    "qualify/scorers.py": "cf60f820e4ed4128f6791576601504b222c0d56bf0867b1f9abd916317e07034",
    "qualify/cases.jsonl": "0eb5fcb53186701d4b8c6dcf0646acbfa8e46b3bf883eb6977b2357f12b007d8",
    "qualify/gates_narrator.json": "9e062ec2f5999b25fbbb5414dca5268780187a5df46fcf219ca6b1b831b37eeb",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sqlite_integrity_ok(path: Path) -> Tuple[bool, str]:
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        msgs = [str(r[0]) for r in rows]
        ok = msgs == ["ok"]
        return ok, "; ".join(msgs)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if conn is not None:
            conn.close()


def _safe_relpath(rel: str) -> PurePosixPath:
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        raise ValueError(f"Unsafe backup path: {rel!r}")
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or any(":" in part for part in p.parts):
        raise ValueError(f"Unsafe backup path: {rel!r}")
    return p


def _gliner_snapshot_dir(home_dir: Optional[Path] = None) -> Path:
    base = Path(home_dir) if home_dir is not None else Path.home()
    return (
        base
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--fastino--GLiNER2.5-Decide"
        / "snapshots"
        / GLINER_REVISION
    )


def _port_listening(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.25)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def run_preflight(
    repo_root: Path,
    data_dir: Optional[Path] = None,
    *,
    check_port: bool = True,
    dashboard_port: int = 8878,
) -> Dict[str, Any]:
    repo = Path(repo_root)
    data = Path(data_dir) if data_dir is not None else repo / "data"
    checks: List[Dict[str, Any]] = []

    def add(check_id: str, status: str, detail: str) -> None:
        checks.append({"id": check_id, "status": status, "detail": detail})

    ver = sys.version_info
    add(
        "python_version",
        "pass" if ver >= (3, 11) else "fail",
        f"Python {ver.major}.{ver.minor}.{ver.micro} (requires >=3.11)",
    )

    try:
        ny = ZoneInfo("America/New_York")
        winter_off = datetime(2026, 1, 15, 12, 0, tzinfo=ny).utcoffset()
        summer_off = datetime(2026, 7, 15, 12, 0, tzinfo=ny).utcoffset()
        if winter_off != summer_off and winter_off is not None and summer_off is not None:
            add("tzdata_america_new_york", "pass", "America/New_York DST offsets verified")
        else:
            add("tzdata_america_new_york", "fail", "America/New_York DST offsets invalid")
    except Exception as exc:
        add("tzdata_america_new_york", "fail", f"ZoneInfo America/New_York unavailable: {exc}")

    bad_frozen: List[str] = []
    for rel, expected in FROZEN_QUALIFY_FILES.items():
        fp = repo / rel
        if not fp.exists() or sha256_file(fp) != expected:
            bad_frozen.append(rel)
    add(
        "frozen_qualify_hashes",
        "pass" if not bad_frozen else "fail",
        "All 3 frozen qualification files match pinned SHA-256"
        if not bad_frozen
        else f"Missing or altered frozen files: {', '.join(bad_frozen)}",
    )

    fmap = repo / "docs" / "feature-map.json"
    try:
        parsed_map = json.loads(fmap.read_text(encoding="utf-8"))
        count = len(parsed_map.get("features") or [])
        add("feature_map", "pass" if count > 0 else "fail", f"{count} mapped features")
    except Exception as exc:
        add("feature_map", "fail", f"Invalid docs/feature-map.json: {exc}")

    present_dbs = [data / name for name in SQLITE_FILES if (data / name).exists()]
    if not present_dbs:
        add("sqlite_integrity", "warn", "No SQLite state databases yet (fresh install)")
    else:
        bad_dbs: List[str] = []
        for db_path in present_dbs:
            ok, msg = sqlite_integrity_ok(db_path)
            if not ok:
                bad_dbs.append(f"{db_path.name} ({msg})")
        add(
            "sqlite_integrity",
            "pass" if not bad_dbs else "fail",
            f"PRAGMA integrity_check ok on {len(present_dbs)} database(s)"
            if not bad_dbs
            else f"Corrupt SQLite DB(s): {'; '.join(bad_dbs)}",
        )

    gliner_dir = _gliner_snapshot_dir()
    add(
        "gliner_cache",
        "pass" if gliner_dir.exists() else "warn",
        f"Pinned GLiNER snapshot {GLINER_REVISION[:8]} present"
        if gliner_dir.exists()
        else f"Pinned GLiNER snapshot {GLINER_REVISION[:8]} missing (fresh install must stage cache)",
    )

    onnx_file, onnx_meta = onnx_paths(data / "local_models")
    if onnx_file.exists() or onnx_meta.exists():
        verified = verify_onnx_cache(data / "local_models")
        add(
            "router_onnx_cache",
            "pass" if verified is not None else "fail",
            f"Verified encoder_fp32 ONNX cache ({verified.name})"
            if verified is not None
            else "ONNX FP32 router cache present but failed SHA-256, revision, or parity gate",
        )
    else:
        add("router_onnx_cache", "warn", "No ONNX FP32 router cache (falls back to PyTorch)")

    found_pkgs = [p for p in ML_PACKAGES if importlib.util.find_spec(p) is not None]
    missing_pkgs = [p for p in ML_PACKAGES if p not in found_pkgs]
    add(
        "ml_packages",
        "pass" if not missing_pkgs else "warn",
        f"Installed: {', '.join(found_pkgs)}" + (f"; missing: {', '.join(missing_pkgs)}" if missing_pkgs else ""),
    )

    dist_index = repo / "frontend" / "dist" / "index.html"
    add(
        "frontend_dist",
        "pass" if dist_index.exists() else "warn",
        "Built frontend/dist/index.html present"
        if dist_index.exists()
        else "frontend/dist/index.html missing (run npm ci --ignore-scripts && npm run build)",
    )

    node_bin = shutil.which("node")
    npm_bin = shutil.which("npm")
    add(
        "node_runtime",
        "pass" if (node_bin and npm_bin) else "warn",
        "node and npm found on PATH" if (node_bin and npm_bin) else "node/npm not on PATH (needed only to rebuild UI)",
    )

    state_file = data / "local_models" / "state.json"
    if not state_file.exists():
        add("local_narrator_state", "warn", "No local narrator state.json yet (fresh install)")
    else:
        try:
            st = json.loads(state_file.read_text(encoding="utf-8"))
            selected = st.get("selected", "facts")
            phase = st.get("phase", "unknown")
            add("local_narrator_state", "pass", f"selected={selected}, phase={phase}")
        except Exception as exc:
            add("local_narrator_state", "fail", f"Corrupt local_models/state.json: {exc}")

    if check_port:
        busy = _port_listening(dashboard_port)
        add(
            "dashboard_port",
            "pass",
            f"127.0.0.1:{dashboard_port} is listening" if busy else f"127.0.0.1:{dashboard_port} is free",
        )

    fail_count = sum(1 for c in checks if c["status"] == "fail")
    warn_count = sum(1 for c in checks if c["status"] == "warn")
    pass_count = sum(1 for c in checks if c["status"] == "pass")
    return {
        "ok": fail_count == 0,
        "fail_count": fail_count,
        "warn_count": warn_count,
        "pass_count": pass_count,
        "checks": checks,
    }


def _collect_backup_targets(data_dir: Path) -> List[Tuple[str, str, Path]]:
    targets: List[Tuple[str, str, Path]] = []
    for name in SQLITE_FILES:
        p = data_dir / name
        if p.exists():
            targets.append((name, "sqlite", p))
    state_file = data_dir / "local_models" / "state.json"
    if state_file.exists():
        targets.append(("local_models/state.json", "file", state_file))
    for label_file in sorted(data_dir.glob("news_corpus_*/label.csv")):
        rel = label_file.relative_to(data_dir).as_posix()
        targets.append((rel, "file", label_file))
    return targets


def _online_sqlite_backup(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(f"file:{src.resolve().as_posix()}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()


def backup_state(data_dir: Path, dest_dir: Path) -> Dict[str, Any]:
    data = Path(data_dir)
    dest = Path(dest_dir)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"Backup destination is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    for rel, kind, src in _collect_backup_targets(data):
        _safe_relpath(rel)
        out_path = dest / Path(*PurePosixPath(rel).parts)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "sqlite":
            _online_sqlite_backup(src, out_path)
            ok, msg = sqlite_integrity_ok(out_path)
            if not ok:
                raise RuntimeError(f"Backup SQLite integrity failed for {rel}: {msg}")
        else:
            shutil.copy2(src, out_path)
        entries.append(
            {
                "rel": rel,
                "kind": kind,
                "bytes": out_path.stat().st_size,
                "sha256": sha256_file(out_path),
            }
        )

    manifest = {
        "format": BACKUP_FORMAT,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "files": entries,
    }
    manifest_path = dest / "manifest.json"
    tmp_manifest = dest / "manifest.json.tmp"
    tmp_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_manifest, manifest_path)
    return manifest


def verify_backup(backup_dir: Path) -> Dict[str, Any]:
    root = Path(backup_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"Backup manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != BACKUP_FORMAT:
        raise ValueError(f"Unsupported backup format: {manifest.get('format')!r}")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("Backup manifest files field must be a list")

    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Malformed backup manifest entry")
        rel = item.get("rel")
        kind = item.get("kind")
        if kind not in ("sqlite", "file"):
            raise ValueError(f"Unsupported backup entry kind: {kind!r}")
        safe = _safe_relpath(str(rel))
        if rel in seen:
            raise ValueError(f"Duplicate backup entry: {rel}")
        seen.add(rel)
        fp = root / Path(*safe.parts)
        if not fp.exists() or not fp.is_file():
            raise ValueError(f"Missing backed-up file: {rel}")
        if fp.stat().st_size != item.get("bytes"):
            raise ValueError(f"Size mismatch for {rel}")
        if sha256_file(fp) != item.get("sha256"):
            raise ValueError(f"SHA-256 mismatch for {rel}")
        if kind == "sqlite":
            ok, msg = sqlite_integrity_ok(fp)
            if not ok:
                raise ValueError(f"SQLite integrity check failed for {rel}: {msg}")
    return manifest


def restore_state(
    backup_dir: Path,
    data_dir: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    safety_backup_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    manifest = verify_backup(backup_dir)
    src_root = Path(backup_dir)
    dst_root = Path(data_dir)
    entries = manifest["files"]

    existing = [
        item["rel"]
        for item in entries
        if (dst_root / Path(*PurePosixPath(item["rel"]).parts)).exists()
    ]
    if existing and not force and not dry_run:
        raise FileExistsError(
            f"Target already contains {', '.join(existing)}. Pass --force to overwrite after a pre-restore safety backup."
        )

    if dry_run:
        return {
            "dry_run": True,
            "verified": True,
            "restored": [item["rel"] for item in entries],
            "would_overwrite": existing,
            "safety_backup": None,
        }

    safety_path: Optional[Path] = None
    if existing and force:
        if safety_backup_dir is not None:
            safety_path = Path(safety_backup_dir)
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safety_path = dst_root / "_backups" / f"pre_restore_{stamp}"
        backup_state(dst_root, safety_path)

    dst_root.mkdir(parents=True, exist_ok=True)
    for item in entries:
        rel = item["rel"]
        parts = PurePosixPath(rel).parts
        src = src_root / Path(*parts)
        dst = dst_root / Path(*parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".restore.tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        if item["kind"] == "sqlite":
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(dst) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
        if sha256_file(dst) != item["sha256"]:
            raise RuntimeError(f"Post-restore SHA-256 verification failed for {rel}")
        if item["kind"] == "sqlite":
            ok, msg = sqlite_integrity_ok(dst)
            if not ok:
                raise RuntimeError(f"Post-restore SQLite integrity failed for {rel}: {msg}")

    return {
        "dry_run": False,
        "verified": True,
        "restored": [item["rel"] for item in entries],
        "would_overwrite": existing,
        "safety_backup": str(safety_path) if safety_path else None,
    }
