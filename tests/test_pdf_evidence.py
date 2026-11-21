"""PDF receipt handoff: real parser, temporary store, current HTTP paths, and lab verdicts only."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid
from xml.etree import ElementTree
from pypdf import PdfWriter, PdfReader
from pypdf.generic import DictionaryObject, DecodedStreamObject, NameObject
from core import documents as D
from core.chat_store import ChatStore
from qualify.receipt_contract import evaluate
import tests.test_documents as T


def pdf(pages):
    w=PdfWriter(); ft=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}); fr=w._add_object(ft)
    for lines in pages:
        p=w.add_blank_page(width=720,height=800); p[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):fr})}); commands=[]
        for x,y,text in lines:
            escaped=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)');commands.append(f'BT /F1 9 Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET')
        stream=DecodedStreamObject();stream.set_data('\n'.join(commands).encode('ascii'));p[NameObject('/Contents')]=w._add_object(stream)
    f=io.BytesIO();w.write(f);return f.getvalue()

RULE='Cedar requires strictly above 98 percent uptime for the entire month.'
ACTUAL='The signed full-month uptime ledger for Cedar shows 99 percent.'
NOTICE='[1] Notice: Ledger.pdf is withdrawn without replacement for Cedar.'
Q='Did Cedar meet its full-month uptime commitment?'

def evdocs(docs):
    return [dict(id=x['id'],name=x['name'],pages=D.evidence_pages(x)) for x in docs]

class EvidencePDF(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ChatStore(Path(self.tmp.name)/'test.db');self.sid=self.store.create_session(uuid.uuid4().hex)['id']
    def add(self,name,pages,session=None):
        body=pdf(pages);return self.store.add_document(session or self.sid,name,hashlib.sha256(body).hexdigest(),len(body),D.extract_pages(body))
    def test_pdf_roundtrip_footnote_raw_search_and_offsets(self):
        self.add('Policy.pdf',[[(40,720,RULE)]]);self.add('Ledger.pdf',[[(40,730,ACTUAL),(40,545,NOTICE)]]); docs=self.store.documents(self.sid);ledger=next(d for d in docs if d['name']=='Ledger.pdf');self.assertNotIn('\n',ledger['pages'][0]);raw=D.evidence_pages(ledger)[0];self.assertIn('\n',raw);self.assertEqual(D.normalize(raw),ledger['pages'][0]);ex=D.select_excerpts([ledger],'withdrawn without replacement')[0];span=ex['source_span'];self.assertEqual(span['page_sha256'],hashlib.sha256(raw.encode()).hexdigest());self.assertEqual(raw[span['start']:span['end']],ex['text']);self.assertIn('\n',ex['text']);v=evaluate(Q,evdocs(docs));self.assertEqual(v['result']['status'],'WITHDRAWN');self.assertEqual([x['quote'] for x in v['rendered']['citations']],[NOTICE,ACTUAL]);self.assertFalse(any('source_pages' in x for x in self.store.session(self.sid)['documents']))
    def test_offsets_hard_window_and_repeated_text(self):
        raw=('Prelude item.\r\n'*105)+'Ledger result:\t reference cedar was signed.\n'+'context. '*163; record={'id':'a','name':'report.pdf','pages':[D.normalize(raw)],'source_pages':[raw]}; e=D.select_excerpts([record],'reference cedar result')[0];span=e['source_span'];self.assertGreater(span['start'],0);self.assertEqual(raw[span['start']:span['end']],e['text']);self.assertIn('reference cedar',e['text']);self.assertLessEqual(len(e['text']),D.WINDOW_CHARS);self.assertFalse(e['text'].startswith('...'))
    def test_no_fabricated_evidence_for_legacy_oversize_or_mismatch(self):
        for d in [{'pages':['old search text']},{'pages':['old search text'],'source_pages':[None]},{'pages':['old search text'],'source_pages':['different text']},{'pages':['old search text'],'source_pages':[]}]:
            with self.subTest(d=d),self.assertRaises(D.DocumentError):D.evidence_pages(d)
            self.assertNotIn('source_span',D.select_excerpts([dict(d,name='old.pdf')],'search')[0]);
        huge=' '*(D.MAX_PAGE_CHARS+1)+'Readable text retained from a large page.'; pack=D.ExtractedPages([huge]);self.assertIn('Readable text',pack[0]);self.assertEqual(pack.source_pages,(None,));self.assertEqual(list(pack),[D.normalize(huge)])
    def test_unicode_whitespace_mapping_and_long_blank_run(self):
        for raw in [' \tCafé\u00a0\u00a0résult\r\n x 🐋  ','Prelude ' + ' '*1400 + 'reference cedar ledger confirmed.']:
            e=D.select_excerpts([{'name':'p.pdf','pages':[D.normalize(raw)],'source_pages':[raw]}],'reference cedar')[0];p=e['source_span'];self.assertEqual(raw[p['start']:p['end']],e['text']);self.assertLessEqual(len(e['text']),D.WINDOW_CHARS)
            if 'confirmed' in raw:self.assertIn('reference',e['text'])
    def test_cross_page_amendment_is_not_withdrawal(self):
        self.add('Draft.pdf',[[(40,735,'The signed quarter-end ledger records 44 million dollars of unrestricted cash for Alder.')]]);self.add('Terms.pdf',[[(40,735,'Alder must hold at least 41 million dollars of unrestricted cash at quarter end.')],[(40,735,'The signed quarter-end reconciliation supersedes the draft and records 40 million dollars of unrestricted cash for Alder.')]]);v=evaluate('Did Alder meet its quarter-end unrestricted cash floor?',evdocs(self.store.documents(self.sid)));self.assertEqual((v['result']['status'],v['result']['verdict']),('VERIFIED','missed'));self.assertEqual(v['result']['observation']['page'],2);self.assertEqual(v['result']['superseded'][0]['document'],'Draft.pdf')
    def test_other_page_conflict_is_not_silently_dropped(self):
        self.add('Policy.pdf',[[(40,710,RULE)]]);self.add('Ledger.pdf',[[(40,710,ACTUAL)],[(40,710,ACTUAL.replace('99 percent','97 percent'))]]);v=evaluate(Q,evdocs(self.store.documents(self.sid)));self.assertEqual(v['result']['status'],'CONFLICT');self.assertEqual({x['page'] for x in v['rendered']['citations']},{1,2})
    def test_unbound_table_remains_uninterpreted(self):
        self.add('Policy.pdf',[[(40,710,'Cedar must hold at least 45 million dollars of unrestricted cash at quarter end.')]]);self.add('Table.pdf',[[(40,745,'Cash balances at quarter end'),(40,700,'Total cash'),(285,700,'Unrestricted cash'),(40,675,'67'),(285,675,'43')]]);v=evaluate('Did Cedar meet its quarter-end unrestricted cash floor?',evdocs(self.store.documents(self.sid)));self.assertEqual(v['result']['status'],'UNINTERPRETED');self.assertEqual(v['result']['blocker_span']['document'],'Table.pdf')
    def test_sidecar_shares_owner_dedup_and_deletion_lifetime(self):
        lines=[[(40,720,ACTUAL),(40,685,NOTICE)]];first=self.add('Ledger.pdf',lines);again=self.add('Ledger.pdf',lines);self.assertTrue(again['duplicate']);self.assertEqual(first['id'],again['id']);other=self.store.create_session(uuid.uuid4().hex)['id'];mine=self.add('Ledger.pdf',lines,other);self.store.remove_document(self.sid,first['id']);self.assertEqual(self.store.documents(self.sid),[]);self.assertIn(NOTICE,D.evidence_pages(self.store.documents(other)[0])[0]);self.store.delete_session(other)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM pdf_source_v1').fetchone()[0],0)
    def test_old_row_not_migrated_to_false_raw_text(self):
        self.store.add_document(self.sid,'Old.pdf','1'*64,100,['Previously flattened page.']);row=self.store.documents(self.sid)[0];self.assertNotIn('source_pages',row);self.assertIn('Previously flattened',D.select_excerpts([row],'page')[0]['text']);self.assertNotIn('source_span',D.select_excerpts([row],'page')[0])

class PdfUploadEvidence(unittest.TestCase):
    def test_http_upload_real_store_and_facts_reply(self):
        api=T.TestChatWithDocuments();api.setUp();self.addCleanup(api.tearDown);body=pdf([[(40,742,ACTUAL),(40,650,NOTICE)]]);status,doc=api.upload(body,'Ledger.pdf');self.assertEqual(status,200);raw=D.evidence_pages(api.service.store.documents(api.sid)[0])[0];self.assertIn('\n',raw);run=api.service.submit(api.sid,'What did the ledger say about withdrawal?',uuid.uuid4().hex,'facts');T.wait(api.service,run);answer=api.service.store.session(api.sid)['messages'][-1];self.assertIn(NOTICE,answer['content']);self.assertIn('\n',answer['content'].split(ACTUAL)[1].split(NOTICE)[0]);self.assertEqual(answer['payload']['document_sources'][0]['page'],1);self.assertEqual(api.call('POST','/api/documents/remove',json.dumps({'session_id':api.sid,'document_id':doc['id']}),{'Content-Type':'application/json'})[0],200);self.assertEqual(api.service.store.documents(api.sid),[])
    @unittest.skipUnless(shutil.which('pdftotext') and shutil.which('pdftoppm'),'Optional independent Poppler renderer unavailable')
    def test_independent_render_places_notice_below_record(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'input.pdf';p.write_bytes(pdf([[(40,742,ACTUAL),(40,540,NOTICE)]]));xml=subprocess.check_output(['pdftotext','-bbox',str(p),'-'],timeout=12);ws=list(ElementTree.fromstring(xml).iter());pick=lambda a:next(x for x in ws if x.tag.endswith('word') and x.text==a);delta=float(pick('Notice:').attrib['yMin'])-float(pick('signed').attrib['yMin']);self.assertGreater(delta,150);subprocess.run(['pdftoppm','-singlefile','-scale-to','600','-png',str(p),str(root/'view')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=12);self.assertTrue((root/'view.png').read_bytes().startswith(b'\x89PNG'));rr=PdfReader(io.BytesIO(p.read_bytes()));self.assertIn(NOTICE,rr.pages[0].extract_text())
