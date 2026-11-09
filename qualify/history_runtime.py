"""Run the frozen historical experiment with the already installed, checksum-verified LFM3 narrator.
Does not download, change the narrator selection, touch live chats, or grant model tools.
python -m qualify.history_runtime --root data/history_benchmark_20261109
"""
from __future__ import annotations
import argparse
import os
import secrets
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from core.local_models import FILENAME,MODEL_SHA,REVISION,RUNTIME_TAG,digest,local_json
from .historical import predict,evaluate

MODEL_META={'id':'lfm3-1.7b-q4km','revision':REVISION,'model_sha256':MODEL_SHA,'runtime':RUNTIME_TAG,'threads':6,'cpu_only':True}


@contextmanager
def narrator(model_root:Path,log_path:Path,alias:str='lfm3-history'):
    """Own a private llama-server for a benchmark. Yields (call, model_meta); always stops the server."""
    model=(model_root/FILENAME).resolve();runtime=(model_root/RUNTIME_TAG).resolve()
    if digest(model)!=MODEL_SHA:raise ValueError('Narrator checksum mismatch')
    servers=list(runtime.rglob('llama-server.exe'))
    if len(servers)!=1:raise ValueError('Expected one managed llama-server executable')
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    key=secrets.token_urlsafe(24);url=f'http://127.0.0.1:{port}'
    env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','COMSPEC'}}
    log_path.parent.mkdir(parents=True,exist_ok=True)
    with log_path.open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([str(servers[0]),'--model',str(model),'--alias',alias,'--host','127.0.0.1','--port',str(port),
            '--ctx-size','4096','--threads','6','--parallel','1','--n-gpu-layers','0','--reasoning','off','--jinja','--no-webui','--api-key',key],
            cwd=runtime,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
        try:
            ready=False
            for _ in range(180):
                if proc.poll() is not None:raise RuntimeError('Owned model server exited')
                try:local_json(url+'/health',key=key,timeout=1);ready=True;break
                except (OSError,ValueError):time.sleep(.5)
            if not ready:raise RuntimeError('Startup timeout')
            def call(system,prompt,**kwargs):
                res=local_json(url+'/v1/chat/completions',{'model':alias,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
                    'temperature':0,'max_tokens':16,'chat_template_kwargs':{'enable_thinking':False}},key=key,timeout=90)
                choice=res['choices'][0]
                if choice.get('finish_reason')!='stop':raise ValueError('Incomplete forecast')
                return choice['message']['content']
            yield call,dict(MODEL_META)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=8)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=8)


def run(root:Path,model_root:Path):
    if (root/'predictions.jsonl').exists():raise ValueError('Existing forecasts: evaluate them, do not silently rerun')
    with narrator(model_root,root/'model-runtime.log') as (call,meta):
        predict(root,call,meta)
    return evaluate(root)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--model-root',type=Path,default=Path('data/local_models'));a=ap.parse_args()
    report=run(a.root,a.model_root)
    for months,window in report['windows_months'].items():
        print(months+' months:',window['arms'])
if __name__=='__main__':main()
