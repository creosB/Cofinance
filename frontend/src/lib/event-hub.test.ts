import { afterEach, describe, expect, it } from 'vitest'
import { resetEventHubForTests, sharedEvents } from './event-hub'

class FakeEventSource {
 static instances: FakeEventSource[] = []
 url: string
 closed = false
 onopen: (() => void) | null = null
 onerror: (() => void) | null = null
 listeners = new Map<string, Set<() => void>>()

 constructor(url: string) {
  this.url = url
  FakeEventSource.instances.push(this)
 }

 addEventListener(type: string, fn: () => void) {
  let set = this.listeners.get(type)
  if (!set) {
   set = new Set()
   this.listeners.set(type, set)
  }
  set.add(fn)
 }

 emit(type: string) {
  for (const fn of this.listeners.get(type) || []) fn()
 }

 close() {
  this.closed = true
 }
}

describe('sharedEvents SSE hub', () => {
 const original = globalThis.EventSource

 afterEach(() => {
  resetEventHubForTests()
  FakeEventSource.instances = []
  globalThis.EventSource = original
 })

 it('multiplexes chat and dashboard subscribers onto one EventSource', () => {
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource
  let chatChanges = 0, dashChanges = 0, dashOpens = 0
  const unsubDash = sharedEvents()!({ open: () => { dashOpens++ }, change: () => { dashChanges++ }, error: () => {} })
  expect(FakeEventSource.instances).toHaveLength(1)
  expect(FakeEventSource.instances[0].url).toBe('/api/events')

  const unsubChat = sharedEvents('s123')!({ open: () => {}, change: () => { chatChanges++ }, error: () => {} })
  expect(FakeEventSource.instances).toHaveLength(2)
  expect(FakeEventSource.instances[0].closed).toBe(true)
  expect(FakeEventSource.instances[1].closed).toBe(false)
  expect(FakeEventSource.instances[1].url).toBe('/api/events?session_id=s123')

  FakeEventSource.instances[1].onopen?.()
  FakeEventSource.instances[1].emit('change')
  expect(dashOpens).toBe(1)
  expect(chatChanges).toBe(1)
  expect(dashChanges).toBe(1)

  unsubChat()
  expect(FakeEventSource.instances).toHaveLength(3)
  expect(FakeEventSource.instances[1].closed).toBe(true)
  expect(FakeEventSource.instances[2].url).toBe('/api/events')

  unsubDash()
  expect(FakeEventSource.instances[2].closed).toBe(true)
 })
})
