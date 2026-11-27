import { useEffect, useRef, useState } from 'react'
import { Activity, ArrowUp, ArrowUpRight, Check, Download, Eraser, History, ListX, LoaderCircle, Newspaper, Plus, RotateCcw, Square, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import '../conversations.css'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { ModelPicker } from './model-library'
import { TraceActivity } from './run-map'
import { PriceChart } from './price-chart'
import { CrowdOdds } from './crowd-odds'
import { HeadlineSignal } from './headline-signal'
import { TokenUsage } from './token-usage'
import { AttachButton, AttachmentChips, DocumentSources } from './attachments'
import { DeepThinkNote, DeepThinkToggle, saveDeepThink, savedDeepThink, thinkOf } from './deep-think'
import { ago, evidenceUrl, statusLabel } from '@/lib/domain'
import { canForget, contextLine, itemFor } from '@/lib/memory'
import { refText, segments, targetId, type CiteRef } from '@/lib/citations'
import { MemoryPanel } from './memory-panel'
import { Input } from '@/components/ui/input'
import { ChevronDown, MoreHorizontal, Search } from 'lucide-react'
import type { ChatController, Message, Snapshot } from '@/lib/use-chat'

const activeStatuses=['queued','running','cancelling']
const num=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)?v:undefined
const price=(v:number)=>v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:v<1?6:4})
export const when=(iso:unknown)=>{const d=new Date(typeof iso==='string'?iso:'');return Number.isNaN(d.getTime())?'':d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}

function Facts({ticker,snap}:{ticker:string;snap:Snapshot}){
 const p=num(snap.price),cur=typeof snap.currency==='string'?snap.currency:''
 const name=typeof snap.name==='string'&&snap.name!==ticker?snap.name:''
 const receipt=snap.quote_status as unknown as {state?:string;failure?:string}|undefined
 const freshness:Record<string,string>={recent:'Recent quote, not live',delayed:'Delayed quote',last_session:'Last session',last_known:'Last known quote',stale:'Stale quote',timestamp_unknown:'Quote time unknown',timestamp_invalid:'Invalid quote time',unavailable:receipt?.failure==='provider_timeout'?'Quote timed out':'Quote unavailable'}
 const state=receipt?.state||(p===undefined?'unavailable':'')
 const exchange=typeof snap.exchange==='string'?snap.exchange:''
 const securityType=typeof snap.quote_type==='string'?snap.quote_type:''
 const unit=cur?' '+cur:''
 const rows:[string,string,string][]=[]
 const add=(field:string,label:string,format:(value:number)=>string)=>{const v=num(snap[field]);if(v!==undefined)rows.push([field,label,format(v)])}
 const big=(v:number)=>Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:2}).format(v)+unit
 add('previous_session_close','Previous close',v=>price(v)+unit+(typeof snap.previous_session_date==='string'?' on '+snap.previous_session_date:''))
 add('market_cap','Market cap',big)
 add('low_52_week','1Y low',v=>price(v)+unit);add('high_52_week','1Y high',v=>price(v)+unit)
 add('volume','Volume',v=>v.toLocaleString())
 add('pe_ratio','Trailing P/E',v=>v.toFixed(2));add('forward_pe_ratio','Forward P/E',v=>v.toFixed(2))
 add('revenue_ttm','Revenue, TTM',big);add('eps','EPS, TTM',v=>price(v)+unit)
 add('drawdown_from_52_week_high_pct','Below 1Y high',v=>v+'%')
 add('all_time_high','All-time high',v=>price(v)+unit)
 add('drawdown_from_all_time_high_pct','Below all-time high',v=>v+'%')
 add('annualized_daily_log_volatility_pct','Volatility, annualized',v=>v+'%')
 add('fear_greed_index','Fear & Greed',v=>v+(typeof snap.fear_greed_label==='string'?' '+snap.fear_greed_label:''))
 const asOf=snap.fundamentals_as_of&&typeof snap.fundamentals_as_of==='object'?snap.fundamentals_as_of as Record<string,{as_of?:string}>:{}
 const source=typeof snap.source==='string'?snap.source:''
 const quoted=[freshness[state],when(snap.price_as_of),source].filter(Boolean).join(' ? ')
 return <div className="facts"><div className="facts-asset"><strong>{ticker}</strong>{name&&<span>{name}</span>}{exchange&&<span className="facts-exchange">{exchange}</span>}</div>
  {p!==undefined&&<div className="facts-quote" data-fact-field="price" tabIndex={-1}><strong>{price(p)}{unit}</strong></div>}
  {quoted&&<p className="facts-source" data-quote-state={state||undefined}>{quoted}</p>}
  {(rows.length>0||securityType)&&<details className="fact-details"><summary>Market details</summary>
   {securityType&&<p className="facts-source">{securityType}</p>}
   {rows.length>0&&<dl className="facts-grid">{rows.map(([field,label,value])=><div data-fact-field={field} tabIndex={-1} key={field}><dt>{label}</dt><dd>{value}</dd>{asOf[field]?.as_of&&<small>{asOf[field].as_of}</small>}{field==='all_time_high'&&typeof snap.history_start==='string'&&<small>History since {snap.history_start}</small>}</div>)}</dl>}
   {snap.fundamentals_status&&['pe_ratio','forward_pe_ratio','market_cap','revenue_ttm','eps'].every(field=>num(snap[field])===undefined)&&<p className="facts-source">Fundamentals unavailable</p>}
  </details>}
 </div>
}
export const memoryLine=(m:Message['payload']['memory'])=>contextLine(m)
const kind=(m:Message)=>m.payload.kind==='conversation'?'General chat (no live tools)':m.payload.kind!=='research'||m.payload.decision_assessment?'':m.payload.mode==='facts'||m.payload.faithful_synthesis===false?'Source facts':''

