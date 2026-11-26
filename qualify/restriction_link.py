"""Full-packet claim, unnamed-restriction, and link benchmark. Research only; no verdict or evidence filtering."""
import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

from qualify import source_selection as F

P, S = F.P, F.S
FIXTURE = Path('qualify/restriction_link_cases.json')
ARMS = ('lexical', 'direct', 'two_stage', 'select_all')
DOMAINS = ('fees', 'dates', 'entitlements')
CAP_DIRECT = 256
CAP_STAGE1 = 128
CAP_STAGE2 = 256

DIRECT_SYSTEM = (
    'You read complete notices without filtering or issuing a verdict. '
    'Notice titles are identifying context only and are never selectable IDs. '
    'Select ONLY body span IDs ([bNNN]): '
    '(1) claims: body spans stating a fee rule, date/deadline, or entitlement; '
    '(2) restrictions: body spans that narrow, limit, qualify, or condition a claim, even when the restriction does not repeat the claim subject; '
    '(3) links: exact (claim_span, restriction_span) pairs where the restriction actually limits that specific claim. '
    'Do not link a restriction from one notice or account class to a different claim in another notice. '
    'If no restriction applies, return empty restrictions and empty links. '
    'A body span cannot be both a claim and a restriction. Return strict JSON matching the schema.'
)

STAGE1_SYSTEM = (
    'You are Stage 1 of a two-stage notice reader. Examine the complete packet without issuing a verdict. '
    'Notice titles are identifying context only and are never selectable IDs. '
    'Select ONLY body span IDs ([bNNN]) that state primary claims (a fee rule, date/deadline, or entitlement). '
    'Do not select restrictions, limitations, exceptions, or neutral background spans. '
    'Return strict JSON matching the schema.'
)

STAGE2_SYSTEM = (
    'You are Stage 2 of a two-stage notice reader. Examine the complete packet and the Stage 1 claim list. '
    'Notice titles are identifying context only and are never selectable IDs. '
    'Inspect every remaining body span not listed as a Stage 1 claim. '
    'Select ONLY remaining body spans that restrict, narrow, limit, or condition one of the Stage 1 claims, even when the subject is not repeated. '
    'Link each selected restriction ONLY to the exact Stage 1 claim span it limits; never link across unrelated notices or account classes. '
    'If no restriction applies, return empty restrictions and empty links. Return strict JSON matching the schema.'
)

CLAIM_RE = re.compile(
    r'\b(may|can|eligible|entitled|qualify|qualifies|receive|receives|waive|waives|deadline|due|available|credit|access|grant|refund)\b',
    re.I,
)
RESTRICTION_RE = re.compile(
    r'\b(only|except|unless|at most|no more than|limited to|applies only|valid only|restricted to|does not apply|not eligible|cannot|expire|expires|must be (?:used|submitted|claimed) by|capped at|first \w+ each)\b',
    re.I,
)

GATE = (
    'Exactly 24 fresh authored packets across 3 domains (fees, dates, entitlements): 12 matched positive/control '
    'families (6 single-claim pairs and 6 two-claim disambiguation pairs). Every positive restriction omits the '
    'claim subject. Notice titles stay visible as read-only context outside selectable body IDs. All invalid '
    'outputs stay in denominators with zero retries or parser repair. INCONCLUSIVE if any deterministic arm is '
    'invalid or either model arm has >1 invalid row. Advance to INDEPENDENT_RETEST_ONLY only if two_stage beats '
    'direct on restriction recall by >=0.15 (paired family-bootstrap 95% CI lower bound >0), beats direct on '
    'exact-link-set accuracy by >=0.10 (95% CI lower bound >0), keeps claim F1 within 0.05 of direct, keeps '
    'restriction precision within 0.05 of direct, has 0/6 wrong-claim misattributions on positive two-claim '
    'packets, 0/12 clean-control packets with any predicted link, and 0 malformed replies in both model arms. '
    'Otherwise NO_PROMOTION. No evidence filtering, integration, or automatic verdict.'
)


def fingerprint():
    files = set(S.fingerprint()) | {
        str(Path(m.__file__).resolve().relative_to(Path.cwd()).as_posix()) for m in (F, F.C)
    } | {'qualify/restriction_link.py'}
    return {f: P.digest(Path(f).read_bytes().replace(b'\r\n', b'\n')) for f in sorted(files)}


