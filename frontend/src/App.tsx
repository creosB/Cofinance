// Cofinance: local finance-research dashboard. Linear-inspired, not a Linear clone.
// Sidebar = navigation plus one contextual list. The top bar carries the title. Chat owns the full
// viewport height with a single scroll region. Emerald is reserved for accepted outcomes.
// DESIGN_VARIANCE=4; MOTION_INTENSITY=2; VISUAL_DENSITY=6. Product UI, marketing rules N/A.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Activity, ArrowUpRight, Bot, ChevronRight, CircleCheck, CircleDashed, CircleDot, CircleX, Database, FileText, FlaskConical, GitBranch, Layers, RefreshCw, Search, ShieldCheck, Wrench, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Inspector, StateBadge } from '@/components/inspector'
import { useDashboard } from '@/lib/use-dashboard'
import { useChat } from '@/lib/use-chat'
import { ChatActions, ChatWorkspace, ConversationList } from '@/components/chat-workspace'
import { NewsPanel, NewsStatus } from '@/components/news-panel'
import { ModelLibrary } from '@/components/model-library'
import { LabelWorkbench } from '@/components/label-workbench'
import { RunMap, SystemGuide } from '@/components/run-map'
import { ago, date, duration, flatten, label, modelGroups, object, statusLabel, text, type Corpus, type Kind, type Run, type Span } from '@/lib/domain'

