"""Bitcoin threshold baseline v1 (accuracy plan step 3): math pieces, selection on the first half only, claims and
the one-look run. A synthetic source stands in for the private data."""
import json
import math
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from qualify import btc_baseline as bb
from qualify.btc_crowd import model_p, noon_et


def synthetic_source(root, seed=7, sigma=0.006):
    """Hourly GBM bars from 2026-02-01, daily noon-ET thresholds from 2026-03-05 to 2026-06-25, and a crowd that
    prices each threshold with the true volatility."""
    rng = random.Random(seed)
    start = int(noon_et(date(2026, 2, 1)).timestamp()) // 3600 * 3600
    end = int(noon_et(date(2026, 6, 27)).timestamp())
    ts, closes, price = [], [], 50000.0
    for t in range(start, end, 3600):
        price *= math.exp(rng.gauss(0, sigma))
        ts.append(t)
        closes.append(round(price, 2))
    series = dict(zip(ts, closes))
    (root / 'btc_1h.json').write_text(json.dumps({'chart': {'result': [{'timestamp': ts, 'indicators': {'quote': [{'close': closes}]}}]}}))
    sample, crowd, d, n = [], [], date(2026, 2, 20), 0  # the first days lack 720 h of history on purpose
    while d <= date(2026, 6, 25):
        close = int(noon_et(d).timestamp())
        ref = series[close - 25 * 3600]
        for m in (0.85, 0.97, 0.99, 1.0, 1.01, 1.03, 1.15):  # 0.85 and 1.15 are near certain a day out
            k = round(ref * m, -1)
            n += 1
            y = series[close - 3600] > k
            snap = {}
            for label, hours in bb.HORIZONS.items():
                s = series[close - hours * 3600 - 3600]
                snap[label] = round(min(.999, max(.001, model_p(s, k, sigma, hours))), 4)
            sample.append({'id': f'm{n}', 'day': d.isoformat(), 'close_ts': close, 'threshold': k, 'y': y,
                           'event_id': 'e', 'volume': 1e4, 'yes_token': 't'})
            crowd.append({'id': f'm{n}', 'crowd': snap, 'error': None, 'points': 3})
        d += timedelta(days=1)
    (root / 'sample.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in sample))
    (root / 'crowd.0.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in crowd))
    return series


class TestMath(unittest.TestCase):
    def test_student_t_cdf_matches_published_values(self):
        for x, df, want in ((2, 4, 0.94194), (1, 3, 0.80450), (2.228, 10, 0.97500), (1.943, 6, 0.95000), (1, 1, 0.75), (1, 2, 0.78868)):
            self.assertAlmostEqual(bb.t_cdf(x, df), want, places=4, msg=(x, df))
            self.assertAlmostEqual(bb.t_cdf(-x, df), 1 - want, places=4)
        self.assertAlmostEqual(bb.t_cdf(1.3, 400), 0.5 * math.erfc(-1.3 / math.sqrt(2)), places=3)
        self.assertEqual(bb.t_cdf(0, 5), 0.5)
        with self.assertRaises(ValueError):
            bb.t_cdf(1, 2.5)

    def test_ewma_reads_past_returns_skips_gaps_and_refuses_stale(self):
        series = {3600 * i: 100 * math.exp(0.01 * (i % 2)) for i in range(400)}  # returns of +-0.01
        table = bb.ewma_table(series, 24)
        self.assertAlmostEqual(bb.ewma_vol_at(table, 3600 * 300), 0.01, places=6)
        self.assertIsNone(bb.ewma_vol_at(table, 3600 * 430))  # last return more than a day old
        del series[3600 * 200]
        keys, _ = bb.ewma_table(series, 24)
        self.assertNotIn(3600 * 200, keys)
        self.assertNotIn(3600 * 201, keys)  # a return across a gap is not invented

    def test_ewma_never_uses_the_return_after_the_snapshot(self):
        series, price = {}, 100.0
        for i in range(400):
            price *= math.exp(0.001 if i % 2 else -0.001)
            series[3600 * i] = price
        series[3600 * 300] = series[3600 * 299] * 1.5  # a jump in the bar that closes an hour after t
        table = bb.ewma_table(series, 24)
        self.assertLess(bb.ewma_vol_at(table, 3600 * 300), 0.002)
        self.assertGreater(bb.ewma_vol_at(table, 3600 * 301), 0.05)

    def test_student_t_has_the_same_variance_as_the_lognormal(self):
        sd = 0.05
        r = {'s': 100.0, 'k': 100.0 * math.exp(sd - sd * sd / 2), 'hours': 24, 'vol720': sd / math.sqrt(24)}
        self.assertAlmostEqual(bb.z_score(r['s'], r['k'], r['vol720'], r['hours']), -1.0, places=9)
        # One standard deviation below: a unit-variance t4 puts less mass there than the normal (0.1151 vs 0.1587).
        self.assertAlmostEqual(bb.p_student(4, r), 0.1151, places=3)
        self.assertAlmostEqual(bb.p_v1(None, r), 0.1587, places=3)

    def test_crowd_plus_uses_the_crowd_and_v1(self):
        r = {'s': 90000.0, 'k': 91000.0, 'hours': 24, 'vol720': 0.005, 'crowd': 0.3}
        self.assertEqual(bb.x_crowd_plus(r), [1.0, bb.logit(0.3), bb.logit(bb.p_v1(None, r))])

    def test_logistic_fit_recovers_known_weights(self):
        rng = random.Random(3)
        xs, ys = [], []
        for _ in range(4000):
            x = rng.uniform(-3, 3)
            xs.append([1.0, x])
            ys.append(int(rng.random() < bb.sigmoid(0.5 + 1.5 * x)))
        a, b = bb.fit_logistic(xs, ys)
        self.assertAlmostEqual(a, 0.5, delta=0.15)
        self.assertAlmostEqual(b, 1.5, delta=0.15)

    def test_v1_candidate_is_the_frozen_v1_model(self):
        r = {'s': 90000.0, 'k': 91000.0, 'hours': 24, 'vol720': 0.005}
        self.assertEqual(bb.p_v1(None, r), model_p(90000.0, 91000.0, 0.005, 24))

    def test_scores_and_intervals(self):
        recs = [{'day': f'2026-05-{1 + i % 20:02d}', 'y': i % 2, 'crowd': .5, 'v1': .6 if i % 2 else .4,
                 'baseline': .9 if i % 2 else .1, 'crowd_plus': .5} for i in range(200)]
        s = bb.score(recs, ('crowd', 'v1', 'baseline', 'crowd_plus'))
        self.assertEqual(s['baseline']['auc'], 1.0)
        self.assertEqual(s['crowd']['auc'], .5)
        lo, hi = s['baseline']['ci95_minus_crowd']
        self.assertLessEqual(lo, s['baseline']['brier_minus_crowd'] + 1e-12)
        self.assertGreaterEqual(hi, s['baseline']['brier_minus_crowd'] - 1e-12)
        self.assertLess(hi, 0)
        self.assertEqual(s['crowd_plus']['ci95_minus_crowd'], [0.0, 0.0])


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.src = Path(self.temp.name) / 'src'
        self.src.mkdir()
        synthetic_source(self.src)

    def tearDown(self):
        self.temp.cleanup()

    def test_records_keep_contested_markets_with_full_inputs(self):
        recs, missing = bb.records(self.src, 24, '24h')
        self.assertTrue(recs)
        for r in recs:
            v1 = bb.p_v1(None, r)
            self.assertTrue(bb.CONTESTED[0] <= r['crowd'] <= bb.CONTESTED[1] or bb.CONTESTED[0] <= v1 <= bb.CONTESTED[1])
            self.assertEqual(set(r['ewma']), {str(h) for h in bb.HALF_LIVES})
        self.assertGreater(missing.get('no_spot_or_vol', 0), 0)  # the first days lack 720 h of history
        self.assertGreater(missing.get('not_contested', 0), 0)  # near-certain thresholds are not scored
        self.assertTrue(all(r['day'] >= '2026-03-02' for r in recs))  # 30 days after the first bar

    def test_choosing_never_sees_the_second_half(self):
        recs, _ = bb.records(self.src, 24, '24h')
        seen = []
        real = bb.month_cv
        with patch('qualify.btc_baseline.month_cv', side_effect=lambda name, dev: seen.extend(r['day'] for r in dev) or real(name, dev)):
            bb.evaluate(recs)
        self.assertTrue(seen)
        self.assertTrue(all(d < bb.SPLIT_DAY for d in seen))

    def test_second_half_outcomes_do_not_change_the_choice(self):
        recs, _ = bb.records(self.src, 24, '24h')
        flipped = [dict(r, y=1 - r['y']) if r['day'] >= bb.SPLIT_DAY else r for r in recs]
        a, _ = bb.evaluate(recs)
        b, _ = bb.evaluate(flipped)
        self.assertEqual((a['chosen'], a['params'], a['first_half']), (b['chosen'], b['params'], b['first_half']))
        self.assertNotEqual(a['second_half']['crowd']['brier'], b['second_half']['crowd']['brier'])

    def test_the_lowest_cross_validated_baseline_is_chosen(self):
        result, _ = bb.evaluate(bb.records(self.src, 24, '24h')[0])
        cv = result['first_half']['cv_brier']
        self.assertEqual(cv[result['chosen']], min(cv[n] for n in bb.BASELINES))
        self.assertIn('crowd_plus', cv)

    def test_a_held_out_month_is_predicted_without_its_own_outcomes(self):
        dev = [r for r in bb.records(self.src, 24, '24h')[0] if r['day'] < bb.SPLIT_DAY]
        month = sorted({r['day'][:7] for r in dev})[-1]
        flipped = [dict(r, y=1 - r['y']) if r['day'][:7] == month else r for r in dev]
        for name in ('recal', 'crowd_plus', 'ewma'):
            a, b = bb.month_cv(name, dev), bb.month_cv(name, flipped)
            same = [x == y for x, y, r in zip(a, b, dev) if r['day'][:7] == month]
            self.assertTrue(same and all(same), name)

    def test_a_true_volatility_crowd_is_not_beaten_by_noise(self):
        result, scored = bb.evaluate(bb.records(self.src, 24, '24h')[0])
        self.assertIn(result['chosen'], bb.BASELINES)
        self.assertFalse(result['claims']['baseline_beats_crowd'])
        self.assertEqual(len(scored), result['second_half']['n'])
        self.assertTrue(all(0 < r['baseline'] < 1 and 0 < r['crowd_plus'] < 1 for r in scored))


class TestRun(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.src, self.root = base / 'src', base / 'lab'
        self.src.mkdir()
        synthetic_source(self.src)

    def tearDown(self):
        self.temp.cleanup()

    def test_freeze_then_one_look(self):
        with self.assertRaises(ValueError):
            bb.run(self.root, source=self.src)
        bb.freeze(self.root, bb.protocol(bb.source_hashes(self.src)))
        report = bb.run(self.root, source=self.src)
        self.assertEqual(set(report['horizons']), set(bb.HORIZONS))
        self.assertEqual(report['claims'], report['horizons'][bb.PRIMARY]['claims'])
        self.assertTrue((self.root / 'results.json').exists())
        with self.assertRaises(ValueError):
            bb.run(self.root, source=self.src)

    def test_a_changed_source_is_refused(self):
        bb.freeze(self.root, bb.protocol(bb.source_hashes(self.src)))
        with (self.src / 'crowd.0.jsonl').open('a') as fh:
            fh.write(json.dumps({'id': 'x', 'crowd': {}, 'error': None, 'points': 0}) + '\n')
        with self.assertRaises(ValueError):
            bb.run(self.root, source=self.src)
        with self.assertRaises(ValueError):
            bb.freeze(self.root, bb.protocol(bb.source_hashes(self.src)))
        self.assertFalse((self.root / 'results.json').exists())


if __name__ == '__main__':
    unittest.main()
