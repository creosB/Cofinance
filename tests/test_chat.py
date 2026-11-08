import hashlib
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from core.chat_service import ChatService
from core.chat_store import ChatStore
from core.local_models import LocalModels, ModelError, Cancelled, public_host, local_json
from core.market import MarketTools
from core.orchestrator import DataAnalystAgent, NewsResearcherAgent, TeamLeadOrchestrator, EventBus, market_fact_lines
from core.gliner_decide import GlinerQueryRouter
from tests.test_orchestrator import StubGliner, StubTools, StubNewsStore, Stub1BLLM
from ui.dashboard import create_server

REPO=Path(__file__).resolve().parents[1]

class FakeMarket(StubTools):
    def __init__(self,cancel=None):
        super().__init__();self.charts={};self.snapshots={};self.evidence=[];self.cancel=cancel
    def get_snapshot(self,ticker):
        self.snapshots[ticker]={'price':138.5,'pe_ratio':34.2,'source':'Test fixture'}
        self.charts[ticker]={'ticker':ticker,'currency':'USD','source':'Test fixture','fetched_at':'2026-11-07T00:00:00Z','window':'Test fixture','adjustment':'Fixture only','points':[{'time':'2026-11-01T00:00:00Z','close':130},{'time':'2026-11-02T00:00:00Z','close':138.5}]}
        self.evidence.append({'title':'Fixture evidence','url':'https://example.test/fixture','source':'Test fixture','as_of':'2026-11-07T00:00:00Z'})
        return self.snapshots[ticker]
    def plot_history(self,ticker):return 'chart://'+ticker


def service(root,market=FakeMarket):
    return ChatService(REPO,root,market_factory=market,router_factory=lambda:GlinerQueryRouter(StubGliner(),{'NVDA','TSLA','BTC','AMD'}))


def wait(service,job):
    for _ in range(250):  # 5 s: 2 s was flaky under full-suite load (seen 2026-11-08)
        value=service.store.job(job['id'])
        if value['status'] not in {'queued','running','cancelling'} and not service.active and job['id'] not in service.threads:return value
        time.sleep(.02)
    raise AssertionError('Fixture job did not finish')


class TestNumberGateRepairs(unittest.TestCase):
    def test_empty_market_facts_cannot_accept_invented_price(self):
        class Empty(StubTools):
            def get_snapshot(self,ticker):return {}
        class Invented:
            def complete(self,*a,**k):return 'The price is 999.'
        result=DataAnalystAgent(Invented(),Empty()).run(['NVDA'],'fundamental_equity',EventBus(),'test')
        self.assertFalse(result.faithful_numbers);self.assertNotIn('999',result.summary)
        self.assertIn('unavailable',result.summary.lower())

    def test_narrator_outage_uses_tool_numbers_not_exception_text(self):
        class Down:
            def complete(self,*a,**k):raise RuntimeError('sensitive internals')
        result=DataAnalystAgent(Down(),StubTools()).run(['NVDA'],'fundamental_equity',EventBus(),'test')
        self.assertFalse(result.faithful_numbers);self.assertIn('138.5',result.summary);self.assertNotIn('sensitive',result.summary)

    def test_empty_news_is_missing_coverage_not_an_all_clear(self):
        class Empty:
            def get_gated_signals(self,*a,**k):return []
        result=NewsResearcherAgent(Stub1BLLM(),Empty()).run(['NVDA'],EventBus(),'test')
        self.assertFalse(result.faithful_numbers);self.assertIn('does not establish',result.summary)

    def test_watchlist_intent_is_not_write_permission(self):
        from core.gliner_decide import RoutedQuery
        class Router:
            def route(self,*a):return RoutedQuery('watchlist_add',.9,'swing_week',['NVDA'])
        tools=StubTools();llm=Stub1BLLM()
        result=TeamLeadOrchestrator(Router(),DataAnalystAgent(llm,tools),NewsResearcherAgent(llm,StubNewsStore()),llm,tools).run('Track NVDA')
        self.assertEqual(tools.watchlist,[])
        self.assertIn('WATCHLIST_ACTION_REQUIRED',[e.event_type for e in result.events])


