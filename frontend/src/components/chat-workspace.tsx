// Chat follows a document-like Linear layout: one scroll region, a centred reading column,
// the composer docked at the bottom, and provenance in quiet secondary text.
import { useEffect, useRef, useState } from 'react'
import { Activity, ArrowUp, ArrowUpRight, Check, Download, Eraser, History, ListX, LoaderCircle, Newspaper, Plus, RotateCcw, Square, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import '../conversations.css'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { ModelPicker } from './model-library'
import { AnswerProcess, TraceActivity } from './run-map'
import { PriceChart } from './price-chart'
import { ago, evidenceUrl, statusLabel } from '@/lib/domain'
import { canForget, contextLine, itemFor } from '@/lib/memory'
import { MemoryPanel } from './memory-panel'
import type { ChatController, Message, Snapshot } from '@/lib/use-chat'

const activeStatuses=['queued','running','cancelling']
const num=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)?v:undefined
const price=(v:number)=>v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:v<1?6:4})
export const when=(iso:unknown)=>{const d=new Date(typeof iso==='string'?iso:'');return Number.isNaN(d.getTime())?'':d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}

/** Tool values exactly as recorded, formatted for reading. Never narrated, never estimated. */
function Facts({ticker,snap}:{ticker:string;snap:Snapshot}){
 const p=num(snap.price),cur=typeof snap.currency==='string'?snap.currency:''
 const name=typeof snap.name==='string'&&snap.name!==ticker?snap.name:''
 if(p===undefined)return <div className="facts"><div className="facts-asset"><strong>{ticker}</strong><span>Market data unavailable. No values were substituted.</span></div></div>
 const rows:[string,string][]=[['Last',price(p)+(cur?' '+cur:'')]]
 const prev=num(snap.previous_session_close);if(prev!==undefined)rows.push(['Previous close',price(prev)])
 const lo=num(snap.low_52_week),hi=num(snap.high_52_week);if(lo!==undefined&&hi!==undefined)rows.push(['1Y range',price(lo)+' to '+price(hi)])
 const vol=num(snap.volume);if(vol!==undefined)rows.push(['Volume',vol.toLocaleString()])
 const pe=num(snap.pe_ratio);if(pe!==undefined)rows.push(['Trailing P/E',pe.toFixed(2)])
 const fpe=num(snap.forward_pe_ratio);if(fpe!==undefined)rows.push(['Forward P/E',fpe.toFixed(2)])
 const big=(v:number)=>Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:2}).format(v)+(cur?' '+cur:'')
 const cap=num(snap.market_cap);if(cap!==undefined)rows.push(['Market cap',big(cap)])
 const rev=num(snap.revenue_ttm);if(rev!==undefined)rows.push(['Revenue, TTM',big(rev)])
 const eps=num(snap.eps);if(eps!==undefined)rows.push(['EPS, TTM',price(eps)])
 const dd=num(snap.drawdown_from_52_week_high_pct);if(dd!==undefined)rows.push(['Below 1Y high',dd+'%'])
 const ath=num(snap.all_time_high),add=num(snap.drawdown_from_all_time_high_pct)
 if(ath!==undefined)rows.push(['All-time high',price(ath)+(add!==undefined?', '+add+'% below':'')])
 const asOf=snap.fundamentals_as_of&&typeof snap.fundamentals_as_of==='object'?Object.values(snap.fundamentals_as_of as Record<string,{as_of?:string}>).map(v=>v?.as_of).filter((d):d is string=>typeof d==='string').sort():[]
 const fundamentalsLine=asOf.length?'fundamentals as of '+(asOf[0]===asOf[asOf.length-1]?asOf[0]:asOf[0]+' to '+asOf[asOf.length-1]):''
 const historyLine=ath!==undefined&&typeof snap.history_start==='string'?'all-time high from history since '+snap.history_start:''
 const noFundamentals=snap.fundamentals_status&&[pe,fpe,cap,rev,eps].every(v=>v===undefined)
 const vola=num(snap.annualized_daily_log_volatility_pct);if(vola!==undefined)rows.push(['Volatility, annualized',vola+'%'])
 const fg=num(snap.fear_greed_index);if(fg!==undefined)rows.push(['Fear & Greed',fg+(typeof snap.fear_greed_label==='string'?' '+snap.fear_greed_label:'')])
 const source=[typeof snap.source==='string'?snap.source:'',when(snap.price_as_of)?'price as of '+when(snap.price_as_of):'',fundamentalsLine,historyLine,noFundamentals?'fundamentals not available':''].filter(Boolean).join(', ')
 return <div className="facts"><div className="facts-asset"><strong>{ticker}</strong>{name&&<span>{name}</span>}</div><dl className="facts-grid">{rows.map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>{source&&<p className="facts-source">{source}</p>}</div>
}
/** Says what conversation context the narrator saw. Context only: recalled numbers never pass the guard. */
export const memoryLine=(m:Message['payload']['memory'])=>contextLine(m)
const kind=(m:Message)=>m.payload.kind!=='research'?'':m.payload.mode==='facts'?'Tool-only':m.payload.faithful_synthesis===false?'Narrator output replaced':'Narrated locally'

