"""Forward ledger statechart: random clock timelines against real record and score, checked against an independent
calendar model (weekday sessions open 14:30 UTC and count once their UTC day has passed)."""
import random
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qualify.forward import HORIZON, chain, record, score, statechart
from tests.test_forward import MON, fake, raw_for

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def sessions_after(seal, now):
    """Completed SPY sessions after seal, in order, by the fixture's calendar. Independent of qualify.forward."""
    day = datetime.fromtimestamp(seal, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today = datetime.fromtimestamp(now, UTC).date()
    out = []
    while day.date() < today:
        opens = day + timedelta(hours=14, minutes=30)
        if day.weekday() < 5 and opens.timestamp() > seal:
            out.append(day.date().isoformat())
        day += timedelta(days=1)
    return out


class TestForwardStatechart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def timeline(self, seed):
        rng, root, seals, matured = random.Random(seed), Path(self.tmp.name) / f'fw{seed}', [], {}
        now = MON
        for _ in range(28):
            now += rng.choice([3, 9, 20, 30, 50, 75]) * 3600
            if now > datetime(2026, 12, 20, tzinfo=UTC).timestamp():
                break
            open_batch = bool(seals) and len(sessions_after(seals[-1], now)) < HORIZON
            if rng.random() < .5:
                if open_batch:
                    with self.assertRaisesRegex(ValueError, 'still open'):
                        record(root, narrator=lambda: fake([]), clock=lambda: now, fetcher=raw_for, news_db=None)
                else:
                    out = record(root, narrator=lambda: fake([]), clock=lambda: now, fetcher=raw_for, news_db=None)
                    self.assertEqual(out['batch'], len(seals) + 1)
                    seals.append(now)
            elif seals:
                rep = score(root, clock=lambda: now, fetcher=raw_for)
                expect_pending = [i + 1 for i, s in enumerate(seals) if len(sessions_after(s, now)) < HORIZON]
                self.assertEqual(rep['pending_batches'], expect_pending)
                self.assertLessEqual(len(expect_pending), 1, 'Two batches open at once.')
                exits = {r['batch']: r['exit_date'] for r in rep['cases']}
                for i, s in enumerate(seals):
                    if i + 1 in expect_pending:
                        self.assertNotIn(i + 1, matured, 'A matured batch went back to pending.')
                        continue
                    want = sessions_after(s, now)[HORIZON - 1]
                    self.assertEqual(exits[i + 1], want)
                    self.assertEqual(matured.setdefault(i + 1, want), want, 'A matured batch changed its exit day.')
                self.assertEqual(rep['batches_matured'], len(seals) - len(expect_pending))
        self.assertEqual(len(chain(root)[0]), len(seals))
        return len(seals), len(matured)

    def test_random_timelines_follow_the_chart(self):
        totals = [self.timeline(seed) for seed in (1, 2, 3, 4)]
        self.assertTrue(all(sealed >= 2 for sealed, _ in totals), totals)
        self.assertTrue(any(m >= 2 for _, m in totals), totals)

    def test_doc_diagram_matches_the_code(self):
        doc = (ROOT / 'docs/statecharts.md').read_text(encoding='utf-8')
        block = re.search(r'## Forward ledger.*?```mermaid\n(.*?)```', doc, re.S)
        self.assertIsNotNone(block, 'docs/statecharts.md lost its forward ledger diagram.')
        self.assertEqual(block.group(1), statechart(), 'Redraw docs/statecharts.md from qualify.forward.statechart().')


if __name__ == '__main__':
    unittest.main()
