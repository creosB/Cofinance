import { useEffect, useState } from 'react'
import { ArrowDown, ArrowRight, Bot, GitBranch, ShieldCheck, Layers, Database, Search, FileText, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { backgroundStages, detectFlow, interactiveStages, isNewsRun, stageLabel, type MapStage } from '@/lib/run-map'
import { duration, parseRun, type Run } from '@/lib/domain'
import { TokenUsage } from './token-usage'

const icons:Record<string,typeof Bot>={request:FileText,router:GitBranch,dispatch:GitBranch,assistant:Bot,data:Bot,news:Bot,document:FileText,lead:Layers,guard:ShieldCheck,result:FileText,sources:Search,pregate:ShieldCheck,judge:GitBranch,signals:ShieldCheck,storage:Database}
function shortOwner(owner:string){
 if(!owner||owner==='User input'||owner==='Local conversation')return ''
 if(owner.includes('GLiNER'))return 'GLiNER'
 if(owner.includes('LFM3'))return 'LFM3 1.7B'
 if(owner.startsWith('Verified single-pass'))return 'Single-pass'
 if(owner.startsWith('No narrator'))return 'Python'
 if(owner.startsWith('Python'))return 'Python'
 return owner
}
export function RunMap({run,selected=null,onInspect}:{run:Run;selected?:string|null;onInspect:(id:string)=>void}){
 const background=isNewsRun(run),flow=background?null:detectFlow(run),stages=background?backgroundStages(run):interactiveStages(run),[chosen,setChosen]=useState('')
 const active=stages.find(s=>s.id===chosen)||stages.find(s=>s.spans.some(n=>n.id===selected))||null
 const topIds=new Set(['request','router','dispatch']),workerIds=new Set(['data','news','document'])
 const topStages=stages.filter(s=>topIds.has(s.id)),workerStages=stages.filter(s=>workerIds.has(s.id)),bottomStages=stages.filter(s=>!topIds.has(s.id)&&!workerIds.has(s.id))
 const linear=background||workerStages.length===0
 const heading=background?'Headline to signal':flow==='open_conversation'?'General chat (no live tools)':flow==='clarification'?'Clarification requested':'What ran'
 const node=(s:MapStage)=>{const Icon=icons[s.id]||Layers,owner=shortOwner(s.owner);return <Button key={s.id} type="button" variant="ghost" className={'map-node map-'+s.state+(active?.id===s.id?' map-selected':'')} data-map-stage={s.id} aria-pressed={active?.id===s.id} onClick={()=>{setChosen(s.id);if(s.primary)onInspect(s.primary.id)}}><span className="map-node-top"><Icon size={16} aria-hidden="true"/><strong>{s.title}</strong><span className="map-state">{stageLabel(s.state)}</span></span>{owner&&<span className="map-owner">{owner}</span>}<span className="map-detail">{s.detail}</span></Button>}
 return <section className="run-map" aria-label={background?'Background news run map':'Interactive research run map'}><div className="run-map-heading"><div><h2>{heading}</h2></div><Badge variant="outline">{background?'Clock 1':'Clock 2'}</Badge></div>
  {linear?<div className="background-map">{stages.map((s,i)=><div key={s.id}>{i>0&&<div className="map-connector" aria-hidden="true"><ArrowDown size={16}/></div>}{node(s)}</div>)}</div>:<><div className="map-top-row">{topStages.map(node)}</div><div className="map-connector"><ArrowDown size={16} aria-hidden="true"/></div><div className="map-worker-row" data-worker-count={workerStages.length}>{workerStages.map(node)}</div><div className="map-connector"><ArrowDown size={16} aria-hidden="true"/></div><div className="map-bottom-row">{bottomStages.map(node)}</div></>}
  {active&&<div className="map-observations"><div className="section-title"><h3>{active.title}</h3></div>{!active.spans.length?<p className="muted">{active.detail}</p>:<><p className="muted">{active.description}</p><div className="map-observation-list">{active.spans.map(s=><Button type="button" key={s.id} variant="ghost" aria-pressed={s.id===selected} onClick={()=>onInspect(s.id)}><span>{s.name}<small>{s.model||s.actor}</small></span><span className="mono">{duration(s.duration_ms)}</span><ChevronRight size={14}/></Button>)}</div></> }</div>}
  <details className="map-footnote"><summary>Reading the map</summary><p>Roles can share a model. Branches show delegation, not timing.</p></details>
 </section>
}
export function TraceActivity({id}:{id:string}){
 const [run,setRun]=useState<Run|null>(null),[error,setError]=useState('')
 useEffect(()=>{
  let disposed=false,timer:ReturnType<typeof setTimeout>|undefined
  const stop=new AbortController()
  async function poll(){
   const controller=new AbortController(),abort=()=>controller.abort();stop.signal.addEventListener('abort',abort,{once:true});const timeout=setTimeout(abort,8000)
   try{const response=await fetch('/api/run?id='+encodeURIComponent(id),{cache:'no-store',credentials:'omit',signal:controller.signal});if(response.status===404){if(!disposed){setRun(null);setError('Trace is no longer available.')};return}if(!response.ok)throw Error('Trace is not available yet.');const next=parseRun(await response.json());if(!disposed){setRun(next);setError('');if(next.status==='running')timer=setTimeout(poll,1500)}}
   catch{if(!disposed){setError('Trace could not be loaded. The saved conversation is unchanged.');timer=setTimeout(poll,3000)}}
   finally{clearTimeout(timeout);stop.signal.removeEventListener('abort',abort)}
  }
  void poll();return()=>{disposed=true;stop.abort();clearTimeout(timer)}
 },[id])
 return run?<TokenUsage usage={run.usage} running={run.status==='running'}/>:<p role="status" className="muted">{error||'Loading trace...'}</p>
}
export function SystemGuide(){
 const [clock,setClock]=useState('interactive')
 return <section className="system-guide" aria-label="How Cofinance works"><div className="section-title"><h2>How the pieces fit</h2><Tabs value={clock} onValueChange={setClock}><TabsList aria-label="System architecture"><TabsTrigger value="interactive">Chat path</TabsTrigger><TabsTrigger value="news">News path</TabsTrigger></TabsList></Tabs></div>
  <div className="system-role-strip"><div><GitBranch size={18}/><strong>Decision model</strong><span>GLiNER classifies labels and entities. No generated answer, no tool authority.</span></div><div><ShieldCheck size={18}/><strong>Python policy</strong><span>Owns dispatch, fetching, thresholds, validation, storage, and explicit writes.</span></div><div><Layers size={18}/><strong>Narrator</strong><span>The selected LFM3 narrator writes the Data Analyst report and the Team Lead answer from supplied evidence, one separate call per role. The News Researcher is Python, no model call.</span></div></div>
  {clock==='interactive'?<div className="system-path"><span>Your question</span><ArrowRight size={15}/><span>GLiNER route</span><ArrowRight size={15}/><span>Python tools</span><ArrowRight size={15}/><span>Specialist roles</span><ArrowRight size={15}/><span>Narrator + guard</span><ArrowRight size={15}/><strong>Accepted answer</strong></div>:<><div className="system-path"><span>Source batch</span><ArrowRight size={15}/><span>Pre-gate</span><ArrowRight size={15}/><span>GLiNER scores</span><ArrowRight size={15}/><span>SignalGate</span><ArrowRight size={15}/><strong>News store</strong></div><p className="coverage-limit">Collection runs when you click Collect news; no schedule runs. Chat reads the collected store, and each new headline is judged once by the narrator for the experimental headline card. Optional adjudicator code exists, but no 2B adjudicator is configured or qualified.</p></>}
  <details><summary>Where Spidermonkey fits</summary><p><strong>Spidermonkey / Jev inspired the signal-gating pattern, not an active hosted dependency.</strong> Cofinance uses its local GLiNER adapter to score relevance and urgency, then Python applies policy. No Spidermonkey service runs behind this UI.</p><p>Inspect evidence, emitted labels, model scores, policy checks, and accepted or rejected outputs. The app does not expose private chain-of-thought or fabricate a model’s internal monologue.</p><p>Conversation memory uses local SQLite with optional hybrid keyword and semantic recall, falling back to keyword search when local embeddings are unavailable.</p></details>
 </section>
}