function Answer({message,inspect,chat}:{message:Message;inspect:(id:string,spanId?:string)=>void;chat:ChatController}){
 const p=message.payload
 if(message.role==='user')return <article className="msg msg-user" aria-label="Your question"><div className="bubble">{message.content}</div></article>
 // When the synthesis was not narrated, show the accepted structure instead of the same facts twice.
 const structured=p.kind==='research'&&!!p.reports&&p.faithful_synthesis!==true&&!!(p.reports.data||p.reports.news)
 // Answers saved before 2026-11-08 stored the raw key=value fallback. Show their snapshot as facts instead;
 // the original text stays in the export and the execution trace.
 const legacy=p.kind==='research'&&!p.reports&&!!p.snapshots&&/\bticker=\S+.*\bfetched_at=/s.test(message.content)
 const legacyNews=legacy?(message.content.match(/News Researcher: (.*)$/s)?.[1]||'').trim():''
 const snaps=Object.entries(p.snapshots||{}),news=chat.workspace?.news,label=legacy?'Tool-only, saved before the fix':kind(message)
 const mem=p.kind==='research'?itemFor(chat.memory,chat.conversation?.id,message.run_id):undefined
 return <article className="msg msg-answer" aria-label="Research response">
  <div className="msg-meta"><span>{new Date(message.created*1000).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}</span>{label&&<span className="answer-kind">{label}</span>}</div>
  {!structured&&!legacy&&<div className="msg-body">{message.content}</div>}
  {snaps.map(([t,s])=><Facts key={t} ticker={t} snap={s}/>)}
  {structured&&p.reports?.news&&<p className="msg-body news-summary">{p.reports.news}</p>}
  {legacy&&legacyNews&&<p className="msg-body news-summary">{legacyNews}</p>}
  {(p.charts||[]).map(chart=><PriceChart key={chart.ticker} data={chart}/>)}
  {!!p.evidence?.length&&<div className="sources" aria-label="Sources">{p.evidence.map((s,i)=>{const href=evidenceUrl(s.url),meta=[s.source,s.as_of==='Unknown'?'time unknown':when(s.as_of)].filter(Boolean).join(', ');return href?<a key={href+i} href={href} target="_blank" rel="noreferrer noopener"><span className="source-name">{s.title}</span><span className="source-meta">{meta}</span><ArrowUpRight size={14} aria-hidden="true"/></a>:<p key={i}><span className="source-name">{s.title}</span><span className="source-meta">link unavailable</span></p>})}</div>}
  {p.coverage_note&&<div className="coverage"><Newspaper size={14} aria-hidden="true"/><span>{p.coverage_note}</span>{news&&!news.health.collected&&news.phase!=='running'&&<Button variant="ghost" size="sm" disabled={chat.busy||!!chat.workspace?.active_job} onClick={()=>{void chat.collectNews().catch(()=>{})}}>Collect news</Button>}{news?.phase==='running'&&<span className="quiet">Collecting news now</span>}</div>}
  {memoryLine(p.memory)&&<div className="coverage" title={(p.memory?.recalled||[]).map(r=>'Recalled: '+r.question).join('\n')||undefined}><History size={14} aria-hidden="true"/><span>{memoryLine(p.memory)}</span></div>}
  {message.role==='assistant'&&p.kind==='research'&&<div className="msg-actions">{message.run_id&&<Button variant="ghost" size="sm" onClick={()=>inspect(message.run_id!)}><Activity size={14}/>Execution</Button>}{mem&&(canForget(mem)?<Button variant="ghost" size="sm" disabled={chat.busy||!!chat.workspace?.active_job} onClick={()=>{void chat.forget(message.run_id!).catch(()=>{})}}><Eraser size={14}/>Forget from memory</Button>:<Button variant="ghost" size="sm" disabled><Check size={14}/>{mem.state==='forgotten'?'Forgotten from memory':'Blocked from recall'}</Button>)}{(p.tickers||[]).map(ticker=>{const tracked=chat.workspace?.watchlist.some(w=>w.ticker===ticker);return <Button key={ticker} variant="ghost" size="sm" disabled={chat.busy||tracked} onClick={()=>{void chat.post('/api/watchlist',{ticker}).catch(()=>{})}}>{tracked?<Check size={14}/>:<Plus size={14}/>}{tracked?'Tracking '+ticker:'Track '+ticker}</Button>})}</div>}
  {message.run_id&&<AnswerProcess id={message.run_id} onInspect={inspect}/>}
 </article>
}