def protocol():
    return dict(
        files=fingerprint(),
        gate=GATE,
        arms=list(ARMS),
        domains=list(DOMAINS),
        model=S.MODEL,
        caps=dict(direct=CAP_DIRECT, stage1=CAP_STAGE1, stage2=CAP_STAGE2),
        prompts=dict(direct=DIRECT_SYSTEM, stage1=STAGE1_SYSTEM, stage2=STAGE2_SYSTEM),
        lexical=dict(claim=CLAIM_RE.pattern, restriction=RESTRICTION_RE.pattern),
        seed=1729,
        interval='Paired family-cluster bootstrap; 2000 draws across 12 families, seed 1729. Descriptive only.',
    )


def index_body_spans(notices):
    spans = []
    n = 0
    for notice in notices:
        nid = notice['id']
        title = notice['title']
        for idx, text in enumerate(notice['body']):
            n += 1
            spans.append(dict(id=f'b{n:03d}', notice_id=nid, title=title, idx=idx, text=text))
    return spans


def render_packet(case, stage1_claims=None):
    lines = [f"Question: {case['question']}", '']
    n = 0
    for notice in case['notices']:
        lines.append(f"=== NOTICE CONTEXT (READ-ONLY TITLE, NOT SELECTABLE): {notice['title']} ===")
        for text in notice['body']:
            n += 1
            lines.append(f'[b{n:03d}] {text}')
        lines.append('')
    if stage1_claims is not None:
        lines.append('Stage 1 extracted claim spans (fixed): ' + (', '.join(stage1_claims) if stage1_claims else '(none)'))
    return '\n'.join(lines).strip()


def check_cases(cases):
    if len(cases) != 24 or len({c['id'] for c in cases}) != 24:
        raise ValueError('Expected 24 unique packets')
    families = {}
    domain_counts = Counter()
    structure_counts = Counter()
    for c in cases:
        if c.get('domain') not in DOMAINS or c.get('structure') not in ('single', 'two_claim'):
            raise ValueError(f"Invalid domain or structure in {c.get('id')}")
        notices = c.get('notices') or []
        if not notices or len({n['id'] for n in notices}) != len(notices):
            raise ValueError(f"Invalid notices in {c['id']}")
        if any(not n.get('title') or not n.get('body') or any(not s.strip() for s in n['body']) for n in notices):
            raise ValueError(f"Empty title or body in {c['id']}")
        if c['structure'] == 'two_claim' and len(notices) < 2:
            raise ValueError(f"Two-claim packet needs >=2 notices: {c['id']}")
        spans = index_body_spans(notices)
        lookup = {s['id']: s for s in spans}
        claims = c['gold_claims']
        restrs = c['gold_restrictions']
        links = c['gold_links']
        if len(claims) != len(set(claims)) or len(restrs) != len(set(restrs)):
            raise ValueError(f"Duplicate gold span in {c['id']}")
        if not set(claims) <= set(lookup) or not set(restrs) <= set(lookup):
            raise ValueError(f"Unknown gold span in {c['id']}")
        if set(claims) & set(restrs):
            raise ValueError(f"Gold claim and restriction overlap in {c['id']}")
        expected_claims = 1 if c['structure'] == 'single' else 2
        if len(claims) != expected_claims:
            raise ValueError(f"Expected {expected_claims} gold claims in {c['id']}")
        for lk in links:
            if len(lk) != 2 or lk[0] not in claims or lk[1] not in restrs:
                raise ValueError(f"Invalid gold link {lk} in {c['id']}")
        if len({tuple(lk) for lk in links}) != len(links):
            raise ValueError(f"Duplicate gold link in {c['id']}")
        positive = bool(restrs)
        if positive:
            if len(restrs) != 1 or len(links) != 1:
                raise ValueError(f"Positive packet must have 1 restriction and 1 link: {c['id']}")
            subj = c['subject'].casefold()
            if subj in lookup[restrs[0]]['text'].casefold():
                raise ValueError(f"Restriction repeats subject {c['subject']!r} in {c['id']}")
        else:
            if links:
                raise ValueError(f"Control packet must have empty links: {c['id']}")
        fam_key = (c['domain'], c['structure'], c['family'])
        families.setdefault(fam_key, []).append(positive)
        domain_counts[c['domain']] += 1
        structure_counts[(c['domain'], c['structure'], positive)] += 1
    if len(families) != 12 or any(sorted(v) != [False, True] for v in families.values()):
        raise ValueError('Expected 12 matched positive/control families')
    if any(domain_counts[d] != 8 for d in DOMAINS):
        raise ValueError('Expected 8 packets per domain')
    for d in DOMAINS:
        for st in ('single', 'two_claim'):
            for pos in (False, True):
                if structure_counts[(d, st, pos)] != 2:
                    raise ValueError(f'Unbalanced split for {(d, st, pos)}')


