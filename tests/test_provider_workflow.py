import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from core.chat_service import ChatService
from core.market import MarketTools, fetch_json
from tests.execution_proof import capture_execution, require_clean_execution, require_saved_trace
from ui.dashboard import create_server

REPO = Path(__file__).resolve().parents[1]
JOURNEYS = (
    ('london_followup', (
        ('Quote VOD.L in London. Show the last quote and its exchange currency.', 'research', {'VOD.L': 'GBp'}, False),
        ('What was its previous close?', 'research', {'VOD.L': 'GBp'}, False),
    )),
    ('hong_kong_numeric', (
        ('What is 0700.HK trading at?', 'research', {'0700.HK': 'HKD'}, False),
    )),
    ('decision', (
        ('Should I buy SAP.DE now?', 'research', {'SAP.DE': 'EUR'}, True),
    )),
    ('foreign_comparison', (
        ('Compare IBM and Nintendo on the Tokyo exchange.', 'research', {'IBM': 'USD', '7974.T': 'JPY'}, False),
    )),
    ('quoted_then_conceptual', (
        ('What is IBM trading at?', 'research', {'IBM': 'USD'}, False),
        ('What is an ADR, in general?', 'conversation', {}, False),
    )),
    ('contradictory_listing', (
        ('What is 7203.T, the Turkish stock, trading at?', 'clarification', {}, False),
    )),
)


VALUATION_REGRESSIONS = (
    ('valuation', (
        ('How much is Amazon worth?', 'clarification', {}, False),
        ('Market cap, please.', 'research', {'AMZN': 'USD'}, False),
        ('What is Amazon enterprise value?', 'clarification', {}, False),
    )),
)


def stage_models(target):
    source = REPO / 'data' / 'local_models'
    target.mkdir()
    for item in source.iterdir():
        if item.is_dir() and item.name.startswith('b12110'):
            shutil.copytree(item, target / item.name, copy_function=os.link)
        elif item.is_file() and (item.name.startswith('gliner_standalone_') or item.suffix in {'.gguf', '.zip'}):
            os.link(item, target / item.name)
        elif item.name in {'state.json', 'qualification.json', 'think_qualification.json', 'runtime-receipt.json'}:
            shutil.copy2(item, target / item.name)


@unittest.skipUnless(os.environ.get('COFINANCE_PROVIDER_BROWSER_TESTS') == '1'
                     and os.environ.get('COFINANCE_NETWORK_TESTS') == '1', 'opt-in Yahoo and Chrome journey')
