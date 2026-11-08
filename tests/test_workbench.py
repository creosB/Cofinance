import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from core.trace import TraceRecorder
from ui.workbench import PAGE, create_server, list_runs, read_trace


class TestTraceFiles(unittest.TestCase):
    def test_file_reads_require_a_known_id_and_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trace = TraceRecorder("Real local file", directory=root)
            trace.call("Inspect", "tool", lambda: {"api_key": "never expose"})
            trace.finish()
            data = read_trace(root, trace.run_id)
            self.assertEqual(data["spans"][0]["outputs"]["api_key"], "[redacted]")
            self.assertEqual(len(list_runs(root)), 1)
            for value in ("../secret", "", "A" * 32, "file.json"):
                with self.assertRaises(ValueError):
                    read_trace(root, value)
            (root / ("a" * 32 + ".json")).write_text('{"format":"wrong"}')
            self.assertEqual(len(list_runs(root)), 1)

    def test_surface_is_self_contained_and_accessible_by_keyboard(self):
        self.assertNotIn("fonts.googleapis", PAGE)
        self.assertNotIn("cdn.", PAGE)
        self.assertIn("aria-label=\"Filter execution steps\"", PAGE)
        self.assertIn("prefers-reduced-motion", PAGE)
        self.assertIn("textContent=json(s.outputs)", PAGE)
        self.assertIn("Control+k Meta+k", PAGE)
        self.assertNotIn("—", PAGE)
        self.assertNotIn("–", PAGE)


