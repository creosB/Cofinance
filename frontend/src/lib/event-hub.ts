import type { Connect, StreamHandlers } from './live-sync'

interface Subscriber {
 handlers: StreamHandlers
 sessionId?: string
}

let source: EventSource | null = null
let activeUrl = ''
let isOpen = false
const subscribers = new Set<Subscriber>()

function desiredUrl(): string {
 let sid = ''
 for (const sub of subscribers) {
  if (sub.sessionId) {
   sid = sub.sessionId
   break
  }
 }
 return '/api/events' + (sid ? '?session_id=' + encodeURIComponent(sid) : '')
}

function closeSource() {
 if (source) {
  source.close()
  source = null
 }
 activeUrl = ''
 isOpen = false
}

function reconcile() {
 if (typeof EventSource === 'undefined' || subscribers.size === 0) {
  closeSource()
  return
 }
 const url = desiredUrl()
 if (source && activeUrl === url) return
 closeSource()
 activeUrl = url
 const events = new EventSource(url)
 source = events
 events.onopen = () => {
  if (source !== events) return
  isOpen = true
  for (const sub of [...subscribers]) sub.handlers.open()
 }
 events.onerror = () => {
  if (source !== events) return
  isOpen = false
  for (const sub of [...subscribers]) sub.handlers.error()
 }
 events.addEventListener('change', () => {
  if (source !== events) return
  for (const sub of [...subscribers]) sub.handlers.change()
 })
}

export function sharedEvents(sessionId?: string): Connect | undefined {
 if (typeof EventSource === 'undefined') return undefined
 return handlers => {
  const sub: Subscriber = { handlers, sessionId }
  subscribers.add(sub)
  reconcile()
  if (isOpen) handlers.open()
  return () => {
   subscribers.delete(sub)
   reconcile()
  }
 }
}

export function resetEventHubForTests() {
 subscribers.clear()
 closeSource()
}
