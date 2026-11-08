import { describe,it,expect } from 'vitest'
import { parseMemory,itemStatus,canForget,noteProblem,itemFor,contextLine,NOTE_MAX } from './memory'
import type { MemoryItem,MemoryState } from './memory'

const item=(p:Partial<MemoryItem>={}):MemoryItem=>({item_id:'a'.repeat(32),kind:'turn',label:'Review NVDA',tickers:['NVDA'],state:'active',registered:true,in_keyword:true,in_semantic:true,last_error:'',created:1,...p})
const state=(p:Partial<MemoryState>={}):MemoryState=>({session_id:'s'.repeat(32),backend:'hybrid',semantic:true,active_run:false,note_limit:50,notes:0,items:[item()],...p})

describe('memory response',()=>{
 it('accepts the server shape and rejects anything else',()=>{
  expect(parseMemory(state()).items).toHaveLength(1)
  expect(()=>parseMemory({})).toThrow()
  expect(()=>parseMemory({...state(),items:[{...item(),state:'deleted'}]})).toThrow()
  expect(()=>parseMemory({...state(),items:[{...item(),kind:'summary'}]})).toThrow()
  expect(()=>parseMemory({...state(),notes:'3'})).toThrow()
 })
})

describe('what the panel says',()=>{
 it('names where an active item can be found, and only real copies',()=>{
  expect(itemStatus(item(),true)).toBe('Searchable by keyword and semantic search')
  expect(itemStatus(item({in_semantic:false}),true)).toBe('Searchable by keyword search')
  expect(itemStatus(item({in_semantic:true}),false)).toBe('Searchable by keyword search') // semantic backend not running: do not claim it
  expect(itemStatus(item({in_keyword:false,in_semantic:false}),true)).toBe('Not indexed yet')
  expect(itemStatus(item({registered:false,in_keyword:false,in_semantic:false}),true)).toBe('Indexed on the next question')
 })
 it('describes notes as pinned, never as searchable copies',()=>{
  expect(itemStatus(item({kind:'note',in_keyword:false,in_semantic:false}),true)).toBe('Pinned to questions about NVDA')
  expect(itemStatus(item({kind:'note',tickers:[],in_keyword:false,in_semantic:false}),true)).toBe('Pinned to every question here')
  expect(itemStatus(item({kind:'note',state:'forgotten'}),true)).toBe('Forgotten')
 })
 it('is honest about a delete that has not finished',()=>{
  expect(itemStatus(item({state:'delete_pending'}),true)).toBe('Blocked from recall, copy deletion pending')
  expect(itemStatus(item({state:'forgotten'}),true)).toBe('Forgotten')
  expect(canForget(item())).toBe(true)
  expect(canForget(item({state:'delete_pending'}))).toBe(false)
  expect(canForget(item({state:'forgotten'}))).toBe(false)
 })
 it('mirrors the server limits for notes without replacing them',()=>{
  expect(noteProblem('   ',null)).toBe('')
  expect(noteProblem('x'.repeat(NOTE_MAX+1),null)).toContain('500')
  expect(noteProblem('NVDA',{notes:50,note_limit:50})).toContain('Forget one first')
  expect(noteProblem('NVDA',{notes:49,note_limit:50})).toBe('')
 })
 it('finds an answer\'s item only inside the same conversation',()=>{
  const m=state()
  expect(itemFor(m,m.session_id,'a'.repeat(32))?.label).toBe('Review NVDA')
  expect(itemFor(m,'t'.repeat(32),'a'.repeat(32))).toBeUndefined()
  expect(itemFor(null,m.session_id,'a'.repeat(32))).toBeUndefined()
  expect(itemFor(m,m.session_id,null)).toBeUndefined()
 })
})

describe('context line under an answer',()=>{
 it('keeps the old wording and counts notes apart from turns',()=>{
  expect(contextLine(undefined)).toBe('')
  expect(contextLine({used_previous:false,recalled:[]})).toBe('')
  expect(contextLine({used_previous:true,recalled:[{},{}]})).toBe('Context: previous answer and 2 earlier turns')
  expect(contextLine({used_previous:true,recalled:[{kind:'turn'},{kind:'note'}]})).toBe('Context: previous answer, 1 earlier turn and 1 note')
  expect(contextLine({used_previous:false,recalled:[{kind:'note'}]})).toBe('Context: 1 note')
 })
})