def _unique_pairs(pairs):
    if len(pairs) != len({k for k, _ in pairs}):
        raise ValueError('Duplicate JSON keys')
    return dict(pairs)


def schema_direct(body_ids):
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['claims', 'restrictions', 'links'],
        'properties': {
            'claims': {'type': 'array', 'items': {'type': 'string', 'enum': body_ids}},
            'restrictions': {'type': 'array', 'items': {'type': 'string', 'enum': body_ids}},
            'links': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': ['claim_span', 'restriction_span'],
                    'properties': {
                        'claim_span': {'type': 'string', 'enum': body_ids},
                        'restriction_span': {'type': 'string', 'enum': body_ids},
                    },
                },
            },
        },
    }


def schema_stage1(body_ids):
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['claims'],
        'properties': {
            'claims': {'type': 'array', 'items': {'type': 'string', 'enum': body_ids}},
        },
    }


def schema_stage2(remaining_ids, claim_ids):
    props = {
        'restrictions': {'type': 'array', 'items': {'type': 'string', 'enum': remaining_ids}} if remaining_ids else {'type': 'array', 'maxItems': 0},
        'links': (
            {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': ['claim_span', 'restriction_span'],
                    'properties': {
                        'claim_span': {'type': 'string', 'enum': claim_ids},
                        'restriction_span': {'type': 'string', 'enum': remaining_ids},
                    },
                },
            }
            if (claim_ids and remaining_ids)
            else {'type': 'array', 'maxItems': 0}
        ),
    }
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['restrictions', 'links'],
        'properties': props,
    }


def _validate_span_list(values, allowed, label):
    if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
        raise ValueError(f'{label} must be a list of span IDs')
    if len(values) != len(set(values)):
        raise ValueError(f'Duplicate span ID in {label}')
    if not set(values) <= allowed:
        raise ValueError(f'Unknown or non-body span ID in {label}')
    return list(values)


def _validate_links(raw_links, claim_set, restr_set):
    if not isinstance(raw_links, list):
        raise ValueError('links must be a list')
    pairs = []
    seen = set()
    for item in raw_links:
        if not isinstance(item, dict) or set(item) != {'claim_span', 'restriction_span'}:
            raise ValueError('Malformed link object')
        c_id, r_id = item['claim_span'], item['restriction_span']
        if c_id not in claim_set or r_id not in restr_set:
            raise ValueError('Link references undeclared span')
        pair = (c_id, r_id)
        if pair in seen:
            raise ValueError('Duplicate link')
        seen.add(pair)
        pairs.append([c_id, r_id])
    return pairs


def decode_direct(raw_text, body_ids):
    obj = json.loads(raw_text, object_pairs_hook=_unique_pairs)
    if not isinstance(obj, dict) or set(obj) != {'claims', 'restrictions', 'links'}:
        raise ValueError('Direct reply schema mismatch')
    allowed = set(body_ids)
    claims = _validate_span_list(obj['claims'], allowed, 'claims')
    restrs = _validate_span_list(obj['restrictions'], allowed, 'restrictions')
    if set(claims) & set(restrs):
        raise ValueError('Span selected as both claim and restriction')
    links = _validate_links(obj['links'], set(claims), set(restrs))
    return dict(claims=claims, restrictions=restrs, links=links)


def decode_stage1(raw_text, body_ids):
    obj = json.loads(raw_text, object_pairs_hook=_unique_pairs)
    if not isinstance(obj, dict) or set(obj) != {'claims'}:
        raise ValueError('Stage 1 reply schema mismatch')
    return _validate_span_list(obj['claims'], set(body_ids), 'claims')


