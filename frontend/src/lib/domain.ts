export type Kind = 'run'|'route'|'agent'|'model'|'decision'|'tool'|'gate'|'handoff'
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type RecordData = { [key: string]: Json }
export interface Span {
  id: string; parent_id: string|null; name: string; kind: Kind; actor: string; model: string;
  status: 'running'|'complete'|'fallback'|'error'; start_ms: number|null; duration_ms: number|null;
  inputs: Json; outputs: Json; error_type?: string;
}
export interface RunMeta {
  id: string; title: string; mode: 'live'|'fixture'|'replay'; started_at: string;
  status: 'running'|'complete'|'degraded'|'error'; duration_ms: number|null; revision: number;
}
export interface Run extends RunMeta { format: 'cofinance-trace/1'; spans: Span[]; summary?: Json; dropped_spans?: number; storage_errors?: number }
export interface Corpus { rows: number; window_days: number; collected_at: string; by_source: Record<string,number>; by_ticker: Record<string,number> }
export const kinds: Kind[] = ['run','route','agent','model','decision','tool','gate','handoff']
export function object(value: unknown): RecordData { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as RecordData : {} }
export const text = (value: unknown): string => typeof value === 'string' ? value : ''
export const numeric = (value: unknown): number|undefined => typeof value === 'number' && Number.isFinite(value) ? value : undefined
export const label = (value: unknown): string => text(value).replaceAll('_',' ') || 'Not recorded'
export const duration = (value: unknown): string => { const n=numeric(value); return n===undefined?'Not recorded':n>=1000?`${(n/1000).toFixed(2)} s`:`${n.toFixed(1)} ms` }
const statusNames: Record<string,string> = {complete:'Complete',degraded:'Fallback used',fallback:'Fallback',error:'Failed',running:'Running',queued:'Queued',cancelling:'Stopping',cancelled:'Stopped',interrupted:'Interrupted'}
export const statusLabel = (value: unknown): string => statusNames[text(value)] || label(value)
/** Compact relative time for lists: 5m, 3h, 2d, or a short date beyond a week. */
export function ago(value: string|number|null|undefined, now = Date.now()): string {
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value || '')
  const t = d.getTime(); if (Number.isNaN(t)) return ''
  const s = Math.max(0, (now - t) / 1000)
  if (s < 60) return 'now'; if (s < 3600) return Math.floor(s / 60) + 'm'; if (s < 86400) return Math.floor(s / 3600) + 'h'
  if (s < 7 * 86400) return Math.floor(s / 86400) + 'd'
  return d.toLocaleDateString(undefined, {month:'short', day:'numeric'})
}
export function date(value: string): string { const d=new Date(value); return Number.isNaN(d.getTime())?'Unknown time':d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) }
export function evidenceUrl(value: unknown): string|null {
  if(typeof value!=='string')return null
  try { const u=new URL(value); return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:null } catch {return null}
}
export function parseMeta(value: unknown): RunMeta {
  const r=object(value)
  if(!/^[0-9a-f]{32}$/.test(text(r.id)) || typeof r.title!=='string' || !['live','fixture','replay'].includes(text(r.mode)) ||
    !['running','complete','degraded','error'].includes(text(r.status)) || typeof r.started_at!=='string' || numeric(r.revision)===undefined ||
    (r.duration_ms!==null && numeric(r.duration_ms)===undefined))throw new Error('Local server returned an invalid run record.')
  return r as unknown as RunMeta
}
export function parseRun(value: unknown): Run {
  parseMeta(value); const r=object(value)
  if(r.format!=='cofinance-trace/1' || !Array.isArray(r.spans) || r.spans.length>2000)throw new Error('Unsupported trace format.')
  const ids=new Set<string>()
  for(const raw of r.spans) {
    const s=object(raw),id=text(s.id)
    if(!/^[0-9a-f]{32}$/.test(id)||ids.has(id)||typeof s.name!=='string'||!kinds.includes(text(s.kind) as Kind)||
      !['running','complete','fallback','error'].includes(text(s.status))||typeof s.actor!=='string'||typeof s.model!=='string'||
      (s.parent_id!==null&&!/^[0-9a-f]{32}$/.test(text(s.parent_id)))||
      [s.start_ms,s.duration_ms].some(n=>n!==null&&(numeric(n)===undefined||Number(n)<0)))throw new Error('Local server returned an invalid trace step.')
    ids.add(id)
  }
  return r as unknown as Run
}
export function parseRuns(value: unknown): RunMeta[] { if(!Array.isArray(value))throw new Error('Invalid local run list.');return value.map(parseMeta) }
export function parseCorpus(value: unknown): Corpus|null {
  if(value===null)return null
  const c=object(value)
  if(numeric(c.rows)===undefined || numeric(c.window_days)===undefined || typeof c.collected_at!=='string' ||
    [c.by_source,c.by_ticker].some(v=>!v||Array.isArray(v)||typeof v!=='object'||Object.values(v).some(n=>numeric(n)===undefined)))throw new Error('Invalid corpus summary.')
  return c as unknown as Corpus
}
export function flatten(spans: Span[]): (Span & {depth: number})[] {
  const ids=new Set(spans.map(s=>s.id)),children=new Map<string|null,Span[]>(),seen=new Set<string>(),out:(Span & {depth:number})[]=[]
  for(const s of spans){const p=ids.has(s.parent_id||'')?s.parent_id:null;children.set(p,[...(children.get(p)||[]),s])}
  function walk(parent:string|null,depth:number){for(const s of children.get(parent)||[]){if(seen.has(s.id))continue;seen.add(s.id);out.push({...s,depth});if(depth<30)walk(s.id,depth+1)}}
  walk(null,0);for(const s of spans)if(!seen.has(s.id))out.push({...s,depth:0});return out
}
const reasons: Record<string,string> = {
  below_digest_threshold:'Materiality stayed below the recorded digest line.',
  material_not_urgent:'Eligible for a digest, but did not meet both alert lines.',
  material_and_urgent:'Met both alert lines. In replay this is a diagnostic outcome, not a sent notification.',
  stale:'Skipped because the item exceeded the age limit.', duplicate:'Skipped because the item was already seen.',
  duplicate_title:'Skipped because the headline was already judged.',
  primary_only:'Only the primary judge ran. The cascade did not call an adjudicator.',
  adjudicated:'The adjudicator supplied the final gray-zone judgment.',
  adjudicator_down:'The adjudicator failed. Alert eligibility was capped; one judge cannot trigger an alert.',
  alert_agreement:'Both judges were required. The lower materiality and urgency scores were used.',
  judgment_budget:'The judgment budget was exhausted. Deferred items can be retried.',
}
export function reason(value: unknown): string {
  const key=text(value)
  if(key.startsWith('classifier_error:'))return 'Classification failed. Deferred, never promoted to an alert.'
  if(key.startsWith('alert_downgraded:'))return 'Python downgraded the alert: '+label(key.split(':')[1])+'.'
  return reasons[key]||''
}
export function modelGroups(spans:Span[]) {
  const groups=new Map<string,{model:string;actors:Set<string>;calls:number;time:number;errors:number}>()
  for(const s of spans.filter(s=>s.kind==='model')){
    const name=s.model||'Identity not supplied',g=groups.get(name)||{model:name,actors:new Set<string>(),calls:0,time:0,errors:0}
    g.actors.add(s.actor);g.calls++;g.time+=s.duration_ms||0;g.errors+=s.status==='error'?1:0;groups.set(name,g)
  }
  return [...groups.values()]
}
