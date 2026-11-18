import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { TokenUsage,parseUsage,type Usage } from './token-usage'
const usage=():Usage=>({version:1,scope:'chat_narrator',input:1200,output:34,total:1234,attempts:2,reported:2,complete:true,roles:[
 {actor:'Data Analyst',model:'fixture',input:600,output:14,total:614,attempts:1,reported:1},
 {actor:'Market Team Lead',model:'fixture',input:600,output:20,total:620,attempts:1,reported:1}]})
const render=(u:unknown,running=false)=>renderToStaticMarkup(<TokenUsage usage={u} running={running}/>)
describe('runtime token counter',()=>{
 it('shows exact input, output and total, with a keyboard disclosure and honest scope',()=>{
  const html=render(usage())
  for(const text of ['1,234 narrator tokens','1,200','34','Data Analyst','Market Team Lead','including retries and rejected output','Excludes GLiNER','Not context-window occupancy'])expect(html).toContain(text)
  expect(html).toContain('<details');expect(html).toContain('<summary>');expect(html).not.toContain(' open=')
 })
 it('never displays a missing or malformed receipt as zero',()=>{
  for(const u of [undefined,null,{}, {...usage(),total:999},{...usage(),input:-1},{...usage(),input:true},{...usage(),output:NaN},{...usage(),roles:[]},{...usage(),complete:'yes'}]){
   expect(parseUsage(u)).toBeNull();const html=render(u);expect(html).toContain('unavailable');expect(html).not.toContain('0 narrator tokens')
  }
 })
 it('keeps partial counts labelled as a known subtotal',()=>{
  const u=usage();u.complete=false;u.attempts=3;u.roles[1].attempts=2
  const html=render(u)
  expect(html).toContain('1,234 recorded tokens (partial)');expect(html).toContain('2 of 3 attempts');expect(html).not.toContain('1,234 narrator tokens')
 })
 it('shows true zero only for a measured no-narrator path',()=>{
  const u:Usage={version:1,scope:'chat_narrator',input:0,output:0,total:0,attempts:0,reported:0,complete:true,roles:[]}
  expect(render(u)).toContain('0 narrator tokens (no narrator call)')
  expect(render(u,true)).toContain('No completed narrator calls yet')
 })
 it('distinguishes unreported, running and complete',()=>{
  const u=usage();u.input=0;u.output=0;u.total=0;u.reported=0;u.complete=false;u.roles=u.roles.map(r=>({...r,input:0,output:0,total:0,reported:0}))
  expect(render(u)).toContain('runtime did not report counts')
  expect(render(usage(),true)).toContain('so far')
  expect(render(undefined,true)).toContain('after each narrator call')
 })
})
