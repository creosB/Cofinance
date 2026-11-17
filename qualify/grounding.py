"""Grounding check on a sealed replay: does every number the system shows trace to a tool value or a served headline?

EXPERIMENTAL. Reuses the sealed team outputs and the same point-in-time tool values the team saw (ReplayMarket at T,
the headlines it was served). Uses the live citation matcher, so it measures the system against its own rule: no
number reaches the user unless a tool produced it. A value match is not a meaning check: a number that matches the
wrong field still counts as traced.

python -m qualify.grounding --root data/replay_20261116 --variant v2
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from core.citations import cite
from core.numeric_guard import number_spans

from . import decision_lab as D
from . import replay as R

FIELDS = ('answer', 'data_summary', 'news_summary')


def traced(text, snap, symbol, served):
    """(numbers, untraced contexts) for one text against one snapshot and the served headlines."""
    spans = number_spans(text or '')
    cited = {(c['start'], c['end']) for c in cite(text or '', {symbol: snap}, [{'title': n['title']} for n in served])}
    return len(spans), [text[max(0, s - 30):e + 15].replace('\n', ' ') for s, e, _ in spans if (s, e) not in cited]


def check(root, variant):
    rows, sha = D.load_source(root, variant)
    series, names, fng = R.load_market(root)
    fields = {f: {'texts': 0, 'numbers': 0, 'untraced': 0, 'examples': []} for f in FIELDS}
    unavailable = Counter()
    for r in rows:
        snap = R.ReplayMarket(series, names, fng, r['origin']).get_snapshot(r['symbol'])
        if snap.get('fundamentals_status'):
            unavailable['fundamentals'] += 1
        if snap.get('status') != 'available':
            unavailable['price'] += 1
        for f in FIELDS:
            if r.get(f) is None:
                continue
            n, missing = traced(r[f], snap, r['symbol'], r.get('news_served') or [])
            out = fields[f]
            out['texts'] += 1
            out['numbers'] += n
            out['untraced'] += len(missing)
            out['examples'] += missing[:max(0, 10 - len(out['examples']))]
    return {'warning': 'Experimental grounding check on sealed dev replay outputs. Not a trading signal or advice.',
            'variant': variant, 'predictions_sha256': sha, 'cases': len(rows), 'fields': fields,
            'guards': {'answer_accepted': sum(bool(r.get('answer_faithful')) for r in rows),
                       'answer_fallback': sum(not r.get('answer_faithful') for r in rows),
                       'reasons': dict(Counter(str(r.get('guard_reason')) for r in rows)),
                       'data_summary_accepted': sum(bool(r.get('data_faithful')) for r in rows),
                       'news_summary_accepted': sum(bool(r.get('news_faithful')) for r in rows)},
            'tool_data_unavailable': dict(unavailable),
            'limits': ['Value matching, not meaning: a number matching the wrong field counts as traced',
                       'Same matcher the guards use, so this checks the rule holds end to end, not an independent judge',
                       'Dev replay window, 72 correlated cases']}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--variant', default='v2', choices=sorted(R.VARIANTS))
    a = ap.parse_args()
    print(json.dumps(check(a.root, a.variant), indent=2))


if __name__ == '__main__':
    main()
