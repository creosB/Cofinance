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
from core.local_models import BACKENDS,FILENAME,MODEL_SHA,REVISION,RUNTIME_TAG,digest,local_json,narrator_spec,server_command,server_flags
from .historical import predict,evaluate

MODEL_META={'id':'lfm3-1.7b-q4km','revision':REVISION,'model_sha256':MODEL_SHA,'runtime':RUNTIME_TAG}


def default_backend(model_root:Path):
    """Same order as the live narrator: the Vulkan GPU build when installed, else CPU. COFINANCE_BACKEND overrides."""
    chosen=os.environ.get('COFINANCE_BACKEND','')
    if chosen:
        if chosen not in BACKENDS:raise ValueError('Unknown COFINANCE_BACKEND')
        return chosen
    return next((b for b in BACKENDS if (model_root/BACKENDS[b]['dir']).is_dir()),'cpu')


@contextmanager
def narrator(model_root:Path,log_path:Path,alias:str='lfm3-history',backend:str|None=None,model_id:str|None=None):
    """Own a private llama-server for a benchmark. Yields (call, model_meta); always stops the server.
    model_id picks a pinned catalog narrator (default: the base LFM3, so older benchmarks are unchanged)."""
    backend=backend or default_backend(model_root)
    spec=narrator_spec(model_id) if model_id else {'filename':FILENAME,'sha':MODEL_SHA,'revision':REVISION}
    meta=dict(MODEL_META) if not model_id else {'id':model_id,'revision':spec['revision'],'model_sha256':spec['sha'],'runtime':RUNTIME_TAG}
    model=(model_root/spec['filename']).resolve();runtime=(model_root/BACKENDS[backend]['dir']).resolve()
    if digest(model)!=spec['sha']:raise ValueError('Narrator checksum mismatch')
    servers=list(runtime.rglob('llama-server.exe'))
    if len(servers)!=1:raise ValueError('Expected one managed llama-server executable')
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    key=secrets.token_urlsafe(24);url=f'http://127.0.0.1:{port}'
    env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','COMSPEC'}}
    log_path.parent.mkdir(parents=True,exist_ok=True)
    with log_path.open('w',encoding='utf-8') as log:
        proc=subprocess.Popen(server_command(servers[0],model,alias,port,key,backend),
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
                body={'model':alias,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
                    'temperature':0,'max_tokens':int(kwargs.get('max_tokens',16)),'chat_template_kwargs':{'enable_thinking':False}}
                if kwargs.get('stop'):body['stop']=list(kwargs['stop'])
                if kwargs.get('response_format'):body['response_format']=kwargs['response_format']
                if kwargs.get('top_logprobs'):body['logprobs']=True;body['top_logprobs']=int(kwargs['top_logprobs'])
                res=local_json(url+'/v1/chat/completions',body,key=key,timeout=kwargs.get('timeout',90))
                choice=res['choices'][0]
                if choice.get('finish_reason')!='stop' and not kwargs.get('allow_length'):raise ValueError('Incomplete forecast')
                return choice if kwargs.get('return_choice') else choice['message']['content']
            yield call,dict(meta,backend=backend,flags=server_flags(backend),cpu_only=backend=='cpu')
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
