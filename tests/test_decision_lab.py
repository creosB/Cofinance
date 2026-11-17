import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qualify import decision_lab as D
from qualify.historical import digest, dump, lines, write_lines


def row(symbol='NVDA', origin='2026-10-27'):
    return {'id': f'{origin}:{symbol}', 'symbol': symbol, 'origin': origin,
            'answer': 'Accepted summary.', 'data_summary': 'Price fell 2%.', 'news_summary': None,
            'label': 'UP', 'return': 99999, 'baselines': {'momentum20': 'HIDDEN_BASELINE'},
            'audit': {'leak': False}, 'news_served': [{'title': 'HIDDEN_RAW_NEWS'}]}


class TestDecisionLab(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / 'screen'
        self.rows = [row('NVDA', '2026-10-27'), row('NVDA', '2026-10-28'), row('NVDA', '2026-11-06'),
                     row('BTC', '2026-10-27'), row('BTC', '2026-11-06')]
        self.source = self.root / 'predictions_v2.jsonl'
        write_lines(self.source, self.rows)
        dump(self.root / 'predictions_v2.seal.json',
             {'cases': len(self.rows), 'predictions_sha256': digest(self.source.read_bytes())})

    def tearDown(self):
        self.tmp.cleanup()

    def run_screen(self, call=None):
        return D.run(self.root, self.out, 'v2', 'contract-v1', 'smoke',
                     call or (lambda *a, **kw: '{"label":"DOWN"}'), {'test': True})

    def test_prompt_matches_original_and_excludes_hidden_fields(self):
        text = D.cached_prompt(self.rows[0])
        self.assertEqual(text, 'Question: ' + D.R.QUERY.format(t='NVDA') +
                         '\n\nTeam answer: Accepted summary.\n\nData Analyst report: Price fell 2%.' +
                         '\n\nNews Researcher report: Not requested.')
        for hidden in ('99999', 'HIDDEN_BASELINE', 'HIDDEN_RAW_NEWS', '2026-10-27'):
            self.assertNotIn(hidden, text, 'Only exact model-visible upstream reports may enter Decision.')

    def test_sample_selection_is_outcome_blind_first_and_last_per_asset(self):
        selected = D.select_cases(self.rows, 'smoke')
        self.assertEqual([r['id'] for r in selected],
                         ['2026-10-27:BTC', '2026-11-06:BTC', '2026-10-27:NVDA', '2026-11-06:NVDA'])
        changed = [{**r, 'return': -123, 'label': 'DOWN'} for r in reversed(self.rows)]
        self.assertEqual([r['id'] for r in selected], [r['id'] for r in D.select_cases(changed, 'smoke')])
        self.assertEqual(len(D.select_cases(self.rows, 'full')), len(self.rows))

    def test_structured_parser_rejects_prose_extra_keys_and_unknown_labels(self):
        for label in ('UP', 'DOWN', 'ABSTAIN'):
            self.assertEqual(D.parse_json_label(json.dumps({'label': label})), label)
        for text in ('UP', '{"label":"UP","reason":"x"}', '{"label":"maybe"}', '[]', '{}', '{"label":null}'):
            self.assertEqual(D.parse_json_label(text), 'ERROR')

    def test_changed_source_is_refused_before_model_call(self):
        self.source.write_text(self.source.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'Source predictions changed'):
            self.run_screen(lambda *a, **kw: self.fail('Unsealed source reached the model'))
        self.assertFalse(self.out.exists())

    def test_run_never_loads_outcomes_and_plan_exists_before_call(self):
        seen = []
        def call(system, prompt, **kw):
            self.assertTrue((self.out / 'plan.json').exists())
            self.assertEqual(kw['response_format'], D.SCHEMA)
            self.assertEqual(kw['max_tokens'], 24)
            self.assertNotIn('allow_length', kw)
            seen.append(prompt)
            return '{"label":"DOWN"}'
        with patch.object(D.R, 'load_market', side_effect=AssertionError('Outcomes entered prediction')):
            self.assertEqual(self.run_screen(call)['cases'], 4)
        self.assertEqual(len(seen), 4)
        D.verified(self.root, self.out)

    def test_failures_remain_in_the_denominator(self):
        def broken(*a, **kw):
            raise ValueError('Incomplete forecast')
        self.run_screen(broken)
        preds = lines(self.out / 'predictions.jsonl')
        self.assertEqual(len(preds), 4)
        self.assertTrue(all(p['label'] == 'ERROR' for p in preds))
        self.assertTrue(all('Incomplete forecast' in p['error'] for p in preds))

    def test_existing_screen_is_never_overwritten(self):
        self.run_screen()
        before = (self.out / 'predictions.jsonl').read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_screen()
        self.assertEqual((self.out / 'predictions.jsonl').read_bytes(), before)

    def test_score_requires_seal_before_loading_outcomes(self):
        with patch.object(D.R, 'load_market', side_effect=AssertionError('Outcomes loaded too soon')):
            with self.assertRaises(FileNotFoundError):
                D.score(self.root, self.out)

    def test_modified_plan_or_predictions_are_rejected(self):
        self.run_screen()
        path = self.out / 'predictions.jsonl'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'changed after sealing'):
            D.verified(self.root, self.out)

    def test_split_and_metrics_count_errors_abstentions_and_missing_outcomes(self):
        rows = self.rows
        for r in rows:
            r.update(entry_date='2026-10-28', exit_date='2026-11-03',
                     baselines={'momentum20': 'DOWN', 'always_up': 'UP', 'news_rule': 'ABSTAIN'})
        write_lines(self.source, rows)
        dump(self.root / 'predictions_v2.seal.json', {'cases': len(rows), 'predictions_sha256': digest(self.source.read_bytes())})
        answers = iter(['{"label":"DOWN"}', '{"label":"ABSTAIN"}', 'bad', '{"label":"UP"}'])
        self.run_screen(lambda *a, **kw: next(answers))
        bars = [{'date': '2026-10-28', 'open': 100, 'close': 100}, {'date': '2026-11-03', 'open': 90, 'close': 90}]
        with patch.object(D.R, 'load_market', return_value=({'NVDA': bars, 'BTC': bars}, {}, {})):
            report = D.score(self.root, self.out)
        self.assertEqual(report['cases'], 4)
        self.assertEqual(report['arms']['candidate']['accuracy_all'], .25)
        self.assertEqual(report['arms']['candidate']['coverage'], .5)
        self.assertEqual(report['arms']['candidate']['labels'], {'UP': 1, 'DOWN': 1, 'ABSTAIN': 1, 'ERROR': 1})
        self.assertEqual(report['paired_vs_source']['clusters'], 2)
        with patch.object(D.R, 'load_market', return_value=({'NVDA': bars, 'BTC': []}, {}, {})):
            missing = D.score(self.root, self.out)
        self.assertEqual(len(missing['missing_outcomes']), 2)
        self.assertEqual(missing['scored'], 2)


