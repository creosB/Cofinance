import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { parseRun, type Run } from './domain'
import { CHAT_FLOWS, detectFlow, interactiveStages, stageLabel, type ChatFlow } from './run-map'
import { RunMap } from '../components/run-map'
import { StepExplanation } from '../components/step-explanation'

function makeTrace(flow: ChatFlow): Run {
  const id = 'a'.repeat(32)
  const sid = (n: number) => n.toString(16).padStart(32, '0')
  const base = {
    id,
    format: 'cofinance-trace/1' as const,
    title: `Flow fixture: ${flow}`,
    mode: 'live' as const,
    started_at: '2026-11-27T11:00:00Z',
    duration_ms: 450,
    revision: 1,
    dropped_spans: 0,
    storage_errors: 0,
  }
  if (flow === 'open_conversation') {
    return parseRun({
      ...base,
      status: 'complete',
      summary: { origin: 'chat', flow, answer_kind: 'conversation', answer: 'Here are a few ways to think about $100.', risk_mode: 'open_conversation' },
      spans: [
        { id: sid(1), parent_id: null, name: 'Route conversational request', kind: 'route', actor: 'Routing executive', model: '', status: 'complete', start_ms: 0, duration_ms: 100, inputs: { query: "I've $100. Now make a suggestion" }, outputs: { intent: 'open_conversation', tickers: [] } },
        { id: sid(2), parent_id: null, name: 'Answer open conversation', kind: 'model', actor: 'Local assistant', model: 'AtomicChat/LFM3-1.7B-Uncensored-GGUF:AD-Q4_K_M', status: 'complete', start_ms: 100, duration_ms: 300, inputs: { live_tools: false }, outputs: 'Here are a few ways to think about $100.' },
      ],
    })
  }
  if (flow === 'clarification') {
    return parseRun({
      ...base,
      status: 'complete',
      summary: { origin: 'chat', flow, answer_kind: 'clarification', answer: 'Tool-only mode needs a stock, ETF, crypto asset, or attached PDF.', risk_mode: 'not_resolved' },
      spans: [
        { id: sid(1), parent_id: null, name: 'Route conversational request', kind: 'route', actor: 'Routing executive', model: '', status: 'complete', start_ms: 0, duration_ms: 90, inputs: { query: 'hello' }, outputs: { intent: 'open_conversation', tickers: [] } },
        { id: sid(2), parent_id: null, name: 'Asset clarification required', kind: 'decision', actor: 'Python', model: '', status: 'complete', start_ms: 90, duration_ms: 1, inputs: {}, outputs: { reason: 'facts_only_no_asset_or_document' } },
      ],
    })
  }
  if (flow === 'single_pass_data_answer' || flow === 'watchlist_add') {
    const intent = flow === 'watchlist_add' ? 'watchlist_add' : 'price_snapshot'
    return parseRun({
      ...base,
      status: 'complete',
      summary: { origin: 'chat', flow, answer_kind: 'research', answer: 'NVDA last price is 138.5 USD.' },
      spans: [
        { id: sid(1), parent_id: null, name: 'Route conversational request', kind: 'route', actor: 'Routing executive', model: '', status: 'complete', start_ms: 0, duration_ms: 80, inputs: { query: 'NVDA' }, outputs: { intent, tickers: ['NVDA'] } },
        { id: sid(2), parent_id: null, name: 'Executive Route', kind: 'handoff', actor: 'GLiNER2.5-Decide', model: '', status: 'complete', start_ms: 80, duration_ms: 1, inputs: {}, outputs: { delegated_agents: ['Data Analyst'] } },
        { id: sid(3), parent_id: null, name: 'Narrate evidence', kind: 'model', actor: 'Data Analyst', model: 'AtomicChat/LFM3-1.7B-Uncensored-GGUF:AD-Q4_K_M', status: 'complete', start_ms: 90, duration_ms: 250, inputs: {}, outputs: 'NVDA last price is 138.5 USD.' },
        { id: sid(4), parent_id: null, name: 'Synthesis faithfulness', kind: 'gate', actor: 'Market Team Lead', model: '', status: 'complete', start_ms: 340, duration_ms: 1, inputs: {}, outputs: { faithful_synthesis: true, method: 'single_pass_data_answer', validation_reason: 'single_pass_data_answer' } },
        { id: sid(5), parent_id: null, name: 'Interactive research', kind: 'run', actor: 'Python', model: '', status: 'complete', start_ms: 0, duration_ms: 350, inputs: {}, outputs: { answer: 'NVDA last price is 138.5 USD.' } },
      ],
    })
  }
  if (flow === 'python_market_answer') {
    return parseRun({
      ...base,
      status: 'complete',
      summary: { origin: 'chat', flow, answer_kind: 'research', answer: 'NVDA previous-session close: 130.0 USD on 2026-11-26.' },
      spans: [
        { id: sid(1), parent_id: null, name: 'Route conversational request', kind: 'route', actor: 'Routing executive', model: '', status: 'complete', start_ms: 0, duration_ms: 80, inputs: { query: 'Previous close for NVDA' }, outputs: { intent: 'price_snapshot', tickers: ['NVDA'] } },
        { id: sid(2), parent_id: null, name: 'Executive Route', kind: 'handoff', actor: 'GLiNER2.5-Decide', model: '', status: 'complete', start_ms: 80, duration_ms: 1, inputs: {}, outputs: { delegated_agents: ['Data Analyst'] } },
        { id: sid(3), parent_id: null, name: 'Market snapshot', kind: 'tool', actor: 'Data Analyst', model: '', status: 'complete', start_ms: 85, duration_ms: 40, inputs: { ticker: 'NVDA' }, outputs: { price: 138.5 } },
        { id: sid(4), parent_id: null, name: 'Synthesis faithfulness', kind: 'gate', actor: 'Market Team Lead', model: '', status: 'complete', start_ms: 130, duration_ms: 1, inputs: {}, outputs: { faithful_synthesis: true, method: 'python_market_answer', validation_reason: 'previous_close_request' } },
        { id: sid(5), parent_id: null, name: 'Interactive research', kind: 'run', actor: 'Python', model: '', status: 'complete', start_ms: 0, duration_ms: 135, inputs: {}, outputs: { answer: 'NVDA previous-session close: 130.0 USD on 2026-11-26.' } },
      ],
    })
  }
  if (flow === 'python_news_answer') {
    return parseRun({
      ...base,
      status: 'complete',
      summary: { origin: 'chat', flow, answer_kind: 'research', answer: 'NVDA news digest.' },
      spans: [
        { id: sid(1), parent_id: null, name: 'Route conversational request', kind: 'route', actor: 'Routing executive', model: '', status: 'complete', start_ms: 0, duration_ms: 80, inputs: { query: 'NVDA news' }, outputs: { intent: 'news_catalysts', tickers: ['NVDA'] } },
        { id: sid(2), parent_id: null, name: 'Executive Route', kind: 'handoff', actor: 'GLiNER2.5-Decide', model: '', status: 'complete', start_ms: 80, duration_ms: 1, inputs: {}, outputs: { delegated_agents: ['News Researcher'] } },
        { id: sid(3), parent_id: null, name: 'News Researcher', kind: 'agent', actor: 'News Researcher', model: '', status: 'complete', start_ms: 90, duration_ms: 10, inputs: {}, outputs: { summary: 'NVDA news digest.' } },
        { id: sid(4), parent_id: null, name: 'Synthesis faithfulness', kind: 'gate', actor: 'Market Team Lead', model: '', status: 'complete', start_ms: 100, duration_ms: 1, inputs: {}, outputs: { faithful_synthesis: true, method: 'python_news_answer', validation_reason: 'python_news_answer' } },
        { id: sid(5), parent_id: null, name: 'Interactive research', kind: 'run', actor: 'Python', model: '', status: 'complete', start_ms: 0, duration_ms: 110, inputs: {}, outputs: { answer: 'NVDA news digest.' } },
      ],
    })
  }
  if (flow === 'document_research' || flow === 'multi_agent_research') {
    const isDoc = flow === 'document_research'
    return parseRun({
      ...base,
      status: 'complete',
      summary: { origin: 'chat', flow, answer_kind: 'research', answer: 'Synthesized report.' },
      spans: [
        { id: sid(1), parent_id: null, name: 'Route conversational request', kind: 'route', actor: 'Routing executive', model: '', status: 'complete', start_ms: 0, duration_ms: 80, inputs: { query: 'Thesis' }, outputs: { intent: 'thesis_review', tickers: isDoc ? [] : ['NVDA'] } },
        { id: sid(2), parent_id: null, name: 'Executive Route', kind: 'handoff', actor: 'GLiNER2.5-Decide', model: '', status: 'complete', start_ms: 80, duration_ms: 1, inputs: {}, outputs: { delegated_agents: isDoc ? ['Document Reader'] : ['Data Analyst', 'News Researcher'] } },
        ...(isDoc ? [{ id: sid(3), parent_id: null, name: 'Document Reader', kind: 'agent', actor: 'Document Reader', model: '', status: 'complete', start_ms: 90, duration_ms: 15, inputs: {}, outputs: { summary: 'Circular excerpt.' } }] : [{ id: sid(3), parent_id: null, name: 'Narrate evidence', kind: 'model', actor: 'Data Analyst', model: 'AtomicChat/LFM3-1.7B-Uncensored-GGUF:AD-Q4_K_M', status: 'complete', start_ms: 90, duration_ms: 150, inputs: {}, outputs: 'NVDA 138.5 USD.' }]),
        { id: sid(4), parent_id: null, name: 'Narrate evidence', kind: 'model', actor: 'Market Team Lead', model: 'AtomicChat/LFM3-1.7B-Uncensored-GGUF:AD-Q4_K_M', status: 'complete', start_ms: 250, duration_ms: 150, inputs: {}, outputs: 'Synthesized report.' },
        { id: sid(5), parent_id: null, name: 'Synthesis faithfulness', kind: 'gate', actor: 'Market Team Lead', model: '', status: 'complete', start_ms: 400, duration_ms: 1, inputs: {}, outputs: { faithful_synthesis: true, validation_reason: 'numbers_matched' } },
        { id: sid(6), parent_id: null, name: 'Interactive research', kind: 'run', actor: 'Python', model: '', status: 'complete', start_ms: 0, duration_ms: 410, inputs: {}, outputs: { answer: 'Synthesized report.' } },
      ],
    })
  }
  return parseRun({
    ...base,
    status: 'error',
    summary: { origin: 'chat', flow, answer_kind: 'none' },
    spans: [
      { id: sid(1), parent_id: null, name: 'Route conversational request', kind: 'route', actor: 'Routing executive', model: '', status: 'complete', start_ms: 0, duration_ms: 50, inputs: { query: 'test' }, outputs: { intent: 'open_conversation', tickers: [] } },
      { id: sid(2), parent_id: null, name: flow === 'cancelled' ? 'Run cancelled' : 'Run failed', kind: 'decision', actor: 'Python', model: '', status: 'fallback', start_ms: 60, duration_ms: 1, inputs: {}, outputs: { reason: flow } },
    ],
  })
}

