// Conversation memory panel. Explicit user control only: remember a short note, forget one item, forget the whole
// conversation. The server enforces every rule (tombstones, limits, untrusted notes); this only shows its state.
import { useState } from 'react'
import { Brain, Check, MessageSquare, StickyNote } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { canForget, itemStatus, NOTE_MAX, noteProblem } from '@/lib/memory'
import type { ChatController } from '@/lib/use-chat'
import '../memory.css'

export function MemoryPanel({chat}:{chat:ChatController}){
 const [open,setOpen]=useState(false),[note,setNote]=useState(''),[message,setMessage]=useState(''),[bad,setBad]=useState(false),[confirm,setConfirm]=useState(false)
 const memory=chat.memory&&chat.memory.session_id===chat.conversation?.id?chat.memory:null
 const items=memory?.items||[],live=items.filter(i=>i.state!=='forgotten').length
 const locked=chat.busy||!!memory?.active_run||!!chat.workspace?.active_job
 const problem=noteProblem(note,memory)
 const report=(text:string,isError=false)=>{setMessage(text);setBad(isError)}
 const save=async()=>{
  if(!note.trim()||problem||locked)return
  try{await chat.rememberNote(note.trim());setNote('');report('Note saved for this conversation.')}
  catch(e){report(e instanceof Error?e.message:'The note could not be saved.',true)}
 }
 const forget=async(id:string)=>{
  try{const r=await chat.forget(id) as {vector_copy_pending?:boolean};report(r?.vector_copy_pending?'Removed from recall. The vector copy will be deleted when the memory service is available.':'Forgotten.')}
  catch(e){report(e instanceof Error?e.message:'Could not forget that item.',true)}
 }
 const forgetAll=async()=>{
  setConfirm(false)
  try{const r=await chat.forget('*') as {forgotten?:number;pending?:number};report('Forgot '+(r?.forgotten??0)+' item'+(r?.forgotten===1?'':'s')+(r?.pending?', '+r.pending+' still deleting a copy':'')+'.')}
  catch(e){report(e instanceof Error?e.message:'Could not forget this conversation.',true)}
 }
 return <Dialog open={open} onOpenChange={v=>{setOpen(v);if(!v){setConfirm(false);setMessage('')}}}>
  <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" aria-label="Conversation memory" disabled={!chat.conversation} onClick={()=>setOpen(true)}><Brain size={16}/></Button></TooltipTrigger><TooltipContent>Conversation memory</TooltipContent></Tooltip>
  <DialogContent className="memory-dialog">
   <DialogHeader><DialogTitle>Conversation memory</DialogTitle><DialogDescription>What this conversation can recall. Forgetting removes it from memory only; the answer stays in the chat history.</DialogDescription></DialogHeader>
   {!memory?<p className="memory-empty">Memory is not available right now. Chat still works.</p>:<>
    <div className="memory-list" role="list" aria-label="Remembered items">{items.length?items.map(i=><div key={i.item_id} role="listitem" className="memory-row" data-state={i.state}>
     {i.kind==='note'?<StickyNote size={15} aria-hidden="true"/>:<MessageSquare size={15} aria-hidden="true"/>}
     <div><strong>{i.label||'Earlier turn'}</strong><small title={i.last_error||undefined}>{i.kind==='note'?'Your note / '+itemStatus(i,memory.semantic):'Answer'+(i.tickers.length?' / '+i.tickers.join(', '):'')+' / '+itemStatus(i,memory.semantic)}</small></div>
     {canForget(i)?<Button variant="ghost" size="sm" disabled={locked} onClick={()=>void forget(i.item_id)}>Forget</Button>:i.state==='forgotten'?<Check size={15} aria-label="Forgotten"/>:<span/>}
    </div>):<p className="memory-empty">Nothing is remembered yet. Accepted answers appear here after a run.</p>}</div>
    <div className="memory-note"><Input aria-label="Note to remember" placeholder="Remember a note, such as: watch Nvidia export rules" maxLength={NOTE_MAX} value={note} onChange={e=>{setNote(e.target.value);setMessage('')}} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();void save()}}}/><Button variant="outline" disabled={locked||!note.trim()||!!problem} onClick={()=>void save()}>Remember</Button></div>
    <p className="memory-help">A note is context for this conversation only. A note that names an asset joins questions about that asset; one that names none joins every question here. It is never treated as a source or an instruction, and numbers in it are never accepted into an answer. {memory.notes} of {memory.note_limit} notes used.</p>
    {memory.sweep_pending&&<p className="memory-message" role="status">Forget all is done for recall. Clearing leftover search copies is pending and retries automatically.</p>}
    {(problem||message)&&<p className="memory-message" role={problem||bad?'alert':'status'} data-bad={problem||bad?'true':undefined}>{problem||message}</p>}
    {memory.active_run&&<p className="memory-message" role="status">A run in this conversation is active. Memory can change when it finishes.</p>}
    <div className="memory-foot"><span>{live} active / backend: {memory.backend}</span>
     {confirm?<span className="memory-confirm"><span>Forget {live} item{live===1?'':'s'}?</span><Button variant="outline" size="sm" disabled={locked} onClick={()=>void forgetAll()}>Yes, forget all</Button><Button variant="ghost" size="sm" onClick={()=>setConfirm(false)}>Cancel</Button></span>:<Button variant="ghost" size="sm" disabled={locked||!live} onClick={()=>setConfirm(true)}>Forget all</Button>}</div>
   </>}
  </DialogContent>
 </Dialog>
}
