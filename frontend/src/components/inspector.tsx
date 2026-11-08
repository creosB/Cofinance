import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, ChevronDown, Check, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StepExplanation } from './step-explanation'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { duration, evidenceUrl, label, numeric, object, reason, statusLabel, text, type Json, type Span } from '@/lib/domain'

export function StateBadge({value}:{value:string}){
  return <Badge variant="outline" className={'state-badge state-'+value}>{statusLabel(value)}</Badge>
}
function Payload({name,value,initial=false}:{name:string;value:Json|undefined;initial?:boolean}){
  const [open,setOpen]=useState(initial)
  return <Collapsible open={open} onOpenChange={setOpen} className="payload">
    <CollapsibleTrigger asChild><Button variant="ghost" className="payload-trigger">{name}<ChevronDown size={16} aria-hidden="true" className={open?'rotated':''}/></Button></CollapsibleTrigger>
    <CollapsibleContent><pre tabIndex={0} aria-label={name} className="code-block">{JSON.stringify(value,null,2)??'Not recorded'}</pre></CollapsibleContent>
  </Collapsible>
}
export function Decision({span}:{span:Span}){
  const out=object(span.outputs),input=object(span.inputs),j=object(out.judgment??(typeof out.material==='number'?out:null)),policy=object(out.thresholds)
  const why=text(out.reason||out.route),headline=text(out.title||input.title),link=evidenceUrl(out.url||input.url)
  const pass=typeof out.passed==='boolean'?out.passed:typeof out.faithful_synthesis==='boolean'?out.faithful_synthesis:undefined
  const rows:[string,unknown,string,unknown][]=[]
  if(numeric(j.material)!==undefined)rows.push(['Materiality',j.material,'Digest ≥',policy.digest_p])
  if(numeric(j.urgent)!==undefined)rows.push(['Urgency',j.urgent,'Alert ≥',policy.alert_urgent_p])
  if(numeric(policy.alert_p)!==undefined)rows.push(['Alert materiality',j.material,'Alert ≥',policy.alert_p])
  const number=(v:unknown)=>numeric(v)?.toFixed(4)??'Not recorded'
  if(!headline&&!why&&!rows.length&&pass===undefined)return null
  return <section className="decision" aria-label="Decision explanation">
    {out.action&&<div className="decision-action"><strong>{text(out.action).toUpperCase()}</strong><span>{text(out.ticker)}</span></div>}
    {headline&&<p className="headline-evidence">{headline}</p>}
    {why&&<><h3>{label(why)}</h3>{reason(why)&&<p className="muted">{reason(why)}</p>}</>}
    {pass!==undefined&&<div className="faithfulness"><h3>{pass?<Check size={16}/>:<X size={16}/>}Number check {pass?'passed':'failed'}</h3><p className="muted">{pass?'Recorded numbers passed the check. Other factual claims are not certified.':'The recorded number check failed. Inspect the accepted fallback output.'}</p></div>}
    {rows.length>0&&<><table className="score-table" aria-label="Recorded decision scores"><thead><tr><th>Check</th><th>Score</th><th>Policy line</th></tr></thead><tbody>{rows.map(([name,score,comparison,line])=><tr key={name}><td>{name}</td><td>{number(score)}</td><td>{numeric(line)===undefined?'Not recorded':comparison+' '+number(line)}</td></tr>)}</tbody></table><p className="quiet">Model scores are not calibrated probabilities.</p></>}
    {j.direction&&<div className="property"><span>Direction</span><strong>{text(j.direction)}</strong></div>}
    {link&&<a href={link} target="_blank" rel="noreferrer noopener" className="source-link">Open source evidence<ArrowUpRight size={15}/></a>}
  </section>
}
export function Inspector({span,parent,close,spans=[]}:{span:Span|null;parent?:Span;close:()=>void;spans?:Span[]}){
  const ref=useRef<HTMLElement>(null)
  useEffect(()=>{if(span&&matchMedia('(max-width: 900px)').matches)ref.current?.scrollIntoView({block:'start',behavior:'instant'})},[span?.id])
  return <aside ref={ref} className="inspector" aria-label="Step inspector">
    {!span?<div className="inspector-empty"><div className="inspect-symbol" aria-hidden="true">↗</div><h2>Follow the evidence</h2><p>Select any step to inspect its model, inputs, output, and recorded decision.</p><span className="quiet">J / K to move between steps</span></div>:<div key={span.id}>
      <div className="inspector-heading"><div><span className="section-label">{label(span.kind)} inspector</span><h2>{span.name}</h2></div><Button variant="ghost" size="icon" aria-label="Close step inspector" onClick={close}><X size={16}/></Button></div>
      <div className="inspector-content">
        <dl className="properties"><div><dt>Actor</dt><dd>{span.actor}</dd></div><div><dt>Model</dt><dd className="mono">{span.model||'Not a model call'}</dd></div><div><dt>State</dt><dd><StateBadge value={span.status}/></dd></div><div><dt>Duration</dt><dd className="mono">{duration(span.duration_ms)}</dd></div><div><dt>Started</dt><dd className="mono">+{duration(span.start_ms)}</dd></div>{parent&&<div><dt>Parent</dt><dd>{parent.name}</dd></div>}{span.error_type&&<div><dt>Error</dt><dd>{span.error_type}</dd></div>}</dl>
        <StepExplanation span={span} spans={spans}/>
        <Decision span={span}/>
        <Payload name="Observed output" value={span.outputs}/><Payload name="Input / policy" value={span.inputs}/>
        <p className="privacy-note">Bounded diagnostic excerpts. Secret-key fields and tagged private thinking are omitted. Not a comprehensive data-loss filter.</p>
      </div>
    </div>}
  </aside>
}