class TestFactCard(unittest.TestCase):
    def setUp(self):
        from tests.test_replay import make_root
        self.tmp, self.root = make_root()
        self.addCleanup(self.tmp.cleanup)
        self.out = self.root / 'screen'
        news = [{'title': 'Nvidia wins a contract', 'available_at': 1.0, 'action': 'alert', 'direction': 'bullish'},
                {'title': 'Regulator opens a probe', 'available_at': 1.0, 'action': 'digest', 'direction': 'bearish'},
                {'title': 'Conference schedule posted', 'available_at': 1.0, 'action': 'digest', 'direction': 'neutral'}]
        self.rows = [{**row(s, o), 'news_served': news} for s, o in
                     (('NVDA', '2026-10-28'), ('NVDA', '2026-10-29'), ('BTC', '2026-10-29'))]
        self.source = self.root / 'predictions_v2.jsonl'
        write_lines(self.source, self.rows)
        dump(self.root / 'predictions_v2.seal.json',
             {'cases': len(self.rows), 'predictions_sha256': digest(self.source.read_bytes())})

    def screen(self):
        prompts = []
        def call(system, prompt, **kw):
            self.assertIn('fact card', system)
            prompts.append(prompt)
            return '{"label":"ABSTAIN"}'
        D.run(self.root, self.out, 'v2', 'signals-v1', 'full', call, {'test': True})
        return prompts

    def test_card_keeps_tool_values_and_gate_labels_without_future_or_hidden_fields(self):
        prompts = self.screen()
        self.assertEqual(len(prompts), 3)
        series, _, _ = D.R.load_market(self.root)
        for prompt, r in zip(prompts, D.select_cases(self.rows, 'full')):
            self.assertTrue(prompt.startswith(D.cached_prompt(r)), 'The card is added to the cached reports, not instead of them.')
            bars = [b for b in series[r['symbol']] if b['date'] <= r['origin']]
            price = bars[-1]['close']
            change = round((price / bars[-2]['close'] - 1) * 100, 4)
            drawdown = round((1 - price / max(max(b['high'] for b in bars), price)) * 100, 4)
            self.assertIn(f'Last session change: {change:+.2f}%', prompt)
            self.assertIn(f'Below the all-time high by {drawdown:.2f}%', prompt, 'Card values must come from bars on or before T.')
            self.assertIn('[alert, bullish] Nvidia wins a contract', prompt)
            self.assertIn('[digest, bearish] Regulator opens a probe', prompt)
            self.assertIn('1 bullish, 1 bearish, 1 neutral or unclear', prompt)
            for hidden in ('99999', 'HIDDEN', '2026-'):
                self.assertNotIn(hidden, prompt, 'The card may carry only point-in-time tool values and served headlines.')
        self.assertEqual(json.loads((self.out / 'plan.json').read_text())['input'], 'card')
        D.verified(self.root, self.out)

    def test_card_states_missing_price_and_news_plainly(self):
        text = D.fact_card({'news_served': []}, {'status': 'unavailable'})
        self.assertIn('Price data unavailable', text)
        self.assertIn('Accepted headlines: none', text)
        self.assertIn('0 bullish, 0 bearish, 0 neutral or unclear', text)

    def test_changed_price_input_is_rejected_at_scoring(self):
        self.screen()
        path = self.root / 'raw' / 'NVDA.json'
        raw = json.loads(path.read_text())
        res = raw['chart']['result'][0]
        i = next(i for i, t in enumerate(res['timestamp']) if D.R.utc_day(t) == '2026-10-29')
        res['indicators']['quote'][0]['close'][i] *= 1.1
        path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, 'Model input differs'):
            D.verified(self.root, self.out)

    def test_shortlist_rule_needs_valid_replies_and_both_halves_above_always_up(self):
        def rep(err, overall, early, late):
            arm = lambda b: {'balanced_accuracy': b, 'labels': {'ERROR': err}}
            return {'arms': {'candidate': arm(overall)},
                    'halves': {'early': {'candidate': arm(early)}, 'late': {'candidate': arm(late)}}}
        self.assertTrue(D.shortlisted(rep(2, .56, .52, .6)))
        self.assertFalse(D.shortlisted(rep(3, .56, .52, .6)))
        self.assertFalse(D.shortlisted(rep(0, .5, .52, .6)))
        self.assertFalse(D.shortlisted(rep(0, .56, .49, .6)))
        self.assertFalse(D.shortlisted(rep(0, .56, None, .6)))