function Cited({message}:{message:Message}){
 const p=message.payload
 const jump=(ref:CiteRef)=>{
  const root=document.getElementById(targetId(message.id,ref))
  if(!root)return
  const el=ref.kind==='fact'?Array.from(root.querySelectorAll<HTMLElement>('[data-fact-field]')).find(node=>node.dataset.factField===ref.field)||root:root
  for(let n=el.parentElement;n;n=n.parentElement)if(n instanceof HTMLDetailsElement)n.open=true
  el.focus({preventScroll:true});el.scrollIntoView({block:'nearest'})
 }
 return <>{segments(message.content,p.citations,p.evidence?.length||0).map((s,i)=>!s.refs?<span key={i}>{s.text}</span>:
  <span key={i} className="cited">{s.text}<Tooltip><TooltipTrigger asChild>
   <button type="button" className="cite-chip" aria-label={'Source '+s.n+': '+s.refs.map(refText).join('; ')} onClick={()=>jump(s.refs![0])}>{s.n}</button>
  </TooltipTrigger><TooltipContent>{s.refs.map((r,j)=><div key={j}>{refText(r)}</div>)}</TooltipContent></Tooltip></span>)}</>
}

function Charts({charts}:{charts:NonNullable<Message['payload']['charts']>}){
 const [open,setOpen]=useState(false)
 return <details className="answer-history" onToggle={e=>setOpen(e.currentTarget.open)}><summary>Price history{charts.length>1?' ('+charts.length+')':''}</summary>
  {open&&charts.map(chart=><PriceChart key={chart.ticker} data={chart}/>)}
 </details>
}

