import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { HeadlineSignal, countLine, signed } from './headline-signal'
import type { HeadlineSignal as Signal } from '@/lib/use-chat'

const base:Signal={version:'headline-v1',call_line:.5,dev_auc:.585,note:'Experimental. Not a forecast or financial advice. Cofinance never trades.',assets:[
 {ticker:'NVDA',score:.6234,call:'UP',served:3,judged:3,headlines:[{title:'A',label:'UP',impact:.5},{title:'B',label:'UP',impact:.2},{title:'C',label:'ABSTAIN',impact:-.08}]},
 {ticker:'AAPL',score:-.1,call:'ABSTAIN',served:4,judged:2,headlines:[]},
 {ticker:'BTC',score:0,call:'ABSTAIN',served:2,judged:0,headlines:[]}]}

describe('headline signal card',()=>{
 it('shows the Python score, the lean and the experimental label',()=>{
  const html=renderToStaticMarkup(<HeadlineSignal signal={base}/>)
  expect(html).toContain('Headline signal')
  expect(html).toContain('Experimental')
  expect(html).toContain('Leans up')
  expect(html).toContain('+0.62')
  expect(html).toContain('No clear lean')
  expect(html).toContain('Cofinance never trades')
  expect(html).not.toContain('<button')
 })
 it('hides assets with no judged headline and says when only some were judged',()=>{
  const html=renderToStaticMarkup(<HeadlineSignal signal={base}/>)
  expect(html).not.toContain('BTC')
  expect(countLine(base.assets[1],.5)).toBe('Sum of 2 judged of 4 headlines. A lean needs 0.5 or more either way.')
  expect(countLine({served:1,judged:1},.5)).toBe('Sum of 1 headline judgment. A lean needs 0.5 or more either way.')
 })
 it('renders nothing without any judgment',()=>{
  expect(renderToStaticMarkup(<HeadlineSignal signal={{...base,assets:[base.assets[2]]}}/>)).toBe('')
  expect([signed(.5),signed(-.25),signed(0)]).toEqual(['+0.50','-0.25','0.00'])
 })
})
