import { describe, expect, it } from 'vitest'
import { duration, evidenceUrl, flatten, modelGroups, parseCorpus, parseMeta, parseRun, parseRuns, reason, type Run, type Span } from './domain'
const span=(id:string,kind:Span['kind']='run',parent_id:string|null=null):Span=>({id:id.repeat(32),parent_id,name:'Fixture '+id,kind,actor:'Test actor',model:'',status:'complete',start_ms:0,duration_ms:10,inputs:{},outputs:{}})
const fixture:Run={id:'a'.repeat(32),format:'cofinance-trace/1',title:'Unit-test fixture',mode:'fixture',status:'complete',started_at:'2026-11-07T10:00:00Z',duration_ms:10,revision:1,spans:[span('b')]}
describe('trace contracts',()=>{
 it('accepts valid explicitly labelled fixture data',()=>expect(parseRun(fixture)).toEqual(fixture))
 it('rejects unknown run modes',()=>expect(()=>parseMeta({...fixture,mode:'pretend-live'})).toThrow())
 it('rejects duplicate and malformed steps',()=>{
  expect(()=>parseRun({...fixture,spans:[span('b'),span('b')]})).toThrow()
  expect(()=>parseRun({...fixture,spans:[{...span('b'),duration_ms:'fast'}]})).toThrow()
  expect(()=>parseRun({...fixture,spans:[{...span('b'),status:'invented'}]})).toThrow()
 })
 it('rejects invalid run lists',()=>expect(()=>parseRuns({runs:[]})).toThrow())
 it('accepts a missing corpus without creating sample evidence',()=>expect(parseCorpus(null)).toBeNull())
 it('rejects nonnumeric corpus counts',()=>expect(()=>parseCorpus({rows:'53',window_days:30,collected_at:'now'})).toThrow())
})
describe('lineage and runtime inventory',()=>{
 it('walks parents before children even if event arrival differs',()=>{
  const child=span('c','gate','b'.repeat(32)),root=span('b')
  expect(flatten([child,root]).map(s=>[s.id,s.depth])).toEqual([[root.id,0],[child.id,1]])
 })
 it('handles missing parents and cycles without infinite recursion',()=>{
  const a=span('a','agent','b'.repeat(32)),b=span('b','agent',a.id)
  expect(flatten([a,b])).toHaveLength(2)
  expect(flatten([span('c','gate','d'.repeat(32))])[0].depth).toBe(0)
 })
 it('counts only model spans, not decisions with a model name',()=>{
  const model={...span('c','model'),model:'fixture/model'},decision={...span('d','decision'),model:'unused/candidate'}
  const groups=modelGroups([model,decision]);expect(groups).toHaveLength(1);expect(groups[0].model).toBe('fixture/model');expect(groups[0].calls).toBe(1)
 })
})
describe('safe evidence and honest presentation',()=>{
 it('allows only credential-free HTTP evidence links',()=>{
  expect(evidenceUrl('https://example.test/article')).toBe('https://example.test/article')
  for(const v of ['javascript:alert(1)','file:///secret','https://user:pass@example.test',null,{}])expect(evidenceUrl(v)).toBeNull()
 })
 it('never invents a duration',()=>{expect(duration(null)).toBe('Not recorded');expect(duration(1000)).toBe('1.00 s')})
 it('explains failures and Python downgrades without claiming a sent alert',()=>{
  expect(reason('classifier_error:TimeoutError')).toContain('never promoted')
  expect(reason('alert_downgraded:ticker_cooldown')).toContain('ticker cooldown')
  expect(reason('material_and_urgent')).toContain('not a sent notification')
 })
})
