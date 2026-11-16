import { describe, expect, it } from 'vitest'
import { refText, segments, targetId } from './citations'

const text='NVDA last traded at 186.26, down 2.10%, near its high of 212.19 and 186.26 again.'
const fact=(field:string,shown:string)=>({kind:'fact',ticker:'NVDA',field,label:field,shown,source:'Yahoo',as_of:'2026-11-13'})
const at=(s:string,from=0)=>{const start=text.indexOf(s,from);return {start,end:start+s.length,text:s}}
const good=[{...at('186.26'),refs:[fact('price','186.26')]},{...at('2.10%'),refs:[fact('change','down 2.10')]},
 {...at('212.19'),refs:[fact('high','212.19')]},{...at('186.26',40),refs:[fact('price','186.26')]}]

describe('segments', () => {
 it('joins back to exactly the answer', () => {
  expect(segments(text,good).map(s=>s.text).join('')).toBe(text)
 })
 it('numbers by source in order of first appearance and reuses a number for the same source', () => {
  expect(segments(text,good).filter(s=>s.n).map(s=>[s.text,s.n])).toEqual([['186.26',1],['2.10%',2],['212.19',3],['186.26',1]])
 })
 it('drops citations that no longer fit the text instead of guessing', () => {
  const bad=[{...at('186.26'),text:'186.25',refs:[fact('price','186.26')]},{start:-1,end:3,text:'NVD',refs:[fact('price','x')]},
   {start:5,end:500,text:'x',refs:[fact('price','x')]},{...at('212.19'),refs:[]},{...at('2.10%'),refs:[{kind:'model',source:'',as_of:''}]},
   {...at('212.19'),refs:[{kind:'evidence',index:3,title:'t',source:'',as_of:''}]},null,'186.26']
  const out=segments(text,bad,2)
  expect(out).toEqual([{text}])
 })
 it('keeps the first of overlapping citations', () => {
  const wide={start:good[0].start,end:good[1].end,text:text.slice(good[0].start,good[1].end),refs:[fact('price','x')]}
  const out=segments(text,[wide,good[1]])
  expect(out.filter(s=>s.n).map(s=>s.text)).toEqual([wide.text])
  expect(out.map(s=>s.text).join('')).toBe(text)
 })
 it('accepts evidence refs inside the list and plain text without citations', () => {
  expect(segments('3 chips',[{start:0,end:1,text:'3',refs:[{kind:'evidence',index:1,title:'Ships 3 chips',source:'Reuters',as_of:''}]}],2)[0].n).toBe(1)
  expect(segments('',undefined)).toEqual([{text:''}])
 })
 it('describes and targets each source', () => {
  expect(refText(fact('price','186.26') as never)).toBe('NVDA price: 186.26 (Yahoo, 2026-11-13)')
  expect(targetId('m1',fact('price','x') as never)).toBe('facts-m1-NVDA')
  expect(targetId('m1',{kind:'evidence',index:2,title:'',source:'',as_of:''})).toBe('source-m1-2')
 })
})
