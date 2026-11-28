import { useCallback, useEffect, useRef, useState } from 'react'
import { parseCorpus, parseRun, parseRuns, type Corpus, type Run, type RunMeta } from './domain'
import { liveSync, type Connect } from './live-sync'
import { sharedEvents } from './event-hub'

async function get(path:string,signal:AbortSignal):Promise<unknown>{
  const response=await fetch(path,{credentials:'omit',cache:'no-store',signal})
  if(!response.ok)throw new Error(response.status===403?'The local API rejected this origin. Open the dashboard in your regular browser.':response.status===404?'This local record is no longer available.':'The local server could not complete the request.')
  return response.json()
}

export interface DashboardOptions {
  active?: boolean
  needCorpus?: boolean
}

export function useDashboard(options: DashboardOptions = {}){
  const active = options.active ?? true
  const needCorpus = options.needCorpus ?? true
  const [runs,setRuns]=useState<RunMeta[]>([]),[run,setRun]=useState<Run|null>(null),[corpus,setCorpus]=useState<Corpus|null>(null)
  const [selected,setSelected]=useState(''),[loading,setLoading]=useState(active),[error,setError]=useState('')
  const current=useRef<Run|null>(null),selectedRef=useRef(''),pinnedRef=useRef(false),triggerRef=useRef<(()=>void)|null>(null)
  const selectionGenRef=useRef(0)
  const activeRef=useRef(active),needCorpusRef=useRef(needCorpus)
  activeRef.current=active
  needCorpusRef.current=needCorpus
  const select=useCallback((id:string)=>{selectionGenRef.current+=1;pinnedRef.current=Boolean(id);selectedRef.current=id;setSelected(id);setRun(null);current.current=null;setLoading(Boolean(id));if(activeRef.current)triggerRef.current?.()},[])
  const followLatest=useCallback(()=>{pinnedRef.current=false;if(activeRef.current)triggerRef.current?.()},[])
  const refresh=useCallback(()=>{triggerRef.current?.()},[])
  useEffect(()=>{
    if(active)triggerRef.current?.()
  },[active,needCorpus])
  useEffect(()=>{
    let disposed=false
    const controller=new AbortController()
    async function poll(){
      if(disposed||!activeRef.current)return
      const deadline=new AbortController(),abort=()=>deadline.abort()
      controller.signal.addEventListener('abort',abort,{once:true})
      const timeout=setTimeout(abort,8000)
      try{
        const corpusPromise=needCorpusRef.current?get('/api/corpus',deadline.signal):Promise.resolve(undefined)
        const [rawRuns,rawCorpus]=await Promise.all([get('/api/runs',deadline.signal),corpusPromise])
        const list=parseRuns(rawRuns)
        if(disposed)return
        setRuns(previous=>JSON.stringify(previous)===JSON.stringify(list)?previous:list)
        if(rawCorpus!==undefined){
          const data=parseCorpus(rawCorpus)
          setCorpus(previous=>JSON.stringify(previous)===JSON.stringify(data)?previous:data)
        }
        let id=selectedRef.current
        if(id&&!list.some(r=>r.id===id)){
          pinnedRef.current=false;current.current=null;setRun(null)
          id=list[0]?.id||''
          selectedRef.current=id;setSelected(id)
        }else if((!id||!pinnedRef.current)&&list[0]&&list[0].id!==id){
          current.current=null;setRun(null)
          id=list[0].id;selectedRef.current=id;setSelected(id)
        }
        if(id){
          const meta=list.find(r=>r.id===id)
          if(meta&&(!current.current||current.current.id!==id||current.current.revision!==meta.revision)){
            const gen=selectionGenRef.current
            const next=parseRun(await get('/api/run?id='+encodeURIComponent(id),deadline.signal))
            if(disposed||selectedRef.current!==id||selectionGenRef.current!==gen)return
            current.current=next;setRun(next)
          }
        }else{
          current.current=null;setRun(null)
        }
        if(!disposed){setError('');setLoading(false)}
      }catch(cause){if(!disposed){setError(cause instanceof Error&&cause.name!=='AbortError'?cause.message:'Local server did not respond. Check that the dashboard launcher is running.');setLoading(false)}}
      finally{clearTimeout(timeout);controller.signal.removeEventListener('abort',abort)}
    }
    const baseConnect=sharedEvents()
    const connect:Connect=on=>{
      triggerRef.current=on.change
      const unsub=baseConnect?.({
        open:on.open,
        error:on.error,
        change:()=>{if(activeRef.current)on.change()},
      })
      return()=>{triggerRef.current=null;unsub?.()}
    }
    const dispose=liveSync(poll,{connect,fallbackMs:2500})
    return()=>{disposed=true;controller.abort();dispose()}
  },[])
  return {runs,run,corpus,selected,select,followLatest,loading,error,refresh}
}
