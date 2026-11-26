"""Per-document isolated reading screen for unnamed restrictions on LFM3 1.7B.

Applies strict cross-document isolation at the pipeline layer: each notice is sent to the
local 1.7B narrator in its own isolated call with a fixed-size per-span role schema, and
Python links restrictions only to claims inside the same notice.

Diagnostic dev screen on the existing 24-notice fixture (qualify/restriction_link_cases.json).
Because these 24 notices were previously used in restriction_link_v1, passing this screen
motivates a fresh unseen-notice validation rather than direct live integration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from qualify import restriction_link as RL

S = RL.S

FORMAT = "cofinance-isolated-restriction-link/1"
WARNING = (
    "Experimental diagnostic screen on the 24 previously authored matched notices. "
    "Not a fresh held-out evaluation, not a live change, and not financial advice."
)
ARMS = ("lexical", "select_all", "full_packet_mask", "isolated_notice_mask")
ROLES = ("none", "claim", "restriction")
CAP_ISOLATED = 160
CAP_FULL = 280

ISOLATED_SYSTEM = (
    "You read ONE notice alone. Read the question and the notice header as context only. "
    "For each numbered body span ID, assign exactly one role: "
    "'claim' if the span states a fee rule, date/deadline, or entitlement that answers the question; "
    "'restriction' if the span limits, restricts, caps, delays, or qualifies a claim in this same notice "
    "(even if it does not repeat the subject name); "
    "'none' for unrelated background, office hours, or neutral details. "
    "Return JSON mapping every body span ID in this notice to one of: none, claim, restriction."
)

FULL_PACKET_SYSTEM = (
    "Read the question and every notice header as context only. "
    "For each numbered body span ID across the packet, return role ('none', 'claim', or 'restriction') "
    "and target ('none' unless role is 'restriction', in which case target is the single claim span ID "
    "that this restriction limits). "
    "A restriction limits a claim even if it does not repeat the subject name. "
    "Assign 'none' to unrelated background, office hours, or neutral details."
)


def _sha(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def render_single_notice(question: str, notice: Mapping[str, Any], notice_spans: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        f"Question: {question}",
        f"Notice header (context only, not selectable): {notice['title']}",
        "Body spans:",
    ]
    for sp in notice_spans:
        lines.append(f"[{sp['id']}] {sp['text']}")
    return "\n".join(lines)


def schema_isolated_notice(span_ids: Sequence[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(span_ids),
        "properties": {
            sid: {"type": "string", "enum": list(ROLES)}
            for sid in span_ids
        },
    }


def schema_full_packet(span_ids: Sequence[str]) -> Dict[str, Any]:
    targets = ["none", *span_ids]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(span_ids),
        "properties": {
            sid: {
                "type": "object",
                "additionalProperties": False,
                "required": ["role", "target"],
                "properties": {
                    "role": {"type": "string", "enum": list(ROLES)},
                    "target": {"type": "string", "enum": targets},
                },
            }
            for sid in span_ids
        },
    }


def decode_isolated_notice(raw_text: str, span_ids: Sequence[str]) -> Dict[str, str]:
    obj = json.loads(raw_text, object_pairs_hook=RL._unique_pairs)
    expected = set(span_ids)
    if not isinstance(obj, dict) or set(obj) != expected:
        raise ValueError("Isolated notice keys do not match body spans")
    out: Dict[str, str] = {}
    for sid in span_ids:
        val = obj[sid]
        if val not in ROLES:
            raise ValueError(f"Invalid role {val!r} for {sid}")
        out[sid] = val
    return out


def assemble_isolated_packet(
    notice_roles: Sequence[Mapping[str, str]],
    spans_by_notice: Sequence[Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Assemble claims, restrictions, and strictly intra-notice links across isolated notices."""
    if len(notice_roles) != len(spans_by_notice):
        raise ValueError("Notice role count does not match notice count")
    claims: List[str] = []
    restrictions: List[str] = []
    links: List[List[str]] = []
    seen_ids = set()
    for roles, n_spans in zip(notice_roles, spans_by_notice):
        n_ids = [sp["id"] for sp in n_spans]
        if set(roles) != set(n_ids):
            raise ValueError("Role keys mismatch notice spans")
        local_claims: List[str] = []
        local_restrs: List[str] = []
        for sid in n_ids:
            if sid in seen_ids:
                raise ValueError(f"Duplicate span ID across notices: {sid}")
            seen_ids.add(sid)
            role = roles[sid]
            if role == "claim":
                local_claims.append(sid)
                claims.append(sid)
            elif role == "restriction":
                local_restrs.append(sid)
                restrictions.append(sid)
            elif role != "none":
                raise ValueError(f"Unknown role: {role}")
        for r_id in local_restrs:
            for c_id in local_claims:
                links.append([c_id, r_id])
    return {"claims": claims, "restrictions": restrictions, "links": links}


