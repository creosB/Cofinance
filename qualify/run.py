"""Qualify a local model on Cofinance's narrow jobs through any OpenAI-compatible
endpoint (llama.cpp `llama-server`, LM Studio, Ollama, vLLM). Stdlib only.

  python -m qualify.run --base-url http://127.0.0.1:8080/v1 --model minicpm5-1b \
      --cases qualify/cases.jsonl --gates qualify/gates.json --out reports/minicpm5-1b.json

Same cases + same decoding for every model arm, or the comparison means nothing.
Exit code 1 when a gate fails. Gate values are proposed starting points, not measured facts.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from .scorers import score

Call = Callable[[Dict[str, Any]], Dict[str, Any]]


def http_call(base_url: str, api_key: str = "none", timeout: float = 300.0) -> Call:
    def call(body: Dict[str, Any]) -> Dict[str, Any]:
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    return call


def load_cases(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip() and not l.startswith("#")]


def pct(values: List[float], p: float) -> Optional[float]:
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    i = (len(v) - 1) * p
    lo = int(i)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def run_cases(cases: List[Dict[str, Any]], call: Call, model: str, max_tokens: int = 512,
              extra: Optional[Dict[str, Any]] = None, repeats: int = 1,
              clock: Callable[[], float] = time.perf_counter) -> Dict[str, Any]:
    rows = []
    for rep in range(repeats):
        for c in cases:
            msgs = ([{"role": "system", "content": c["system"]}] if c.get("system") else []) \
                + [{"role": "user", "content": c["prompt"]}]
            body = {"model": model, "messages": msgs, "temperature": 0, "max_tokens": max_tokens, **(extra or {})}
            t0 = clock()
            try:
                resp = call(body)
                text = resp["choices"][0]["message"].get("content")
                finish = resp["choices"][0].get("finish_reason")
                toks = (resp.get("usage") or {}).get("completion_tokens")
                err = None
            except Exception as e:                                     # recorded, never swallowed
                text, finish, toks, err = None, None, None, f"{type(e).__name__}: {e}"
            ms = (clock() - t0) * 1000
            res = score(c, text if finish in (None, "stop") else None) if err is None \
                else {"passed": False, "reason": "request_error"}
            rows.append({"id": c["id"], "category": c["category"], "repeat": rep + 1, "ms": ms,
                         "completion_tokens": toks, "finish": finish, "error": err, **res})
    by_cat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    summary = {cat: {"n": len(v), "pass_rate": sum(r["passed"] for r in v) / len(v),
                     "p50_ms": pct([r["ms"] for r in v], .5), "p95_ms": pct([r["ms"] for r in v], .95),
                     "incomplete": sum(r["reason"] == "incomplete_or_no_output" for r in v)}
               for cat, v in by_cat.items()}
    return {"model": model, "n": len(rows), "summary": summary, "rows": rows,
            "note": "Small smoke suite, not a capability benchmark. p95 is not reliable at this n."}


def check_gates(report: Dict[str, Any], gates: Dict[str, float]) -> Dict[str, Any]:
    res = {}
    for cat, need in gates.items():
        got = report["summary"].get(cat)
        res[cat] = {"required": need, "got": None if got is None else got["pass_rate"],
                    "passed": got is not None and got["pass_rate"] >= need}
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--cases", default="qualify/cases.jsonl")
    ap.add_argument("--gates", default="qualify/gates.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--extra-json", default="{}", help='merged into every request, e.g. {"chat_template_kwargs":{"enable_thinking":false}}')
    a = ap.parse_args()
    report = run_cases(load_cases(a.cases), http_call(a.base_url), a.model, a.max_tokens,
                       json.loads(a.extra_json), a.repeats)
    with open(a.gates, encoding="utf-8") as f:
        gates = json.load(f)
    report["gates"] = check_gates(report, gates)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    for cat, s in report["summary"].items():
        print(f"{cat:22s} n={s['n']:3d} pass={s['pass_rate']:.2f} p50={s['p50_ms'] and round(s['p50_ms'])}ms incomplete={s['incomplete']}")
    ok = all(g["passed"] for g in report["gates"].values())
    print("GATES:", "PASS" if ok else "FAIL", {k: v["passed"] for k, v in report["gates"].items()})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