class TestHeadlessMarket(unittest.TestCase):
    def payload(self):
        return {'chart':{'result':[{'meta':{'regularMarketPrice':100,'regularMarketTime':1794000000,'currency':'USD','fiftyTwoWeekHigh':200,'fiftyTwoWeekLow':50},'timestamp':[1793800000,1793900000,1794000000],'indicators':{'quote':[{'close':[90,None,100]}]}}]}}
    def test_equity_chart_uses_real_fields_without_made_up_fundamentals(self):
        tools=MarketTools(fetch=lambda url:self.payload());result=tools.get_snapshot('NVDA')
        self.assertEqual(result['price'],100);self.assertNotIn('pe_ratio',result);self.assertEqual(len(tools.charts['NVDA']['points']),2)
        self.assertIn('finance.yahoo.com',tools.evidence[0]['url'])
    def test_failed_source_never_generates_random_demo_prices(self):
        def down(url):raise OSError('fixture outage')
        result=MarketTools(fetch=down).get_snapshot('NVDA')
        self.assertEqual(result['status'],'unavailable');self.assertNotIn('price',result)
    def test_crypto_never_calls_52_week_high_an_ath_or_substitutes_sentiment(self):
        def fetch(url):
            if 'alternative.me' in url:raise OSError('outage')
            return self.payload()
        result=MarketTools(fetch=fetch).get_snapshot('BTC')
        self.assertEqual(result['drawdown_from_52_week_high_pct'],50)
        self.assertNotIn('fear_greed_index',result);self.assertIn('not an all-time high',result['ath_drawdown'])
    def routed(self, fundamentals=True, history=True, currency='USD'):
        def point(date, raw, cur=currency, period='TTM'):
            return {'asOfDate': date, 'periodType': period, 'currencyCode': cur, 'reportedValue': {'raw': raw}}
        series = {'trailingPeRatio': [point('2026-11-05', 22.0, None), point('2026-11-06', 23.595449, None)],
                  'trailingMarketCap': [point('2026-11-06', 4506796080000.0)],
                  'trailingDilutedEPS': [point('2026-07-31', 7.91)],
                  'trailingTotalRevenue': [point('2026-07-31', 302970000000.0, 'EUR')]}  # wrong currency: dropped
        def fetch(url):
            if 'fundamentals-timeseries' in url:
                if not fundamentals:raise OSError('outage')
                return {'timeseries': {'result': [{'meta': {'type': [k]}, k: v} for k, v in series.items()], 'error': None}}
            if 'range=max' in url:
                if not history:raise OSError('outage')
                ts = [946684800 + i * 2592000 for i in range(24)]
                return {'chart': {'result': [{'meta': {}, 'timestamp': ts, 'indicators': {'quote': [{'high': [50 + i for i in range(23)] + [125.0]}]}}]}}
            if 'alternative.me' in url:raise OSError('outage')
            return self.payload()
        return fetch

    def test_fundamentals_keep_their_dates_and_never_cross_currencies(self):
        snap = MarketTools(fetch=self.routed()).get_snapshot('NVDA')
        self.assertEqual((snap['pe_ratio'], snap['market_cap'], snap['eps']), (23.5954, 4506796080000, 7.91))
        self.assertNotIn('revenue_ttm', snap)  # reported in EUR, quote in USD
        self.assertEqual(snap['fundamentals_as_of']['pe_ratio'], {'as_of': '2026-11-06', 'period': 'TTM'})  # latest, not first
        self.assertEqual(snap['fundamentals_status'], 'Available')
        lines = market_fact_lines('NVDA', snap)
        self.assertIn('Market cap: 4.51 trillion USD', lines)
        self.assertIn('Trailing P/E: 23.60', lines)
        self.assertIn('Trailing EPS: 7.91 USD', lines)
        self.assertNotIn('Fundamentals: not available from this source', lines)
        self.assertFalse([l for l in lines if '2026' in l])  # dates are provenance, never narrator facts

    def test_all_time_high_is_full_history_and_labelled(self):
        snap = MarketTools(fetch=self.routed()).get_snapshot('BTC')
        self.assertEqual((snap['all_time_high'], snap['drawdown_from_all_time_high_pct']), (125.0, 20.0))
        self.assertEqual(snap['history_start'], '2000-01')
        self.assertIn('since 2000-01', snap['ath_drawdown'])
        self.assertIn('All-time high in source history: 125.0 USD', market_fact_lines('BTC', snap))

    def test_extra_sources_fail_visibly_and_never_block_the_price(self):
        snap = MarketTools(fetch=self.routed(fundamentals=False, history=False)).get_snapshot('NVDA')
        self.assertEqual(snap['price'], 100)
        self.assertFalse({'pe_ratio', 'market_cap', 'eps', 'all_time_high'} & set(snap))
        self.assertEqual(snap['fundamentals_status'], 'Unavailable from this source')
        self.assertIn('not an all-time high', snap['ath_drawdown'])
        self.assertIn('Fundamentals: not available from this source', market_fact_lines('NVDA', snap))

    def test_symbol_cannot_be_a_path_or_url(self):
        for ticker in ('../secrets','https://attacker.test','NVDA&url=x','nvda'):
            with self.assertRaises(ValueError):MarketTools().get_snapshot(ticker)
    def test_research_tools_have_no_watchlist_write_authority(self):
        with self.assertRaises(RuntimeError):MarketTools().add_to_watchlist('NVDA')


