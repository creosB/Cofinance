"""Persistent local GLiNER subprocess with bounded, cancellable JSON RPC.
The model loads in its own main thread. The HTTP server never imports the ML stack.
"""
from __future__ import annotations
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from .local_models import Cancelled, ModelError
from .process_guard import global_slot_lock, require_memory_headroom, spawn_managed

REVISION='9d1bfb848cd16d93ee56ccbc59cd92bf0f284d90'
ONNX_PARITY_MAX_ERR=1e-3
ONNX_FORMAT='encoder_fp32'


def onnx_paths(cache_dir: Path) -> tuple[Path, Path]:
    base=Path(cache_dir)/f'gliner_encoder_fp32_{REVISION[:12]}'
    return base.with_suffix('.onnx'), base.with_suffix('.meta.json')


def file_sha256(path: Path) -> str:
    with Path(path).open('rb') as fh:
        return hashlib.file_digest(fh, 'sha256').hexdigest()


def verify_onnx_cache(cache_dir: Path) -> Path | None:
    onnx_path, meta_path=onnx_paths(cache_dir)
    if not onnx_path.is_file() or not meta_path.is_file():
        return None
    try:
        meta=json.loads(meta_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    if meta.get('revision')!=REVISION or meta.get('format')!=ONNX_FORMAT:
        return None
    err=meta.get('max_abs_err')
    if not isinstance(err, (int, float)) or err>ONNX_PARITY_MAX_ERR:
        return None
    if onnx_path.stat().st_size!=meta.get('bytes'):
        return None
    if file_sha256(onnx_path)!=meta.get('sha256'):
        return None
    return onnx_path


def make_onnx_encoder(onnx_path: Path, config, threads: int = 6):
    import onnxruntime as ort
    import torch

    opts=ort.SessionOptions()
    opts.intra_op_num_threads=threads
    opts.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session=ort.InferenceSession(str(onnx_path), sess_options=opts, providers=['CPUExecutionProvider'])

    class OnnxEncoderModule(torch.nn.Module):
        def __init__(self, sess, cfg):
            super().__init__()
            self.session=sess
            self.config=cfg

        def forward(self, input_ids, attention_mask, **kwargs):
            ids_np=input_ids.detach().cpu().numpy().astype('int64')
            mask_np=attention_mask.detach().cpu().numpy().astype('int64')
            (lhs,)=self.session.run(['last_hidden_state'], {'input_ids': ids_np, 'attention_mask': mask_np})
            return SimpleNamespace(last_hidden_state=torch.from_numpy(lhs))

    return OnnxEncoderModule(session, config)


def maybe_attach_onnx_encoder(model, cache_dir: Path | None, *, mode: str = 'auto', threads: int = 6, loader=make_onnx_encoder) -> str:
    mode=(mode or 'auto').strip().lower()
    if mode=='off' or cache_dir is None:
        return 'pytorch_fp32'
    onnx_path=verify_onnx_cache(Path(cache_dir))
    if onnx_path is None:
        if mode=='required':
            raise ModelError('Verified ONNX encoder cache is missing or failed integrity verification.')
        return 'pytorch_fp32'
    original=model.encoder
    try:
        model.encoder=loader(onnx_path, original.config, threads)
        return ONNX_FORMAT
    except Exception:
        model.encoder=original
        if mode=='required':
            raise
        return 'pytorch_fp32'


def export_onnx_encoder(cache_dir: Path, *, source_onnx: Path | None = None, threads: int = 6) -> dict:
    import numpy as np
    import onnxruntime as ort
    import torch
    from gliner2 import AutoExtractor

    cache_dir=Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    onnx_path, meta_path=onnx_paths(cache_dir)
    part_path=onnx_path.with_suffix('.onnx.part')

    torch.set_num_threads(threads)
    snap=Path.home()/'.cache/huggingface/hub/models--fastino--GLiNER2.5-Decide/snapshots'/REVISION
    model=AutoExtractor.from_pretrained(str(snap), local_files_only=True).cpu().eval()
    base_encoder=model.encoder

    if source_onnx is not None and Path(source_onnx).is_file():
        shutil.copyfile(source_onnx, part_path)
    else:
        class ExportWrapper(torch.nn.Module):
            def __init__(self, enc):
                super().__init__()
                self.enc=enc

            def forward(self, input_ids, attention_mask):
                return self.enc(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state

        dummy_ids=torch.randint(10, 5000, (1, 32), dtype=torch.long)
        dummy_mask=torch.ones((1, 32), dtype=torch.long)
        with torch.no_grad():
            torch.onnx.export(
                ExportWrapper(base_encoder).eval(),
                (dummy_ids, dummy_mask),
                str(part_path),
                input_names=['input_ids', 'attention_mask'],
                output_names=['last_hidden_state'],
                dynamic_axes={
                    'input_ids': {0: 'batch', 1: 'seq'},
                    'attention_mask': {0: 'batch', 1: 'seq'},
                    'last_hidden_state': {0: 'batch', 1: 'seq'},
                },
                opset_version=17,
                do_constant_folding=True,
                dynamo=False,
            )

    opts=ort.SessionOptions()
    opts.intra_op_num_threads=threads
    opts.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess=ort.InferenceSession(str(part_path), sess_options=opts, providers=['CPUExecutionProvider'])

    max_err=0.0
    errs={}
    gen=torch.Generator().manual_seed(1729)
    with torch.no_grad():
        for seq_len in (24, 64, 160):
            ids=torch.randint(10, 5000, (1, seq_len), dtype=torch.long, generator=gen)
            mask=torch.ones((1, seq_len), dtype=torch.long)
            ref=base_encoder(input_ids=ids, attention_mask=mask).last_hidden_state.numpy()
            (got,)=sess.run(['last_hidden_state'], {'input_ids': ids.numpy().astype('int64'), 'attention_mask': mask.numpy().astype('int64')})
            err=float(np.max(np.abs(got-ref)))
            errs[str(seq_len)]=round(err, 6)
            max_err=max(max_err, err)

    del sess
    if max_err>ONNX_PARITY_MAX_ERR:
        part_path.unlink(missing_ok=True)
        raise ModelError(f'ONNX encoder parity check failed: max_abs_err={max_err:.6f} > {ONNX_PARITY_MAX_ERR}')

    os.replace(part_path, onnx_path)
    meta={
        'revision': REVISION,
        'format': ONNX_FORMAT,
        'bytes': onnx_path.stat().st_size,
        'sha256': file_sha256(onnx_path),
        'max_abs_err': round(max_err, 6),
        'seq_errors': errs,
    }
    meta_part=meta_path.with_suffix('.json.part')
    meta_part.write_text(json.dumps(meta, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    os.replace(meta_part, meta_path)
    return meta


class RouterProcess:
    model_id='fastino/GLiNER2.5-Decide@'+REVISION
    def __init__(self,repo,logs,cancel=None):
        self.repo,self.logs=Path(repo),Path(logs);self.cancel=cancel
        self.process=None;self.queue=queue.Queue();self.lock=threading.Lock();self.seq=0;self.backend=None;self._slot_lock=None
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
        require_memory_headroom(1600, 'routing model (GLiNER)', error_type=ModelError)
        slot=global_slot_lock('router','Cofinance routing model',error_type=ModelError)
        slot.acquire()
        self._slot_lock=slot
        try:
            env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','COMSPEC','PROCESSOR_ARCHITECTURE'}}
            env.update(
                HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',USE_TF='0',USE_FLAX='0',
                OMP_NUM_THREADS='6',MKL_NUM_THREADS='6',
                COFINANCE_ROUTER_CACHE=str(self.logs.resolve()),
                COFINANCE_ROUTER_ONNX='required',
            )
            if os.environ.get('COFINANCE_SKIP_MEM_CHECK','').strip()=='1':
                env['COFINANCE_SKIP_MEM_CHECK']='1'
            with (self.logs/'router.log').open('a',encoding='utf-8') as log:
                self.process=spawn_managed([sys.executable,'-E','-B','-u','-m','core.router_worker'],error_type=ModelError,cwd=self.repo,env=env,
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
            if not ready.get('ready'):self.close();raise ModelError(ROUTER_CACHE_ERROR)
            self.backend=ready.get('backend','pytorch_fp32')
        except BaseException:
            self.close()
            raise
    def _rpc(self,payload,timeout=20):
        with self.lock:
            self.start();self.check();self.seq+=1
            message={'id':self.seq,**payload}
            self.process.stdin.write(json.dumps(message,ensure_ascii=True)+'\n');self.process.stdin.flush()
            value=self.receive(timeout)
            if value.get('id')!=self.seq or 'result' not in value:
                raise ModelError('Routing inference failed or returned an invalid response.')
            return value['result']
    def call(self,method,text,schema,kwargs):
        return self._rpc({'method':method,'text':text,'schema':schema,'kwargs':kwargs},timeout=20)
    def classify_text(self,text,schema,**kwargs):return self.call('classify_text',text,schema,kwargs)
    def batch_classify_text(self,texts,schema,**kwargs):
        items=list(texts)
        if not items:return []
        timeout=min(120,max(20,10*len(items)))
        return self._rpc({'method':'batch_classify_text','texts':items,'schema':schema,'kwargs':kwargs},timeout=timeout)
    def extract_entities(self,text,entity_types):return self.call('extract_entities',text,entity_types,{})
    def close(self):
        process=self.process;self.process=None;self.backend=None
        if process:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
            if process.stdin:process.stdin.close()
            if process.stdout:process.stdout.close()
        if self._slot_lock is not None:
            self._slot_lock.release()
            self._slot_lock=None


def _install_offline_guard():
    import socket
    connect=socket.socket.connect
    def guarded(sock,address):
        if isinstance(address,tuple) and address[0] not in {'127.0.0.1','::1','localhost'}:
            raise OSError('Routing worker has no external network authority.')
        return connect(sock,address)
    socket.socket.connect=guarded


ROUTER_CACHE_ERROR = 'The verified standalone ONNX router is unavailable. Restore its local cache; no PyTorch fallback was started.'


def _load_worker_model(cache_dir: Path | None, mode: str, threads: int = 6):
    mode = (mode or 'required').strip().lower()
    if mode != 'off':
        from .gliner_onnx import STANDALONE_FORMAT, load_standalone_onnx
        if mode not in {'auto', 'required'} or cache_dir is None:
            raise ModelError(ROUTER_CACHE_ERROR)
        try:
            standalone = load_standalone_onnx(cache_dir, threads=threads)
        except Exception as exc:
            raise ModelError(ROUTER_CACHE_ERROR) from exc
        if standalone is None:
            raise ModelError(ROUTER_CACHE_ERROR)
        return standalone, STANDALONE_FORMAT

    require_memory_headroom(3200, "PyTorch router fallback (standalone ONNX cache missing)", error_type=ModelError)
    from gliner2 import AutoExtractor
    import torch
    torch.set_num_threads(threads)
    path = Path.home() / '.cache/huggingface/hub/models--fastino--GLiNER2.5-Decide/snapshots' / REVISION
    model = AutoExtractor.from_pretrained(str(path), local_files_only=True)
    backend = maybe_attach_onnx_encoder(model, cache_dir, mode=mode, threads=threads)
    return model, backend


def main():
    import contextlib
    _install_offline_guard()
    if len(sys.argv)>=3 and sys.argv[1]=='export-onnx':
        src=Path(sys.argv[3]) if len(sys.argv)>=4 else None
        with contextlib.redirect_stdout(sys.stderr):
            meta=export_onnx_encoder(Path(sys.argv[2]), source_onnx=src)
        print(json.dumps(meta, indent=2, sort_keys=True), flush=True)
        return
    if len(sys.argv)>=3 and sys.argv[1]=='export-standalone':
        from .gliner_onnx import export_standalone_onnx
        src=Path(sys.argv[3]) if len(sys.argv)>=4 else None
        with contextlib.redirect_stdout(sys.stderr):
            meta=export_standalone_onnx(Path(sys.argv[2]), source_onnx=src)
        print(json.dumps(meta, indent=2, sort_keys=True), flush=True)
        return
    protocol=sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            cache_env=os.environ.get('COFINANCE_ROUTER_CACHE')
            model, backend = _load_worker_model(
                Path(cache_env) if cache_env else None,
                os.environ.get('COFINANCE_ROUTER_ONNX', 'required'),
                threads=6,
            )
    except ModelError:
        protocol.write(json.dumps({'ready':False})+'\n');protocol.flush()
        return
    protocol.write(json.dumps({'ready':True,'backend':backend})+'\n');protocol.flush()
    for line in sys.stdin:
        if len(line)>256000:break
        message=json.loads(line)
        try:
            method=message['method']
            with contextlib.redirect_stdout(sys.stderr):
                if method=='classify_text':result=model.classify_text(message['text'],message['schema'],**message.get('kwargs',{}))
                elif method=='batch_classify_text':result=model.batch_classify_text(message['texts'],message['schema'],**message.get('kwargs',{}))
                elif method=='extract_entities':result=model.extract_entities(message['text'],message['schema'])
                else:raise ValueError('Unsupported inference method')
            value={'id':message['id'],'result':result}
        except Exception as exc:value={'id':message.get('id'),'error':type(exc).__name__}
        protocol.write(json.dumps(value,ensure_ascii=True)+'\n');protocol.flush()


if __name__=='__main__':main()