def choice(label, alts, prefix=''):
    """A runtime reply shaped like llama-server's: JSON label tokens, top log-probabilities at the label token."""
    first = {'UP': 'UP', 'DOWN': 'D', 'ABSTAIN': 'AB', 'YES': 'YES', 'NO': 'NO'}[label]
    tok = lambda t, top=(): {'token': t, 'logprob': 0.0, 'top_logprobs': [{'token': a, 'logprob': lp} for a, lp in top]}
    content = [tok('{\n'), tok(' "'), tok('label'), tok('":'), tok('' if prefix else ' "'), tok(prefix + first, alts)]
    return {'message': {'content': json.dumps({'label': label})}, 'logprobs': {'content': content}}


class TestProbReadout(unittest.TestCase):
    def test_label_probs_reads_the_label_token_and_ignores_invalid_tokens(self):
        lps, floor = D.label_probs(choice('UP', [('UP', -0.1), ('AB', -2.5), ('D', -4.0), (' UP', -1.0), ('ST', -6.0)]))
        self.assertAlmostEqual(lps['UP'], -0.1)
        self.assertAlmostEqual(lps['DOWN'], -4.0)
        self.assertAlmostEqual(lps['ABSTAIN'], -2.5)
        self.assertEqual(floor, -6.0)
        s, q, floored = D.direction(lps, floor)
        self.assertAlmostEqual(s, 3.9)
        self.assertEqual((D.policy(q), floored), ('UP', []))
        lps, floor = D.label_probs(choice('DOWN', [(' "D', -0.2), (' "UP', -1.9), ('UP', -3.0)], prefix=' "'))
        self.assertAlmostEqual(lps['DOWN'], -0.2)
        self.assertAlmostEqual(lps['UP'], -1.9, msg='A token without the chosen prefix is not a valid label start.')
        self.assertEqual(D.policy(D.direction(lps, floor)[1]), 'DOWN')

    def test_missing_labels_take_the_floor_and_python_owns_abstention(self):
        lps, floor = D.label_probs(choice('UP', [('UP', -0.01), ('AB', -5.0), ('U', -9.0)]))
        self.assertIsNone(lps['DOWN'])
        s, q, floored = D.direction(lps, floor)
        self.assertEqual(floored, ['DOWN'])
        self.assertGreater(s, 8.9)
        s, q, floored = D.direction({'UP': None, 'DOWN': None, 'ABSTAIN': -0.01}, -8.0)
        self.assertEqual((s, q, D.policy(q), floored), (0, 0.5, 'ABSTAIN', ['UP', 'DOWN']))
        self.assertEqual([D.policy(x) for x in (None, .6, .4, .55, .45)], ['ERROR', 'UP', 'DOWN', 'ABSTAIN', 'ABSTAIN'])
        with self.assertRaises(ValueError):
            D.label_probs({'logprobs': {'content': []}})

    def test_auc_is_ranking_skill_with_half_credit_for_ties(self):
        self.assertEqual(D.auc([(3, True), (1, False)]), 1.0)
        self.assertEqual(D.auc([(1, True), (3, False)]), 0.0)
        self.assertEqual(D.auc([(2, True), (2, False), (5, True), (0, False)]), 0.875)
        self.assertEqual(D.auc([(0, True), (0, False)]), 0.5, 'A constant score (always-UP) has no skill.')
        self.assertIsNone(D.auc([(1, True)]))

    def test_prob_shortlist_needs_skill_in_both_halves(self):
        rep = lambda f, a, e, l: {'failures': f, 'ranking': {'auc': a, 'early': e, 'late': l}}
        self.assertTrue(D.prob_shortlisted(rep(2, .56, .51, .6)))
        self.assertFalse(D.prob_shortlisted(rep(3, .56, .51, .6)))
        self.assertFalse(D.prob_shortlisted(rep(0, .55, .51, .6)))
        self.assertFalse(D.prob_shortlisted(rep(0, .6, .49, .7)))
        self.assertFalse(D.prob_shortlisted(rep(0, .6, None, .7)))

    def test_prob_run_records_probabilities_and_scores_the_python_call(self):
        TestFactCard.setUp(self)
        for r in self.rows:
            r.update(entry_date='2026-10-29', exit_date='2026-10-30',
                     baselines={'always_up': 'UP', 'momentum20': 'DOWN', 'news_rule': 'ABSTAIN'})
        write_lines(self.source, self.rows)
        dump(self.root / 'predictions_v2.seal.json', {'cases': len(self.rows), 'predictions_sha256': digest(self.source.read_bytes())})
        replies = iter([choice('UP', [('UP', -0.1), ('D', -3.0)]), choice('DOWN', [('D', -0.2), ('UP', -2.0)]),
                        {'message': {'content': '{"label":"UP"}'}, 'logprobs': {'content': []}}])
        def call(system, prompt, **kw):
            self.assertEqual((kw['top_logprobs'], kw['return_choice']), (20, True))
            return next(replies)
        D.run(self.root, self.out, 'v2', 'prob-v1', 'full', call, {'test': True})
        preds = lines(self.out / 'predictions.jsonl')
        self.assertEqual([p['python_label'] for p in preds], ['UP', 'DOWN', 'ERROR'])
        self.assertIn('No label token', preds[2]['error'])
        plan = json.loads((self.out / 'plan.json').read_text())
        self.assertEqual((plan['readout'], plan['shortlist_rule']), ('logprobs', D.PROB_SHORTLIST))
        report = D.score(self.root, self.out)
        self.assertEqual(report['failures'], 1)
        self.assertEqual(sum(report['python_call']['all']['labels'].values()), 3)
        self.assertIn('auc', report['ranking'])
        self.assertIsInstance(report['shortlist'], bool)