def decode_stage2(raw_text, claim_ids, body_ids):
    obj = json.loads(raw_text, object_pairs_hook=_unique_pairs)
    if not isinstance(obj, dict) or set(obj) != {'restrictions', 'links'}:
        raise ValueError('Stage 2 reply schema mismatch')
    claim_set = set(claim_ids)
    remaining = set(body_ids) - claim_set
    restrs = _validate_span_list(obj['restrictions'], remaining, 'restrictions')
    links = _validate_links(obj['links'], claim_set, set(restrs))
    return dict(claims=list(claim_ids), restrictions=restrs, links=links)


def lexical_baseline(case):
    spans = index_body_spans(case['notices'])
    claims = []
    restrs = []
    links = []
    last_claim_by_notice = {}
    for sp in spans:
        is_restr = bool(RESTRICTION_RE.search(sp['text']))
        is_claim = bool(CLAIM_RE.search(sp['text'])) and not is_restr
        if is_claim:
            claims.append(sp['id'])
            last_claim_by_notice[sp['notice_id']] = sp['id']
        elif is_restr:
            restrs.append(sp['id'])
            target = last_claim_by_notice.get(sp['notice_id'])
            if target:
                links.append([target, sp['id']])
    return dict(claims=claims, restrictions=restrs, links=links)


def select_all_baseline(case):
    ids = [sp['id'] for sp in index_body_spans(case['notices'])]
    links = [[c, r] for c in ids for r in ids if c != r]
    return dict(claims=list(ids), restrictions=list(ids), links=links)


def _prf(tp, pred_n, gold_n):
    prec = tp / pred_n if pred_n else 0.0
    rec = tp / gold_n if gold_n else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return dict(tp=tp, pred=pred_n, gold=gold_n, precision=round(prec, 4), recall=round(rec, 4), f1=round(f1, 4))


def score_row(case, output):
    spans = index_body_spans(case['notices'])
    n_spans = len(spans)
    gold_c = set(case['gold_claims'])
    gold_r = set(case['gold_restrictions'])
    gold_l = {tuple(p) for p in case['gold_links']}
    valid = output['error'] is None and output['pred'] is not None
    if valid:
        pred_c = set(output['pred']['claims'])
        pred_r = set(output['pred']['restrictions'])
        pred_l = {tuple(p) for p in output['pred']['links']}
    else:
        pred_c, pred_r, pred_l = set(), set(), set()

    c_tp = len(pred_c & gold_c)
    r_tp = len(pred_r & gold_r)
    l_tp = len(pred_l & gold_l)
    exact_link = bool(valid and pred_l == gold_l)

    positive = bool(gold_r)
    is_two_claim_pos = case['structure'] == 'two_claim' and positive
    wrong_target = False
    gold_r_links_total = 0
    gold_r_links_wrong = 0
    if is_two_claim_pos:
        gold_r_id = next(iter(gold_r))
        gold_c_id = next(iter(gold_l))[0]
        for c_id, r_id in pred_l:
            if r_id == gold_r_id:
                gold_r_links_total += 1
                if c_id != gold_c_id:
                    gold_r_links_wrong += 1
                    wrong_target = True

    is_clean = not positive
    clean_any_link = bool(is_clean and valid and len(pred_l) > 0)
    clean_possible_pairs = n_spans * max(0, n_spans - 1) if is_clean else 0
    clean_pred_links = len(pred_l) if is_clean else 0

    return dict(
        case=case['id'],
        family=case['family'],
        domain=case['domain'],
        structure=case['structure'],
        positive=positive,
        arm=output['arm'],
        valid=valid,
        malformed=not valid,
        claim_tp=c_tp,
        claim_pred=len(pred_c),
        claim_gold=len(gold_c),
        restr_tp=r_tp,
        restr_pred=len(pred_r),
        restr_gold=len(gold_r),
        link_tp=l_tp,
        link_pred=len(pred_l),
        link_gold=len(gold_l),
        exact_link=exact_link,
        two_claim_pos=is_two_claim_pos,
        wrong_target=wrong_target,
        gold_r_links_total=gold_r_links_total,
        gold_r_links_wrong=gold_r_links_wrong,
        clean_control=is_clean,
        clean_any_link=clean_any_link,
        clean_pred_links=clean_pred_links,
        clean_possible_pairs=clean_possible_pairs,
        input_tokens=output.get('input_tokens', 0),
        output_tokens=output.get('output_tokens', 0),
        seconds=output.get('seconds', 0.0),
    )


