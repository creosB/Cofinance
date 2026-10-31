"""Decision-model bake-off on labelled headlines: GLiNER-only vs 2B judge vs cascade.

  python -m qualify.decide_eval --arm gliner --out reports/gliner.json
  python -m qualify.decide_eval --arm systemone=http://127.0.0.1:8013 --out reports/jev2b.json
  python -m qualify.decide_eval --arm d1 --d1-revision <commit-sha> --out reports/d1.json
  python -m qualify.decide_eval --arm cascade:gliner+systemone=http://127.0.0.1:8013 --out reports/cascade.json
  python -m qualify.decide_eval --compare reports/gliner.json reports/cascade.json

Same rows, same GateConfig for every arm. Errors count as wrong answers (and are reported).
Threshold fitting reported here is fit ON this set: confirm it on a held-out split before trusting it.
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable, Dict, List, Optional

from core.signals import GateConfig, Item

from .run import pct


def load_rows(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip() and not l.lstrip().startswith("#")]


def _f1(tp: int, fp: int, fn: int) -> float:
    return 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)


def ece(probs: List[float], labels: List[bool], bins: int = 10) -> Optional[float]:
    if not probs:
        return None
    total, n = 0.0, len(probs)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if idx:
            conf = sum(probs[i] for i in idx) / len(idx)
            acc = sum(labels[i] for i in idx) / len(idx)
            total += len(idx) / n * abs(conf - acc)
    return total


def evaluate_classifier(clf: Any, rows: List[Dict[str, Any]], gate: Optional[GateConfig] = None,
                        clock: Callable[[], float] = time.perf_counter) -> Dict[str, Any]:
    g = gate or GateConfig()
    out, probs, labels, ms = [], [], [], []
    for r in rows:
        item = Item(r["ticker"], r.get("source", "eval"), r.get("url", f"eval://{r['id']}"), r["title"], r.get("text", ""))
        t0 = clock()
        try:
            j, err = clf.judge(item), None
        except Exception as e:
            j, err = None, f"{type(e).__name__}: {e}"
        ms.append((clock() - t0) * 1000)
        mat = bool(r["material"])
        if j is None:
            out.append({"id": r["id"], "error": err, "correct": False, "alert": False})
            continue
        pred = j.material >= g.digest_p
        alert = j.material >= g.alert_p and j.urgent >= g.alert_urgent_p
        probs.append(j.material)
        labels.append(mat)
        out.append({"id": r["id"], "material_p": j.material, "urgent_p": j.urgent, "direction": j.direction,
                    "pred_material": pred, "alert": alert, "correct": pred == mat, "trap": r.get("trap")})
    by_id = {r["id"]: r for r in rows}
    tp = sum(1 for o in out if o.get("pred_material") and by_id[o["id"]]["material"])
    fp = sum(1 for o in out if o.get("pred_material") and not by_id[o["id"]]["material"])
    fn = sum(1 for o in out if not o.get("pred_material") and by_id[o["id"]]["material"])
    tn = sum(1 for o in out if "pred_material" in o and not o["pred_material"] and not by_id[o["id"]]["material"])
    neg = [r for r in rows if not r["material"]]
    urgent_rows = [r for r in rows if r.get("urgent")]
    traps = [o for o in out if by_id[o["id"]].get("trap")]
    alerts = {o["id"] for o in out if o["alert"]}
    best = max(((round(t / 20, 2), _f1(sum(1 for p, l in zip(probs, labels) if p >= t / 20 and l),
                                       sum(1 for p, l in zip(probs, labels) if p >= t / 20 and not l),
                                       sum(1 for p, l in zip(probs, labels) if p < t / 20 and l)))
                for t in range(1, 20)), key=lambda x: x[1], default=(None, 0.0))
    return {
        "n": len(rows), "errors": sum(1 for o in out if o.get("error")),
        "accuracy": sum(o["correct"] for o in out) / len(rows) if rows else 0.0,
        "macro_f1": (_f1(tp, fp, fn) + _f1(tn, fn, fp)) / 2,
        "brier": sum((p - l) ** 2 for p, l in zip(probs, labels)) / len(probs) if probs else None,
        "ece": ece(probs, labels),
        "false_alert_rate": sum(1 for r in neg if r["id"] in alerts) / len(neg) if neg else 0.0,
        "alert_recall": sum(1 for r in urgent_rows if r["id"] in alerts) / len(urgent_rows) if urgent_rows else None,
        "trap_accuracy": sum(o["correct"] for o in traps) / len(traps) if traps else None,
        "p50_ms": pct(ms, .5), "p95_ms": pct(ms, .95),
        "best_digest_threshold_on_this_set": {"threshold": best[0], "f1": round(best[1], 4)},
        "rows": out,
    }


def adopt_adjudicator(baseline: Dict[str, Any], candidate: Dict[str, Any],
                      min_f1_gain: float = 0.05, min_false_alert_cut: float = 0.30) -> Dict[str, Any]:
    """Keep the 2B judge only if it earns its RAM: +5pp macro-F1 or >=30% fewer false alerts, no regression."""
    f1_gain = candidate["macro_f1"] - baseline["macro_f1"]
    b_fa, c_fa = baseline["false_alert_rate"], candidate["false_alert_rate"]
    fa_cut = (b_fa - c_fa) / b_fa if b_fa > 0 else 0.0
    regress = candidate["macro_f1"] < baseline["macro_f1"] - 1e-9 or c_fa > b_fa + 1e-9 \
        or candidate["errors"] > baseline["errors"]
    adopt = not regress and (f1_gain >= min_f1_gain or fa_cut >= min_false_alert_cut)
    return {"adopt": adopt, "macro_f1_gain": round(f1_gain, 4), "false_alert_cut": round(fa_cut, 4),
            "regression": regress}


def build_arm(spec: str, d1_revision: Optional[str] = None) -> Any:
    if spec.startswith("cascade:"):
        from core.cascade import CascadeClassifier
        a, b = spec[len("cascade:"):].split("+", 1)
        return CascadeClassifier(build_arm(a, d1_revision), build_arm(b, d1_revision), GateConfig())
    if spec == "gliner":
        from core.gliner_decide import GlinerDecideClassifier
        return GlinerDecideClassifier.from_pretrained()
    from core.systemone import SystemOneClient, SystemOneSignalClassifier, http_transport, inprocess_transport
    if spec.startswith("systemone="):
        return SystemOneSignalClassifier(SystemOneClient(http_transport(spec.split("=", 1)[1])))
    if spec == "d1":
        if not d1_revision:
            raise SystemExit("d1 loads remote code: pass --d1-revision <commit sha> to pin it")
        import torch
        from transformers import AutoModel
        dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        m = AutoModel.from_pretrained("LiquidAI/d1-2B", trust_remote_code=True, revision=d1_revision,
                                      dtype=torch.float32 if dev == "cpu" else torch.bfloat16).to(dev)
        return SystemOneSignalClassifier(SystemOneClient(inprocess_transport(m)))
    raise SystemExit(f"unknown arm {spec!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm")
    ap.add_argument("--data", default="qualify/headlines.jsonl")
    ap.add_argument("--out", default="")
    ap.add_argument("--d1-revision", default=None)
    ap.add_argument("--compare", nargs=2, metavar=("BASELINE", "CANDIDATE"))
    a = ap.parse_args()
    if a.compare:
        reports = []
        for p in a.compare:
            with open(p, encoding="utf-8") as f:
                reports.append(json.load(f))
        print(json.dumps(adopt_adjudicator(*reports), indent=2))
        return 0
    clf = build_arm(a.arm, a.d1_revision)
    rep = evaluate_classifier(clf, load_rows(a.data))
    rep["arm"] = a.arm
    if hasattr(clf, "routes"):
        rep["cascade_routes"] = dict(clf.routes)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)
    keys = ("n", "errors", "accuracy", "macro_f1", "brier", "ece", "false_alert_rate", "alert_recall", "trap_accuracy", "p50_ms", "p95_ms")
    print({k: (round(rep[k], 4) if isinstance(rep[k], float) else rep[k]) for k in keys})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