class TestHeadlineAndPair(unittest.TestCase):
    def setUp(self):
        TestFactCard.setUp(self)
        for r in self.rows:
            r.update(entry_date='2026-10-29', exit_date='2026-10-30',
                     baselines={'always_up': 'UP', 'momentum20': 'DOWN', 'news_rule': 'ABSTAIN'})
        write_lines(self.source, self.rows)
        dump(self.root / 'predictions_v2.seal.json', {'cases': len(self.rows), 'predictions_sha256': digest(self.source.read_bytes())})

    def test_headlines_are_judged_once_from_asset_and_title_only(self):
        items = D.headline_items(self.rows)
        self.assertEqual([i['key'] for i in items],
                         sorted({f"{r['symbol']}|{n['title']}" for r in self.rows for n in r['news_served']}))
        self.assertEqual(len(items), 6)
        prompt = D.headline_prompt(items[0])
        self.assertEqual(prompt, f"Asset: {items[0]['symbol']}\nHeadline: {items[0]['title']}")
        for hidden in ('bullish', 'bearish', 'neutral', 'alert', 'digest', '99999', '2026-'):
            self.assertNotIn(hidden, prompt.lower(), 'The judge sees the headline, not the gate label or any outcome.')

    def test_impact_discounts_no_effect_mass(self):
        self.assertAlmostEqual(D.impact({'UP': math.log(.6), 'DOWN': math.log(.2), 'ABSTAIN': math.log(.2)}), .4)
        self.assertAlmostEqual(D.impact({'UP': math.log(.05), 'DOWN': math.log(.05), 'ABSTAIN': math.log(.9)}), 0)
        self.assertAlmostEqual(D.impact({'UP': math.log(.1), 'DOWN': None, 'ABSTAIN': math.log(.9)}), .1)
        self.assertEqual(D.impact({'UP': None, 'DOWN': None, 'ABSTAIN': None}), 0.0)

    def test_headline_screen_aggregates_per_case_and_counts_failures(self):
        judged = {'Nvidia wins a contract': ('UP', [('UP', math.log(.8)), ('D', math.log(.1)), ('AB', math.log(.1))]),
                  'Regulator opens a probe': ('DOWN', [('D', math.log(.7)), ('UP', math.log(.1)), ('AB', math.log(.2))])}
        def call(system, prompt, **kw):
            self.assertIn('[Headline Judge]', system)
            title = prompt.split('Headline: ')[1]
            if title not in judged:
                raise ValueError('Incomplete forecast')
            return choice(*judged[title])
        D.run(self.root, self.out, 'v2', 'headline-v1', 'full', call, {'test': True})
        report = D.score(self.root, self.out)
        self.assertEqual((report['headlines'], report['scored']), (6, 3))
        self.assertEqual(report['failures'], 2, 'Failed judgments stay counted and score 0.')
        preds = {p['id']: p for p in lines(self.out / 'predictions.jsonl')}
        self.assertAlmostEqual(preds['NVDA|Nvidia wins a contract']['impact'], .7)
        self.assertAlmostEqual(preds['NVDA|Regulator opens a probe']['impact'], -.6)
        self.assertEqual(report['gate_agreement'], 4)
        self.assertEqual(report['python_call']['all']['labels']['ABSTAIN'], 3, 'Each case sums to 0.1, below the 0.5 call line.')
        self.assertIsInstance(report['shortlist'], bool)

    def test_pair_scores_rise_minus_fall(self):
        answers = {'higher': [('YES', -0.1), ('NO', -2.4)], 'lower': [('YES', -0.2), ('NO', -1.7)]}
        def call(system, prompt, **kw):
            self.assertEqual(kw['response_format'], D.YN)
            return choice('YES', answers['higher' if 'close higher' in system else 'lower'])
        D.run(self.root, self.out, 'v2', 'pair-v1', 'full', call, {'test': True})
        p = lines(self.out / 'predictions.jsonl')[0]
        self.assertAlmostEqual(p['sides']['rise']['logit'], 2.3)
        self.assertAlmostEqual(p['sides']['fall']['logit'], 1.5)
        self.assertAlmostEqual(p['score'], 0.8)
        self.assertEqual(p['python_label'], 'UP')
        report = D.score(self.root, self.out)
        self.assertEqual(report['failures'], 0)
        self.assertIn('auc', report['ranking'])

    def test_constant_yes_cancels_out(self):
        same = [('YES', -0.1), ('NO', -2.4)]
        D.run(self.root, self.out, 'v2', 'pair-v1', 'full', lambda s, p, **kw: choice('YES', same), {'test': True})
        self.assertTrue(all(p['score'] == 0 and p['python_label'] == 'ABSTAIN' for p in lines(self.out / 'predictions.jsonl')))

    def test_skill_shortlist_must_beat_the_gate_labels(self):
        rep = lambda a, gate, e=.6, l=.6, f=0: {'failures': f, 'ranking': {
            'auc': a, 'early': e, 'late': l, 'references_context_only': {'net_headline_labels': gate}}}
        self.assertTrue(D.skill_shortlisted(rep(.6, .556)))
        self.assertFalse(D.skill_shortlisted(rep(.55, .5)))
        self.assertFalse(D.skill_shortlisted(rep(.57, .58)))
        self.assertFalse(D.skill_shortlisted(rep(.6, .5, e=.49)))
        self.assertFalse(D.skill_shortlisted(rep(.6, .5, f=3)))
        self.assertTrue(D.skill_shortlisted(rep(.56, None)))