def aggregate(rows):
    n = len(rows)
    valid_n = sum(int(r['valid']) for r in rows)
    malformed_n = sum(int(r['malformed']) for r in rows)
    two_pos = [r for r in rows if r['two_claim_pos']]
    clean = [r for r in rows if r['clean_control']]
    wrong_n = sum(int(r['wrong_target']) for r in two_pos)
    gr_tot = sum(r['gold_r_links_total'] for r in two_pos)
    gr_wrong = sum(r['gold_r_links_wrong'] for r in two_pos)
    clean_any = sum(int(r['clean_any_link']) for r in clean)
    clean_pred = sum(r['clean_pred_links'] for r in clean)
    clean_poss = sum(r['clean_possible_pairs'] for r in clean)
    return dict(
        n=n,
        valid=valid_n,
        malformed=malformed_n,
        malformed_rate=round(malformed_n / n, 4) if n else 0.0,
        claim=_prf(sum(r['claim_tp'] for r in rows), sum(r['claim_pred'] for r in rows), sum(r['claim_gold'] for r in rows)),
        restriction=_prf(sum(r['restr_tp'] for r in rows), sum(r['restr_pred'] for r in rows), sum(r['restr_gold'] for r in rows)),
        link=_prf(sum(r['link_tp'] for r in rows), sum(r['link_pred'] for r in rows), sum(r['link_gold'] for r in rows)),
        exact_link_hits=sum(int(r['exact_link']) for r in rows),
        exact_link_accuracy=round(sum(int(r['exact_link']) for r in rows) / n, 4) if n else 0.0,
        wrong_target_hits=wrong_n,
        wrong_target_total=len(two_pos),
        wrong_target_rate=round(wrong_n / len(two_pos), 4) if two_pos else 0.0,
        cond_wrong_link_rate=round(gr_wrong / gr_tot, 4) if gr_tot else 0.0,
        clean_any_link_hits=clean_any,
        clean_controls_total=len(clean),
        clean_any_link_rate=round(clean_any / len(clean), 4) if clean else 0.0,
        clean_pair_false_link_rate=round(clean_pred / clean_poss, 4) if clean_poss else 0.0,
        input_tokens=sum(r['input_tokens'] for r in rows),
        output_tokens=sum(r['output_tokens'] for r in rows),
        seconds=round(sum(r['seconds'] for r in rows), 3),
    )


def paired_bootstrap(rows, arm_b='two_stage', arm_a='direct', draws=2000, seed=1729):
    by_b = {r['case']: r for r in rows if r['arm'] == arm_b}
    by_a = {r['case']: r for r in rows if r['arm'] == arm_a}
    fam_keys = sorted({(r['domain'], r['structure'], r['family']) for r in by_a.values()})
    clusters = []
    for fk in fam_keys:
        cids = [cid for cid, r in by_a.items() if (r['domain'], r['structure'], r['family']) == fk]
        clusters.append(([by_b[cid] for cid in cids], [by_a[cid] for cid in cids]))
    rng = random.Random(seed)
    restr_rec_d = []
    exact_d = []
    link_f1_d = []
    for _ in range(draws):
        sampled = rng.choices(clusters, k=len(clusters))
        rb = [r for grp_b, _ in sampled for r in grp_b]
        ra = [r for _, grp_a in sampled for r in grp_a]
        ab = aggregate(rb)
        aa = aggregate(ra)
        restr_rec_d.append(ab['restriction']['recall'] - aa['restriction']['recall'])
        exact_d.append(ab['exact_link_accuracy'] - aa['exact_link_accuracy'])
        link_f1_d.append(ab['link']['f1'] - aa['link']['f1'])

    def ci95(vals):
        s = sorted(vals)
        lo = s[int((len(s) - 1) * 0.025)]
        hi = s[int((len(s) - 1) * 0.975)]
        return [round(lo, 4), round(hi, 4)]

    return dict(
        arm_b=arm_b,
        arm_a=arm_a,
        families=len(fam_keys),
        draws=draws,
        restriction_recall_delta_ci95=ci95(restr_rec_d),
        exact_link_accuracy_delta_ci95=ci95(exact_d),
        link_f1_delta_ci95=ci95(link_f1_d),
    )