const views=[{id:'chat',name:'Chat',icon:Bot},{id:'research',name:'Research',icon:FileText},{id:'execution',name:'Execution',icon:Activity},{id:'models',name:'Models',icon:Layers},{id:'dataset',name:'Dataset',icon:Database}] as const
type View=typeof views[number]['id']
const icons:Record<Kind,LucideIcon>={run:Activity,route:GitBranch,agent:Bot,model:Layers,decision:GitBranch,tool:Wrench,gate:ShieldCheck,handoff:ArrowUpRight}
const filters=[['all','All steps'],['agent','Agents'],['model','Models'],['decision','Decisions'],['tool','Tools'],['gate','Gates']] as const
function RunGlyph({status}:{status:string}){
  const Icon=status==='complete'?CircleCheck:status==='error'?CircleX:status==='running'?CircleDashed:CircleDot
  return <Icon size={14} className={'run-glyph run-glyph-'+status} role="img" aria-label={statusLabel(status)}/>
}
// Live runs need no banner. Fixture, replay and incomplete traces always say so.
function Mode({run}:{run:Run}){
  const incomplete=!!(run.dropped_spans||run.storage_errors)
  if(run.mode==='live'&&!incomplete)return null
  return <div className="mode-notice" role="note"><FlaskConical size={16}/><p>{run.mode!=='live'&&<><strong>{run.mode==='fixture'?'Fixture run':'Diagnostic replay'}.</strong> {run.mode==='fixture'?'Model and tool outputs are test stubs, not live market data.':'Recorded evidence; actual model calls where identified. No notifications or watchlist writes.'}</>}{incomplete&&<span className="error-text"> Trace incomplete: {run.dropped_spans||0} omitted steps; {run.storage_errors||0} storage errors.</span>}</p></div>
}
function Empty({title,children}:{title:string;children:React.ReactNode}){return <div className="empty-state"><h2>{title}</h2><div className="muted">{children}</div></div>}
function Research({run,inspect}:{run:Run|null;inspect:(span?:Span)=>void}){
  if(!run)return <Empty title="No research runs yet"><p>Ask a question in Chat. Each run appears here with its findings, evidence and model activity.</p></Empty>
  const summary=object(run.summary),root=object(run.spans.find(s=>s.kind==='run')?.outputs)
  const answer=text(summary.answer||root.answer)
  const agents=run.spans.filter(s=>s.kind==='agent'),gates=run.spans.filter(s=>s.kind==='gate')
  const counts:Record<string,number>={}
  gates.forEach(s=>{const action=text(object(s.outputs).action);if(action)counts[action]=(counts[action]||0)+1})
  return <div className="research-layout"><div className="research-main"><div className="result-heading"><span className="section-label">Recorded result</span><h2>{answer?'Research synthesis':'News decision review'}</h2><Button variant="outline" onClick={()=>inspect()}>View execution<ChevronRight size={15}/></Button></div>
    {answer?<p className="synthesis">{answer}</p>:<><p className="synthesis">{gates.length?'The decision model scored source headlines. Inspect each gate to see how the policy handled them.':'This run has no recorded synthesis. Follow its execution to inspect what actually ran.'}</p>{Object.keys(counts).length>0&&<div className="outcome-strip">{Object.entries(counts).map(([name,n])=><span key={name}><strong>{n}</strong>{name.toUpperCase()}</span>)}</div>}</>}
    {agents.length>0&&<section className="research-section"><h3>Specialist reports</h3>{agents.map(s=><article key={s.id} className="specialist-report"><div><Bot size={16}/><h4>{s.actor}</h4><StateBadge value={s.status}/></div><p>{text(object(s.outputs).summary)||'No report text recorded.'}</p><Button variant="ghost" onClick={()=>inspect(s)}>Inspect agent<ArrowUpRight size={15}/></Button></article>)}</section>}
    {gates.length>0&&<section className="research-section"><div className="section-title"><h3>Recorded decisions</h3><span className="muted">{gates.length} gate checks</span></div><div className="evidence-list">{gates.map(s=>{const o=object(s.outputs);return <Button key={s.id} variant="ghost" className="evidence-row" onClick={()=>inspect(s)}><span className="evidence-ticker">{text(o.ticker)||s.actor}</span><span className="evidence-title">{text(o.title)||s.name}<small>{label(o.reason||s.status)}</small></span><Badge variant="outline" className="outcome">{text(o.action).toUpperCase()||statusLabel(s.status)}</Badge><ChevronRight size={15}/></Button>})}</div></section>}
  </div><aside className="run-context"><h3>Run context</h3><dl className="properties"><div><dt>Mode</dt><dd>{label(run.mode)}</dd></div><div><dt>State</dt><dd><StateBadge value={run.status}/></dd></div><div><dt>Started</dt><dd>{date(run.started_at)}</dd></div><div><dt>Duration</dt><dd className="mono">{duration(run.duration_ms)}</dd></div><div><dt>Risk mode</dt><dd>{label(summary.risk_mode)}</dd></div></dl></aside></div>
}
function Models({run}:{run:Run|null}){
  const groups=modelGroups(run?.spans||[])
  return !groups.length?<Empty title="No observed model calls"><p>Choose a recorded run with model calls. Downloaded models are not counted as executed.</p></Empty>:<><div className="section-title"><h2>Observed model calls</h2><span className="muted">{run?.title}</span></div><div className="table-scroll"><table className="models-table"><thead><tr><th>Model / backend</th><th>Roles</th><th>Calls</th><th>Wall time</th><th>Errors</th></tr></thead><tbody>{groups.map(g=><tr key={g.model}><td><Layers size={16}/><span className="mono">{g.model}</span></td><td>{[...g.actors].join(', ')}</td><td className="mono">{g.calls}</td><td className="mono">{duration(g.time)}</td><td>{g.errors}</td></tr>)}</tbody></table></div><p className="section-foot">Model identity is recorded, never inferred from a role. Timings include instrumentation overhead.</p></>
}
function Dataset({corpus,chat,inspect}:{corpus:Corpus|null;chat:ReturnType<typeof useChat>;inspect:(id:string)=>void}){
  return <><NewsPanel chat={chat} inspect={inspect}/><section className="dataset-section" aria-label="Qualification corpus"><div className="section-title"><h2>Qualification corpus</h2>{corpus&&<span className="muted">{corpus.rows} unlabelled candidates, {corpus.window_days}-day window</span>}</div>
    {!corpus?<p className="muted">No local corpus found. Collect source evidence with qualify.news_dataset before labelling.</p>:<><div className="mode-notice"><ShieldCheck size={16}/><p><strong>Not benchmark-ready.</strong> Human labels and sufficient class, trap, and source coverage are still required.</p></div><div className="table-scroll"><table><thead><tr><th>Evidence source</th><th>Rows</th><th>Timestamp meaning</th></tr></thead><tbody>{Object.entries(corpus.by_source).map(([name,n])=><tr key={name}><td>{label(name)}</td><td className="mono">{n}</td><td>{name==='apple_newsroom'?'Feed update, publication unknown':name==='hackernews'?'HN submission, not article publication':'Source publication'}</td></tr>)}</tbody></table></div><section className="research-section"><h3>Coverage limits</h3><p className="muted">Company announcements are issuer claims. HN is supplementary discussion. Regulatory coverage is not connected, and Bitcoin has HN only. Thresholds are not calibrated on this corpus.</p></section></>}
  </section>{corpus&&<LabelWorkbench chat={chat}/>}</>
}
export default function App(){
  const data=useDashboard(),chat=useChat(),[view,setView]=useState<View>('chat'),[step,setStep]=useState<string|null>(null),[query,setQuery]=useState(''),[filter,setFilter]=useState('all'),[finder,setFinder]=useState(false),[find,setFind]=useState('')
  const search=useRef<HTMLInputElement>(null),inspector=useRef<HTMLDivElement>(null)
  const [executionMode,setExecutionMode]=useState<'map'|'steps'>('map')
  const spans=data.run?.spans||[],active=spans.find(s=>s.id===step)||null
  const visible=useMemo(()=>flatten(spans).filter(s=>(filter==='all'||s.kind===filter||(filter==='decision'&&s.kind==='route'))&&JSON.stringify(s).toLowerCase().includes(query.toLowerCase())),[spans,filter,query])
  const choose=(id:string)=>{setStep(null);setQuery('');setFilter('all');data.select(id);setFinder(false);setView('research')}
  const inspectRun=(id:string,spanId?:string)=>{setStep(spanId||null);setQuery('');setFilter('all');data.refresh();data.select(id);setExecutionMode('map');setView('execution')}
  const inspect=(s?:Span)=>{setView('execution');setExecutionMode(s?'steps':'map');setQuery('');setFilter('all');setStep(s?.id||null)}
  const close=()=>{const old=step;setStep(null);requestAnimationFrame(()=>document.querySelector<HTMLButtonElement>('[data-step="'+old+'"]')?.focus({preventScroll:true}))}
  useEffect(()=>{
    const handler=(ev:KeyboardEvent)=>{
      if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='k'){ev.preventDefault();setFind('');setFinder(v=>!v);return}
      if(finder)return
      const target=ev.target as HTMLElement
      if(target.isContentEditable||target.closest('input,textarea,select,[role="combobox"]'))return
      if(ev.ctrlKey||ev.metaKey||ev.altKey)return
      if(ev.key==='/'){ev.preventDefault();setView('execution');setExecutionMode('steps');requestAnimationFrame(()=>search.current?.focus())}
      if(['1','2','3','4'].includes(ev.key))setView(views[Number(ev.key)].id)
      if(ev.key==='c')setView('chat')
      if(ev.key==='Escape')close()
      if(view==='execution'&&['j','k'].includes(ev.key)&&visible.length){ev.preventDefault();const index=visible.findIndex(s=>s.id===step),next=index<0?0:Math.max(0,Math.min(visible.length-1,index+(ev.key==='j'?1:-1)));setStep(visible[next].id);requestAnimationFrame(()=>document.querySelector('[data-step="'+visible[next].id+'"]')?.scrollIntoView({block:'nearest'}))}
    }
    window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler)
  },[finder,view,step,visible])
  const meta=views.find(v=>v.id===view)!,Icon=meta.icon
  const crumb=view==='chat'?chat.conversation?.title||'':(view==='research'||view==='execution')?data.run?.title||'':''
  const findRuns=data.runs.filter(r=>(r.title+' '+r.status+' '+r.mode).toLowerCase().includes(find.toLowerCase()))
  const tracked=chat.workspace?.watchlist.map(w=>w.ticker)||[]
  return <div className="app-shell"><aside className="sidebar" aria-label="Workspace navigation">
    <div className="brand"><span className="brand-mark">C</span><strong>Cofinance</strong></div>
    <Button variant="outline" className="find-button" onClick={()=>{setFind('');setFinder(true)}}><Search size={14}/>Find a run<kbd>Ctrl K</kbd></Button>
    <nav>{views.map((v,i)=><Tooltip key={v.id}><TooltipTrigger asChild><Button variant="ghost" className={'nav-item '+(view===v.id?'nav-active':'')} onClick={()=>setView(v.id)} aria-current={view===v.id?'page':undefined}><v.icon size={16}/><span>{v.name}</span></Button></TooltipTrigger><TooltipContent side="right">{v.name} <kbd>{i===0?'C':i}</kbd></TooltipContent></Tooltip>)}</nav>
    {view==='chat'?<ConversationList chat={chat}/>:<div className="side-section"><div className="side-heading"><span>Runs</span><span>{data.runs.length||''}</span></div><div className="side-list">{data.loading&&!data.runs.length?<><Skeleton className="run-skeleton"/><Skeleton className="run-skeleton"/></>:data.runs.length?data.runs.map(r=><Button key={r.id} variant="ghost" title={r.title+' ('+statusLabel(r.status)+', '+r.mode+')'} onClick={()=>choose(r.id)} className={'side-row run-entry '+(data.selected===r.id?'side-row-active':'')}><RunGlyph status={r.status}/><span className="side-row-title">{r.title}</span><span className="side-row-time">{r.mode==='live'?ago(r.started_at):r.mode}</span></Button>):<p className="sidebar-empty">No recorded runs yet.</p>}</div></div>}
    <div className="sidebar-bottom"><NewsStatus chat={chat} open={()=>setView('dataset')}/>{tracked.length>0&&<div className="side-meta"><span>Tracking</span><span className="mono">{tracked.join(', ')}</span></div>}{data.error&&<div className="side-meta side-status-error" role="status"><span className="connection-dot disconnected"/>Connection interrupted</div>}</div>
  </aside>
    <main className={'main-area'+(view==='chat'?' main-chat':'')}><header className="topbar"><div className="breadcrumb"><Icon size={15}/><strong>{meta.name}</strong>{crumb&&<><ChevronRight size={14}/><span className="crumb-title" title={crumb}>{crumb}</span></>}{(view==='research'||view==='execution')&&data.run&&<StateBadge value={data.run.status}/>}</div><div className="topbar-actions"><div className="mobile-nav"><Select value={view} onValueChange={v=>setView(v as View)}><SelectTrigger aria-label="Navigate workspace"><SelectValue/></SelectTrigger><SelectContent>{views.map(v=><SelectItem key={v.id} value={v.id}>{v.name}</SelectItem>)}</SelectContent></Select></div>{view==='chat'&&<ChatActions chat={chat}/>}<Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" aria-label="Refresh local data" onClick={()=>{data.refresh();chat.refresh()}}><RefreshCw size={15}/></Button></TooltipTrigger><TooltipContent>Refresh local data</TooltipContent></Tooltip><Button variant="outline" className="mobile-find" onClick={()=>{setFind('');setFinder(true)}}><Search size={15}/>Find run</Button></div></header>
      {view==='chat'?<ChatWorkspace chat={chat} inspect={inspectRun}/>:<div className="page-body">{data.error&&<div className="error-banner" role="alert"><span>{data.error}</span><Button variant="outline" onClick={data.refresh}>Retry</Button></div>}
      {data.run&&(view==='research'||view==='execution')&&<Mode run={data.run}/>}
      {data.loading&&!data.run&&(view==='research'||view==='execution')?<div className="loading-state" role="status" aria-label="Loading local trace"><Skeleton className="line-skeleton"/><Skeleton className="line-skeleton"/><Skeleton className="line-skeleton"/></div>:<>
      {view==='research'&&<Research run={data.run} inspect={inspect}/>}
      {view==='models'&&<><ModelLibrary chat={chat}/><SystemGuide/><Models run={data.run}/></>}
      {view==='dataset'&&<Dataset corpus={data.corpus} chat={chat} inspect={id=>inspectRun(id)}/>}
      {view==='execution'&&(data.run?<><div className="run-stats"><span><strong className="mono">{duration(data.run.duration_ms)}</strong> total</span><span><strong>{spans.filter(s=>s.kind==='model').length}</strong> model calls</span><span><strong>{spans.filter(s=>s.kind==='agent').length}</strong> agents</span><span><strong>{spans.filter(s=>s.kind==='gate').length}</strong> gate checks</span></div><Tabs value={executionMode} onValueChange={v=>setExecutionMode(v as 'map'|'steps')} className="execution-view-switch"><TabsList aria-label="Execution presentation"><TabsTrigger value="map">Run map</TabsTrigger><TabsTrigger value="steps">Step list</TabsTrigger></TabsList></Tabs><div className={executionMode==='steps'?'trace-toolbar':'trace-toolbar trace-toolbar-hidden'}><div className="search-field"><label htmlFor="trace-search" className="sr-only">Search this run</label><Search size={16}/><Input ref={search} id="trace-search" placeholder="Search agents, models, evidence..." value={query} onChange={ev=>setQuery(ev.target.value)}/>{query&&<Button variant="ghost" size="icon" aria-label="Clear search" onClick={()=>setQuery('')}><X size={14}/></Button>}</div><Tabs value={filter} onValueChange={setFilter}><TabsList aria-label="Filter execution steps">{filters.map(([id,name])=><TabsTrigger key={id} value={id}>{name}</TabsTrigger>)}</TabsList></Tabs></div><div className="execution-grid"><section className="timeline" aria-label="Execution timeline">{executionMode==='map'?<RunMap key={data.run.id} run={data.run} selected={step} onInspect={setStep}/>:<><div className="timeline-head"><span>Execution steps <small>{visible.length}</small></span><span>State</span><span>Duration</span></div>{visible.length?visible.map(s=>{const StepIcon=icons[s.kind];return <Button key={s.id} data-step={s.id} variant="ghost" className={'trace-row '+(step===s.id?'step-selected':'')} aria-pressed={step===s.id} onClick={()=>setStep(s.id)}><span className="step-identity" style={{'--depth':Math.min(s.depth,5)} as React.CSSProperties}><span className={'step-icon kind-'+s.kind}><StepIcon size={15}/></span><span className="step-copy"><strong>{s.name}</strong><small>{s.model||s.actor}{text(object(s.inputs).title)&&' / '+text(object(s.inputs).title)}</small></span></span><span className={'status-text status-'+s.status}>{statusLabel(s.status)}</span><span className="step-duration mono">{duration(s.duration_ms)}</span></Button>}):<Empty title="No matching steps"><p>Try a different term or show all steps.</p><Button variant="outline" onClick={()=>{setQuery('');setFilter('all')}}>Clear filters</Button></Empty>}</>}</section><div ref={inspector}><Inspector span={active} parent={spans.find(s=>s.id===active?.parent_id)} close={close} spans={spans}/></div></div></>:<Empty title="No execution traces"><p>Ask a question in Chat or collect news to record a run. Nothing here is simulated as live.</p></Empty>)}
      </>}</div>}
    </main><Dialog open={finder} onOpenChange={setFinder}><DialogContent className="run-finder"><DialogHeader><DialogTitle>Find a recorded run</DialogTitle><DialogDescription>Search local runs by title, mode, or state.</DialogDescription></DialogHeader><Input aria-label="Find a run" placeholder="Search recorded runs..." value={find} onChange={ev=>setFind(ev.target.value)} onKeyDown={ev=>{if(ev.key==='Enter'&&findRuns[0])choose(findRuns[0].id)}}/><div className="finder-results">{findRuns.length?findRuns.map(r=><Button key={r.id} variant="ghost" className="finder-row" onClick={()=>choose(r.id)}><RunGlyph status={r.status}/><span>{r.title}<small>{r.mode} / {statusLabel(r.status)}</small></span><ChevronRight size={15}/></Button>):<p className="empty-finder">No matching runs.</p>}</div></DialogContent></Dialog>
  </div>
}