function Answer({message,inspect,chat}:{message:Message;inspect:(id:string,spanId?:string)=>void;chat:ChatController}){
 const p=message.payload
 if(message.role==='user')return <article className="msg msg-user" aria-label="Your question"><div className="bubble">{message.content}</div></article>
 const structured=!!p.decision_assessment||(p.kind==='research'&&!!p.reports&&p.faithful_synthesis!==true&&!!(p.reports.data||p.reports.news))
 // Answers saved before 2026-11-08 stored the raw key=value fallback. Show their snapshot as facts instead;
 // the original text stays in the export and the execution trace.
 const legacy=p.kind==='research'&&!p.reports&&!!p.snapshots&&/\bticker=\S+.*\bfetched_at=/s.test(message.content)
 const legacyNews=legacy?(message.content.match(/News Researcher: (.*)$/s)?.[1]||'').trim():''
 const snaps=Object.entries(p.snapshots||{}),news=chat.workspace?.news,label=legacy?'Source facts':kind(message)
 const mem=p.kind==='research'?itemFor(chat.memory,chat.conversation?.id,message.run_id):undefined
 return <article className="msg msg-answer" aria-label="Research response">
  <div className="msg-meta"><span>{new Date(message.created*1000).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}</span>{label&&<span className="answer-kind">{label}</span>}<DeepThinkNote use={p.deep_think}/></div>
  {p.decision_assessment&&<p className="msg-body" data-decision-state={p.decision_assessment.status}>{p.decision_assessment.text}</p>}
  {((!structured&&!legacy)||p.market_answer_note)&&<div className="msg-body"><Cited message={message}/></div>}
  {snaps.map(([t,s])=><div key={t} id={'facts-'+message.id+'-'+t} tabIndex={-1} className="cite-target"><Facts ticker={t} snap={s}/></div>)}
  {structured&&p.reports?.news&&<p className="msg-body news-summary">{p.reports.news}</p>}
  {legacy&&legacyNews&&<p className="msg-body news-summary">{legacyNews}</p>}
  {!!p.charts?.length&&<Charts charts={p.charts}/>}
  {p.headline_signal&&<HeadlineSignal signal={p.headline_signal}/>}
  {p.crowd_odds&&<CrowdOdds odds={p.crowd_odds}/>}
  {!!p.evidence?.length&&<details className="answer-sources"><summary>Sources ({p.evidence.length})</summary><div className="sources" aria-label="Sources">{p.evidence.map((s,i)=>{const href=evidenceUrl(s.url),meta=[s.source,s.as_of==='Unknown'?'time unknown':when(s.as_of)].filter(Boolean).join(', ');return href?<a key={href+i} id={'source-'+message.id+'-'+i} href={href} target="_blank" rel="noreferrer noopener"><span className="source-name">{s.title}</span><span className="source-meta">{meta}</span><ArrowUpRight size={14} aria-hidden="true"/></a>:<p key={i} id={'source-'+message.id+'-'+i} tabIndex={-1} className="cite-target"><span className="source-name">{s.title}</span><span className="source-meta">link unavailable</span></p>})}</div></details>}
  <DocumentSources sources={p.document_sources}/>
  {p.coverage_note&&<div className="coverage"><Newspaper size={14} aria-hidden="true"/><span>{p.coverage_note}</span>{news&&!news.health.collected&&news.phase!=='running'&&<Button variant="ghost" size="sm" disabled={chat.busy||!!chat.workspace?.active_job} onClick={()=>{void chat.collectNews().catch(()=>{})}}>Collect news</Button>}{news?.phase==='running'&&<span className="quiet">Collecting news now</span>}</div>}
  {message.role==='assistant'&&(p.kind==='research'||message.run_id)&&<div className="msg-actions">{message.run_id&&<Button variant="ghost" size="sm" onClick={()=>inspect(message.run_id!)}><Activity size={14}/>Execution</Button>}{p.kind==='research'&&mem&&(canForget(mem)?<Button variant="ghost" size="sm" disabled={chat.busy||!!chat.workspace?.active_job} onClick={()=>{void chat.forget(message.run_id!).catch(()=>{})}}><Eraser size={14}/>Forget from memory</Button>:<Button variant="ghost" size="sm" disabled><Check size={14}/>{mem.state==='forgotten'?'Forgotten from memory':'Blocked from recall'}</Button>)}{p.kind==='research'&&(p.tickers||[]).map(ticker=>{const tracked=chat.workspace?.watchlist.some(w=>w.ticker===ticker);return <Button key={ticker} variant="ghost" size="sm" disabled={chat.busy} title={tracked?'Remove '+ticker+' from watchlist':'Add '+ticker+' to watchlist'} onClick={()=>{void chat.post(tracked?'/api/watchlist/remove':'/api/watchlist',{ticker}).catch(()=>{})}}>{tracked?<Check size={14}/>:<Plus size={14}/>}{tracked?'Tracking '+ticker:'Track '+ticker}</Button>})}</div>}
  {(!!p.usage||memoryLine(p.memory))&&<details className="answer-details"><summary>Answer details</summary>
   {memoryLine(p.memory)&&<div className="coverage" title={(p.memory?.recalled||[]).map(r=>'Recalled: '+r.question).join('\n')||undefined}><History size={14} aria-hidden="true"/><span>{memoryLine(p.memory)}</span></div>}
   <TokenUsage usage={p.usage}/>
  </details>}
 </article>
}

