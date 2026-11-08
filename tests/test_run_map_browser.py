import os
import tempfile
import threading
import unittest
from pathlib import Path

from core.trace import TraceRecorder,run_traced_team,run_traced_clock1
from core.orchestrator import DataAnalystAgent,NewsResearcherAgent,TeamLeadOrchestrator
from core.gliner_decide import GlinerQueryRouter
from core.signals import Item,Judgment
from core.collector import FetchResult
from tests.test_orchestrator import Stub1BLLM,StubGliner,StubTools,StubNewsStore
from ui.dashboard import create_server,DIST


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
        self.page.goto('http://127.0.0.1:'+str(self.server.server_port),wait_until='networkidle')
        self.page.locator('.sidebar nav').get_by_role('button',name='Execution',exact=False).click()
        self.page.wait_for_selector('[data-map-stage="router"]')
    def tearDown(self):
        self.page.close();self.server.shutdown();self.server.server_close();self.thread.join(timeout=2);self.temp.cleanup()
        self.assertEqual(self.errors,[])
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
        node.click();self.assertIn('diagram position does not mean',self.page.locator('.map-observations').inner_text())
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

if __name__=='__main__':unittest.main()
