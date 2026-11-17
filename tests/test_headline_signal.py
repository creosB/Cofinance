"""headline-v1 live: judged at ingestion with the screened prompt, scored in Python over exactly the served headlines."""
import json
import math
import tempfile
import unittest
from pathlib import Path

from core import headline_signal as H
from core.news_store import NewsStore
from core.signals import Action, Item, Judgment, Outcome
from qualify import decision_lab as D

NOW = 1_800_000_000.0


def reply(up, down, abstain):
    """llama-server shaped reply: JSON label tokens with top log-probabilities at the label token."""
    alts = [(t, math.log(p)) for t, p in (('UP', up), ('D', down), ('AB', abstain)) if p > 0]
    best = max(alts, key=lambda a: a[1])[0]
    label = {'UP': 'UP', 'D': 'DOWN', 'AB': 'ABSTAIN'}[best]
    tok = lambda t, top=(): {'token': t, 'logprob': 0.0, 'top_logprobs': [{'token': a, 'logprob': lp} for a, lp in top]}
    return {'message': {'content': json.dumps({'label': label})},
            'logprobs': {'content': [tok('{'), tok(' "'), tok('label'), tok('":'), tok(' "'), tok(best, alts)]}}


class FakeRuntime:
    def __init__(self, probs, fail=()):
        self.probs, self.fail, self.calls = probs, set(fail), []

    def __call__(self, system, prompt, **kwargs):
        self.calls.append((system, prompt, kwargs))
        title = prompt.split('Headline: ', 1)[1]
        if title in self.fail:
            raise ValueError('runtime error')
        return reply(*self.probs[title])


class HeadlineSignalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'news.db'
        self.store = NewsStore(str(self.db), clock=lambda: NOW)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add(self, ticker, title, action=Action.DIGEST, direction='bullish', ttl=None):
        item = Item(ticker, 'official_feed', 'https://example.com/' + title.replace(' ', '-'), title, 'body', NOW - 60)
        out = Outcome(item, action, 'test', Judgment(0.9, 0.1, direction, 0.8))
        with self.store.transaction():
            self.store.record([out], NOW if ttl is None else ttl)

    def test_live_code_is_the_screened_code(self):
        self.assertIs(D.CANDIDATES['headline-v1'], H.SYSTEM)
        self.assertIs(D.SCHEMA, H.SCHEMA)
        self.assertIs(D.impact, H.impact)
        self.assertIs(D.label_probs, H.label_probs)
        self.assertEqual(D.headline_prompt({'symbol': 'NVDA', 'title': 'T'}), H.prompt('NVDA', 'T'))
        self.assertEqual(D.TOP_LOGPROBS, H.TOP_LOGPROBS)

    def test_judges_each_live_headline_once_from_asset_and_title_only(self):
        self.add('NVDA', 'NVIDIA wins order', direction='bearish')
        self.add('NVDA', 'NVIDIA recalls chips')
        self.add('AAPL', 'Apple event')
        runtime = FakeRuntime({'NVIDIA wins order': (.7, .1, .2), 'NVIDIA recalls chips': (.1, .8, .1), 'Apple event': (.2, .1, .7)})
        res = H.judge_pending(self.store.conn, runtime, model='fake', clock=lambda: NOW)
        self.assertEqual((res['pending'], res['judged'], res['failed']), (3, 3, 0))
        for system, prompt, kwargs in runtime.calls:
            self.assertEqual(system, H.SYSTEM)
            self.assertRegex(prompt, r'^Asset: [A-Z]+\nHeadline: [^\n]+$')
            for hidden in ('bullish', 'bearish', 'digest', '0.9', 'official'):
                self.assertNotIn(hidden, prompt)
            self.assertEqual((kwargs['response_format'], kwargs['top_logprobs']), (H.SCHEMA, H.TOP_LOGPROBS))
        again = H.judge_pending(self.store.conn, runtime, model='fake', clock=lambda: NOW)
        self.assertEqual((again['pending'], len(runtime.calls)), (0, 3), 'a judged headline is never judged again')

    def test_failed_judgment_writes_nothing_and_is_retried(self):
        self.add('NVDA', 'A')
        self.add('NVDA', 'B')
        runtime = FakeRuntime({'A': (.6, .2, .2), 'B': (.2, .6, .2)}, fail={'B'})
        res = H.judge_pending(self.store.conn, runtime, model='fake', clock=lambda: NOW)
        self.assertEqual((res['judged'], res['failed']), (1, 1))
        runtime.fail.clear()
        res = H.judge_pending(self.store.conn, runtime, model='fake', clock=lambda: NOW)
        self.assertEqual((res['pending'], res['judged']), (1, 1))

    def test_expired_headlines_are_not_judged(self):
        self.add('NVDA', 'Old news', ttl=NOW - 8 * 86400)
        res = H.judge_pending(self.store.conn, FakeRuntime({}), model='fake', clock=lambda: NOW)
        self.assertEqual(res['pending'], 0)

    def test_card_sums_exactly_the_served_headlines(self):
        for t in ('A', 'B', 'C', 'Not served'):
            self.add('NVDA', t)
        runtime = FakeRuntime({'A': (.7, .1, .2), 'B': (.6, .2, .2), 'C': (.1, .1, .8), 'Not served': (.0001, .9, .0999)})
        H.judge_pending(self.store.conn, runtime, model='fake', clock=lambda: NOW)
        c = H.card(self.db, {'NVDA': ['A', 'B', 'C', 'Unjudged']})
        a = c['assets'][0]
        expected = round(sum(H.impact({'UP': math.log(u), 'DOWN': math.log(d), 'ABSTAIN': math.log(x)})
                             for u, d, x in ((.7, .1, .2), (.6, .2, .2), (.1, .1, .8))), 6)
        self.assertAlmostEqual(a['score'], expected, places=5)
        self.assertEqual((a['served'], a['judged'], a['call']), (4, 3, 'UP' if expected >= .5 else 'ABSTAIN'))
        self.assertIsNone([h for h in a['headlines'] if h['title'] == 'Unjudged'][0]['impact'])
        self.assertIn('Experimental', c['note'])
        self.assertIn('not', c['note'].lower())

    def test_call_line_is_the_preregistered_half(self):
        self.assertEqual([H.call_for(x) for x in (.5, .49, -.5, -.49, 0)], ['UP', 'ABSTAIN', 'DOWN', 'ABSTAIN', 'ABSTAIN'])

    def test_no_judgments_or_no_table_means_no_card(self):
        self.assertIsNone(H.card(self.db, {'NVDA': ['A']}))
        self.assertIsNone(H.card(Path(self.tmp.name) / 'missing.db', {'NVDA': ['A']}))
        self.assertIsNone(H.card(self.db, {}))

    def test_collection_skips_without_a_qualified_narrator(self):
        from core.chat_service import ChatService

        class Unqualified:
            def qualified(self):
                return False

            def load(self):
                raise AssertionError('must not load an unqualified narrator')

        service = ChatService.__new__(ChatService)
        service.models = Unqualified()
        self.assertEqual(service.judge_headlines(self.store, lambda: None)['status'], 'narrator_not_qualified')


if __name__ == '__main__':
    unittest.main()
