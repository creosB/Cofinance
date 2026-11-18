"""Deep think dev screen v1. EXPERIMENTAL: dev data only, not a held-out result or a forecast. Cofinance never trades.

Does LFM3's thinking mode earn its cost? Two cheap stages on evidence that already exists, with the default
narrator, the same runtime build and the same backend as its thinking-off qualification:

  A. Narrator smoke: the frozen qualification cases and gates (read only), thinking on. The thinking-off side is the
     existing qualification report on the same fingerprint, pinned in the protocol.
  B. Headline judgments: the 40 unique headlines of the sealed 11/17 AtomicChat dev screen, thinking on, scored by
     the same Python aggregation and ranking as that thinking-off screen.

The rules below were written after a plumbing probe on made-up inputs only (one narration: 421 tokens, 8.3 s with
thinking versus 35 and 0.4 s without; a thought-through headline label came out near certain), and before any
thinking output on these inputs. One run per root; a changed rule needs a new root.

    python -m qualify.think_lab freeze --root data/think_lab_v1 --publish docs/benchmarks/think-lab-protocol-v1.json
    python -m qualify.think_lab run    --root data/think_lab_v1 --publish docs/benchmarks/think-lab-v1.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from core.local_models import DEFAULT_NARRATOR, narrator_spec, runtime_fingerprint
from . import decision_lab as D
from .historical import digest, dump
from .run import check_gates, load_cases, run_cases

FORMAT = 'cofinance-think-lab/1'
WARNING = ('Experimental dev screen of thinking mode on the local narrator. Known dev outcomes, small n. '
           'Not a forecast, trading signal or financial advice.')
BACKEND = 'vulkan'
BUDGET = 384                 # thinking tokens; the answer keeps its own qualified budget on top
NARRATION_TOKENS = 256       # the qualification's answer budget
HEADLINE_TOKENS = 24         # the headline screen's answer budget
HEADLINE_TIMEOUT = 120
CATEGORIES = ('narration', 'number_faithfulness')
REPLAY_ROOT = Path('data/replay_20261116')
SOURCE = 'v2'
CANDIDATE = 'headline-v1'
OFF_SCREEN = Path('data/decision_screen_20261117_headline_atomic')
CASES = Path('qualify/cases.jsonl')
GATES = Path('qualify/gates_narrator.json')
NEAR_CERTAIN = 0.99
CHAT_RULE = ('Offer an opt-in Deep think switch for chat answers only if the thinking arm passes every frozen narrator '
             'smoke gate. Default stays off. The switch never changes headline judgments, Polymarket records or any '
             'sealed benchmark. Cost (median seconds and completion tokens, thinking on versus the pinned thinking-off '
             'qualification) is reported, not gated: Baris decides whether it is worth it.')
HEADLINE_RULE = ('Thinking for headline judgments becomes a candidate for the next unseen window only if, on the same 72 '
                 'dev cases and Python aggregation, at most 2 judgments fail and the case-score AUC is above 0.55, above '
                 'the thinking-off AUC, and above 0.50 in both halves. Otherwise headline judging stays thinking-off. '
                 'Nothing is adopted from dev results.')


REPO = Path(__file__).resolve().parents[1]


def qualified_fingerprint(backend, model):
    """LocalModels.fingerprint without an instance: the runtime descriptor plus the frozen qualification files."""
    h = hashlib.sha256(runtime_fingerprint(backend, model).encode())
    for name in ('qualify/cases.jsonl', 'qualify/gates_narrator.json', 'qualify/scorers.py'):
        h.update((REPO / name).read_bytes())
    return h.hexdigest()


def off_qualification(model_root, model=DEFAULT_NARRATOR, backend=BACKEND):
    """The thinking-off smoke report on the same model, runtime and flags, or an error naming what is missing."""
    path = Path(model_root) / 'qualification.json'
    reports = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    report = reports.get(f'{model}:{backend}')
    if not report or not report.get('passed'):
        raise ValueError(f'No passed thinking-off qualification for {model} on {backend}; qualify it first')
    if report.get('fingerprint') != qualified_fingerprint(backend, model):
        raise ValueError('The thinking-off qualification is for a different model, runtime or flag set')
    return report


def protocol(narrator_id, narrator_sha, off_report, off_plan_sha, off_seal_sha):
    return {'format': FORMAT, 'warning': WARNING, 'narrator': {'id': narrator_id, 'model_sha256': narrator_sha},
            'backend': BACKEND,
            'thinking': {'server_flags': ['--reasoning', 'on', '--reasoning-budget', str(BUDGET),
                                          '--reasoning-format', 'deepseek'],
                         'request': {'chat_template_kwargs': {'enable_thinking': True}}, 'budget': BUDGET},
            'stage_a': {'cases_sha256': digest(CASES.read_bytes()), 'gates_sha256': digest(GATES.read_bytes()),
                        'categories': list(CATEGORIES), 'max_tokens': NARRATION_TOKENS + BUDGET,
                        'off_qualification': {'fingerprint': off_report['fingerprint'],
                                              'evaluated_at': off_report['evaluated_at']}},
            'stage_b': {'replay_root': REPLAY_ROOT.as_posix(), 'source': SOURCE, 'candidate': CANDIDATE,
                        'off_screen': OFF_SCREEN.as_posix(), 'off_plan_sha256': off_plan_sha,
                        'off_seal_sha256': off_seal_sha, 'max_tokens': HEADLINE_TOKENS + BUDGET,
                        'timeout_s': HEADLINE_TIMEOUT, 'near_certain': NEAR_CERTAIN,
                        'note': 'The screen plan records the answer budget (24); thinking adds the budget on top.'},
            'rules': {'chat': CHAT_RULE, 'headlines': HEADLINE_RULE},
            'scope': 'Dev evidence only. Passing selects candidates; it adopts nothing for forecasts.'}


def current_protocol(model_root):
    spec = narrator_spec(DEFAULT_NARRATOR)
    off = off_qualification(model_root)
    return protocol(DEFAULT_NARRATOR, spec['sha'], off, digest((OFF_SCREEN / 'plan.json').read_bytes()),
                    digest((OFF_SCREEN / 'seal.json').read_bytes()))


def freeze(root, p, publish=None):
    """Write the protocol once; later calls must match it exactly."""
    root = Path(root)
    path = root / 'protocol.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != p:
            raise ValueError('Protocol changed after freezing; a changed rule needs a new --root')
    else:
        root.mkdir(parents=True, exist_ok=True)
        dump(path, p)
    if publish:
        Path(publish).write_bytes(path.read_bytes())
    return digest(path.read_bytes())


def budget_call(call, budget=BUDGET, timeout=HEADLINE_TIMEOUT):
    """The screen's call with the thinking budget added on top of each answer budget."""
    def wrapped(system, prompt, **kw):
        kw['max_tokens'] = int(kw.get('max_tokens', 16)) + budget
        kw['timeout'] = max(float(kw.get('timeout', 30)), timeout)
        return call(system, prompt, **kw)
    return wrapped


