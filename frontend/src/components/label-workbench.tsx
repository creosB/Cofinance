// Blind human labelling for the qualification corpus. One headline at a time, Linear-style list + detail.
// No model scores or gate outcomes are fetched or shown here, so labels are not anchored on the model.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, Check, CircleDashed } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { evidenceUrl, label as words } from '@/lib/domain'
import type { ChatController } from '@/lib/use-chat'

export interface LabelValues {material:string;urgent:string;direction:string;trap:string;event_group:string;notes:string}
export interface LabelItem {id:string;ticker:string;title:string;text:string;url:string;timestamp_kind:string;timestamp:string;source:string;label:LabelValues;complete:boolean}
export interface LabelState {corpus:string;total:number;labelled:number;target:number;trap_target:number;traps:number;material:number;urgent:number;splits:{train:number;heldout:number};issues:string[];event_groups:string[];items:LabelItem[];warning:string;provenance?:{labeler:string|null;model:string|null;human_review:boolean|null;labelled_at:string|null}}

export const DIRECTIONS=['bullish','neutral','bearish','unclear'] as const
export const TRAPS:[string,string][]=[['none','Not a trap'],['denial','Denies or rebuts a claim'],['speculation','Rumour or speculation'],['retrospective','Looks back at old news'],['peer_mention','Names the company, about someone else'],['injection','Contains instructions aimed at a model']]

/** Suggested event group: ticker plus the first title words. Only stored when the user saves. */
export function suggestGroup(item:Pick<LabelItem,'ticker'|'title'>){
 const slug=item.title.toLowerCase().replace(/[^a-z0-9]+/g,'-').split('-').filter(w=>w.length>2&&!['the','and','for','with','its','new'].includes(w)).slice(0,4).join('-')
 return (item.ticker.toLowerCase()+'-'+(slug||'event')).slice(0,100)
}
/** Mirrors the server rules so the Save button explains itself before a round trip. */
export function labelProblem(v:LabelValues){
 if(!['true','false'].includes(v.material))return 'Choose whether it is material.'
 if(!['true','false'].includes(v.urgent))return 'Choose whether it is urgent.'
 if(v.urgent==='true'&&v.material!=='true')return 'Urgent requires material.'
 if(!(DIRECTIONS as readonly string[]).includes(v.direction))return 'Choose a direction.'
 if(!TRAPS.some(([t])=>t===v.trap))return 'Choose a trap type, or Not a trap.'
 if(!/^[A-Za-z0-9_.:-]{1,100}$/.test(v.event_group))return 'Event group: letters, digits, . _ : - only.'
 return ''
}
const cap=(s:string)=>s?s[0].toUpperCase()+s.slice(1):s
const stamp=(kind:string)=>kind==='updated'?'Feed update time, publication unknown':kind==='submitted'?'HN submission time':kind==='published'?'Publication time':words(kind)

function Segmented({name,value,options,onChange,disabled}:{name:string;value:string;options:[string,string][];onChange:(v:string)=>void;disabled?:boolean}){
 return <div className="label-field"><span className="label-name">{name}</span><div className="segmented" role="group" aria-label={name}>{options.map(([v,text])=><Button key={v} type="button" variant="outline" size="sm" disabled={disabled} aria-pressed={value===v} className={value===v?'seg-on':''} onClick={()=>onChange(v)}>{text}</Button>)}</div></div>
}

