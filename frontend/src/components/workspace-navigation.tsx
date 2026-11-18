import type { ReactNode } from 'react'
import { Activity, Bot, ChevronDown, Database, FileText, Layers, PanelLeftClose, Plus, Search, Wrench } from 'lucide-react'
import { Button } from '@/components/ui/button'
export const views=[{id:'chat',name:'Chat',icon:Bot},{id:'research',name:'Research',icon:FileText},{id:'execution',name:'Execution',icon:Activity},{id:'models',name:'Models',icon:Layers},{id:'dataset',name:'Dataset',icon:Database}] as const
export type View=typeof views[number]['id']
/** Chat is the primary job. Diagnostics remain reachable, but never crowd the conversation list. */
export function WorkspaceNavigation({view,onView,onFind,onNew,onCollapse,busy,children,bottom}:{view:View;onView:(v:View)=>void;onFind:()=>void;onNew:()=>void;onCollapse:()=>void;busy:boolean;children:ReactNode;bottom:ReactNode}){
 return <>
  <div className="sidebar-brand"><span className="brand-mark" aria-hidden="true">C</span><strong>Cofinance</strong><Button variant="ghost" size="icon" aria-label="Close sidebar" onClick={onCollapse}><PanelLeftClose size={18}/></Button></div>
  <Button className="primary-new-chat" variant="outline" disabled={busy} onClick={onNew}><Plus size={18}/>New chat</Button>
  {view!=='chat'&&<Button variant="ghost" className="nav-item back-chat" onClick={()=>onView('chat')}><Bot size={17}/>Chat</Button>}
  {children}
  <details className="workspace-tools" key={view==='chat'?'chat':'tools'} open={view!=='chat'}>
   <summary><Wrench size={17}/><span>Workspace tools</span><ChevronDown size={15} className="disclosure-chevron"/></summary>
   <nav aria-label="Workspace tools">{views.filter(v=>v.id!=='chat').map(v=><Button key={v.id} variant="ghost" className={'nav-item '+(view===v.id?'nav-active':'')} aria-current={view===v.id?'page':undefined} onClick={()=>onView(v.id)}><v.icon size={17}/><span>{v.name}</span></Button>)}
    <Button variant="ghost" className="nav-item find-button" onClick={onFind}><Search size={17}/><span>Find a run</span><kbd>Ctrl K</kbd></Button>
   </nav>
  </details>
  <div className="sidebar-bottom">{bottom}</div>
 </>
}
