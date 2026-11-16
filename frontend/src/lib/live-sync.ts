/** The three things a change stream reports. Wire them to an EventSource, or to a fake in tests. */
export interface StreamHandlers {open:()=>void;change:()=>void;error:()=>void}
/** Opens a change stream and returns a function that closes it. */
export type Connect=(on:StreamHandlers)=>()=>void
export interface Clock {after:(fn:()=>void,ms:number)=>unknown;cancel:(handle:unknown)=>void}
const realClock:Clock={after:(fn,ms)=>setTimeout(fn,ms),cancel:handle=>clearTimeout(handle as ReturnType<typeof setTimeout>)}

/**
 * Keeps a view fresh with one fetch loop. While the stream is open, it fetches only when the server reports a change.
 * While the stream is down or missing, it fetches every fallbackMs. Fetches never overlap: changes that arrive during
 * a fetch collapse into one follow-up fetch. fetchOnce must report its own errors; a failed fetch never stops the loop.
 * Returns dispose, which closes the stream and cancels the pending fetch.
 */
export function liveSync(fetchOnce:()=>Promise<void>,{connect,clock=realClock,fallbackMs=1200}:{connect?:Connect;clock?:Clock;fallbackMs?:number}={}):()=>void{
 let disposed=false,live=false,running=false,again=false,timer:unknown
 const cancel=()=>{if(timer!==undefined){clock.cancel(timer);timer=undefined}}
 const schedule=()=>{cancel();if(!disposed&&!live)timer=clock.after(()=>{timer=undefined;void run()},fallbackMs)}
 async function run(){
  if(disposed)return
  if(running){again=true;return}
  running=true
  try{await fetchOnce()}catch{/* fetchOnce shows its own error state */}
  finally{running=false;if(again){again=false;void run()}else schedule()}
 }
 const close=connect?.({open:()=>{live=true;cancel()},change:()=>{void run()},error:()=>{live=false;if(!running)void run()}})
 void run()
 return()=>{disposed=true;cancel();close?.()}
}