def decide_gate(summary, paired):
    all_arms = summary['all']
    for arm in ('lexical', 'select_all'):
        if all_arms[arm]['n'] != 24 or all_arms[arm]['malformed'] > 0:
            return 'INCONCLUSIVE'
    for arm in ('direct', 'two_stage'):
        if all_arms[arm]['n'] != 24 or all_arms[arm]['malformed'] > 1:
            return 'INCONCLUSIVE'
    d = all_arms['direct']
    b = all_arms['two_stage']
    if d['malformed'] > 0 or b['malformed'] > 0:
        return 'NO_PROMOTION'
    checks = [
        (b['restriction']['recall'] - d['restriction']['recall']) >= 0.15,
        paired['restriction_recall_delta_ci95'][0] > 0.0,
        (b['exact_link_accuracy'] - d['exact_link_accuracy']) >= 0.10,
        paired['exact_link_accuracy_delta_ci95'][0] > 0.0,
        b['claim']['f1'] >= d['claim']['f1'] - 0.05,
        b['restriction']['precision'] >= d['restriction']['precision'] - 0.05,
        b['wrong_target_hits'] == 0,
        b['clean_any_link_hits'] == 0,
    ]
    return 'INDEPENDENT_RETEST_ONLY' if all(checks) else 'NO_PROMOTION'


def diagnose(rows):
    slices = {
        'all': lambda r: True,
        'single': lambda r: r['structure'] == 'single',
        'two_claim': lambda r: r['structure'] == 'two_claim',
        'fees': lambda r: r['domain'] == 'fees',
        'dates': lambda r: r['domain'] == 'dates',
        'entitlements': lambda r: r['domain'] == 'entitlements',
    }
    summary = {
        sl: {a: aggregate([r for r in rows if r['arm'] == a and pred(r)]) for a in ARMS}
        for sl, pred in slices.items()
    }
    paired = paired_bootstrap(rows, 'two_stage', 'direct')
    return dict(
        experimental=True,
        decision=decide_gate(summary, paired),
        results=summary,
        paired=paired,
        scope='Fresh authored matched notices across fees, dates, and entitlements; not field issuer validation or answer quality.',
        production_change=False,
    )


def verify_plan(root):
    if F.read(root / 'protocol.json') != protocol():
        raise ValueError('Protocol drift; do not open the frozen run')


def freeze(root):
    root.mkdir(exist_ok=False)
    pr = protocol()
    P.write(root / 'protocol.json', pr)
    return P.digest(P.enc(pr))


def seal(root, fixture=FIXTURE):
    verify_plan(root)
    cases = F.read(fixture)
    check_cases(cases)
    P.write(root / 'cases.json', cases)
    binding = {'cases': P.digest(P.enc(cases)), 'protocol': P.digest(P.enc(protocol()))}
    P.write(root / 'seal.json', binding)
    return binding


