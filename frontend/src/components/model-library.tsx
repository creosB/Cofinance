import { Download, Layers, ShieldCheck, Play, Pause, Square, Check, FlaskConical } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { ChatController, ModelState, NarratorState } from '@/lib/use-chat'
import { NarratorComparison } from './narrator-comparison'

const bytes=(n:number)=>n>=1024**3?(n/1024**3).toFixed(2)+' GiB':(n/1024**2).toFixed(1)+' MiB'
const working=(m:ModelState)=>['downloading','verifying','loading','qualifying'].includes(m.phase)
// Older servers sent one narrator without a list; keep that working.
export const narratorsOf=(m?:ModelState|null):NarratorState[]=>m?.narrators?.length?m.narrators:m?[{id:m.id||'lfm3-1.7b-q4km',name:m.name||'LFM3 1.7B',publisher:'Liquid AI',quantization:m.quantization||'Q4_K_M',size_bytes:m.size_bytes,revision:m.revision,source:m.source,note:'',downloaded:m.phase!=='not_downloaded',qualified:m.can_select,loaded:m.loaded,active:true}]:[]
// Only a narrator that passed its own qualification can be chosen; the server enforces the same rule.
export function narratorOptions(m:ModelState|null|undefined,compact:boolean){
 return narratorsOf(m).map(n=>({id:n.id,disabled:!n.qualified,label:compact?n.name+(!n.qualified?' (qualify first)':n.loaded?' (loaded)':''):n.name+' / '+n.quantization+(!n.qualified?' (qualification required)':'')}))
}
export function ModelPicker({chat,compact=false}:{chat:ChatController;compact?:boolean}){
 const m=chat.workspace?.models
 const choose=(model:string)=>{void chat.post('/api/models/select',{model}).catch(()=>{})}
 const items=narratorOptions(m,compact).map(o=><SelectItem key={o.id} value={o.id} disabled={o.disabled}>{o.label}</SelectItem>)
 if(compact)return <div className="model-picker-compact" title={m?.selection_note||undefined}><Select value={m?.selected||'facts'} disabled={!m||chat.busy||!!chat.workspace?.active_job||working(m)} onValueChange={choose}><SelectTrigger aria-label="Choose narrator" size="sm"><Layers size={14} aria-hidden="true"/><SelectValue/></SelectTrigger><SelectContent><SelectItem value="facts">Tool-only</SelectItem>{items}</SelectContent></Select></div>
 return <div className="model-picker"><label>Narrator<Select value={m?.selected||'facts'} disabled={!m||chat.busy||!!chat.workspace?.active_job||working(m)} onValueChange={choose}><SelectTrigger aria-label="Choose narrator"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="facts">Tool-only reports</SelectItem>{items}</SelectContent></Select></label><span className="model-picker-status">{m?.selection_note||(m?.selected==='facts'?'No generative narrator':m?.loaded?'Loaded locally':'Loads on first run')}</span></div>
}
const status=(n:NarratorState)=>n.loaded?'Loaded':!n.downloaded?'Not downloaded':!n.qualified?'Needs qualification':n.active?'Selected':'Qualified'
// One row per pinned narrator. A narrator that is not the active one can be downloaded or qualified here, so it can be
// chosen afterwards; load and unload stay with the active narrator below.
export function NarratorRows({narrators,locked,act}:{narrators:NarratorState[];locked:boolean;act:(action:string,model:string)=>void}){
 return <>{narrators.map(n=><div key={n.id} className="model-library-row"><Layers size={19}/><div className="model-row-copy"><h3>{n.name} <span className="quiet">{n.publisher} / {n.quantization}</span></h3><p>{n.note||'Data Analyst and Market Team Lead narrator, headline judge'}</p><span className="quiet">{bytes(n.size_bytes||1)}{n.default===undefined?'':n.default?' / default':' / optional'}{n.active?' / active narrator':''}</span>
  {!n.active&&(!n.downloaded||!n.qualified)&&<div className="model-actions">{!n.downloaded?<Button variant="outline" disabled={locked} onClick={()=>act('download',n.id)}><Download size={15}/>Download {n.name}</Button>:<Button variant="outline" disabled={locked} onClick={()=>act('qualify',n.id)}><FlaskConical size={15}/>Verify & test {n.name}</Button>}</div>}</div>
  <Badge variant="outline" className={n.loaded?'state-complete':''}>{status(n)}</Badge></div>)}</>
}
export function ModelLibrary({chat}:{chat:ChatController}){
 const m=chat.workspace?.models
 if(!m)return <div role="status" className="mode-notice">Connecting to the local model manager...</div>
 const action=(action:string)=>{void chat.post('/api/models/action',{action}).catch(()=>{})}
 const forNarrator=(action:string,model:string)=>{void chat.post('/api/models/action',{action,model}).catch(()=>{})}
 const locked=chat.busy||!!chat.workspace?.active_job||working(m)
 const percent=m.total_bytes>0?Math.max(0,Math.min(100,m.downloaded_bytes/m.total_bytes*100)):0
 return <section className="model-library" aria-label="Local model library"><div className="section-title"><div><h2>Local model library</h2><p className="muted">Routing and narration have different jobs. No hosted inference.</p></div><ModelPicker chat={chat}/></div>
  {chat.error&&<div className="error-banner" role="alert"><span>{chat.error}</span><Button variant="outline" onClick={chat.refresh}>Refresh</Button></div>}
  <div className="model-library-row"><ShieldCheck size={19}/><div className="model-row-copy"><h3>GLiNER2.5-Decide</h3><p>Routing executive and signal classifier</p><span className="quiet">Pinned cached revision. Thresholds remain uncalibrated.</span></div><Badge variant="outline">{m.router.replaceAll('_',' ')}</Badge></div>
  <NarratorRows narrators={narratorsOf(m)} locked={locked} act={forNarrator}/>
  <div className="model-download"><div className="download-description" role="status" aria-live="polite"><strong>{m.stage||'Not downloaded'}</strong><span className="quiet">Active narrator: {m.name} / {m.quantization} / {m.runtime}</span>{(m.phase==='downloading'||m.phase==='paused')&&<span className="mono">{bytes(m.downloaded_bytes)} / {bytes(m.total_bytes)}{m.speed_bps>0?' / '+bytes(m.speed_bps)+'/s':''}</span>}</div>
   {(m.phase==='downloading'||m.phase==='paused')&&<progress aria-label="Model and runtime download" value={m.downloaded_bytes} max={m.total_bytes}><span>{percent.toFixed(1)}%</span></progress>}
   {m.error&&<p className="error-text" role="alert">{m.error}</p>}
   <div className="model-actions">
    {['not_downloaded','paused','error'].includes(m.phase)&&<Button variant="outline" disabled={chat.busy||!!chat.workspace?.active_job} onClick={()=>action('download')}><Download size={15}/>{m.phase==='paused'?'Resume download':m.phase==='error'?'Retry download':'Download '+(m.name||'model')}</Button>}
    {working(m)?<Button variant="outline" disabled={chat.busy||m.phase!=='downloading'} onClick={()=>action('pause')}><Pause size={15}/>Pause download</Button>:<>
     {!['not_downloaded','paused'].includes(m.phase)&&<Button variant="outline" disabled={locked} onClick={()=>action('qualify')}><FlaskConical size={15}/>Verify & test</Button>}
     {m.can_select&&!m.loaded&&<Button variant="outline" disabled={locked} onClick={()=>action('load')}><Play size={15}/>Load narrator</Button>}
     {m.loaded&&<Button variant="outline" disabled={locked} onClick={()=>action('unload')}><Square size={15}/>Unload narrator</Button>}
    </>}
   </div>
   {m.qualification&&<div className="qualification-summary"><strong>{m.qualification.passed?<Check size={15}/>:<FlaskConical size={15}/>}Smoke qualification {m.qualification.passed?'passed':'failed'}</strong><span>{m.qualification.n} synthetic cases. Not a finance-accuracy benchmark.</span><details><summary>Gate results and provenance</summary><ul>{Object.entries(m.qualification.gates).map(([name,g])=><li key={name}>{name.replaceAll('_',' ')}: {g.passed?'passed':'failed'} ({g.got===null?'not measured':Math.round(g.got*100)+'%'}; required {Math.round(g.required*100)}%)</li>)}</ul><p>Model revision: <code>{m.revision}</code></p><p>License: {m.license}. {m.license_note}</p><a href={m.source} target="_blank" rel="noreferrer noopener">Official model source</a></details></div>}
  </div>
  <NarratorComparison/>
 </section>
}
