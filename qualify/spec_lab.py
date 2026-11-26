"""Frozen A/B lab for zero-weight n-gram speculative decoding on LFM3 1.7B (Vulkan).

Tests whether llama.cpp's built-in --spec-type ngram-mod accelerates the pinned LFM3 1.7B
narrator in thinking-off and Deep Think modes while preserving 100% exact greedy output parity.
Never edits frozen qualification files or live server flags.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import secrets
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core.local_models import (
    DEFAULT_NARRATOR,
    THINK_BUDGET,
    narrator_spec,
    server_command,
    server_flags,
    thinking_server_flags,
)
from qualify.run import load_cases

FORMAT = "cofinance-spec-lab/1"
WARNING = (
    "Experimental one-look A/B lab of zero-weight n-gram speculative decoding on the local "
    "Vulkan LFM3 1.7B narrator. Not a trading signal or financial advice."
)
BACKEND = "vulkan"
CATEGORIES = ("narration", "number_faithfulness")
NARRATION_TOKENS = 256
CASES_PATH = Path("qualify/cases.jsonl")
REPO = Path(__file__).resolve().parents[1]

ARMS: Dict[str, List[str]] = {
    "baseline": [],
    "ngram_mod_default": ["--spec-type", "ngram-mod"],
    "ngram_mod_n8": [
        "--spec-type",
        "ngram-mod",
        "--spec-ngram-mod-n-match",
        "8",
        "--spec-ngram-mod-n-min",
        "2",
        "--spec-ngram-mod-n-max",
        "16",
    ],
}

MODES: Tuple[Tuple[str, bool, int], ...] = (
    ("standard", False, NARRATION_TOKENS),
    ("deep_think", True, NARRATION_TOKENS + THINK_BUDGET),
)

MIN_SPEEDUP_CI_LO = 1.10


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha(obj: Any) -> str:
    return _sha256_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def select_cases(cases_path: Path = REPO / CASES_PATH) -> List[Dict[str, Any]]:
    raw = load_cases(cases_path)
    return [c for c in raw if c.get("category") in CATEGORIES]


def build_protocol(cases_path: Path = REPO / CASES_PATH, narrator_id: str = DEFAULT_NARRATOR) -> Dict[str, Any]:
    spec = narrator_spec(narrator_id)
    cases = select_cases(cases_path)
    body = {
        "format": FORMAT,
        "warning": WARNING,
        "narrator_id": narrator_id,
        "narrator_sha256": spec["sha"],
        "backend": BACKEND,
        "cases_path": str(CASES_PATH),
        "cases_sha256": _sha256_bytes(Path(cases_path).read_bytes()),
        "categories": list(CATEGORIES),
        "case_ids": [c["id"] for c in cases],
        "n_cases": len(cases),
        "arms": {k: list(v) for k, v in ARMS.items()},
        "modes": [{"name": m, "thinking": t, "max_tokens": tok} for m, t, tok in MODES],
        "gates": {
            "exact_greedy_content_parity": 1.0,
            "max_errors": 0,
            "min_eval_speedup_ci_lo": MIN_SPEEDUP_CI_LO,
            "require_both_halves_faster": True,
        },
    }
    body["protocol_sha256"] = _canonical_sha(body)
    return body


def freeze(run_dir: Path, cases_path: Path = REPO / CASES_PATH) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    proto_path = run_dir / "protocol.json"
    if proto_path.exists():
        raise FileExistsError(f"Protocol already frozen at {proto_path}; use a new run folder.")
    plan = build_protocol(cases_path=cases_path)
    proto_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return plan


def verify_protocol(run_dir: Path, cases_path: Path = REPO / CASES_PATH) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    proto_path = run_dir / "protocol.json"
    plan = json.loads(proto_path.read_text(encoding="utf-8"))
    saved_sha = plan.get("protocol_sha256")
    copy = {k: v for k, v in plan.items() if k != "protocol_sha256"}
    if saved_sha != _canonical_sha(copy):
        raise ValueError("Protocol SHA-256 mismatch.")
    if plan.get("cases_sha256") != _sha256_bytes(Path(cases_path).read_bytes()):
        raise ValueError("Cases file SHA-256 mismatch.")
    return plan


def paired_ratio_bootstrap(
    base_ms: Sequence[float],
    cand_ms: Sequence[float],
    *,
    iters: int = 2000,
    seed: int = 20261127,
) -> Dict[str, float]:
    n = len(base_ms)
    if n == 0 or n != len(cand_ms):
        return {"point": 0.0, "ci_lo": 0.0, "ci_hi": 0.0}
    total_base = sum(base_ms)
    total_cand = sum(cand_ms)
    point = (total_base / total_cand) if total_cand > 0 else 0.0
    rng = random.Random(seed)
    ratios: List[float] = []
    for _ in range(iters):
        idxs = [rng.randrange(n) for _ in range(n)]
        sb = sum(base_ms[i] for i in idxs)
        sc = sum(cand_ms[i] for i in idxs)
        ratios.append((sb / sc) if sc > 0 else 0.0)
    ratios.sort()
    return {
        "point": round(point, 4),
        "ci_lo": round(ratios[int(0.025 * iters)], 4),
        "ci_hi": round(ratios[int(0.975 * iters)], 4),
    }


def summarize_mode(
    base_rows: Sequence[Mapping[str, Any]],
    cand_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    n = len(base_rows)
    base_by_id = {r["id"]: r for r in base_rows}
    cand_by_id = {r["id"]: r for r in cand_rows}
    ordered_ids = [r["id"] for r in base_rows]
    base_errs = sum(1 for r in base_rows if r.get("error"))
    cand_errs = sum(1 for r in cand_rows if r.get("error"))
    exact_content = 0
    exact_reasoning = 0
    divergent_ids: List[str] = []
    base_eval: List[float] = []
    cand_eval: List[float] = []
    base_wall: List[float] = []
    cand_wall: List[float] = []
    draft_gen = 0
    draft_acc = 0
    for cid in ordered_ids:
        b = base_by_id[cid]
        c = cand_by_id[cid]
        if not b.get("error") and not c.get("error"):
            if b.get("content") == c.get("content"):
                exact_content += 1
            else:
                divergent_ids.append(cid)
            if (b.get("reasoning_content") or "") == (c.get("reasoning_content") or ""):
                exact_reasoning += 1
            base_eval.append(float(b.get("predicted_ms") or b.get("wall_ms") or 0.0))
            cand_eval.append(float(c.get("predicted_ms") or c.get("wall_ms") or 0.0))
            base_wall.append(float(b.get("wall_ms") or 0.0))
            cand_wall.append(float(c.get("wall_ms") or 0.0))
            draft_gen += int(c.get("draft_n") or 0)
            draft_acc += int(c.get("draft_n_accepted") or 0)
        else:
            divergent_ids.append(cid)

    eval_ratio = paired_ratio_bootstrap(base_eval, cand_eval)
    wall_ratio = paired_ratio_bootstrap(base_wall, cand_wall)
    mid = len(base_eval) // 2
    half1_speedup = (
        round(sum(base_eval[:mid]) / sum(cand_eval[:mid]), 4)
        if mid > 0 and sum(cand_eval[:mid]) > 0
        else 0.0
    )
    half2_speedup = (
        round(sum(base_eval[mid:]) / sum(cand_eval[mid:]), 4)
        if mid < len(base_eval) and sum(cand_eval[mid:]) > 0
        else 0.0
    )
    return {
        "n": n,
        "baseline_errors": base_errs,
        "candidate_errors": cand_errs,
        "exact_content_matches": exact_content,
        "exact_content_rate": round(exact_content / n, 4) if n else 0.0,
        "exact_reasoning_matches": exact_reasoning,
        "divergent_ids": divergent_ids,
        "baseline_median_eval_ms": round(statistics.median(base_eval), 1) if base_eval else 0.0,
        "candidate_median_eval_ms": round(statistics.median(cand_eval), 1) if cand_eval else 0.0,
        "baseline_median_wall_ms": round(statistics.median(base_wall), 1) if base_wall else 0.0,
        "candidate_median_wall_ms": round(statistics.median(cand_wall), 1) if cand_wall else 0.0,
        "eval_speedup": eval_ratio,
        "wall_speedup": wall_ratio,
        "split_halves_eval_speedup": {"half_1": half1_speedup, "half_2": half2_speedup},
        "draft_n": draft_gen,
        "draft_n_accepted": draft_acc,
        "draft_acceptance_rate": round(draft_acc / draft_gen, 4) if draft_gen > 0 else 0.0,
    }


def decide_arm(summary: Mapping[str, Any]) -> str:
    if summary["baseline_errors"] > 0 or summary["candidate_errors"] > 0:
        return "REJECT_ERRORS"
    if summary["exact_content_matches"] != summary["n"] or summary["exact_reasoning_matches"] != summary["n"]:
        return "REJECT_PARITY_DRIFT"
    halves = summary["split_halves_eval_speedup"]
    if (
        summary["eval_speedup"]["ci_lo"] >= MIN_SPEEDUP_CI_LO
        and halves["half_1"] > 1.0
        and halves["half_2"] > 1.0
    ):
        return "CANDIDATE_FOR_QUALIFICATION"
    return "REJECT_INSUFFICIENT_GAIN"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run_server_arm(
    cases: Sequence[Mapping[str, Any]],
    *,
    extra_flags: Sequence[str],
    thinking: bool,
    max_tokens: int,
    model_root: Path = REPO / "data" / "local_models",
) -> List[Dict[str, Any]]:
    spec = narrator_spec(DEFAULT_NARRATOR)
    model_path = Path(model_root) / spec["filename"]
    exe = Path(model_root) / "b12110-vulkan" / "llama-server.exe"
    port = _free_port()
    key = secrets.token_hex(12)
    base_flags = thinking_server_flags(BACKEND) if thinking else server_flags(BACKEND)
    cmd = server_command(exe, model_path, DEFAULT_NARRATOR, port, key, BACKEND, flags=[*base_flags, *extra_flags])
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ready = False
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"llama-server exited early with {proc.returncode}")
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers={"Authorization": f"Bearer {key}"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.25)
        if not ready:
            raise TimeoutError("llama-server health timeout")

        warm_body = {
            "model": DEFAULT_NARRATOR,
            "temperature": 0,
            "seed": 0,
            "max_tokens": 16,
            "chat_template_kwargs": {"enable_thinking": thinking},
            "messages": [{"role": "user", "content": "Reply OK."}],
        }
        warm_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(warm_body).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(warm_req, timeout=60) as resp:
            resp.read()

        rows: List[Dict[str, Any]] = []
        for case in cases:
            body = {
                "model": DEFAULT_NARRATOR,
                "temperature": 0,
                "seed": 0,
                "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": thinking},
                "messages": [
                    {"role": "system", "content": case["system"]},
                    {"role": "user", "content": case["prompt"]},
                ],
            }
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                method="POST",
            )
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                wall_ms = round((time.perf_counter() - t0) * 1000, 1)
                msg = data["choices"][0]["message"]
                timings = data.get("timings") or {}
                usage = data.get("usage") or {}
                rows.append({
                    "id": case["id"],
                    "category": case["category"],
                    "wall_ms": wall_ms,
                    "prompt_ms": round(float(timings.get("prompt_ms") or 0.0), 1),
                    "predicted_ms": round(float(timings.get("predicted_ms") or 0.0), 1),
                    "predicted_n": int(timings.get("predicted_n") or 0),
                    "predicted_per_second": round(float(timings.get("predicted_per_second") or 0.0), 2),
                    "draft_n": int(timings.get("draft_n") or 0),
                    "draft_n_accepted": int(timings.get("draft_n_accepted") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "content": msg.get("content") or "",
                    "reasoning_content": msg.get("reasoning_content") or "",
                    "error": None,
                })
            except Exception as exc:
                rows.append({
                    "id": case["id"],
                    "category": case["category"],
                    "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "prompt_ms": 0.0,
                    "predicted_ms": 0.0,
                    "predicted_n": 0,
                    "predicted_per_second": 0.0,
                    "draft_n": 0,
                    "draft_n_accepted": 0,
                    "completion_tokens": 0,
                    "content": "",
                    "reasoning_content": "",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return rows
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def run_eval(
    run_dir: Path,
    *,
    cases_path: Path = REPO / CASES_PATH,
    runner: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    plan = verify_protocol(run_dir, cases_path=cases_path)
    started_path = run_dir / "started.json"
    if started_path.exists():
        raise FileExistsError(f"One-look guard: {started_path} already exists; create a new run directory.")
    started_path.write_text(
        json.dumps({"started_at": time.time(), "protocol_sha256": plan["protocol_sha256"]}, indent=2) + "\n",
        encoding="utf-8",
    )

    cases = select_cases(cases_path)
    run_arm = runner or _run_server_arm
    raw_outputs: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    results: Dict[str, Dict[str, Any]] = {}
    decisions: Dict[str, Dict[str, str]] = {}

    for mode_name, thinking, max_tokens in MODES:
        raw_outputs[mode_name] = {}
        results[mode_name] = {}
        decisions[mode_name] = {}
        for arm_name, extra_flags in ARMS.items():
            raw_outputs[mode_name][arm_name] = run_arm(
                cases,
                extra_flags=extra_flags,
                thinking=thinking,
                max_tokens=max_tokens,
            )
        base_rows = raw_outputs[mode_name]["baseline"]
        for arm_name in ARMS:
            if arm_name == "baseline":
                continue
            summary = summarize_mode(base_rows, raw_outputs[mode_name][arm_name])
            results[mode_name][arm_name] = summary
            decisions[mode_name][arm_name] = decide_arm(summary)

    report = {
        "format": FORMAT,
        "warning": WARNING,
        "protocol_sha256": plan["protocol_sha256"],
        "cases_sha256": plan["cases_sha256"],
        "narrator_id": plan["narrator_id"],
        "narrator_sha256": plan["narrator_sha256"],
        "backend": BACKEND,
        "n_cases": len(cases),
        "decisions": decisions,
        "results": results,
        "production_change": False,
        "limits": [
            "Measured on one local Windows PC (Radeon RX 5700 XT, llama.cpp b12110-vulkan) over the frozen narration and number_faithfulness cases.",
            "Greedy decoding only (temperature=0, seed=0); stochastic sampling and long multi-PDF contexts (>4K) were not tested.",
            "Any adopted server flag would change runtime_fingerprint and require full narrator re-qualification before live use.",
        ],
    }
    (run_dir / "outputs.json").write_text(json.dumps(raw_outputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Frozen LFM3 1.7B zero-weight n-gram speculation A/B lab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_freeze = sub.add_parser("freeze")
    p_freeze.add_argument("run_dir")
    p_run = sub.add_parser("run")
    p_run.add_argument("run_dir")
    args = ap.parse_args(argv)
    if args.cmd == "freeze":
        plan = freeze(Path(args.run_dir))
        print("protocol", plan["protocol_sha256"])
        return 0
    if args.cmd == "run":
        rep = run_eval(Path(args.run_dir))
        print(json.dumps({"decisions": rep["decisions"], "protocol_sha256": rep["protocol_sha256"]}, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
