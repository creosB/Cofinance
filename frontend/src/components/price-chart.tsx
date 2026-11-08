import { useEffect, useRef, useState } from 'react'
import type { PriceChart as ChartData } from '@/lib/use-chat'

// Plotly is lazy-loaded. The modebar appears on hover only, so the chart reads as content, not a tool.
export function PriceChart({data}:{data:ChartData}){
 const root=useRef<HTMLDivElement>(null),[error,setError]=useState(''),[loaded,setLoaded]=useState(false)
 useEffect(()=>{
  let disposed=false,observer:ResizeObserver|undefined,plot:typeof import('plotly.js')|undefined
  const element=root.current!
  async function draw(){
   try{
    const module=await import('plotly.js-basic-dist-min');if(disposed)return
    plot=module.default
    const styles=getComputedStyle(document.documentElement),canvas=document.createElement('canvas'),ctx=canvas.getContext('2d')!
    const color=(token:string)=>{ctx.fillStyle=styles.getPropertyValue(token);return ctx.fillStyle}
    await plot.react(element,[{x:data.points.map(p=>p.time),y:data.points.map(p=>p.close),type:'scatter',mode:'lines',name:data.ticker,line:{color:color('--primary'),width:1.6},hovertemplate:'%{x|%b %d, %Y}<br>%{y:,.4f} '+data.currency+'<extra></extra>'}],{
     autosize:true,height:220,margin:{l:52,r:12,t:8,b:32},paper_bgcolor:color('--background'),plot_bgcolor:color('--background'),
     font:{family:'Geist Variable, system-ui, sans-serif',size:14,color:color('--quiet')},
     xaxis:{type:'date',showgrid:false,zeroline:false,showline:false},yaxis:{fixedrange:false,gridcolor:color('--card'),zeroline:false},
     hovermode:'x unified',hoverlabel:{bgcolor:color('--popover'),bordercolor:color('--border'),font:{color:color('--foreground')}},dragmode:'zoom',showlegend:false,
    },{responsive:true,displaylogo:false,displayModeBar:'hover',modeBarButtonsToRemove:['lasso2d','select2d','toImage','sendDataToCloud']})
    if(disposed){plot.purge(element);return}
    setLoaded(true);observer=new ResizeObserver(()=>{if(plot)void plot.Plots.resize(element)});observer.observe(element)
   }catch{if(!disposed)setError('The chart could not render. The data table below still has every point.')}
  }
  void draw();return()=>{disposed=true;observer?.disconnect();plot?.purge(element)}
 },[data])
 return <figure className="price-chart"><figcaption><strong>{data.ticker}</strong><span>{data.window} close, {data.currency}</span></figcaption>{!loaded&&!error&&<div className="chart-loading" role="status" aria-label="Loading chart"/>}{error&&<p role="alert" className="error-text">{error}</p>}<div ref={root} role="img" aria-label={data.ticker+' daily close-price chart, '+data.window+', '+data.currency}/><details className="chart-data"><summary>{data.source}: {data.adjustment.charAt(0).toLowerCase()+data.adjustment.slice(1)}</summary><div className="table-scroll"><table><thead><tr><th>Date</th><th>Close ({data.currency})</th></tr></thead><tbody>{data.points.map(p=><tr key={p.time}><td>{new Date(p.time).toLocaleDateString()}</td><td className="mono">{p.close}</td></tr>)}</tbody></table></div></details></figure>
}
