import { describe,it,expect } from 'vitest'
import { interactiveStages,backgroundStages,isNewsRun,numericReview,stateFor,routeFacts,memoryDetail,shadowDetail } from './run-map'
import type { Run,Span } from './domain'
let sequence=0
const span=(partial:Partial<Span>):Span=>({id:(++sequence).toString(16).padStart(32,'0'),parent_id:null,name:'Fixture step',kind:'tool',actor:'Python',model:'',status:'complete',start_ms:0,duration_ms:10,inputs:{},outputs:{},...partial})
const run=(spans:Span[],status:Run['status']='complete'):Run=>({id:'a'.repeat(32),format:'cofinance-trace/1',title:'Map unit fixture',mode:'fixture',status,started_at:'2026-11-07T00:00:00Z',duration_ms:100,revision:1,spans})
describe('observed run map',()=>{
 it('never marks absent roles as executed',()=>{
  const r=run([span({kind:'route',outputs:{intent:'price_snapshot',tickers:['NVDA'],intent_confidence:.6,horizon:'intraday'}}),span({kind:'handoff',name:'Executive Route',outputs:{delegated_agents:['Data Analyst']}})])
  const nodes=interactiveStages(r)
  expect(nodes.find(n=>n.id==='news')?.state).toBe('not_dispatched')
  expect(nodes.find(n=>n.id==='news')?.spans).toHaveLength(0)
  expect(nodes.find(n=>n.id==='guard')?.state).toBe('not_observed')
 })
 it('does not count a role or deterministic fallback as model inference',()=>{
  const data=span({kind:'agent',actor:'Data Analyst',name:'Data Analyst',status:'fallback'})
  const nodes=interactiveStages(run([data]))
  expect(nodes.find(n=>n.id==='data')?.detail).toContain('0 narrator calls')
  expect(nodes.find(n=>n.id==='data')?.owner).toBe('No narrator inference observed')
 })
 it('keeps live state separate from interrupted stale running spans',()=>{
  const s=span({status:'running'})
  expect(stateFor([s],run([s],'running'))).toBe('running')
  expect(stateFor([s],run([s],'error'))).toBe('not_reached')
  expect(stateFor([],run([],'error'))).toBe('not_reached')
 })
 it('counts only actual model calls and preserves fallback state',()=>{
  const agent=span({kind:'agent',actor:'Data Analyst',status:'fallback'}),model=span({kind:'model',actor:'Data Analyst',model:'fixture/base',parent_id:agent.id}),gate=span({kind:'gate',actor:'Data Analyst',status:'fallback',parent_id:agent.id})
  const node=interactiveStages(run([agent,model,gate],'degraded')).find(n=>n.id==='data')!
  expect(node.owner).toBe('fixture/base');expect(node.detail).toContain('1 narrator call');expect(node.state).toBe('fallback')
 })
 it('shows diagnostic news as its own path, not a chat route',()=>{
  const r={...run([span({kind:'run',name:'Clock 1 diagnostic replay'}),span({kind:'gate',name:'Signal gate: DIGEST'})]),mode:'replay' as const}
  expect(isNewsRun(r)).toBe(true)
  expect(backgroundStages(r).find(n=>n.id==='storage')?.detail).toContain('isolated')
 })
 it('does not invent a router confidence',()=>{
  expect(routeFacts(span({outputs:{intent:'thesis_review',tickers:['BTC']}}))?.confidence).toBeUndefined()
 })
})
describe('guard evidence, not hidden thinking',()=>{
 it('reconstructs rejected numeric tokens from the exact sibling model output',()=>{
  const agent=span({kind:'agent',actor:'Data Analyst',outputs:{summary:'NVDA is 138.5.',allowed_numbers:[138.5]}})
  const model=span({kind:'model',actor:'Data Analyst',parent_id:agent.id,outputs:'NVDA is 999.'})
  const gate=span({kind:'gate',actor:'Data Analyst',parent_id:agent.id,outputs:{passed:false,allowed_numbers:[138.5]}})
  const result=numericReview(gate,[agent,model,gate])!
  expect(result.unmatched).toEqual([999]);expect(result.allowed).toEqual([138.5]);expect(result.accepted).toContain('138.5');expect(result.pass).toBe(false)
 })
 it('does not infer rejected output from another agent or run',()=>{
  const gate=span({kind:'gate',actor:'News Researcher',outputs:{passed:false,allowed_numbers:[]}})
  const other=span({kind:'model',actor:'Data Analyst',outputs:'Unrelated 999'})
  const result=numericReview(gate,[gate,other])!
  expect(result.output).toBe('');expect(result.unmatched).toEqual([])
 })
 it('derives old synthesis allowlists only from accepted specialist numbers',()=>{
  const agent=span({kind:'agent',outputs:{summary:'The supplied price is 10.',allowed_numbers:[10,20]}})
  const model=span({kind:'model',actor:'Market Team Lead',outputs:'The price is 20.'})
  const gate=span({kind:'gate',name:'Synthesis faithfulness',outputs:{faithful_synthesis:false}})
  expect(numericReview(gate,[agent,model,gate])?.allowed).toEqual([10])
  expect(numericReview(gate,[agent,model,gate])?.unmatched).toEqual([20])
 })
 it('reads scale words like the backend guard and prefers recorded rejections',()=>{
  const agent=span({kind:'agent',outputs:{summary:'Cap 4.51 trillion USD.',allowed_numbers:[4.51e12]}})
  const model=span({kind:'model',actor:'Market Team Lead',outputs:'Cap 4.51 trillion USD.'})
  const gate=span({kind:'gate',name:'Synthesis faithfulness',outputs:{faithful_synthesis:true,allowed_numbers:[4.51e12],rejected_numbers:[]}})
  expect(numericReview(gate,[agent,model,gate])?.unmatched).toEqual([])
 })
 it('shows the candidate that was used after a strict follow-up retry',()=>{
  const first=span({kind:'model',actor:'Market Team Lead',outputs:'First answer 10.'})
  const second=span({kind:'model',actor:'Market Team Lead',outputs:'Retry answer.'})
  const gate=span({kind:'gate',name:'Synthesis faithfulness',outputs:{faithful_synthesis:true,allowed_numbers:[10],rejected_numbers:[],
   follow_up_retry:{first_restated:4,retry_restated:5,retry_passed_guards:true,used:'first'}}})
  const result=numericReview(gate,[first,second,gate])!
  expect(result.output).toBe('First answer 10.')
  expect(result.retry).toEqual({used:'first',first:4,second:5,passed:true})
  expect(result.explanation).toContain('the first answer was used')
 })
 it('explains an invented risk and the strict retry',()=>{
  const model=span({kind:'model',actor:'Market Team Lead',outputs:'Main risk: supply-chain disruption.'})
  const gate=span({kind:'gate',name:'Synthesis faithfulness',outputs:{faithful_synthesis:false,allowed_numbers:[],rejected_numbers:[],
   unsupported_risks:['supply-chain disruption'],guard_retry:{reason:'unsupported_risk',rejected:['supply-chain disruption'],retry_passed_guards:false,used:'fallback'}}})
  const result=numericReview(gate,[model,gate])!
  expect(result.risks).toEqual(['supply-chain disruption'])
  expect(result.explanation).toContain('not in the Python risk list')
  expect(result.explanation).toContain('specialist reports were used')
 })
 it('explains a wrong-direction rejection',()=>{
  const model=span({kind:'model',actor:'Data Analyst',outputs:'186.64, down from 181.6.'})
  const gate=span({kind:'gate',actor:'Data Analyst',outputs:{passed:false,allowed_numbers:[186.64,181.6],rejected_numbers:[],direction_conflicts:['down']}})
  expect(numericReview(gate,[model,gate])?.explanation).toContain('wrong move since the previous close (down)')
 })
})
describe('conversation memory in the lead stage',()=>{
 it('reports what recall returned without claiming a model call',()=>{
  const recall=span({name:'Recall conversation memory',actor:'Conversation memory',outputs:{recalled:[{question:'Review NVDA',run_id:'r1',score:1}]}})
  const lead=interactiveStages(run([recall])).find(n=>n.id==='lead')
  expect(lead?.detail).toBe('No synthesis model call recorded / memory: 1 earlier turn recalled')
  expect(memoryDetail([span({name:'Recall conversation memory',actor:'Conversation memory',outputs:{recalled:[]}})])).toBe('memory: no earlier turn matched')
  expect(memoryDetail([span({name:'Recall conversation memory',actor:'Conversation memory',status:'error'})])).toBe('memory recall failed, not used')
  expect(memoryDetail([])).toBe('')
 })
 it('names the backend that found each turn and a visible keyword fallback',()=>{
  const hybrid=span({name:'Recall conversation memory',actor:'Conversation memory',outputs:{recalled:[{run_id:'r1',found_by:['keyword','semantic']},{run_id:'r2',found_by:['semantic']}]}})
  expect(memoryDetail([hybrid])).toBe('memory: 2 earlier turns recalled (found by both, semantic)')
  const failed=span({name:'Recall conversation memory',actor:'Conversation memory',status:'error'})
  const fallback=span({name:'Recall conversation memory (keyword fallback)',actor:'Conversation memory',outputs:{recalled:[{run_id:'r1'}]}})
  expect(memoryDetail([failed,fallback])).toBe('memory: keyword fallback, 1 earlier turn recalled')
  const withNote=span({name:'Recall conversation memory',actor:'Conversation memory',outputs:{recalled:[{run_id:'r1',kind:'turn',found_by:['keyword']},{run_id:null,kind:'note',found_by:['keyword']}]}})
  expect(memoryDetail([withNote])).toBe('memory: 1 earlier turn and 1 note recalled (found by keyword)')
  const differs=span({name:'Recall conversation memory',actor:'Conversation memory',outputs:{recalled:[{run_id:'r2',found_by:['semantic']}],shadow_keyword:{agrees:false,keyword_only:[]}}})
  expect(memoryDetail([differs])).toBe('memory: 1 earlier turn recalled (found by semantic), keyword alone would differ (comparison only)')
 })
})
describe('shadow rule urgency',()=>{
 it('counts would-be urgent headlines without implying an action',()=>{
  const g=(u:boolean)=>span({kind:'gate',name:'Signal gate: DIGEST',outputs:{action:'digest',shadow_rule_urgency:{would_be_urgent:u,rule:u?'results':null}}})
  expect(shadowDetail([g(true),g(false)])).toBe('2 gate checks / shadow rules: 1 would be urgent, no action changed')
  expect(shadowDetail([span({kind:'gate',name:'Signal gate: IGNORE',outputs:{action:'ignore'}})])).toBe('')
 })
})