class TestModelManager(unittest.TestCase):
    def test_download_hosts_are_fixed_public_distributors(self):
        self.assertTrue(public_host('https://huggingface.co/official/file'))
        for url in ('http://huggingface.co/file','https://huggingface.co.attacker.test/x','https://127.0.0.1/x','file:///secret','https://user:pass@github.com/x'):
            self.assertFalse(public_host(url))
    def test_inference_cannot_be_redirected_to_hosted_providers(self):
        with self.assertRaises(ModelError):local_json('https://api.example.test/chat',{})
    def test_unsupported_model_or_action_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            models=LocalModels(Path(root),REPO)
            with self.assertRaises(ValueError):models.select('hosted-model')
            with self.assertRaises(ValueError):models.launch_task('run_shell')
            with self.assertRaises(ModelError):models.select('lfm3-1.7b-q4km')
    def test_hash_mismatch_is_not_promoted_to_a_ready_download(self):
        with tempfile.TemporaryDirectory() as root:
            models=LocalModels(Path(root),REPO);target=Path(root)/'fixture.gguf'
            target.with_suffix('.gguf.part').write_bytes(b'bad')
            with self.assertRaises(ModelError):models.fetch('https://huggingface.co/x',target,3,hashlib.sha256(b'yes').hexdigest(),0)
            self.assertFalse(target.exists())
    def test_completed_partial_is_verified_before_atomic_promotion(self):
        with tempfile.TemporaryDirectory() as root:
            models=LocalModels(Path(root),REPO);target=Path(root)/'fixture.gguf'
            target.with_suffix('.gguf.part').write_bytes(b'yes')
            models.fetch('https://huggingface.co/x',target,3,hashlib.sha256(b'yes').hexdigest(),0)
            self.assertEqual(target.read_bytes(),b'yes')
    def test_failed_or_stale_qualification_never_enables_picker(self):
        with tempfile.TemporaryDirectory() as root:
            models=LocalModels(Path(root),REPO)
            models.state['qualification']={'passed':True,'fingerprint':'different'}
            self.assertFalse(models.qualified())
    def test_download_retry_resumes_exact_partial_range(self):
        class Response(io.BytesIO):
            status=206;headers={'Content-Range':'bytes 2-3/4'}
        with tempfile.TemporaryDirectory() as root:
            models=LocalModels(Path(root),REPO);target=Path(root)/'fixture.gguf';target.with_suffix('.gguf.part').write_bytes(b'ab')
            with patch('core.local_models.public_open',return_value=Response(b'cd')) as opened:
                models.fetch('https://huggingface.co/x',target,4,hashlib.sha256(b'abcd').hexdigest(),0)
                self.assertEqual(opened.call_args[0][1],{'Range':'bytes=2-'})
            self.assertEqual(target.read_bytes(),b'abcd')


