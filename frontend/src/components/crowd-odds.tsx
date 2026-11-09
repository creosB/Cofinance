// Crowd odds: what Polymarket traders are paying for questions about the run's assets. Values are shown exactly as
// Python recorded them; the narrator never sees them. Read-only: Cofinance never trades or links to trading actions.
import { ArrowUpRight, Scale } from 'lucide-react'
import '../crowd-odds.css'
import type { CrowdMarket, CrowdOdds as Odds } from '@/lib/use-chat'

export const pct=(v:number)=>{const x=v*100;return (x>0&&x<1?x.toFixed(1):x>99&&x<100?x.toFixed(1):Math.round(x).toString())+'%'}
const money=(v:number)=>'$'+Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1}).format(v)
const ends=(iso:string|null)=>{const d=new Date(iso||'');return Number.isNaN(d.getTime())?'':'ends '+d.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'})}
const horizonWords:Record<string,string>={'1d':'a day','7d':'a week','30d':'a month'}
const safe=(url:string)=>/^https:\/\/polymarket\.com\/event\/[a-z0-9-]+$/.test(url)?url:''

export function historyLine(h:NonNullable<CrowdMarket['history']>){
 const range=h.ci95&&h.ci95.length===2?', likely range '+pct(h.ci95[0])+' to '+pct(h.ci95[1]):''
 return 'In the study, markets priced '+pct(h.lo)+' to '+pct(h.hi)+' about '+(horizonWords[h.horizon]||h.horizon)+' before closing went this way '+pct(h.resolved_rate)+' of the time ('+h.n+' markets'+range+').'
}

export function CrowdOdds({odds}:{odds:Odds}){
 if(!odds.markets.length){
  return odds.errors.length?<div className="coverage"><Scale size={14} aria-hidden="true"/><span>Crowd odds are unavailable right now. Research is unaffected.</span></div>:null
 }
 return <section className="crowd-odds" aria-label="Crowd odds from Polymarket">
  <div className="crowd-head"><Scale size={14} aria-hidden="true"/><strong>Crowd odds</strong><span>Polymarket, read-only</span></div>
  <ul>{odds.markets.map(m=>{const href=safe(m.url);return <li key={m.url+m.question}>
   <div className="crowd-row">
    <span className="crowd-q">{href?<a href={href} target="_blank" rel="noreferrer noopener">{m.question}<ArrowUpRight size={13} aria-hidden="true"/></a>:m.question}</span>
    <span className="crowd-p" aria-label={m.outcome+' '+pct(m.probability)}><span>{m.outcome}</span>{pct(m.probability)}</span>
   </div>
   <p className="crowd-meta">{[m.price_basis==='midpoint'?'bid/ask midpoint':m.price_basis,money(m.volume_24h)+' traded in 24h',ends(m.ends)].filter(Boolean).join(' · ')}</p>
   {m.history&&<p className="crowd-history">{historyLine(m.history)}</p>}
  </li>})}</ul>
  <p className="crowd-note">{odds.note}</p>
 </section>
}
