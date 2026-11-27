import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ChatWorkspace } from './chat-workspace'
import { TooltipProvider } from './ui/tooltip'
import type { ChatController, Message } from '@/lib/use-chat'

function makeChat(messages: Message[]): ChatController {
  return {
    workspace: {
      sessions: [{ id: 's1', title: 'hi', updated: 1700000000, tickers: ['NVDA'] }],
      watchlist: [],
      active_job: null,
      models: {
        selected: 'lfm3-1.7b-atomic-ad-q4km',
        loaded: true,
        phase: 'ready',
        stage: 'Ready',
        qualified: true,
        think_qualified: true,
        narrators: [],
        router: { loaded: true, warming: false, backend: 'standalone_fp32' },
      },
      news: { phase: 'idle', health: { collected: false, tickers: [], sources: [], uncovered: [] } },
      csrf: 'tok',
    } as never,
    conversation: { id: 's1', title: 'hi', tickers: ['NVDA'], messages, jobs: [], documents: [] } as never,
    selected: 's1',
    busy: false,
    error: '',
    memory: null,
    select: vi.fn(),
    startNew: vi.fn(),
    send: vi.fn(),
    stop: vi.fn(),
    post: vi.fn(),
    uploadDocument: vi.fn(),
    removeDocument: vi.fn(),
    removeConversation: vi.fn(),
    clearConversations: vi.fn(),
    collectNews: vi.fn(),
    forget: vi.fn(),
    refresh: vi.fn(),
  } as unknown as ChatController
}

describe('Open conversation message rendering', () => {
  it('labels open conversation replies as General chat (no live tools) without research Track actions', () => {
    const messages: Message[] = [
      { id: 'm1', role: 'user', content: "what's the capital of france", created: 1700000001, run_id: 'r1', payload: {} },
      {
        id: 'm2',
        role: 'assistant',
        content: 'Paris is the capital of France.',
        created: 1700000002,
        run_id: 'r1',
        payload: { kind: 'conversation', unverified_chat: true, tickers: ['NVDA'], mode: 'lfm3-1.7b-atomic-ad-q4km' },
      },
    ]
    const html = renderToStaticMarkup(
      <TooltipProvider>
        <ChatWorkspace chat={makeChat(messages)} inspect={vi.fn()} />
      </TooltipProvider>,
    )
    expect(html).toContain('General chat (no live tools)')
    expect(html).toContain('Paris is the capital of France.')
    expect(html).not.toContain('Track NVDA')
  })
})
