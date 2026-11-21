import contextlib
import copy
import io
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from qualify import counterevidence as C


class Counterevidence(unittest.TestCase):
    def test_new_packets_and_exact_original_receipts(self):
        cases=C.F.read(C.FIXTURE); C.check_cases(cases)
        self.assertFalse({c['subject'] for c in cases} & {r[1] for r in C.F.C.CONTRAST})
        total=unnamed=0
        for c in cases:
            q=C.F.view(c);sp=C.F.C.index(q['documents']); lookup={x['id']:x for x in sp}
            for i in c['counter_facets']:
                total+=1; unnamed+=all(c['subject'].casefold() not in lookup[k]['text'].casefold() for k in c['facets'][i])
            for x in sp:
                original=next(d[x['field']] for d in q['documents'] if d['id']==x['doc'])
                self.assertEqual(original[x['start']:x['end']],x['text']);self.assertEqual(x['sha'],C.P.digest(original.encode()))
            alt=dict(q,documents=list(reversed(q['documents'])));self.assertEqual(C.F.rule(q,sp),C.F.rule(alt,C.F.C.index(alt['documents'])))
        self.assertEqual(total,12);self.assertGreaterEqual(unnamed,6)
        for invalid in ('duplicate','unknown'):
            bad=copy.deepcopy(cases);primary=next(c for c in bad if len(c['facets'])==1)
            first=primary['facets'][0][0];primary['facets'][0].append(first if invalid=='duplicate' else 'ghost')
            with self.assertRaisesRegex(ValueError,'Unknown, duplicate or ambiguous key'):C.check_cases(bad)

    def test_every_span_visible_to_both_arms_no_key_no_verdict(self):
        c=C.F.read(C.FIXTURE)[0];q=C.F.view(c);sp=C.F.C.index(q['documents']);h=C.F.rule(q,sp)
        for arm in ('direct','second'):
            system,text=C.envelope(q,sp,arm,h)
            for v in sp: self.assertIn(v['id']+' '+v['text'],text)
            self.assertEqual('Untrusted highlights' in text,arm=='second')
            self.assertIn('not',system.lower());self.assertNotIn(c['id'],text);self.assertNotIn('counter_facets',text)
            with self.assertRaises(ValueError):C.envelope(q,sp[:-1],arm,h)
            with self.assertRaises(ValueError):C.envelope(dict(q,facets=[]),sp,arm,h)

    def test_union_keeps_seed_without_creating_evidence(self):
        self.assertEqual(C.combine(['x'],['z','x'],{'x','z'}),['x','z'])
        self.assertEqual(C.combine(['x'],[],{'x','z'}),['x'])
        for args in ((['x'],['ghost']),(['x','x'],[]),([],['z','z'])):
            with self.assertRaises(ValueError):C.combine(*args,{'x','z'})

    def test_noise_misses_and_malformed_empty(self):
        cs=C.F.read(C.FIXTURE);pos=next(c for c in cs if len(c['counter_facets'])==2);nil=next(c for c in cs if c['kind']=='empty')
        def row(ids,error=None):return dict(arm='second',ids=ids,error=error,input_tokens=0,output_tokens=0)
        ids=[f[0] for f in pos['facets']];g=C.score(pos,row(ids));self.assertTrue(g['exact']);self.assertEqual(g['counter_hits'],2)
        early=C.score(pos,row(ids[:1]));self.assertEqual(early['counter_hits'],0);self.assertEqual(early['counter_missed'],2)
        self.assertTrue(C.score(nil,row([]))['exact']);self.assertFalse(C.score(nil,row([],'truncated'))['exact'])
        v=C.F.C.index(C.F.view(nil)['documents'])[0]['id'];self.assertEqual(C.score(nil,row([v]))['noisy_controls'],1)

    def test_frozen_advance_rule_and_every_veto(self):
        v=dict(n=20,valid=20,counter_hits=8,unnamed_hits=5,exact=12,tp=18,fp=3,noisy_controls=1)
        s={z:{a:dict(v) for a in C.ARMS} for z in ('all',*C.KINDS)};s['all']['direct'].update(counter_hits=7);s['all']['seed'].update(counter_hits=4,unnamed_hits=3)
        self.assertEqual(C.gate(s),'INDEPENDENT_RETEST_ONLY')
        for z,a,k,val,want in [('all','second','counter_hits',7,'NO_PROMOTION'),('all','seed','counter_hits',8,'NO_PROMOTION'),('restriction','direct','counter_hits',9,'NO_PROMOTION'),('all','second','unnamed_hits',4,'NO_PROMOTION'),('all','second','exact',11,'NO_PROMOTION'),('all','second','fp',5,'NO_PROMOTION'),('all','second','tp',8,'NO_PROMOTION'),('all','second','noisy_controls',2,'NO_PROMOTION'),('all','second','valid',18,'INCONCLUSIVE'),('all','all','n',19,'INCONCLUSIVE'),('all','seed','valid',19,'INCONCLUSIVE')]:
            with self.subTest(z=z,a=a,k=k):
                r=copy.deepcopy(s);r[z][a][k]=val;self.assertEqual(C.gate(r),want)

    def test_one_look_and_incomplete_replies_are_not_retried(self):
        class Fake:
            calls=[]
            def post(self,req):
                mask=req['response_format']['json_schema']['schema']['required'];system=req['messages'][0]['content'];self.calls.append(system)
                return dict(choices=[dict(finish_reason='length' if system==C.FOCUS else 'stop',message=dict(content=json.dumps(dict.fromkeys(mask,False))))],usage=dict(prompt_tokens=10,completion_tokens=9))
        fake=Fake()
        with tempfile.TemporaryDirectory() as t, contextlib.redirect_stdout(io.StringIO()):
            root=Path(t);C.P.write(root/'protocol.json',C.protocol());C.seal(root)
            with patch.object(C.S.T,'narrator',return_value=contextlib.nullcontext((fake,{}))), patch.object(C.S.T,'default_backend',return_value='cpu'):
                C.run(root);self.assertEqual(Counter(fake.calls),{C.FOCUS:20,C.F.SYSTEM+C.M.APPEND:20})
                r=C.F.read(root/'report.json');self.assertEqual(r['decision'],'INCONCLUSIVE');self.assertEqual(r['results']['all']['second']['valid'],0);self.assertEqual(r['results']['empty']['second']['exact'],0)
                with self.assertRaises(FileExistsError) as stopped:C.run(root)
                self.assertEqual(Path(stopped.exception.filename).name,'started.json')
                self.assertEqual(len(fake.calls),40)
            c=C.F.read(root/'cases.json');c[0]['question']+=' Changed';(root/'cases.json').write_bytes(C.P.enc(c))
            with self.assertRaisesRegex(ValueError,'after sealing'):C.run(root)
            (root/'protocol.json').write_text('{}');
            with self.assertRaisesRegex(ValueError,'Protocol drift'):C.run(root)
