import { describe, expect, it } from 'vitest'
import { liveSync } from './live-sync'
import type { StreamHandlers } from './live-sync'

function fakeClock(){
 const due=new Map<number,()=>void>();let n=0
 return {after:(fn:()=>void)=>{due.set(++n,fn);return n},cancel:(h:unknown)=>{due.delete(h as number)},
  pending:()=>due.size,fire:()=>{const fns=[...due.values()];due.clear();fns.forEach(f=>f())}}
}
function fetcher(){
 const waiting:(()=>void)[]=[];let calls=0,inFlight=0,maxInFlight=0
 return {fn:()=>{calls++;inFlight++;maxInFlight=Math.max(maxInFlight,inFlight);return new Promise<void>(r=>waiting.push(()=>{inFlight--;r()}))},
  finish:()=>waiting.shift()?.(),get calls(){return calls},get maxInFlight(){return maxInFlight}}
}
function fakeStream(){
 let on:StreamHandlers|undefined,closed=false
 return {connect:(h:StreamHandlers)=>{on=h;return()=>{closed=true}},get on(){return on!},get closed(){return closed}}
}
const flush=async()=>{for(let i=0;i<6;i++)await Promise.resolve()}

describe('liveSync', () => {
 it('polls on the fallback timer when there is no stream', async () => {
  const clock=fakeClock(),f=fetcher()
  liveSync(f.fn,{clock})
  expect(f.calls).toBe(1);expect(clock.pending()).toBe(0)
  f.finish();await flush();expect(clock.pending()).toBe(1)
  clock.fire();expect(f.calls).toBe(2)
 })
 it('stops the timer while the stream is open and fetches on each change', async () => {
  const clock=fakeClock(),f=fetcher(),s=fakeStream()
  liveSync(f.fn,{clock,connect:s.connect})
  s.on.open();f.finish();await flush()
  expect(clock.pending()).toBe(0)
  s.on.change();expect(f.calls).toBe(2)
  f.finish();await flush();expect(clock.pending()).toBe(0)
 })
 it('never overlaps fetches and collapses a burst of changes into one follow-up', async () => {
  const clock=fakeClock(),f=fetcher(),s=fakeStream()
  liveSync(f.fn,{clock,connect:s.connect})
  s.on.open();s.on.change();s.on.change();s.on.change()
  expect(f.calls).toBe(1)
  f.finish();await flush();expect(f.calls).toBe(2)
  f.finish();await flush();expect(f.calls).toBe(2);expect(f.maxInFlight).toBe(1)
 })
 it('falls back to polling when the stream drops and stops again when it reopens', async () => {
  const clock=fakeClock(),f=fetcher(),s=fakeStream()
  liveSync(f.fn,{clock,connect:s.connect})
  s.on.open();f.finish();await flush()
  s.on.error();expect(f.calls).toBe(2)
  f.finish();await flush();expect(clock.pending()).toBe(1)
  s.on.open();expect(clock.pending()).toBe(0)
 })
 it('a drop during a fetch schedules the fallback once that fetch ends', async () => {
  const clock=fakeClock(),f=fetcher(),s=fakeStream()
  liveSync(f.fn,{clock,connect:s.connect})
  s.on.open();s.on.error();expect(f.calls).toBe(1)
  f.finish();await flush();expect(clock.pending()).toBe(1)
 })
 it('keeps looping after a failed fetch', async () => {
  const clock=fakeClock();let calls=0
  liveSync(async()=>{calls++;throw Error('down')},{clock})
  await flush();expect(clock.pending()).toBe(1)
  clock.fire();await flush();expect(calls).toBe(2);expect(clock.pending()).toBe(1)
 })
 it('dispose closes the stream, cancels the timer and ignores late events', async () => {
  const clock=fakeClock(),f=fetcher(),s=fakeStream()
  const dispose=liveSync(f.fn,{clock,connect:s.connect})
  f.finish();await flush();expect(clock.pending()).toBe(1)
  dispose();expect(s.closed).toBe(true);expect(clock.pending()).toBe(0)
  s.on.change();s.on.error();await flush();expect(f.calls).toBe(1);expect(clock.pending()).toBe(0)
 })
})
