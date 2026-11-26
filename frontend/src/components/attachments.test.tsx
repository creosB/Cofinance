import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AttachmentChips,DocumentSources,groupDocumentSources } from './attachments'
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
 it('groups interleaved multi-notice pages under their source file in ascending page order',()=>{
  const grouped=groupDocumentSources([
   {document_id:'a',name:'Circular_47.pdf',page:3,matched:true},
   {document_id:'b',name:'Circular_48.pdf',page:1,matched:true},
   {document_id:'a',name:'Circular_47.pdf',page:1,matched:true},
   {document_id:'a',name:'Circular_47.pdf',page:3,matched:true},
  ])
  expect(grouped.map(g=>({name:g.name,pages:g.pages}))).toEqual([
   {name:'Circular_47.pdf',pages:[1,3]},
   {name:'Circular_48.pdf',pages:[1]},
  ])
  const html=renderToStaticMarkup(<DocumentSources sources={[
   {document_id:'a',name:'Circular_47.pdf',page:3,matched:true},
   {document_id:'b',name:'Circular_48.pdf',page:1,matched:true},
   {document_id:'a',name:'Circular_47.pdf',page:1,matched:true},
  ]}/>)
  expect(html).toContain('data-document-group="Circular_47.pdf"')
  expect(html).toContain('pages 1, 3')
  expect(html).toContain('data-document-group="Circular_48.pdf"')
  expect(html).toContain('page 1')
  expect(html.indexOf('Circular_47.pdf')).toBeLessThan(html.indexOf('Circular_48.pdf'))
 })
})