/** Sidebar list. Delete (per row, inline confirm) and Clear all (dialog) are permanent: the server deletes memory copies,
 *  messages, runs and run traces, and refuses while a run or its memory write is in flight. */
export function ConversationList({chat,onSelect}:{chat:ChatController;onSelect?:()=>void}){
 const sessions=chat.workspace?.sessions||[],[search,setSearch]=useState('')
 const matches=sessions.filter(s=>s.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
 const [confirmId,setConfirmId]=useState(''),[clearOpen,setClearOpen]=useState(false),[working,setWorking]=useState('')
 const [notice,setNotice]=useState<{text:string;bad?:boolean}|null>(null)
 const runSession=chat.workspace?.active_job?.session_id,anyRun=!!chat.workspace?.active_job
 const fail=(e:unknown,fallback:string)=>setNotice({text:e instanceof Error?e.message:fallback,bad:true})
 const done=(r:unknown,text:string)=>{const v=(r||{}) as {memory_pending?:number;sweep_pending?:boolean|number}
  setNotice({text:text+(v.memory_pending||v.sweep_pending?' Leftover memory copies are being deleted in the background.':'')})}
 const remove=async(id:string)=>{
  setConfirmId('');setWorking(id)
  try{done(await chat.removeConversation(id),'Conversation deleted.')}catch(e){fail(e,'The conversation could not be deleted.')}finally{setWorking('')}
 }
 const clearAll=async()=>{
  setWorking('*')
  try{const r=await chat.clearConversations() as {deleted?:number;cleanup_retry?:boolean;error?:string};const n=r?.deleted??0
   if(r?.cleanup_retry)setNotice({text:r.error||'Local memory cleanup is incomplete. Retry Clear All.',bad:true})
   else done(r,'Deleted '+n+' conversation'+(n===1?'':'s')+'.')
   setClearOpen(false)}
  catch(e){fail(e,'The conversations could not be cleared.')}finally{setWorking('')}
 }
 return <div className="side-section conversation-history"><div className="history-search"><Search size={16} aria-hidden="true"/><Input aria-label="Search conversations" placeholder="Search conversations" value={search} onChange={e=>setSearch(e.target.value)}/></div><div className="side-heading"><span>Conversations</span></div>
  {notice&&<p className="side-notice" role={notice.bad?'alert':'status'} data-bad={notice.bad?'true':undefined}>{notice.text}</p>}
  <div className="side-list">{matches.map(s=>{
   const current=chat.selected===s.id,running=runSession===s.id
   if(confirmId===s.id)return <div key={s.id} className="conversation-row confirming" role="group" aria-label={'Delete '+s.title}
     onKeyDown={e=>{if(e.key==='Escape'){e.stopPropagation();setConfirmId('')}}}>
     <span className="row-confirm-text" title={s.title}>Delete for good?</span>
     <Button variant="destructive" size="sm" onClick={()=>void remove(s.id)}>Delete</Button>
     <Button variant="ghost" size="sm" autoFocus onClick={()=>setConfirmId('')}>Cancel</Button></div>
   return <div key={s.id} className={'conversation-row'+(current?' side-row-active':'')} data-working={working===s.id?'true':undefined}>
    <button type="button" title={s.title} aria-current={current?'true':undefined} className="side-row conversation-item" onClick={()=>{chat.select(s.id);onSelect?.()}}>
     <span className="side-row-title">{working===s.id?'Deleting':s.title}</span><span className="side-row-time">{ago(s.updated)}</span></button>
    <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" className="row-delete" aria-label={'Delete conversation: '+s.title}
     disabled={chat.busy||running||!!working} onClick={()=>{setNotice(null);setConfirmId(s.id)}}><Trash2 size={14}/></Button></TooltipTrigger>
     <TooltipContent side="right">{running?'A run is active in this conversation':'Delete conversation'}</TooltipContent></Tooltip>
   </div>})}
   {chat.workspace&&!sessions.length&&<p className="sidebar-empty">No conversations yet.</p>}{sessions.length>0&&!matches.length&&<p className="sidebar-empty">No matching conversations.</p>}</div>
  <details className="history-options"><summary>History options<ChevronDown size={14}/></summary><Button variant="ghost" disabled={chat.busy||anyRun||!!working} onClick={()=>{setNotice(null);setClearOpen(true)}}><ListX size={16}/>Clear all conversations</Button>{anyRun&&<p className="sidebar-empty">Wait for the active run to finish.</p>}</details>
  <Dialog open={clearOpen} onOpenChange={v=>{if(working!=='*')setClearOpen(v)}}>
   <DialogContent className="clear-dialog">
    <DialogHeader><DialogTitle>Clear all conversations?</DialogTitle>
     <DialogDescription>Deletes {sessions.length>=100?'every':'all '+sessions.length} conversation{sessions.length===1?'':'s'} for good: messages, run traces and conversation memory, notes included. Your watchlist, collected news and models are kept.</DialogDescription></DialogHeader>
    {notice?.bad&&<p className="side-notice" role="alert" data-bad="true">{notice.text}</p>}
    <DialogFooter><Button variant="ghost" disabled={working==='*'} onClick={()=>setClearOpen(false)}>Cancel</Button>
     <Button variant="destructive" disabled={working==='*'||anyRun} onClick={()=>void clearAll()}>{working==='*'?<><LoaderCircle size={14} className="spin"/>Deleting</>:'Delete all'}</Button></DialogFooter>
   </DialogContent>
  </Dialog>
 </div>
}

export function ChatActions({chat,onRefresh}:{chat:ChatController;onRefresh:()=>void}){
 const conversation=chat.conversation,menu=useRef<HTMLDetailsElement>(null)
 useEffect(()=>{const close=(e:PointerEvent)=>{if(!menu.current?.contains(e.target as Node))menu.current?.removeAttribute('open')};document.addEventListener('pointerdown',close);return()=>document.removeEventListener('pointerdown',close)},[])
 const exportChat=()=>{
  if(!conversation)return
  const blob=new Blob([JSON.stringify(conversation,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a')
  a.href=url;a.download='cofinance-conversation.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
 }
 return <details ref={menu} className="chat-options" onKeyDown={e=>{if(e.key==='Escape'){menu.current?.removeAttribute('open');menu.current?.querySelector('summary')?.focus()}}}><summary aria-label="Conversation options"><MoreHorizontal size={20}/></summary><div className="chat-options-panel" onClick={e=>{if((e.target as HTMLElement).closest('button'))menu.current?.removeAttribute('open')}}>
  {conversation&&<><MemoryPanel chat={chat} labeled/><Button variant="ghost" aria-label="Export conversation" onClick={()=>{exportChat();menu.current?.removeAttribute('open')}}><Download size={16}/>Export conversation</Button></>}
  <Button variant="ghost" onClick={()=>{onRefresh();menu.current?.removeAttribute('open')}}><RotateCcw size={16}/>Refresh local data</Button>
 </div></details>
}

export function ChatWorkspace({chat,inspect,onOpenModels}:{chat:ChatController;inspect:(id:string,spanId?:string)=>void;onOpenModels?:()=>void}){
 const draft=chat.questionDraft?.text||'',setDraft=chat.setQuestionDraft,scroller=useRef<HTMLDivElement>(null),textarea=useRef<HTMLTextAreaElement>(null)
 const [think,setThink]=useState(savedDeepThink)  // off unless the user turned it on; stays a per-answer request
 const conversation=chat.conversation,ownJob=conversation?.jobs.find(j=>activeStatuses.includes(j.status)),globalJob=chat.workspace?.active_job
 const model=chat.workspace?.models,newsRunning=chat.workspace?.news?.phase==='running',docs=conversation?.documents||[]
 const runningModel=!!model&&['downloading','verifying','qualifying','loading'].includes(model.phase)
 const blocked=!chat.workspace||chat.busy||!!globalJob||runningModel||newsRunning
 const lastJob=conversation?.jobs[0],failed=!ownJob&&lastJob&&['error','interrupted','cancelled'].includes(lastJob.status)?lastJob:null
 useEffect(()=>{const el=scroller.current;if(el)el.scrollTop=el.scrollHeight},[conversation?.id,conversation?.messages.length,ownJob?.id])
 useEffect(()=>{textarea.current?.focus()},[chat.draftRevision])
 const send=async(retry=false)=>{if((!draft.trim()&&!retry)||blocked)return
  try{await chat.send(draft.trim(),retry,think&&!!thinkOf(model)?.qualified&&model?.selected!=='facts')}catch{}}
 const notice=!chat.workspace?'Connecting. Draft kept.':runningModel?model?.stage||'Preparing model':newsRunning?'Collecting news':globalJob&&!ownJob?'Another chat is running':''
 return <div className="chat-view">
  <div className="chat-scroll" ref={scroller} role="log" aria-label="Conversation messages" aria-live="polite"><div className="chat-column">
   {chat.error&&<div role="alert" className="error-banner"><span>{chat.error}</span><Button variant="outline" onClick={chat.refresh}>Refresh</Button></div>}
   {conversation?.message_count&&conversation.message_count>100?<p className="quiet history-note">Showing the latest 100 messages.</p>:null}
   {conversation?.messages.length?conversation.messages.map(message=><Answer key={message.id} message={message} chat={chat} inspect={inspect}/>):<div className="chat-welcome"><h2>What should we research?</h2><div className="starter-questions">{['What is NVDA trading at?','Review the NVDA thesis','Compare Apple and Nvidia'].map(question=><Button key={question} variant="outline" size="sm" onClick={()=>{setDraft(question);textarea.current?.focus()}}>{question}</Button>)}</div></div>}
   {ownJob&&<div className="live-job" role="status"><div className="live-head"><LoaderCircle size={15} className="animate-spin" aria-hidden="true"/><strong>{ownJob.status==='cancelling'?'Stopping run':ownJob.stage}</strong><Button variant="ghost" size="sm" onClick={()=>inspect(ownJob.id)}>Execution<ArrowUpRight size={14}/></Button></div></div>}
   {failed&&<div className="job-error" role="alert"><strong>{statusLabel(failed.status)}</strong><span>{failed.error}</span><TraceActivity key={failed.id} id={failed.id}/><Button variant="ghost" size="sm" onClick={()=>inspect(failed.id)}>Execution</Button></div>}
  </div></div>
  <div className="chat-dock"><div className="chat-column">
   {notice&&<p className="dock-notice" role="status">{notice}</p>}
   <div className="composer"><AttachmentChips chat={chat} documents={docs} disabled={chat.busy||!!ownJob}/>
    <Textarea ref={textarea} id="research-question" aria-label="Research question" value={draft} onChange={e=>setDraft(e.target.value)} maxLength={3000} rows={1} placeholder={docs.length?'Ask about the PDF':'Ask a question'} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(!blocked)void send()}}}/>
    <div className="composer-bar"><AttachButton chat={chat} disabled={chat.busy||!!ownJob}/><ModelPicker chat={chat} compact/><DeepThinkToggle chat={chat} on={think} onChange={v=>{setThink(v);saveDeepThink(v)}} disabled={chat.busy||!!ownJob} onOpenModels={onOpenModels}/>
     {ownJob&&<Button variant="ghost" size="sm" disabled={chat.busy||ownJob.status==='cancelling'} onClick={()=>{void chat.post('/api/jobs/cancel',{id:ownJob.id}).catch(()=>{})}}><Square size={13}/>Stop run</Button>}
     {chat.hasRetry&&<Button variant="ghost" size="sm" disabled={blocked} onClick={()=>void send(true)}><RotateCcw size={13}/>Retry submission</Button>}
     <span className="composer-spacer"/>
     <Tooltip><TooltipTrigger asChild><Button size="icon" className="send-button" aria-label="Send question" disabled={blocked||!draft.trim()} onClick={()=>void send()}><ArrowUp size={16}/></Button></TooltipTrigger><TooltipContent>Send with Enter. Shift+Enter adds a line.</TooltipContent></Tooltip>
    </div>
   </div>
  </div></div>
 </div>
}
