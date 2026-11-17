import { describe,it,expect,vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { NarratorRows, narratorOptions, narratorsOf } from './model-library'
import type { ModelState, NarratorState } from '@/lib/use-chat'

const narrator=(over:Partial<NarratorState>):NarratorState=>({id:'lfm3-1.7b-q4km',name:'LFM3 1.7B',publisher:'Liquid AI',quantization:'Q4_K_M',size_bytes:1099627808,revision:'r',source:'s',note:'',downloaded:true,qualified:true,loaded:false,active:false,...over})
const base=narrator({active:true,loaded:true})
const atomic=narrator({id:'lfm3-1.7b-atomic-ad-q4km',name:'LFM3 1.7B Uncensored',publisher:'AtomicChat',quantization:'AD-Q4_K_M',size_bytes:1132208000,qualified:false})
const state=(narrators?:NarratorState[])=>({id:'lfm3-1.7b-q4km',name:'LFM3 1.7B',quantization:'Q4_K_M',size_bytes:1,phase:'ready',can_select:true,loaded:true,narrators} as unknown as ModelState)

describe('narrator choice',()=>{
 it('offers every pinned narrator and blocks one that has not passed its own qualification',()=>{
  const options=narratorOptions(state([base,atomic]),false)
  expect(options.map(o=>o.id)).toEqual(['lfm3-1.7b-q4km','lfm3-1.7b-atomic-ad-q4km'])
  expect(options.map(o=>o.disabled)).toEqual([false,true])
  expect(options[1].label).toBe('LFM3 1.7B Uncensored / AD-Q4_K_M (qualification required)')
  expect(narratorOptions(state([base,{...atomic,qualified:true}]),true)[1]).toEqual({id:'lfm3-1.7b-atomic-ad-q4km',disabled:false,label:'LFM3 1.7B Uncensored'})
 })
 it('keeps an older single-narrator server working',()=>{
  expect(narratorsOf(state(undefined)).map(n=>[n.id,n.qualified,n.active])).toEqual([['lfm3-1.7b-q4km',true,true]])
  expect(narratorsOf(null)).toEqual([])
 })
 it('shows each narrator with its status and offers the next step only for a non-active one',()=>{
  const act=vi.fn()
  const html=renderToStaticMarkup(<NarratorRows narrators={[base,atomic]} locked={false} act={act}/>)
  expect(html).toContain('LFM3 1.7B Uncensored')
  expect(html).toContain('AtomicChat / AD-Q4_K_M')
  expect(html).toContain('Needs qualification')
  expect(html).toContain('Verify &amp; test LFM3 1.7B Uncensored')
  expect(html).toContain('active narrator')
  expect(html).toContain('Loaded')
  expect(html.match(/<button/g)?.length).toBe(1)
  const missing=renderToStaticMarkup(<NarratorRows narrators={[{...atomic,downloaded:false}]} locked={true} act={act}/>)
  expect(missing).toContain('Download LFM3 1.7B Uncensored')
  expect(missing).toContain('disabled')
  expect(renderToStaticMarkup(<NarratorRows narrators={[{...atomic,qualified:true}]} locked={false} act={act}/>)).not.toContain('<button')
 })
})
