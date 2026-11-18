import '../token-usage.css'

type Counts={input:number;output:number;total:number;attempts:number;reported:number}
type Role=Counts&{actor:string;model:string}
export type Usage=Counts&{version:1;scope:'chat_narrator';complete:boolean;roles:Role[]}
const count=(n:unknown):n is number=>typeof n==='number'&&Number.isSafeInteger(n)&&n>=0
const obj=(v:unknown):Record<string,unknown>=>v!==null&&typeof v==='object'&&!Array.isArray(v)?v as Record<string,unknown>:{}
const keys=['input','output','total','attempts','reported'] as const
const valid=(r:Record<string,unknown>)=>keys.every(k=>count(r[k]))&&Number(r.input)+Number(r.output)===r.total&&Number(r.reported)<=Number(r.attempts)
/** Unknown/old/corrupt usage never becomes a plausible-looking zero. Do not break the saved answer. */
export function parseUsage(value:unknown):Usage|null{
 const r=obj(value)
 if(r.version!==1||r.scope!=='chat_narrator'||typeof r.complete!=='boolean'||!valid(r)||!Array.isArray(r.roles)||r.roles.length>40)return null
 for(const value of r.roles){const row=obj(value);if(!valid(row)||typeof row.actor!=='string'||typeof row.model!=='string')return null}
 const roles=r.roles as Role[]
 if(keys.some(k=>roles.reduce((sum,row)=>sum+row[k],0)!==r[k])||(r.complete&&r.attempts!==r.reported))return null
 if(r.reported===0&&r.total!==0)return null
 return r as unknown as Usage
}
const fmt=(n:number)=>n.toLocaleString('en-US')
export function TokenUsage({usage,running=false}:{usage:unknown;running?:boolean}){
 const u=parseUsage(usage)
 if(!u)return <p className="token-usage token-unknown">{running?'Token usage appears after each narrator call.':'Token usage unavailable for this response.'}</p>
 if(u.attempts===0&&u.complete)return <p className="token-usage token-zero">{running?'No completed narrator calls yet.':'0 narrator tokens (no narrator call)'}</p>
 if(u.reported===0)return <p className="token-usage token-unknown">Token usage unavailable{running?' so far':''}. The runtime did not report counts.</p>
 return <details className="token-usage" aria-label="Narrator token usage">
  <summary><span className="token-summary"><span>{fmt(u.total)} {u.complete?'narrator tokens':'recorded tokens (partial)'}{running?' so far':''}</span><span>{fmt(u.input)} in / {fmt(u.output)} out</span></span></summary>
  <div className="token-detail">
   <p>Runtime-reported totals across this answer's narrator calls, including retries and rejected output. Input includes system prompts and supplied context; output includes text you may not see. Not context-window occupancy.</p>
   <dl>{u.roles.map((r,i)=><div key={r.actor+r.model+i}><dt>{r.actor}<small>{r.attempts} attempt{r.attempts===1?'':'s'}, {r.reported} reported</small></dt><dd>{r.reported?`${fmt(r.input)} in / ${fmt(r.output)} out`:'Unavailable'}</dd></div>)}</dl>
   {!u.complete&&<p>Incomplete: {u.reported} of {u.attempts} attempts reported usage, or the trace omitted steps. These are known counts, not the full total.</p>}
   <p>Excludes GLiNER routing, memory embeddings and separate news collection. Counts update after a call finishes, not token by token.</p>
  </div>
 </details>
}