export function LabelWorkbench({chat}:{chat:ChatController}){
 const [state,setState]=useState<LabelState|null>(null),[error,setError]=useState(''),[selected,setSelected]=useState(''),[filter,setFilter]=useState<'todo'|'done'|'all'>('todo')
 const [form,setForm]=useState<LabelValues|null>(null),[saving,setSaving]=useState(false)
 const load=useCallback(async()=>{try{const r=await fetch('/api/labels',{cache:'no-store',credentials:'omit'});const v=await r.json();if(!r.ok)throw Error(v.error||'Labels unavailable.');setState(v as LabelState);setError('')}catch(e){setError(e instanceof Error?e.message:'Labels unavailable.')}},[])
 useEffect(()=>{void load()},[load])
 const list=useMemo(()=>(state?.items||[]).filter(i=>filter==='all'||(filter==='done')===i.complete),[state,filter])
 const item=state?.items.find(i=>i.id===selected)||list[0]||null
 useEffect(()=>{if(item)setForm({...item.label,event_group:item.label.event_group||suggestGroup(item),trap:item.label.trap||'none'})},[item?.id])
 const problem=form?labelProblem(form):'Choose a headline.'
 const save=useCallback(async()=>{
  if(!item||!form||problem||saving)return
  setSaving(true)
  try{
   // A row saved by a person is a human-reviewed label, whatever its earlier provenance.
   const notes='[human reviewed] '+form.notes.replace(/^\[(model label|human reviewed)\]\s*/,'')
   const next=await chat.post('/api/labels',{id:item.id,...form,notes:notes.trim(),material:form.material==='true',urgent:form.urgent==='true'}) as LabelState
   setState(next);setError('')
   const after=next.items.find(i=>!i.complete&&i.id!==item.id);if(after&&filter!=='done')setSelected(after.id)
  }catch(e){setError(e instanceof Error?e.message:'Could not save this label.')}
  finally{setSaving(false)}
 },[item,form,problem,saving,chat,filter])
 useEffect(()=>{
  const key=(ev:KeyboardEvent)=>{
   const t=ev.target as HTMLElement
   if(t.isContentEditable||t.closest('input,textarea,select,[role="combobox"]')||ev.ctrlKey||ev.metaKey||ev.altKey)return
   if((ev.key==='j'||ev.key==='k')&&list.length){ev.preventDefault();const i=Math.max(0,list.findIndex(x=>x.id===item?.id));setSelected(list[Math.max(0,Math.min(list.length-1,i+(ev.key==='j'?1:-1)))].id)}
   if(ev.key==='Enter'&&!t.closest('button')){ev.preventDefault();void save()}
  }
  window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key)
 },[list,item,save])
 if(error&&!state)return <section className="dataset-section"><h2>Labels</h2><p className="error-text">{error}</p><Button variant="outline" onClick={()=>void load()}>Retry</Button></section>
 if(!state)return <section className="dataset-section"><h2>Labels</h2><p className="muted">Loading the local corpus.</p></section>
 const set=(k:keyof LabelValues)=>(v:string)=>setForm(f=>f?{...f,[k]:v,...(k==='material'&&v==='false'?{urgent:'false'}:{})}:f)
 const href=item?evidenceUrl(item.url):''
 return <section className="dataset-section label-workbench" aria-label="Human labels">
  <div className="section-title"><h2>Human labels</h2><span className="muted">Blind: no model scores are shown while you label.</span></div>
  <div className="label-progress"><span><strong>{state.labelled}</strong> / {state.target} labelled</span><span><strong>{state.traps}</strong> / {state.trap_target} traps</span><span>{state.splits.train} train, {state.splits.heldout} held-out</span><span className="muted">{state.total} candidates in corpus</span></div>
  {state.provenance?.labeler==='model'&&<div className="mode-notice" role="note"><p><strong>Model labels, not human-reviewed.</strong> Labelled blind by {state.provenance.model||'a model'}. Any row you save here is marked human-reviewed.</p></div>}
  {state.issues.length>0&&<p className="muted label-issues">Not ready to calibrate: {state.issues.join('; ')}.</p>}
  <div className="label-grid">
   <div className="label-list"><Tabs value={filter} onValueChange={v=>setFilter(v as typeof filter)}><TabsList aria-label="Filter headlines"><TabsTrigger value="todo">To label</TabsTrigger><TabsTrigger value="done">Labelled</TabsTrigger><TabsTrigger value="all">All</TabsTrigger></TabsList></Tabs>
    <div className="side-list">{list.map(i=><Button key={i.id} variant="ghost" title={i.title} aria-current={item?.id===i.id?'true':undefined} className={'side-row '+(item?.id===i.id?'side-row-active':'')} onClick={()=>setSelected(i.id)}>{i.complete?<Check size={14} className="accepted-icon" aria-label="Labelled"/>:<CircleDashed size={14} aria-label="Not labelled"/>}<span className="label-ticker mono">{i.ticker}</span><span className="side-row-title">{i.title}</span></Button>)}{!list.length&&<p className="sidebar-empty">{filter==='todo'?'Every candidate is labelled.':'Nothing here yet.'}</p>}</div></div>
   {item&&form?<form className="label-editor" onSubmit={e=>{e.preventDefault();void save()}}>
    <div className="label-evidence"><div className="msg-meta"><span className="mono">{item.ticker}</span><span>{words(item.source)}</span><span>{stamp(item.timestamp_kind)}: {item.timestamp.slice(0,10)}</span></div><h3>{item.title}</h3>{item.text&&<p>{item.text}</p>}{href&&<a href={href} target="_blank" rel="noreferrer noopener" className="quiet">Open source<ArrowUpRight size={14}/></a>}</div>
    <Segmented name="Material" value={form.material} options={[['true','Yes'],['false','No']]} onChange={set('material')}/>
    <Segmented name="Urgent" value={form.urgent} options={[['true','Yes'],['false','No']]} onChange={set('urgent')} disabled={form.material==='false'}/>
    <Segmented name="Direction" value={form.direction} options={DIRECTIONS.map(d=>[d,cap(words(d))] as [string,string])} onChange={set('direction')}/>
    <div className="label-field"><span className="label-name">Trap</span><div className="segmented segmented-wrap" role="group" aria-label="Trap">{TRAPS.map(([v,text])=><Button key={v} type="button" variant="outline" size="sm" title={text} aria-pressed={form.trap===v} className={form.trap===v?'seg-on':''} onClick={()=>set('trap')(v)}>{v==='none'?'None':cap(words(v))}</Button>)}</div></div>
    <label className="label-field"><span className="label-name">Event group</span><Input list="label-groups" value={form.event_group} onChange={e=>set('event_group')(e.target.value)} aria-describedby="event-help"/><datalist id="label-groups">{state.event_groups.map(g=><option key={g} value={g}/>)}</datalist><small id="event-help" className="muted">Same story, same group. Groups never split across train and held-out.</small></label>
    <label className="label-field"><span className="label-name">Notes</span><Textarea rows={2} maxLength={2000} value={form.notes} onChange={e=>set('notes')(e.target.value)}/></label>
    <div className="label-actions"><Button type="submit" disabled={!!problem||saving}>{saving?'Saving':'Save and next'}</Button><span className={problem?'muted':'quiet'}>{problem||'Enter saves. J and K move.'}</span>{error&&<span className="error-text">{error}</span>}</div>
   </form>:<p className="muted">No headline selected.</p>}
  </div>
 </section>
}
