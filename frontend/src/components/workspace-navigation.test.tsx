import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { WorkspaceNavigation,views,type View } from './workspace-navigation'
const render=(view:View)=>renderToStaticMarkup(<WorkspaceNavigation view={view} onView={()=>{}} onFind={()=>{}} onNew={()=>{}} onCollapse={()=>{}} busy={false} bottom={<span>Local news</span>}><div>Conversation history</div></WorkspaceNavigation>)
describe('chat-first workspace navigation',()=>{
 it('has one labelled New chat, with tools collapsed in ordinary chat',()=>{
  const html=render('chat')
  expect(html.match(/>New chat</g)?.length).toBe(1)
  expect(html).toContain('<details class="workspace-tools">')
  expect(html).not.toContain('<details class="workspace-tools" open')
  expect(html.indexOf('Conversation history')).toBeLessThan(html.indexOf('Workspace tools'))
  expect(html).toContain('Close sidebar')
 })
 it('preserves every diagnostic destination and expands the active tools',()=>{
  for(const view of views.filter(v=>v.id!=='chat')){
   const html=render(view.id)
   expect(html).toContain('class="workspace-tools" open=""')
   expect(html).toContain('aria-current="page"')
   expect(html).toContain('>Chat</button>')
   for(const item of views.filter(v=>v.id!=='chat'))expect(html).toContain(item.name)
   expect(html).toContain('Find a run')
  }
 })
})
