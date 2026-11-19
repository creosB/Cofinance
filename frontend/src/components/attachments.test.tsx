import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AttachmentChips,DocumentSources } from './attachments'
import type { ChatController,DocumentMeta } from '@/lib/use-chat'
const chat={detach:async()=>({})} as unknown as ChatController
const doc=(n:number,pages=3,bytes=2048):DocumentMeta=>({id:String(n).repeat(32).slice(0,32),name:`report-${n}.pdf`,bytes,page_count:pages,created:1})
describe('attached PDFs',()=>{
 it('lists each file with pages, size and a labelled remove button',()=>{
  const html=renderToStaticMarkup(<AttachmentChips chat={chat} documents={[doc(1),doc(2,1,3*1024*1024)]} disabled={false}/>)
  for(const text of ['report-1.pdf','3 pages, 2 KB','1 page, 3.0 MB','aria-label="Remove report-1.pdf"','aria-label="Attached PDFs"'])expect(html).toContain(text)
 })
 it('renders nothing without files and disables remove during a run',()=>{
  expect(renderToStaticMarkup(<AttachmentChips chat={chat} documents={[]} disabled={false}/>)).toBe('')
  expect(renderToStaticMarkup(<AttachmentChips chat={chat} documents={[doc(1)]} disabled/>)).toContain('disabled')
 })
 it('shows the pages an answer quoted, and says when none matched the question',()=>{
  const matched=renderToStaticMarkup(<DocumentSources sources={[{document_id:'a',name:'q3.pdf',page:2,matched:true}]}/>)
  expect(matched).toContain('q3.pdf');expect(matched).toContain('page 2');expect(matched).not.toContain('first pages')
  const unmatched=renderToStaticMarkup(<DocumentSources sources={[{document_id:'a',name:'q3.pdf',page:1,matched:false}]}/>)
  expect(unmatched).toContain('first pages were used')
  expect(renderToStaticMarkup(<DocumentSources sources={[]}/>)).toBe('')
 })
})
