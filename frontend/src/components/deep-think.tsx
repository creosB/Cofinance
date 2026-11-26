// Deep think (Baris chose "build" on 2026-11-19). Off by default. Only the Market Team Lead's final answer thinks; it is
// checked by the same guards, and the thinking text is never shown or saved. Headline judging never thinks.
import { Brain, Check, FlaskConical, Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { ChatController, ModelState } from '@/lib/use-chat'

export interface ThinkState {budget:number;qualified:boolean;loaded:boolean;report:null|{passed:boolean|null;n?:number|null;gates?:Record<string,{passed:boolean;got:number|null;required:number}>|null;backend?:string|null;evaluated_at?:number|null;budget?:number|null;error?:string|null}}
export interface DeepThinkUse {requested:boolean;used:boolean;note?:string}
export const thinkOf=(m:ModelState|null|undefined):ThinkState|undefined=>(m as (ModelState&{think?:ThinkState})|null|undefined)?.think
const KEY='cofinance.deepthink'
export const savedDeepThink=()=>{try{return localStorage.getItem(KEY)==='on'}catch{return false}}
export const saveDeepThink=(on:boolean)=>{try{localStorage.setItem(KEY,on?'on':'off')}catch{}}

export function DeepThinkToggle({chat,on,onChange,disabled,onOpenModels}:{chat:ChatController;on:boolean;onChange:(on:boolean)=>void;disabled:boolean;onOpenModels?:()=>void}){
 const m=chat.workspace?.models,t=thinkOf(m)
 if(!m||m.selected==='facts'||!t)return null
 const ready=t.qualified,active=on&&ready
 const tip=!ready?'Test deep think in Models first.':active?'On: the narrator thinks before the final answer. About 2.5 s slower per answer.':'Off. Turn on to let the narrator think before the final answer (about 2.5 s slower).'
 return <span className="deep-think-control"><Tooltip><TooltipTrigger asChild><span><Button type="button" variant="outline" size="sm" className="deep-think-toggle" data-on={active||undefined} data-locked={!ready||undefined} aria-pressed={active} disabled={disabled||!ready} onClick={()=>onChange(!on)}>
  {ready?<Brain size={14} aria-hidden="true"/>:<Lock size={13} aria-hidden="true"/>}Deep think{active&&<span className="deep-think-chip">On</span>}</Button></span></TooltipTrigger><TooltipContent>{tip}</TooltipContent></Tooltip>{!ready&&onOpenModels&&<Button type="button" variant="ghost" size="sm" className="deep-think-setup" disabled={disabled} onClick={onOpenModels}>Test in Models</Button>}</span>
}

export function DeepThinkNote({use}:{use?:DeepThinkUse|null}){
 if(!use?.requested)return null
 return use.used?<span className="answer-kind deep-think-label deep-think-used"><Brain size={12} aria-hidden="true"/>Deep think</span>
  :<span className="quiet deep-think-label">{use.note||'Deep think was requested but not used.'}</span>
}

export function DeepThinkPanel({chat,locked}:{chat:ChatController;locked:boolean}){
 const m=chat.workspace?.models,t=thinkOf(m)
 if(!m||!t)return null
 const r=t.report,status=t.qualified?'Passed the smoke gates'+(r?.backend?' on '+(r.backend==='vulkan'?'Vulkan GPU':'CPU'):''):r?(r.error?'Could not run: '+r.error:'Did not pass, so it stays off'):'Not tested yet'
 return <div id="deep-think-panel" className="qualification-summary deep-think-panel" aria-label="Deep think">
  <strong>{t.qualified?<Check size={15}/>:<Brain size={15}/>}Deep think (optional): {status}</strong>
  <span>Lets the narrator think up to {t.budget} tokens before the final chat answer. In the lab it took about 2.5 s instead of 0.4 s per answer. The answer goes through the same checks; the thinking text is never shown or saved. Headline judging never thinks.</span>
  {r?.gates&&<details><summary>Gate results</summary><ul>{Object.entries(r.gates).map(([name,g])=><li key={name}>{name.replaceAll('_',' ')}: {g.passed?'passed':'failed'} ({g.got===null?'not measured':Math.round(g.got*100)+'%'}; required {Math.round(g.required*100)}%)</li>)}</ul></details>}
  <div className="model-actions"><Button variant="outline" disabled={locked||!m.can_select} onClick={()=>{void chat.post('/api/models/action',{action:'qualify_thinking',model:m.id}).catch(()=>{})}}><FlaskConical size={15}/>{t.qualified||r?'Test deep think again':'Test deep think'}</Button></div>
 </div>
}
