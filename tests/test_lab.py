import json
import random
import tempfile
import unittest
from pathlib import Path
from qualify.lab import DEV_END, TEST_START, build, folds, metrics, run, split_of
from qualify.historical import ASSETS, lines


def raw(seed):
    from datetime import datetime, timedelta, timezone
    rnd=random.Random(seed);day=datetime(2014,9,1,tzinfo=timezone.utc);ts=[];o=[];h=[];l=[];c=[];p=100.
    while day<datetime(2026,11,7,tzinfo=timezone.utc):
        if day.weekday()<5:
            p*=1+rnd.gauss(.0004,.015);ts.append(int((day+timedelta(hours=14,minutes=30)).timestamp()))
            o.append(p*.999);h.append(p*1.01);l.append(p*.99);c.append(p)
        day+=timedelta(days=1)
    return {'chart':{'result':[{'timestamp':ts,'indicators':{'quote':[{'open':o,'high':h,'low':l,'close':c}]}}]}}


class TestLab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        for i,s in enumerate((*ASSETS,'SPY')):(cls.root/(s+'.json')).write_text(json.dumps(raw(i)))
        build(cls.root);cls.rows=lines(cls.root/'cases.jsonl')
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_boundary_rows_are_embargoed(self):
        self.assertIsNone(split_of('2023-12-28','2024-01-04'))
        for r in self.rows:
            if r['split']=='dev':self.assertLess(r['exit_date'],DEV_END)
            if r['split']=='validation':self.assertTrue(r['entry_date']>=DEV_END and r['exit_date']<TEST_START)
            if r['split']=='test':self.assertGreaterEqual(r['entry_date'],TEST_START)
            self.assertLess(r['origin'],r['entry_date'])

    def test_models_never_train_on_overlapping_or_later_rows(self):
        for train,target in folds(self.rows,final=True):
            if train and target:self.assertLess(max(r['exit_date'] for r in train),min(r['entry_date'] for r in target))
        self.assertFalse(any(r['split']=='test' for train,_ in folds(self.rows) for r in train))
        self.assertFalse(any(r['split']=='test' for _,target in folds(self.rows) for r in target))

    def test_test_split_is_locked_unless_final_and_every_peek_is_logged(self):
        rep=run(self.root)
        self.assertNotIn('test',rep['splits']);self.assertFalse((self.root/'test_peeks.jsonl').exists())
        rep=run(self.root,final=True)
        self.assertIn('test',rep['splits']);self.assertEqual(rep['test_peeks'],1)
        self.assertEqual(run(self.root,final=True)['test_peeks'],2)

    def test_abstentions_stay_in_denominator(self):
        rows=[{'id':str(i),'origin':'2024-02-01','return':r} for i,r in enumerate([.1,-.1,.1,.1])]
        m=metrics(rows,{'0':'UP','1':'DOWN','2':'ABSTAIN','3':'ABSTAIN'})
        self.assertEqual(m['accuracy_all'],.5);self.assertEqual(m['accuracy_covered'],1);self.assertEqual(m['coverage'],.5)

    def test_changed_cases_are_refused(self):
        p=self.root/'cases.jsonl';old=p.read_bytes()
        try:
            p.write_bytes(old.replace(b'"split": "test"',b'"split": "dev"',1))
            with self.assertRaises(ValueError):run(self.root)
        finally:p.write_bytes(old)

if __name__=='__main__':unittest.main()
