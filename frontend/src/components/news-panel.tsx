// Clock 1 status. Collection is a manual action over registered official feeds; no schedule runs.
import { ArrowUpRight, LoaderCircle, Newspaper, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ago, date } from '@/lib/domain'
import type { ChatController } from '@/lib/use-chat'

export function newsLabel(chat:ChatController){
 const n=chat.workspace?.news
 if(!n)return ''
 if(n.phase==='running')return 'Collecting news'
 if(n.phase==='error')return 'News collection failed'
 if(!n.health.collected)return 'News not collected'
 const a=ago(n.health.last_run?.started_at||'')
 return a==='now'?'News just now':/^\d/.test(a)?'News '+a+' ago':'News '+a
}

export function NewsStatus({chat,open}:{chat:ChatController;open:()=>void}){
 const n=chat.workspace?.news
 if(!n)return null
 return <Button variant="ghost" className={'side-row news-status-row'+(n.phase==='error'?' side-status-error':'')} onClick={open}>{n.phase==='running'?<LoaderCircle size={14} className="animate-spin" aria-hidden="true"/>:<Newspaper size={14} aria-hidden="true"/>}<span className="side-row-title">{newsLabel(chat)}</span></Button>
}

export function NewsPanel({chat,inspect}:{chat:ChatController;inspect:(id:string)=>void}){
 const n=chat.workspace?.news
 if(!n)return <section className="news-panel" aria-label="News store"><h2>News store</h2><p className="muted" role="status">Connecting to the local server...</p></section>
 const h=n.health,run=h.last_run,running=n.phase==='running'
 const summary=h.collected&&run?.started_at?`Last collected ${date(run.started_at)}. ${run.fetched} items read, ${run.quarantined} quarantined, ${run.duplicates} repeated headlines skipped.`:'Not collected yet.'
 return <section className="news-panel" aria-label="News store">
  <div className="news-head"><div><h2>News store</h2><p>{summary} Runs only when you start it.</p></div>
   <Button variant="outline" disabled={running||chat.busy||!!chat.workspace?.active_job} onClick={()=>{void chat.collectNews().catch(()=>{})}}>{running?<LoaderCircle size={15} className="animate-spin"/>:<RefreshCw size={15}/>}{running?'Collecting':'Collect now'}</Button></div>
  {running&&<div className="news-progress" role="status"><span>{n.stage||'Starting collection'}</span>{n.run_id&&<Button variant="ghost" size="sm" onClick={()=>inspect(n.run_id!)}>Follow execution<ArrowUpRight size={14}/></Button>}</div>}
  {n.phase==='error'&&<p className="error-text news-error" role="alert">{n.error||'Collection failed.'}</p>}
  {h.error&&<p className="error-text news-error" role="alert">{h.error}</p>}
  {!running&&n.run_id&&<Button variant="ghost" size="sm" className="news-last-run" onClick={()=>inspect(n.run_id!)}>Inspect last collection<ArrowUpRight size={14}/></Button>}
  <div className="table-scroll"><table className="news-table"><thead><tr><th>Asset</th><th>Source</th><th>Digest</th><th>Alert</th><th>Newest stored item</th><th>Last fetch</th></tr></thead><tbody>
   {h.tickers.map(t=>{const sources=h.sources.filter(s=>s.ticker===t.ticker),status=run?.source_status?.[t.ticker];return <tr key={t.ticker}><td className="mono">{t.ticker}</td><td>{sources.map(s=>s.label).join(', ')||'None'}{sources.length>0&&<small>{sources.map(s=>s.timestamp_kind).join(', ')}</small>}</td><td className="mono">{t.digest}</td><td className="mono">{t.alert}</td><td>{t.newest_item?date(t.newest_item):'None'}</td><td>{status?Object.values(status).join('; '):'Not fetched'}</td></tr>})}
   {h.uncovered.map(t=><tr key={'uncovered-'+t}><td className="mono">{t}</td><td colSpan={5} className="muted">No approved news source for this tracked asset.</td></tr>)}
  </tbody></table></div>
  <p className="section-foot">Stored alerts are rows, not notifications. Company announcements are issuer claims. Digest and alert lines are uncalibrated.</p>
 </section>
}
