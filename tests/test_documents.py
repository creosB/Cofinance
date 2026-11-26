"""Attached PDFs (2026-11-19): parsing limits, excerpt choice, number guard, storage, API and the chat run."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from core import documents as docs
from core.chat_store import ChatStore
from core.gliner_decide import RoutedQuery
from core.orchestrator import DataAnalystAgent, DocumentReaderAgent, EventBus, NewsResearcherAgent, TeamLeadOrchestrator
from tests.test_chat import service, wait
from tests.test_orchestrator import StubNewsStore, StubTools
from ui.dashboard import create_server


def make_pdf(pages):
    """A minimal valid PDF, one text line per page (None = a page with no text layer, like a scan)."""
    objs = ['<< /Type /Catalog /Pages 2 0 R >>', None, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for text in pages:
        stream = '' if text is None else 'BT /F1 12 Tf 72 720 Td (' + text.replace('(', '').replace(')', '') + ') Tj ET'
        objs.append(f'<< /Length {len(stream)} >>\nstream\n{stream}\nendstream')
        objs.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>')
        kids.append(len(objs))
    objs[1] = f'<< /Type /Pages /Kids [{" ".join(f"{k} 0 R" for k in kids)}] /Count {len(kids)} >>'
    out, offsets = b'%PDF-1.4\n', []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f'{i} 0 obj\n{body}\nendobj\n'.encode('latin-1')
    xref = len(out)
    out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode() + b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    out += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return out


REPORT = make_pdf(['Quarterly report for Example Corp', 'Revenue grew to 412 million dollars in the third quarter',
                   'Outlook: management expects margins near 18 percent'])


class TestParsing(unittest.TestCase):
    def test_text_pages_come_back_in_order(self):
        pages = docs.extract_pages(REPORT)
        self.assertEqual(len(pages), 3)
        self.assertIn('412 million', pages[1])

    def test_non_pdf_scanned_and_empty_files_are_refused_plainly(self):
        for data, words in ((b'hello', 'not a PDF'), (b'', 'empty'), (make_pdf([None, None]), 'no text layer')):
            with self.assertRaises(docs.DocumentError) as caught:
                docs.extract_pages(data)
            self.assertIn(words, str(caught.exception))

    def test_size_page_and_time_limits(self):
        with self.assertRaises(docs.DocumentError) as caught:
            docs.extract_pages(b'%PDF-' + b'0' * (docs.MAX_BYTES + 1))
        self.assertIn('20 MB', str(caught.exception))
        with self.assertRaises(docs.DocumentError) as caught:
            docs.extract_pages(REPORT, command=[sys.executable, '-B', str(docs.WORKER), '2'])
        self.assertIn('limited to 2 pages', str(caught.exception))
        with self.assertRaises(docs.DocumentError) as caught:
            docs.extract_pages(REPORT, timeout=0.5, command=[sys.executable, '-c', 'import time; time.sleep(5)'])
        self.assertIn('too long', str(caught.exception))

    def test_names_are_cleaned_and_must_be_pdf(self):
        self.assertEqual(docs.clean_name('C:\\Users\\x\\report.pdf'), 'report.pdf')
        for bad in ('notes.txt', '', None, 'a' * 130 + '.pdf'):
            with self.assertRaises(docs.DocumentError):
                docs.clean_name(bad)


class TestExcerpts(unittest.TestCase):
    def setUp(self):
        self.docs = [{'id': 'd1', 'name': 'q3.pdf', 'pages': docs.extract_pages(REPORT)}]

    def test_the_page_sharing_the_question_words_wins(self):
        top = docs.select_excerpts(self.docs, 'What revenue did the third quarter show?')
        self.assertEqual(top[0]['page'], 2)
        self.assertTrue(top[0]['matched'])

    def test_no_shared_word_falls_back_to_the_first_pages_marked_unmatched(self):
        top = docs.select_excerpts(self.docs, 'Summarize this')
        self.assertEqual([e['page'] for e in top], [1, 2])
        self.assertFalse(any(e['matched'] for e in top))


class DocRouter:
    def route(self, text):
        return RoutedQuery('thesis_review', .9, 'swing_week', [])


class Says:
    def __init__(self, text):
        self.text = text

    def complete(self, *args, **kwargs):
        return self.text


def team(llm):
    tools = StubTools()
    return TeamLeadOrchestrator(DocRouter(), DataAnalystAgent(llm, tools), NewsResearcherAgent(llm, StubNewsStore()), llm, tools)


class TestDocumentAnswers(unittest.TestCase):
    def setUp(self):
        self.docs = [{'id': 'd1', 'name': 'q3.pdf', 'pages': docs.extract_pages(REPORT)}]

    def test_reader_allows_only_excerpt_and_page_numbers(self):
        rep = DocumentReaderAgent().run('revenue third quarter', self.docs, EventBus(), 's')
        self.assertIn(412e6, rep.allowed_numbers)  # the guard reads "412 million" with its scale
        self.assertIn(2.0, rep.allowed_numbers)
        self.assertNotIn(18.0, rep.allowed_numbers)  # page 3 was not quoted for this question

    def test_an_answer_with_an_excerpt_number_is_accepted(self):
        result = team(Says('Revenue grew to 412 million dollars in the third quarter, page 2.')).run('What was revenue?', documents=self.docs)
        self.assertTrue(result.faithful_synthesis)
        self.assertIn('412', result.final_synthesis)

    def test_an_invented_number_falls_back_to_the_quoted_pages(self):
        result = team(Says('Revenue grew to 999 million dollars.')).run('What was revenue?', documents=self.docs)
        self.assertFalse(result.faithful_synthesis)
        self.assertNotIn('999', result.final_synthesis)
        self.assertIn('page 2', result.final_synthesis)

    def test_without_documents_nothing_changes(self):
        result = team(Says('')).run('What was revenue?')
        self.assertIsNone(result.document_report)

    def test_multi_document_excerpts_stay_grouped_by_source_and_ascending_page(self):
        doc_a = {
            'id': 'doc-a',
            'name': 'Circular_47.pdf',
            'pages': [
                'Household checking accounts may waive transfer fees each month.',
                'Branch lobby hours run Monday through Friday.',
                'The household transfer fee waiver applies only to the first transfer each month.',
            ],
        }
        doc_b = {
            'id': 'doc-b',
            'name': 'Circular_48.pdf',
            'pages': [
                'Business checking accounts may waive transfer fees on domestic accounts.',
            ],
        }
        rep = DocumentReaderAgent().run('transfer fee waiver household business', [doc_a, doc_b], EventBus(), 's')
        self.assertEqual(rep.validation['document_count'], 2)
        self.assertEqual([g['name'] for g in rep.raw_facts['groups']], ['Circular_47.pdf', 'Circular_48.pdf'])
        self.assertEqual(rep.raw_facts['groups'][0]['pages'], [1, 3])
        self.assertEqual(rep.raw_facts['groups'][1]['pages'], [1])
        self.assertEqual([(e['name'], e['page']) for e in rep.raw_facts['excerpts']], [
            ('Circular_47.pdf', 1),
            ('Circular_47.pdf', 3),
            ('Circular_48.pdf', 1),
        ])
        self.assertIn('grouped by source file', rep.summary)
        self.assertIn('Source "Circular_47.pdf":', rep.summary)
        self.assertIn('Source "Circular_48.pdf":', rep.summary)
        idx_a1 = rep.summary.index('Page 1: "Household')
        idx_a3 = rep.summary.index('Page 3: "The household')
        idx_b = rep.summary.index('Source "Circular_48.pdf":')
        self.assertLess(idx_a1, idx_a3)
        self.assertLess(idx_a3, idx_b)

    def test_multi_document_synthesis_prompt_scopes_restrictions_to_source_file(self):
        seen_prompts = []

        class CaptureLLM:
            def complete(self, system, prompt, **kwargs):
                seen_prompts.append(prompt)
                return 'Circular 47 covers household accounts, page 1.'

        doc_a = {'id': 'a', 'name': 'Circular_47.pdf', 'pages': ['Household fee waiver for transfer accounts.']}
        doc_b = {'id': 'b', 'name': 'Circular_48.pdf', 'pages': ['Business fee waiver for transfer accounts.']}
        team(CaptureLLM()).run('Compare transfer fee waivers', documents=[doc_a, doc_b])
        self.assertTrue(seen_prompts)
        self.assertIn('keep each fact and restriction inside its own source file', seen_prompts[0])
        self.assertIn('Source "Circular_47.pdf":', seen_prompts[0])
        self.assertIn('Source "Circular_48.pdf":', seen_prompts[0])


class TestStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.temp.name) / 'chat_v2.db')
        self.sid = self.store.create_session(uuid.uuid4().hex)['id']

    def tearDown(self):
        self.temp.cleanup()

    def add(self, n=0):
        return self.store.add_document(self.sid, f'f{n}.pdf', f'{n:064x}', 10, ['page text'])

    def test_same_file_is_one_document_and_the_cap_is_five(self):
        first = self.add(0)
        self.assertTrue(self.add(0)['duplicate'])
        self.assertEqual(self.add(0)['id'], first['id'])
        for n in range(1, 5):
            self.add(n)
        with self.assertRaises(ValueError):
            self.add(9)
        listed = self.store.session(self.sid)['documents']
        self.assertEqual(len(listed), 5)
        self.assertNotIn('pages', listed[0])  # the conversation view never carries page text

    def test_remove_and_conversation_delete_take_the_text(self):
        one, two = self.add(1), self.add(2)
        self.store.remove_document(self.sid, one['id'])
        with self.assertRaises(KeyError):
            self.store.remove_document(self.sid, one['id'])
        self.assertEqual([d['id'] for d in self.store.documents(self.sid)], [two['id']])
        self.store.delete_session(self.sid)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_documents').fetchone()[0], 0)

    def test_another_conversation_cannot_remove_it(self):
        doc = self.add(1)
        other = self.store.create_session(uuid.uuid4().hex)['id']
        with self.assertRaises(KeyError):
            self.store.remove_document(other, doc['id'])


class TestChatWithDocuments(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = service(root)
        self.dist = root / 'dist'
        self.dist.mkdir()
        (self.dist / 'index.html').write_text('<div id="root"></div>')
        self.server = create_server(port=0, dist=self.dist, trace_dir=root / 'traces', corpus_dir=root / 'missing', service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.token = self.call('GET', '/api/state')[1]['csrf']
        self.sid = self.service.store.create_session(uuid.uuid4().hex)['id']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.service.close()
        self.temp.cleanup()

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=60)
        h = {} if method == 'GET' else {'Origin': self.origin, 'X-Cofinance-CSRF': getattr(self, 'token', '')}
        h.update(headers or {})
        conn.request(method, path, body=body, headers=h)
        response = conn.getresponse()
        status, value = response.status, json.loads(response.read() or b'{}')
        conn.close()
        return status, value

    def upload(self, data=REPORT, name='q3.pdf', headers=None):
        return self.call('POST', f'/api/documents/upload?session_id={self.sid}&name={name}', data,
                         {'Content-Type': 'application/pdf', **(headers or {})})

    def test_upload_checks_origin_type_and_size(self):
        self.assertEqual(self.upload(headers={'X-Cofinance-CSRF': 'wrong'})[0], 403)
        self.assertEqual(self.upload(headers={'Content-Type': 'application/json'})[0], 415)
        self.assertEqual(self.upload(b'not a pdf at all')[0], 400)
        self.assertEqual(self.upload(name='notes.txt')[0], 400)
        self.assertEqual(self.service.store.session(self.sid)['documents'], [])
        status, doc = self.upload()
        self.assertEqual((status, doc['page_count'], doc['name']), (200, 3, 'q3.pdf'))
        status, _ = self.call('POST', '/api/documents/remove', json.dumps({'session_id': self.sid, 'document_id': doc['id']}),
                              {'Content-Type': 'application/json'})
        self.assertEqual(status, 200)
        self.assertEqual(self.service.store.session(self.sid)['documents'], [])

    def test_a_declared_size_over_the_limit_is_refused_before_reading(self):
        status, _ = self.upload(b'%PDF-1.4 tiny', headers={'Content-Length': str(docs.MAX_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertEqual(self.service.store.session(self.sid)['documents'], [])

    def test_files_cannot_change_while_a_run_in_the_conversation_is_active(self):
        from core.local_models import ModelError
        run = uuid.uuid4().hex
        self.service.store.create_job(run, uuid.uuid4().hex, self.sid, 'NVDA', 'facts')
        self.service.active = run
        try:
            with self.assertRaises(ModelError):
                self.service.add_document(self.sid, 'q3.pdf', REPORT)
        finally:
            self.service.active = None
        self.assertEqual(self.service.store.session(self.sid)['documents'], [])

    def test_question_without_an_asset_answers_from_the_file(self):
        self.assertEqual(self.upload()[0], 200)
        job = self.service.submit(self.sid, 'What does it say about revenue?', uuid.uuid4().hex, 'facts')
        wait(self.service, job)
        answer = self.service.store.session(self.sid)['messages'][-1]
        self.assertEqual(answer['payload']['kind'], 'research')
        self.assertIn('412 million', answer['content'])
        self.assertEqual(answer['payload']['document_sources'][0]['page'], 2)

    def test_without_files_the_same_question_still_asks_for_an_asset(self):
        job = self.service.submit(self.sid, 'What does it say about revenue?', uuid.uuid4().hex, 'facts')
        wait(self.service, job)
        self.assertEqual(self.service.store.session(self.sid)['messages'][-1]['payload']['kind'], 'clarification')


if __name__ == '__main__':
    unittest.main()
