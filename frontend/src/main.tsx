import { StrictMode, Component, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
import App from './App'
import './index.css'
import './execution-map.css'

class Boundary extends Component<{children:ReactNode},{failed:boolean}> {
  state={failed:false}
  static getDerivedStateFromError(){return {failed:true}}
  render(){return this.state.failed?<main className="empty-state"><h1>Dashboard could not render</h1><p className="muted">Your local evidence is unchanged. Reload the dashboard to try again.</p><Button onClick={()=>location.reload()}>Reload dashboard</Button></main>:this.props.children}
}
createRoot(document.getElementById('root')!).render(
  <StrictMode><Boundary><TooltipProvider><App/></TooltipProvider></Boundary></StrictMode>,
)