class TestWindow(unittest.TestCase):
    def setUp(self):
        from tests.test_replay import make_root
        self.tmp, self.root = make_root()
        self.addCleanup(self.tmp.cleanup)
        self.out = self.root / 'window'

    def judge(self, system, prompt, **kw):
        up = 'contract' in prompt
        return choice('UP' if up else 'DOWN', [('UP', math.log(.8 if up else .1)), ('D', math.log(.1 if up else .8)),
                                               ('AB', math.log(.1))])

    def seal(self):
        return D.seal_window(self.root, self.out, '2026-10-29', '2026-10-29', 'headline-v1', self.judge, {'test': True})

    def test_window_serves_only_news_before_each_cutoff_and_seals_calls(self):
        self.assertEqual(self.seal()['cases'], len(D.R.ASSETS))
        calls = {c['id']: c for c in lines(self.out / 'calls.jsonl')}
        nvda = calls['2026-10-29:NVDA']
        self.assertEqual([n['title'] for n in nvda['news_served']], ['Nvidia wins a contract'],
                         'A headline published after the cutoff must never be judged for that case.')
        self.assertAlmostEqual(nvda['score'], .7)
        self.assertEqual([calls[f'2026-10-29:{t}']['label'] for t in ('NVDA', 'AAPL', 'MSFT')], ['UP', 'DOWN', 'ABSTAIN'])

    def test_window_is_sealed_and_scored_once(self):
        self.seal()
        with self.assertRaises(FileExistsError):
            self.seal()
        report = D.score_window(self.root, self.out)
        self.assertEqual(report['cases'], len(D.R.ASSETS))
        self.assertIn('confirmed', report)
        with self.assertRaisesRegex(ValueError, 'already scored'):
            D.score_window(self.root, self.out)

    def test_tampered_calls_are_refused(self):
        self.seal()
        path = self.out / 'calls.jsonl'
        path.write_text(path.read_text().replace('"label": "DOWN"', '"label": "UP"', 1))
        with self.assertRaisesRegex(ValueError, 'changed after sealing'):
            D.score_window(self.root, self.out)

    def register(self):
        with patch.object(D, 'exits_on_disk', return_value=[]):
            return D.register_addendum(self.root, self.out, 'headline-v1-mean', now='2026-11-17T09:00:00+00:00')

    def test_addendum_is_registered_before_outcomes_and_scored_in_the_one_look(self):
        self.seal()
        reg = self.register()
        doc = json.loads((self.out / 'addendum-headline-v1-mean.json').read_text())
        judged = {j['id']: j['impact'] for j in lines(self.out / 'judgments.jsonl')}
        for c in lines(self.out / 'calls.jsonl'):
            imps = [judged[f"{c['symbol']}|{n['title']}"] for n in c['news_served']]
            mine = [x for x in doc['calls'] if x['id'] == c['id']][0]
            self.assertAlmostEqual(mine['score'], sum(imps) / len(imps) if imps else 0.0, places=6)
        self.assertEqual(reg['cases'], len(D.R.ASSETS))
        with self.assertRaisesRegex(ValueError, 'already registered'):
            self.register()
        with patch.object(D, 'PUBLIC_DIR', self.root):
            report = D.score_window(self.root, self.out)
        self.assertIn('headline-v1-mean', report['addenda'])
        self.assertIn('confirmed', report['addenda']['headline-v1-mean'])
        self.assertIn('adopt_mean_live', report)
        with self.assertRaisesRegex(ValueError, 'already scored'):
            self.register()

    def test_addendum_is_refused_once_any_outcome_is_on_disk(self):
        self.seal()
        with self.assertRaisesRegex(ValueError, 'already on disk'):
            D.register_addendum(self.root, self.out, 'headline-v1-mean')
        self.assertFalse((self.out / 'addendum-headline-v1-mean.json').exists())
        series = {'SPY': [{'date': d} for d in ('2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13', '2026-11-16')]}
        calls = [{'id': 'a', 'origin': '2026-11-09'}, {'id': 'b', 'origin': '2026-11-10'}]
        self.assertEqual(D.exits_on_disk(series, calls), ['a'], 'origin 11/09 exits 5 sessions later, on 11/16')

    def test_tampered_or_republished_addendum_is_refused(self):
        self.seal()
        self.register()
        path = self.out / 'addendum-headline-v1-mean.json'
        original = path.read_text()
        path.write_text(original.replace('"label": "ABSTAIN"', '"label": "UP"', 1))
        with patch.object(D, 'PUBLIC_DIR', self.root), self.assertRaisesRegex(ValueError, 'changed after registration'):
            D.score_window(self.root, self.out)
        path.write_text(original)
        public = self.root / 'window-2026-11-09-headline-mean-addendum.json'
        public.write_text(original.replace('2026-11-17T09:00:00', '2026-11-18T09:00:00'))
        with patch.object(D, 'PUBLIC_DIR', self.root), self.assertRaisesRegex(ValueError, 'published copy'):
            D.score_window(self.root, self.out)
        public.write_bytes(original.replace('\n', '\r\n').encode('utf-8'))
        with patch.object(D, 'PUBLIC_DIR', self.root):
            report = D.score_window(self.root, self.out)  # a checkout that only changed line endings is the same copy
        self.assertIn('headline-v1-mean', report['addenda'])

    def test_aggregations(self):
        self.assertEqual((D.aggregate([.5, -.1, .2], 'mean'), D.aggregate([], 'mean'), D.aggregate([.5, -.1], 'sum')), (.2, 0.0, .4))
        with self.assertRaises(ValueError):
            D.aggregate([1], 'max')

    def test_only_headline_candidates_can_seal_a_window(self):
        with self.assertRaises(ValueError):
            D.seal_window(self.root, self.out, '2026-10-29', '2026-10-29', 'prob-v1', self.judge, {})


if __name__ == '__main__':
    unittest.main()
