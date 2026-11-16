/** Number citations chosen by Python (core/citations.py). This file only checks they still fit the answer text and
 * numbers them for display. A citation that does not fit is dropped, never repaired or guessed. */
export interface FactRef {kind:'fact';ticker:string;field:string;label:string;shown:string;source:string;as_of:string}
export interface EvidenceRef {kind:'evidence';index:number;title:string;source:string;as_of:string}
export type CiteRef=FactRef|EvidenceRef
export interface Citation {start:number;end:number;text:string;refs:CiteRef[]}
export interface Segment {text:string;n?:number;refs?:CiteRef[]}

const str=(v:unknown)=>typeof v==='string'
const whole=(v:unknown):v is number=>typeof v==='number'&&Number.isInteger(v)
function ref(v:unknown,evidenceCount:number):v is CiteRef{
 if(!v||typeof v!=='object')return false
 const r=v as Record<string,unknown>
 if(!str(r.source)||!str(r.as_of))return false
 if(r.kind==='fact')return str(r.ticker)&&str(r.field)&&str(r.label)&&str(r.shown)
 return r.kind==='evidence'&&whole(r.index)&&r.index>=0&&r.index<evidenceCount&&str(r.title)
}
function fits(v:unknown,content:string,evidenceCount:number):v is Citation{
 if(!v||typeof v!=='object')return false
 const c=v as Record<string,unknown>
 return whole(c.start)&&whole(c.end)&&c.start>=0&&c.start<c.end&&c.end<=content.length&&str(c.text)
  &&content.slice(c.start,c.end)===c.text&&Array.isArray(c.refs)&&c.refs.length>0&&c.refs.every(r=>ref(r,evidenceCount))
}
const key=(r:CiteRef)=>r.kind==='fact'?`f:${r.ticker}:${r.field}`:`e:${r.index}`

/** The answer as plain and cited pieces, in order; joined, the pieces are exactly the answer. The same source keeps
 * the same number; numbers follow first appearance. Overlapping citations keep the first. */
export function segments(content:string,citations:unknown,evidenceCount=0):Segment[]{
 const list=(Array.isArray(citations)?citations:[]).filter((c):c is Citation=>fits(c,content,evidenceCount)).sort((a,b)=>a.start-b.start)
 const out:Segment[]=[],numbers=new Map<string,number>();let at=0
 for(const c of list){
  if(c.start<at)continue
  if(c.start>at)out.push({text:content.slice(at,c.start)})
  const k=c.refs.map(key).join('|');if(!numbers.has(k))numbers.set(k,numbers.size+1)
  out.push({text:c.text,n:numbers.get(k),refs:c.refs});at=c.end
 }
 if(at<content.length||!out.length)out.push({text:content.slice(at)})
 return out
}

/** One line per source, for the chip's tooltip and accessible name. */
export function refText(r:CiteRef):string{
 const where=[r.source,r.as_of].filter(Boolean).join(', ')
 return (r.kind==='fact'?`${r.ticker} ${r.label}: ${r.shown}`:r.title)+(where?` (${where})`:'')
}

/** The element a chip jumps to: the asset's facts card, or the source row in the answer's sources list. */
export const targetId=(messageId:string,r:CiteRef)=>r.kind==='fact'?`facts-${messageId}-${r.ticker}`:`source-${messageId}-${r.index}`
