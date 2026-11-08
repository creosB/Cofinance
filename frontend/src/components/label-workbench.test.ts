import { describe, it, expect } from 'vitest'
import { labelProblem, suggestGroup, type LabelValues } from './label-workbench'

const good:LabelValues={material:'true',urgent:'false',direction:'neutral',trap:'none',event_group:'aapl-designers',notes:''}
describe('label rules mirror the finalize validator',()=>{
 it('accepts a complete label',()=>{expect(labelProblem(good)).toBe('')})
 it('rejects urgent without material',()=>{expect(labelProblem({...good,material:'false',urgent:'true'})).toBe('Urgent requires material.')})
 it('requires explicit choices',()=>{
  expect(labelProblem({...good,material:''})).toContain('material')
  expect(labelProblem({...good,direction:'up'})).toContain('direction')
  expect(labelProblem({...good,trap:''})).toContain('trap')
  expect(labelProblem({...good,event_group:'has space'})).toContain('Event group')
 })
 it('suggests a safe event group from the ticker and title',()=>{
  const g=suggestGroup({ticker:'NVDA',title:'NVIDIA and Partners Unveil the New DSX Power Platform!'})
  expect(g).toBe('nvda-nvidia-partners-unveil-dsx')
  expect(labelProblem({...good,event_group:g})).toBe('')
 })
})