def decode_full_packet(raw_text: str, span_ids: Sequence[str]) -> Dict[str, Any]:
    obj = json.loads(raw_text, object_pairs_hook=RL._unique_pairs)
    expected = set(span_ids)
    if not isinstance(obj, dict) or set(obj) != expected:
        raise ValueError("Full packet keys do not match body spans")
    claims: List[str] = []
    restrictions: List[str] = []
    raw_links: List[List[str]] = []
    for sid in span_ids:
        entry = obj[sid]
        if not isinstance(entry, dict) or set(entry) != {"role", "target"}:
            raise ValueError(f"Malformed span entry for {sid}")
        role = entry["role"]
        target = entry["target"]
        if role not in ROLES:
            raise ValueError(f"Invalid role {role!r}")
        if role == "claim":
            claims.append(sid)
        elif role == "restriction":
            restrictions.append(sid)
            if target != "none":
                raw_links.append([target, sid])
    claim_set = set(claims)
    links: List[List[str]] = []
    seen = set()
    for c_id, r_id in raw_links:
        if c_id not in claim_set:
            raise ValueError(f"Restriction {r_id} linked to non-claim {c_id}")
        pair = (c_id, r_id)
        if pair not in seen:
            seen.add(pair)
            links.append([c_id, r_id])
    return {"claims": claims, "restrictions": restrictions, "links": links}


def build_protocol(fixture: Path = RL.FIXTURE) -> Dict[str, Any]:
    cases = json.loads(Path(fixture).read_text(encoding="utf-8"))
    RL.check_cases(cases)
    body = {
        "format": FORMAT,
        "warning": WARNING,
        "model": S.MODEL,
        "arms": list(ARMS),
        "n_cases": len(cases),
        "cases_sha256": _sha(cases),
        "cap_isolated": CAP_ISOLATED,
        "cap_full": CAP_FULL,
        "isolated_system": ISOLATED_SYSTEM,
        "full_packet_system": FULL_PACKET_SYSTEM,
        "gates": {
            "max_malformed": 0,
            "max_wrong_target_two_claim": 0,
            "min_exact_link_accuracy": 0.75,
            "min_restriction_recall": 0.6,
            "max_clean_any_link": 1,
        },
    }
    body["protocol_sha256"] = _sha(body)
    return body


def freeze(run_dir: Path, fixture: Path = RL.FIXTURE) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    proto_path = run_dir / "protocol.json"
    if proto_path.exists():
        raise FileExistsError(f"Protocol already frozen at {proto_path}")
    plan = build_protocol(fixture=fixture)
    proto_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return plan


def verify_protocol(run_dir: Path, fixture: Path = RL.FIXTURE) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    plan = json.loads((run_dir / "protocol.json").read_text(encoding="utf-8"))
    saved = plan.get("protocol_sha256")
    copy = {k: v for k, v in plan.items() if k != "protocol_sha256"}
    if saved != _sha(copy):
        raise ValueError("Protocol SHA-256 mismatch.")
    cases = json.loads(Path(fixture).read_text(encoding="utf-8"))
    if plan.get("cases_sha256") != _sha(cases):
        raise ValueError("Cases SHA-256 mismatch.")
    return plan


def decide(summary: Mapping[str, Any]) -> str:
    if summary["malformed"] > 0:
        return "INCONCLUSIVE_MALFORMED"
    if summary["wrong_target_hits"] > 0:
        return "REJECT_CROSS_NOTICE_LEAK"
    restr_rec = float((summary.get("restriction") or {}).get("recall") or 0.0)
    if summary["exact_link_accuracy"] >= 0.75 and restr_rec >= 0.6 and summary["clean_any_link_hits"] <= 1:
        return "CANDIDATE_FOR_FRESH_UNSEEN_TEST"
    return "REJECT_LOW_ACCURACY_OR_NOISE"


def group_spans_by_notice(case: Mapping[str, Any]) -> List[List[Dict[str, Any]]]:
    all_spans = RL.index_body_spans(case["notices"])
    by_nid: Dict[str, List[Dict[str, Any]]] = {}
    for sp in all_spans:
        by_nid.setdefault(sp["notice_id"], []).append(sp)
    return [by_nid.get(n["id"], []) for n in case["notices"]]