/** Sidebar list. Delete (per row, inline confirm) and Clear all (dialog) are permanent: the server deletes memory copies,
 *  messages, runs and run traces, and refuses while a run or its memory write is in flight. */
export function ConversationList({chat}:{chat:ChatController}){
 const sessions=chat.workspace?.sessions||[]
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
  try{const r=await chat.clearConversations() as {deleted?:number};const n=r?.deleted??0;done(r,'Deleted '+n+' conversation'+(n===1?'':'s')+'.');setClearOpen(false)}
  catch(e){fail(e,'The conversations could not be cleared.')}finally{setWorking('')}
 }
 return <div className="side-section"><div className="side-heading"><span>Conversations</span><span className="side-heading-actions">
   <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" aria-label="Clear all conversations" disabled={chat.busy||anyRun||!sessions.length||!!working} onClick={()=>{setNotice(null);setClearOpen(true)}}><ListX size={15}/></Button></TooltipTrigger><TooltipContent side="right">{anyRun?'Wait for the active run to finish':'Clear all conversations'}</TooltipContent></Tooltip>
   <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" aria-label="New chat" disabled={chat.busy} onClick={()=>{void chat.create().catch(()=>{})}}><Plus size={15}/></Button></TooltipTrigger><TooltipContent side="right">New chat</TooltipContent></Tooltip>
  </span></div>
  {notice&&<p className="side-notice" role={notice.bad?'alert':'status'} data-bad={notice.bad?'true':undefined}>{notice.text}</p>}
  <div className="side-list">{sessions.map(s=>{
   const current=chat.selected===s.id,running=runSession===s.id
   if(confirmId===s.id)return <div key={s.id} className="conversation-row confirming" role="group" aria-label={'Delete '+s.title}
     onKeyDown={e=>{if(e.key==='Escape'){e.stopPropagation();setConfirmId('')}}}>
     <span className="row-confirm-text" title={s.title}>Delete for good?</span>
     <Button variant="destructive" size="sm" onClick={()=>void remove(s.id)}>Delete</Button>
     <Button variant="ghost" size="sm" autoFocus onClick={()=>setConfirmId('')}>Cancel</Button></div>
   return <div key={s.id} className={'conversation-row'+(current?' side-row-active':'')} data-working={working===s.id?'true':undefined}>
    <button type="button" title={s.title} aria-current={current?'true':undefined} className="side-row conversation-item" onClick={()=>chat.select(s.id)}>
     <span className="side-row-title">{working===s.id?'Deleting':s.title}</span><span className="side-row-time">{ago(s.updated)}</span></button>
    <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" className="row-delete" aria-label={'Delete conversation: '+s.title}
     disabled={chat.busy||running||!!working} onClick={()=>{setNotice(null);setConfirmId(s.id)}}><Trash2 size={14}/></Button></TooltipTrigger>
     <TooltipContent side="right">{running?'A run is active in this conversation':'Delete conversation'}</TooltipContent></Tooltip>
   </div>})}
   {chat.workspace&&!sessions.length&&<p className="sidebar-empty">No conversations yet.</p>}</div>
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

