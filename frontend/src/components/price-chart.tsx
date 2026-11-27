import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PriceChart as ChartData } from '@/lib/use-chat'

const CHART_HEIGHT = 220
const PAD = { left: 56, right: 14, top: 12, bottom: 28 }

interface Point {
 time: string
 ms: number
 close: number
}

function formatPrice(value: number, currency: string): string {
 const digits = Math.abs(value) < 1 ? 6 : 2
 const formatted = value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: digits })
 return currency ? `${formatted} ${currency}` : formatted
}

function formatAxisPrice(value: number): string {
 if (Math.abs(value) >= 1000) return value.toLocaleString('en-US', { maximumFractionDigits: 0 })
 if (Math.abs(value) < 1) return value.toFixed(4)
 return value.toFixed(2)
}

function formatShortDate(ms: number): string {
 const d = new Date(ms)
 if (Number.isNaN(d.getTime())) return ''
 return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit', timeZone: 'UTC' })
}

function formatFullDate(ms: number): string {
 const d = new Date(ms)
 if (Number.isNaN(d.getTime())) return ''
 return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
}

function cssColor(styles: CSSStyleDeclaration, token: string, fallback: string): string {
 const value = styles.getPropertyValue(token).trim()
 return value || fallback
}

export function normalizeSeries(points: ChartData['points']): Point[] {
 const out: Point[] = []
 for (const p of points || []) {
  const ms = Date.parse(p.time)
  if (!Number.isFinite(ms) || typeof p.close !== 'number' || !Number.isFinite(p.close)) continue
  out.push({ time: p.time, ms, close: p.close })
 }
 out.sort((a, b) => a.ms - b.ms)
 return out
}

