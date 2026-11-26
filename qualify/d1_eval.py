"""Frozen paired one-look screen: LiquidAI/d1-2B vs GLiNER2.5-Decide (and cascade).

Protocol is frozen before inference (`freeze`), then executed once (`run`) with a
one-look lock (`started.json`). Evaluates ranking AUC, balanced accuracy, macro-F1,
Brier, ECE, trap accuracy, split consistency, paired bootstrap CIs, errors, and CPU
latency against explicit adoption gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core.router_worker import REVISION as GLINER_REVISION
from core.signals import GateConfig, Item, Judgment
from qualify.decide_eval import ece, load_rows
from qualify.run import pct

FORMAT = "cofinance-d1-eval/1"
D1_REPO = "LiquidAI/d1-2B"
D1_REVISION = "a6bce774e6dde338dcc3036ceb5d91f78bf4c19e"
DEFAULT_DATA = "qualify/headlines.jsonl"
DEFAULT_DTYPE = "float32"
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261126
CASCADE_LOW = 0.25
CASCADE_HIGH = 0.85
ADOPT_MIN_F1_GAIN = 0.05
ADOPT_MIN_AUC_GAIN = 0.05
ADOPT_MAX_P50_MS = 5000.0


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def roc_auc(scores: Sequence[float], labels: Sequence[bool]) -> Optional[float]:
    if len(scores) != len(labels) or not scores:
        return None
    n_pos = sum(1 for y in labels if y)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    indexed = sorted(enumerate(scores), key=lambda kv: kv[1])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    pos_rank_sum = sum(ranks[idx] for idx, y in enumerate(labels) if y)
    return round((pos_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg), 4)


def balanced_accuracy(preds: Sequence[Optional[bool]], labels: Sequence[bool]) -> Optional[float]:
    if len(preds) != len(labels) or not labels:
        return None
    pos = [i for i, y in enumerate(labels) if y]
    neg = [i for i, y in enumerate(labels) if not y]
    if not pos or not neg:
        return None
    tpr = sum(1 for i in pos if preds[i] is True) / len(pos)
    tnr = sum(1 for i in neg if preds[i] is False) / len(neg)
    return round(0.5 * (tpr + tnr), 4)


def _f1(tp: int, fp: int, fn: int) -> float:
    return 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)


def score_predictions(
    rows: Sequence[Mapping[str, Any]],
    preds: Sequence[Mapping[str, Any]],
    gate: Optional[GateConfig] = None,
) -> Dict[str, Any]:
    g = gate or GateConfig()
    if len(rows) != len(preds):
        raise ValueError(f"Row count mismatch: {len(rows)} rows vs {len(preds)} predictions")
    by_id = {str(p["id"]): p for p in preds}

    mat_labels: List[bool] = []
    mat_scores_all: List[float] = []
    mat_preds_all: List[Optional[bool]] = []
    urg_labels: List[bool] = []
    urg_scores_all: List[float] = []
    valid_probs: List[float] = []
    valid_labels: List[bool] = []
    ms_list: List[float] = []
    tp = fp = fn = tn = 0
    errors = 0
    trap_total = 0
    trap_ok = 0
    neg_total = 0
    false_alerts = 0
    urg_total = 0
    urg_alerts = 0

    for row in rows:
        rid = str(row["id"])
        p = by_id.get(rid)
        if p is None:
            raise ValueError(f"Missing prediction for row {rid!r}")
        mat_true = bool(row["material"])
        urg_true = bool(row.get("urgent", False))
        mat_labels.append(mat_true)
        urg_labels.append(urg_true)
        if not mat_true:
            neg_total += 1
        if urg_true:
            urg_total += 1
        if row.get("trap"):
            trap_total += 1
        ms_list.append(float(p.get("ms", 0.0)))

        if p.get("error"):
            errors += 1
            mat_preds_all.append(None)
            mat_scores_all.append(0.5)
            urg_scores_all.append(0.5)
            if mat_true:
                fn += 1
            else:
                fp += 1
            continue

        mp = float(p["material_p"])
        up = float(p["urgent_p"])
        pred_mat = mp >= g.digest_p
        alert = mp >= g.alert_p and up >= g.alert_urgent_p
        mat_preds_all.append(pred_mat)
        mat_scores_all.append(mp)
        urg_scores_all.append(up)
        valid_probs.append(mp)
        valid_labels.append(mat_true)

        if pred_mat and mat_true:
            tp += 1
        elif pred_mat and not mat_true:
            fp += 1
        elif not pred_mat and mat_true:
            fn += 1
        else:
            tn += 1

        if row.get("trap") and pred_mat == mat_true:
            trap_ok += 1
        if alert and not mat_true:
            false_alerts += 1
        if alert and urg_true:
            urg_alerts += 1

    n = len(rows)
    macro_f1 = round((_f1(tp, fp, fn) + _f1(tn, fn, fp)) / 2.0, 4) if n else 0.0
    acc = round((tp + tn) / n, 4) if n else 0.0
    brier = round(sum((p - y) ** 2 for p, y in zip(valid_probs, valid_labels)) / len(valid_probs), 4) if valid_probs else None
    ece_val = ece(valid_probs, valid_labels)
    return {
        "n": n,
        "errors": errors,
        "accuracy": acc,
        "balanced_accuracy": balanced_accuracy(mat_preds_all, mat_labels),
        "macro_f1": macro_f1,
        "material_auc": roc_auc(mat_scores_all, mat_labels),
        "urgent_auc": roc_auc(urg_scores_all, urg_labels),
        "brier": brier,
        "ece": round(ece_val, 4) if ece_val is not None else None,
        "false_alert_rate": round(false_alerts / neg_total, 4) if neg_total else 0.0,
        "alert_recall": round(urg_alerts / urg_total, 4) if urg_total else None,
        "trap_accuracy": round(trap_ok / trap_total, 4) if trap_total else None,
        "p50_ms": round(pct(ms_list, 0.5), 1) if ms_list else 0.0,
        "p95_ms": round(pct(ms_list, 0.95), 1) if ms_list else 0.0,
        "total_s": round(sum(ms_list) / 1000.0, 2),
    }


def build_cascade_predictions(
    gliner_preds: Sequence[Mapping[str, Any]],
    d1_preds: Sequence[Mapping[str, Any]],
    *,
    low: float = CASCADE_LOW,
    high: float = CASCADE_HIGH,
    gate: Optional[GateConfig] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    g = gate or GateConfig()
    d1_by_id = {str(p["id"]): p for p in d1_preds}
    out: List[Dict[str, Any]] = []
    routes = {"gliner_only": 0, "escalated_d1": 0, "d1_error_fallback": 0}

    for gp in gliner_preds:
        rid = str(gp["id"])
        dp = d1_by_id[rid]
        if gp.get("error"):
            routes["escalated_d1"] += 1
            out.append(dict(dp, route="escalated_d1", ms=round(float(gp.get("ms", 0.0)) + float(dp.get("ms", 0.0)), 1)))
            continue
        mp = float(gp["material_p"])
        up = float(gp["urgent_p"])
        would_alert = mp >= g.alert_p and up >= g.alert_urgent_p
        in_gray = low <= mp <= high
        if in_gray or would_alert:
            routes["escalated_d1"] += 1
            combined_ms = round(float(gp.get("ms", 0.0)) + float(dp.get("ms", 0.0)), 1)
            if dp.get("error"):
                routes["d1_error_fallback"] += 1
                out.append({**gp, "route": "d1_error_fallback", "ms": combined_ms, "alert_allowed": False})
            else:
                out.append({**dp, "route": "escalated_d1", "ms": combined_ms})
        else:
            routes["gliner_only"] += 1
            out.append({**gp, "route": "gliner_only"})
    return out, routes


def paired_bootstrap_diff(
    rows: Sequence[Mapping[str, Any]],
    base_preds: Sequence[Mapping[str, Any]],
    cand_preds: Sequence[Mapping[str, Any]],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    gate: Optional[GateConfig] = None,
) -> Dict[str, Any]:
    g = gate or GateConfig()
    n = len(rows)
    base_by_id = {str(p["id"]): p for p in base_preds}
    cand_by_id = {str(p["id"]): p for p in cand_preds}
    ordered_base = [base_by_id[str(r["id"])] for r in rows]
    ordered_cand = [cand_by_id[str(r["id"])] for r in rows]

    point_base = score_predictions(rows, ordered_base, g)
    point_cand = score_predictions(rows, ordered_cand, g)

    rng = random.Random(seed)
    diffs: Dict[str, List[float]] = {"material_auc": [], "balanced_accuracy": [], "macro_f1": []}
    for _ in range(resamples):
        idxs = [rng.randrange(n) for _ in range(n)]
        s_rows = [dict(rows[i], id=f"b{k}") for k, i in enumerate(idxs)]
        s_base = [dict(ordered_base[i], id=f"b{k}") for k, i in enumerate(idxs)]
        s_cand = [dict(ordered_cand[i], id=f"b{k}") for k, i in enumerate(idxs)]
        sb = score_predictions(s_rows, s_base, g)
        sc = score_predictions(s_rows, s_cand, g)
        for metric in ("material_auc", "balanced_accuracy", "macro_f1"):
            bv, cv = sb[metric], sc[metric]
            if bv is not None and cv is not None:
                diffs[metric].append(cv - bv)

    out: Dict[str, Any] = {}
    for metric in ("material_auc", "balanced_accuracy", "macro_f1"):
        vals = sorted(diffs[metric])
        pb, pc = point_base[metric], point_cand[metric]
        pt = round(pc - pb, 4) if (pb is not None and pc is not None) else None
        if vals:
            lo = round(vals[int(0.025 * (len(vals) - 1))], 4)
            hi = round(vals[int(0.975 * (len(vals) - 1))], 4)
        else:
            lo = hi = None
        out[metric] = {"diff": pt, "ci95": [lo, hi]}
    return out


def split_half_check(
    rows: Sequence[Mapping[str, Any]],
    preds: Sequence[Mapping[str, Any]],
    gate: Optional[GateConfig] = None,
) -> Dict[str, Any]:
    mid = len(rows) // 2
    by_id = {str(p["id"]): p for p in preds}
    first_rows = list(rows[:mid])
    second_rows = list(rows[mid:])
    first = score_predictions(first_rows, [by_id[str(r["id"])] for r in first_rows], gate)
    second = score_predictions(second_rows, [by_id[str(r["id"])] for r in second_rows], gate)
    return {
        "first_half": {k: first[k] for k in ("n", "material_auc", "balanced_accuracy", "macro_f1", "errors")},
        "second_half": {k: second[k] for k in ("n", "material_auc", "balanced_accuracy", "macro_f1", "errors")},
    }


def decide_adoption(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    paired: Mapping[str, Any],
) -> Dict[str, Any]:
    f1_gain = float(candidate["macro_f1"]) - float(baseline["macro_f1"])
    auc_diff = paired["material_auc"]["diff"]
    auc_lo = paired["material_auc"]["ci95"][0]
    bal_b = baseline.get("balanced_accuracy")
    bal_c = candidate.get("balanced_accuracy")

    regress = (
        int(candidate["errors"]) > int(baseline["errors"])
        or float(candidate["false_alert_rate"]) > float(baseline["false_alert_rate"]) + 1e-9
        or (bal_b is not None and bal_c is not None and float(bal_c) < float(bal_b) - 1e-9)
    )
    skill_gain_met = (
        f1_gain >= ADOPT_MIN_F1_GAIN
        or (auc_diff is not None and auc_lo is not None and auc_diff >= ADOPT_MIN_AUC_GAIN and auc_lo > 0.0)
    )
    latency_met = float(candidate["p50_ms"]) <= ADOPT_MAX_P50_MS
    adopt = (not regress) and skill_gain_met and latency_met

    if adopt:
        verdict = "ADOPT"
    elif regress:
        verdict = "REJECT_REGRESSION"
    elif not skill_gain_met and not latency_met:
        verdict = "REJECT_NO_GAIN_AND_SLOW"
    elif not skill_gain_met:
        verdict = "REJECT_NO_SKILL_GAIN"
    else:
        verdict = "REJECT_TOO_SLOW_ON_CPU"

    return {
        "adopt": adopt,
        "verdict": verdict,
        "regression": regress,
        "skill_gain_met": skill_gain_met,
        "latency_met": latency_met,
        "macro_f1_gain": round(f1_gain, 4),
        "material_auc_diff": auc_diff,
        "material_auc_ci95_lo": auc_lo,
        "candidate_p50_ms": candidate["p50_ms"],
        "max_p50_ms": ADOPT_MAX_P50_MS,
    }


def build_protocol(data_path: Path, *, dtype: str = DEFAULT_DTYPE) -> Dict[str, Any]:
    rows = load_rows(str(data_path))
    plan = {
        "format": FORMAT,
        "data_path": data_path.as_posix(),
        "data_sha256": file_sha256(data_path),
        "n_rows": len(rows),
        "arms": {
            "gliner": {"model": "fastino/GLiNER2.5-Decide", "revision": GLINER_REVISION},
            "d1": {"model": D1_REPO, "revision": D1_REVISION, "dtype": dtype},
            "cascade_gliner_d1": {"low": CASCADE_LOW, "high": CASCADE_HIGH},
        },
        "gate": {"digest_p": 0.50, "alert_p": 0.85, "alert_urgent_p": 0.70},
        "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED},
        "adoption": {
            "min_f1_gain": ADOPT_MIN_F1_GAIN,
            "min_auc_gain": ADOPT_MIN_AUC_GAIN,
            "require_auc_ci_above_zero": True,
            "max_p50_ms": ADOPT_MAX_P50_MS,
        },
    }
    plan["protocol_sha256"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


def freeze(out_dir: Path, data_path: Path = Path(DEFAULT_DATA), *, dtype: str = DEFAULT_DTYPE) -> Dict[str, Any]:
    root = Path(out_dir)
    proto_path = root / "protocol.json"
    if proto_path.exists():
        raise FileExistsError(f"Protocol already frozen at {proto_path}. Create a new directory for a new run.")
    root.mkdir(parents=True, exist_ok=True)
    plan = build_protocol(data_path, dtype=dtype)
    proto_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return plan


def verify_protocol(out_dir: Path) -> Dict[str, Any]:
    root = Path(out_dir)
    plan = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
    if plan.get("format") != FORMAT:
        raise ValueError(f"Unsupported protocol format: {plan.get('format')!r}")
    saved_sha = plan.get("protocol_sha256")
    check_copy = {k: v for k, v in plan.items() if k != "protocol_sha256"}
    recomputed = hashlib.sha256(
        json.dumps(check_copy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if saved_sha != recomputed:
        raise ValueError("Protocol SHA-256 mismatch.")
    data_path = Path(plan["data_path"])
    if not data_path.exists() or file_sha256(data_path) != plan["data_sha256"]:
        raise ValueError("Dataset SHA-256 changed after protocol freeze.")
    return plan


def run_arm_predictions(
    clf: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> List[Dict[str, Any]]:
    preds: List[Dict[str, Any]] = []
    for row in rows:
        item = Item(
            str(row["ticker"]),
            str(row.get("source", "eval")),
            str(row.get("url", f"eval://{row['id']}")),
            str(row["title"]),
            str(row.get("text", "")),
        )
        t0 = clock()
        try:
            j = clf.judge(item)
            err = None
        except Exception as exc:
            j = None
            err = f"{type(exc).__name__}: {exc}"
        elapsed_ms = round((clock() - t0) * 1000.0, 1)
        if j is None:
            preds.append({"id": str(row["id"]), "error": err, "ms": elapsed_ms})
        else:
            preds.append({
                "id": str(row["id"]),
                "material_p": round(float(j.material), 4),
                "urgent_p": round(float(j.urgent), 4),
                "direction": str(j.direction),
                "direction_p": round(float(j.direction_p), 4),
                "ms": elapsed_ms,
            })
    return preds


def run_frozen(
    out_dir: Path,
    *,
    gliner_factory: Optional[Callable[[], Any]] = None,
    d1_factory: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    root = Path(out_dir)
    plan = verify_protocol(root)
    started_path = root / "started.json"
    if started_path.exists():
        raise FileExistsError(f"One-look guard: {started_path} already exists. Create a new folder for a new run.")
    started_path.write_text(
        json.dumps({"protocol_sha256": plan["protocol_sha256"], "started_epoch": time.time()}, indent=2) + "\n",
        encoding="utf-8",
    )

    rows = load_rows(plan["data_path"])
    dtype_name = str(plan["arms"]["d1"]["dtype"])
    gate = GateConfig(**plan["gate"])

    if gliner_factory is None:
        from core.gliner_decide import GlinerDecideClassifier
        from core.router_worker import RouterProcess

        proc = RouterProcess(Path.cwd(), Path.cwd() / "data" / "local_models")
        proc.start()
        try:
            gliner_clf = GlinerDecideClassifier(proc)
            gliner_preds = run_arm_predictions(gliner_clf, rows)
            gliner_backend = proc.backend
        finally:
            proc.close()
    else:
        gliner_preds = run_arm_predictions(gliner_factory(), rows)
        gliner_backend = "injected"

    if d1_factory is None:
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModel
        from core.systemone import SystemOneClient, SystemOneSignalClassifier, inprocess_transport

        snap = snapshot_download(repo_id=D1_REPO, revision=D1_REVISION, local_files_only=True)
        dt = torch.bfloat16 if dtype_name == "bfloat16" else torch.float32
        model = AutoModel.from_pretrained(snap, trust_remote_code=True, local_files_only=True, dtype=dt)
        d1_clf = SystemOneSignalClassifier(SystemOneClient(inprocess_transport(model)))
        d1_preds = run_arm_predictions(d1_clf, rows)
    else:
        d1_preds = run_arm_predictions(d1_factory(dtype_name), rows)

    cascade_preds, cascade_routes = build_cascade_predictions(gliner_preds, d1_preds, gate=gate)

    gliner_score = score_predictions(rows, gliner_preds, gate)
    d1_score = score_predictions(rows, d1_preds, gate)
    cascade_score = score_predictions(rows, cascade_preds, gate)

    paired_d1 = paired_bootstrap_diff(rows, gliner_preds, d1_preds, gate=gate)
    paired_cascade = paired_bootstrap_diff(rows, gliner_preds, cascade_preds, gate=gate)

    decision_d1 = decide_adoption(gliner_score, d1_score, paired_d1)
    decision_cascade = decide_adoption(gliner_score, cascade_score, paired_cascade)

    report = {
        "warning": "Experimental one-look screen on 24 synthetic headlines. Not a trading signal, not financial advice.",
        "format": FORMAT,
        "protocol_sha256": plan["protocol_sha256"],
        "data_sha256": plan["data_sha256"],
        "data_path": plan["data_path"],
        "gliner_backend": gliner_backend,
        "d1_revision": D1_REVISION,
        "d1_dtype": dtype_name,
        "arms": {
            "gliner": gliner_score,
            "d1": d1_score,
            "cascade_gliner_d1": {**cascade_score, "routes": cascade_routes},
        },
        "splits": {
            "gliner": split_half_check(rows, gliner_preds, gate),
            "d1": split_half_check(rows, d1_preds, gate),
            "cascade_gliner_d1": split_half_check(rows, cascade_preds, gate),
        },
        "paired_vs_gliner": {
            "d1": paired_d1,
            "cascade_gliner_d1": paired_cascade,
        },
        "decisions": {
            "d1": decision_d1,
            "cascade_gliner_d1": decision_cascade,
        },
        "limits": [
            "24 synthetic/authored headlines from qualify/headlines.jsonl, not real live market feeds",
            "CPU-only measurement on one Windows machine; GPU latency was not tested",
            "d1-2B evaluated via SystemOneSignalClassifier with default noul/choice prompts, not fine-tuned",
        ],
    }
    outputs = {
        "protocol_sha256": plan["protocol_sha256"],
        "gliner": gliner_preds,
        "d1": d1_preds,
        "cascade_gliner_d1": cascade_preds,
    }
    (root / "outputs.json").write_text(json.dumps(outputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="qualify.d1_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_fr = sub.add_parser("freeze")
    p_fr.add_argument("out_dir")
    p_fr.add_argument("--data", default=DEFAULT_DATA)
    p_fr.add_argument("--dtype", choices=("bfloat16", "float32"), default=DEFAULT_DTYPE)
    p_rn = sub.add_parser("run")
    p_rn.add_argument("out_dir")
    a = ap.parse_args(argv)
    if a.cmd == "freeze":
        plan = freeze(Path(a.out_dir), Path(a.data), dtype=a.dtype)
        print(f"protocol {plan['protocol_sha256']}")
        return 0
    rep = run_frozen(Path(a.out_dir))
    print(json.dumps({"decisions": rep["decisions"], "arms": rep["arms"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
