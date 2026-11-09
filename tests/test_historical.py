import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from qualify.historical import ASSETS, END, bars, digest, dump, evaluate, feature, lines, metrics, predict, prepare

class TestHistorical(unittest.TestCase):
    def test_future_and_invalid_bars_are_excluded(self):
        ts=lambda d:int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp())
        raw={'chart':{'result':[{'timestamp':[ts('2026-11-06'),ts('2026-11-07')],
            'indicators':{'quote':[dict(open=[10,999],high=[11,999],low=[9,999],close=[10,999])]}}]}}
        self.assertEqual(list(bars(raw)),['2026-11-06'])
    def test_past_features_do_not_need_future_values(self):
        past=[dict(open=i+1,high=i+2,low=i+.5,close=i+1,timestamp=i) for i in range(70)]
        a=feature(past[:61]);past[-1]['close']=999999
        self.assertEqual(a,feature(past[:61]))
        self.assertNotIn('price',a)
    def test_errors_and_abstentions_stay_in_accuracy_denominator(self):
        rows=[{'id':str(i),'origin':'2026-10-12','return':r,'momentum20':'UP'} for i,r in enumerate([.1,-.1,.1])]
        preds={'0':{'label':'UP'},'1':{'label':'ERROR'},'2':{'label':'ABSTAIN'}}
        m=metrics(rows,preds,'lfm3')
        self.assertAlmostEqual(m['directional_accuracy_all'],1/3)
        self.assertEqual(m['errors'],1);self.assertEqual(m['abstentions'],1)
        self.assertEqual(m['directional_accuracy_covered'],1)
    def test_next_open_fees_and_no_shorting(self):
        rows=[{'id':'a','origin':'2026-10-12','return':.1,'momentum20':'DOWN'}]
        m=metrics(rows,{'a':{'label':'UP'}},'lfm3')
        self.assertAlmostEqual(m['illustrative_long_cash_net_return'],.999*1.1*.999-1)
        self.assertEqual(metrics(rows,{'a':{'label':'DOWN'}},'lfm3')['illustrative_long_cash_net_return'],0)
    def test_predict_reads_features_only_and_rejects_reruns(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);f={'id':'case0001','features':{'past_return':1}}
            (p/'features.jsonl').write_text(json.dumps(f)+'\n')
            from qualify.historical import SYSTEM
            dump(p/'protocol.json',{'features_sha256':digest((p/'features.jsonl').read_bytes()),'system':SYSTEM})
            def call(system,prompt,**kw):
                self.assertNotIn('symbol',prompt);self.assertNotIn('date',prompt);return 'UP'
            predict(p,call,{'id':'test_only'})  # no outcomes file exists: prediction must not open it
            self.assertEqual(lines(p/'predictions.jsonl')[0]['label'],'UP')
            with self.assertRaises(ValueError):predict(p,call,{})
    def test_edited_features_are_rejected_before_model_call(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'features.jsonl').write_text('{}\n');dump(p/'protocol.json',{'features_sha256':'wrong'})
            with self.assertRaises(ValueError):predict(p,lambda *a,**k:self.fail('must not call'),{})
    def test_evaluation_rejects_missing_or_tampered_forecasts(self):
        from qualify.historical import SYSTEM,write_lines
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            write_lines(p/'features.jsonl',[{'id':'one','features':{}}])
            write_lines(p/'outcomes.jsonl',[{'id':'one','symbol':'AAPL','origin':'2026-10-12','entry_date':'2026-10-13','exit_date':'2026-10-19','return':.1,'momentum20':'UP'}])
            dump(p/'protocol.json',{'features_sha256':digest((p/'features.jsonl').read_bytes()),'outcomes_sha256':digest((p/'outcomes.jsonl').read_bytes()),'system':SYSTEM,'model_scope':'test','limitations':[]})
            predict(p,lambda *a,**k:'UP',{})
            self.assertEqual(evaluate(p)['windows_months']['1']['arms']['lfm3']['n'],1)
            (p/'predictions.jsonl').write_text('')
            with self.assertRaises(ValueError):evaluate(p)

    def test_bad_model_output_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);from qualify.historical import SYSTEM
            (p/'features.jsonl').write_text('{"id":"one","features":{}}\n')
            dump(p/'protocol.json',{'features_sha256':digest((p/'features.jsonl').read_bytes()),'system':SYSTEM})
            predict(p,lambda *a,**k:'UP, definitely',{})
            self.assertEqual(lines(p/'predictions.jsonl')[0]['label'],'ERROR')
            self.assertEqual(lines(p/'predictions.jsonl')[0]['error'],'invalid_label')

if __name__=='__main__':unittest.main()
