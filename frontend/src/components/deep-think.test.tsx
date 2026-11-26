import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { TooltipProvider } from '@/components/ui/tooltip'
import { DeepThinkNote,DeepThinkPanel,DeepThinkToggle,thinkOf } from './deep-think'
import type { ChatController } from '@/lib/use-chat'
const chatWith=(selected:string,think?:unknown)=>({workspace:{models:{selected,id:'m',can_select:true,think}},post:async()=>({})}) as unknown as ChatController
const think=(qualified:boolean,report:unknown=null)=>({budget:384,qualified,loaded:false,report})
const toggle=(c:ChatController,on:boolean,disabled=false,onOpenModels?:()=>void)=>renderToStaticMarkup(<TooltipProvider><DeepThinkToggle chat={c} on={on} onChange={()=>{}} disabled={disabled} onOpenModels={onOpenModels}/></TooltipProvider>)
describe('deep think switch',()=>{
 it('is hidden without a narrator and disabled until deep think passed its own test',()=>{
  expect(toggle(chatWith('facts',think(true)),true)).toBe('')
  expect(toggle({workspace:null} as unknown as ChatController,true)).toBe('')
  expect(toggle(chatWith('m',undefined),true)).toBe('')
  const untested=toggle(chatWith('m',think(false)),true)
  expect(untested).toContain('disabled');expect(untested).toContain('aria-pressed="false"');expect(untested).toContain('data-locked="true"')
  expect(untested).not.toContain('Test in Models')
  const withShortcut=toggle(chatWith('m',think(false)),false,false,()=>{})
  expect(withShortcut).toContain('Test in Models')
 })
 it('shows on only when requested and qualified, and locks during a run',()=>{
  const c=chatWith('m',think(true))
  const active=toggle(c,true)
  expect(active).toContain('aria-pressed="true"');expect(active).toContain('deep-think-chip');expect(active).toContain('On')
  expect(active).not.toContain('Test in Models')
  expect(toggle(c,false)).toContain('aria-pressed="false"')
  expect(toggle(c,false)).not.toContain('deep-think-chip')
  expect(toggle(c,true,true)).toContain('disabled')
 })
 it('labels an answer only when deep think was requested, and says why it was not used',()=>{
  expect(renderToStaticMarkup(<DeepThinkNote use={undefined}/>)).toBe('')
  expect(renderToStaticMarkup(<DeepThinkNote use={{requested:false,used:false}}/>)).toBe('')
  expect(renderToStaticMarkup(<DeepThinkNote use={{requested:true,used:true}}/>)).toContain('Deep think')
  const skipped=renderToStaticMarkup(<DeepThinkNote use={{requested:true,used:false,note:'Deep think could not start, so this answer was written without it.'}}/>)
  expect(skipped).toContain('without it');expect(skipped).not.toContain('answer-kind')
 })
 it('explains the cost, never promises accuracy, and offers the test in Models',()=>{
  const html=renderToStaticMarkup(<DeepThinkPanel chat={chatWith('m',think(false))} locked={false}/>)
  for(const text of ['Not tested yet','384 tokens','2.5 s instead of 0.4 s','never shown or saved','Headline judging never thinks','Test deep think'])expect(html).toContain(text)
  expect(html.toLowerCase()).not.toContain('more accurate')
  const failed=renderToStaticMarkup(<DeepThinkPanel chat={chatWith('m',think(false,{passed:false,gates:{narration_faithful:{passed:false,got:.5,required:.9}}}))} locked={false}/>)
  expect(failed).toContain('Did not pass, so it stays off');expect(failed).toContain('narration faithful: failed')
  const passed=renderToStaticMarkup(<DeepThinkPanel chat={chatWith('m',think(true,{passed:true,backend:'vulkan'}))} locked/>)
  expect(passed).toContain('Passed the smoke gates on Vulkan GPU');expect(passed).toContain('Test deep think again');expect(passed).toContain('disabled')
  expect(renderToStaticMarkup(<DeepThinkPanel chat={chatWith('m',undefined)} locked={false}/>)).toBe('')
  expect(thinkOf(null)).toBeUndefined()
 })
})
