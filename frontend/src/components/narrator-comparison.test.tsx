import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { NarratorComparison } from './narrator-comparison'
import benchmark from '@/lib/narrator-benchmark.json'

describe('recorded narrator comparison',()=>{
 it('retains LFM3 without presenting the experiment as a live selection',()=>{
  expect(benchmark.retainedNarratorId).toBe('lfm3-1.7b-q4km')
  const html=renderToStaticMarkup(<NarratorComparison/>)
  expect(html).toContain('LFM3 remains the chosen narrator')
  expect(html).toContain('Retained choice')
  expect(html).toContain('Candidate only')
  expect(html).not.toContain('<button')
  expect(html).not.toContain('<select')
  expect(html).not.toContain('/api/models')
 })
 it('keeps the observed scores rather than inventing a quality ranking',()=>{
  expect(benchmark.models.map(m=>m.narrationMedianMs)).toEqual([3837,2770,4393])
  expect(benchmark.models.map(m=>m.effectiveTokensPerSecond)).toEqual([9.5,13.9,5.4])
  for(const model of benchmark.models){
   expect(model.scores.narration).toEqual({passed:8,total:8})
   expect(model.scores.number_faithfulness).toEqual({passed:6,total:6})
   expect(model.passedGate).toBe(true)
   expect(model.revision).toMatch(/^[a-f0-9]{40}$/)
   expect(model.modelSha256).toMatch(/^[a-f0-9]{64}$/)
   expect(model.reportSha256).toMatch(/^[a-f0-9]{64}$/)
  }
 })
 it('marks the small sample, repeated cases and timing limits',()=>{
  const html=renderToStaticMarkup(<NarratorComparison/>)
  expect(html).toContain('Seven unique narrator cases, repeated twice')
  expect(html).toContain('not a demonstrated quality upgrade')
  expect(html).toContain('Background activity may affect timings')
  expect(benchmark.method.allCasesPerArm).toBe(46)
  expect(benchmark.method.uniqueNarratorCases).toBe(7)
 })
 it('uses a collapsed keyboard-accessible disclosure and semantic table',()=>{
  const html=renderToStaticMarkup(<NarratorComparison/>)
  expect(html).toContain('<details>')
  expect(html).not.toContain('<details open')
  expect(html).toContain('<summary>Recorded smoke comparison</summary>')
  expect(html).toContain('tabindex="0"')
  expect(html).toContain('scope="col"')
  expect(html).toContain('scope="row"')
 })
})
