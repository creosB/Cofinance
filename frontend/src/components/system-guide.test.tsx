import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { SystemGuide } from './run-map'
import { StepExplanation } from './step-explanation'
import type { Span } from '@/lib/domain'

const span=(partial:Partial<Span>):Span=>({id:'a'.repeat(32),parent_id:null,name:'Step',kind:'gate',actor:'Market Team Lead',model:'',status:'complete',start_ms:0,duration_ms:10,inputs:{},outputs:{},...partial})

describe('system guide',()=>{
 it('describes the current chat path: selected narrator for two roles, News Researcher in Python',()=>{
  const html=renderToStaticMarkup(<SystemGuide/>)
  expect(html).not.toContain('Base narrator')
  expect(html).not.toContain('Hy-Memory remains planned')
  expect(html).toContain('The selected LFM3 narrator writes the Data Analyst report and the Team Lead answer')
  expect(html).toContain('The News Researcher is Python, no model call.')
  expect(html).toContain('hybrid keyword and semantic recall')
 })
 it('renders claim provenance conflicts and updated Deep Think note in StepExplanation',()=>{
  const model=span({id:'b'.repeat(32),kind:'model',name:'Narrate evidence',actor:'Market Team Lead',outputs:'AAPL trades at 138.5%.'})
  const gate=span({id:'c'.repeat(32),kind:'gate',name:'Synthesis faithfulness',actor:'Market Team Lead',outputs:{
   faithful_synthesis:false,allowed_numbers:[138.5],rejected_numbers:[],validation_reason:'claim_provenance',
   provenance_conflicts:[{kind:'asset_misattribution',text:'138.5%',claim:{asset:'AAPL',fields:['price'],unit:'percent',cue:'price',clause:'AAPL trades at 138.5%.'},matches:[{asset:'NVDA',field:'price',shown:'138.50',unit:'currency'}],violations:['asset','unit']}],
  }})
  const html=renderToStaticMarkup(<StepExplanation span={gate} spans={[model,gate]}/>)
  expect(html).toContain('Claim provenance conflicts')
  expect(html).toContain('138.5%')
  expect(html).toContain('NVDA price 138.50')
  const modelHtml=renderToStaticMarkup(<StepExplanation span={model} spans={[model]}/>)
  expect(modelHtml).toContain('Deep Think is an opt-in Market Team Lead mode')
  expect(modelHtml).not.toContain('Thinking is disabled for the managed narrator')
 })
})
