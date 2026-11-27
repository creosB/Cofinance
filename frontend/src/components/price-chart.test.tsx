import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { normalizeSeries, PriceChart } from './price-chart'

describe('PriceChart canvas component', () => {
 it('sorts out-of-order points and filters invalid values', () => {
  const series = normalizeSeries([
   { time: '2026-11-27T00:00:00Z', close: 150 },
   { time: 'not-a-date', close: 999 },
   { time: '2026-11-25T00:00:00Z', close: 120 },
   { time: '2026-11-26T00:00:00Z', close: Number.NaN },
  ])
  expect(series.map(p => p.close)).toEqual([120, 150])
 })

 it('renders accessible chart semantics, delta readout, and fallback table', () => {
  const html = renderToStaticMarkup(
   <PriceChart
    data={{
     ticker: 'NVDA',
     currency: 'USD',
     source: 'Yahoo Finance',
     fetched_at: '2026-11-27T16:00:00Z',
     window: '1 year',
     adjustment: 'Source Close, not total return',
     points: [
      { time: '2026-01-02T00:00:00Z', close: 100 },
      { time: '2026-11-27T00:00:00Z', close: 125 },
     ],
    }}
   />,
  )
  expect(html).toContain('role="img"')
  expect(html).toContain('aria-label="NVDA daily close-price chart, 1 year, USD"')
  expect(html).toContain('aria-description="Nov 27, 2026: 125.00 USD"')
  expect(html).toContain('+25.0% (1 year)')
  expect(html).toContain('<table>')
  expect(html).toContain('125')
 })

 it('renders clean empty state when no valid points exist', () => {
  const html = renderToStaticMarkup(
   <PriceChart
    data={{
     ticker: 'MISS',
     currency: 'USD',
     source: 'Yahoo Finance',
     fetched_at: '2026-11-27T16:00:00Z',
     window: '1 year',
     adjustment: 'Source Close',
     points: [],
    }}
   />,
  )
  expect(html).toContain('No chart points returned.')
 })
})
