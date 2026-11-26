"""Real browser regressions for chat navigation. Disposable SQLite, fake evidence, no model or network."""
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from tests.test_chat import service, wait
from ui.dashboard import create_server


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

if __name__=='__main__': unittest.main()
