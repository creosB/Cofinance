"""Track 2 offline financial-signal AUC screen on cached headline judgments (cache-only, zero network).

Compares direction-agnostic ranking skill (ROC AUC, 0.5 = no skill) and balanced accuracy across five
pure-Python aggregation strategies over the already-cached AtomicChat headline judgments and local
GLiNER2.5-Decide materiality scores (retiring the inverted urgency head):
  1. sum_v1                        - baseline sum of headline impacts (call line +-0.5)
  2. mean_v1                       - arithmetic mean of headline impacts (call line +-0.4)
  3. materiality_weighted_mean     - GLiNER material_p weighted mean (call line +-0.4)
  4. dedup_mean                    - near-duplicate story clustering (Jaccard >= 0.45) + mean
  5. materiality_dedup_source_mean - deduped story clusters weighted by material_p * source_tier

Strictly offline and retrospective on existing cached windows. Never modifies sealed files, never
reopens missing exits, and never adopts a live formula from dev/reused outcomes.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.gliner_decide import GlinerDecideClassifier
from core.signals import Item
from core.urgency import OFFICIAL
from qualify import decision_lab as dl
from qualify import replay as R

WARNING = (
    "Experimental offline aggregation screen on existing cached windows (dev 72-case replay and the "
    "already-looked-at 24 scored cases of the 11/09 window). Not a fresh unseen evaluation, not a "
    "trading signal, and not financial advice. Cofinance never trades."
)

STOP_WORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "has", "have",
    "its", "into", "over", "after", "before", "amid", "says", "new", "will", "about", "more",
    "to", "of", "in", "on", "at", "as", "by", "down", "up", "out",
})
ISSUER_SOURCES = frozenset(OFFICIAL) | {"company_release"}
JACCARD_THRESHOLD = 0.45


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{2,}", (title or "").lower())
    filtered = {w for w in words if w not in STOP_WORDS}
    return filtered or set(words)


def source_tier_weight(source: str) -> float:
    low = (source or "").strip().lower()
    if low in ISSUER_SOURCES:
        return 0.5
    if low == "hackernews":
        return 0.75
    return 1.0


def dedup_story_items(items: Sequence[Mapping[str, Any]], *, threshold: float = JACCARD_THRESHOLD) -> List[Dict[str, Any]]:
    """Cluster near-duplicate syndicated titles for one asset-origin case and keep the strongest |impact| item per story."""
    clusters: List[Tuple[set[str], Dict[str, Any]]] = []
    for raw in items:
        toks = title_tokens(str(raw.get("title") or ""))
        imp = float(raw.get("impact") or 0.0)
        matched_idx = None
        for idx, (ctoks, _) in enumerate(clusters):
            union = toks | ctoks
            if union and (len(toks & ctoks) / len(union)) >= threshold:
                matched_idx = idx
                break
        item_copy = dict(raw)
        if matched_idx is None:
            clusters.append((set(toks), item_copy))
        else:
            ctoks, best = clusters[matched_idx]
            ctoks |= toks
            if abs(imp) > abs(float(best.get("impact") or 0.0)):
                clusters[matched_idx] = (ctoks, item_copy)
    return [best for _, best in clusters]


def aggregate_case_score(items: Sequence[Mapping[str, Any]], arm: str) -> Tuple[float, str]:
    if not items:
        return 0.0, "ABSTAIN"
    if arm == "sum_v1":
        s = round(sum(float(x.get("impact") or 0.0) for x in items), 6)
        call = "UP" if s >= 0.5 else "DOWN" if s <= -0.5 else "ABSTAIN"
        return s, call
    if arm == "mean_v1":
        s = round(sum(float(x.get("impact") or 0.0) for x in items) / len(items), 6)
    elif arm == "materiality_weighted_mean":
        weights = [max(0.05, float(x.get("material_p") if x.get("material_p") is not None else 0.5)) for x in items]
        total_w = sum(weights)
        s = round(sum(w * float(x.get("impact") or 0.0) for w, x in zip(weights, items)) / total_w, 6)
    elif arm == "dedup_mean":
        deduped = dedup_story_items(items)
        s = round(sum(float(x.get("impact") or 0.0) for x in deduped) / len(deduped), 6)
    elif arm == "materiality_dedup_source_mean":
        deduped = dedup_story_items(items)
        weights = [
            max(0.05, float(x.get("material_p") if x.get("material_p") is not None else 0.5))
            * source_tier_weight(str(x.get("source") or ""))
            for x in deduped
        ]
        total_w = sum(weights)
        s = round(sum(w * float(x.get("impact") or 0.0) for w, x in zip(weights, deduped)) / total_w, 6)
    else:
        raise ValueError(f"Unknown Track 2 arm: {arm!r}")
    call = "UP" if s >= 0.4 else "DOWN" if s <= -0.4 else "ABSTAIN"
    return s, call


ARMS = (
    "sum_v1",
    "mean_v1",
    "materiality_weighted_mean",
    "dedup_mean",
    "materiality_dedup_source_mean",
)


def paired_auc_delta_ci(
    rows_a: Sequence[Mapping[str, Any]],
    rows_b: Sequence[Mapping[str, Any]],
    *,
    draws: int = 2000,
    seed: int = 0,
) -> Optional[List[float]]:
    """Origin-cluster bootstrap 95% CI for AUC(candidate) - AUC(baseline)."""
    by_origin: Dict[str, List[Tuple[float, float, bool]]] = {}
    for ra, rb in zip(rows_a, rows_b):
        if ra["return"] == 0:
            continue
        by_origin.setdefault(ra["origin"], []).append((float(ra["score"]), float(rb["score"]), ra["return"] > 0))
    groups = list(by_origin.values())
    if not groups:
        return None
    rng = random.Random(seed)
    deltas: List[float] = []
    for _ in range(draws):
        sample = [row for _ in groups for row in rng.choice(groups)]
        auc_a = dl.auc([(x[0], x[2]) for x in sample])
        auc_b = dl.auc([(x[1], x[2]) for x in sample])
        if auc_a is not None and auc_b is not None:
            deltas.append(round(auc_a - auc_b, 4))
    if not deltas:
        return None
    deltas.sort()
    return [deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas)) - 1]]


def evaluate_arm(
    cases: Sequence[Mapping[str, Any]],
    arm: str,
    *,
    baseline_rows: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    scored_rows: List[Dict[str, Any]] = []
    for case in cases:
        score, call = aggregate_case_score(case["items"], arm)
        scored_rows.append({
            "id": case["id"],
            "symbol": case["symbol"],
            "origin": case["origin"],
            "score": score,
            "label": call,
            "return": case["return"],
            "has_news": bool(case["items"]),
        })
    nonzero = [r for r in scored_rows if r["return"] != 0]
    origins = sorted({r["origin"] for r in nonzero})
    midpoint = max(1, len(origins) // 2)
    early_set = set(origins[:midpoint])

    auc_all = dl.auc([(r["score"], r["return"] > 0) for r in nonzero])
    auc_early = dl.auc([(r["score"], r["return"] > 0) for r in nonzero if r["origin"] in early_set])
    auc_late = dl.auc([(r["score"], r["return"] > 0) for r in nonzero if r["origin"] not in early_set])
    news_only = [r for r in nonzero if r["has_news"]]
    auc_news = dl.auc([(r["score"], r["return"] > 0) for r in news_only])

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in nonzero:
        groups.setdefault(r["origin"], []).append(r)
    rng, vals, boot = random.Random(0), list(groups.values()), []
    for _ in range(2000):
        sample = [r for _ in vals for r in rng.choice(vals)]
        a = dl.auc([(r["score"], r["return"] > 0) for r in sample])
        if a is not None:
            boot.append(a)
    boot.sort()
    ci95 = [boot[int(0.025 * len(boot))], boot[int(0.975 * len(boot)) - 1]] if boot else None

    metrics_all = R.arm_metrics(scored_rows, "system") if scored_rows else {"balanced_accuracy": None, "coverage": 0.0, "labels": {}}
    covered = [r for r in scored_rows if r["label"] in ("UP", "DOWN")]
    cov_pos = [r for r in covered if r["return"] > 0]
    cov_neg = [r for r in covered if r["return"] < 0]
    bal_cov = None
    if cov_pos and cov_neg:
        tpr = sum(r["label"] == "UP" for r in cov_pos) / len(cov_pos)
        tnr = sum(r["label"] == "DOWN" for r in cov_neg) / len(cov_neg)
        bal_cov = round((tpr + tnr) / 2.0, 4)

    delta_ci = paired_auc_delta_ci(scored_rows, baseline_rows) if baseline_rows is not None else None
    delta_auc = round(auc_all - dl.auc([(r["score"], r["return"] > 0) for r in baseline_rows if r["return"] != 0]), 4) if (baseline_rows is not None and auc_all is not None) else 0.0

    return {
        "arm": arm,
        "cases": len(scored_rows),
        "news_cases": len(news_only),
        "auc": auc_all,
        "early_auc": auc_early,
        "late_auc": auc_late,
        "news_only_auc": auc_news,
        "origin_cluster_bootstrap_95": ci95,
        "delta_auc_vs_sum_v1": delta_auc,
        "paired_delta_auc_95": delta_ci,
        "balanced_accuracy_all": metrics_all["balanced_accuracy"],
        "balanced_accuracy_covered": bal_cov,
        "coverage": metrics_all["coverage"],
        "labels": metrics_all["labels"],
        "_rows": scored_rows,
    }


def _score_materiality_gliner(items: Sequence[Tuple[str, str]], cache_dir: Path) -> Dict[str, float]:
    from core.gliner_onnx import load_standalone_onnx

    model = load_standalone_onnx(cache_dir, threads=6)
    if model is None:
        raise RuntimeError(f"Standalone GLiNER ONNX cache missing in {cache_dir}")
    clf = GlinerDecideClassifier(model, include_urgency=False)
    obj_items = [Item(sym, "cache", f"cache://{i}", title) for i, (sym, title) in enumerate(items)]
    judgments = clf.judge_batch(obj_items, batch_size=6)
    out: Dict[str, float] = {}
    for (sym, title), j in zip(items, judgments):
        key = f"{sym}|{title}"
        out[key] = float(j.material) if not isinstance(j, Exception) else 0.5
    return out


def _source_lookup(corpus_dirs: Sequence[Path]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for d in corpus_dirs:
        cand = d / "candidates.jsonl"
        if not cand.is_file():
            continue
        for line in cand.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            title = str(row.get("title") or "").strip()
            ticker = str(row.get("ticker") or "").upper()
            src = str(row.get("source") or "").strip()
            if title and ticker and src:
                mapping.setdefault(f"{ticker}|{title}", src)
    return mapping


def _build_dev_cases(root: Path, mat_scores: Mapping[str, float], sources: Mapping[str, str]) -> List[Dict[str, Any]]:
    replay_dir = root / "replay_20261116"
    rows, _ = dl.load_source(replay_dir, "v2")
    preds_path = root / "decision_screen_20261117_headline_atomic" / "predictions.jsonl"
    judged = {json.loads(line)["id"]: json.loads(line) for line in preds_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    series, _, _ = R.load_market(replay_dir)
    cases: List[Dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: (r["symbol"], r["origin"])):
        bars = {b["date"]: b for b in series[row["symbol"]]}
        enter, leave = bars.get(row["entry_date"]), bars.get(row["exit_date"])
        if enter is None or leave is None:
            continue
        items = []
        for n in row.get("news_served") or []:
            key = f"{row['symbol']}|{n['title']}"
            j = judged.get(key) or {}
            items.append({
                "title": n["title"],
                "impact": float(j.get("impact") or 0.0),
                "material_p": mat_scores.get(key, 0.5),
                "source": n.get("source") or sources.get(key, ""),
            })
        cases.append({
            "id": row["id"],
            "symbol": row["symbol"],
            "origin": row["origin"],
            "return": leave["close"] / enter["open"] - 1,
            "items": items,
        })
    return cases


def _build_window_cases(root: Path, mat_scores: Mapping[str, float], sources: Mapping[str, str]) -> List[Dict[str, Any]]:
    win_dir = root / "decision_window_20261109_headline_atomic"
    calls = [json.loads(line) for line in (win_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    judged = {json.loads(line)["id"]: json.loads(line) for line in (win_dir / "judgments.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    report = json.loads((win_dir / "report.json").read_text(encoding="utf-8"))
    excluded = set(report.get("missing_outcomes") or [])
    series, _, _ = R.load_market(root / "prices_20261119")
    spy = [b["date"] for b in series["SPY"]]
    cases: List[Dict[str, Any]] = []
    for row in calls:
        if row["id"] in excluded:
            continue
        i = spy.index(row["origin"]) if row["origin"] in spy else None
        bars = {b["date"]: b for b in series[row["symbol"]]}
        if i is None or i + R.HORIZON >= len(spy):
            continue
        enter_d, exit_d = spy[i + 1], spy[i + R.HORIZON]
        enter, leave = bars.get(enter_d), bars.get(exit_d)
        if enter is None or leave is None:
            continue
        items = []
        for n in row.get("news_served") or []:
            key = f"{row['symbol']}|{n['title']}"
            j = judged.get(key) or {}
            items.append({
                "title": n["title"],
                "impact": float(j.get("impact") or 0.0),
                "material_p": mat_scores.get(key, 0.5),
                "source": n.get("source") or sources.get(key, ""),
            })
        cases.append({
            "id": row["id"],
            "symbol": row["symbol"],
            "origin": row["origin"],
            "return": leave["close"] / enter["open"] - 1,
            "items": items,
        })
    return cases


def run_screen(
    root: Path,
    *,
    scorer: Optional[Callable[[Sequence[Tuple[str, str]], Path], Dict[str, float]]] = None,
) -> Dict[str, Any]:
    root = Path(root)
    sources = _source_lookup(sorted(root.glob("news_corpus_*")))
    dev_preds = [
        json.loads(line)
        for line in (root / "decision_screen_20261117_headline_atomic" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    win_preds = [
        json.loads(line)
        for line in (root / "decision_window_20261109_headline_atomic" / "judgments.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    unique_pairs: Dict[str, Tuple[str, str]] = {}
    for p in dev_preds + win_preds:
        sym, title = p["id"].split("|", 1)
        unique_pairs.setdefault(p["id"], (sym, title))

    score_fn = scorer or _score_materiality_gliner
    mat_scores = score_fn(list(unique_pairs.values()), root / "local_models")

    dev_cases = _build_dev_cases(root, mat_scores, sources)
    win_cases = _build_window_cases(root, mat_scores, sources)

    def eval_split(cases: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        base = evaluate_arm(cases, "sum_v1")
        base_rows = base.pop("_rows")
        arms_out = {"sum_v1": base}
        for arm in ARMS[1:]:
            res = evaluate_arm(cases, arm, baseline_rows=base_rows)
            res.pop("_rows", None)
            arms_out[arm] = res
        return arms_out

    dev_results = eval_split(dev_cases)
    win_results = eval_split(win_cases)

    protocol_payload = {
        "arms": list(ARMS),
        "jaccard_threshold": JACCARD_THRESHOLD,
        "source_weights": {"issuer": 0.5, "hackernews": 0.75, "other": 1.0},
        "call_lines": {"sum_v1": 0.5, "mean_arms": 0.4},
        "urgency_head_retired": True,
        "gliner_materiality_only": True,
    }
    protocol_sha = hashlib.sha256(json.dumps(protocol_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    report: Dict[str, Any] = {
        "schema_version": 1,
        "warning": WARNING,
        "protocol_sha256": protocol_sha,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline": "sum_v1 (current headline-v1 sum over AtomicChat headline impacts)",
        "gliner_headlines_scored": len(mat_scores),
        "error_and_abstention_accounting": {
            "dev_judgments_failed": sum(not p.get("ok") for p in dev_preds),
            "window_judgments_failed": sum(not p.get("ok") for p in win_preds),
            "window_missing_outcomes_not_reopened": 16,
            "note": "Abstentions stay counted in coverage and balanced_accuracy_all; no missing exits were reopened.",
        },
        "splits": {
            "dev_72": {"cases": len(dev_cases), "arms": dev_results},
            "window_24_retrospective_only": {"cases": len(win_cases), "arms": win_results},
        },
        "adoption_decision": "NO_LIVE_SWITCH_WITHOUT_FRESH_UNSEEN_WINDOW",
        "limits": [
            "Dev 72-case outcomes were already known; the 24 scored cases of the 11/09 window were previously looked at on 2026-11-19 and are retrospective diagnostic context only, not a fresh blind holdout.",
            "Headline titles only (no full article bodies); small sample sizes (9 dev origins, 3 scored window origins) yield wide origin-cluster confidence intervals.",
            "No live headline-v1 formula was switched by this offline screen; adoption requires a fresh unseen validation rule approved by the user.",
        ],
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    report["sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def main() -> None:
    root = Path("data")
    out_json = Path("docs/benchmarks/signal-screen-v1.json")
    report = run_screen(root)
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for split_name, split in report["splits"].items():
        print(f"=== {split_name} (n={split['cases']}) ===")
        for arm, m in split["arms"].items():
            print(
                f"  {arm:30s} AUC={m['auc']:.4f} (early={m['early_auc']}, late={m['late_auc']}, news={m['news_only_auc']}) "
                f"dAUC={m['delta_auc_vs_sum_v1']:+.4f} CI95={m['paired_delta_auc_95']} "
                f"bal_all={m['balanced_accuracy_all']} bal_cov={m['balanced_accuracy_covered']} cov={m['coverage']}"
            )
    print(f"Saved {out_json} (sha256={report['sha256'][:16]})")


if __name__ == "__main__":
    main()
