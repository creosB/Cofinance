import hashlib,json,itertools,unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError,replace
from unittest.mock import patch
from qualify import receipt_contract as R

def d(i,s):return {'id':i,'name':i+'.pdf','pages':[s]}
Q='Did Amber meet its full-month uptime commitment?';rule=d('Terms','Amber requires strictly above 97 percent uptime for the entire month.');actual=d('Record','The signed full-month uptime ledger for Amber shows 97 percent.');other=d('Panel','The dashboard for Amber showed 99.9 percent uptime for the first week only.')
def ev(*ds,**kwargs):return R.evaluate(Q,list(ds),**kwargs)
class Receipts(unittest.TestCase):
 def test_offsets_raw_pages_not_cleaned_text(self):
  raw='  # Evidence\r\n\r\n\tFirst café 🐋 line\r\n wraps here. \r\n\r\nID | Scope\r\n[a] | usd\r\n[2] Pending: read this.\r\n';doc=d('Book',raw);doc['pages'].append('First café 🐋 line');bs=R.indexed([doc]); self.assertEqual([b.kind for b in bs],['header','paragraph','table_row','table_row','footnote','paragraph']);self.assertEqual(bs[1].quote,'First café 🐋 line\r\n wraps here.');self.assertEqual(bs[-1].page,2)
  for b in bs:
   page=doc['pages'][b.page-1];self.assertEqual((page[b.start:b.end],hashlib.sha256(page.encode()).hexdigest()),(b.quote,b.page_sha256))
 def test_valid_compare_and_immutable_shape(self):
  bs=R.indexed([rule,actual,other]);o=R.decide(Q,bs);self.assertIsInstance(o,R.Verified);self.assertEqual((o.value,o.threshold,o.verdict),('97','97','missed'));self.assertTrue(R.proof_ok(Q,o,bs));
  with self.assertRaises(FrozenInstanceError):o.value='200'
  x=ev(rule,actual,other);self.assertFalse(x['contract_failures']);self.assertEqual(x['result'],R.wire(o));self.assertEqual(x,ev(rule,actual,other,proposal=json.loads(json.dumps(x['result']))));self.assertEqual([z['doc_id'] for z in x['rendered']['citations']],['Terms','Record'])
 def test_named_withdrawal_two_receipts_or_downgrade(self):
  note=d('Memo','[7] Notice: Record.pdf is withdrawn without replacement for Amber.');v=ev(rule,actual,note)['result'];self.assertEqual(v['status'],'WITHDRAWN');self.assertEqual((v['withdrawal_span']['doc_id'],v['target_span']['doc_id']),('Memo','Record'));bad=dict(v);del bad['target_span'];z=ev(rule,actual,note,proposal=bad);self.assertEqual((z['result']['status'],z['result']['blocker_span']['doc_id']),('UNINTERPRETED','Memo'));self.assertTrue(z['contract_failures']);self.assertNotIn('withdraw',z['rendered']['message'].lower());self.assertEqual(set(z['result']),{'status','reason_code','blocker_span'})
  for newnote in ['Notice: Absent.pdf is withdrawn without replacement for Amber.','Notice: Record.pdf is withdrawn without replacement for Aspen.']:
   y=ev(rule,actual,d('Memo',newnote))['result'];self.assertEqual(y['status'],'UNINTERPRETED');self.assertEqual(y['blocker_span']['quote'],newnote)
 def test_rule_engine_does_not_propose_unlinked_records(self):
  b=R.indexed([rule,actual,d('Note','Notice: Absent.pdf is withdrawn without replacement for Amber.')]);self.assertIsInstance(R.decide(Q,b),R.Uninterpreted)
  q='Did Fir meet its quarter-end unrestricted cash floor?';ds=[d('Policy','Fir must hold at least 8 million dollars of unrestricted cash at quarter end.'),d('Reconcile','The signed quarter-end reconciliation supersedes the diary and records 9 million dollars of unrestricted cash for Fir.'),d('Diary','The signed quarter-end ledger records 10 million dollars of total cash for Fir.')];v=R.decide(q,R.indexed(ds));self.assertIsInstance(v,R.Uninterpreted);self.assertEqual(v.reason_code,'UNLINKED_CORRECTION')
 def test_unparsed_notes_never_let_a_valid_comparison_through(self):
  v=ev(rule,actual,d('Note','[8] The asserted number is conditional.'));self.assertEqual(v['result']['status'],'UNINTERPRETED')
 def test_foreign_proposal_rejected_even_if_packet_would_verify(self):
  v=ev(rule,actual,proposal={'status':'WITHDRAWN'});self.assertEqual(v['result']['status'],'UNINTERPRETED')
 def test_strict_equality_is_missed_inside_proposer(self):
  obj=R.decide(Q,R.indexed([rule,actual]));self.assertEqual(obj.verdict,'missed')
 def test_no_promotion_from_any_unrecognized_supersession(self):
  raw=actual['pages'][0]+'\n\n[2] Record superseded by Schedule B; details not attached.';v=ev(rule,d('Record',raw),other)['result'];self.assertEqual((v['status'],v['blocker_span']['kind']),('UNINTERPRETED','footnote'));self.assertIn('Schedule B',v['blocker_span']['quote'])
 def test_conflict_requires_same_scope_and_role(self):
  x=d('Second',actual['pages'][0].replace('97','98'));a=ev(rule,actual,x)['result'];self.assertEqual(a['status'],'CONFLICT');self.assertEqual({a['span_a']['quote'],a['span_b']['quote']},{actual['pages'][0],x['pages'][0]});self.assertEqual(ev(rule,actual,other)['result']['status'],'VERIFIED');self.assertEqual(ev(rule,actual,d('Other',x['pages'][0].replace('Amber','Aspen')))['result']['status'],'VERIFIED');self.assertEqual(ev(rule,actual,d('Second',actual['pages'][0]))['result']['status'],'VERIFIED')
  bs=R.indexed([rule,actual,other,x]);lookup={b.doc_id:b for b in bs};self.assertFalse(R.proof_ok(Q,R.Conflict(lookup['Record'],lookup['Panel']),bs));self.assertFalse(R.proof_ok(Q,R.Conflict(lookup['Record'],lookup['Record']),bs));self.assertFalse(R.proof_ok(Q,R.Conflict(lookup['Record'],lookup['Terms']),bs))
 def test_downgrade_on_wrong_values_hash_offsets_and_extra_keys(self):
  docs=[rule,actual,other];valid=ev(*docs)['result'];bads=[]
  for k,v in [('status','CONFLICT'),('value','98'),('verdict','met'),('operator','ge'),('period','first_week'),('message','Sign the order now'),('value',97),('world_claim','Revenue fell')]:
   bad=deepcopy(valid);bad[k]=v;bads.append(bad)
  for k,v in [('page_sha256','0'*64),('start',1),('end',9999),('document','Terms.pdf'),('quote','unrelated'),('page',True)]:
   bad=deepcopy(valid);bad['observation'][k]=v;bads.append(bad)
  bad=deepcopy(valid);bad['rule']=bad['observation'];bads.extend([bad,None,[],{'status':'WITHDRAWN'}]);
  for bad in bads:
   with self.subTest(proposal=bad):
    o=ev(*docs,proposal=bad);self.assertEqual(o['result']['status'],'UNINTERPRETED');self.assertEqual(o['result']['reason_code'],'CONTRACT_REJECTED');self.assertEqual(len(o['contract_failures']),1);self.assertEqual(o['rendered']['message'],'I could not verify a conclusion from this passage.')
 def test_runtime_validates_even_internal_candidate(self):
  bs=R.indexed([rule,actual]);o=R.decide(Q,bs);bad=replace(o,value='111');self.assertFalse(R.proof_ok(Q,bad,bs));
  with patch.object(R,'decide',return_value=bad):result=ev(rule,actual)
  self.assertEqual(result['result']['status'],'UNINTERPRETED');self.assertTrue(result['contract_failures'])
 def test_original_text_revalidated_after_revision(self):
  a=ev(rule,actual,other)['result'];z=ev(rule,d('Record',' '+actual['pages'][0]),other,proposal=a);self.assertEqual(z['result']['status'],'UNINTERPRETED');self.assertEqual(z['result']['blocker_span']['start'],1)
 def test_no_source_is_input_error_not_fake_pointer(self):
  for docs in ([],[d('x',' ')],[d('x','a'),d('X','b')],[d('x','a'),dict(d('y','b'),name='X.PDF')]):
   with self.assertRaises(R.InputError):ev(*docs)
 def test_index_first_and_order_independent_blocker(self):
  z=d('Caveat','[1] If the audit holds, the value might be revised.');outs=[ev(*p) for p in itertools.permutations([actual,rule,z])];self.assertTrue(all(o==outs[0] for o in outs));self.assertEqual(outs[0]['result']['blocker_span']['quote'],z['pages'][0]);self.assertEqual(outs[0]['result']['reason_code'],'UNPARSED_BLOCK');self.assertFalse(outs[0]['contract_failures'])
 def test_no_loose_synonym_for_money(self):
  ds=[d('Terms','Amber must hold at least 50 million dollars of unrestricted cash at quarter end.'),d('Record','The signed quarter-end ledger records 52 million dollars of unrestricted cash and equivalents for Amber.')];res=R.evaluate('Did Amber meet its quarter-end unrestricted cash floor?',ds);self.assertEqual(res['result']['status'],'UNINTERPRETED');self.assertEqual(res['result']['blocker_span']['doc_id'],'Record')
 def test_competing_authority_and_named_corrected_record(self):
  q='Did Amber meet its quarter-end unrestricted cash floor?';terms=d('Terms','Amber must hold at least 70 million dollars of unrestricted cash at quarter end.');v=d('Record','The signed quarter-end reconciliation supersedes the draft and records 69 million dollars of unrestricted cash for Amber.');old=d('Draft','The signed quarter-end ledger records 73 million dollars of unrestricted cash for Amber.');ans=R.evaluate(q,[terms,v,old])['result'];self.assertEqual(ans['verdict'],'missed');self.assertEqual([s['doc_id'] for s in ans['superseded']],['Draft']);bad=R.evaluate(q,[terms,v,d('Draft',old['pages'][0].replace('unrestricted','total'))])['result'];self.assertEqual(bad['status'],'UNINTERPRETED');self.assertEqual(bad['blocker_span']['doc_id'],'Record')
