"""Real browser regressions for chat navigation. Disposable SQLite, fake evidence, no model or network."""
import os
import re
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from core.gliner_decide import GlinerQueryRouter
from core.market import MarketTools
from tests.execution_proof import require_clean_execution
from tests.test_chat import service, wait
from tests.test_research_acceptance import ModelFixture, THY, listing
from ui.dashboard import create_server


class ResearchClock(datetime):
    @classmethod
    def now(cls, tz=None):
        stamp = cls(2026, 11, 27, 19, 0, tzinfo=timezone.utc)
        return stamp.astimezone(tz) if tz else stamp.replace(tzinfo=None)


class JourneyRouter(ModelFixture):
    def extract_entities(self, text, entity_types):
        return {'entities': {'company': [asset for asset in ('THY', 'Toyota', 'VOD.L') if asset in text]}}


class JourneyMarket:
    listings = {
        'THY': THY,
        'Toyota': [listing('TM', 'Toyota Motor ADR', 'EQUITY', 'NYQ', 90000),
                   listing('7203.T', 'Toyota Motor', 'EQUITY', 'JPX', 55000)],
        'VOD.L': [listing('VOD.L', 'Vodafone Group', 'EQUITY', 'LSE', 55000)],
    }
    quotes = {
        'THYAO.IS': ('Turkish Airlines', 'TRY', 'Istanbul', 'Europe/Istanbul', 303.5, 298.25, 15),
        '7203.T': ('Toyota Motor', 'JPY', 'Tokyo', 'Asia/Tokyo', 2810, 2775, 6),
        'VOD.L': ('Vodafone Group', 'GBp', 'London', 'Europe/London', 84.2, 82.65, 16),
    }

    def __init__(self):
        self.fail = set()
        self.reads = []

    def fetch(self, url):
        request = urlsplit(url)
        params = parse_qs(request.query)
        if request.path.endswith('/search'):
            return {'quotes': self.listings.get(params['q'][0], [])}
        if 'fundamentals-timeseries' in request.path:
            return {'timeseries': {'result': []}}
        if '/chart/' not in request.path:
            raise AssertionError('Unexpected provider request: '+request.path)
        ticker = request.path.rsplit('/', 1)[1]
        if params.get('range') == ['max']:
            return {'chart': {'result': []}}
        self.reads.append(ticker)
        if ticker in self.fail:
            raise TimeoutError('fixture timeout')
        name, currency, exchange, tz, latest, previous, hour = self.quotes[ticker]
        stamp = datetime(2026, 11, 27, hour, 30, tzinfo=timezone.utc).timestamp()
        meta = dict(symbol=ticker, longName=name, currency=currency, instrumentType='EQUITY',
                    fullExchangeName=exchange, exchangeTimezoneName=tz, regularMarketPrice=latest,
                    regularMarketTime=stamp, currentTradingPeriod={'regular': {'start': stamp-6*3600, 'end': stamp}},
                    fiftyTwoWeekHigh=latest*1.2, fiftyTwoWeekLow=previous*.8)
        return {'chart': {'result': [{'meta': meta, 'timestamp': [stamp-86400, stamp],
                                      'indicators': {'quote': [{'close': [previous, latest]}]}}]}}


