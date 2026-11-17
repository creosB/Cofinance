// Headline signal (headline-v1, experimental): the local model judged each served headline on its own, and Python
// added the judgments up over exactly the headlines this run was served. Shown as Python computed it; the narrator
// never sees it. Read-only, no actions.
import { Newspaper } from 'lucide-react'
import '../crowd-odds.css'
import type { HeadlineAsset, HeadlineSignal as Signal } from '@/lib/use-chat'

const lean:Record<string,string>={UP:'Leans up',DOWN:'Leans down',ABSTAIN:'No clear lean'}
export const signed=(v:number)=>(v>0?'+':v<0?'-':'')+Math.abs(v).toFixed(2)
export function countLine(a:Pick<HeadlineAsset,'served'|'judged'>,line:number){
 const n=a.judged===a.served?'Sum of '+a.served+' headline '+(a.served===1?'judgment':'judgments'):'Sum of '+a.judged+' judged of '+a.served+' headlines'
 return n+'. A lean needs '+line+' or more either way.'
}

export function HeadlineSignal({signal}:{signal:Signal}){
 const assets=signal.assets.filter(a=>a.judged>0)
 if(!assets.length)return null
 return <section className="crowd-odds" aria-label="Experimental headline signal">
  <div className="crowd-head"><Newspaper size={14} aria-hidden="true"/><strong>Headline signal</strong><span>Experimental</span></div>
  <ul>{assets.map(a=><li key={a.ticker}>
   <div className="crowd-row">
    <span className="crowd-q">{a.ticker}</span>
    <span className="crowd-p" aria-label={a.ticker+': '+(lean[a.call]||a.call)+', score '+signed(a.score)}><span>{lean[a.call]||a.call}</span>{signed(a.score)}</span>
   </div>
   <p className="crowd-meta">{countLine(a,signal.call_line)}</p>
  </li>)}</ul>
  <p className="crowd-note">{signal.note}</p>
 </section>
}
