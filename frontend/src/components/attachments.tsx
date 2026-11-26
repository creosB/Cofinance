// Attached PDFs (2026-11-19): an attach button in the composer, one chip per file, and the quoted pages under an answer.
// The server parses the file and keeps only its page text; nothing here reads the PDF.
import { useRef, useState } from 'react'
import { FileText, LoaderCircle, Paperclip, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { PDF_LIMIT_COUNT } from '@/lib/use-chat'
import type { ChatController, DocumentMeta, DocumentSource } from '@/lib/use-chat'

const size=(bytes:number)=>bytes<1024*1024?Math.max(1,Math.round(bytes/1024))+' KB':(bytes/1024/1024).toFixed(1)+' MB'

export function AttachButton({chat,disabled}:{chat:ChatController;disabled:boolean}){
 const input=useRef<HTMLInputElement>(null),[reading,setReading]=useState(false)
 const count=chat.conversation?.documents?.length||0,full=count>=PDF_LIMIT_COUNT
 const pick=async(files:FileList|null)=>{
  const file=files?.[0];if(input.current)input.current.value='';if(!file)return
  setReading(true);try{await chat.attach(file)}catch{}finally{setReading(false)}
 }
 const label=full?`A conversation holds ${PDF_LIMIT_COUNT} PDFs. Remove one to attach another.`:'Attach a PDF (text only, up to 20 MB)'
 return <>
  <input ref={input} type="file" accept="application/pdf,.pdf" className="sr-only" tabIndex={-1} aria-hidden="true" onChange={e=>void pick(e.target.files)}/>
  <Tooltip><TooltipTrigger asChild><span><Button variant="ghost" size="icon" className="attach-button" aria-label="Attach PDF" disabled={disabled||full||reading||!chat.workspace} onClick={()=>input.current?.click()}>
   {reading?<LoaderCircle size={16} className="animate-spin" aria-hidden="true"/>:<Paperclip size={16} aria-hidden="true"/>}</Button></span></TooltipTrigger>
   <TooltipContent>{reading?'Reading the file':label}</TooltipContent></Tooltip>
 </>
}

export function AttachmentChips({chat,documents,disabled}:{chat:ChatController;documents:DocumentMeta[];disabled:boolean}){
 if(!documents.length)return null
 return <ul className="attachment-chips" aria-label="Attached PDFs">{documents.map(d=><li key={d.id} className="attachment-chip">
  <FileText size={14} aria-hidden="true"/><span className="attachment-name" title={d.name}>{d.name}</span>
  <span className="attachment-meta">{d.page_count} {d.page_count===1?'page':'pages'}, {size(d.bytes)}</span>
  <Button variant="ghost" size="icon" className="attachment-remove" aria-label={'Remove '+d.name} disabled={disabled} onClick={()=>{void chat.detach(d.id).catch(()=>{})}}><X size={14} aria-hidden="true"/></Button>
 </li>)}</ul>
}

export interface GroupedDocumentSource {
 key:string
 document_id:string
 name:string
 pages:number[]
 matched:boolean
}

export function groupDocumentSources(sources:DocumentSource[]):GroupedDocumentSource[]{
 const groups=new Map<string,GroupedDocumentSource>()
 for(const s of sources){
  const key=s.document_id||s.name
  const existing=groups.get(key)
  if(existing){
   if(!existing.pages.includes(s.page))existing.pages.push(s.page)
   existing.matched=existing.matched||s.matched
  }else{
   groups.set(key,{key,document_id:s.document_id,name:s.name,pages:[s.page],matched:s.matched})
  }
 }
 return Array.from(groups.values()).map(g=>({...g,pages:[...g.pages].sort((a,b)=>a-b)}))
}

export function DocumentSources({sources}:{sources?:DocumentSource[]}){
 if(!sources?.length)return null
 const unmatched=sources.every(s=>!s.matched)
 const grouped=groupDocumentSources(sources)
 return <div className="sources document-sources" aria-label="Pages used">
  {grouped.map(g=><p key={g.key} data-document-group={g.name}><FileText size={14} aria-hidden="true"/><span className="source-name">{g.name}</span><span className="source-meta">{g.pages.length===1?'page '+g.pages[0]:'pages '+g.pages.join(', ')}</span></p>)}
  {unmatched&&<p className="quiet">No page shared words with the question, so the first pages were used.</p>}
 </div>
}
