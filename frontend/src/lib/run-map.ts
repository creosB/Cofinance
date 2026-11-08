import { object, text, numeric, duration, type Run, type Span } from './domain'

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
export function interactiveStages(run:Run):MapStage[]{
 const spans=run.spans,route=spans.filter(s=>s.kind==='route'),routeOutput=object(route.find(s=>text(object(s.outputs).intent))?.outputs)
 const routerCalls=spans.filter(s=>s.kind==='model'&&s.actor==='Routing executive')
 const handoffs=spans.filter(s=>s.kind==='handoff'),dispatch=handoffs.find(s=>s.name==='Executive Route')
 const delegated=object(dispatch?.outputs).delegated_agents
 const workers=(actor:string)=>spans.filter(s=>s.actor===actor&&s.kind!=='handoff')
 const workerDetail=(actor:string)=>{const nodes=workers(actor),models=nodes.filter(s=>s.kind==='model'),gates=nodes.filter(s=>s.kind==='gate');if(!nodes.length)return Array.isArray(delegated)&&!delegated.includes(actor)?'Python did not dispatch this role for this intent.':'No role activity recorded.';return models.length+' narrator call'+(models.length===1?'':'s')+' / '+nodes.filter(s=>s.kind==='tool').length+' tool calls'+(gates.some(s=>s.status==='fallback')?' / report replaced after validation':'')}
 const lead=spans.filter(s=>s.actor==='Market Team Lead'&&s.kind==='model')
 const memory=spans.filter(s=>s.actor==='Conversation memory')
 const finalGate=spans.filter(s=>s.name==='Synthesis faithfulness')
 const root=spans.filter(s=>s.kind==='run'),results=root.filter(s=>text(object(s.outputs).answer))
 const modelIds=[...new Set(spans.filter(s=>s.kind==='model'&&s.actor!=='Routing executive').map(s=>s.model).filter(Boolean))]
 const narrator=modelIds.length===1?(modelIds[0].includes('LiquidAI/LFM3')?'LFM3 1.7B / shared narrator':modelIds[0]):modelIds.length?'Multiple recorded backends':'No narrator inference observed'
 return [
  stage('request','Your question','User input','A question enters the local job runner. Follow-ups can reuse assets from this conversation only.',route.slice(0,1).length?route.slice(0,1):root,run,text(object(route[0]?.inputs).query)||run.title),
  stage('router','Decision model','GLiNER2.5-Decide','Classifies intent and horizon, then resolves entities. It does not generate the answer or call tools.',[...route,...routerCalls],run,text(routeOutput.intent)?text(routeOutput.intent).replaceAll('_',' ')+' / '+(Array.isArray(routeOutput.tickers)?routeOutput.tickers.join(', '):'No assets'):'Waiting for a recorded route'),
  stage('dispatch','Dispatch policy','Python coordinator','Python maps the classified intent to specialist roles. A model label is not permission to write user data.',handoffs,run,Array.isArray(delegated)?delegated.join(' + ')||'No specialist dispatched':'No dispatch recorded'),
  stage('data','Data Analyst',workers('Data Analyst').some(s=>s.kind==='model')?narrator:'No narrator inference observed','Python fetches market data and chart series first. The narrator receives evidence; a number check accepts it or replaces it.',workers('Data Analyst'),run,workerDetail('Data Analyst'),run.status==='running'?'not_observed':'not_dispatched'),
  stage('news','News Researcher',workers('News Researcher').some(s=>s.kind==='model')?narrator:'No narrator inference observed','Reads accepted local news, not a fresh web search. A missing store is a coverage gap, never proof of no catalysts.',workers('News Researcher'),run,workerDetail('News Researcher'),run.status==='running'?'not_observed':'not_dispatched'),
  stage('lead','Team Lead synthesis',lead[0]?.model||'Python fallback','The same selected narrator combines accepted reports in a separate role call. Conversation memory is passed as context only; its numbers never pass the guard. Python still owns every side effect.',[...lead,...memory],run,[lead.length?lead.length+' synthesis call'+(lead.length===1?'':'s'):'No synthesis model call recorded',memoryDetail(memory)].filter(Boolean).join(' / ')),
  stage('guard','Accept or replace','Python number guard','Checks generated numbers against evidence. A failed or incomplete narration uses the deterministic specialist output.',finalGate,run,finalGate.length?(object(finalGate[0].outputs).faithful_synthesis===true?'Synthesis passed the recorded number check':'Synthesis rejected; fallback used'):'No final validation recorded'),
  stage('result','Accepted result','Local conversation','Only the accepted result is saved to this conversation. Tool evidence and execution traces remain inspectable.',results,run,results.length?(run.status==='degraded'?'Saved with a visible fallback':'Recorded final answer'):run.status==='running'?'No final answer yet':'No accepted final answer recorded'),
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
 const guardRetry=object(out.guard_retry),why:Record<string,string>={unsupported_risk:'named a risk outside the Python risk list',unsupported_terms:'used words that are not in the reports',unsupported_numeric_tokens:'used numbers that are not in the reports',direction_conflict:'stated the wrong move since the previous close'}
 const riskLine=text(guardRetry.used)?' Feedback retry: the first draft '+(why[text(guardRetry.reason)]||'failed a guard')+' ('+(Array.isArray(guardRetry.rejected)?guardRetry.rejected.filter((t):t is string=>typeof t==='string').join('; '):'')+'); the retry was told exactly that and was '+(text(guardRetry.used)==='retry'?'accepted.':'also rejected, so the specialist reports were used.'):''
 const retryLine=riskLine+(retry?' Follow-up retry: the first answer repeated '+retry.first+' figures from the earlier answer, the stricter retry repeated '+(retry.second??'none')+(retry.passed?'':' and failed a guard')+'; the '+(retry.used==='retry'?'retry':'first answer')+' was used.':'')
 return {pass,allowed:[...new Set(allowed)],unmatched,terms,direction,retry,candidate,output,accepted,derived:synth&&!recorded,
  risks,explanation:(pass?'Every output number matched the evidence and every checked name and claim word appears in it. Risks are limited to the Python risk list. Paraphrase and tone are not certified.'+retryLine:risks.length?'Numbers and names matched, but a stated risk is not in the Python risk list built from the facts: '+risks.join('; ')+'.'+retryLine:direction.length?'Numbers matched, but the text states the wrong move since the previous close ('+direction.join(', ')+'). Python computed the direction; the deterministic fallback was used.':candidate?.status==='error'?'The narrator call failed or was cut off. Python used a fallback.':!candidate?'No narrator output was accepted. Evidence was absent or narration was disabled.':!output?'No complete narrator text was accepted.':terms.length?'Numbers matched, but the wording check found terms that are not in the evidence: '+terms.join(', ')+'. Python used the deterministic fallback.':unmatched.length?'Output included numeric tokens outside the recorded evidence allowlist. Dates, labels, and formatting can also trigger this conservative check.':'The recorded guard rejected the candidate. Inspect raw input and output for the exact evidence boundary.')}
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
export function elapsed(spans:Span[]){return duration(spans.reduce((n,s)=>n+(s.kind==='model'?(s.duration_ms||0):0),0))}
