import json
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from core.chat_service import ChatService
from core.claim_provenance import check_claim_provenance
from core.gliner_decide import GlinerQueryRouter
from core.hymemory_port import HyMemoryLite
from core.market import MarketTools, resolve_yahoo_symbol, valid_symbol
from core.local_models import ModelError
from core.router_worker import RouterProcess
from tests.test_chat import FakeMarket, wait
from tests.test_open_chat import RecordingNarratorModels

REPO = Path(__file__).resolve().parents[1]


def listing(symbol, name, kind, exchange, score):
    return dict(symbol=symbol, shortname=name, quoteType=kind, exchange=exchange, score=score)


KANDO = [listing('KANDOA.L', 'Kando Industries', 'EQUITY', 'LSE', 51000),
         listing('KANDOB.L', 'Kando Logistics', 'EQUITY', 'LSE', 50000)]
VEL = listing('VEL', 'Vel Portfolio Fund', 'ETF', 'PCX', 10_000_000)
THY = [listing('THY', 'Toews Agility Shares', 'ETF', 'PCX', 10_009_500),
       listing('THYAO.IS', 'Turkish Airlines', 'EQUITY', 'IST', 20162)]


class ModelFixture:
    def classify_text(self, text, schema, **kwargs):
        intent = 'compare_assets' if text.startswith('Compare') else 'open_conversation' if 'previous close' in text else 'price_snapshot'
        return {'intent': {'label': intent, 'confidence': .9},
                'horizon': {'label': 'intraday', 'confidence': .9}}

    def extract_entities(self, text, entity_types):
        return {'entities': {'company': [name for name in ('VEL', 'Kando', 'THY', 'Hoku', 'Beck') if name in text]}}