class TestWorkbenchServer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trace = TraceRecorder("Test local run", directory=self.root)
        self.trace.finish()
        self.server = create_server(port=0, trace_dir=self.root, corpus_dir=self.root / "missing")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path="/", method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        status, data, headers = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return status, data, headers

    def test_local_only_read_api_and_security_headers(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, data, headers = self.request()
        self.assertEqual(status, 200)
        self.assertIn(b"Cofinance", data)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        import hashlib
        self.assertEqual(headers["X-Cofinance-UI"], hashlib.sha256(data).hexdigest())
        self.assertEqual(data, PAGE.encode("utf-8"))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, data, _ = self.request("/api/runs")
        self.assertEqual(len(json.loads(data)), 1)
        status, data, _ = self.request("/api/run?id=" + self.trace.run_id)
        self.assertEqual(json.loads(data)["id"], self.trace.run_id)
        status, data, _ = self.request("/api/corpus")
        self.assertIsNone(json.loads(data))

    def test_cross_origin_and_dns_rebinding_requests_are_refused(self):
        for headers in ({"Host": "attacker.test"}, {"Origin": "https://attacker.test"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/runs", headers=headers)[0], 403)

    def test_clicked_external_link_can_open_only_the_ui_shell(self):
        navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                      "Sec-Fetch-Dest": "document", "Sec-Fetch-User": "?1"}
        status, data, headers = self.request("/", headers=navigation)
        self.assertEqual(status, 200)
        self.assertEqual(data, PAGE.encode("utf-8"))
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path in ("/api/runs", "/api/corpus", "/api/run?id=" + self.trace.run_id, "/?query=1", "/other"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, headers=navigation)[0], 403)

    def test_cross_site_frames_fetches_and_nonlocal_origins_stay_blocked(self):
        navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                      "Sec-Fetch-Dest": "document", "Sec-Fetch-User": "?1"}
        variants = [{**navigation, "Sec-Fetch-Dest": "iframe"},
                    {**navigation, "Sec-Fetch-Mode": "cors"},
                    {k: v for k, v in navigation.items() if k != "Sec-Fetch-User"},
                    {**navigation, "Origin": "https://attacker.test"},
                    {**navigation, "Host": "attacker.test"}]
        for headers in variants:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/", headers=headers)[0], 403)

    def test_no_filesystem_traversal_or_write_endpoints(self):
        for path in ("/api/run?id=../secret", "/api/run?id=" + self.trace.run_id + "&other=1", "/data/raw.json", "/.env"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/api/runs", method="POST")[0], 501)

    def test_corpus_only_returns_summary_not_private_headlines(self):
        corpus = self.root / "corpus"
        corpus.mkdir()
        (corpus / "manifest.json").write_text(json.dumps({"rows": 53, "private_extra": "hidden", "by_source": {"hackernews": 13}}))
        other = create_server(port=0, trace_dir=self.root, corpus_dir=corpus)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", other.server_port, timeout=3)
            connection.request("GET", "/api/corpus")
            response = connection.getresponse()
            data = json.loads(response.read())
            connection.close()
            self.assertEqual(data["rows"], 53)
            self.assertNotIn("private_extra", data)
        finally:
            other.shutdown()
            other.server_close()
            thread.join(timeout=2)


@unittest.skipUnless(__import__("os").environ.get("COFINANCE_BROWSER_TESTS") == "1",
                     "set COFINANCE_BROWSER_TESTS=1 to exercise installed Chrome with Playwright")
class TestWorkbenchBrowser(unittest.TestCase):
    """Uses only marked fixtures and a fresh ephemeral loopback server per test.

    No downloads or global package changes. Chrome and Playwright must already exist.
    """
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        try:
            cls.browser = cls.playwright.chromium.launch(executable_path=str(chrome), headless=True)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trace = TraceRecorder("Browser test fixture", mode="fixture", directory=self.root)
        with self.trace.span("Test execution", "run"):
            self.trace.event("Signal gate: DIGEST", "gate", {
                "title": "Literal <img src=x onerror=alert(1)> evidence", "ticker": "TEST",
                "action": "digest", "reason": "material_not_urgent", "url": "javascript:alert(1)",
                "judgment": {"material": .6, "urgent": .2, "direction": "neutral"},
                "thresholds": {"digest_p": .5, "alert_p": .85, "alert_urgent_p": .7},
                "excerpt": "Long fixture evidence. " * 150,
            })
        self.trace.finish()
        self.server = create_server(port=0, trace_dir=self.root, corpus_dir=self.root / "no-corpus")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        response = self.page.goto(f"http://127.0.0.1:{self.server.server_port}", wait_until="networkidle")
        self.assertEqual(response.body(), PAGE.encode("utf-8"))
        self.page.wait_for_selector(".trace-row")

    def tearDown(self):
        self.page.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def inspect_gate(self):
        self.page.locator("[data-filter=gate]").click()
        self.page.locator(".trace-row").click()

    def test_mobile_layout_fits_without_hiding_document_overflow(self):
        for width in (320, 390, 620, 768, 1024, 1440):
            self.page.set_viewport_size({"width": width, "height": 900})
            self.page.wait_for_timeout(80)
            with self.subTest(width=width):
                self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                self.assertEqual(self.page.evaluate("getComputedStyle(document.documentElement).overflowX"), "visible")
                self.assertTrue(self.page.locator("#palette-open").is_visible())
                if width <= 620:
                    for view in ("models", "dataset", "execution"):
                        self.page.locator("#mobile-nav").select_option(view)
                        self.assertTrue(self.page.locator("#" + view + "-view").is_visible())
                        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))

    def test_inspector_disclosures_survive_real_trace_updates(self):
        self.inspect_gate()
        detail = self.page.locator("#inspector details").first
        detail.locator("summary").click()
        self.assertIsNotNone(detail.get_attribute("open"))
        self.page.locator("#output-data").evaluate("el => el.scrollTop=100")
        self.page.locator("#inspector details").nth(1).locator("summary").focus()
        self.trace.data["spans"][0]["outputs"] = {"new_observation": True}
        self.trace.flush()
        self.page.wait_for_timeout(2900)
        self.assertIsNotNone(detail.get_attribute("open"))
        self.assertEqual(self.page.locator("#output-data").evaluate("el => el.scrollTop"), 100)
        self.assertEqual(self.page.evaluate("document.activeElement.closest('details')?.dataset.section"), "input")
        self.assertEqual(self.page.locator(".trace-row[aria-pressed=true]").count(), 1)
        self.assertEqual(self.page.locator("[data-filter=gate]").get_attribute("aria-pressed"), "true")

    def test_evidence_is_text_and_scores_have_explicit_policy_lines(self):
        self.inspect_gate()
        self.assertEqual(self.page.locator("#inspector img").count(), 0)
        self.assertEqual(self.page.locator("#inspector .evidence-link").count(), 0)
        self.assertIn("<img", self.page.locator(".decision-headline").inner_text())
        self.assertTrue(self.page.get_by_role("table", name="Recorded decision scores").is_visible())
        self.assertIn("0.8500", self.page.locator(".score-table").inner_text())
        self.assertTrue(self.page.get_by_text("Model scores are not calibrated probabilities.").is_visible())

    def test_clicked_link_from_another_site_opens_the_local_workbench(self):
        # Intercepted fixture page: no external network request or data disclosure.
        source = "https://navigation-fixture.test/"
        target = f"http://127.0.0.1:{self.server.server_port}/"
        self.page.route(source, lambda route: route.fulfill(
            content_type="text/html", body=f'<a href="{target}">Open local workbench</a>'))
        self.page.goto(source)
        with self.page.expect_navigation(wait_until="networkidle") as navigation:
            self.page.get_by_role("link", name="Open local workbench").click()
        response = navigation.value
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body(), PAGE.encode("utf-8"))
        # Header-policy combinations are asserted in TestWorkbenchServer.
        # Chromium's intercepted fixture navigation may omit Fetch Metadata.
        self.page.wait_for_selector(".trace-row")

    def test_escape_closes_finder_with_nonempty_search(self):
        self.page.keyboard.press("Control+k")
        self.page.locator("#palette-search").fill("nonempty query")
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector("#palette", state="hidden")
        self.page.locator("#search").fill("no_matching_step")
        self.assertTrue(self.page.get_by_text("No matching steps").is_visible())
        self.page.locator("#clear-filters").click()
        self.assertEqual(self.page.locator(".trace-row").count(), 2)


if __name__ == "__main__":
    unittest.main()
