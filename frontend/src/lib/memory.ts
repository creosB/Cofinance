// Conversation memory: types, response validation and the wording the Memory panel shows.
// Pure functions only, so they are tested without a DOM. The server owns every rule; this only displays its state.
import { object, numeric, text } from './domain'

export interface MemoryItem {item_id:string;kind:'turn'|'note';label:string;tickers:string[];state:'active'|'delete_pending'|'forgotten';registered:boolean;in_keyword:boolean;in_semantic:boolean;last_error:string;created:number}
export interface MemoryState {session_id:string;backend:string;semantic:boolean;active_run:boolean;note_limit:number;notes:number;items:MemoryItem[];sweep_pending?:boolean}

const states=['active','delete_pending','forgotten']
export const NOTE_MAX=500

export function parseMemory(value:unknown):MemoryState{
 const v=object(value)
 if(typeof v.session_id!=='string'||!Array.isArray(v.items)||typeof v.active_run!=='boolean'||typeof v.semantic!=='boolean'||numeric(v.note_limit)===undefined||numeric(v.notes)===undefined)throw Error('Invalid memory response.')
 for(const raw of v.items){
  const i=object(raw)
  if(typeof i.item_id!=='string'||!['turn','note'].includes(text(i.kind))||!states.includes(text(i.state))||typeof i.label!=='string'||!Array.isArray(i.tickers))throw Error('Invalid memory item.')
 }
 return v as unknown as MemoryState
}

/** One line saying where an item can still be found. A pending delete says so: it is already blocked from recall. */
export function itemStatus(item:MemoryItem,semantic:boolean):string{
 if(item.state==='forgotten')return 'Forgotten'
 if(item.state==='delete_pending')return 'Blocked from recall, copy deletion pending'
 // Notes are pinned in the ledger, not searched: they join every question about their assets, or every question.
 if(item.kind==='note')return item.tickers.length?'Pinned to questions about '+item.tickers.join(', '):'Pinned to every question here'
 if(!item.registered)return 'Indexed on the next question'
 const where=[item.in_keyword?'keyword':'',semantic&&item.in_semantic?'semantic':''].filter(Boolean)
 return where.length?'Searchable by '+where.join(' and ')+' search':'Not indexed yet'
}

export const canForget=(item:MemoryItem)=>item.state==='active'

/** Same limits the server enforces, so an obvious mistake is caught before a round trip. '' means fine. */
export function noteProblem(value:string,memory:Pick<MemoryState,'notes'|'note_limit'>|null):string{
 const t=value.trim()
 if(!t)return ''
 if(t.length>NOTE_MAX)return 'A note is at most '+NOTE_MAX+' characters.'
 if(memory&&memory.notes>=memory.note_limit)return 'This conversation already has '+memory.note_limit+' notes. Forget one first.'
 return ''
}

export const itemFor=(memory:MemoryState|null,conversationId:string|undefined,runId:string|null|undefined)=>
 memory&&conversationId&&runId&&memory.session_id===conversationId?memory.items.find(i=>i.item_id===runId):undefined

/** 'Context: previous answer, 2 earlier turns and 1 note'. Notes are counted apart: they are context, never evidence. */
export function contextLine(m:{used_previous:boolean;recalled:{kind?:string}[]}|undefined):string{
 if(!m)return ''
 const notes=m.recalled.filter(r=>r.kind==='note').length,turns=m.recalled.length-notes
 const parts=[m.used_previous?'previous answer':'',turns?turns+' earlier '+(turns===1?'turn':'turns'):'',notes?notes+(notes===1?' note':' notes'):''].filter(Boolean)
 if(!parts.length)return ''
 return 'Context: '+(parts.length>2?parts.slice(0,-1).join(', ')+' and '+parts[parts.length-1]:parts.join(' and '))
}