class TestProviderWorkflow(unittest.TestCase):
    def test_frozen_journeys(self):
        self.check_journeys(JOURNEYS + VALUATION_REGRESSIONS)

    @unittest.skipUnless(os.environ.get('COFINANCE_SHOWCASE_DIR'), 'run dev.py showcase-preview explicitly')
    def test_showcase_preview(self):
        destination = Path(os.environ['COFINANCE_SHOWCASE_DIR'])
        images = self.check_journeys(VALUATION_REGRESSIONS, capture=True)
        if set(images) != {'chat.png', 'execution.png'}:
            self.fail('Both verified showcase images are required before saving either image')
        destination.mkdir()
        for name, contents in images.items():
            (destination / name).write_bytes(contents)
        print(f'Preview only: {destination.relative_to(REPO)}', flush=True)

    def check_journeys(self, journeys, *, capture=False):
        from playwright.sync_api import expect, sync_playwright

        digest = hashlib.sha256(json.dumps(JOURNEYS, sort_keys=True).encode()).hexdigest()
        print(f'Provider journey protocol SHA-256: {digest}', flush=True)
        print('Valuation regression SHA-256: '+hashlib.sha256(json.dumps(VALUATION_REGRESSIONS, sort_keys=True).encode()).hexdigest(), flush=True)
        events = []
        images = {}

        def yahoo(url):
            uri = urlsplit(url)
            if uri.scheme != 'https' or uri.hostname not in {'query1.finance.yahoo.com', 'query2.finance.yahoo.com'}:
                raise AssertionError(f'Unexpected provider: {uri.hostname}')
            record = {'request': hashlib.sha256(url.encode()).hexdigest()[:16], 'path': uri.path}
            try:
                response = fetch_json(url)
                record['response'] = hashlib.sha256(json.dumps(response, sort_keys=True).encode()).hexdigest()[:16]
                if isinstance(response, dict) and 'quotes' in response:
                    record['candidates'] = [{k: r.get(k) for k in ('symbol', 'exchange', 'quoteType')}
                                            for r in response['quotes'] if isinstance(r, dict)]
                return response
            except (ValueError, OSError) as exc:
                record['failure'] = type(exc).__name__
                raise
            finally:
                events.append(record)

        def market(cancel=None):
            return MarketTools(fetch=yahoo, cancel=cancel)

        with tempfile.TemporaryDirectory(dir=REPO / 'data', prefix='_provider_audit_') as temporary, \
                patch.dict(os.environ, {'COFINANCE_MEMORY': 'keyword', 'COFINANCE_CROWD_ODDS': 'off',
                                        'COFINANCE_HEADLINE_SIGNAL': 'off', 'COFINANCE_ROUTER_ONNX': 'auto'}):
            root = Path(temporary)
            stage_models(root / 'local_models')
            service = ChatService(REPO, root, market_factory=market)
            self.assertNotEqual(service.models.state['selected'], 'facts', 'Pinned narrator must be qualified for this audit')
            self.assertTrue(service.models.installed_backends(), 'Pinned runtime archives must be staged')
            server = create_server(port=0, trace_dir=root / 'traces', corpus_dir=root / 'no_corpus', service=service)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            service.warm_router()
            service.warm_narrator()
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
                    try:
                        for name, turns in journeys:
                            with browser.new_context(viewport={'width': 1440, 'height': 1000 if capture else 900}) as context:
                                page = context.new_page()
                                page_errors = []
                                page.on('pageerror', lambda error: page_errors.append(str(error)))
                                page.set_default_timeout(90000)
                                page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='domcontentloaded')
                                existing = set(service.store.session_ids())
                                for index, (question, kind, expected, decision) in enumerate(turns):
                                    with self.subTest(journey=name, turn=index):
                                        before = len(events)
                                        box = page.get_by_role('textbox', name='Research question')
                                        box.fill(question)
                                        page.get_by_role('button', name='Send question').click()
                                        finished = page.locator('.msg-answer').nth(index)
                                        failure = page.locator('.error-banner')
                                        finished.or_(failure).first.wait_for(timeout=60000)
                                        self.assertEqual(failure.count(), 0, failure.all_inner_texts())
                                        session = service.store.session(next(iter(set(service.store.session_ids()) - existing)))
                                        answer = session['messages'][-1]
                                        payload = answer['payload']
                                        traces = root / 'traces'
                                        trace = require_saved_trace(traces, answer['run_id'], mode='live')
                                        card = page.locator('.msg-answer').nth(index)
                                        seen = {k: v.get('currency') for k, v in payload.get('snapshots', {}).items()}
                                        routes = [s.get('outputs') for s in trace['spans'] if s['name'] == 'Route conversational request']
                                        provider = events[before:]
                                        print(json.dumps({'journey': name, 'turn': index, 'expected': expected, 'seen': seen,
                                                          'kind': payload.get('kind'), 'decision': bool(payload.get('decision_assessment')),
                                                          'routes': routes, 'provider': provider}, ensure_ascii=True), flush=True)
                                        self.assertEqual(payload.get('kind'), kind)
                                        self.assertEqual(seen, expected)
                                        for symbol, unit in expected.items():
                                            self.assertIn(symbol, card.locator('.facts-asset').all_inner_texts()[list(seen).index(symbol)])
                                            self.assertIn(unit, card.inner_text())
                                            self.assertTrue(payload['snapshots'][symbol].get('exchange'))
                                            self.assertTrue(payload['snapshots'][symbol].get('quote_type'))
                                        self.assertEqual(card.locator('[data-quote-state]').count(), len(expected))
                                        self.assertEqual(card.locator('[data-decision-state="insufficient_evidence"]').count(), int(decision))
                                        if name == 'london_followup' and index == 1:
                                            self.assertRegex(payload.get('market_answer_note', ''), r'VOD\.L previous-session close: .+ GBp on \d{4}-\d{2}-\d{2}')
                                            self.assertIn(payload['market_answer_note'], answer['content'])
                                            self.assertFalse(any('/finance/search' in e['path'] for e in provider))
                                        if name == 'valuation':
                                            if index == 0:
                                                self.assertIn('Do you mean the share price', answer['content'])
                                            elif index == 1:
                                                self.assertTrue(answer['content'].startswith('AMZN market capitalization:'))
                                                self.assertGreater(payload['snapshots']['AMZN']['market_cap'], 0)
                                            else:
                                                self.assertIn('Enterprise value is not available', answer['content'])
                                            if index != 1:
                                                self.assertEqual(provider, [])
                                        if expected:
                                            self.assertTrue(any('/chart/' in e['path'] and 'response' in e for e in provider))
                                        if kind != 'research':
                                            self.assertFalse(any('/chart/' in e['path'] for e in provider))
                                        if capture and index == 1:
                                            expect(page.locator('.msg-answer').first).to_contain_text('Do you mean the share price')
                                            page.mouse.move(3, 3)
                                            page.evaluate('document.fonts.ready')
                                            page.locator('.chat-scroll').evaluate('el => el.scrollTop = 0')
                                            images['chat.png'] = page.screenshot(animations='disabled')
                                        card.get_by_role('button', name='Execution', exact=True).click()
                                        require_clean_execution(page, traces, answer['run_id'], mode='live')
                                        if capture and index == 1:
                                            page.locator('[data-map-stage="result"]').wait_for()
                                            page.locator('[data-map-stage="data"]').click()
                                            page.locator('.inspector').wait_for()
                                            page.mouse.move(3, 3)
                                            images['execution.png'] = capture_execution(page, traces, answer['run_id'])
                                        self.assertEqual(page_errors, [])
                                        page.locator('.sidebar').get_by_role('button', name='Chat', exact=True).click()
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)
                service.close()
        return images