function loadFlowTraces(): Record<ChatFlow, Run> {
  const realPath = process.env.COFINANCE_REAL_TRACES_FILE
  if (realPath) {
    const raw = JSON.parse(readFileSync(realPath, 'utf-8')) as Record<string, unknown>
    const out = {} as Record<ChatFlow, Run>
    for (const flow of CHAT_FLOWS) {
      expect(raw[flow], `Missing real Python trace for flow ${flow}`).toBeTruthy()
      out[flow] = parseRun(raw[flow])
    }
    return out
  }
  const out = {} as Record<ChatFlow, Run>
  for (const flow of CHAT_FLOWS) out[flow] = makeTrace(flow)
  return out
}

describe('Complete ChatService -> RunMap flow contract (all 9 flows)', () => {
  const traces = loadFlowTraces()

  it('covers every flow with zero dangling route slashes and honest terminal stages', () => {
    for (const flow of CHAT_FLOWS) {
      const run = traces[flow]
      expect(detectFlow(run)).toBe(flow)
      const stages = interactiveStages(run)
      const router = stages.find(s => s.id === 'router')
      expect(router, `${flow}: missing router`).toBeTruthy()
      expect(router!.detail.trim().endsWith('/'), `${flow}: dangling slash in ${router!.detail}`).toBe(false)

      const result = stages.find(s => s.id === 'result')
      expect(result, `${flow}: missing result`).toBeTruthy()

      if (flow === 'cancelled' || flow === 'error') {
        expect(result!.state, `${flow}: must not accept`).not.toBe('complete')
        continue
      }

      expect(result!.state, `${flow}: result must be complete`).toBe('complete')
      expect(result!.detail, `${flow}: must not claim no accepted answer`).not.toContain('No accepted final answer recorded')

      if (flow === 'open_conversation') {
        const assistant = stages.find(s => s.id === 'assistant')
        expect(assistant?.state).toBe('complete')
        expect(assistant?.detail).toContain('Direct local chat')
        expect(stages.some(s => s.state === 'not_observed')).toBe(false)
        const html = renderToStaticMarkup(<RunMap run={run} onInspect={() => {}} />)
        expect(html).toContain('General chat (no live tools)')
        expect(html).toContain('Local assistant')
        expect(html).not.toContain('Python fallback')
        expect(html).not.toContain('No accepted final answer recorded')
      } else if (flow === 'clarification') {
        expect(stages.some(s => s.state === 'not_observed')).toBe(false)
        const html = renderToStaticMarkup(<RunMap run={run} onInspect={() => {}} />)
        expect(html).toContain('Clarification requested')
        expect(html).not.toContain('Python fallback')
      } else {
        const html = renderToStaticMarkup(<RunMap run={run} onInspect={() => {}} />)
        expect(html).toContain(stageLabel('complete'))
      }
    }
  })

  it('renders human-readable Inspector explanations for every non-research and routing span', () => {
    const sid = (n: number) => n.toString(16).padStart(32, '0')
    const cases: [Parameters<typeof StepExplanation>[0]['span'], string][] = [
      [{ id: sid(1), parent_id: null, name: 'Answer open conversation', kind: 'model', actor: 'Local assistant', model: 'LFM3', status: 'complete', start_ms: 0, duration_ms: 100, inputs: {}, outputs: 'Hi' }, 'Direct local chat (no live tools)'],
      [{ id: sid(2), parent_id: null, name: 'Asset clarification required', kind: 'decision', actor: 'Python', model: '', status: 'complete', start_ms: 0, duration_ms: 1, inputs: {}, outputs: { reason: 'empty_watchlist' } }, 'Your watchlist is empty'],
      [{ id: sid(3), parent_id: null, name: 'Resolve asset symbol', kind: 'tool', actor: 'Yahoo Finance search', model: '', status: 'complete', start_ms: 0, duration_ms: 50, inputs: { term: 'Palantir' }, outputs: { symbol: 'PLTR' } }, 'Palantir'],
      [{ id: sid(4), parent_id: null, name: 'Reuse conversation assets', kind: 'decision', actor: 'Python', model: '', status: 'complete', start_ms: 0, duration_ms: 1, inputs: {}, outputs: { tickers: ['NVDA'] } }, 'Conversation follow-up'],
      [{ id: sid(5), parent_id: null, name: 'Run cancelled', kind: 'decision', actor: 'Python', model: '', status: 'fallback', start_ms: 0, duration_ms: 1, inputs: {}, outputs: { reason: 'user_cancelled' } }, 'Stopped by user'],
      [{ id: sid(6), parent_id: null, name: 'Run failed', kind: 'decision', actor: 'Python', model: '', status: 'fallback', start_ms: 0, duration_ms: 1, inputs: {}, outputs: { error_type: 'RuntimeError' } }, 'RuntimeError'],
    ]
    for (const [span, expected] of cases) {
      const html = renderToStaticMarkup(<StepExplanation span={span} spans={[span]} />)
      expect(html, span.name).toContain(expected)
    }
  })
})
