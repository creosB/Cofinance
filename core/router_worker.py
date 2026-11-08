"""Persistent local GLiNER subprocess with bounded, cancellable JSON RPC.
The model loads in its own main thread. The HTTP server never imports the ML stack.
"""
from __future__ import annotations
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from .local_models import Cancelled, ModelError

REVISION='9d1bfb848cd16d93ee56ccbc59cd92bf0f284d90'


class RouterProcess:
    model_id='fastino/GLiNER2.5-Decide@'+REVISION
    def __init__(self,repo,logs,cancel=None):
        self.repo,self.logs=Path(repo),Path(logs);self.cancel=cancel
        self.process=None;self.queue=queue.Queue();self.lock=threading.Lock();self.seq=0
    def check(self):
        if self.cancel and self.cancel.is_set():self.close();raise Cancelled('Routing stopped.')
    def receive(self,timeout):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            self.check()
            try:
                value=self.queue.get(timeout=.2)
                if value is None:raise ModelError('Routing process exited. Check its local log.')
                return value
            except queue.Empty:
                if self.process and self.process.poll() is not None:raise ModelError('Routing process exited. Check its local log.')
        self.close();raise ModelError('Routing operation exceeded its time budget.')
    def start(self):
        if self.process and self.process.poll() is None:return
        self.queue=queue.Queue();self.logs.mkdir(parents=True,exist_ok=True)
        env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','COMSPEC','PROCESSOR_ARCHITECTURE'}}
        env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',USE_TF='0',USE_FLAX='0',OMP_NUM_THREADS='6',MKL_NUM_THREADS='6')
        with (self.logs/'router.log').open('a',encoding='utf-8') as log:
            self.process=subprocess.Popen([sys.executable,'-E','-B','-u','-m','core.router_worker'],cwd=self.repo,env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',errors='replace',
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        proc=self.process;out=self.queue
        def reader():
            try:
                for line in proc.stdout:
                    if len(line)>1024*1024:break
                    try:out.put(json.loads(line))
                    except ValueError:continue
            finally:out.put(None)
        threading.Thread(target=reader,daemon=True).start()
        ready=self.receive(120)
        if not ready.get('ready'):self.close();raise ModelError('Cached routing model failed to load.')
    def call(self,method,text,schema,kwargs):
        with self.lock:
            self.start();self.check();self.seq+=1
            message={'id':self.seq,'method':method,'text':text,'schema':schema,'kwargs':kwargs}
            self.process.stdin.write(json.dumps(message,ensure_ascii=True)+'\n');self.process.stdin.flush()
            value=self.receive(20)
            if value.get('id')!=self.seq or 'result' not in value:
                raise ModelError('Routing inference failed or returned an invalid response.')
            return value['result']
    def classify_text(self,text,schema,**kwargs):return self.call('classify_text',text,schema,kwargs)
    def extract_entities(self,text,entity_types):return self.call('extract_entities',text,entity_types,{})
    def close(self):
        process=self.process;self.process=None
        if process:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
            if process.stdin:process.stdin.close()
            if process.stdout:process.stdout.close()


def main():
    import contextlib
    import socket
    protocol=sys.stdout
    # Enforce no outbound sockets even if a dependency ignores offline flags.
    connect=socket.socket.connect
    def guarded(sock,address):
        if isinstance(address,tuple) and address[0] not in {'127.0.0.1','::1','localhost'}:
            raise OSError('Routing worker has no external network authority.')
        return connect(sock,address)
    socket.socket.connect=guarded
    with contextlib.redirect_stdout(sys.stderr):
        from gliner2 import AutoExtractor
        import torch
        torch.set_num_threads(6)
        path=Path.home()/'.cache/huggingface/hub/models--fastino--GLiNER2.5-Decide/snapshots'/REVISION
        model=AutoExtractor.from_pretrained(str(path),local_files_only=True)
    protocol.write(json.dumps({'ready':True})+'\n');protocol.flush()
    for line in sys.stdin:
        if len(line)>32000:break
        message=json.loads(line)
        try:
            method=message['method']
            with contextlib.redirect_stdout(sys.stderr):
                if method=='classify_text':result=model.classify_text(message['text'],message['schema'],**message.get('kwargs',{}))
                elif method=='extract_entities':result=model.extract_entities(message['text'],message['schema'])
                else:raise ValueError('Unsupported inference method')
            value={'id':message['id'],'result':result}
        except Exception as exc:value={'id':message.get('id'),'error':type(exc).__name__}
        protocol.write(json.dumps(value,ensure_ascii=True)+'\n');protocol.flush()


if __name__=='__main__':main()
