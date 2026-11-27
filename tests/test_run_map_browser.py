import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core.run_trace import TraceRecorder,run_traced_team,run_traced_clock1
from core.orchestrator import DataAnalystAgent,NewsResearcherAgent,TeamLeadOrchestrator
from core.gliner_decide import GlinerQueryRouter
from core.signals import Item,Judgment
from core.collector import FetchResult
from tests.execution_proof import capture_execution, expect_rendered_trace, require_clean_execution
from tests.test_orchestrator import Stub1BLLM,StubGliner,StubTools,StubNewsStore
from ui.dashboard import create_server,DIST
from ui.workbench import read_trace


@unittest.skipUnless(os.environ.get('COFINANCE_BROWSER_TESTS')=='1','installed Chrome browser tests are opt-in')
class TestRunMapBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.traces=self.root/'traces'
        tools=StubTools();llm=Stub1BLLM(hallucinate_lead=True);llm.model_id='fixture/narrator'
        orch=TeamLeadOrchestrator(GlinerQueryRouter(StubGliner(),{'NVDA','TSLA'}),DataAnalystAgent(llm,tools),NewsResearcherAgent(llm,StubNewsStore()),llm,tools)
        self.trace=TraceRecorder('Browser map fixture',mode='fixture',directory=self.traces)
        run_traced_team(orch,'Review NVDA',self.trace)
        self.server=create_server(port=0,trace_dir=self.traces,corpus_dir=self.root/'none')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.page=self.browser.new_page(viewport={'width':1440,'height':1000});self.errors=[]
        self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.goto('http://127.0.0.1:'+str(self.server.server_port),wait_until='domcontentloaded')
        tools = self.page.locator('.workspace-tools')
        tools.wait_for()
        if not tools.evaluate('e => e.open'):
            tools.locator('summary').click()
        self.page.locator('.sidebar nav').get_by_role('button',name='Execution',exact=False).click()
        self.page.wait_for_selector('[data-map-stage="router"]')
    def tearDown(self):
        self.page.close();self.server.shutdown();self.server.server_close();self.thread.join(timeout=2);self.temp.cleanup()
        self.assertEqual(self.errors,[])
    def open_running_trace(self, title):
        trace = TraceRecorder(title, directory=self.traces)
        trace.event('Fetch quote', 'tool', {'price': 23.0})
        self.page.get_by_role('button', name='Refresh local data').click()
        self.page.locator('.run-entry').filter(has_text=title).click()
        expect_rendered_trace(self.page, read_trace(self.traces, trace.run_id))
        return trace

    @unittest.skipUnless(os.name == 'nt', 'Windows readers can block atomic replacement')
    def test_open_execution_reaches_the_saved_terminal_revision_after_windows_contention(self):
        trace = self.open_running_trace('Windows reader contention')
        before = read_trace(self.traces, trace.run_id)
        destination = self.traces / (trace.run_id + '.json')
        with destination.open('rb') as reader, patch('core.run_trace.time.sleep', side_effect=lambda _: reader.close()) as retry:
            trace.finish(summary={'answer': 'Published after the reader released the file.'})
        retry.assert_called_once()
        saved = require_clean_execution(self.page, self.traces, trace.run_id, mode='live')
        self.assertNotEqual(saved['revision'], before['revision'])
        self.assertEqual(saved['status'], 'complete')

    def test_capture_refuses_a_stale_trace_and_the_complete_view_shows_storage_loss(self):
        from playwright.sync_api import expect

        trace = self.open_running_trace('Final trace could not be written')
        with patch('core.run_trace.replace_trace', side_effect=PermissionError('fixture replacement failed')):
            trace.finish()
        self.assertEqual(trace.data['status'], 'complete')
        with self.assertRaisesRegex(AssertionError, 'trace_not_terminal'):
            capture_execution(self.page, self.traces, trace.run_id)
        trace.flush()
        saved = read_trace(self.traces, trace.run_id)
        self.assertEqual(saved['status'], 'complete')
        expect_rendered_trace(self.page, saved)
        expect(self.page.get_by_text('Trace incomplete: 1 save error.')).to_be_visible()
        expect(self.page.locator('.error-banner')).to_have_count(0)
        with self.assertRaisesRegex(AssertionError, 'trace_storage_errors'):
            capture_execution(self.page, self.traces, trace.run_id)

    def test_capture_accepts_a_complete_degraded_trace_but_blocks_a_visible_warning(self):
        saved = require_clean_execution(self.page, self.traces, self.trace.run_id, mode='fixture')
        self.assertEqual(saved['status'], 'degraded')
        image = capture_execution(self.page, self.traces, self.trace.run_id, mode='fixture')
        self.assertTrue(image.startswith(b'\x89PNG\r\n\x1a\n'))
        self.page.locator('.mode-notice').evaluate("el => el.append('Trace incomplete: simulated render-only warning')")
        with self.assertRaisesRegex(AssertionError, 'Trace incomplete:'):
            capture_execution(self.page, self.traces, self.trace.run_id, mode='fixture')

    def test_capture_rejects_an_api_revision_that_disagrees_with_disk(self):
        def stale_response(directory, run_id):
            saved = read_trace(directory, run_id)
            return {**saved, 'revision': saved['revision'] ^ 1}

        with patch('ui.workbench.read_trace', side_effect=stale_response):
            with self.assertRaisesRegex(AssertionError, 'Execution API disagrees with the saved trace: revision'):
                capture_execution(self.page, self.traces, self.trace.run_id, mode='fixture')

    def test_map_links_route_model_call_and_rejected_synthesis(self):
        self.page.locator('[data-map-stage="router"]').click()
        self.assertIn('Routing decision',self.page.locator('.inspector').inner_text())
        self.assertIn('thesis review',self.page.locator('.inspector').inner_text())
        self.page.locator('.map-observation-list').get_by_role('button',name='Classify heads',exact=False).click()
        self.assertIn('Decision-model output',self.page.locator('.inspector').inner_text())
        self.page.locator('[data-map-stage="guard"]').click()
        review=self.page.get_by_role('region',name='Number validation evidence')
        if not review.count():review=self.page.locator('.guard-review')
        self.assertIn('Candidate replaced',review.inner_text())
        self.assertIn('999',review.locator('.rejected-numbers').inner_text())
        review.locator('summary',has_text='Fallback actually used').click()
        self.assertIn('138.5',review.locator('details').last.inner_text())
        self.assertNotIn('999',review.locator('details').last.inner_text())
    def test_not_dispatched_is_distinct_from_a_successful_role(self):
        class Price(StubGliner):
            def classify_text(self,*a,**k):return {'intent':{'label':'price_snapshot','confidence':.7},'horizon':'intraday'}
        llm=Stub1BLLM();llm.model_id='fixture/narrator';tools=StubTools()
        orch=TeamLeadOrchestrator(GlinerQueryRouter(Price(),{'NVDA'}),DataAnalystAgent(llm,tools),NewsResearcherAgent(llm,StubNewsStore()),llm,tools)
        trace=TraceRecorder('Price-only fixture',mode='fixture',directory=self.traces);run_traced_team(orch,'NVDA',trace)
        self.page.get_by_role('button',name='Refresh local data').click();self.page.wait_for_timeout(400)
        self.page.locator('.run-entry').filter(has_text='Price-only fixture').click()
        self.page.locator('.sidebar nav').get_by_role('button',name='Execution',exact=False).click()
        node=self.page.locator('[data-map-stage="news"]');self.assertIn('Not dispatched',node.inner_text())
        node.click();self.assertEqual(self.page.locator('.map-observation-list button').count(),0)
    def test_background_replay_does_not_masquerade_as_an_operational_service(self):
        class Judge:
            def judge(self,item):return Judgment(.6,.2,'neutral',.7)
        trace=TraceRecorder('News replay fixture',mode='replay',directory=self.traces)
        item=Item('NVDA','test fixture','https://example.test/item','Synthetic fixture evidence')
        run_traced_clock1(['NVDA'],lambda ticker:FetchResult([item]),Judge(),trace,clock=lambda:1900000000)
        self.page.get_by_role('button',name='Refresh local data').click();self.page.wait_for_timeout(400)
        self.page.locator('.run-entry').filter(has_text='News replay fixture').click()
        self.page.locator('.sidebar nav').get_by_role('button',name='Execution',exact=False).click()
        self.assertTrue(self.page.get_by_role('region',name='Background news run map').is_visible())
        self.assertIn('isolated from operational',self.page.locator('[data-map-stage="storage"]').inner_text())
        self.page.locator('[data-map-stage="signals"]').click();self.assertIn('Recorded decision scores',self.page.locator('.inspector').inner_html())
    def test_map_and_guide_fit_mobile_and_keep_disclosure_state_across_polling(self):
        self.page.locator('[data-map-stage="guard"]').click()
        self.page.locator('.guard-review summary').first.click()
        first=self.page.locator('.guard-review details').first
        self.page.locator('.guard-review summary').first.focus()
        self.trace.data['summary']['map_update_fixture']=True;self.trace.flush()
        self.page.wait_for_timeout(2900)
        self.assertTrue(first.evaluate('e=>e.open'))
        self.assertIn('Candidate text',self.page.evaluate('document.activeElement.textContent'))
        for width in (320,390,620,768,1024,1440):
            self.page.set_viewport_size({'width':width,'height':900})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width)
        self.page.locator('.sidebar nav').get_by_role('button',name='Models',exact=False).click()
        self.page.locator('.system-guide summary').click()
        self.assertIn('not an active hosted dependency',self.page.locator('.system-guide').inner_text())
        for width in (320,390,768,1440):
            self.page.set_viewport_size({'width':width,'height':900})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width)

    def test_single_pass_guard_telemetry_provenance_and_document_reader_mobile(self):
        import json, uuid
        def sid(): return uuid.uuid4().hex
        sp_trace = {
            'id': sid(), 'format': 'cofinance-trace/1', 'title': 'Single-pass PDF fixture',
            'mode': 'fixture', 'status': 'complete', 'started_at': '2026-11-27T00:00:00Z',
            'duration_ms': 420.0, 'revision': 1, 'dropped_spans': 0, 'storage_errors': 0,
            'guard_telemetry': {
                'version': 1, 'narrated_checks': 1, 'first_pass_accepted': 1, 'first_pass_acceptance_rate': 1.0,
                'guard_retries': 0, 'guard_retry_recovered': 0, 'follow_up_retries': 0, 'fallbacks': 0,
                'total_narrator_passes': 1, 'wasted_passes': 0, 'total_narrator_ms': 280.0, 'wasted_ms': 0.0,
                'wasted_ms_complete': True, 'bypass_methods': {'single_pass_data_answer': 1}, 'failure_reasons': {}, 'complete': True,
            },
            'spans': [
                {'id': sid(), 'parent_id': None, 'name': 'Interactive research', 'kind': 'run', 'actor': 'Market Team Lead', 'model': '', 'status': 'complete', 'start_ms': 0, 'duration_ms': 420.0, 'inputs': {}, 'outputs': {'answer': 'NVDA trades at 138.50 USD.'}},
                {'id': sid(), 'parent_id': None, 'name': 'Executive Route', 'kind': 'handoff', 'actor': 'Routing executive', 'model': '', 'status': 'complete', 'start_ms': 10, 'duration_ms': 5.0, 'inputs': {}, 'outputs': {'delegated_agents': ['Data Analyst', 'Document Reader']}},
                {'id': sid(), 'parent_id': None, 'name': 'Data Analyst', 'kind': 'agent', 'actor': 'Data Analyst', 'model': '', 'status': 'complete', 'start_ms': 20, 'duration_ms': 300.0, 'inputs': {}, 'outputs': {'summary': 'NVDA trades at 138.50 USD.', 'allowed_numbers': [138.5]}},
                {'id': sid(), 'parent_id': None, 'name': 'Document Reader', 'kind': 'agent', 'actor': 'Document Reader', 'model': '', 'status': 'complete', 'start_ms': 25, 'duration_ms': 15.0, 'inputs': {}, 'outputs': {'summary': 'Circular_47.pdf p.1'}},
                {'id': sid(), 'parent_id': None, 'name': 'Synthesis faithfulness', 'kind': 'gate', 'actor': 'Market Team Lead', 'model': '', 'status': 'complete', 'start_ms': 330, 'duration_ms': 2.0, 'inputs': {}, 'outputs': {'faithful_synthesis': True, 'method': 'single_pass_data_answer', 'allowed_numbers': [138.5], 'unsupported_risks': []}},
            ],
        }
        prov_model_id = sid()
        prov_trace = {
            'id': sid(), 'format': 'cofinance-trace/1', 'title': 'Provenance conflict fixture',
            'mode': 'fixture', 'status': 'degraded', 'started_at': '2026-11-27T00:01:00Z',
            'duration_ms': 610.0, 'revision': 1, 'dropped_spans': 0, 'storage_errors': 0,
            'guard_telemetry': {
                'version': 1, 'narrated_checks': 1, 'first_pass_accepted': 0, 'first_pass_acceptance_rate': 0.0,
                'guard_retries': 1, 'guard_retry_recovered': 0, 'follow_up_retries': 0, 'fallbacks': 1,
                'total_narrator_passes': 2, 'wasted_passes': 2, 'total_narrator_ms': 500.0, 'wasted_ms': 500.0,
                'wasted_ms_complete': True, 'bypass_methods': {}, 'failure_reasons': {'claim_provenance': 1}, 'complete': True,
            },
            'spans': [
                {'id': sid(), 'parent_id': None, 'name': 'Interactive research', 'kind': 'run', 'actor': 'Market Team Lead', 'model': '', 'status': 'complete', 'start_ms': 0, 'duration_ms': 610.0, 'inputs': {}, 'outputs': {'answer': 'NVDA trades at 138.50 USD.'}},
                {'id': prov_model_id, 'parent_id': None, 'name': 'Narrate evidence', 'kind': 'model', 'actor': 'Market Team Lead', 'model': 'AtomicChat/LFM3', 'status': 'complete', 'start_ms': 100, 'duration_ms': 250.0, 'inputs': {}, 'outputs': 'AAPL trades at 138.5%.'},
                {'id': sid(), 'parent_id': None, 'name': 'Synthesis faithfulness', 'kind': 'gate', 'actor': 'Market Team Lead', 'model': '', 'status': 'fallback', 'start_ms': 360, 'duration_ms': 3.0, 'inputs': {}, 'outputs': {
                    'faithful_synthesis': False, 'allowed_numbers': [138.5], 'rejected_numbers': [], 'validation_reason': 'claim_provenance',
                    'provenance_conflicts': [{'kind': 'asset_misattribution', 'text': '138.5%', 'claim': {'asset': 'AAPL', 'fields': ['price'], 'unit': 'percent', 'cue': 'price', 'clause': 'AAPL trades at 138.5%.'}, 'matches': [{'asset': 'NVDA', 'field': 'price', 'shown': '138.50', 'unit': 'currency'}], 'violations': ['asset', 'unit']}],
                    'guard_retry': {'reason': 'claim_provenance', 'rejected': ['138.5% (AAPL price percent)'], 'retry_passed_guards': False, 'used': 'fallback'},
                }},
            ],
        }
        (self.traces / f"{sp_trace['id']}.json").write_text(json.dumps(sp_trace), encoding='utf-8')
        (self.traces / f"{prov_trace['id']}.json").write_text(json.dumps(prov_trace), encoding='utf-8')
        self.page.get_by_role('button', name='Refresh local data').click()
        self.page.wait_for_timeout(400)
        self.page.locator('.run-entry').filter(has_text='Single-pass PDF fixture').click()
        self.page.locator('.sidebar nav').get_by_role('button', name='Execution', exact=False).click()
        self.assertIn('Verified single-pass', self.page.locator('[data-map-stage="lead"]').inner_text())
        self.assertNotIn('Python fallback', self.page.locator('[data-map-stage="lead"]').inner_text())
        self.assertEqual(self.page.locator('[data-map-stage="document"]').count(), 1)
        self.assertEqual(self.page.locator('.map-worker-row').get_attribute('data-worker-count'), '3')
        self.assertIn('1/1', self.page.locator('[data-guard-stat="pass1"]').inner_text())
        self.assertIn('Single-pass', self.page.locator('[data-guard-stat="single-pass"]').inner_text())
        for width in (320, 390, 768, 1440):
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'), width)

        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        self.page.locator('.run-entry').filter(has_text='Provenance conflict fixture').click()
        self.page.locator('.sidebar nav').get_by_role('button', name='Execution', exact=False).click()
        self.assertIn('1 guard retry', self.page.locator('[data-guard-stat="retries"]').inner_text())
        self.assertIn('2 wasted passes', self.page.locator('[data-guard-stat="wasted"]').inner_text())
        self.page.locator('[data-map-stage="guard"]').click()
        prov = self.page.locator('.provenance-conflicts')
        prov.wait_for()
        self.assertIn('138.5%', prov.inner_text())
        self.assertIn('NVDA price 138.50', prov.inner_text())

if __name__=='__main__':unittest.main()
