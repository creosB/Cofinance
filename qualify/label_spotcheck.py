"""Stratified blind 20-item human spot-check sampler and agreement scorer (stdlib only).

Draws a sealed, stratified blind CSV from a model-labelled headline corpus without
exposing any model labels, then computes exact agreement, Cohen's kappa, balanced
accuracy, and per-row disagreements once a human completes the blind sheet.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .label_store import EVIDENCE, LABEL_FIELDS, clean_label, complete
from .news_dataset import FIELDS, FORMAT as CORPUS_FORMAT, csv_safe, digest

FORMAT = "cofinance-label-spotcheck/1"
DEFAULT_SAMPLE_SIZE = 20
DEFAULT_SEED = 20261126
BLIND_FILENAME = "spotcheck_20_blind.csv"
KEY_FILENAME = "spotcheck_20_key.json"


def _load_corpus(corpus_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    root = Path(corpus_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    raw = (root / "candidates.jsonl").read_bytes()
    if manifest.get("format") != CORPUS_FORMAT or digest(raw) != manifest.get("candidate_sha256"):
        raise ValueError("Corpus manifest or candidate SHA-256 mismatch.")
    reader = csv.DictReader(io.StringIO((root / "label.csv").read_bytes().decode("utf-8-sig"), newline=""))
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError("label.csv header does not match news_dataset FIELDS.")
    rows = list(reader)
    return manifest, rows


def _stratum(cleaned: Mapping[str, str]) -> str:
    if cleaned["trap"] != "none":
        return "trap"
    if cleaned["urgent"] == "true":
        return "urgent_material"
    if cleaned["material"] == "true":
        return "non_urgent_material"
    return "clean_non_material"


def select_stratified_sample(
    rows: Sequence[Mapping[str, str]],
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
) -> List[Tuple[Dict[str, str], Dict[str, str], str]]:
    valid: List[Tuple[Dict[str, str], Dict[str, str], str]] = []
    for row in rows:
        if complete(dict(row)):
            cleaned = clean_label(dict(row))
            valid.append((dict(row), cleaned, _stratum(cleaned)))
    if len(valid) < sample_size:
        raise ValueError(f"Need at least {sample_size} complete labelled rows, found {len(valid)}.")

    rng = random.Random(seed)
    buckets: Dict[str, List[Tuple[Dict[str, str], Dict[str, str], str]]] = {
        "trap": [],
        "urgent_material": [],
        "non_urgent_material": [],
        "clean_non_material": [],
    }
    for item in sorted(valid, key=lambda x: str(x[0].get("id") or "")):
        buckets[item[2]].append(item)
    for key in buckets:
        rng.shuffle(buckets[key])

    order = ("trap", "urgent_material", "non_urgent_material", "clean_non_material")
    picked: List[Tuple[Dict[str, str], Dict[str, str], str]] = []
    idx = 0
    while len(picked) < sample_size:
        progressed = False
        for key in order:
            if len(picked) >= sample_size:
                break
            if buckets[key]:
                picked.append(buckets[key].pop())
                progressed = True
        if not progressed:
            break
        idx += 1

    rng.shuffle(picked)
    return picked


def prepare_spotcheck(
    corpus_dir: Path,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
    overwrite: bool = False,
) -> Dict[str, Any]:
    root = Path(corpus_dir)
    blind_path = root / BLIND_FILENAME
    key_path = root / KEY_FILENAME
    if not overwrite and (blind_path.exists() or key_path.exists()):
        raise FileExistsError(f"Spot-check files already exist in {root}. Pass overwrite=True to regenerate.")

    manifest, rows = _load_corpus(root)
    picked = select_stratified_sample(rows, sample_size=sample_size, seed=seed)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    strata_counts: Dict[str, int] = {}
    key_rows: Dict[str, Dict[str, Any]] = {}

    for row, cleaned, stratum in picked:
        strata_counts[stratum] = strata_counts.get(stratum, 0) + 1
        blind_row = {k: csv_safe(row.get(k, "")) for k in EVIDENCE}
        for lf in LABEL_FIELDS:
            blind_row[lf] = ""
        writer.writerow(blind_row)
        key_rows[row["id"]] = {
            "ticker": row.get("ticker", ""),
            "title": row.get("title", ""),
            "stratum": stratum,
            "model_label": cleaned,
        }

    blind_bytes = buf.getvalue().encode("utf-8-sig")
    blind_path.write_bytes(blind_bytes)

    key_payload = {
        "format": FORMAT,
        "corpus": root.name,
        "candidate_sha256": manifest["candidate_sha256"],
        "seed": seed,
        "sample_size": len(picked),
        "strata_counts": dict(sorted(strata_counts.items())),
        "blind_csv": BLIND_FILENAME,
        "blind_csv_sha256": hashlib.sha256(blind_bytes).hexdigest(),
        "ids": [row["id"] for row, _, _ in picked],
        "reference_labels": key_rows,
    }
    key_bytes = (json.dumps(key_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    key_path.write_bytes(key_bytes)
    return {
        "format": FORMAT,
        "corpus": root.name,
        "sample_size": len(picked),
        "strata_counts": dict(sorted(strata_counts.items())),
        "blind_csv": str(blind_path),
        "key_json": str(key_path),
        "blind_csv_sha256": key_payload["blind_csv_sha256"],
        "human_completed": 0,
    }


def cohens_kappa(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    n = len(a)
    if n == 0 or n != len(b):
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    labels = sorted(set(a) | set(b))
    pe = sum((sum(1 for x in a if x == lbl) / n) * (sum(1 for y in b if y == lbl) / n) for lbl in labels)
    if abs(1.0 - pe) < 1e-12:
        return 1.0 if po == 1.0 else 0.0
    return round((po - pe) / (1.0 - pe), 4)


def balanced_accuracy(y_true: Sequence[bool], y_pred: Sequence[bool]) -> Optional[float]:
    pos = [i for i, v in enumerate(y_true) if v]
    neg = [i for i, v in enumerate(y_true) if not v]
    if not pos or not neg:
        return None
    tpr = sum(1 for i in pos if y_pred[i]) / len(pos)
    tnr = sum(1 for i in neg if not y_pred[i]) / len(neg)
    return round(0.5 * (tpr + tnr), 4)


def score_spotcheck(
    corpus_dir: Path,
    *,
    blind_csv: Optional[Path] = None,
    key_json: Optional[Path] = None,
) -> Dict[str, Any]:
    root = Path(corpus_dir)
    blind_path = Path(blind_csv) if blind_csv else root / BLIND_FILENAME
    key_path = Path(key_json) if key_json else root / KEY_FILENAME

    key_data = json.loads(key_path.read_text(encoding="utf-8"))
    if key_data.get("format") != FORMAT:
        raise ValueError(f"Unsupported spot-check key format: {key_data.get('format')!r}")
    expected_ids = list(key_data["ids"])
    ref_map: Dict[str, Dict[str, Any]] = key_data["reference_labels"]

    reader = csv.DictReader(io.StringIO(blind_path.read_bytes().decode("utf-8-sig"), newline=""))
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError("Spot-check CSV header does not match news_dataset FIELDS.")
    rows = list(reader)
    got_ids = [r.get("id", "") for r in rows]
    if set(got_ids) != set(expected_ids) or len(got_ids) != len(expected_ids):
        raise ValueError("Spot-check CSV row IDs do not match the sealed key.")

    human_by_id: Dict[str, Dict[str, str]] = {}
    incomplete_ids: List[str] = []
    for row in rows:
        rid = row["id"]
        if not complete(row):
            incomplete_ids.append(rid)
        else:
            human_by_id[rid] = clean_label(row)

    if incomplete_ids:
        raise ValueError(
            f"Spot-check incomplete: {len(incomplete_ids)} of {len(expected_ids)} rows still need valid human labels."
        )

    fields_to_score = ("material", "urgent", "direction", "trap")
    metrics: Dict[str, Dict[str, Any]] = {}
    for field in fields_to_score:
        h_vals = [human_by_id[rid][field] for rid in expected_ids]
        m_vals = [ref_map[rid]["model_label"][field] for rid in expected_ids]
        exact = sum(1 for x, y in zip(h_vals, m_vals) if x == y)
        entry: Dict[str, Any] = {
            "agreement": round(exact / len(expected_ids), 4),
            "matches": exact,
            "total": len(expected_ids),
            "cohens_kappa": cohens_kappa(h_vals, m_vals),
        }
        if field in ("material", "urgent"):
            entry["balanced_accuracy_vs_human"] = balanced_accuracy(
                [v == "true" for v in h_vals],
                [v == "true" for v in m_vals],
            )
        elif field == "trap":
            entry["trap_any_kappa"] = cohens_kappa(
                ["trap" if v != "none" else "none" for v in h_vals],
                ["trap" if v != "none" else "none" for v in m_vals],
            )
        metrics[field] = entry

    disagreements: List[Dict[str, Any]] = []
    for rid in expected_ids:
        h = human_by_id[rid]
        m = ref_map[rid]["model_label"]
        diffs = {f: {"human": h[f], "model": m[f]} for f in fields_to_score if h[f] != m[f]}
        if diffs:
            disagreements.append({
                "id": rid,
                "ticker": ref_map[rid]["ticker"],
                "title": ref_map[rid]["title"],
                "stratum": ref_map[rid]["stratum"],
                "diffs": diffs,
            })

    return {
        "format": FORMAT,
        "corpus": key_data["corpus"],
        "n": len(expected_ids),
        "human_completed": len(expected_ids),
        "metrics": metrics,
        "disagreement_count": len(disagreements),
        "disagreements": disagreements,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="qualify.label_spotcheck")
    sub = ap.add_subparsers(dest="action", required=True)
    p_prep = sub.add_parser("prepare")
    p_prep.add_argument("--corpus", default="data/news_corpus_20261108")
    p_prep.add_argument("--size", type=int, default=DEFAULT_SAMPLE_SIZE)
    p_prep.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_prep.add_argument("--overwrite", action="store_true")
    p_score = sub.add_parser("score")
    p_score.add_argument("--corpus", default="data/news_corpus_20261108")
    a = ap.parse_args(argv)
    if a.action == "prepare":
        print(json.dumps(prepare_spotcheck(Path(a.corpus), sample_size=a.size, seed=a.seed, overwrite=a.overwrite), indent=2))
        return 0
    print(json.dumps(score_spotcheck(Path(a.corpus)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