def run_eval(
    run_dir: Path,
    *,
    fixture: Path = RL.FIXTURE,
    post_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    plan = verify_protocol(run_dir, fixture=fixture)
    started = run_dir / "started.json"
    if started.exists():
        raise FileExistsError(f"One-look guard: {started} already exists")
    started.write_text(json.dumps({"started_at": time.time(), "protocol_sha256": plan["protocol_sha256"]}, indent=2) + "\n", encoding="utf-8")

    cases = json.loads(Path(fixture).read_text(encoding="utf-8"))
    RL.check_cases(cases)
    rng = random.Random(20261127)
    order = list(cases)
    rng.shuffle(order)

    def execute_with_poster(poster: Callable[[Dict[str, Any]], Dict[str, Any]], alias: str) -> List[Dict[str, Any]]:
        outputs: List[Dict[str, Any]] = []
        for case in order:
            spans = RL.index_body_spans(case["notices"])
            body_ids = [sp["id"] for sp in spans]
            by_notice = group_spans_by_notice(case)
            arms = list(ARMS)
            rng.shuffle(arms)
            for arm in arms:
                row: Dict[str, Any] = {
                    "case": case["id"],
                    "arm": arm,
                    "pred": None,
                    "error": None,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "seconds": 0.0,
                }
                t0 = time.monotonic()
                try:
                    if arm == "lexical":
                        row["pred"] = RL.lexical_baseline(case)
                    elif arm == "select_all":
                        row["pred"] = RL.select_all_baseline(case)
                    elif arm == "full_packet_mask":
                        body = S.T.request_body(
                            alias,
                            FULL_PACKET_SYSTEM,
                            RL.render_packet(case),
                            max_tokens=CAP_FULL,
                            response_format={
                                "type": "json_schema",
                                "json_schema": {
                                    "name": "full_packet_mask",
                                    "strict": True,
                                    "schema": schema_full_packet(body_ids),
                                },
                            },
                        )
                        resp = poster(body)
                        ch = resp["choices"][0]
                        row["input_tokens"] = int((resp.get("usage") or {}).get("prompt_tokens") or 0)
                        row["output_tokens"] = int((resp.get("usage") or {}).get("completion_tokens") or 0)
                        if ch.get("finish_reason") == "length" or row["output_tokens"] > CAP_FULL:
                            raise ValueError("Output truncated at token cap")
                        row["pred"] = decode_full_packet(ch["message"]["content"], body_ids)
                    elif arm == "isolated_notice_mask":
                        notice_roles: List[Dict[str, str]] = []
                        in_toks = 0
                        out_toks = 0
                        for notice, n_spans in zip(case["notices"], by_notice):
                            n_ids = [sp["id"] for sp in n_spans]
                            if not n_ids:
                                notice_roles.append({})
                                continue
                            body = S.T.request_body(
                                alias,
                                ISOLATED_SYSTEM,
                                render_single_notice(case["question"], notice, n_spans),
                                max_tokens=CAP_ISOLATED,
                                response_format={
                                    "type": "json_schema",
                                    "json_schema": {
                                        "name": "isolated_notice_mask",
                                        "strict": True,
                                        "schema": schema_isolated_notice(n_ids),
                                    },
                                },
                            )
                            resp = poster(body)
                            ch = resp["choices"][0]
                            in_toks += int((resp.get("usage") or {}).get("prompt_tokens") or 0)
                            out_toks += int((resp.get("usage") or {}).get("completion_tokens") or 0)
                            if ch.get("finish_reason") == "length" or int((resp.get("usage") or {}).get("completion_tokens") or 0) > CAP_ISOLATED:
                                raise ValueError(f"Output truncated on notice {notice['id']}")
                            notice_roles.append(decode_isolated_notice(ch["message"]["content"], n_ids))
                        row["input_tokens"] = in_toks
                        row["output_tokens"] = out_toks
                        row["pred"] = assemble_isolated_packet(notice_roles, by_notice)
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                row["seconds"] = round(time.monotonic() - t0, 3)
                outputs.append(row)
        return outputs

    alias = "isolated-restriction"
    if post_fn is not None:
        outputs = execute_with_poster(post_fn, alias)
    else:
        modelroot = Path("data/local_models")
        with S.T.narrator(modelroot, run_dir / "server.log", alias=alias, model_id=S.MODEL, backend=S.T.default_backend(modelroot)) as (local, meta):
            (run_dir / "runtime.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            outputs = execute_with_poster(local.post, alias)

    case_map = {c["id"]: c for c in cases}
    scored = [RL.score_row(case_map[o["case"]], o) for o in outputs]
    results_all = {arm: RL.aggregate([r for r in scored if r["arm"] == arm]) for arm in ARMS}
    results_two = {
        arm: RL.aggregate([r for r in scored if r["arm"] == arm and r["structure"] == "two_claim"])
        for arm in ARMS
    }
    results_single = {
        arm: RL.aggregate([r for r in scored if r["arm"] == arm and r["structure"] == "single"])
        for arm in ARMS
    }

    iso_by_case = {r["case"]: r for r in scored if r["arm"] == "isolated_notice_mask"}
    full_by_case = {r["case"]: r for r in scored if r["arm"] == "full_packet_mask"}
    fams = sorted({(c["domain"], c["structure"], c["family"]) for c in cases})
    fam_cases: Dict[Any, List[str]] = {f: [] for f in fams}
    for c in cases:
        fam_cases[(c["domain"], c["structure"], c["family"])].append(c["id"])

    rng_boot = random.Random(20261127)
    diffs_exact: List[float] = []
    diffs_rec: List[float] = []
    diffs_f1: List[float] = []
    for _ in range(2000):
        draw = [rng_boot.choice(fams) for _ in range(len(fams))]
        iso_rows = [iso_by_case[cid] for f in draw for cid in fam_cases[f]]
        full_rows = [full_by_case[cid] for f in draw for cid in fam_cases[f]]
        a_iso = RL.aggregate(iso_rows)
        a_full = RL.aggregate(full_rows)
        diffs_exact.append(a_iso["exact_link_accuracy"] - a_full["exact_link_accuracy"])
        diffs_rec.append(a_iso["restriction"]["recall"] - a_full["restriction"]["recall"])
        diffs_f1.append(a_iso["link"]["f1"] - a_full["link"]["f1"])

    def _ci(vals: List[float], point: float) -> Dict[str, float]:
        s = sorted(vals)
        return {
            "point": round(point, 4),
            "ci_lo": round(s[int(0.025 * (len(s) - 1))], 4),
            "ci_hi": round(s[int(0.975 * (len(s) - 1))], 4),
        }

    paired_diff = {
        "isolated_minus_full_exact_link": _ci(
            diffs_exact,
            results_all["isolated_notice_mask"]["exact_link_accuracy"] - results_all["full_packet_mask"]["exact_link_accuracy"],
        ),
        "isolated_minus_full_restriction_recall": _ci(
            diffs_rec,
            results_all["isolated_notice_mask"]["restriction"]["recall"] - results_all["full_packet_mask"]["restriction"]["recall"],
        ),
        "isolated_minus_full_link_f1": _ci(
            diffs_f1,
            results_all["isolated_notice_mask"]["link"]["f1"] - results_all["full_packet_mask"]["link"]["f1"],
        ),
    }

    decisions = {
        "isolated_notice_mask": decide(results_all["isolated_notice_mask"]),
        "full_packet_mask": decide(results_all["full_packet_mask"]),
    }
    report = {
        "format": FORMAT,
        "warning": WARNING,
        "protocol_sha256": plan["protocol_sha256"],
        "cases_sha256": plan["cases_sha256"],
        "n_cases": len(cases),
        "decisions": decisions,
        "paired": paired_diff,
        "results": {
            "all": results_all,
            "two_claim": results_two,
            "single": results_single,
        },
        "production_change": False,
        "limits": [
            "Diagnostic dev screen on the 24 previously authored notices from restriction_link_v1; not an unseen held-out test.",
            "Structural isolation forbids cross-notice links by construction; it cannot catch a genuine cross-document amendment that intentionally overrides another notice.",
            "Requires a fresh unseen multi-notice corpus before any live pipeline integration.",
        ],
    }
    (run_dir / "outputs.json").write_text(json.dumps(outputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    sys.addaudithook(RL.F.local_only)
    ap = argparse.ArgumentParser(description="Per-document isolated reading screen for unnamed restrictions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_f = sub.add_parser("freeze")
    p_f.add_argument("run_dir")
    p_r = sub.add_parser("run")
    p_r.add_argument("run_dir")
    args = ap.parse_args(argv)
    if args.cmd == "freeze":
        plan = freeze(Path(args.run_dir))
        print("protocol", plan["protocol_sha256"])
        return 0
    if args.cmd == "run":
        rep = run_eval(Path(args.run_dir))
        print(json.dumps({"decisions": rep["decisions"], "paired": rep["paired"]}, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
