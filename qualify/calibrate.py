"""SignalGate threshold calibration: thresholds from train only, one held-out evaluation.

  python -m qualify.calibrate score --corpus data/news_corpus_YYYYMMDD --out data/calibration_X/scores.jsonl
  python -m qualify.calibrate calibrate --corpus data/news_corpus_YYYYMMDD --scores .../scores.jsonl --out data/calibration_X
                                        [--provisional]

Rules:
  * labels, split and readiness come from qualify.news_dataset (same functions finalize uses);
  * if the readiness gate fails, calibration runs only with --provisional and the report says so;
  * label provenance (human or model) is copied into the report; model labels are never called human;
  * held-out is evaluated once per (corpus, labels, scores) hash; a second run is refused;
  * the report proposes thresholds. It never edits core.signals.GateConfig.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .news_dataset import FIELDS, FORMAT, digest, labelled_rows, readiness, split_rows

GRID = [round(0.05 * i, 2) for i in range(1, 20)]


def load_corpus(corpus: Path):
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    raw = (corpus / "candidates.jsonl").read_bytes()
    if manifest.get("format") != FORMAT or digest(raw) != manifest.get("candidate_sha256"):
        raise ValueError("corpus manifest or hash mismatch")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    labels_raw = (corpus / "label.csv").read_bytes()
    reader = csv.DictReader(io.StringIO(labels_raw.decode("utf-8-sig"), newline=""))
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError("label CSV header does not match the template")
    provenance_path = corpus / "label_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.is_file() else {"labeler": "human"}
    return manifest, rows, list(reader), digest(raw), digest(labels_raw), provenance


def counts(pred, gold):
    tp = sum(p and g for p, g in zip(pred, gold)); fp = sum(p and not g for p, g in zip(pred, gold))
    fn = sum(g and not p for p, g in zip(pred, gold)); tn = len(gold) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "f1": round(f1, 4)}


def fbeta(c, beta):
    p, r = c["precision"] or 0.0, c["recall"] or 0.0
    return (1 + beta * beta) * p * r / (beta * beta * p + r) if p and r else 0.0


def decide(score, cfg):
    digest_ = score["material"] >= cfg["digest_p"]
    alert = digest_ and score["material"] >= cfg["alert_p"] and score["urgent"] >= cfg["alert_urgent_p"]
    return digest_, alert


def evaluate(rows, scores, cfg):
    decisions = [decide(scores[r["id"]], cfg) for r in rows]
    traps = [i for i, r in enumerate(rows) if r.get("trap")]
    material_rows = [i for i, r in enumerate(rows) if r["material"]]
    return {"rows": len(rows),
            "digest_vs_material": counts([d for d, _ in decisions], [r["material"] for r in rows]),
            "alert_vs_urgent": counts([a for _, a in decisions], [r["urgent"] for r in rows]),
            "traps": len(traps), "traps_surfaced": sum(decisions[i][0] for i in traps), "traps_alerted": sum(decisions[i][1] for i in traps),
            "direction_agreement_on_material": (sum(scores[rows[i]["id"]]["direction"] == rows[i]["direction"] for i in material_rows),
                                                len(material_rows))}


def choose(train, scores):
    """Digest: max F1 on material. Alert: max F0.5 on urgent (alerts interrupt, so precision counts more).
    Ties prefer higher thresholds (fewer surfaced items)."""
    gold_m = [r["material"] for r in train]
    best_d = max(GRID, key=lambda t: (counts([scores[r["id"]]["material"] >= t for r in train], gold_m)["f1"], t))
    gold_u = [r["urgent"] for r in train]
    def alert_score(pair):
        a, u = pair
        pred = [scores[r["id"]]["material"] >= max(a, best_d) and scores[r["id"]]["urgent"] >= u for r in train]
        return (round(fbeta(counts(pred, gold_u), 0.5), 6), a, u)
    pairs = [(a, u) for a in GRID if a >= best_d for u in GRID]
    best_a, best_u = max(pairs, key=alert_score)[:2] if pairs else (best_d, 0.95)
    return {"digest_p": best_d, "alert_p": best_a, "alert_urgent_p": best_u}, sum(gold_u)


def score(corpus: Path, out: Path) -> dict:
    from core.chat_service import GLINER_REVISION
    from core.gliner_decide import GlinerDecideClassifier
    from core.router_worker import RouterProcess
    from core.signals import Item
    from core.collector import epoch
    _, rows, _, corpus_sha, _, _ = load_corpus(corpus)
    repo = Path(__file__).resolve().parents[1]
    backend = RouterProcess(repo, repo / "data" / "local_models", threading.Event())
    backend.start()
    judge = GlinerDecideClassifier(backend)
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        with out.open("w", encoding="utf-8") as f:
            for r in rows:
                j = judge.judge(Item(r["ticker"], r["source"], r["url"], r["title"], r.get("text", ""), epoch(r.get("timestamp", ""))))
                f.write(json.dumps({"id": r["id"], **asdict(j)}) + "\n")
    finally:
        backend.close()
    meta = {"model": "fastino/GLiNER2.5-Decide", "revision": GLINER_REVISION, "network": False, "corpus_sha256": corpus_sha,
            "scores_sha256": digest(out.read_bytes()), "rows": len(rows), "seconds": round(time.time() - started, 1)}
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def calibrate(corpus: Path, scores_path: Path, out: Path, provisional: bool) -> dict:
    from core.signals import GateConfig
    manifest, rows, annotations, corpus_sha, labels_sha, provenance = load_corpus(corpus)
    meta = json.loads(scores_path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    scores_sha = digest(scores_path.read_bytes())
    if meta.get("corpus_sha256") != corpus_sha or meta.get("scores_sha256") != scores_sha:
        raise ValueError("scores do not belong to this corpus or were changed")
    scores = {s["id"]: s for s in map(json.loads, scores_path.read_text(encoding="utf-8").splitlines()) if s}
    splits = split_rows(labelled_rows(rows, annotations))
    issues = readiness(splits)
    if issues and not provisional:
        raise ValueError("not calibration-ready: " + "; ".join(issues) + " (use --provisional to record a non-final run)")
    key = digest(corpus_sha + labels_sha + scores_sha)
    marker = out / "heldout_used.json"
    if marker.is_file() and json.loads(marker.read_text(encoding="utf-8")).get("key") == key:
        raise ValueError("held-out was already evaluated for these labels and scores; refusing a second look")
    chosen, train_urgent = choose(splits["train"], scores)
    g = GateConfig()
    current = {"digest_p": g.digest_p, "alert_p": g.alert_p, "alert_urgent_p": g.alert_urgent_p}
    report = {"format": "cofinance-calibration/1", "status": "provisional" if issues else "final", "readiness_issues": issues,
              "labels": {"labeler": provenance.get("labeler"), "model": provenance.get("model"), "human_review": provenance.get("human_review", provenance.get("labeler") == "human")},
              "scores": {k: meta[k] for k in ("model", "revision", "network")},
              "hashes": {"corpus": corpus_sha, "labels": labels_sha, "scores": scores_sha},
              "split_counts": {k: len(v) for k, v in splits.items()}, "train_urgent_positives": train_urgent,
              "current_thresholds": current, "calibrated_thresholds": chosen,
              "train": {"current": evaluate(splits["train"], scores, current), "calibrated": evaluate(splits["train"], scores, chosen)},
              "heldout": {"current": evaluate(splits["heldout"], scores, current), "calibrated": evaluate(splits["heldout"], scores, chosen)},
              "note": "Thresholds are proposals. GateConfig is unchanged until a reviewed edit."}
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    marker.write_text(json.dumps({"key": key, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}), encoding="utf-8")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("score"); s.add_argument("--corpus", required=True); s.add_argument("--out", required=True)
    c = sub.add_parser("calibrate"); c.add_argument("--corpus", required=True); c.add_argument("--scores", required=True)
    c.add_argument("--out", required=True); c.add_argument("--provisional", action="store_true")
    a = ap.parse_args(argv)
    try:
        result = score(Path(a.corpus), Path(a.out)) if a.command == "score" else \
            calibrate(Path(a.corpus), Path(a.scores), Path(a.out), a.provisional)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"Calibration stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
