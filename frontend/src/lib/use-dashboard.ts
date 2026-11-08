import { useCallback, useEffect, useRef, useState } from 'react'
import { parseCorpus, parseRun, parseRuns, type Corpus, type Run, type RunMeta } from './domain'

async function get(path:string,signal:AbortSignal):Promise<unknown>{
  const response=await fetch(path,{credentials:'omit',cache:'no-store',signal})
  if(!response.ok)throw new Error(response.status===403?'The local API rejected this origin. Open the dashboard in your regular browser.':response.status===404?'This local record is no longer available.':'The local server could not complete the request.')
  return response.json()
}
export function useDashboard(){
  const [runs,setRuns]=useState<RunMeta[]>([]),[run,setRun]=useState<Run|null>(null),[corpus,setCorpus]=useState<Corpus|null>(null)
  const [selected,setSelected]=useState(''),[loading,setLoading]=useState(true),[error,setError]=useState(''),[version,setVersion]=useState(0)
  const current=useRef<Run|null>(null),selectedRef=useRef('')
  const select=useCallback((id:string)=>{selectedRef.current=id;setSelected(id);setRun(null);current.current=null;setLoading(true)},[])
  const refresh=useCallback(()=>setVersion(v=>v+1),[])
  useEffect(()=>{
    let disposed=false,busy=false,timer:ReturnType<typeof setTimeout>|undefined
    const controller=new AbortController()
    async function poll(){
      if(disposed||busy)return
      busy=true
      const deadline=new AbortController(),abort=()=>deadline.abort()
      controller.signal.addEventListener('abort',abort,{once:true})
      const timeout=setTimeout(abort,8000)
      try{
        const [rawRuns,rawCorpus]=await Promise.all([get('/api/runs',deadline.signal),get('/api/corpus',deadline.signal)])
        const list=parseRuns(rawRuns),data=parseCorpus(rawCorpus)
        if(disposed)return
        setRuns(previous=>JSON.stringify(previous)===JSON.stringify(list)?previous:list)
        setCorpus(previous=>JSON.stringify(previous)===JSON.stringify(data)?previous:data)
        const id=selectedRef.current||list[0]?.id
        if(id){
          const meta=list.find(r=>r.id===id)
          if(!meta)throw new Error('Selected run is no longer available. Choose another run.')
          if(!selectedRef.current){selectedRef.current=id;setSelected(id)}
          if(!current.current||current.current.id!==id||current.current.revision!==meta.revision){
            const next=parseRun(await get('/api/run?id='+encodeURIComponent(id),deadline.signal))
            if(disposed||selectedRef.current!==id)return
            current.current=next;setRun(next)
          }
        }
        if(!disposed){setError('');setLoading(false)}
      }catch(cause){if(!disposed){setError(cause instanceof Error&&cause.name!=='AbortError'?cause.message:'Local server did not respond. Check that the dashboard launcher is running.');setLoading(false)}}
      finally{clearTimeout(timeout);controller.signal.removeEventListener('abort',abort);busy=false;if(!disposed)timer=setTimeout(poll,2500)}
    }
    void poll()
    return()=>{disposed=true;controller.abort();clearTimeout(timer)}
  },[selected,version])
  return {runs,run,corpus,selected,select,loading,error,refresh}
}