def smoke_cost(report):
    rows = [r for r in report.get('rows', []) if r.get('category') in CATEGORIES]
    ms = [r['ms'] for r in rows if r.get('ms') is not None]
    toks = [r['completion_tokens'] for r in rows if isinstance(r.get('completion_tokens'), int)]
    return {'calls': len(rows), 'median_seconds': round(statistics.median(ms) / 1000, 3) if ms else None,
            'median_completion_tokens': statistics.median(toks) if toks else None}


def near_certain(predictions, line=NEAR_CERTAIN):
    """Share of successful judgments whose most likely label carries at least `line` of the label mass."""
    tops = []
    for p in predictions:
        lps = p.get('logprobs') or {}
        mass = [math.exp(v) for v in lps.values() if isinstance(v, (int, float))]
        if p.get('ok') and mass:
            tops.append(max(mass) / sum(mass))
    return {'judged': len(tops), 'near_certain': sum(t >= line for t in tops),
            'share': round(sum(t >= line for t in tops) / len(tops), 4) if tops else None}


def chat_decision(gates):
    return bool(gates) and all(g['passed'] for g in gates.values())


def headline_decision(think, off_auc):
    rk = think['ranking']
    auc = rk.get('auc')
    return bool(think['failures'] <= 2 and auc is not None and off_auc is not None and auc > .55 and auc > off_auc
                and all(rk.get(h) is not None and rk[h] > .5 for h in ('early', 'late')))


def already_run(root):
    root = Path(root)
    return any((root / n).exists() for n in ('smoke.json', 'headlines', 'report.json'))


