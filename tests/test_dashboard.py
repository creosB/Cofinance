import hashlib
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from core.trace import TraceRecorder
from ui.dashboard import DIST, create_server


class TestDashboardServer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dist = self.root / 'dist'
        (self.dist / 'assets').mkdir(parents=True)
        (self.dist / 'index.html').write_text('<div id="root"></div><script type="module" src="/assets/index.js"></script>', encoding='utf-8')
        (self.dist / 'assets/index.js').write_text('console.log("fixture")', encoding='utf-8')
        (self.dist / 'assets/index.css').write_text('body{margin:0}', encoding='utf-8')
        (self.dist / 'assets/font.woff2').write_bytes(b'font fixture')
        self.traces = self.root / 'traces'
        trace = TraceRecorder('Server fixture', mode='fixture', directory=self.traces)
        trace.finish()
        self.run_id = trace.run_id
        self.server = create_server(port=0, dist=self.dist, trace_dir=self.traces, corpus_dir=self.root/'missing')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path='/', headers=None, method='GET'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_react_shell_and_bundled_assets_share_the_api_origin(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        status, data, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(data, (self.dist/'index.html').read_bytes())
        self.assertEqual(headers['X-Cofinance-Frontend'], 'react')
        self.assertEqual(headers['X-Cofinance-Build'], hashlib.sha256(data).hexdigest())
        self.assertIn("script-src 'self';", headers['Content-Security-Policy'])
        self.assertNotIn("script-src 'unsafe-inline'", headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        for path, mime in (('/assets/index.js','text/javascript'),('/assets/index.css','text/css'),('/assets/font.woff2','font/woff2')):
            status, data, headers = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(mime, headers['Content-Type'])
        status, data, _ = self.request('/api/runs')
        self.assertEqual(json.loads(data)[0]['id'], self.run_id)

    def test_link_navigation_allowed_but_frames_and_cross_site_data_are_not(self):
        nav={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document','Sec-Fetch-User':'?1'}
        self.assertEqual(self.request('/',nav)[0],200)
        for path in ('/api/runs','/api/corpus','/assets/index.js'):
            self.assertEqual(self.request(path,nav)[0],403)
        self.assertEqual(self.request('/',{**nav,'Sec-Fetch-Dest':'iframe'})[0],403)
        self.assertEqual(self.request('/assets/index.js',{'Host':'attacker.test'})[0],403)
        self.assertEqual(self.request('/assets/index.js',{'Origin':'https://attacker.test'})[0],403)

    def test_no_arbitrary_files_spa_fallback_or_mutation(self):
        for path in ('/assets/../index.html','/assets/%2e%2e/secret.js','/assets/sub/file.js','/assets/file.map','/assets/index.js?x=1','/.env','/research'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.request('/api/runs',method='POST')[0],501)

    def test_oversized_static_files_are_refused(self):
        (self.dist/'assets/huge.js').write_bytes(b'x'*(5*1024*1024+1))
        self.assertEqual(self.request('/assets/huge.js')[0],404)


@unittest.skipUnless(os.environ.get('COFINANCE_BROWSER_TESTS') == '1', 'set COFINANCE_BROWSER_TESTS=1 for installed Chrome/Playwright')
class TestReactDashboardBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        if not (DIST/'index.html').exists():
            raise RuntimeError('Build the React frontend before running browser tests.')
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.trace=TraceRecorder('React browser fixture',mode='fixture',directory=self.root/'traces')
        with self.trace.span('QA run','run'):
            self.trace.event('Signal gate: DIGEST','gate',{'title':'Literal <img src=x onerror=alert(1)> evidence',
                 'action':'digest','reason':'material_not_urgent','ticker':'TEST','url':'javascript:alert(1)',
                 'judgment':{'material':.6,'urgent':.2,'direction':'neutral'},
                 'thresholds':{'digest_p':.5,'alert_p':.85,'alert_urgent_p':.7},'long':'Fixture evidence. '*300})
        self.trace.finish()
        self.server=create_server(port=0,trace_dir=self.root/'traces',corpus_dir=self.root/'no-corpus')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.page=self.browser.new_page(viewport={'width':1440,'height':1000})
        self.errors=[];self.requests=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.on('request',lambda request:self.requests.append(request.url))
        response=self.page.goto(f'http://127.0.0.1:{self.server.server_port}',wait_until='networkidle')
        self.assertEqual(response.headers.get('x-cofinance-frontend'),'react')
        self.page.locator('.sidebar nav').get_by_role('button',name='Research',exact=False).click()
        self.page.wait_for_selector('.evidence-row')

    def tearDown(self):
        self.page.close();self.server.shutdown();self.server.server_close();self.thread.join(timeout=2);self.temp.cleanup()
        self.assertEqual(self.errors,[])
        from urllib.parse import urlsplit
        self.assertEqual({urlsplit(url).hostname for url in self.requests},{'127.0.0.1'})

    def inspect_gate(self):
        self.page.locator('.evidence-row').first.click()
        self.page.wait_for_selector('.score-table')

    def test_research_connects_to_real_recorded_execution_and_safe_evidence(self):
        self.assertIn('Fixture run',self.page.locator('.mode-notice').inner_text())
        self.inspect_gate()
        self.assertEqual(self.page.locator('.inspector img').count(),0)
        self.assertEqual(self.page.locator('.source-link').count(),0)
        self.assertIn('<img',self.page.locator('.headline-evidence').inner_text())
        self.assertIn('0.8500',self.page.locator('.score-table').inner_text())
        self.assertTrue(self.page.get_by_text('Model scores are not calibrated probabilities.').is_visible())

    def test_keyed_inspector_keeps_disclosures_scroll_and_focus_when_data_changes(self):
        self.inspect_gate()
        self.page.get_by_role('button',name='Observed output',exact=True).click()
        code=self.page.locator('pre[aria-label="Observed output"]')
        self.page.wait_for_timeout(80)
        code.evaluate('el=>el.scrollTop=100')
        trigger=self.page.get_by_role('button',name='Input / policy',exact=True)
        trigger.focus()
        self.trace.data['spans'][0]['outputs']={'new_observation':True};self.trace.flush()
        self.page.wait_for_timeout(2900)
        self.assertEqual(self.page.get_by_role('button',name='Observed output',exact=True).get_attribute('aria-expanded'),'true')
        self.assertEqual(code.evaluate('el=>el.scrollTop'),100)
        self.assertEqual(self.page.evaluate('document.activeElement.textContent'),'Input / policy')

    def test_mobile_and_desktop_views_fit_without_document_clipping(self):
        for width in (320,390,620,768,1024,1440):
            self.page.set_viewport_size({'width':width,'height':900});self.page.wait_for_timeout(100)
            for view in ('Research','Execution','Models','Dataset'):
                if width<=700:
                    self.page.get_by_role('combobox',name='Navigate workspace').click()
                    self.page.get_by_role('option',name=view,exact=True).click()
                else:
                    self.page.locator('.sidebar nav').get_by_role('button',name=view,exact=False).click()
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,view))
                self.assertEqual(self.page.evaluate('getComputedStyle(document.documentElement).overflowX'),'visible')

    def test_keyboard_finder_filter_clear_and_empty_states(self):
        self.page.keyboard.press('Control+k')
        self.page.get_by_role('textbox',name='Find a run').fill('missing_run')
        self.assertTrue(self.page.get_by_text('No matching runs.').is_visible())
        self.page.keyboard.press('Escape')
        self.page.wait_for_selector('[role="dialog"]',state='hidden')
        self.page.keyboard.press('/')
        self.page.get_by_role('textbox',name='Search this run').fill('no_matching_trace_step')
        self.assertTrue(self.page.get_by_text('No matching steps').is_visible())
        self.page.get_by_role('button',name='Clear filters').click()
        self.assertEqual(self.page.locator('.trace-row').count(),2)

    def test_connection_failure_stays_visible_and_retry_recovers(self):
        self.page.route('**/api/runs',lambda route:route.fulfill(status=503,body='{}',content_type='application/json'))
        self.page.get_by_role('button',name='Refresh local data').click()
        self.page.wait_for_selector('[role="alert"]')
        self.assertTrue(self.page.get_by_role('button',name='Retry',exact=True).is_visible())
        self.page.unroute('**/api/runs')
        self.page.get_by_role('button',name='Retry',exact=True).click()
        self.page.wait_for_selector('[role="alert"]',state='detached')


if __name__=='__main__':
    unittest.main()
