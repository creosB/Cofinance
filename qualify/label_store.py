"""Human label store for the blind headline corpus (data/news_corpus_*/label.csv).

The dashboard Labels view reads and writes the same label.csv that `news_dataset finalize` validates, so there
is one source of truth. Rules:
  * evidence columns (id..timestamp) are never edited; only the six label columns change;
  * each save is validated with the finalize rules (true/false, direction, trap, event group, urgent => material);
  * the corpus hash must match its manifest, otherwise the store is read-only;
  * writes are atomic (temp file + replace) and the first UI write keeps a one-time backup;
  * the view is blind: no model scores or gate outcomes are ever returned from here.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

from .news_dataset import DIRECTIONS, FIELDS, FORMAT, TRAPS, digest, labelled_rows, readiness, split_rows

EVIDENCE = FIELDS[:7]
LABEL_FIELDS = ("material", "urgent", "direction", "trap", "event_group", "notes")
TARGET, TRAP_TARGET = 120, 20


def clean_label(values: dict) -> dict:
    """Validate one row's labels with the finalize rules and return the CSV strings to store."""
    def flag(name):
        value = values.get(name)
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in {"true", "false"}:
            return str(value).strip().lower() == "true"
        raise ValueError(name + " must be true or false")
    material, urgent = flag("material"), flag("urgent")
    if urgent and not material:
        raise ValueError("urgent cannot be true when material is false")
    direction = str(values.get("direction") or "").strip().lower()
    trap = str(values.get("trap") or "").strip().lower()
    if direction not in DIRECTIONS:
        raise ValueError("direction must be one of " + ", ".join(sorted(DIRECTIONS)))
    if trap not in TRAPS:
        raise ValueError("trap must be one of " + ", ".join(sorted(TRAPS)))
    event = str(values.get("event_group") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", event):
        raise ValueError("event group required (letters, digits, . _ : -)")
    notes = str(values.get("notes") or "")
    if len(notes) > 2000:
        raise ValueError("notes exceed 2000 characters")
    return {"material": str(material).lower(), "urgent": str(urgent).lower(), "direction": direction, "trap": trap,
            "event_group": event, "notes": notes}


def complete(annotation: dict) -> bool:
    try:
        clean_label(annotation)
        return True
    except ValueError:
        return False


class LabelStore:
    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)
        self.lock = threading.Lock()

    def _load(self):
        manifest = json.loads((self.folder / "manifest.json").read_text(encoding="utf-8"))
        raw = (self.folder / "candidates.jsonl").read_bytes()
        if manifest.get("format") != FORMAT or digest(raw) != manifest.get("candidate_sha256"):
            raise ValueError("Corpus manifest or hash mismatch. Labels are read-only until it is repaired.")
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        reader = csv.DictReader(io.StringIO((self.folder / "label.csv").read_bytes().decode("utf-8-sig"), newline=""))
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError("label.csv header does not match the template")
        return manifest, rows, list(reader)

    def snapshot(self) -> dict:
        manifest, rows, annotations = self._load()
        by_id = {a.get("id"): a for a in annotations}
        items, done = [], []
        for row in rows:
            a = by_id.get(row["id"], {})
            ok = complete(a)
            if ok:
                done.append(row)
            items.append({**{k: row.get(k, "") for k in EVIDENCE}, "source": row.get("source", ""),
                          "label": {k: a.get(k, "") for k in LABEL_FIELDS}, "complete": ok})
        if done:
            splits = split_rows(labelled_rows(done, [by_id[r["id"]] for r in done]))
        else:
            splits = {"train": [], "heldout": []}
        labelled = splits["train"] + splits["heldout"]
        provenance_path = self.folder / "label_provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.is_file() else {"labeler": "human"}
        return {"corpus": self.folder.name, "collected_at": manifest.get("collected_at"), "warning": manifest.get("warning", ""),
                "provenance": {k: provenance.get(k) for k in ("labeler", "model", "human_review", "labelled_at")},
                "total": len(rows), "labelled": len(labelled), "target": TARGET, "trap_target": TRAP_TARGET,
                "traps": sum(bool(r.get("trap")) for r in labelled), "material": sum(r["material"] for r in labelled),
                "urgent": sum(r["urgent"] for r in labelled), "splits": {k: len(v) for k, v in splits.items()},
                "issues": readiness(splits, TARGET, TRAP_TARGET),
                "event_groups": sorted({by_id[r["id"]]["event_group"] for r in done}), "items": items}

    def save(self, body: dict) -> dict:
        row_id = str(body.get("id") or "")
        values = clean_label(body)
        with self.lock:
            _, _, annotations = self._load()
            target = next((a for a in annotations if a.get("id") == row_id), None)
            if target is None:
                raise KeyError("Headline not found in this corpus.")
            target.update(values)
            backup = self.folder / "label.before-ui.csv"
            if not backup.exists():
                shutil.copy2(self.folder / "label.csv", backup)
            fd, temp = tempfile.mkstemp(prefix=".label-", suffix=".csv", dir=self.folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows(annotations)
                os.replace(temp, self.folder / "label.csv")
            except BaseException:
                if os.path.exists(temp):
                    os.unlink(temp)
                raise
        return self.snapshot()
