import { useEffect, useState } from 'react'
import { ArrowDown, ArrowRight, Bot, GitBranch, ShieldCheck, Layers, Database, Search, FileText, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { backgroundStages, interactiveStages, isNewsRun, stageLabel, elapsed, type MapStage } from '@/lib/run-map'
import { duration, parseRun, type Run } from '@/lib/domain'

const icons:Record<string,typeof Bot>={request:FileText,router:GitBranch,dispatch:GitBranch,data:Bot,news:Bot,lead:Layers,guard:ShieldCheck,result:FileText,sources:Search,pregate:ShieldCheck,judge:GitBranch,signals:ShieldCheck,storage:Database}
export function RunMap({run,selected=null,onInspect,compact=false}:{run:Run;selected?:string|null;onInspect:(id:string)=>void;compact?:boolean}){
 const background=isNewsRun(run),stages=background?backgroundStages(run):interactiveStages(run),[chosen,setChosen]=useState('')
 const active=stages.find(s=>s.id===chosen)||stages.find(s=>s.spans.some(n=>n.id===selected))||null
 const node=(s:MapStage)=>{const Icon=icons[s.id]||Layers;return <Button key={s.id} type="button" variant="ghost" className={'map-node map-'+s.state+(active?.id===s.id?' map-selected':'')} data-map-stage={s.id} aria-pressed={active?.id===s.id} onClick={()=>{setChosen(s.id);if(s.primary)onInspect(s.primary.id)}}><span className="map-node-top"><Icon size={16} aria-hidden="true"/><strong>{s.title}</strong><span className="map-state">{stageLabel(s.state)}</span></span><span className="map-owner">{s.owner}</span><span className="map-detail">{s.detail}</span></Button>}
 return <section className={'run-map '+(compact?'run-map-compact':'')} aria-label={background?'Background news run map':'Interactive research run map'}><div className="run-map-heading"><div><h2>{background?'Headline to signal':'Question to accepted answer'}</h2><p>{run.mode==='fixture'?'Fixture observations, not live activity.':run.mode==='replay'?'Replay observations, not a running news service.':run.status==='running'?'Updates from actual backend observations.':'Recorded activity from this run.'}</p></div><Badge variant="outline">{background?'Clock 1':'Clock 2'}</Badge></div>
  {background?<div className="background-map">{stages.map((s,i)=><div key={s.id}>{i>0&&<div className="map-connector" aria-hidden="true"><ArrowDown size={16}/></div>}{node(s)}</div>)}</div>:<><div className="map-top-row">{stages.slice(0,3).map(node)}</div><div className="map-connector"><ArrowDown size={16} aria-hidden="true"/><span>Python dispatches only the required roles</span></div><div className="map-worker-row">{stages.slice(3,5).map(node)}</div><div className="map-connector"><ArrowDown size={16} aria-hidden="true"/><span>Tool evidence → specialist report → number check</span></div><div className="map-bottom-row">{stages.slice(5).map(node)}</div></>}
  {active&&<div className="map-observations"><div className="section-title"><h3>{active.title}</h3><span className="muted">{active.spans.filter(s=>s.kind==='model').length} actual model calls</span></div>{!active.spans.length?<p className="muted">{active.detail} A diagram position does not mean this component ran.</p>:<><p className="muted">{active.description}</p><p className="muted">{active.spans.some(s=>s.kind==='model')?'Model wall time: '+elapsed(active.spans)+'. Select an observation for its input, output, and validation.':'Select an observation to inspect its recorded evidence.'}</p><div className="map-observation-list">{active.spans.map(s=><Button type="button" key={s.id} variant="ghost" aria-pressed={s.id===selected} onClick={()=>onInspect(s.id)}><span>{s.name}<small>{s.model||s.actor}</small></span><span className="mono">{duration(s.duration_ms)}</span><ChevronRight size={14}/></Button>)}</div></> }</div>}
  <p className="map-footnote">A role is not a separate model. The selected narrator can serve several roles in separate calls. Map branches show responsibility, not parallel execution.</p>
 </section>
}
export function TraceActivity({id,onInspect}:{id:string;onInspect:(runId:string,spanId?:string)=>void}){
 const [run,setRun]=useState<Run|null>(null),[error,setError]=useState('')
 useEffect(()=>{
  let disposed=false,timer:ReturnType<typeof setTimeout>|undefined
  const stop=new AbortController()
  async function poll(){
   const controller=new AbortController(),abort=()=>controller.abort();stop.signal.addEventListener('abort',abort,{once:true});const timeout=setTimeout(abort,8000)
   try{const response=await fetch('/api/run?id='+encodeURIComponent(id),{cache:'no-store',credentials:'omit',signal:controller.signal});if(!response.ok)throw Error('Trace is not available yet.');const next=parseRun(await response.json());if(!disposed){setRun(next);setError('');if(next.status==='running')timer=setTimeout(poll,1500)}}
   catch{if(!disposed){setError('Trace could not be loaded. The saved conversation is unchanged.');timer=setTimeout(poll,3000)}}
   finally{clearTimeout(timeout);stop.signal.removeEventListener('abort',abort)}
  }
  void poll();return()=>{disposed=true;stop.abort();clearTimeout(timer)}
 },[id])
 return run?<RunMap run={run} compact onInspect={spanId=>onInspect(id,spanId)}/>:<p role="status" className="muted">{error||'Loading observed execution...'}</p>
}
export function AnswerProcess({id,onInspect}:{id:string;onInspect:(runId:string,spanId?:string)=>void}){
 const [open,setOpen]=useState(false)
 return <details className="answer-process" onToggle={e=>setOpen(e.currentTarget.open)}><summary>How this answer was produced</summary>{open&&<TraceActivity id={id} onInspect={onInspect}/>}</details>
}
export function SystemGuide(){
 const [clock,setClock]=useState('interactive')
 return <section className="system-guide" aria-label="How Cofinance works"><div className="section-title"><h2>How the pieces fit</h2><Tabs value={clock} onValueChange={setClock}><TabsList aria-label="System architecture"><TabsTrigger value="interactive">Chat path</TabsTrigger><TabsTrigger value="news">News path</TabsTrigger></TabsList></Tabs></div>
  <div className="system-role-strip"><div><GitBranch size={18}/><strong>Decision model</strong><span>GLiNER classifies labels and entities. No generated answer, no tool authority.</span></div><div><ShieldCheck size={18}/><strong>Python policy</strong><span>Owns dispatch, fetching, thresholds, validation, storage, and explicit writes.</span></div><div><Layers size={18}/><strong>Base narrator</strong><span>LFM3 writes from supplied evidence. Each agent role has a separate call, not a separate downloaded model.</span></div></div>
  {clock==='interactive'?<div className="system-path"><span>Your question</span><ArrowRight size={15}/><span>GLiNER route</span><ArrowRight size={15}/><span>Python tools</span><ArrowRight size={15}/><span>Specialist roles</span><ArrowRight size={15}/><span>Narrator + guard</span><ArrowRight size={15}/><strong>Accepted answer</strong></div>:<><div className="system-path"><span>Source batch</span><ArrowRight size={15}/><span>Pre-gate</span><ArrowRight size={15}/><span>GLiNER scores</span><ArrowRight size={15}/><span>SignalGate</span><ArrowRight size={15}/><strong>News store</strong></div><p className="coverage-limit">Collector and diagnostic replays exist. An ongoing news service is not connected to chat yet. Optional adjudicator infrastructure exists, but no 2B adjudicator is configured or qualified for live chat.</p></>}
  <details><summary>Where Spidermonkey fits</summary><p><strong>Spidermonkey / Jev inspired the signal-gating pattern, not an active hosted dependency.</strong> Cofinance uses its local GLiNER adapter to score relevance and urgency, then Python applies policy. No Spidermonkey service runs behind this UI.</p><p>Inspect evidence, emitted labels, model scores, policy checks, and accepted or rejected outputs. The app does not expose private chain-of-thought or fabricate a model’s internal monologue.</p><p>Hy-Memory remains planned. Current conversation persistence is local SQLite, not Hy-Memory.</p></details>
 </section>
}