@unittest.skipUnless(os.environ.get('COFINANCE_BROWSER_TESTS') == '1', 'installed Chrome checks are opt-in')
class TestChatShellBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.env = patch.dict(os.environ, {'COFINANCE_MEMORY':'keyword'}); self.env.start()
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.service = service(self.root)
        self.sessions = []
        for query in ('Review NVDA', 'Review TSLA'):
            s = self.service.store.create_session(uuid.uuid4().hex)
            wait(self.service, self.service.submit(s['id'], query, uuid.uuid4().hex, 'facts'))
            self.sessions.append(s)
        self.server = create_server(port=0, trace_dir=self.root/'traces', corpus_dir=self.root/'none', service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.context = self.browser.new_context(viewport={'width':1440,'height':900}, accept_downloads=True)
        self.page = self.context.new_page(); self.errors=[]
        self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.origin = 'http://127.0.0.1:'+str(self.server.server_port)
        self.page.goto(self.origin, wait_until='domcontentloaded')
        self.page.get_by_role('textbox',name='Research question').wait_for()
        self.page.locator('.conversation-row').first.wait_for()

    def tearDown(self):
        self.context.close(); self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)
        self.service.close(); self.temp.cleanup(); self.env.stop()
        self.assertEqual(self.errors, [])

    def choose(self, title='Review NVDA', scope=None):
        (scope or self.page).get_by_role('button',name=title,exact=False).first.click()
        self.page.locator('.msg-answer').first.wait_for()

    def test_new_chat_is_blank_without_creating_empty_rows_and_submission_works(self):
        self.choose(); before=len(self.service.store.session_ids())
        self.page.get_by_role('textbox',name='Research question').fill('unsent draft')
        self.page.get_by_role('button',name='New chat',exact=True).click()
        self.page.get_by_role('heading',name='What should we research?').wait_for()
        self.assertEqual(self.page.get_by_role('textbox',name='Research question').input_value(),'')
        self.page.get_by_label('Conversation options').click()
        self.page.get_by_role('button',name='Refresh local data').click()
        self.assertEqual(len(self.service.store.session_ids()),before)
        self.page.reload(wait_until='domcontentloaded')
        self.page.get_by_role('heading',name='What should we research?').wait_for()
        self.page.get_by_role('textbox',name='Research question').fill('What is NVDA trading at?')
        self.page.get_by_role('textbox',name='Research question').press('Enter')
        self.page.locator('.msg-answer').wait_for()
        self.assertEqual(len(self.service.store.session_ids()),before+1)
        self.assertEqual(self.page.get_by_role('button',name='New chat',exact=True).count(),1)

    def test_attach_pdf_ask_without_an_asset_see_pages_then_remove(self):
        from tests.test_documents import REPORT
        self.page.get_by_role('button',name='New chat',exact=True).click()
        self.page.get_by_role('heading',name='What should we research?').wait_for()
        before=set(self.service.store.session_ids())
        self.page.locator('input[type=file]').set_input_files({'name':'q3-report.pdf','mimeType':'application/pdf','buffer':REPORT})
        chip=self.page.get_by_role('list',name='Attached PDFs')
        chip.get_by_text('q3-report.pdf').wait_for()
        self.assertIn('3 pages',chip.inner_text())
        (sid,)=set(self.service.store.session_ids())-before  # the file belongs to exactly one new saved conversation
        self.assertEqual([d['name'] for d in self.service.store.session(sid)['documents']],['q3-report.pdf'])
        box=self.page.get_by_role('textbox',name='Research question')
        self.assertIn('PDF',box.get_attribute('placeholder'))
        box.fill('How much revenue did the third quarter bring?'); box.press('Enter')
        sources=self.page.get_by_label('Pages used'); sources.wait_for()
        self.assertIn('q3-report.pdf',sources.inner_text()); self.assertIn('page 2',sources.inner_text())
        self.assertNotIn('which asset',self.page.locator('.msg-answer').inner_text().lower())
        self.page.get_by_role('button',name='Remove q3-report.pdf').click()
        chip.wait_for(state='detached')
        self.assertEqual(self.service.store.session(sid)['documents'],[])
        self.page.reload(wait_until='domcontentloaded')
        self.choose('How much revenue')
        self.assertEqual(self.page.get_by_role('list',name='Attached PDFs').count(),0)
        box=self.page.get_by_role('textbox',name='Research question')
        box.fill('How much revenue did the third quarter bring?'); box.press('Enter')
        second=self.page.get_by_role('article',name='Research response').nth(1)
        second.wait_for(timeout=10000)
        self.assertEqual(second.get_by_label('Pages used').count(),0)
        self.assertNotIn('q3-report.pdf',second.inner_text())

    def test_multi_pdf_grouped_sources_and_event_driven_sse(self):
        from tests.test_documents import make_pdf
        from ui import dashboard
        pdf_a = make_pdf([
            'Household checking accounts may waive transfer fees each month.',
            'Branch lobby hours run Monday through Friday.',
            'The household transfer fee waiver applies only to the first transfer each month.',
        ])
        pdf_b = make_pdf([
            'Business checking accounts may waive transfer fees on domestic accounts.',
        ])
        with patch.object(dashboard, 'EVENT_TICK_S', 3.0):
            self.page.get_by_role('button', name='New chat', exact=True).click()
            self.page.get_by_role('heading', name='What should we research?').wait_for()
            file_input = self.page.locator('input[type=file]')
            file_input.set_input_files({'name': 'Circular_47.pdf', 'mimeType': 'application/pdf', 'buffer': pdf_a})
            chip = self.page.get_by_role('list', name='Attached PDFs')
            chip.get_by_text('Circular_47.pdf').wait_for()
            file_input.set_input_files({'name': 'Circular_48.pdf', 'mimeType': 'application/pdf', 'buffer': pdf_b})
            chip.get_by_text('Circular_48.pdf').wait_for()
            box = self.page.get_by_role('textbox', name='Research question')
            box.fill('Compare household and business transfer fee waivers')
            box.press('Enter')
            sources = self.page.get_by_label('Pages used')
            sources.wait_for(timeout=8000)
            row_a = sources.locator('[data-document-group="Circular_47.pdf"]')
            row_b = sources.locator('[data-document-group="Circular_48.pdf"]')
            self.assertEqual(row_a.count(), 1)
            self.assertEqual(row_b.count(), 1)
            self.assertIn('pages 1, 3', row_a.inner_text())
            self.assertIn('page 1', row_b.inner_text())
            answer_text = self.page.locator('.msg-answer').inner_text()
            self.assertIn('Circular_47.pdf', answer_text)
            self.assertIn('Circular_48.pdf', answer_text)

    def test_tools_collapse_persistence_and_conversation_options(self):
        self.choose()
        side=self.page.locator('.workspace-sidebar')  # answers carry their own Execution action; test the sidebar only
        self.assertFalse(side.get_by_role('button',name='Execution',exact=True).is_visible())
        side.get_by_text('Workspace tools',exact=True).click()
        for name in ('Research','Execution','Models','Dataset'):
            side.get_by_role('button',name=name,exact=True).click()
            self.assertIn(name,self.page.locator('.breadcrumb').inner_text())
        side.get_by_role('button',name='Chat',exact=True).click()
        self.page.get_by_role('button',name='Close sidebar').click()
        self.assertFalse(self.page.get_by_role('complementary',name='Workspace navigation').is_visible())
        self.page.reload(wait_until='domcontentloaded')
        self.page.get_by_role('button',name='Open sidebar').wait_for()
        self.page.get_by_role('button',name='Open sidebar').click()
        self.page.get_by_label('Conversation options').click()
        self.page.get_by_role('button',name='Conversation memory').click()
        self.page.get_by_role('dialog',name='Conversation memory').wait_for()
        self.page.keyboard.press('Escape')
        self.page.get_by_label('Conversation options').click()
        with self.page.expect_download() as download:
            self.page.get_by_role('button',name='Export conversation').click()
        self.assertEqual(download.value.suggested_filename,'cofinance-conversation.json')
        self.page.get_by_label('Conversation options').focus(); self.page.keyboard.press('Enter');self.page.keyboard.press('Escape')
        self.assertFalse(self.page.locator('.chat-options').evaluate('e=>e.open'))

    def test_mobile_drawer_search_keyboard_focus_and_responsive_hit_targets(self):
        for width in (320,390,620):
            self.page.set_viewport_size({'width':width,'height':844})
            self.assertLessEqual(self.page.locator('.topbar').bounding_box()['height'],60)
            self.page.get_by_role('button',name='Open sidebar').click()
            dialog=self.page.get_by_role('dialog',name='Workspace navigation')
            dialog.get_by_role('textbox',name='Search conversations').fill('NVDA')
            self.assertEqual(dialog.locator('.conversation-row').count(),1)
            self.choose(scope=dialog)
            self.assertEqual(self.page.get_by_role('dialog',name='Workspace navigation').count(),0)
            self.page.get_by_role('button',name='Open sidebar').click()
            self.page.keyboard.press('Escape')
            self.assertEqual(self.page.locator(':focus').get_attribute('aria-label'),'Open sidebar')
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            # Every visible header target has a genuine 44 x 44 hit area, not just a larger icon.
            for target in self.page.locator('.topbar button:visible,.topbar summary:visible').all():
                r=target.bounding_box(); self.assertGreaterEqual(r['width'],44);self.assertGreaterEqual(r['height'],44)
                self.assertTrue(target.evaluate('(e)=>{const r=e.getBoundingClientRect(),h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return h===e||e.contains(h)}'))
        self.page.get_by_role('button',name='Open sidebar').click()
        self.page.set_viewport_size({'width':1024,'height':900})
        self.page.get_by_role('dialog',name='Workspace navigation').wait_for(state='hidden')
        self.page.get_by_role('button',name='New chat',exact=True).click()
        self.page.get_by_role('heading',name='What should we research?').wait_for()

    def test_deep_think_composer_states_models_shortcut_and_mobile_layout(self):
        from core.chat_service import ChatService
        from core.gliner_decide import GlinerQueryRouter
        from core.local_models import DEFAULT_NARRATOR as MODEL
        from tests.test_chat import FakeMarket, REPO
        from tests.test_deep_think import FakeModels
        from tests.test_orchestrator import StubGliner

        models = FakeModels(self.root / 'dt_models', REPO)
        models.think_ok = False
        models.state['selected'] = MODEL
        dt_service = ChatService(
            REPO, self.root / 'dt_data', models=models, market_factory=FakeMarket,
            router_factory=lambda: GlinerQueryRouter(StubGliner(), {'NVDA', 'TSLA'}),
        )
        dt_server = create_server(port=0, trace_dir=self.root / 'dt_traces', corpus_dir=self.root / 'none', service=dt_service)
        dt_thread = threading.Thread(target=dt_server.serve_forever, daemon=True)
        dt_thread.start()
        page = self.context.new_page()
        page.on('pageerror', lambda e: self.errors.append(str(e)))
        try:
            origin = 'http://127.0.0.1:' + str(dt_server.server_port)
            page.goto(origin, wait_until='domcontentloaded')
            toggle = page.locator('.deep-think-toggle')
            toggle.wait_for()
            self.assertTrue(toggle.is_disabled())
            self.assertEqual(toggle.get_attribute('aria-pressed'), 'false')
            self.assertEqual(toggle.get_attribute('data-locked'), 'true')
            setup_btn = page.get_by_role('button', name='Test in Models', exact=True)
            setup_btn.wait_for()
            setup_btn.click()
            page.locator('#deep-think-panel').wait_for()
            self.assertIn('Models', page.locator('.breadcrumb').inner_text())

            models.think_ok = True
            page.goto(origin, wait_until='domcontentloaded')
            toggle = page.locator('.deep-think-toggle')
            toggle.wait_for()
            self.assertFalse(toggle.is_disabled())
            self.assertEqual(page.get_by_role('button', name='Test in Models', exact=True).count(), 0)
            self.assertEqual(toggle.get_attribute('aria-pressed'), 'false')
            toggle.click()
            self.assertEqual(toggle.get_attribute('aria-pressed'), 'true')
            self.assertIn('On', toggle.locator('.deep-think-chip').inner_text())

            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            box_rect = toggle.bounding_box()
            self.assertGreaterEqual(box_rect['height'], 44)

            page.set_viewport_size({'width': 1440, 'height': 900})
            box = page.get_by_role('textbox', name='Research question')
            box.fill('Review the NVDA thesis')
            box.press('Enter')
            page.locator('.deep-think-used').wait_for(timeout=8000)
            self.assertIn('Deep think', page.locator('.deep-think-used').inner_text())
        finally:
            page.close()
            dt_server.shutdown()
            dt_server.server_close()
            dt_thread.join(timeout=3)
            dt_service.close()

    def test_open_chat_run_map_and_clear_all_removes_run_from_execution(self):
        from core.chat_service import ChatService
        from core.gliner_decide import RoutedQuery
        from core.run_trace import TraceRecorder
        from tests.test_chat import FakeMarket
        from tests.test_system_integration import _FlowModels

        REPO = Path(__file__).resolve().parents[1]
        oc_data = self.root / 'oc_data'
        models = _FlowModels(oc_data / 'local_models')
        models.state['selected'] = 'lfm3-1.7b-atomic-ad-q4km'

        class _OpenRouter:
            def route(self, q):
                return RoutedQuery('open_conversation', 0.88, 'swing_week', [])

        oc_service = ChatService(
            REPO, oc_data, models=models, market_factory=FakeMarket,
            router_factory=lambda: _OpenRouter(),
        )
        fix = TraceRecorder('Fixture baseline run', mode='fixture', directory=oc_data / 'traces')
        fix.finish(summary={'answer': 'Fixture summary.'})
        oc_server = create_server(port=0, trace_dir=oc_data / 'traces', corpus_dir=self.root / 'none', service=oc_service)
        oc_thread = threading.Thread(target=oc_server.serve_forever, daemon=True)
        oc_thread.start()
        page = self.context.new_page()
        page.set_default_timeout(8000)
        page.on('pageerror', lambda e: self.errors.append(str(e)))
        try:
            origin = 'http://127.0.0.1:' + str(oc_server.server_port)
            page.goto(origin, wait_until='domcontentloaded')
            page.locator('.deep-think-toggle').wait_for(timeout=8000)
            box = page.get_by_role('textbox', name='Research question')
            box.wait_for()
            box.fill("I've $100. Now make a suggestion")
            box.press('Enter')
            page.get_by_text('General chat (no live tools)', exact=True).wait_for(timeout=8000)

            page.locator('.msg-actions').get_by_role('button', name='Execution', exact=True).click()
            page.locator('[data-map-stage="assistant"]').wait_for(timeout=8000)
            map_text = page.locator('.run-map').inner_text()
            self.assertIn('General chat (no live tools)', map_text)
            self.assertIn('Local assistant', map_text)
            self.assertIn('Observed', page.locator('[data-map-stage="assistant"]').inner_text())
            self.assertIn('Observed', page.locator('[data-map-stage="result"]').inner_text())
            self.assertNotIn('Not observed', map_text)
            self.assertNotIn('Python fallback', map_text)
            self.assertNotIn('No accepted final answer recorded', map_text)
            self.assertIn("I've $100", page.locator('[data-run-group="chat"]').inner_text())
            self.assertIn('Fixture baseline run', page.locator('[data-run-group="system"]').inner_text())

            page.locator('[data-map-stage="assistant"]').click()
            self.assertIn('Direct local chat (no live tools)', page.locator('.inspector').inner_text())

            page.locator('[data-run-group="system"]').get_by_text('Fixture baseline run', exact=False).click()
            self.assertIn('Execution', page.locator('.breadcrumb').inner_text())

            page.locator('.sidebar').get_by_role('button', name='Chat', exact=True).click()
            hist = page.locator('details.history-options')
            if not hist.evaluate('e => e.open'):
                hist.locator('summary').click()
            page.get_by_role('button', name='Clear all conversations', exact=True).click()
            page.get_by_role('dialog').get_by_role('button', name='Delete all', exact=True).click()
            page.get_by_text('Deleted 1 conversation.', exact=False).wait_for(timeout=8000)

            tools = page.locator('.workspace-tools')
            if not tools.evaluate('e => e.open'):
                tools.locator('summary').click()
            page.locator('.sidebar nav').get_by_role('button', name='Execution', exact=False).click()
            page.locator('[data-run-group="chat"]').get_by_text('No chat runs.', exact=True).wait_for(timeout=8000)
            self.assertEqual(page.locator('.error-banner').count(), 0)
            self.assertNotIn("I've $100", page.locator('.main-area').inner_text())
            self.assertIn('Fixture baseline run', page.locator('[data-run-group="system"]').inner_text())

            page.reload(wait_until='domcontentloaded')
            page.locator('.deep-think-toggle').wait_for(timeout=8000)
            self.assertEqual(page.locator('.error-banner').count(), 0)
        finally:
            page.close()
            oc_server.shutdown()
            oc_server.server_close()
            oc_thread.join(timeout=3)
            oc_service.close()

    def test_late_submit_acknowledgment_does_not_clear_a_newer_draft(self):
        from playwright.sync_api import expect

        self.choose()
        page = self.page
        pending = []
        page.route('**/api/chat', lambda route: pending.append(route))
        box = page.get_by_role('textbox', name='Research question')
        box.fill('Review NVDA with a delayed acknowledgment')
        box.evaluate("e => { for(let i=0;i<2;i++) e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true})) }")
        page.wait_for_function("document.querySelector('.send-button').disabled")
        self.assertEqual(len(pending), 1)
        box.fill('A newer draft in NVDA')
        box.press('Enter')
        self.assertEqual(len(pending), 1)
        self.choose('Review TSLA')
        self.assertEqual(box.input_value(), '')
        box.fill('Unsent question in another conversation')
        with page.expect_response(lambda response: response.url.endswith('/api/chat')):
            pending.pop().continue_()
        page.unroute('**/api/chat')
        expect(page.get_by_role('button', name='Send question')).to_be_enabled()
        self.assertEqual(box.input_value(), 'Unsent question in another conversation')
        a, b = (self.service.store.session(s['id']) for s in self.sessions)
        self.assertEqual(len(a['jobs']), 2)
        self.assertEqual(len(b['jobs']), 1)
        self.assertNotIn('delayed acknowledgment', page.locator('.bubble').first.inner_text())
        page.locator('.msg-actions').get_by_role('button', name='Execution', exact=True).first.click()
        page.locator('.sidebar').get_by_role('button', name='Chat', exact=True).click()
        self.assertEqual(page.get_by_role('textbox', name='Research question').input_value(), 'Unsent question in another conversation')
        self.choose()
        self.assertEqual(box.input_value(), 'A newer draft in NVDA')

    def test_delayed_conversation_creation_does_not_override_a_new_selection(self):
        from playwright.sync_api import expect

        page = self.page
        pending = []
        page.route('**/api/sessions', lambda route: pending.append(route) if route.request.method=='POST' else route.continue_())
        page.get_by_role('button', name='New chat', exact=True).click()
        box = page.get_by_role('textbox', name='Research question')
        box.fill('Review NVDA in a brand new conversation')
        page.get_by_role('button', name='Send question').click()
        self.choose('Review TSLA')
        box.fill('Draft for the already selected chat')
        self.assertEqual(len(pending), 1)
        with page.expect_response(lambda response: response.url.endswith('/api/chat')):
            pending.pop().continue_()
        page.unroute('**/api/sessions')
        expect(page.get_by_role('button', name='Send question')).to_be_enabled()
        self.assertEqual(box.input_value(), 'Draft for the already selected chat')
        self.assertIn('Review TSLA', page.locator('.bubble').first.inner_text())
        latest_id = next(iter(set(self.service.store.session_ids()) - {s['id'] for s in self.sessions}))
        created = self.service.store.session(latest_id)
        self.assertEqual(len(created['jobs']), 1)
        self.assertEqual(created['jobs'][0]['query'], 'Review NVDA in a brand new conversation')

    def test_early_enter_keeps_the_draft_until_workspace_connects(self):
        page = self.context.new_page()
        pending = []
        page.route('**/api/state', lambda route: pending.append(route))
        try:
            page.goto(self.origin, wait_until='domcontentloaded')
            box = page.get_by_role('textbox', name='Research question')
            page.get_by_text('Connecting. Draft kept.', exact=False).wait_for()
            box.fill('Review NVDA after startup')
            box.press('Enter')
            self.assertEqual(box.input_value(), 'Review NVDA after startup')
            self.assertTrue(page.get_by_role('button', name='Send question').is_disabled())
            self.assertEqual(len(self.service.store.session_ids()), 2)
            self.assertTrue(pending)
            for route in pending:
                route.continue_()
            page.unroute('**/api/state')
            page.get_by_role('button', name='Send question').click()
            page.locator('.msg-answer').wait_for()
            self.assertEqual(len(self.service.store.session_ids()), 3)
        finally:
            page.close()

    def prepare_research_journey(self):
        market = JourneyMarket()
        clock = patch('core.market.datetime', ResearchClock)
        clock.start()
        self.addCleanup(clock.stop)
        self.service.router_factory = lambda: GlinerQueryRouter(JourneyRouter(), {'NVDA', 'VOD.L'})
        self.service.market_factory = lambda cancel=None: MarketTools(fetch=market.fetch, cancel=cancel)
        return market

    def submit_question(self, page, question, index=0):
        from playwright.sync_api import expect

        box = page.get_by_role('textbox', name='Research question')
        box.fill(question)
        button = page.get_by_role('button', name='Send question')
        expect(button).to_be_enabled()
        if page.viewport_size['width'] <= 700:
            button.tap()
        else:
            box.press('Enter')
        answer = page.get_by_role('article', name='Research response').nth(index)
        expect(answer).to_be_visible(timeout=10000)
        return answer

    def test_check_the_right_listing_open_execution_and_verify_yesterdays_close(self):
        from playwright.sync_api import expect

        market = self.prepare_research_journey()
        page = self.page
        before = set(self.service.store.session_ids())
        answer = self.submit_question(page, 'THY, the Turkish stock. What is it trading at?')
        expect(answer.locator('.facts-asset')).to_contain_text('THYAO.IS')
        expect(answer.locator('.facts-quote')).to_have_text('303.50 TRY')
        expect(answer.locator('[data-quote-state="last_session"]')).to_contain_text('Yahoo Finance')
        self.assertNotIn(' ? ', answer.inner_text())
        self.assertIn(' \u00b7 ', answer.locator('[data-quote-state="last_session"]').inner_text())
        expect(answer.get_by_text('1Y high', exact=True)).to_be_hidden()
        expect(answer.get_by_role('link', name=re.compile('THYAO.IS market data'))).to_be_hidden()
        expect(answer.locator('figure.price-chart')).to_have_count(0)
        self.assertEqual(market.reads, ['THYAO.IS'])
        self.assertEqual(self.service.store.watchlist(), [])
        (sid,) = set(self.service.store.session_ids()) - before
        first_run = self.service.store.session(sid)['messages'][-1]['run_id']

        draft = 'What did THY close at yesterday?'
        box = page.get_by_role('textbox', name='Research question')
        box.fill(draft)
        answer.get_by_role('button', name='Execution', exact=True).click()
        require_clean_execution(page, self.root/'traces', first_run, mode='live')
        expect(page.locator('[data-map-stage="router"] .map-owner')).to_have_text('GLiNER')
        expect(page.locator('[data-map-stage="data"] .map-owner')).to_have_text('Python')
        page.locator('.sidebar').get_by_role('button', name='Chat', exact=True).click()
        expect(box).to_have_value(draft)
        box.press('Enter')
        followup = page.get_by_role('article', name='Research response').nth(1)
        expect(followup.locator('.msg-body')).to_contain_text(re.compile(r'THYAO.IS previous-session close: 298.25.*TRY on 2026-11-26', re.S))
        cite = followup.get_by_role('button', name=re.compile(r'Source .*THYAO.IS.*Previous', re.I))
        cite.focus()
        cite.press('Enter')
        metric = followup.locator('[data-fact-field="previous_session_close"]')
        expect(metric).to_be_focused()
        expect(metric).to_contain_text('298.25 TRY on 2026-11-26')
        followup.get_by_text('Sources (1)', exact=True).click()
        expect(followup.get_by_role('link', name=re.compile('THYAO.IS market data'))).to_have_attribute('href', 'https://finance.yahoo.com/quote/THYAO.IS')
        followup.get_by_role('button', name='Track THYAO.IS', exact=True).click()
        expect(followup.get_by_role('button', name='Tracking THYAO.IS', exact=True)).to_be_visible()
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('.sidebar').get_by_role('button', name='Untrack THYAO.IS')).to_be_visible()
        page.locator('.sidebar').get_by_role('button', name='Untrack THYAO.IS').click()
        expect(page.locator('.sidebar').get_by_role('button', name='Untrack THYAO.IS')).to_have_count(0)
        self.assertEqual(self.service.store.watchlist(), [])
        self.assertEqual(market.reads, ['THYAO.IS', 'THYAO.IS'])

    def test_phone_comparison_reveals_timeout_then_retries_with_both_units(self):
        from playwright.sync_api import expect

        market = self.prepare_research_journey()
        market.fail.add('VOD.L')
        before = set(self.service.store.session_ids())
        with self.browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True) as context:
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.goto(self.origin, wait_until='domcontentloaded')
            question = 'Compare Toyota on the Tokyo exchange and VOD.L in London.'
            partial = self.submit_question(page, question)
            expect(partial.locator('.msg-body').first).to_contain_text('comparison is incomplete')
            expect(partial.locator('[data-quote-state="unavailable"]')).to_have_text(re.compile('Quote timed out'))
            expect(partial.locator('[data-quote-state="unavailable"]')).to_contain_text('No values were substituted')
            self.assertNotIn(' ? ', partial.inner_text())
            expect(partial.locator('.facts-quote')).to_have_text('2,810.00 JPY')
            london = partial.locator('.facts').filter(has_text='VOD.L')
            expect(london.locator('[data-quote-state="unavailable"]')).to_contain_text('Quote timed out')
            expect(partial).not_to_contain_text('84.20')
            self.assertCountEqual(market.reads, ['7203.T', 'VOD.L'])

            market.fail.clear()
            answer = self.submit_question(page, question, index=1)
            expect(answer.locator('.facts-quote')).to_have_count(2)
            for ticker, quote in [('7203.T', '2,810.00 JPY'), ('VOD.L', '84.20 GBp')]:
                expect(answer.locator('.facts').filter(has_text=ticker).locator('.facts-quote')).to_have_text(quote)
            expect(answer.locator('figure.price-chart')).to_have_count(0)
            history = answer.get_by_text('Price history (2)', exact=True)
            expect(history).to_be_visible()
            self.assertGreaterEqual(history.bounding_box()['height'], 44)
            history.tap()
            expect(answer.get_by_role('img', name=re.compile(r'7203.T daily close-price chart, 1 year, JPY'))).to_be_visible()
            expect(answer.get_by_role('img', name=re.compile(r'VOD.L daily close-price chart, 1 year, GBp'))).to_be_visible()
            history.tap()
            expect(answer.locator('figure.price-chart')).to_have_count(0)
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            self.assertEqual(errors, [])
            self.assertCountEqual(market.reads, ['7203.T', 'VOD.L', '7203.T', 'VOD.L'])
            (sid,) = set(self.service.store.session_ids()) - before
            messages = [m for m in self.service.store.session(sid)['messages'] if m['role']=='assistant']
            draft = page.get_by_role('textbox', name='Research question')
            draft.fill('Which moved more since the previous close?')
            answer.get_by_role('button', name='Execution', exact=True).tap()
            require_clean_execution(page, self.root/'traces', messages[-1]['run_id'], mode='live')
            back = page.get_by_role('button', name='Back to chat')
            self.assertGreaterEqual(back.bounding_box()['width'], 44)
            back.tap()
            expect(draft).to_have_value('Which moved more since the previous close?')

    def test_clear_all_failure_is_visible_and_can_be_retried_with_empty_history(self):
        clear = self.service.clear_conversations
        requests = []

        def clear_with_one_failure(request_id):
            result = clear(request_id)
            requests.append(request_id)
            if len(requests) == 1:
                return {**result, 'cleanup_retry': True, 'sweep_pending': 1,
                        'error': 'Some local memory copies could not be cleared. Retry Clear All.'}
            return result

        page = self.page
        hist = page.locator('details.history-options')
        if not hist.evaluate('e => e.open'):
            hist.locator('summary').click()
        trigger = page.get_by_role('button', name='Clear all conversations', exact=True)
        with patch.object(self.service, 'clear_conversations', side_effect=clear_with_one_failure):
            trigger.click()
            page.get_by_role('dialog').get_by_role('button', name='Delete all', exact=True).click()
            page.get_by_role('alert').filter(has_text='Retry Clear All.').wait_for()
            page.get_by_text('No conversations yet.', exact=True).wait_for()
            trigger.click()
            page.get_by_role('dialog').get_by_role('button', name='Delete all', exact=True).click()
            page.get_by_text('Deleted 0 conversations.', exact=True).wait_for()
        self.assertEqual(len(requests), 2)
        self.assertNotEqual(*requests)

    def test_320px_mobile_drawer_navigation_and_execution_back_to_research(self):
        from playwright.sync_api import expect

        with self.browser.new_context(viewport={'width': 320, 'height': 740}, is_mobile=True, has_touch=True) as context:
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.goto(self.origin, wait_until='domcontentloaded')
            menu = page.get_by_role('button', name='Open sidebar')
            expect(menu).to_be_visible()
            self.assertGreaterEqual(menu.bounding_box()['width'], 44)
            menu.tap()
            drawer = page.locator('.navigation-drawer')
            expect(drawer).to_be_visible()
            drawer.get_by_role('button', name='Review TSLA', exact=False).first.tap()
            expect(drawer).to_be_hidden()
            box = page.get_by_role('textbox', name='Research question')
            box.fill('TSLA phone draft')
            menu.tap()
            drawer.get_by_role('button', name='Review NVDA', exact=False).first.tap()
            expect(box).to_have_value('')
            menu.tap()
            drawer.get_by_role('button', name='Review TSLA', exact=False).first.tap()
            expect(box).to_have_value('TSLA phone draft')
            menu.tap()
            tools = drawer.locator('details.workspace-tools')
            if not tools.evaluate('e => e.open'):
                tools.locator('summary').tap()
            drawer.get_by_role('button', name='Research', exact=True).tap()
            expect(page.locator('main .breadcrumb[data-view="research"]')).to_be_visible()
            page.get_by_role('button', name='View execution').tap()
            expect(page.locator('main .breadcrumb[data-view="execution"]')).to_be_visible()
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            back = page.get_by_role('button', name='Back to research')
            expect(back).to_be_visible()
            for ctrl in (menu, back, page.get_by_role('button', name='Refresh local data')):
                rect = ctrl.bounding_box()
                self.assertGreaterEqual(rect['width'], 44)
                self.assertGreaterEqual(rect['height'], 44)
            back.tap()
            expect(page.locator('main .breadcrumb[data-view="research"]')).to_be_visible()
            self.assertEqual(errors, [])

    def test_citations_all_fields_multi_ref_evidence_and_quote_quality_states(self):
        from playwright.sync_api import expect

        s = self.service.store.create_session(uuid.uuid4().hex)
        job = self.service.store.create_job(uuid.uuid4().hex, uuid.uuid4().hex, s['id'], 'Citation and quote state check', 'facts')
        content = 'THYAO.IS moved 1.76% from 298.25 to 303.50 on revenue beat 9.4.'
        payload = {
            'kind': 'research', 'mode': 'facts', 'tickers': ['THYAO.IS', 'DELY', 'STAL', 'UNKN', 'BADT', 'MISS'],
            'charts': [], 'faithful_synthesis': True,
            'snapshots': {
                'THYAO.IS': {
                    'price': 303.5, 'previous_session_close': 298.25, 'previous_session_date': '2026-11-26',
                    'change_from_previous_session_pct': 1.76, 'currency': 'TRY', 'exchange': 'IST',
                    'source': 'Yahoo Finance', 'price_as_of': '2026-11-27T15:30:00Z',
                    'quote_status': {'state': 'last_session'},
                },
                'DELY': {'price': 10.0, 'currency': 'USD', 'source': 'Yahoo Finance', 'quote_status': {'state': 'delayed'}},
                'STAL': {'price': 20.0, 'currency': 'USD', 'source': 'Yahoo Finance', 'quote_status': {'state': 'stale'}},
                'UNKN': {'price': 30.0, 'currency': 'USD', 'source': 'Yahoo Finance', 'quote_status': {'state': 'timestamp_unknown'}},
                'BADT': {'price': 40.0, 'currency': 'USD', 'source': 'Yahoo Finance', 'quote_status': {'state': 'timestamp_invalid'}},
                'MISS': {'status': 'unavailable', 'fundamentals_status': 'unavailable', 'quote_status': {'state': 'unavailable'}},
            },
            'evidence': [
                {'title': 'THY revenue beat 9.4 billion', 'url': 'https://example.com/thy-q3', 'source': 'Reuters', 'as_of': '2026-11-27T10:00:00Z'},
                {'title': 'Unlinked wire 9.4', 'url': 'javascript:alert(1)', 'source': 'Wire', 'as_of': 'Unknown'},
            ],
            'citations': [
                {'start': 15, 'end': 20, 'text': '1.76%', 'refs': [
                    {'kind': 'fact', 'ticker': 'THYAO.IS', 'field': 'change_from_previous_session_pct', 'label': 'Change since previous session close (percent)', 'shown': '1.76%', 'source': 'Yahoo Finance', 'as_of': '2026-11-27'},
                ]},
                {'start': 26, 'end': 32, 'text': '298.25', 'refs': [
                    {'kind': 'fact', 'ticker': 'THYAO.IS', 'field': 'price', 'label': 'Last price', 'shown': '303.50 TRY', 'source': 'Yahoo Finance', 'as_of': '2026-11-27'},
                    {'kind': 'fact', 'ticker': 'THYAO.IS', 'field': 'previous_session_close', 'label': 'Previous session close', 'shown': '298.25 TRY', 'source': 'Yahoo Finance', 'as_of': '2026-11-26'},
                ]},
                {'start': 59, 'end': 62, 'text': '9.4', 'refs': [
                    {'kind': 'evidence', 'index': 0, 'title': 'THY revenue beat 9.4 billion', 'source': 'Reuters', 'as_of': '2026-11-27T10:00:00Z'},
                    {'kind': 'evidence', 'index': 1, 'title': 'Unlinked wire 9.4', 'source': 'Wire', 'as_of': 'Unknown'},
                ]},
            ],
        }
        self.service.store.progress(job['id'], 'Running', 'running')
        self.service.store.finish(job['id'], content, payload, payload['tickers'], 'complete')
        page = self.page
        page.reload(wait_until='domcontentloaded')
        self.choose('Citation and quote state check')
        answer = page.get_by_role('article', name='Research response').first
        self.assertNotIn(' ? ', answer.inner_text())

        pct_chip = answer.get_by_role('button', name=re.compile(r'Source 1:.*Change since previous'))
        pct_chip.click()
        expect(answer.locator('[data-fact-field="change_from_previous_session_pct"]')).to_be_focused()

        multi_chip = answer.get_by_role('button', name=re.compile(r'Source 2:.*Last price.*Previous session close'))
        multi_chip.click()
        expect(answer.locator('[data-fact-field="price"]').first).to_be_focused()
        multi_chip.click()
        expect(answer.locator('[data-fact-field="previous_session_close"]')).to_be_focused()

        ev_chip = answer.get_by_role('button', name=re.compile(r'Source 3:.*THY revenue beat'))
        ev_chip.click()
        expect(answer.get_by_role('link', name=re.compile('THY revenue beat 9.4 billion'))).to_be_focused()
        ev_chip.click()
        expect(answer.locator('p.cite-target', has_text='Unlinked wire 9.4')).to_be_focused()

        expect(answer.locator('[data-quote-state="delayed"]')).to_contain_text('Delayed quote')
        expect(answer.locator('[data-quote-state="stale"]')).to_contain_text('Stale quote')
        expect(answer.locator('[data-quote-state="timestamp_unknown"]')).to_contain_text('Quote time unknown')
        expect(answer.locator('[data-quote-state="timestamp_invalid"]')).to_contain_text('Invalid quote time')
        miss = answer.locator('.facts').filter(has_text='MISS')
        expect(miss.locator('[data-quote-state="unavailable"]')).to_contain_text('No values were substituted')
        miss.get_by_text('Market details', exact=True).click()
        expect(miss.get_by_text('Fundamentals unavailable', exact=True)).to_be_visible()

    def test_mid_run_switch_cancel_reload_and_deep_think_toggle(self):
        from playwright.sync_api import expect

        entered = threading.Event()
        release = threading.Event()
        market = JourneyMarket()

        def gated_fetch(url):
            if '/chart/' in url:
                entered.set()
                release.wait(timeout=10)
            return market.fetch(url)

        clock = patch('core.market.datetime', ResearchClock)
        clock.start()
        self.addCleanup(clock.stop)
        self.service.router_factory = lambda: GlinerQueryRouter(JourneyRouter(), {'NVDA', 'VOD.L'})
        self.service.market_factory = lambda cancel=None: MarketTools(fetch=gated_fetch, cancel=cancel)
        orig_snapshot = self.service.models.snapshot
        def snapshot_with_think():
            snap = orig_snapshot()
            snap['selected'] = 'fixture-model'
            snap['think'] = {'budget': 512, 'qualified': True, 'loaded': False, 'report': {'passed': True}}
            return snap

        page = self.page
        with patch.object(self.service.models, 'snapshot', side_effect=snapshot_with_think):
            page.reload(wait_until='domcontentloaded')
            page.get_by_role('button', name='New chat', exact=True).click()
            think_btn = page.get_by_role('button', name=re.compile(r'Deep think'))
            expect(think_btn).to_be_enabled()
            think_btn.click()
            expect(think_btn).to_have_attribute('aria-pressed', 'true')
        page.reload(wait_until='domcontentloaded')
        self.choose('Review NVDA')
        box = page.get_by_role('textbox', name='Research question')
        box.fill('Quote VOD.L in London.')
        box.press('Enter')
        self.assertTrue(entered.wait(timeout=8), 'Expected gated market fetch to start')
        self.choose('Review TSLA')
        box.fill('Draft while NVDA job is running')
        self.assertEqual(box.input_value(), 'Draft while NVDA job is running')
        self.choose('Review NVDA')
        stop = page.get_by_role('button', name='Stop')
        expect(stop).to_be_visible(timeout=5000)
        stop.click()
        release.set()
        expect(page.get_by_text('Stopped by user', exact=False)).to_be_visible(timeout=8000)
        page.reload(wait_until='domcontentloaded')
        self.choose('Review NVDA')
        expect(page.get_by_role('article', name='Research response')).to_have_count(1)

    def test_single_sse_view_gated_dashboard_progressive_facts_and_canvas_chart(self):
        from playwright.sync_api import expect

        page = self.page
        requests_seen = []
        page.on('request', lambda req: requests_seen.append(req.url))

        page.goto(self.origin, wait_until='domcontentloaded')
        page.get_by_role('textbox', name='Research question').wait_for(timeout=10000)
        page.wait_for_timeout(400)

        event_streams = [u for u in requests_seen if '/api/events' in u]
        self.assertEqual(len(event_streams), 1, f'Expected 1 shared SSE stream, got {event_streams}')
        self.assertEqual([u for u in requests_seen if '/api/runs' in u or '/api/corpus' in u], [],
                         f'Chat view must not poll inactive Execution/Dataset endpoints: {requests_seen}')
        self.assertEqual([u for u in requests_seen if 'plotly' in u.lower()], [])

        narrator_entered = threading.Event()
        narrator_release = threading.Event()
        self.addCleanup(narrator_release.set)

        class SlowNarrator:
            def __init__(self, base):
                self._base = base
                self.model_id = 'fixture/lfm3'
                self.narrator_id = 'lfm3-1.7b-atomic-ad-q4km'
                self.state = {**base.state, 'selected': 'lfm3-1.7b-atomic-ad-q4km', 'phase': 'ready'}
                self.lock = base.lock
                self.thread = None
                self.busy = False

            def __getattr__(self, name):
                return getattr(self._base, name)

            def is_loaded(self, model=None):
                return True

            def qualified(self, backend=None, model=None):
                return True

            def think_qualified(self, backend=None, model=None):
                return False

            def thinker_loaded(self, model=None, backend=None):
                return False

            def stop_thinker(self):
                pass

            def load(self, *args, **kwargs):
                pass

            def snapshot(self):
                snap = self._base.snapshot()
                snap['selected'] = 'lfm3-1.7b-atomic-ad-q4km'
                snap['can_select'] = True
                snap['loaded'] = True
                return snap

            def complete(self, system, prompt, *, max_tokens=256, **options):
                narrator_entered.set()
                narrator_release.wait(timeout=10)
                return 'Vodafone Group trades at 84.20 GBp.'

        market = JourneyMarket()
        clock = patch('core.market.datetime', ResearchClock)
        clock.start()
        self.addCleanup(clock.stop)
        orig_models = self.service.models
        self.service.models = SlowNarrator(orig_models)
        self.addCleanup(lambda: setattr(self.service, 'models', orig_models))
        self.service.router_factory = lambda: GlinerQueryRouter(JourneyRouter(), {'NVDA', 'VOD.L'})
        self.service.market_factory = lambda cancel=None: MarketTools(fetch=market.fetch, cancel=cancel)

        page.reload(wait_until='domcontentloaded')
        page.get_by_role('button', name='New chat', exact=True).click()
        box = page.get_by_role('textbox', name='Research question')
        box.fill('What is VOD.L trading at?')
        box.press('Enter')

        self.assertTrue(narrator_entered.wait(timeout=8), 'Expected narrator to start after snapshot')
        live_ev = page.locator('.live-job [data-live-evidence="verified"]')
        expect(live_ev).to_be_visible(timeout=5000)
        expect(live_ev).to_contain_text('VOD.L')
        expect(live_ev).to_contain_text('84.20 GBp')

        narrator_release.set()
        answer = page.get_by_role('article', name='Research response').last
        expect(answer).to_be_visible(timeout=8000)

        chart_toggle = answer.locator('details.answer-history summary')
        chart_toggle.click()
        chart = answer.locator('.chart-canvas-wrap[role="img"]')
        expect(chart).to_be_visible(timeout=5000)
        expect(answer.locator('canvas')).to_have_count(1)
        before_val = chart.get_attribute('aria-description')
        chart.focus()
        chart.press('Home')
        after_home = chart.get_attribute('aria-description')
        self.assertNotEqual(before_val, after_home)
        chart.press('End')
        self.assertEqual(chart.get_attribute('aria-description'), before_val)

        page.get_by_role('button', name='Execution', exact=True).click()
        page.wait_for_timeout(400)
        self.assertTrue(any('/api/runs' in u for u in requests_seen), 'Execution view must load /api/runs on demand')


if __name__=='__main__': unittest.main()