class TestResearchAcceptance(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.searches = []
        self.quotes_read = []
        parent = self
        provider = {'VEL': [], 'Kando': KANDO, 'THY': THY,
                    'Hoku': [listing('7203.T', 'Hoku Auto', 'EQUITY', 'JPX', 51000)],
                    '7203.T': [listing('7203.T', 'Hoku Auto', 'EQUITY', 'JPX', 51000)],
                    'Beck': [listing('BECK.L', 'Beck Fund', 'ETF', 'LSE', 51000)]}

        def fetch(url):
            term = parse_qs(urlsplit(url).query)['q'][0]
            parent.searches.append(term)
            return {'quotes': provider[term]}

        class Market(FakeMarket):
            def resolve_symbol(self, term, context_query=''):
                return MarketTools(fetch=fetch).resolve_symbol(term, context_query)

            def get_snapshot(self, ticker):
                parent.quotes_read.append(ticker)
                data = super().get_snapshot(ticker)
                currency = {'THYAO.IS': 'TRY', '7203.T': 'JPY', 'BECK.L': 'GBp'}.get(ticker, 'USD')
                data.update(ticker=ticker, name='Turkish Airlines' if ticker == 'THYAO.IS' else ticker,
                            currency=currency, status='available', previous_session_close=130.0,
                            previous_session_date='2026-11-26')
                self.charts[ticker]['currency'] = currency
                return data

        models = RecordingNarratorModels()
        models.state['selected'] = 'facts'
        self.svc = ChatService(REPO, self.root, models=models, market_factory=Market,
                               router_factory=lambda: GlinerQueryRouter(ModelFixture(), {'NVDA', 'AMD', 'MSFT'}))
        self.addCleanup(self.svc.close)
        self.sid = self.svc.store.create_session(uuid.uuid4().hex)['id']

    def ask(self, question):
        job = self.svc.submit(self.sid, question, uuid.uuid4().hex, 'facts')
        wait(self.svc, job)
        trace = json.loads((self.root / 'traces' / (job['id'] + '.json')).read_text(encoding='utf-8'))
        session = self.svc.store.session(self.sid)
        return session['messages'][-1], trace, session

    def test_explicit_lookup_miss_never_reuses_the_previous_asset(self):
        self.ask('What is NVDA trading at?')
        self.quotes_read.clear()
        msg, trace, session = self.ask('What is VEL trading at?')
        self.assertEqual(self.searches, ['VEL'])
        self.assertEqual(msg['payload']['kind'], 'clarification', 'An explicit lookup miss is not an asset-free follow-up')
        self.assertIn('VEL', msg['content'])
        self.assertEqual(self.quotes_read, [])
        self.assertEqual(session['tickers'], ['NVDA'])
        self.assertFalse(any(s['name'] == 'Reuse conversation assets' for s in trace['spans']))

    def test_asset_free_followup_still_reuses_the_previous_asset(self):
        self.ask('What is NVDA trading at?')
        msg, trace, _ = self.ask('What is the previous close?')
        self.assertEqual(msg['payload']['tickers'], ['NVDA'])
        self.assertEqual(self.searches, [])
        self.assertTrue(any(s['name'] == 'Reuse conversation assets' for s in trace['spans']))
        self.assertEqual(msg['payload']['market_answer_note'], 'NVDA previous-session close: 130.0 USD on 2026-11-26.')

    def test_comparison_never_silently_omits_an_ambiguous_leg(self):
        msg, trace, _ = self.ask('Compare NVDA and Kando')
        self.assertEqual(self.searches, ['Kando'])
        self.assertEqual(msg['payload']['kind'], 'clarification')
        for symbol in ('KANDOA.L', 'KANDOB.L'):
            self.assertIn(symbol, msg['content'])
        self.assertEqual(self.quotes_read, [])
        self.assertEqual(trace['summary']['flow'], 'clarification')

    def test_comparison_never_omits_a_third_ambiguous_leg(self):
        msg, _, _ = self.ask('Compare NVDA, AMD, and Kando')
        self.assertEqual(self.searches, ['Kando'])
        self.assertEqual(msg['payload']['kind'], 'clarification')
        self.assertIn('KANDOB.L', msg['content'])
        self.assertEqual(self.quotes_read, [])

    def test_cancelled_slow_provider_cannot_save_a_reply_or_seed_another_session(self):
        begun, release = threading.Event(), threading.Event()
        calls = []
        original = self.svc.market_factory
        market_fixture = original()

        def provider(url):
            calls.append(url)
            begun.set()
            if not release.wait(5):
                raise AssertionError('The test did not release the provider')
            return {}

        def market(cancel=None):
            return MarketTools(fetch=provider, cancel=cancel)

        self.svc.market_factory = market
        job = self.svc.submit(self.sid, 'Review NVDA', uuid.uuid4().hex, 'facts')
        second = self.svc.store.create_session(uuid.uuid4().hex)['id']
        try:
            self.assertTrue(begun.wait(4))
            self.assertEqual(self.svc.stop(job['id'])['status'], 'cancelling')
            with self.assertRaisesRegex(ModelError, 'already active'):
                self.svc.submit(second, 'Review MSFT', uuid.uuid4().hex, 'facts')
        finally:
            release.set()
        self.assertEqual(wait(self.svc, job)['status'], 'cancelled')
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(m['role']=='assistant' for m in self.svc.store.session(self.sid)['messages']))
        self.assertEqual(self.svc.store.session(second)['messages'], [])
        self.svc.market_factory = lambda cancel=None: market_fixture
        next_job = self.svc.submit(second, 'Review MSFT', uuid.uuid4().hex, 'facts')
        wait(self.svc, next_job)
        self.assertEqual(self.svc.store.session(second)['messages'][-1]['payload']['tickers'], ['MSFT'])

    def test_a_timed_out_quote_cannot_reuse_yesterdays_number_in_a_decision(self):
        self.ask('What is NVDA trading at?')

        def timeout(_url):
            raise TimeoutError('test transport details')

        self.svc.market_factory = lambda cancel=None: MarketTools(fetch=timeout, cancel=cancel)
        msg, trace, _ = self.ask('Should I buy NVDA now?')
        self.assertEqual(trace['status'], 'degraded')
        self.assertIn('does not establish whether to buy', msg['content'])
        self.assertIn('market-data provider timed out', msg['content'])
        self.assertNotIn('test transport details', msg['content'])
        self.assertNotIn('138.5', msg['content'])
        self.assertEqual(msg['payload']['snapshots']['NVDA']['quote_status']['failure'], 'provider_timeout')
        self.assertNotIn('price', msg['payload']['snapshots']['NVDA'])
        self.assertEqual(msg['payload']['charts'], [])

    def test_comparison_binds_market_and_type_per_mention(self):
        msg, _, _ = self.ask('Compare Hoku Japanese stock and Beck UK ETF')
        self.assertCountEqual(self.searches, ['Hoku', 'Beck'])
        self.assertCountEqual(msg['payload']['tickers'], ['7203.T', 'BECK.L'])
        self.assertCountEqual(self.quotes_read, ['7203.T', 'BECK.L'])
        self.assertEqual({chart['ticker']: chart['currency'] for chart in msg['payload']['charts']},
                         {'7203.T': 'JPY', 'BECK.L': 'GBp'})

    def test_comparison_with_a_failed_quote_is_explicitly_incomplete(self):
        original = self.svc.market_factory

        def partial(cancel=None):
            tools = original(cancel=cancel)
            available = tools.get_snapshot

            def snapshot(ticker):
                if ticker != 'BECK.L':
                    return available(ticker)
                row = {'ticker': ticker, 'status': 'unavailable', 'error': 'fixture quote outage'}
                tools.snapshots[ticker] = row
                return row

            tools.get_snapshot = snapshot
            return tools

        self.svc.market_factory = partial
        msg, trace, _ = self.ask('Compare Hoku Japanese stock and Beck UK ETF')
        self.assertIn('The comparison is incomplete', msg['content'])
        self.assertIn('BECK.L', msg['payload']['market_answer_note'])
        self.assertEqual(trace['status'], 'degraded')
        self.assertTrue(any(s['name'] == 'Withhold incomplete comparison' for s in trace['spans']))
        self.assertEqual([chart['ticker'] for chart in msg['payload']['charts']], ['7203.T'])

    def test_thy_known_control_checks_saved_answer_and_chart(self):
        msg, trace, _ = self.ask('THY, Turkish stock. Should I buy now?')
        self.assertEqual(self.searches, ['THY'])
        self.assertEqual(self.quotes_read, ['THYAO.IS'])
        self.assertEqual(msg['payload']['tickers'], ['THYAO.IS'])
        self.assertIn('TRY', msg['content'])
        self.assertTrue(msg['content'].startswith('The available evidence does not establish whether to buy'))
        self.assertEqual(msg['payload']['decision_assessment']['status'], 'insufficient_evidence')
        self.assertTrue(any(s['name'] == 'Decision evidence assessment' for s in trace['spans']))
        self.assertEqual(msg['payload']['charts'][0]['currency'], 'TRY')
        self.assertTrue(any(s['name'] == 'Resolve asset symbol' for s in trace['spans']))

    def test_explicit_market_and_type_constraints_do_not_fall_back(self):
        cases = [('VEL', 'VEL, Turkish stock', [VEL]),
                 ('ORION', 'ORION German ETF price', [listing('ORION.DE', 'Orion Works', 'EQUITY', 'GER', 50000),
                                                     listing('ORION', 'Orion Fund', 'ETF', 'PCX', 50000)])]
        for term, query, rows in cases:
            with self.subTest(query=query):
                self.assertIsNone(resolve_yahoo_symbol(term, lambda _: {'quotes': rows}, context_query=query))

    def test_listing_country_does_not_resolve_a_same_market_tie(self):
        for query in ('Kando stock price', 'Kando UK stock price'):
            with self.subTest(query=query):
                result = resolve_yahoo_symbol('Kando', lambda _: {'quotes': KANDO}, context_query=query)
                self.assertTrue(result['ambiguous'])
                self.assertEqual({a['symbol'] for a in result['alternatives']}, {'KANDOA.L', 'KANDOB.L'})

    def test_numeric_leading_symbols_need_exchange_qualification(self):
        for symbol in ('7203.T', '0700.HK', '005930.KS', 'BRK-B', 'THYAO.IS'):
            with self.subTest(symbol=symbol):
                self.assertEqual(valid_symbol(symbol), symbol)
        for symbol in ('7203', '123.45', '0700..HK', '0700.HK/else'):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                valid_symbol(symbol)

    def test_numeric_listing_in_a_raw_question_uses_a_provider_lookup(self):
        msg, _, _ = self.ask('What is 7203.T trading at?')
        self.assertEqual(self.searches, ['7203.T'])
        self.assertEqual(msg['payload']['tickers'], ['7203.T'])
        self.assertEqual(msg['payload']['charts'][0]['currency'], 'JPY')

    def test_currency_denomination_is_not_just_a_number_category(self):
        for currency, accept, reject in [('JPY', ('JPY', 'yen'), ('USD', 'EUR', '$')),
                                         ('GBp', ('GBp', 'GBX', 'pence'), ('GBP', 'pounds', '£')),
                                         ('USD', ('USD', '$'), ('CAD', 'EUR'))]:
            facts = {'KANDO.L': {'ticker': 'KANDO.L', 'price': 123.45, 'currency': currency, 'status': 'available'}}
            for marker in (*accept, *reject):
                with self.subTest(evidence=currency, claim=marker):
                    amount = marker + '123.45' if marker in '$£' else '123.45 ' + marker
                    conflicts = check_claim_provenance(f'KANDO.L last traded at {amount}.', facts)
                    if marker in accept:
                        self.assertEqual(conflicts, [])
                    else:
                        self.assertEqual([c['kind'] for c in conflicts], ['currency_mismatch'])

    @unittest.skipUnless(os.name == 'nt', 'Windows containment contract')
    def test_router_start_does_not_keep_a_child_that_failed_containment(self):
        worker = RouterProcess(REPO, self.root / 'router_logs')
        process = Mock(stdin=None, stdout=None, stderr=None)
        process.poll.return_value = None
        with patch('core.process_guard.subprocess.Popen', return_value=process), \
                patch('core.process_guard.bind_child', return_value=False), \
                patch.object(worker, 'receive') as receive:
            with self.assertRaisesRegex(ModelError, 'could not be contained'):
                worker.start()
        receive.assert_not_called()
        process.kill.assert_called_once_with()
        self.assertIsNone(worker.process)

    def test_clear_all_reports_real_scrub_failure_and_new_request_retries(self):
        hm = HyMemoryLite(REPO, self.root)
        self.addCleanup(hm.close)
        cache = hm.base / 'store' / 'data' / 'cache.db'
        cache.parent.mkdir(parents=True)
        self.svc.delete_conversation(self.sid, uuid.uuid4().hex)
        keyword, _ = self.svc.backends()
        self.svc.backends = lambda: (keyword, hm)
        connection = sqlite3.connect(cache)
        self.addCleanup(connection.close)
        marker = 'fixture_cleanup_marker'
        with connection:
            connection.execute('CREATE TABLE memory_operations_v2 (content TEXT)')
            connection.execute('INSERT INTO memory_operations_v2 VALUES (?)', (marker,))
        request_id = uuid.uuid4().hex
        failed = self.svc.clear_conversations(request_id)
        self.assertTrue(failed['error'], 'Clear All must not report success if a backend rolled back')
        self.assertGreater(failed['sweep_pending'], 0)
        self.assertEqual(connection.execute('SELECT content FROM memory_operations_v2').fetchone(), (marker,))
        with connection:
            for name in ('pipeline_logs_v2', 'write_outcomes', 'episode_state'):
                connection.execute(f'CREATE TABLE {name} (content TEXT)')
        self.assertEqual(self.svc.clear_conversations(request_id), failed)
        result = self.svc.clear_conversations(uuid.uuid4().hex)
        self.assertFalse(result['error'])
        self.assertEqual(result['sweep_pending'], 0)
        self.assertIsNone(connection.execute('SELECT content FROM memory_operations_v2').fetchone())