export function PriceChart({ data }: { data: ChartData }) {
 const wrapRef = useRef<HTMLDivElement>(null)
 const canvasRef = useRef<HTMLCanvasElement>(null)
 const [width, setWidth] = useState(0)
 const [activeIndex, setActiveIndex] = useState<number | null>(null)
 const series = useMemo(() => normalizeSeries(data.points), [data.points])

 const summary = useMemo(() => {
  if (!series.length) return null
  const first = series[0], last = series[series.length - 1]
  const delta = first.close > 0 ? ((last.close - first.close) / first.close) * 100 : null
  return { first, last, delta }
 }, [series])

 const selected = activeIndex !== null && series[activeIndex] ? series[activeIndex] : summary?.last ?? null

 useEffect(() => {
  setActiveIndex(null)
 }, [series])

 useEffect(() => {
  const el = wrapRef.current
  if (!el) return
  const measure = () => {
   const next = Math.round(el.getBoundingClientRect().width)
   if (next > 0) setWidth(prev => (prev === next ? prev : next))
  }
  measure()
  if (typeof ResizeObserver === 'undefined') return
  const ro = new ResizeObserver(measure)
  ro.observe(el)
  return () => ro.disconnect()
 }, [])

 useEffect(() => {
  const canvas = canvasRef.current
  if (!canvas || width <= 0 || !series.length) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 3))
  canvas.width = Math.round(width * dpr)
  canvas.height = Math.round(CHART_HEIGHT * dpr)
  canvas.style.width = `${width}px`
  canvas.style.height = `${CHART_HEIGHT}px`
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  const styles = getComputedStyle(document.documentElement)
  const bg = cssColor(styles, '--background', '#0b0d12')
  const lineColor = cssColor(styles, '--primary', '#7c8cff')
  const gridColor = cssColor(styles, '--card', '#1a1f2c')
  const borderColor = cssColor(styles, '--border', '#2c3345')
  const quietColor = cssColor(styles, '--quiet', '#9aa3b2')
  const fgColor = cssColor(styles, '--foreground', '#f5f7fb')

  ctx.fillStyle = bg
  ctx.fillRect(0, 0, width, CHART_HEIGHT)

  const plotW = Math.max(10, width - PAD.left - PAD.right)
  const plotH = Math.max(10, CHART_HEIGHT - PAD.top - PAD.bottom)

  let minV = series[0].close, maxV = series[0].close
  for (const pt of series) {
   if (pt.close < minV) minV = pt.close
   if (pt.close > maxV) maxV = pt.close
  }
  const padV = maxV === minV ? Math.max(Math.abs(maxV) * 0.05, 1) : (maxV - minV) * 0.08
  const lo = minV - padV
  const hi = maxV + padV
  const spanV = hi - lo || 1

  const xFor = (i: number) => PAD.left + (series.length === 1 ? plotW / 2 : (i / (series.length - 1)) * plotW)
  const yFor = (v: number) => PAD.top + (1 - (v - lo) / spanV) * plotH

  ctx.strokeStyle = gridColor
  ctx.lineWidth = 1
  ctx.fillStyle = quietColor
  ctx.font = '11px Geist Variable, system-ui, sans-serif'
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  for (let tick = 0; tick <= 2; tick++) {
   const frac = tick / 2
   const val = hi - frac * spanV
   const y = Math.round(PAD.top + frac * plotH) + 0.5
   ctx.beginPath()
   ctx.moveTo(PAD.left, y)
   ctx.lineTo(PAD.left + plotW, y)
   ctx.stroke()
   ctx.fillText(formatAxisPrice(val), PAD.left - 8, y)
  }

  ctx.textBaseline = 'top'
  const xIndices = series.length === 1 ? [0] : [0, Math.floor((series.length - 1) / 2), series.length - 1]
  xIndices.forEach((idx, pos) => {
   ctx.textAlign = pos === 0 ? 'left' : pos === xIndices.length - 1 ? 'right' : 'center'
   ctx.fillText(formatShortDate(series[idx].ms), xFor(idx), PAD.top + plotH + 8)
  })

  if (series.length > 1) {
   const grad = ctx.createLinearGradient(0, PAD.top, 0, PAD.top + plotH)
   grad.addColorStop(0, lineColor)
   grad.addColorStop(1, bg)
   ctx.save()
   ctx.globalAlpha = 0.16
   ctx.beginPath()
   ctx.moveTo(xFor(0), PAD.top + plotH)
   for (let i = 0; i < series.length; i++) ctx.lineTo(xFor(i), yFor(series[i].close))
   ctx.lineTo(xFor(series.length - 1), PAD.top + plotH)
   ctx.closePath()
   ctx.fillStyle = grad
   ctx.fill()
   ctx.restore()
  }

  ctx.beginPath()
  for (let i = 0; i < series.length; i++) {
   const x = xFor(i), y = yFor(series[i].close)
   if (i === 0) ctx.moveTo(x, y)
   else ctx.lineTo(x, y)
  }
  ctx.strokeStyle = lineColor
  ctx.lineWidth = 1.75
  ctx.lineJoin = 'round'
  ctx.stroke()

  if (activeIndex !== null && series[activeIndex]) {
   const x = xFor(activeIndex), y = yFor(series[activeIndex].close)
   ctx.strokeStyle = borderColor
   ctx.lineWidth = 1
   ctx.setLineDash([3, 3])
   ctx.beginPath()
   ctx.moveTo(x, PAD.top)
   ctx.lineTo(x, PAD.top + plotH)
   ctx.stroke()
   ctx.setLineDash([])

   ctx.beginPath()
   ctx.arc(x, y, 4, 0, Math.PI * 2)
   ctx.fillStyle = lineColor
   ctx.fill()
   ctx.lineWidth = 1.5
   ctx.strokeStyle = fgColor
   ctx.stroke()
  }
 }, [width, series, activeIndex])

 const indexFromClientX = useCallback((clientX: number) => {
  const el = wrapRef.current
  if (!el || !series.length) return null
  const rect = el.getBoundingClientRect()
  const plotW = Math.max(10, rect.width - PAD.left - PAD.right)
  const rel = Math.max(0, Math.min(1, (clientX - rect.left - PAD.left) / plotW))
  return Math.round(rel * (series.length - 1))
 }, [series.length])

 const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
  if (!series.length) return
  const step = e.shiftKey ? 10 : 1
  if (e.key === 'ArrowRight') {
   e.preventDefault()
   setActiveIndex(prev => Math.min(series.length - 1, (prev ?? series.length - 1) + step))
  } else if (e.key === 'ArrowLeft') {
   e.preventDefault()
   setActiveIndex(prev => Math.max(0, (prev ?? series.length - 1) - step))
  } else if (e.key === 'Home') {
   e.preventDefault()
   setActiveIndex(0)
  } else if (e.key === 'End') {
   e.preventDefault()
   setActiveIndex(series.length - 1)
  } else if (e.key === 'Escape') {
   setActiveIndex(null)
  }
 }

 const valueText = selected ? `${formatFullDate(selected.ms)}: ${formatPrice(selected.close, data.currency)}` : 'No price points'

 return (
  <figure className="price-chart">
   <figcaption>
    <strong>{data.ticker}</strong>
    <span className="chart-readout" aria-live="polite">
     {selected ? (
      <>
       <span className="mono">{formatPrice(selected.close, data.currency)}</span>
       <span> · {formatFullDate(selected.ms)}</span>
       {summary?.delta !== null && summary?.delta !== undefined && activeIndex === null && (
        <span className="mono"> · {summary.delta >= 0 ? '+' : ''}{summary.delta.toFixed(1)}% ({data.window})</span>
       )}
      </>
     ) : (
      <span>{data.window} close, {data.currency}</span>
     )}
    </span>
   </figcaption>
   {series.length > 0 ? (
    <div
     ref={wrapRef}
     className="chart-canvas-wrap"
     role="img"
     tabIndex={0}
     aria-label={`${data.ticker} daily close-price chart, ${data.window}, ${data.currency}`}
     aria-description={valueText}
     onPointerMove={e => setActiveIndex(indexFromClientX(e.clientX))}
     onPointerLeave={() => setActiveIndex(null)}
     onPointerCancel={() => setActiveIndex(null)}
     onKeyDown={onKeyDown}
    >
     <canvas ref={canvasRef} height={CHART_HEIGHT} aria-hidden="true" />
    </div>
   ) : (
    <p className="quiet" role="status">No chart points returned.</p>
   )}
   <details className="chart-data">
    <summary>{data.source}: {data.adjustment.charAt(0).toLowerCase() + data.adjustment.slice(1)}</summary>
    <div className="table-scroll">
     <table>
      <thead><tr><th>Date</th><th>Close ({data.currency})</th></tr></thead>
      <tbody>{series.map(p => <tr key={p.time}><td>{formatFullDate(p.ms)}</td><td className="mono">{p.close}</td></tr>)}</tbody>
     </table>
    </div>
   </details>
  </figure>
 )
}