def run(root, *, narrator, model_root=Path('data/local_models'), publish=None):
    """One look. `narrator` is qualify.history_runtime.narrator (or a test double with the same signature)."""
    root = Path(root)
    p = current_protocol(model_root)
    path = root / 'protocol.json'
    if not path.exists() or json.loads(path.read_text(encoding='utf-8')) != p:
        raise ValueError('Freeze this exact protocol first; a changed one needs a new --root')
    if already_run(root):
        raise ValueError('Already run: one look per protocol')
    off_plan, off_predictions, _, _ = D.verified(REPLAY_ROOT, OFF_SCREEN)
    off_report = json.loads((OFF_SCREEN / 'report.json').read_text(encoding='utf-8'))
    cases = [c for c in load_cases(str(CASES)) if c['category'] in CATEGORIES]
    gates = json.loads(GATES.read_text(encoding='utf-8'))
    started = datetime.now(timezone.utc).isoformat()
    with narrator(Path(model_root), root / 'model-runtime.log', alias='lfm3-think-lab', backend=BACKEND,
                  model_id=DEFAULT_NARRATOR, thinking_budget=BUDGET) as (call, meta):
        if meta.get('model_sha256') != p['narrator']['model_sha256'] or (meta.get('thinking') or {}).get('budget') != BUDGET:
            raise ValueError('Runtime is not the frozen narrator with the frozen thinking budget')
        smoke = run_cases(cases, call.post, 'lfm3-think-lab', max_tokens=NARRATION_TOKENS + BUDGET,
                          extra={'chat_template_kwargs': {'enable_thinking': True}})
        smoke['gates'] = check_gates(smoke, gates)
        smoke['model'] = meta
        dump(root / 'smoke.json', smoke)
        D.run_headlines(REPLAY_ROOT, root / 'headlines', SOURCE, CANDIDATE, budget_call(call), meta)
    think = D.score(REPLAY_ROOT, root / 'headlines')
    think_predictions = [json.loads(l) for l in (root / 'headlines' / 'predictions.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    if [r['id'] for r in think_predictions] != off_plan['ids']:
        raise ValueError('Thinking arm judged different headlines than the sealed thinking-off screen')
    off_q = off_qualification(model_root)
    off_auc = off_report['ranking']['auc']
    report = {'format': FORMAT, 'warning': WARNING, 'protocol_sha256': digest(path.read_bytes()),
              'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
              'narrator': p['narrator'], 'backend': BACKEND, 'budget': BUDGET,
              'smoke': {'gates': smoke['gates'], 'think_cost': smoke_cost(smoke), 'off_cost': smoke_cost(off_q),
                        'incomplete': sum(r.get('reason') == 'incomplete_or_no_output' for r in smoke['rows'])},
              'headlines': {'think': {k: think['ranking'][k] for k in ('auc', 'early', 'late', 'origin_cluster_bootstrap_95')},
                            'off': {k: off_report['ranking'][k] for k in ('auc', 'early', 'late', 'origin_cluster_bootstrap_95')},
                            'think_failures': think['failures'], 'off_failures': off_report['failures'],
                            'think_labels': think['headline_labels'], 'off_labels': off_report['headline_labels'],
                            'think_median_seconds': think['median_decision_seconds'],
                            'off_median_seconds': off_report['median_decision_seconds'],
                            'think_near_certain': near_certain(think_predictions),
                            'off_near_certain': near_certain(off_predictions)},
              'decisions': {'chat_deep_think_switch': chat_decision(smoke['gates']),
                            'headline_thinking_candidate': headline_decision(think, off_auc)},
              'rules': p['rules'],
              'limits': ['Dev evidence with known outcomes; 72 cases at 9 overlapping origins, 40 unique headlines',
                         'Smoke suite is small; it checks number faithfulness and length, not answer quality',
                         'One thinking budget (384 tokens) tested; a different budget is a new root',
                         'Thinking-off sides are existing reports on the same fingerprint, not a fresh rerun']}
    dump(root / 'report.json', report)
    if publish:
        Path(publish).write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['freeze', 'run'])
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--model-root', type=Path, default=Path('data/local_models'))
    ap.add_argument('--publish', type=Path)
    a = ap.parse_args()
    if a.action == 'freeze':
        print(freeze(a.root, current_protocol(a.model_root), a.publish))
        return
    from .history_runtime import narrator
    report = run(a.root, narrator=narrator, model_root=a.model_root, publish=a.publish)
    print(json.dumps({k: report[k] for k in ('smoke', 'headlines', 'decisions')}, indent=2))


if __name__ == '__main__':
    main()
