import { useCallback, useEffect, useRef, useState } from 'react'
import { object, text, numeric } from './domain'
import { parseMemory } from './memory'
import type { MemoryState } from './memory'

export interface Job {id:string;session_id:string;query:string;model:string;status:string;stage:string;error:string;created:number;updated:number}
export interface SessionMeta {id:string;title:string;created:number;updated:number}
export interface Evidence {title:string;url:string;source:string;as_of:string}
export interface PriceChart {ticker:string;currency:string;source:string;fetched_at:string;window:string;adjustment:string;points:{time:string;close:number}[]}
export type Snapshot = Record<string, string|number|boolean|null>
/** Polymarket crowd prices for a run's assets, chosen and formatted by Python. Never narrated, never advice. */
export interface CrowdMarket {ticker:string;event:string;question:string;outcome:string;probability:number;price_basis:string;bid:number|null;ask:number|null;volume_24h:number;ends:string|null;url:string;history:{horizon:string;lo:number;hi:number;n:number;resolved_rate:number;ci95?:number[]|null;study?:string|null}|null}
export interface CrowdOdds {source:string;fetched_at:string|null;markets:CrowdMarket[];errors:string[];calibration_study?:string|null;note:string}
export interface Message {id:string;role:'user'|'assistant';content:string;created:number;run_id:string|null;payload:{kind?:string;mode?:string;tickers?:string[];charts?:PriceChart[];evidence?:Evidence[];snapshots?:Record<string,Snapshot>;coverage_note?:string;crowd_odds?:CrowdOdds|null;news_collected?:boolean;faithful_synthesis?:boolean;reports?:{data:string|null;news:string|null;news_items:number};memory?:{backend:string;used_previous:boolean;recalled:{question:string;run_id:string|null;score:number;kind?:string;found_by?:string[]}[]}}}
export interface NewsTicker {ticker:string;digest:number;alert:number;newest_item:string|null}
export interface NewsSource {name:string;label:string;ticker:string;host:string;timestamp_kind:string}
export interface NewsRun {started_at:string|null;fetched:number;quarantined:number;duplicates:number;actions:Record<string,number>;source_status:Record<string,Record<string,string>>;errors:Record<string,string>}
export interface NewsHealth {collected:boolean;last_run:NewsRun|null;sources:NewsSource[];tickers:NewsTicker[];uncovered:string[];error:string}
export interface NewsState {phase:'idle'|'running'|'error';stage:string;run_id:string|null;error:string;started:number|null;finished:number|null;health:NewsHealth}
export interface Conversation extends SessionMeta {messages:Message[];jobs:Job[];tickers:string[];message_count:number}
export interface ModelState {id:string;name:string;size_bytes:number;phase:string;stage:string;selected:string;preferred?:string;selection_note?:string;downloaded_bytes:number;total_bytes:number;speed_bps:number;error:string;loaded:boolean;can_select:boolean;busy:boolean;quantization:string;runtime:string;revision:string;router:string;router_model:string;license:string;license_note:string;source:string;qualification:null|{passed:boolean;n:number;gates:Record<string,{passed:boolean;got:number;required:number}>;evaluated_at:number}}
export interface WorkspaceState {csrf:string;sessions:SessionMeta[];models:ModelState;active_job:Job|null;watchlist:{ticker:string;added:number}[];news?:NewsState}
export function parseNews(value:unknown):NewsState|undefined{
 if(value===undefined)return undefined
 const v=object(value),h=object(v.health)
 if(!['idle','running','error'].includes(text(v.phase))||typeof v.stage!=='string'||typeof v.error!=='string'||(v.run_id!==null&&!id(v.run_id))||typeof h.collected!=='boolean'||!Array.isArray(h.tickers)||!Array.isArray(h.sources)||!Array.isArray(h.uncovered))throw Error('Invalid news collection state.')
 for(const raw of h.tickers){const t=object(raw);if(typeof t.ticker!=='string'||numeric(t.digest)===undefined||numeric(t.alert)===undefined)throw Error('Invalid news coverage row.')}
 return v as unknown as NewsState
}
const id=(v:unknown)=>typeof v==='string'&&/^[0-9a-f]{32}$/.test(v)
const statuses=['queued','running','cancelling','complete','degraded','cancelled','interrupted','error']
export function parseJob(value:unknown):Job{
 const v=object(value);if(!id(v.id)||!id(v.session_id)||typeof v.query!=='string'||typeof v.model!=='string'||!statuses.includes(text(v.status))||typeof v.stage!=='string'||typeof v.error!=='string')throw Error('Invalid local job response.')
 return v as unknown as Job
}
export function parseWorkspace(value:unknown):WorkspaceState{
 const v=object(value),m=object(v.models)
 if(typeof v.csrf!=='string'||v.csrf.length<20||!Array.isArray(v.sessions)||!Array.isArray(v.watchlist)||typeof m.phase!=='string'||typeof m.selected!=='string'||typeof m.loaded!=='boolean'||typeof m.can_select!=='boolean'||numeric(m.downloaded_bytes)===undefined||numeric(m.total_bytes)===undefined)throw Error('Invalid local workspace state.')
 for(const s of v.sessions){const r=object(s);if(!id(r.id)||typeof r.title!=='string')throw Error('Invalid conversation index.')}
 if(v.active_job!==null)parseJob(v.active_job)
 parseNews(v.news)
 return v as unknown as WorkspaceState
}
export function parseConversation(value:unknown):Conversation{
 const v=object(value)
 if(!id(v.id)||typeof v.title!=='string'||!Array.isArray(v.messages)||!Array.isArray(v.jobs)||!Array.isArray(v.tickers))throw Error('Invalid conversation response.')
 for(const raw of v.messages){const m=object(raw);if(!id(m.id)||!['user','assistant'].includes(text(m.role))||typeof m.content!=='string'||!m.payload||typeof m.payload!=='object')throw Error('Invalid local message.')}
 v.jobs.forEach(parseJob)
 return v as unknown as Conversation
}
async function request(path:string,signal:AbortSignal,body?:unknown,csrf=''){
 const response=await fetch(path,{method:body===undefined?'GET':'POST',cache:'no-store',credentials:'omit',signal,
  headers:body===undefined?{}:{'Content-Type':'application/json','X-Cofinance-CSRF':csrf},body:body===undefined?undefined:JSON.stringify(body)})
 const result:unknown=await response.json()
 if(!response.ok)throw Error(text(object(result).error)||'Local action could not complete.')
 return result
}
export function useChat(){
 const [workspace,setWorkspace]=useState<WorkspaceState|null>(null),[conversation,setConversation]=useState<Conversation|null>(null)
 const [selected,setSelected]=useState(()=>{try{return localStorage.getItem('cofinance.chat')||''}catch{return ''}})
 const selectedRef=useRef(selected),[error,setError]=useState(''),[busy,setBusy]=useState(false),[version,setVersion]=useState(0)
 const alive=useRef(true),pending=useRef<{session_id:string;query:string;request_id:string;model:string}|null>(null)
 const newId=useRef<string|null>(null),[hasRetry,setHasRetry]=useState(false),[memory,setMemory]=useState<MemoryState|null>(null)
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
 const refresh=useCallback(()=>setVersion(v=>v+1),[])
 const select=useCallback((value:string)=>{selectedRef.current=value;setSelected(value);setConversation(null);setMemory(null);try{localStorage.setItem('cofinance.chat',value)}catch{}},[])
 useEffect(()=>{
  let disposed=false,timer:ReturnType<typeof setTimeout>|undefined
  const stop=new AbortController()
  async function poll(){
   const controller=new AbortController(),abort=()=>controller.abort();stop.signal.addEventListener('abort',abort,{once:true});const timeout=setTimeout(abort,10000)
   try{
    const state=parseWorkspace(await request('/api/state',controller.signal))
    if(disposed)return
    setWorkspace(old=>JSON.stringify(old)===JSON.stringify(state)?old:state)
    let sid=selectedRef.current
    if(!sid&&state.sessions[0]){sid=state.sessions[0].id;select(sid)}
    if(sid){
     if(!state.sessions.some(s=>s.id===sid)){selectedRef.current='';setSelected('');setConversation(null);try{localStorage.removeItem('cofinance.chat')}catch{};throw Error('The selected conversation is no longer available.')}
     const next=parseConversation(await request('/api/conversation?id='+sid,controller.signal))
     if(disposed||selectedRef.current!==sid)return
     setConversation(old=>JSON.stringify(old)===JSON.stringify(next)?old:next)
     // Memory is an extra view: if it cannot load, chat keeps working and the memory controls simply wait.
     try{
      const mem=parseMemory(await request('/api/memory?session_id='+sid,controller.signal))
      if(!disposed&&selectedRef.current===sid)setMemory(old=>JSON.stringify(old)===JSON.stringify(mem)?old:mem)
     }catch(e){if(!disposed&&!(e instanceof Error&&e.name==='AbortError'))setMemory(null)}
    }
    if(!disposed)setError('')
   }catch(e){if(!disposed)setError(e instanceof Error&&e.name!=='AbortError'?e.message:'The local server did not respond. Your saved conversations are unchanged.')}
   finally{clearTimeout(timeout);stop.signal.removeEventListener('abort',abort);if(!disposed)timer=setTimeout(poll,1200)}
  }
  void poll();return()=>{disposed=true;stop.abort();clearTimeout(timer)}
 },[selected,version,select])
 const post=useCallback(async(path:string,body:unknown,timeoutMs=15000)=>{
  if(!workspace)throw Error('Wait for the local workspace to connect.')
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),timeoutMs)
  setBusy(true)
  try{const result=await request(path,controller.signal,body,workspace.csrf);if(alive.current){setError('');refresh()}return result}
  catch(e){const message=e instanceof Error&&e.name!=='AbortError'?e.message:'The action timed out. Retry safely using the same request.';if(alive.current)setError(message);throw Error(message)}
  finally{clearTimeout(timeout);if(alive.current)setBusy(false)}
 },[workspace,refresh])
 const create=useCallback(async()=>{
  newId.current ||= crypto.randomUUID().replaceAll('-','')
  const value=parseConversation(await post('/api/sessions',{request_id:newId.current}));newId.current=null;select(value.id);setConversation(value);return value.id
 },[post,select])
 const send=useCallback(async(query:string,retry=false)=>{
  try{
   if(!retry||!pending.current){const sid=selectedRef.current||await create();pending.current={session_id:sid,query,request_id:crypto.randomUUID().replaceAll('-',''),model:workspace?.models.selected||'facts'}}
   const result=parseJob(await post('/api/chat',pending.current));pending.current=null;setHasRetry(false);refresh();return result
  }catch(e){setHasRetry(!!pending.current);throw e}
 },[create,post,refresh,workspace?.models.selected])
 // One request id per click, reused if the same click is retried after a timeout.
 const newsRequest=useRef<string|null>(null)
 const collectNews=useCallback(async()=>{
  newsRequest.current ||= crypto.randomUUID().replaceAll('-','')
  const result=await post('/api/news/collect',{request_id:newsRequest.current});newsRequest.current=null;return result
 },[post])
 // Explicit memory actions. One request id per click, reused if the same click is retried, so a timeout never doubles a note.
 const memoryRequests=useRef<Record<string,string>>({})
 const once=(key:string)=>(memoryRequests.current[key] ||= crypto.randomUUID().replaceAll('-',''))
 const rememberNote=useCallback(async(note:string)=>{
  const sid=selectedRef.current;if(!sid)throw Error('Open a conversation first.')
  const key='note:'+sid+':'+note,result=await post('/api/memory/note',{session_id:sid,text:note,request_id:once(key)});delete memoryRequests.current[key];return result
 },[post])
 const forget=useCallback(async(itemId:string)=>{
  const sid=selectedRef.current;if(!sid)throw Error('Open a conversation first.')
  const key='forget:'+sid+':'+itemId,result=await post('/api/memory/forget',{session_id:sid,item_id:itemId,request_id:once(key)});delete memoryRequests.current[key];return result
 },[post])
 // Delete is permanent: memory copies, messages, runs and run traces. Up to 2 minutes, since deleting memory copies can
 // start the local embedder. Same request id on a retried click, so a timeout never deletes twice or reports wrongly.
 const removeConversation=useCallback(async(sessionId:string)=>{
  const key='delete:'+sessionId,result=await post('/api/sessions/delete',{session_id:sessionId,request_id:once(key)},120000);delete memoryRequests.current[key]
  if(selectedRef.current===sessionId)select('')  // the poll then opens the newest remaining conversation
  return result
 },[post,select])
 const clearConversations=useCallback(async()=>{
  const result=await post('/api/sessions/clear',{request_id:once('clear')},300000);delete memoryRequests.current.clear
  select('');return result
 },[post,select])
 return {workspace,conversation,memory,selected,error,busy,hasRetry,select,create,send,post,refresh,collectNews,rememberNote,forget,removeConversation,clearConversations}
}
export type ChatController=ReturnType<typeof useChat>
