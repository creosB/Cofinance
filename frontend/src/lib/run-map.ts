import { object, text, numeric, type Run, type Span } from './domain'

export type StageState='running'|'complete'|'fallback'|'error'|'not_observed'|'not_dispatched'|'not_reached'
export interface MapStage {id:string;title:string;owner:string;description:string;state:StageState;detail:string;spans:Span[];primary:Span|null}
export const stageLabel=(state:StageState)=>({running:'Running',complete:'Observed',fallback:'Fallback',error:'Failed',not_observed:'Not observed',not_dispatched:'Not dispatched',not_reached:'Not reached'})[state]
export function stateFor(spans:Span[],run:Run,absent:StageState='not_observed'):StageState{
 if(!spans.length)return run.status==='error'?'not_reached':absent
 if(spans.some(s=>s.status==='error'))return 'error'
 if(spans.some(s=>s.status==='fallback'))return 'fallback'
 if(spans.some(s=>s.status==='running'))return run.status==='running'?'running':'not_reached'
 return 'complete'
}
export function stage(id:string,title:string,owner:string,description:string,spans:Span[],run:Run,detail='',absent:StageState='not_observed'):MapStage{
 return {id,title,owner,description,state:stateFor(spans,run,absent),detail:detail||(!spans.length?'No matching observation in this trace.':spans.length+' recorded observation'+(spans.length===1?'':'s')),spans,primary:spans.find(s=>s.kind==='agent'||s.kind==='route'||s.kind==='gate')||spans[0]||null}
}
export function isNewsRun(run:Run){return run.spans.some(s=>s.name.startsWith('Clock 1')||s.name==='Judge headline'||s.name==='Signal gate: DIGEST'||s.name==='Signal gate: IGNORE')}
export interface ProvenanceConflict {
 kind:string;text:string;asset:string;cue:string;unit:string;clause:string;violations:string[];
 matches:{asset:string;field:string;shown:string;unit:string}[];
}
export function parseProvenanceConflicts(raw:unknown):ProvenanceConflict[]{
 if(!Array.isArray(raw))return []
 return raw.map(item=>{
  const c=object(item),claim=object(c.claim)
  const matches=Array.isArray(c.matches)?c.matches.map(m=>{const o=object(m);return {asset:text(o.asset),field:text(o.field),shown:text(o.shown),unit:text(o.unit)}}).filter(m=>m.asset||m.field||m.shown):[]
  const violations=Array.isArray(c.violations)?c.violations.filter((v):v is string=>typeof v==='string'):[]
  return {kind:text(c.kind)||'claim_provenance',text:text(c.text),asset:text(claim.asset),cue:text(claim.cue),unit:text(claim.unit),clause:text(claim.clause),violations,matches}
 }).filter(c=>c.text||c.violations.length||c.matches.length)
}
export function formatProvenanceConflict(c:ProvenanceConflict):string{
 const claimed=[c.asset,c.cue.replaceAll('_',' '),c.unit].filter(Boolean).join(' ')
 const detail=c.violations.length?'wrong '+c.violations.join('/'):c.kind.replaceAll('_',' ')
 return (c.text||'value')+(claimed?' as '+claimed:'')+' ('+detail+')'
}
export const CHAT_FLOWS=['open_conversation','clarification','single_pass_data_answer','python_market_answer','python_news_answer','multi_agent_research','document_research','watchlist_add','cancelled','error'] as const
export type ChatFlow=typeof CHAT_FLOWS[number]
function formatNarrator(modelIds:string[]){
 return modelIds.length===1?(modelIds[0].includes('LiquidAI/LFM3')?'LFM3 1.7B / shared narrator':modelIds[0].includes('AtomicChat/LFM3')?'LFM3 1.7B Uncensored (AtomicChat) / shared narrator':modelIds[0]):modelIds.length?'Multiple recorded backends':'No narrator inference observed'
}
export function detectFlow(run:Run):ChatFlow{
 const summary=object(run.summary),recorded=text(summary.flow)
 if((CHAT_FLOWS as readonly string[]).includes(recorded))return recorded as ChatFlow
 const spans=run.spans
 if(spans.some(s=>s.name==='Run cancelled'))return 'cancelled'
 if(run.status==='error'||spans.some(s=>s.name==='Run failed'))return 'error'
 if(spans.some(s=>s.actor==='Local assistant'||s.name==='Answer open conversation')||text(summary.risk_mode)==='open_conversation')return 'open_conversation'
 if(spans.some(s=>s.name==='Asset clarification required'))return 'clarification'
 const routeOut=object(spans.find(s=>s.kind==='route'&&text(object(s.outputs).intent))?.outputs)
 if(text(routeOut.intent)==='watchlist_add')return 'watchlist_add'
 if(spans.some(s=>s.actor==='Document Reader'))return 'document_research'
 const finalGate=spans.find(s=>s.name==='Synthesis faithfulness')
 const gateMethod=text(object(finalGate?.outputs).method)||text(object(finalGate?.outputs).validation_reason)
 if(gateMethod==='single_pass_data_answer')return 'single_pass_data_answer'
 if(gateMethod==='python_market_answer')return 'python_market_answer'
 if(['python_news_answer','python_digest','no_news'].includes(gateMethod)&&!spans.some(s=>s.actor==='Data Analyst'&&s.kind!=='handoff'))return 'python_news_answer'
 return 'multi_agent_research'
}
export function interactiveStages(run:Run):MapStage[]{
 const spans=run.spans,summary=object(run.summary),flow=detectFlow(run)
 const route=spans.filter(s=>s.kind==='route'),routeOutput=object(route.find(s=>text(object(s.outputs).intent))?.outputs)
 const routerCalls=spans.filter(s=>s.kind==='model'&&s.actor==='Routing executive')
 const intentText=text(routeOutput.intent).replaceAll('_',' ')
 const tickerList=Array.isArray(routeOutput.tickers)?routeOutput.tickers.map(t=>String(t).trim()).filter(Boolean):[]
 const routeDetail=intentText?(tickerList.length?intentText+' / '+tickerList.join(', '):intentText+' · no assets'):'Waiting for a recorded route'
 const root=spans.filter(s=>s.kind==='run')
 const requestSpans=route.slice(0,1).length?route.slice(0,1):root
 const requestStage=stage('request','Your question','User input','A question enters the local job runner. Follow-ups can reuse assets from this conversation only.',requestSpans,run,text(object(route[0]?.inputs).query)||run.title)
 const routerStage=stage('router','Decision model','GLiNER2.5-Decide','Classifies intent and horizon, then resolves entities. It does not generate the answer or call tools.',[...route,...routerCalls],run,routeDetail)
 const summaryAnswer=text(summary.answer),rootAnswers=root.filter(s=>text(object(s.outputs).answer))
 const answerAccepted=(run.status==='complete'||run.status==='degraded')&&(Boolean(summaryAnswer)||rootAnswers.length>0)
 const modelIds=[...new Set(spans.filter(s=>s.kind==='model'&&s.actor!=='Routing executive').map(s=>s.model).filter(Boolean))]
 const narrator=formatNarrator(modelIds)

 if(flow==='open_conversation'){
  const assistantSpans=spans.filter(s=>s.actor==='Local assistant'||s.name==='Answer open conversation')
  const assistantOwner=assistantSpans[0]?.model?formatNarrator([assistantSpans[0].model]):narrator
  const assistantStage=stage('assistant','Local assistant',assistantOwner,'Direct local conversation without market tools, news fetchers, or faithfulness guards.',assistantSpans,run,assistantSpans.length?'Direct local chat (no live tools)':'No local assistant call recorded')
  const resultStage=stage('result','Accepted result','Local conversation','Saved as general chat (no live tools) while keeping active conversation tickers intact.',assistantSpans,run,answerAccepted?'Recorded general chat reply (not tool-verified)':'No accepted final answer recorded')
  if(answerAccepted&&resultStage.state==='not_observed')resultStage.state='complete'
  if(!answerAccepted)resultStage.state=run.status==='running'?'running':'not_reached'
  return [requestStage,routerStage,assistantStage,resultStage]
 }

 if(flow==='clarification'){
  const clarifySpans=spans.filter(s=>s.name==='Asset clarification required')
  const reason=text(object(clarifySpans[0]?.outputs).reason)
  const lookupClarification=reason==='ambiguous_symbol'||reason==='unresolved_symbol'
  const measureClarification=reason==='ambiguous_valuation'||reason==='unsupported_valuation'
  const detail=reason==='unsupported_valuation'?'Requested measure is unavailable':measureClarification?'Requested measure needs clarification':lookupClarification?'Symbol lookup needs clarification':'Asset clarification required'
  const hint=measureClarification?'A share price cannot stand in for a different valuation measure.':'The request needs a confirmed asset before specialists can research it.'
  const dispatchStage=stage('dispatch','Dispatch policy','Python coordinator',hint,clarifySpans,run,detail)
  const resultStage=stage('result','Accepted result','Local conversation','Saved the clarification without narrating a research answer.',clarifySpans,run,answerAccepted?'Clarification recorded (no narrator call)':'No accepted final answer recorded')
  if(answerAccepted&&resultStage.state==='not_observed')resultStage.state='complete'
  if(!answerAccepted)resultStage.state=run.status==='running'?'running':'not_reached'
  return [requestStage,routerStage,dispatchStage,resultStage]
 }

 const handoffs=spans.filter(s=>s.kind==='handoff'),dispatch=handoffs.find(s=>s.name==='Executive Route')
 const delegated=object(dispatch?.outputs).delegated_agents
 const workers=(actor:string)=>spans.filter(s=>s.actor===actor&&s.kind!=='handoff')
 const workerDetail=(actor:string)=>{const nodes=workers(actor),models=nodes.filter(s=>s.kind==='model'),gates=nodes.filter(s=>s.kind==='gate');if(!nodes.length)return Array.isArray(delegated)&&!delegated.includes(actor)?'Python did not dispatch this role for this intent.':'No role activity recorded.';const noNews=actor==='News Researcher'&&gates.some(s=>text(object(s.outputs).validation_reason)==='no_news');return models.length+' narrator call'+(models.length===1?'':'s')+' / '+nodes.filter(s=>s.kind==='tool').length+' tool calls'+(noNews?' / no accepted local news':gates.some(s=>s.status==='fallback')?' / report replaced after validation':'')}
 const docSpans=workers('Document Reader'),showDocs=docSpans.length>0||(Array.isArray(delegated)&&delegated.includes('Document Reader'))
 const lead=spans.filter(s=>s.actor==='Market Team Lead'&&s.kind==='model')
 const memory=spans.filter(s=>s.actor==='Conversation memory')
 const finalGate=spans.filter(s=>s.name==='Synthesis faithfulness')
 const gateOut=object(finalGate[0]?.outputs),gateMethod=text(gateOut.method)||text(gateOut.validation_reason)
 const singlePass=lead.length===0&&gateMethod==='single_pass_data_answer'
 const pythonMarket=lead.length===0&&gateMethod==='python_market_answer'
 const pythonNews=lead.length===0&&['python_news_answer','python_digest','no_news'].includes(gateMethod)
 const leadOwner=lead[0]?.model||(singlePass?'Verified single-pass (Data Analyst)':pythonMarket?'Python market answer':pythonNews?'Python news synthesis':run.status==='error'?'Not reached':'Python fallback')
 const leadDetail=[lead.length?lead.length+' synthesis call'+(lead.length===1?'':'s'):singlePass?'Verified single-pass: reused Data Analyst report':pythonMarket?'Deterministic market answer, no narrator pass needed':pythonNews?'Deterministic news answer, no synthesis pass needed':run.status==='error'?'Run stopped before synthesis':'No synthesis model call recorded',memoryDetail(memory)].filter(Boolean).join(' / ')
 const leadStage=stage('lead','Team Lead synthesis',leadOwner,'The same selected narrator combines accepted reports in a separate role call, or Python reuses a single verified specialist report directly. Conversation memory is passed as context only; its numbers never pass the guard.',[...lead,...memory],run,leadDetail)
 if((singlePass||pythonMarket||pythonNews)&&leadStage.state==='not_observed')leadStage.state='complete'
 const workerStages:MapStage[]=[
  stage('data','Data Analyst',workers('Data Analyst').some(s=>s.kind==='model')?narrator:'No narrator inference observed','Python fetches market data and chart series first. The narrator receives evidence; a number check accepts it or replaces it.',workers('Data Analyst'),run,workerDetail('Data Analyst'),run.status==='running'?'not_observed':'not_dispatched'),
  stage('news','News Researcher',workers('News Researcher').some(s=>s.kind==='model')?narrator:'No narrator inference observed','Reads accepted local news, not a fresh web search. A missing store is a coverage gap, never proof of no catalysts.',workers('News Researcher'),run,workerDetail('News Researcher'),run.status==='running'?'not_observed':'not_dispatched'),
 ]
 if(showDocs)workerStages.push(stage('document','Document Reader',docSpans.some(s=>s.kind==='model')?narrator:'Python PDF reader','Reads page text from PDFs attached to this conversation. Quoted pages stay linked to their source file.',docSpans,run,workerDetail('Document Reader'),run.status==='running'?'not_observed':'not_dispatched'))
 const resultSpans=rootAnswers.length?rootAnswers:finalGate
 const resultStage=stage('result','Accepted result','Local conversation','Only the accepted result is saved to this conversation. Tool evidence and execution traces remain inspectable.',resultSpans,run,answerAccepted?(run.status==='degraded'?'Saved with a visible fallback':'Recorded final answer'):run.status==='running'?'No final answer yet':flow==='cancelled'?'Stopped before an answer was accepted':'No accepted final answer recorded')
 if(answerAccepted&&resultStage.state==='not_observed')resultStage.state='complete'
 if(!answerAccepted&&run.status!=='running')resultStage.state='not_reached'
 return [
  requestStage,
  routerStage,
  stage('dispatch','Dispatch policy','Python coordinator','Python maps the classified intent to specialist roles. A model label is not permission to write user data.',handoffs,run,Array.isArray(delegated)?delegated.join(' + ')||'No specialist dispatched':'No dispatch recorded'),
  ...workerStages,
  leadStage,
  stage('guard','Accept or replace','Python faithfulness guard','Checks numbers, claim provenance, direction, terms, and risks against evidence. A failed narration uses the deterministic specialist output.',finalGate,run,finalGate.length?(gateOut.faithful_synthesis===true?(singlePass?'Verified single-pass report accepted':pythonMarket?'Deterministic market answer accepted':pythonNews?'Deterministic news answer accepted':'Synthesis passed the recorded guard checks'):(pythonMarket?'Comparison incomplete; quote unavailable':'Synthesis rejected; fallback used')):'No final validation recorded'),
  resultStage,
 ]
}
/** Live Clock 1 collection that wrote the real store, identified by its recorded run input. */
/** 'memory: 1 earlier turn recalled' from the recorded recall span, or '' when no recall ran. */
export function memoryDetail(spans:Span[]){
 const span=spans.find(s=>s.name==='Recall conversation memory');if(!span)return ''
 const fallback=spans.find(s=>s.name==='Recall conversation memory (keyword fallback)'&&s.status!=='error')
 const used=span.status==='error'?fallback:span
 if(!used)return 'memory recall failed, not used'
 const recalled=object(used.outputs).recalled,list=Array.isArray(recalled)?recalled:[],n=list.length
 // Hybrid recall records which backend found each turn: keyword, semantic, or both.
 const via=[...new Set(list.map(r=>{const f=object(r).found_by;return Array.isArray(f)&&f.length?(f.length>1?'both':String(f[0])):''}).filter(Boolean))]
 const shadow=object(object(used.outputs).shadow_keyword)
 const differs=shadow.agrees===false?', keyword alone would differ (comparison only)':''
 // Notes are the user's own words, kept apart from earlier answers: context only, never evidence.
 const notes=list.filter(r=>object(r).kind==='note').length,turns=n-notes
 const what=[turns?turns+' earlier turn'+(turns===1?'':'s'):'',notes?notes+' note'+(notes===1?'':'s'):''].filter(Boolean).join(' and ')
 return (used===fallback?'memory: keyword fallback, ':'memory: ')+(n?what+' recalled':'no earlier turn matched')+(via.length?' (found by '+via.join(', ')+')':'')+differs
}
/** 'N gate checks / shadow rules: K would be urgent' from recorded gate outputs; '' when no shadow data exists. */
export function shadowDetail(gates:Span[]){
 const shadows=gates.map(g=>object(object(g.outputs).shadow_rule_urgency)).filter(o=>Object.keys(o).length)
 if(!shadows.length)return ''
 const urgent=shadows.filter(o=>o.would_be_urgent===true).length
 if(shadows.some(o=>o.live===true))return gates.length+' gate check'+(gates.length===1?'':'s')+' / urgency rules: '+urgent+' urgent'
 return gates.length+' gate check'+(gates.length===1?'':'s')+' / shadow rules: '+urgent+' would be urgent, no action changed'
}
export function operationalStorage(run:Run){return run.mode==='live'&&run.spans.some(n=>n.kind==='run'&&n.name==='Clock 1 collection'&&text(object(n.inputs).persistence)==='local news store')}
function storedDetail(run:Run){
 const out=object(run.spans.find(n=>n.kind==='run'&&n.name==='Clock 1 collection')?.outputs),actions=object(out.actions)
 const d=numeric(actions.digest)||0,a=numeric(actions.alert)||0
 return run.status==='running'?'Collection in progress':d+' digest / '+a+' alert rows stored'
}
export function backgroundStages(run:Run):MapStage[]{
 const s=run.spans
 return [
 stage('sources','Source batch','Collector','Reads recorded or fetched source items. Source timestamps keep their actual meaning.',s.filter(n=>n.name==='Read source batch'),run),
 stage('pregate','Quarantine & dedupe','Python pre-gate','Drops detected instruction-like text before judging. Title and URL rules prevent repeated judgments.',s.filter(n=>n.name==='Pre-gate quarantine'||text(object(n.outputs).reason).startsWith('duplicate')),run),
 stage('judge','Decision model','GLiNER2.5-Decide / observed backend','Scores materiality, urgency, and direction. Scores are not calibrated probabilities.',s.filter(n=>n.kind==='decision'||n.kind==='model'),run),
 stage('signals','Ignore, digest, alert','Python SignalGate','Applies recorded thresholds, age, cooldown, and cap rules. Model errors defer instead of alerting. Urgency comes from named rules (results, big market moves, production stops, trade blocks), because the model urgency head ranked backwards on labelled headlines.',s.filter(n=>n.name.startsWith('Signal gate:')),run,shadowDetail(s.filter(n=>n.name.startsWith('Signal gate:')))),
 operationalStorage(run)?stage('storage','Stored signals','Local news store','Digest and alert rows are written to the local news store with a seven-day expiry. Stored alerts are rows; no notification is sent.',s.filter(n=>n.kind==='run'),run,storedDetail(run)):
 stage('storage','Diagnostic result','No operational notifications','Diagnostic replay uses a fresh in-memory store. It is not an operational news collection or a sent notification.',s.filter(n=>n.kind==='run'),run,run.mode==='replay'?'Replay result, isolated from operational news storage':run.mode==='fixture'?'Fixture result, not live evidence':'Inspect persistence details in the run input'),
 ]
}
export function numericReview(span:Span,spans:Span[]){
 const out=object(span.outputs),pass=typeof out.passed==='boolean'?out.passed:typeof out.faithful_synthesis==='boolean'?out.faithful_synthesis:undefined
 if(pass===undefined)return null
 const parent=spans.find(s=>s.id===span.parent_id),report=object(parent?.outputs)
 const recorded=Array.isArray(out.allowed_numbers),explicit=recorded?out.allowed_numbers as unknown[]:[]
 let allowed=explicit.filter((n):n is number=>typeof n==='number'&&Number.isFinite(n))
 const synth=span.name==='Synthesis faithfulness'
 if(synth&&!recorded){
  const reports=spans.filter(s=>s.kind==='agent').map(s=>object(s.outputs))
  allowed=reports.flatMap(r=>{const nums=Array.isArray(r.allowed_numbers)?r.allowed_numbers.filter((n):n is number=>typeof n==='number'):[];return numbers(text(r.summary)).filter(n=>nums.some(a=>Math.abs(a-n)<=Math.max(1e-9,Math.abs(a)*1e-9)))})
 }
 const calls=spans.filter(s=>s.kind==='model'&&(synth?s.actor==='Market Team Lead':s.parent_id===span.parent_id&&s.actor===span.actor))
 // Strict follow-ups: a second Lead call is a retry. Show the candidate that was actually judged and used.
 const retryInfo=object(out.follow_up_retry),usedFirst=synth&&text(retryInfo.used)==='first'&&calls.length>1
 const candidate=usedFirst?calls.at(-2):calls.at(-1)
 const output=typeof candidate?.outputs==='string'?candidate.outputs:''
 const recordedRejects=Array.isArray(out.rejected_numbers)?out.rejected_numbers.filter((n):n is number=>typeof n==='number'):null
 const unmatched=recordedRejects??[...new Set(numbers(output).filter(n=>!allowed.some(a=>Math.abs(a-n)<=Math.max(1e-9,Math.abs(a)*1e-9))))]
 const accepted=synth?text(object(parent?.outputs).answer):text(report.summary)
 const terms=Array.isArray(out.unsupported_terms)?out.unsupported_terms.filter((t):t is string=>typeof t==='string'):[]
 const direction=Array.isArray(out.direction_conflicts)?out.direction_conflicts.filter((t):t is string=>typeof t==='string'):[]
 const retry=synth&&text(retryInfo.used)?{used:text(retryInfo.used),first:numeric(retryInfo.first_restated),second:numeric(retryInfo.retry_restated),passed:retryInfo.retry_passed_guards===true}:null
 const risks=Array.isArray(out.unsupported_risks)?out.unsupported_risks.filter((t):t is string=>typeof t==='string'):[]
 const provenance=parseProvenanceConflicts(out.provenance_conflicts)
 const guardRetry=object(out.guard_retry),why:Record<string,string>={unsupported_risk:'named a risk outside the Python risk list',unsupported_terms:'used words that are not in the reports',unsupported_numeric_tokens:'used numbers that are not in the reports',direction_conflict:'stated the wrong move since the previous close',claim_provenance:'bound a number to the wrong asset, metric, or unit'}
 const riskLine=text(guardRetry.used)?' Feedback retry: the first draft '+(why[text(guardRetry.reason)]||'failed a guard')+' ('+(Array.isArray(guardRetry.rejected)?guardRetry.rejected.filter((t):t is string=>typeof t==='string').join('; '):'')+'); the retry was told exactly that and was '+(text(guardRetry.used)==='retry'?'accepted.':'also rejected, so the specialist reports were used.'):''
 const retryLine=riskLine+(retry?' Follow-up retry: the first answer repeated '+retry.first+' figures from the earlier answer, the stricter retry repeated '+(retry.second??'none')+(retry.passed?'':' and failed a guard')+'; the '+(retry.used==='retry'?'retry':'first answer')+' was used.':'')
 return {pass,allowed:[...new Set(allowed)],unmatched,terms,direction,provenance,retry,candidate,output,accepted,derived:synth&&!recorded,
  risks,explanation:(pass?'Every output number matched the evidence and every checked asset, metric, unit, name, and claim word aligned with it. Risks are limited to the Python risk list. Paraphrase and tone are not certified.'+retryLine:provenance.length?'Numbers appeared in the evidence, but claim provenance found mismatched asset, metric, or unit bindings: '+provenance.map(formatProvenanceConflict).join('; ')+'.'+retryLine:risks.length?'Numbers and names matched, but a stated risk is not in the Python risk list built from the facts: '+risks.join('; ')+'.'+retryLine:direction.length?'Numbers matched, but the text states the wrong move since the previous close ('+direction.join(', ')+'). Python computed the direction; the deterministic fallback was used.':candidate?.status==='error'?'The narrator call failed or was cut off. Python used a fallback.':!candidate?'No narrator output was accepted. Evidence was absent or narration was disabled.':!output?'No complete narrator text was accepted.':terms.length?'Numbers matched, but the wording check found terms that are not in the evidence: '+terms.join(', ')+'. Python used the deterministic fallback.':unmatched.length?'Output included numeric tokens outside the recorded evidence allowlist. Dates, labels, and formatting can also trigger this conservative check.':'The recorded guard rejected the candidate. Inspect raw input and output for the exact evidence boundary.')}
}
const SCALE:Record<string,number>={thousand:1e3,million:1e6,billion:1e9,trillion:1e12}
/** Mirrors core/numeric_guard.py: "4.51 trillion" is 4.51e12, so the map never shows a scaled figure as unmatched. */
export function numbers(value:string):number[]{
 return [...value.matchAll(/(?<![\w.])-?\d[\d,]*\.?\d*%?(?:\s+(thousand|million|billion|trillion)\b)?/gi)].map(m=>{
  const base=Number(m[0].replace(/\s+(thousand|million|billion|trillion)$/i,'').replaceAll(',','').replace(/%$/,'').replace(/\.$/,''))
  return m[1]?base*SCALE[m[1].toLowerCase()]:base
 }).filter(Number.isFinite)
}
export function routeFacts(span:Span){const o=object(span.outputs);return text(o.intent)?{intent:text(o.intent).replaceAll('_',' '),confidence:numeric(o.intent_confidence),tickers:Array.isArray(o.tickers)?o.tickers.join(', '):'',horizon:text(o.horizon).replaceAll('_',' ')}:null}