export function ChatActions({chat}:{chat:ChatController}){
 const conversation=chat.conversation
 const exportChat=()=>{
  if(!conversation)return
  const blob=new Blob([JSON.stringify(conversation,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a')
  a.href=url;a.download='cofinance-conversation.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
 }
 return <><MemoryPanel chat={chat}/><Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" aria-label="New chat" className="topbar-new-chat" disabled={chat.busy} onClick={()=>{void chat.create().catch(()=>{})}}><Plus size={16}/></Button></TooltipTrigger><TooltipContent>New chat</TooltipContent></Tooltip><Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" aria-label="Export conversation" disabled={!conversation} onClick={exportChat}><Download size={16}/></Button></TooltipTrigger><TooltipContent>Export conversation (JSON)</TooltipContent></Tooltip></>
}

export function ChatWorkspace({chat,inspect}:{chat:ChatController;inspect:(id:string,spanId?:string)=>void}){
 const [draft,setDraft]=useState(''),scroller=useRef<HTMLDivElement>(null),textarea=useRef<HTMLTextAreaElement>(null)
 const conversation=chat.conversation,ownJob=conversation?.jobs.find(j=>activeStatuses.includes(j.status)),globalJob=chat.workspace?.active_job
 const model=chat.workspace?.models,newsRunning=chat.workspace?.news?.phase==='running'
 const runningModel=!!model&&['downloading','verifying','qualifying','loading'].includes(model.phase)
 const blocked=chat.busy||!!globalJob||runningModel||newsRunning
 const lastJob=conversation?.jobs[0],failed=!ownJob&&lastJob&&['error','interrupted','cancelled'].includes(lastJob.status)?lastJob:null
 useEffect(()=>{const el=scroller.current;if(el)el.scrollTop=el.scrollHeight},[conversation?.id,conversation?.messages.length,ownJob?.id])
 const send=async(retry=false)=>{if((!draft.trim()&&!retry)||chat.busy)return;try{await chat.send(draft.trim(),retry);setDraft('');textarea.current?.focus()}catch{}}
 const notice=runningModel?(model?.stage||model?.phase)+'. Research can start when this finishes.':newsRunning?'News collection is running. Research can start when it finishes.':globalJob&&!ownJob?'A run in another conversation is active.':''
 return <div className="chat-view">
  <div className="chat-scroll" ref={scroller} role="log" aria-label="Conversation messages" aria-live="polite"><div className="chat-column">
   <div className="mobile-conversation"><Select value={chat.selected||undefined} onValueChange={chat.select}><SelectTrigger aria-label="Choose conversation"><SelectValue placeholder="New research"/></SelectTrigger><SelectContent>{chat.workspace?.sessions.map(s=><SelectItem key={s.id} value={s.id}>{s.title}</SelectItem>)}</SelectContent></Select></div>
   {chat.error&&<div role="alert" className="error-banner"><span>{chat.error}</span><Button variant="outline" onClick={chat.refresh}>Refresh</Button></div>}
   {conversation?.message_count&&conversation.message_count>100?<p className="quiet history-note">Showing the latest 100 messages.</p>:null}
   {conversation?.messages.length?conversation.messages.map(message=><Answer key={message.id} message={message} chat={chat} inspect={inspect}/>):<div className="chat-welcome"><h2>What should we research?</h2><div className="starter-questions">{['What is NVDA trading at?','Review the NVDA thesis','Compare Apple and Nvidia'].map(question=><Button key={question} variant="outline" size="sm" onClick={()=>{setDraft(question);textarea.current?.focus()}}>{question}</Button>)}</div></div>}
   {ownJob&&<div className="live-job" role="status"><div className="live-head"><LoaderCircle size={15} className="animate-spin" aria-hidden="true"/><strong>{ownJob.status==='cancelling'?'Stopping run':ownJob.stage}</strong><Button variant="ghost" size="sm" onClick={()=>inspect(ownJob.id)}>Open execution<ArrowUpRight size={14}/></Button></div><TraceActivity key={ownJob.id} id={ownJob.id} onInspect={inspect}/></div>}
   {failed&&<div className="job-error" role="alert"><strong>{statusLabel(failed.status)}</strong><span>{failed.error}</span><Button variant="ghost" size="sm" onClick={()=>inspect(failed.id)}>Inspect trace</Button></div>}
  </div></div>
  <div className="chat-dock"><div className="chat-column">
   {notice&&<p className="dock-notice" role="status">{notice}</p>}
   <div className="composer"><Textarea ref={textarea} id="research-question" aria-label="Research question" value={draft} onChange={e=>setDraft(e.target.value)} maxLength={3000} rows={1} placeholder="Ask about a stock or crypto asset" onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(!blocked)void send()}}}/>
    <div className="composer-bar"><ModelPicker chat={chat} compact/>
     {ownJob&&<Button variant="ghost" size="sm" disabled={chat.busy||ownJob.status==='cancelling'} onClick={()=>{void chat.post('/api/jobs/cancel',{id:ownJob.id}).catch(()=>{})}}><Square size={13}/>Stop run</Button>}
     {chat.hasRetry&&<Button variant="ghost" size="sm" disabled={blocked} onClick={()=>void send(true)}><RotateCcw size={13}/>Retry submission</Button>}
     <span className="composer-spacer"/>
     <Tooltip><TooltipTrigger asChild><Button size="icon" className="send-button" aria-label="Send question" disabled={blocked||!draft.trim()||!chat.workspace} onClick={()=>void send()}><ArrowUp size={16}/></Button></TooltipTrigger><TooltipContent>Send with Enter. Shift+Enter adds a line.</TooltipContent></Tooltip>
    </div>
   </div>
  </div></div>
 </div>
}