class TestChatPersistence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.service=service(Path(self.temp.name))
        self.session=self.service.store.create_session(uuid.uuid4().hex)
    def tearDown(self):self.service.close();self.temp.cleanup()
    def submit(self,query='Review NVDA',request=None):return self.service.submit(self.session['id'],query,request or uuid.uuid4().hex,'facts')
    def test_fixture_research_is_persisted_and_reloadable(self):
        job=self.submit();self.assertEqual(wait(self.service,job)['status'],'degraded')
        session=ChatStore(Path(self.temp.name)/'chat_v2.db').session(self.session['id'])
        self.assertEqual([m['role'] for m in session['messages']],['user','assistant'])
        self.assertEqual(session['messages'][-1]['payload']['mode'],'facts')
        self.assertTrue(session['messages'][-1]['payload']['charts'])
        self.assertEqual(self.service.store.watchlist(),[])
    def test_same_request_is_idempotent_and_mismatched_reuse_rejected(self):
        request=uuid.uuid4().hex;one=self.submit(request=request);two=self.submit(request=request)
        self.assertEqual(one['id'],two['id']);wait(self.service,one)
        with self.assertRaises(ValueError):self.submit('Different content',request)
        self.assertEqual(len(self.service.store.session(self.session['id'])['messages']),2)
    def test_followup_uses_only_its_own_session_assets(self):
        job=self.submit();wait(self.service,job)
        follow=self.submit('What about its price?');wait(self.service,follow)
        self.assertEqual(self.service.store.session(self.session['id'])['tickers'],['NVDA'])
        other=self.service.store.create_session(uuid.uuid4().hex)
        job=self.service.submit(other['id'],'What about its price?',uuid.uuid4().hex,'facts');wait(self.service,job)
        self.assertEqual(self.service.store.session(other['id'])['messages'][-1]['payload']['kind'],'clarification')
    def test_invalid_requests_do_not_create_messages(self):
        for query in ('','x'*3001,'bad\x00input'):
            with self.assertRaises(ValueError):self.submit(query)
        self.assertEqual(self.service.store.session(self.session['id'])['messages'],[])
    def test_restart_marks_unfinished_jobs_interrupted_without_replaying_them(self):
        run=uuid.uuid4().hex
        self.service.store.create_job(run,uuid.uuid4().hex,self.session['id'],'NVDA','facts');self.service.store.recover()
        self.assertEqual(self.service.store.job(run)['status'],'interrupted')
        self.assertEqual(len(self.service.store.session(self.session['id'])['messages']),1)
    def test_watchlist_only_changes_on_explicit_action(self):
        self.service.store.add_watch('NVDA');self.service.store.add_watch('NVDA')
        self.assertEqual(len(self.service.store.watchlist()),1)
    def test_startup_cannot_overwrite_a_cancellation_or_terminal_state(self):
        run=uuid.uuid4().hex
        self.service.store.create_job(run,uuid.uuid4().hex,self.session['id'],'NVDA','facts')
        self.service.store.progress(run,'Stop requested','cancelling')
        self.service.store.progress(run,'Late startup','running')
        self.assertEqual(self.service.store.job(run)['status'],'cancelling')
        self.service.store.progress(run,'Stopped','cancelled')
        self.service.store.progress(run,'Late startup again','running')
        self.assertEqual(self.service.store.job(run)['status'],'cancelled')

    def test_cancel_is_honest_and_active_slot_is_released(self):
        class Slow(FakeMarket):
            def get_snapshot(self,ticker):time.sleep(.15);return super().get_snapshot(ticker)
        self.service.market_factory=Slow
        job=self.submit();stopping=self.service.stop(job['id'])
        self.assertIn(stopping['status'],{'cancelling','cancelled'})
        self.assertEqual(wait(self.service,job)['status'],'cancelled')
        self.assertEqual(len(self.service.store.session(self.session['id'])['messages']),1)


class TestChatAPI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);self.service=service(root)
        self.dist=root/'dist';self.dist.mkdir();(self.dist/'index.html').write_text('<div id="root"></div>')
        self.server=create_server(port=0,dist=self.dist,trace_dir=root/'traces',corpus_dir=root/'missing',service=self.service)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.token=self.request('/api/state')[1]['csrf']
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.service.close();self.temp.cleanup()
    def request(self,path,body=None,headers=None,raw=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        data=None if body is None else json.dumps(body)
        h={} if body is None else {'Origin':self.origin,'Content-Type':'application/json','X-Cofinance-CSRF':getattr(self,'token','')}
        h.update(headers or {})
        conn.request('GET' if body is None else 'POST',path,body=raw if raw is not None else data,headers=h)
        response=conn.getresponse();status=response.status;value=json.loads(response.read());conn.close();return status,value
    def test_actions_require_matching_origin_and_csrf_even_on_loopback(self):
        body={'request_id':uuid.uuid4().hex}
        for headers in ({'Origin':'https://attacker.test'},{'X-Cofinance-CSRF':''},{'Sec-Fetch-Site':'cross-site'},{'Origin':'http://localhost:'+str(self.server.server_port)}):
            self.assertEqual(self.request('/api/sessions',body,headers)[0],403)
        self.assertEqual(self.service.store.sessions(),[])
    def test_cross_site_reads_cannot_get_a_request_token(self):
        self.assertEqual(self.request('/api/state',headers={'Sec-Fetch-Site':'cross-site'})[0],403)
    def test_malformed_json_and_unknown_fields_are_rejected(self):
        self.assertEqual(self.request('/api/sessions',{},raw='bad json')[0],400)
        self.assertEqual(self.request('/api/sessions',{'request_id':uuid.uuid4().hex,'extra':1})[0],400)
        self.assertEqual(self.request('/api/sessions',{'request_id':'../outside'})[0],400)
    def test_api_creates_conversation_and_research_without_legacy_tables(self):
        status,session=self.request('/api/sessions',{'request_id':uuid.uuid4().hex});self.assertEqual(status,200)
        status,job=self.request('/api/chat',{'session_id':session['id'],'query':'NVDA','request_id':uuid.uuid4().hex,'model':'facts'})
        self.assertEqual(status,202);wait(self.service,job)
        self.assertEqual(len(self.request('/api/conversation?id='+session['id'])[1]['messages']),2)


if __name__=='__main__':unittest.main()