def run(root):
    verify_plan(root)
    cases = F.read(root / 'cases.json')
    binding = F.read(root / 'seal.json')
    check_cases(cases)
    if binding != {'cases': P.digest(P.enc(cases)), 'protocol': P.digest(P.enc(protocol()))}:
        raise ValueError('Cases or protocol changed after sealing')
    if (root / 'started.json').exists():
        raise ValueError('One-look run already started; do not rerun')
    P.write(root / 'started.json', binding)

    rng = random.Random(1729)
    order = list(cases)
    rng.shuffle(order)
    modelroot = Path('data/local_models')
    alias = 'restriction-linker'
    outputs = []
    with S.T.narrator(modelroot, root / 'server.log', alias=alias, model_id=S.MODEL, backend=S.T.default_backend(modelroot)) as (local, meta):
        P.write(root / 'runtime.json', meta)
        for idx, case in enumerate(order, 1):
            spans = index_body_spans(case['notices'])
            body_ids = [sp['id'] for sp in spans]
            arms = list(ARMS)
            rng.shuffle(arms)
            for arm in arms:
                row = dict(
                    case=case['id'],
                    arm=arm,
                    pred=None,
                    raw=None,
                    error=None,
                    finish=None,
                    input_tokens=0,
                    output_tokens=0,
                    seconds=0.0,
                )
                start = time.monotonic()
                try:
                    if arm == 'lexical':
                        row['pred'] = lexical_baseline(case)
                    elif arm == 'select_all':
                        row['pred'] = select_all_baseline(case)
                    elif arm == 'direct':
                        body = S.T.request_body(
                            alias,
                            DIRECT_SYSTEM,
                            render_packet(case),
                            max_tokens=CAP_DIRECT,
                            response_format={
                                'type': 'json_schema',
                                'json_schema': {'name': 'direct_links', 'strict': True, 'schema': schema_direct(body_ids)},
                            },
                        )
                        resp = local.post(body)
                        ch = resp['choices'][0]
                        row['finish'] = ch['finish_reason']
                        row['input_tokens'] = resp['usage']['prompt_tokens']
                        row['output_tokens'] = resp['usage']['completion_tokens']
                        row['raw'] = ch['message']['content']
                        if row['finish'] == 'length' or row['output_tokens'] > CAP_DIRECT:
                            raise ValueError('Output truncated at token cap')
                        row['pred'] = decode_direct(row['raw'], body_ids)
                    elif arm == 'two_stage':
                        b1 = S.T.request_body(
                            alias,
                            STAGE1_SYSTEM,
                            render_packet(case),
                            max_tokens=CAP_STAGE1,
                            response_format={
                                'type': 'json_schema',
                                'json_schema': {'name': 'stage1_claims', 'strict': True, 'schema': schema_stage1(body_ids)},
                            },
                        )
                        r1 = local.post(b1)
                        c1 = r1['choices'][0]
                        f1 = c1['finish_reason']
                        it1, ot1 = r1['usage']['prompt_tokens'], r1['usage']['completion_tokens']
                        raw1 = c1['message']['content']
                        row['input_tokens'] += it1
                        row['output_tokens'] += ot1
                        row['raw'] = {'stage1': raw1, 'stage2': None}
                        row['finish'] = f1
                        if f1 == 'length' or ot1 > CAP_STAGE1:
                            raise ValueError('Stage 1 truncated at token cap')
                        claims = decode_stage1(raw1, body_ids)
                        remaining = [bid for bid in body_ids if bid not in set(claims)]
                        b2 = S.T.request_body(
                            alias,
                            STAGE2_SYSTEM,
                            render_packet(case, stage1_claims=claims),
                            max_tokens=CAP_STAGE2,
                            response_format={
                                'type': 'json_schema',
                                'json_schema': {'name': 'stage2_links', 'strict': True, 'schema': schema_stage2(remaining, claims)},
                            },
                        )
                        r2 = local.post(b2)
                        c2 = r2['choices'][0]
                        f2 = c2['finish_reason']
                        it2, ot2 = r2['usage']['prompt_tokens'], r2['usage']['completion_tokens']
                        raw2 = c2['message']['content']
                        row['input_tokens'] += it2
                        row['output_tokens'] += ot2
                        row['raw']['stage2'] = raw2
                        row['finish'] = f'{f1}/{f2}'
                        if f2 == 'length' or ot2 > CAP_STAGE2:
                            raise ValueError('Stage 2 truncated at token cap')
                        row['pred'] = decode_stage2(raw2, claims, body_ids)
                except Exception as exc:
                    row['error'] = f'{type(exc).__name__}: {str(exc)[:200]}'
                row['seconds'] = round(time.monotonic() - start, 4)
                outputs.append(row)
            print(f'completed packet {idx}/{len(order)}', flush=True)

    P.write(root / 'outputs.json', outputs)
    by_id = {c['id']: c for c in cases}
    scores = [score_row(by_id[o['case']], o) for o in outputs]
    P.write(root / 'scores.json', scores)
    report = diagnose(scores) | dict(protocol_sha=binding['protocol'], cases_sha=binding['cases'])
    P.write(root / 'report.json', report)
    print('decision:', report['decision'], flush=True)
    return report


def main():
    sys.addaudithook(F.local_only)
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['freeze', 'seal', 'run'])
    ap.add_argument('root', type=Path)
    args = ap.parse_args()
    if args.action == 'freeze':
        print('protocol', freeze(args.root))
    elif args.action == 'seal':
        print('sealed', seal(args.root))
    else:
        run(args.root)


if __name__ == '__main__':
    main()
