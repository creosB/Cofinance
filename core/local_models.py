"""Pinned local narrator management. No hosted inference, arbitrary URLs, or shell execution.

Only the explicitly approved catalog can be downloaded. Byte progress is measured;
verification and qualification are separate states. Partial files resume on retry.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

MODEL_ID = "lfm3-1.7b-q4km"
REVISION = "cd0c74c575b5c416f3011b5b4c298077f26ca8c2"
FILENAME = "LFM3-1.7B-Q4_K_M.gguf"
MODEL_SHA = "3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4"
MODEL_SIZE = 1099627808
MODEL_URL = f"https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF/resolve/{REVISION}/{FILENAME}"
LICENSE_URL = f"https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF/raw/{REVISION}/LICENSE"
LICENSE_BLOB = "25e731c697c4fe4c52ee654cedf7106a827748d2"
RUNTIME_TAG = "b12110"
RUNTIME_SHA = "98901e327c252875accaf2d1bf364e10e868843b0b63468baad1aa945cdc5da2"
RUNTIME_SIZE = 24708445
RUNTIME_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{RUNTIME_TAG}/llama-{RUNTIME_TAG}-bin-win-cpu-x64.zip"


class ModelError(RuntimeError):
    pass


class Cancelled(ModelError):
    pass


def atomic(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def digest(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def public_host(url):
    p = urlsplit(url)
    host = p.hostname or ""
    return (p.scheme == "https" and not p.username and not p.password and p.port in (None, 443)
            and (host in {"huggingface.co", "github.com", "release-assets.githubusercontent.com", "cdn-lfs.huggingface.co"}
                 or host.endswith(".xethub.hf.co") or host.endswith(".cdn.hf.co")))


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_host(newurl):
            raise ModelError("Download redirected outside the approved distribution hosts.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_open(url, headers=None):
    if not public_host(url):
        raise ModelError("Unapproved download host.")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicRedirect())
    return opener.open(urllib.request.Request(url, headers={"User-Agent": "Cofinance-local-model-manager", **(headers or {})}), timeout=25)


def local_json(url, body=None, key="", timeout=90):
    p = urlsplit(url)
    if p.scheme != "http" or p.hostname != "127.0.0.1" or p.username or p.password:
        raise ModelError("Inference must use the managed loopback endpoint.")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            raise ModelError("Local inference redirects are not allowed.")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body, allow_nan=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with op.open(req, timeout=timeout) as response:
        data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ModelError("Local model response exceeded the limit.")
        return json.loads(data)


class LocalModels:
    def __init__(self, root: Path, repo: Path):
        self.root, self.repo = Path(root), Path(repo)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.process = None
        self.url = ""
        self.key = ""
        self.ready = False
        self.busy = False
        self.state = {"phase": "not_downloaded", "stage": "", "downloaded_bytes": 0, "total_bytes": MODEL_SIZE + RUNTIME_SIZE,
                      "speed_bps": 0, "error": "", "qualification": None, "selected": "facts", "router": "not_loaded",
                      "preferred": "facts", "selection_note": ""}
        path = self.root / "state.json"
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                for key in ("phase", "qualification", "downloaded_bytes", "preferred"):
                    if key in saved:
                        self.state[key] = saved[key]
                if self.state["phase"] in {"downloading", "verifying", "loading", "qualifying", "ready"}:
                    self.state["phase"] = "downloaded" if self.model_path.exists() else "paused"
            except (OSError, ValueError):
                self.state["error"] = "Saved model state could not be read. Retry verification."
        self.restore_choice()

    def restore_choice(self):
        """Restore the user's last narrator choice, never a loaded process.

        A restart still never starts inference: the selection comes back, the model loads only when the next research
        run needs it, and load() re-verifies the SHA-256 first. A stale qualification or missing file falls back to
        tool-only and says why, instead of silently switching narrators.
        """
        preferred = self.state.get("preferred")
        if preferred not in {"facts", MODEL_ID}:
            preferred = "facts"
        self.state["preferred"], self.state["selected"], self.state["selection_note"] = preferred, "facts", ""
        if preferred == "facts":
            return
        try:
            file_ok = self.model_path.is_file() and self.model_path.stat().st_size == MODEL_SIZE
            still_qualified = self.qualified()
        except OSError:
            file_ok = still_qualified = False
        if not file_ok:
            self.state["selection_note"] = "Last choice was LFM3 1.7B, but the model file is missing. Using tool-only."
        elif not still_qualified:
            self.state["selection_note"] = "Last choice was LFM3 1.7B, but its qualification is out of date. Using tool-only."
        else:
            self.state["selected"] = MODEL_ID
            self.state["selection_note"] = "Restored from the last session. Loads on the next run."

    @property
    def model_path(self):
        return self.root / FILENAME

    @property
    def runtime_dir(self):
        return self.root / RUNTIME_TAG

    def update(self, **values):
        with self.lock:
            self.state.update(values)
            atomic(self.root / "state.json", self.state)

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
            result.update(id=MODEL_ID, name="LFM3 1.7B", quantization="Q4_K_M", size_bytes=MODEL_SIZE,
                          revision=REVISION, runtime="llama.cpp " + RUNTIME_TAG + " / CPU", loaded=self.ready and self.process is not None and self.process.poll() is None,
                          license="LFM Open License v1.0", license_note="Commercial use is limited by the $10M annual-revenue threshold.",
                          source="https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF", router_model="fastino/GLiNER2.5-Decide",
                          can_select=self.qualified(), busy=self.busy)
            return result

    def qualified(self):
        q = self.state.get("qualification")
        return bool(q and q.get("passed") and q.get("fingerprint") == self.fingerprint())

    def fingerprint(self):
        h = hashlib.sha256((MODEL_SHA + RUNTIME_SHA + "greedy-thinking-off-v1").encode())
        for name in ("qualify/cases.jsonl", "qualify/gates_narrator.json", "qualify/scorers.py"):
            h.update((self.repo / name).read_bytes())
        return h.hexdigest()

    def launch_task(self, action):
        if action not in {"download", "qualify", "load", "unload", "pause"}:
            raise ValueError("Unknown model action.")
        if action == "pause":
            self.cancel.set()
            return self.snapshot()
        with self.lock:
            if self.busy:
                raise ModelError("A research run is using the model. Wait for it to finish.")
            if self.thread and self.thread.is_alive():
                raise ModelError("Another model operation is still running.")
            self.cancel.clear()
            def work():
                try:
                    if action == "download": self.download()
                    elif action == "qualify": self.qualify()
                    elif action == "load": self.load()
                    elif action == "unload": self.unload()
                except Cancelled:
                    self.update(phase="paused", speed_bps=0, error="Operation paused. Retry to resume.")
                except Exception as exc:
                    self.update(phase="error", speed_bps=0, error=f"{type(exc).__name__}: {str(exc)[:250]}")
            self.thread = threading.Thread(target=work, daemon=True)
            self.thread.start()
        return self.snapshot()

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled("Paused by user.")

    def fetch(self, url, target, size, sha, base):
        part = target.with_suffix(target.suffix + ".part")
        if target.exists():
            self.update(phase="verifying", stage=target.name, downloaded_bytes=base + size)
            if target.stat().st_size == size and digest(target) == sha:
                return
            raise ModelError("Existing file failed verification; left untouched for inspection.")
        offset = part.stat().st_size if part.exists() else 0
        if offset > size:
            raise ModelError("Partial file exceeds expected size.")
        if offset < size:
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with public_open(url, headers) as response:
                if offset and response.status != 206:
                    offset = 0  # server ignored Range; safely restart only the partial file
                if response.status == 206 and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ModelError("Unexpected range response.")
                started, initial, last = time.monotonic(), offset, 0.0
                with part.open("ab" if offset else "wb") as file:
                    while True:
                        self.check_cancel()
                        data = response.read(1024 * 1024)
                        if not data: break
                        offset += len(data)
                        if offset > size: raise ModelError("Download exceeded its pinned size.")
                        file.write(data)
                        now = time.monotonic()
                        if now - last >= .4:
                            self.update(phase="downloading", stage=target.name, downloaded_bytes=base+offset,
                                        speed_bps=round((offset-initial)/max(.01,now-started)))
                            last = now
        self.update(phase="verifying", stage=target.name, downloaded_bytes=base+offset, speed_bps=0)
        self.check_cancel()
        if part.stat().st_size != size or digest(part) != sha:
            raise ModelError("SHA-256 or size verification failed; partial file retained, never executed.")
        os.replace(part, target)

    def download(self):
        if self.process and self.process.poll() is None:
            raise ModelError("Unload the narrator before checking downloads.")
        if shutil.disk_usage(self.root).free < MODEL_SIZE + 512*1024*1024 and not self.model_path.exists():
            raise ModelError("At least 1.6 GB of free disk is required.")
        self.update(phase="downloading", stage="License", error="")
        with public_open(LICENSE_URL) as response:
            license_data = response.read(20000)
        if hashlib.sha1(b"blob " + str(len(license_data)).encode() + b"\0" + license_data).hexdigest() != LICENSE_BLOB:
            raise ModelError("Pinned license verification failed.")
        (self.root / "LICENSE-LFM.txt").write_bytes(license_data)
        archive = self.root / f"llama-{RUNTIME_TAG}-cpu.zip"
        self.fetch(RUNTIME_URL, archive, RUNTIME_SIZE, RUNTIME_SHA, 0)
        self.fetch(MODEL_URL, self.model_path, MODEL_SIZE, MODEL_SHA, RUNTIME_SIZE)
        self.update(phase="verifying", stage="Runtime files")
        receipts = {}
        with zipfile.ZipFile(archive) as z:
            files = z.infolist()
            if len(files) > 300 or sum(f.file_size for f in files) > 250*1024*1024:
                raise ModelError("Unexpected runtime archive size.")
            for entry in files:
                name = PurePosixPath(entry.filename)
                if name.is_absolute() or ".." in name.parts or ":" in str(name) or "\\" in str(name) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ModelError("Unsafe runtime archive member.")
                if entry.is_dir(): continue
                data = z.read(entry)
                path = self.runtime_dir.joinpath(*name.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_symlink(): raise ModelError("Runtime symlink refused.")
                path.write_bytes(data)
                receipts[str(name)] = hashlib.sha256(data).hexdigest()
        atomic(self.root / "runtime-receipt.json", receipts)
        self.update(phase="downloaded", stage="SHA-256 verified", downloaded_bytes=MODEL_SIZE+RUNTIME_SIZE, speed_bps=0, error="")

    def verify(self):
        self.update(phase="verifying", stage="Model and runtime integrity", error="")
        if not self.model_path.is_file() or self.model_path.is_symlink() or digest(self.model_path) != MODEL_SHA:
            raise ModelError("Download and verify the pinned model first.")
        archive = self.root / f"llama-{RUNTIME_TAG}-cpu.zip"
        if not archive.is_file() or digest(archive) != RUNTIME_SHA:
            raise ModelError("Pinned runtime archive failed verification.")
        # Compare extracted bytes directly to the verified archive, not a mutable receipt alone.
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                if entry.is_dir(): continue
                path = self.runtime_dir / entry.filename
                if path.is_symlink() or not path.is_file() or digest(path) != hashlib.sha256(z.read(entry)).hexdigest():
                    raise ModelError("Extracted runtime integrity check failed.")
        self.check_cancel()

    def load(self, for_qualification=False):
        if not for_qualification and not self.qualified():
            raise ModelError("The narrator must pass qualification before it can be loaded for research.")
        if self.process and self.process.poll() is None and self.ready:
            return
        self.verify()
        executables = list(self.runtime_dir.rglob("llama-server.exe"))
        if len(executables) != 1: raise ModelError("Managed llama-server executable not found.")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        self.key = secrets.token_urlsafe(32)
        self.url = f"http://127.0.0.1:{port}"
        command = [str(executables[0]), "--model", str(self.model_path), "--alias", MODEL_ID, "--host", "127.0.0.1", "--port", str(port),
                   "--ctx-size", "4096", "--threads", "6", "--parallel", "1", "--n-gpu-layers", "0", "--reasoning", "off", "--jinja",
                   "--no-webui", "--api-key", self.key]
        env = {k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP","USERPROFILE","LOCALAPPDATA","APPDATA","COMSPEC","PROCESSOR_ARCHITECTURE"}}
        self.update(phase="loading", stage="Starting local CPU narrator", error="")
        with (self.root / "llama-server.log").open("a", encoding="utf-8") as log:
            self.process = subprocess.Popen(command, cwd=self.runtime_dir, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        for _ in range(180):
            self.check_cancel()
            if self.process.poll() is not None: raise ModelError("Local narrator exited. See the managed runtime log.")
            try:
                local_json(self.url + "/health", key=self.key, timeout=2)
                self.ready = True
                self.update(phase="qualifying" if for_qualification else "ready", stage="Local narrator loaded")
                return
            except (OSError, ValueError):
                time.sleep(.5)
        raise ModelError("Local narrator did not become ready within 90 seconds.")

    def complete(self, system, prompt, *, max_tokens=256):
        if not self.ready or not self.process or self.process.poll() is not None:
            raise ModelError("Local narrator is not loaded.")
        self.check_cancel()
        response = local_json(self.url + "/v1/chat/completions", {
            "model": MODEL_ID, "messages": [{"role":"system","content":system},{"role":"user","content":prompt}],
            "temperature": 0, "max_tokens": min(512,max_tokens), "stream": False,
            "chat_template_kwargs": {"enable_thinking": False}}, key=self.key)
        self.check_cancel()
        choices = response.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise ModelError("Narration was incomplete; rejected.")
        content = choices[0].get("message",{}).get("content")
        if not isinstance(content,str) or len(content)>16000:
            raise ModelError("Invalid narrator output.")
        return content

    @property
    def model_id(self):
        return "LiquidAI/LFM3-1.7B-GGUF:Q4_K_M@" + REVISION[:12]

    def qualify(self):
        from qualify.run import load_cases, run_cases, check_gates
        self.load(for_qualification=True)
        cases = [c for c in load_cases(str(self.repo/"qualify/cases.jsonl")) if c["category"] in {"narration","number_faithfulness"}]
        self.update(phase="qualifying", stage="Narrator smoke qualification", qualification=None)
        done = 0
        def call(body):
            nonlocal done
            self.check_cancel()
            result = local_json(self.url+"/v1/chat/completions", {**body,"chat_template_kwargs":{"enable_thinking":False}}, key=self.key)
            done += 1
            self.update(stage=f"Checked {done} of {len(cases)} qualification cases")
            return result
        report = run_cases(cases, call, MODEL_ID, max_tokens=256)
        gates = json.loads((self.repo/"qualify/gates_narrator.json").read_text())
        report["gates"] = check_gates(report,gates)
        report.update(fingerprint=self.fingerprint(), model_sha256=MODEL_SHA, revision=REVISION, runtime=RUNTIME_TAG,
                      quantization="Q4_K_M", thinking=False, evaluated_at=time.time(), passed=all(g["passed"] for g in report["gates"].values()))
        atomic(self.root/"qualification.json",report)
        self.update(qualification=report, phase="ready" if report["passed"] else "qualification_failed", stage="Smoke gates passed" if report["passed"] else "Narrator remains disabled")
        if not report["passed"]:
            self.ready = False
            if self.process and self.process.poll() is None:
                self.process.terminate(); self.process.wait(timeout=8)

    def select(self, model):
        if model not in {"facts",MODEL_ID}: raise ValueError("Unknown narrator choice.")
        if model != "facts" and not self.qualified(): raise ModelError("Narrator qualification has not passed.")
        if self.busy: raise ModelError("Wait for the current run before changing models.")
        self.update(selected=model, preferred=model, selection_note="")
        return self.snapshot()

    def unload(self, keep_choice=False):
        """Stop the narrator. A user unload also switches to tool-only; shutdown keeps the remembered choice."""
        if self.busy: raise ModelError("A research run is using the narrator.")
        self.ready = False
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=8)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=3)
        choice = {} if keep_choice else {"selected": "facts", "preferred": "facts", "selection_note": ""}
        self.update(phase="downloaded" if self.model_path.exists() else "not_downloaded", stage="Narrator unloaded", **choice)

    def close(self):
        self.cancel.set()
        self.busy = False
        self.unload(keep_choice=True)


def main():
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action",choices=["download","qualify","status"])
    args=ap.parse_args()
    repo=Path(__file__).resolve().parents[1]
    manager=LocalModels(repo/"data/local_models",repo)
    try:
        if args.action=="download":manager.download()
        elif args.action=="qualify":manager.qualify()
        print(json.dumps(manager.snapshot(),indent=2))
    finally:
        if manager.process:manager.close()


if __name__=="__main__":
    main()
