import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { CrowdOdds, historyLine, pct } from './crowd-odds'
import type { CrowdOdds as Odds } from '@/lib/use-chat'

const base:Odds={source:'Polymarket public API, read-only',fetched_at:'2026-11-10T00:00:00+00:00',errors:[],note:'Crowd prices are not advice. Cofinance never trades.',markets:[
 {ticker:'BTC',event:'What price will Bitcoin hit in November?',question:'Will Bitcoin reach $80,000 in November?',outcome:'Yes',probability:.495,price_basis:'midpoint',bid:.49,ask:.5,volume_24h:331148,ends:'2026-12-01T05:00:00+00:00',url:'https://polymarket.com/event/what-price-will-bitcoin-hit-in-november-2026',
  history:{horizon:'30d',lo:.4,hi:.5,n:120,resolved_rate:.41,ci95:[.33,.5]}}]}

describe('crowd odds card',()=>{
 it('shows the recorded crowd price, outcome and read-only source',()=>{
  const html=renderToStaticMarkup(<CrowdOdds odds={base}/>)
  expect(html).toContain('Crowd odds')
  expect(html).toContain('Polymarket, read-only')
  expect(html).toContain('Will Bitcoin reach $80,000 in November?')
  expect(html).toContain('50%')
  expect(html).toContain('Cofinance never trades')
  expect(html).not.toContain('<button')
 })
 it('states the measured calibration with its sample size',()=>{
  expect(historyLine(base.markets[0].history!)).toBe('In the study, markets priced 40% to 50% about a month before closing went this way 41% of the time (120 markets, likely range 33% to 50%).')
 })
 it('only links to Polymarket event pages',()=>{
  const bad={...base,markets:[{...base.markets[0],url:'javascript:alert(1)'}]}
  const html=renderToStaticMarkup(<CrowdOdds odds={bad}/>)
  expect(html).not.toContain('href=')
  expect(html).toContain('Will Bitcoin reach')
 })
 it('degrades quietly when the lookup failed',()=>{
  const html=renderToStaticMarkup(<CrowdOdds odds={{...base,markets:[],errors:['BTC: TimeoutError']}}/>)
  expect(html).toContain('Crowd odds are unavailable right now')
  expect(renderToStaticMarkup(<CrowdOdds odds={{...base,markets:[]}}/>)).toBe('')
 })
 it('keeps small and near-certain prices readable',()=>{
  expect(pct(.004)).toBe('0.4%');expect(pct(.999)).toBe('99.9%');expect(pct(.5)).toBe('50%')
 })
})
