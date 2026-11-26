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
COMMON_FLAGS = ("--ctx-size", "4096", "--parallel", "1", "--reasoning", "off", "--jinja")
# Speed lab 11/17 on the target PC (Ryzen 5 3600X, Radeon RX 5700 XT): full Vulkan offload reads prompts at about
# 1,500 tok/s and writes about 158 tok/s, versus 128 and 25 on the CPU. Same release, same model file. GPU first;
# the CPU build stays as the qualified fallback. Server flags are part of the qualification fingerprint, so any
# flag change forces a new qualification. "--device Vulkan0" makes a missing GPU fail loudly instead of silently
# running the Vulkan build on the CPU.
BACKENDS = {
    "vulkan": {"label": "Vulkan GPU", "archive": f"llama-{RUNTIME_TAG}-vulkan.zip", "dir": f"{RUNTIME_TAG}-vulkan",
               "url": f"https://github.com/ggml-org/llama.cpp/releases/download/{RUNTIME_TAG}/llama-{RUNTIME_TAG}-bin-win-vulkan-x64.zip",
               "sha": "9732a5e48887ca54d0bfe4af4324dc85fd56cd8cda19b52df3542fa6ebe6874f", "size": 37661146,
               "flags": ("--device", "Vulkan0", "--n-gpu-layers", "99", "--flash-attn", "on", "--threads", "6")},
    "cpu": {"label": "CPU", "archive": f"llama-{RUNTIME_TAG}-cpu.zip", "dir": RUNTIME_TAG, "url": RUNTIME_URL,
            "sha": RUNTIME_SHA, "size": RUNTIME_SIZE,
            "flags": ("--n-gpu-layers", "0", "--threads", "6", "--threads-batch", "12", "--flash-attn", "on")},
}
RUNTIMES_SIZE = sum(b["size"] for b in BACKENDS.values())

# Pinned narrator catalog. Only these files can be downloaded, verified, qualified and selected. AtomicChat's
# refusal-direction edit of LFM3 was approved by Baris on 2026-11-17 after a same-input comparison: equal on the
# smoke gates, shorter replies, headline judgments ranked dev outcomes better (AUC 0.625 vs 0.574, not confirmed
# on unseen data). Each narrator is qualified on its own: its SHA-256 is part of the fingerprint.
ATOMIC_ID = "lfm3-1.7b-atomic-ad-q4km"
ATOMIC_REVISION = "3ad712542eb9de349dbd28f2739b69c925cdec7d"
ATOMIC_FILE = "LFM3-1.7B-Uncensored-AD-Q4_K_M.gguf"
EXTRA_NARRATORS = {
    ATOMIC_ID: {"name": "LFM3 1.7B Uncensored", "publisher": "AtomicChat", "quantization": "AD-Q4_K_M",
                "repo": "AtomicChat/LFM3-1.7B-Uncensored-GGUF", "revision": ATOMIC_REVISION, "filename": ATOMIC_FILE,
                "sha": "b22df65673b54ed326f18f74826d76f3b65dadc237ffd6306cd52be04544827d", "size": 1132208000,
                "url": f"https://huggingface.co/AtomicChat/LFM3-1.7B-Uncensored-GGUF/resolve/{ATOMIC_REVISION}/{ATOMIC_FILE}",
                "license_url": f"https://huggingface.co/AtomicChat/LFM3-1.7B-Uncensored-GGUF/raw/{ATOMIC_REVISION}/LICENSE",
                "license_sha256": "4d28ca14dedc0b3d0fcc2b3339f0e79931faa33874f3d24f522183a8fc70068c",
                "license_file": "LICENSE-AtomicChat-LFM.txt",
                "source": "https://huggingface.co/AtomicChat/LFM3-1.7B-Uncensored-GGUF",
                "note": "Default narrator. Refusal-direction edit of LFM3 1.7B. Shorter replies; headline judgments ranked dev outcomes better than the base. Not confirmed on unseen data."},
}
# Baris, 2026-11-18: AtomicChat is the default. A fresh install downloads it; the base is an optional download from
# Models. Only the fallback changes: a remembered choice (base included) is restored as before, and the base keeps
# its fingerprint, so existing base qualifications stay valid.
DEFAULT_NARRATOR = ATOMIC_ID


def narrators():
    """The catalog, read at call time so tests can patch the base constants."""
    base = {"name": "LFM3 1.7B", "publisher": "Liquid AI", "quantization": "Q4_K_M", "repo": "LiquidAI/LFM3-1.7B-GGUF",
            "revision": REVISION, "filename": FILENAME, "sha": MODEL_SHA, "size": MODEL_SIZE, "url": MODEL_URL,
            "license_url": LICENSE_URL, "license_blob": LICENSE_BLOB, "license_file": "LICENSE-LFM.txt",
            "source": "https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF",
            "note": "Optional. The original LFM3 1.7B release; download it here if you prefer it to the default."}
    catalog = {MODEL_ID: base, **EXTRA_NARRATORS}
    return {DEFAULT_NARRATOR: catalog[DEFAULT_NARRATOR], **catalog}  # the default is listed first


def narrator_spec(model):
    catalog = narrators()
    if model not in catalog:
        raise ValueError("Unknown narrator choice.")
    return catalog[model]


def server_flags(backend):
    return [*COMMON_FLAGS, *BACKENDS[backend]["flags"]]


def server_command(executable, model_path, alias, port, key, backend, flags=None):
    """The one llama-server command line, shared by the live narrator and every benchmark. `flags` is only for the
    deep-think server; every other caller gets the qualified server_flags(backend)."""
    return [str(executable), "--model", str(model_path), "--alias", alias, "--host", "127.0.0.1", "--port", str(port),
            *(server_flags(backend) if flags is None else flags), "--no-webui", "--api-key", key]


def runtime_fingerprint(backend, model=None):
    """Everything that can change the narrator's output: weights, runtime build and server flags."""
    b = BACKENDS[backend]
    return "|".join([narrator_spec(model or MODEL_ID)["sha"], b["sha"], " ".join(server_flags(backend)), "greedy-thinking-off-v1"])


# Deep think chat switch (Baris chose "build" on 2026-11-19, after the think lab: smoke gates 1.0 with thinking, about
# 2.5 s instead of 0.4 s per answer, headline thinking rejected). A SECOND llama-server with thinking allowed, started
# only when a chat asks for deep think and used only for the Market Team Lead's answer. The qualified server keeps
# COMMON_FLAGS, so every frozen benchmark, headline judgment and existing fingerprint stays exactly as it was.
# Same budget and reasoning format as the think lab (docs/benchmarks/think-lab-protocol-v1.json).
THINK_BUDGET = 384
THINK_FORMAT = "deepseek"  # reasoning goes to message.reasoning_content; content holds only the answer


def thinking_server_flags(backend):
    flags = list(server_flags(backend))
    i = flags.index("--reasoning")
    flags[i + 1] = "on"
    return [*flags, "--reasoning-budget", str(THINK_BUDGET), "--reasoning-format", THINK_FORMAT]


def thinking_runtime_fingerprint(backend, model=None):
    """The deep-think server's own identity. Never equal to runtime_fingerprint, so one report cannot pass for the other."""
    b = BACKENDS[backend]
    return "|".join([narrator_spec(model or MODEL_ID)["sha"], b["sha"], " ".join(thinking_server_flags(backend)), "greedy-thinking-on-v1"])


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
    def __init__(self, root: Path, repo: Path, on_change=None):
        self.root, self.repo = Path(root), Path(repo)
        self._on_change = on_change
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.process = None
        self.loaded_model = None
        self.url = ""
        self.key = ""
        self.ready = False
        self.busy = False
        self.thinker = None  # deep-think server: {process, url, key, model, backend}; started only on request
        self.state = {"phase": "not_downloaded", "stage": "", "downloaded_bytes": 0,
                      "total_bytes": narrator_spec(DEFAULT_NARRATOR)["size"] + RUNTIMES_SIZE,
                      "speed_bps": 0, "error": "", "qualification": None, "selected": "facts", "router": "not_loaded",
                      "preferred": "facts", "selection_note": ""}
        path = self.root / "state.json"
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                for key in ("phase", "qualification", "qualifications", "think_qualifications", "backend", "downloaded_bytes", "preferred"):
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
        if preferred not in {"facts", *narrators()}:
            preferred = "facts"
        self.state["preferred"], self.state["selected"], self.state["selection_note"] = preferred, "facts", ""
        if preferred == "facts":
            return
        spec = narrator_spec(preferred)
        try:
            path = self.path_of(preferred)
            file_ok = path.is_file() and path.stat().st_size == spec["size"]
            still_qualified = self.qualified(model=preferred)
        except OSError:
            file_ok = still_qualified = False
        if not file_ok:
            self.state["selection_note"] = f"Last choice was {spec['name']}, but the model file is missing. Using tool-only."
        elif not still_qualified:
            self.state["selection_note"] = f"Last choice was {spec['name']}, but its qualification is out of date. Using tool-only."
        else:
            self.state["selected"] = preferred
            self.state["selection_note"] = "Restored from the last session. Loads on the next run."

    @property
    def narrator_id(self):
        """The narrator that loads, judges headlines and is shown: the selection, else the remembered preference."""
        catalog = narrators()
        for key in ("selected", "preferred"):
            if self.state.get(key) in catalog:
                return self.state[key]
        return DEFAULT_NARRATOR

    def path_of(self, model=None):
        return self.root / narrator_spec(model or self.narrator_id)["filename"]

    @property
    def model_path(self):
        return self.path_of()

    @property
    def runtime_dir(self):
        return self.backend_dir(self.active_backend)

    def backend_dir(self, backend):
        return self.root / BACKENDS[backend]["dir"]

    def installed_backends(self):
        """Runtimes whose pinned archive is on disk, fastest first."""
        return [b for b in BACKENDS if (self.root / BACKENDS[b]["archive"]).is_file()]

    @property
    def active_backend(self):
        chosen = self.state.get("backend")
        if chosen in BACKENDS:
            return chosen
        installed = self.installed_backends()
        return installed[0] if installed else "cpu"

    def update(self, **values):
        with self.lock:
            self.state.update(values)
            atomic(self.root / "state.json", self.state)
        cb = getattr(self, "_on_change", None)
        if cb:
            cb()

    def is_loaded(self, model=None):
        running = self.ready and self.process is not None and self.process.poll() is None
        return running and (model is None or self.loaded_model == model)

    def report_for(self, model):
        """Best qualification report of one narrator: a passing one on the fastest runtime, else any."""
        reports = [r for r in [self.state.get("qualification"), *(self.state.get("qualifications") or {}).values()]
                   if r and r.get("fingerprint") in {self.fingerprint(b, model) for b in BACKENDS}]
        return next((r for r in reports if r.get("passed")), reports[0] if reports else None)

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
            active = self.narrator_id
            spec = narrator_spec(active)
            listed = []
            for model, s in narrators().items():
                path = self.path_of(model)
                listed.append({"id": model, "name": s["name"], "publisher": s["publisher"], "quantization": s["quantization"],
                               "size_bytes": s["size"], "revision": s["revision"], "source": s["source"], "note": s["note"],
                               "downloaded": path.is_file() and path.stat().st_size == s["size"],
                               "qualified": self.qualified(model=model), "loaded": self.is_loaded(model), "active": model == active,
                               "default": model == DEFAULT_NARRATOR, "think_qualified": self.think_qualified(model=model)})
            result.update(id=active, name=spec["name"], quantization=spec["quantization"], size_bytes=spec["size"],
                          revision=spec["revision"], runtime="llama.cpp " + RUNTIME_TAG + " / " + BACKENDS[self.active_backend]["label"],
                          backend=self.active_backend, loaded=self.is_loaded(), loaded_model=self.loaded_model if self.is_loaded() else None,
                          license="LFM Open License v1.0", license_note="Commercial use is limited by the $10M annual-revenue threshold.",
                          source=spec["source"], router_model="fastino/GLiNER2.5-Decide", narrators=listed,
                          qualification=self.report_for(active), can_select=self.qualified(model=active), busy=self.busy,
                          think={"budget": THINK_BUDGET, "qualified": self.think_qualified(model=active),
                                 "loaded": self.thinker_loaded(active), "report": self.think_report(active)})
            result.pop("think_qualifications", None)  # full reports stay on disk; the view gets the summary above
            return result

    def qualified(self, backend=None, model=None):
        """With no backend: can any runtime narrate. A report counts only for the exact weights, build and flags,
        so one narrator's report never qualifies another."""
        model = model or self.narrator_id
        if backend is None:
            return any(self.qualified(b, model) for b in BACKENDS)
        reports = [self.state.get("qualification"), *(self.state.get("qualifications") or {}).values()]
        target = self.fingerprint(backend, model)
        return any(bool(q and q.get("passed") and q.get("fingerprint") == target) for q in reports)

    def fingerprint(self, backend=None, model=None):
        h = hashlib.sha256(runtime_fingerprint(backend or self.active_backend, model or self.narrator_id).encode())
        for name in ("qualify/cases.jsonl", "qualify/gates_narrator.json", "qualify/scorers.py"):
            h.update((self.repo / name).read_bytes())
        return h.hexdigest()

    # -- deep think (second server, Lead answer only) ------------------------------------------------------------------
    def think_fingerprint(self, backend=None, model=None):
        h = hashlib.sha256(thinking_runtime_fingerprint(backend or self.active_backend, model or self.narrator_id).encode())
        for name in ("qualify/cases.jsonl", "qualify/gates_narrator.json", "qualify/scorers.py"):
            h.update((self.repo / name).read_bytes())
        return h.hexdigest()

    def think_qualified(self, backend=None, model=None):
        """Deep think passed the frozen smoke gates for these exact weights, build and thinking flags. Separate from
        qualified(): a thinking-off report never enables deep think and a thinking report never enables the narrator."""
        model = model or self.narrator_id
        if backend is None:
            return any(self.think_qualified(b, model) for b in BACKENDS)
        target = self.think_fingerprint(backend, model)
        return any(bool(r and r.get("passed") and r.get("fingerprint") == target)
                   for r in (self.state.get("think_qualifications") or {}).values())

    def think_report(self, model):
        current = {self.think_fingerprint(b, model) for b in BACKENDS}
        reports = [r for r in (self.state.get("think_qualifications") or {}).values() if r and r.get("fingerprint") in current]
        best = next((r for r in reports if r.get("passed")), reports[0] if reports else None)
        if not best:
            return None
        return {k: best.get(k) for k in ("passed", "n", "gates", "backend", "evaluated_at", "budget", "error")}

    def thinker_loaded(self, model=None, backend=None):
        t = self.thinker
        return bool(t and t["process"].poll() is None and (model is None or t["model"] == model)
                    and (backend is None or t["backend"] == backend))

    def launch_task(self, action, model=None):
        if action not in {"download", "qualify", "qualify_thinking", "load", "unload", "pause"}:
            raise ValueError("Unknown model action.")
        if model is not None:
            narrator_spec(model)
            if action not in {"download", "qualify", "qualify_thinking"}:
                raise ValueError("Only download and qualification take a narrator; load uses the selected one.")
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
                    if action == "download": self.download(model)
                    elif action == "qualify": self.qualify(model)
                    elif action == "qualify_thinking": self.qualify_thinking(model)
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

    def download(self, model=None):
        model = model or self.narrator_id
        spec, path = narrator_spec(model), self.path_of(model)
        if self.process and self.process.poll() is None:
            raise ModelError("Unload the narrator before checking downloads.")
        if shutil.disk_usage(self.root).free < spec["size"] + 512*1024*1024 and not path.exists():
            raise ModelError("At least 1.6 GB of free disk is required.")
        self.update(phase="downloading", stage="License", error="", total_bytes=spec["size"] + RUNTIMES_SIZE)
        with public_open(spec["license_url"]) as response:
            license_data = response.read(20001)
        if "license_blob" in spec:
            ok = hashlib.sha1(b"blob " + str(len(license_data)).encode() + b"\0" + license_data).hexdigest() == spec["license_blob"]
        else:
            ok = len(license_data) <= 20000 and hashlib.sha256(license_data).hexdigest() == spec["license_sha256"]
        if not ok:
            raise ModelError("Pinned license verification failed.")
        (self.root / spec["license_file"]).write_bytes(license_data)
        base = 0
        for b in BACKENDS.values():
            self.fetch(b["url"], self.root / b["archive"], b["size"], b["sha"], base)
            base += b["size"]
        self.fetch(spec["url"], path, spec["size"], spec["sha"], base)
        self.update(phase="verifying", stage="Runtime files")
        receipts = {name: self.extract(name) for name in BACKENDS}
        atomic(self.root / "runtime-receipt.json", receipts)
        self.update(phase="downloaded", stage=spec["name"] + ": SHA-256 verified", downloaded_bytes=spec["size"]+RUNTIMES_SIZE, speed_bps=0, error="")

    def extract(self, backend):
        archive, target, receipts = self.root / BACKENDS[backend]["archive"], self.backend_dir(backend), {}
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
                path = target.joinpath(*name.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_symlink(): raise ModelError("Runtime symlink refused.")
                path.write_bytes(data)
                receipts[str(name)] = hashlib.sha256(data).hexdigest()
        return receipts

    def verify(self, backend=None, model=None):
        backend = backend or self.active_backend
        path, sha = self.path_of(model), narrator_spec(model or self.narrator_id)["sha"]
        self.update(phase="verifying", stage="Model and runtime integrity", error="")
        if not path.is_file() or path.is_symlink() or digest(path) != sha:
            raise ModelError("Download and verify the pinned model first.")
        archive = self.root / BACKENDS[backend]["archive"]
        if not archive.is_file() or digest(archive) != BACKENDS[backend]["sha"]:
            raise ModelError("Pinned runtime archive failed verification.")
        # Compare extracted bytes directly to the verified archive, not a mutable receipt alone.
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                if entry.is_dir(): continue
                path = self.backend_dir(backend) / entry.filename
                if path.is_symlink() or not path.is_file() or digest(path) != hashlib.sha256(z.read(entry)).hexdigest():
                    raise ModelError("Extracted runtime integrity check failed.")
        self.check_cancel()

    def stop_process(self):
        self.stop_thinker()  # deep think never outlives the narrator it belongs to
        self.ready = False
        self.loaded_model = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=8)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=3)

    def stop_thinker(self):
        t, self.thinker = self.thinker, None
        if t and t["process"].poll() is None:
            t["process"].terminate()
            try: t["process"].wait(timeout=8)
            except subprocess.TimeoutExpired: t["process"].kill(); t["process"].wait(timeout=3)

    def load_thinker(self, for_qualification=False, backend=None, model=None):
        """Start the deep-think server next to the qualified one, on the same runtime when the narrator is loaded.
        Refused unless deep think passed qualification for these exact weights, build and flags (or while qualifying)."""
        model = model or self.narrator_id
        if backend is None:
            loaded = self.state.get("backend") if self.is_loaded(model) else None
            backend = loaded if loaded and self.think_qualified(loaded, model) else next(
                (b for b in self.installed_backends() if self.think_qualified(b, model)), None)
        if backend is None or (not for_qualification and not self.think_qualified(backend, model)):
            raise ModelError("Deep think has not passed qualification for this narrator on this runtime.")
        if self.thinker_loaded(model, backend):
            return
        self.stop_thinker()
        if not (self.is_loaded(model) and self.state.get("backend") == backend):
            self.verify(backend, model)  # the loaded narrator's file and runtime were verified when it started
        executables = list(self.backend_dir(backend).rglob("llama-server.exe"))
        if len(executables) != 1: raise ModelError("Managed llama-server executable not found.")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        key, url = secrets.token_urlsafe(32), f"http://127.0.0.1:{port}"
        command = server_command(executables[0], self.path_of(model), model, port, key, backend, flags=thinking_server_flags(backend))
        env = {k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP","USERPROFILE","LOCALAPPDATA","APPDATA","COMSPEC","PROCESSOR_ARCHITECTURE"}}
        with (self.root / "llama-server-think.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=self.backend_dir(backend), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.thinker = {"process": process, "url": url, "key": key, "model": model, "backend": backend}
        try:
            for _ in range(180):
                self.check_cancel()
                if process.poll() is not None: raise ModelError("Deep think runtime exited. See the managed runtime log.")
                try:
                    local_json(url + "/health", key=key, timeout=2)
                    return
                except (OSError, ValueError):
                    time.sleep(.5)
            raise ModelError("Deep think runtime did not become ready within 90 seconds.")
        except BaseException:
            self.stop_thinker()
            raise

    def load(self, for_qualification=False, backend=None, model=None):
        """Start the fastest qualified runtime; if it cannot start (for example no GPU), fall back to the next one.
        A different narrator still running is stopped first, so two models never share the GPU unnoticed."""
        model = model or self.narrator_id
        if not for_qualification and not self.qualified(model=model):
            raise ModelError("The narrator must pass qualification before it can be loaded for research.")
        if self.is_loaded(model) and backend in (None, self.state.get("backend")):
            return
        if self.process and self.process.poll() is None:
            self.stop_process()
        order = [backend] if backend else [b for b in self.installed_backends() if self.qualified(b, model)]
        failures = []
        for name in order:
            try:
                self.start(name, for_qualification, failures, model=model)
                return
            except Cancelled:
                self.stop_process()
                raise
            except ModelError as exc:
                self.stop_process()
                failures.append(f"{BACKENDS[name]['label']}: {exc}")
        raise ModelError("; ".join(failures) or "No qualified runtime is installed.")

    def start(self, backend, for_qualification, failures, model=None):
        model = model or self.narrator_id
        self.verify(backend, model)
        executables = list(self.backend_dir(backend).rglob("llama-server.exe"))
        if len(executables) != 1: raise ModelError("Managed llama-server executable not found.")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        self.key = secrets.token_urlsafe(32)
        self.url = f"http://127.0.0.1:{port}"
        command = server_command(executables[0], self.path_of(model), model, port, self.key, backend)
        env = {k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP","USERPROFILE","LOCALAPPDATA","APPDATA","COMSPEC","PROCESSOR_ARCHITECTURE"}}
        label = BACKENDS[backend]["label"]
        self.update(phase="loading", stage=f"Starting local {label} narrator", error="")
        with (self.root / "llama-server.log").open("a", encoding="utf-8") as log:
            self.process = subprocess.Popen(command, cwd=self.backend_dir(backend), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        for _ in range(180):
            self.check_cancel()
            if self.process.poll() is not None: raise ModelError("Local narrator exited. See the managed runtime log.")
            try:
                local_json(self.url + "/health", key=self.key, timeout=2)
                self.ready = True
                self.loaded_model = model
                note = ("Fell back to " + label + " (" + "; ".join(failures) + ")") if failures else ""
                self.update(phase="qualifying" if for_qualification else "ready", backend=backend,
                            stage=f"Local narrator loaded on {label}", runtime_note=note)
                return
            except (OSError, ValueError):
                time.sleep(.5)
        raise ModelError("Local narrator did not become ready within 90 seconds.")

    def complete(self, system, prompt, *, max_tokens=256, stop=None, think=False):
        """think=True (deep think, Lead answer only) goes to the deep-think server with THINK_BUDGET more tokens; the
        reasoning text stays in reasoning_content and is never returned, stored or shown. Same rejection rules."""
        if not self.ready or not self.process or self.process.poll() is not None:
            raise ModelError("Local narrator is not loaded.")
        self.check_cancel()
        body = {
            "model": self.loaded_model, "messages": [{"role":"system","content":system},{"role":"user","content":prompt}],
            "temperature": 0, "max_tokens": min(512,max_tokens), "stream": False,
            "chat_template_kwargs": {"enable_thinking": False}}
        if stop:
            body["stop"] = list(stop)
        url, key, timeout = self.url, self.key, 90
        if think:
            t = self.thinker
            if not (self.thinker_loaded(self.loaded_model) and self.think_qualified(t["backend"], t["model"])):
                raise ModelError("Deep think is not loaded.")
            url, key, timeout = t["url"], t["key"], 180
            body.update(max_tokens=body["max_tokens"] + THINK_BUDGET, chat_template_kwargs={"enable_thinking": True})
        response = local_json(url + "/v1/chat/completions", body, key=key, timeout=timeout)
        from .usage import record_usage
        record_usage(response.get("usage"))  # include generated text even if a guard or cancellation rejects it
        self.check_cancel()
        choices = response.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise ModelError("Narration was incomplete; rejected.")
        content = choices[0].get("message",{}).get("content")
        if not isinstance(content,str) or len(content)>16000:
            raise ModelError("Invalid narrator output.")
        return content

    def choice(self, system, prompt, *, max_tokens=24, response_format=None, top_logprobs=0, timeout=30):
        """A short structured call that returns the runtime's choice (with label log-probabilities when asked).
        Same request fields as the benchmark runtime, so live and lab readouts match."""
        if not self.ready or not self.process or self.process.poll() is not None:
            raise ModelError("Local narrator is not loaded.")
        self.check_cancel()
        body = {"model": self.loaded_model, "messages": [{"role":"system","content":system},{"role":"user","content":prompt}],
                "temperature": 0, "max_tokens": min(64, int(max_tokens)), "chat_template_kwargs": {"enable_thinking": False}}
        if response_format:
            body["response_format"] = response_format
        if top_logprobs:
            body["logprobs"] = True
            body["top_logprobs"] = int(top_logprobs)
        response = local_json(self.url + "/v1/chat/completions", body, key=self.key, timeout=timeout)
        choices = response.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise ModelError("Structured reply was incomplete; rejected.")
        return choices[0]

    @property
    def model_id(self):
        """Recorded in traces and headline judgments: repository, quantization and pinned revision."""
        spec = narrator_spec(self.loaded_model or self.narrator_id)
        return f"{spec['repo']}:{spec['quantization']}@{spec['revision'][:12]}"

    def qualify(self, model=None):
        """Qualify one narrator on every installed runtime with the same frozen gates: CPU fallback first, GPU last so
        it stays loaded. Other narrators' reports are kept; legacy reports without a model belong to the base."""
        from qualify.run import load_cases, run_cases, check_gates
        model = model or self.narrator_id
        spec = narrator_spec(model)
        installed = self.installed_backends()
        if not installed:
            raise ModelError("Download and verify the pinned model first.")
        cases = [c for c in load_cases(str(self.repo/"qualify/cases.jsonl")) if c["category"] in {"narration","number_faithfulness"}]
        gates = json.loads((self.repo/"qualify/gates_narrator.json").read_text())
        kept = {k: v for k, v in (self.state.get("qualifications") or {}).items() if (v or {}).get("model", MODEL_ID) != model}
        reports = {}
        self.update(qualifications=kept, **({"qualification": None} if model == MODEL_ID else {}))
        for backend in reversed(installed):
            label = BACKENDS[backend]["label"]
            self.stop_process()
            try:
                self.load(for_qualification=True, backend=backend, model=model)
            except Cancelled:
                raise
            except ModelError as exc:
                reports[backend] = {"passed": False, "backend": backend, "model": model, "error": str(exc)[:250],
                                    "fingerprint": self.fingerprint(backend, model)}
                continue
            self.update(phase="qualifying", stage=f"Narrator smoke qualification on {label}")
            done = 0
            def call(body):
                nonlocal done
                self.check_cancel()
                result = local_json(self.url+"/v1/chat/completions", {**body,"chat_template_kwargs":{"enable_thinking":False}}, key=self.key)
                done += 1
                self.update(stage=f"{label}: checked {done} of {len(cases)} qualification cases")
                return result
            report = run_cases(cases, call, model, max_tokens=256)
            report["gates"] = check_gates(report,gates)
            report.update(fingerprint=self.fingerprint(backend, model), backend=backend, flags=server_flags(backend), model=model,
                          model_sha256=spec["sha"], revision=spec["revision"], runtime=f"{RUNTIME_TAG} {label}", quantization=spec["quantization"],
                          thinking=False, evaluated_at=time.time(), passed=all(g["passed"] for g in report["gates"].values()))
            reports[backend] = report
            self.update(qualifications={**kept, **{f"{model}:{b}": r for b, r in reports.items()}})
        atomic(self.root/"qualification.json", {**kept, **{f"{model}:{b}": r for b, r in reports.items()}})
        best = next((reports[b] for b in BACKENDS if reports.get(b, {}).get("passed")), None)
        legacy = {"qualification": best} if model == MODEL_ID else {}
        loaded = self.state.get("backend")
        if self.ready and self.loaded_model == model and reports.get(loaded, {}).get("passed"):
            self.update(phase="ready", stage=f"{spec['name']}: smoke gates passed on {BACKENDS[loaded]['label']}", **legacy)
        else:
            self.stop_process()
            self.update(phase="downloaded" if best else "qualification_failed",
                        stage=f"{spec['name']}: smoke gates passed; loads on the next run" if best else f"{spec['name']} remains disabled", **legacy)

    def qualify_thinking(self, model=None):
        """Smoke-qualify deep think for one narrator on every runtime where the narrator itself is qualified: the same
        frozen cases and gates as qualify(), thinking on, answer budget raised by THINK_BUDGET. The deep-think server is
        stopped afterwards; a chat starts it again when asked. Other narrators' reports are kept."""
        from qualify.run import load_cases, run_cases, check_gates
        model = model or self.narrator_id
        spec = narrator_spec(model)
        backends = [b for b in self.installed_backends() if self.qualified(b, model)]
        if not backends:
            raise ModelError("Qualify the narrator itself before deep think.")
        phase = self.state.get("phase")  # verify() marks the whole model as verifying; testing deep think must not leave it so
        cases = [c for c in load_cases(str(self.repo/"qualify/cases.jsonl")) if c["category"] in {"narration","number_faithfulness"}]
        gates = json.loads((self.repo/"qualify/gates_narrator.json").read_text())
        kept = {k: v for k, v in (self.state.get("think_qualifications") or {}).items() if (v or {}).get("model") != model}
        reports = {}
        self.update(think_qualifications=kept)
        for backend in backends:
            label = BACKENDS[backend]["label"]
            self.update(stage=f"Starting deep think on {label}")
            try:
                self.load_thinker(for_qualification=True, backend=backend, model=model)
            except Cancelled:
                raise
            except ModelError as exc:
                reports[backend] = {"passed": False, "backend": backend, "model": model, "error": str(exc)[:250], "thinking": True,
                                    "budget": THINK_BUDGET, "fingerprint": self.think_fingerprint(backend, model)}
                continue
            t, done = self.thinker, 0
            def call(body):
                nonlocal done
                self.check_cancel()
                result = local_json(t["url"]+"/v1/chat/completions", {**body, "max_tokens": int(body.get("max_tokens", 256)) + THINK_BUDGET,
                                    "chat_template_kwargs": {"enable_thinking": True}}, key=t["key"], timeout=180)
                done += 1
                self.update(stage=f"Deep think on {label}: checked {done} of {len(cases)} qualification cases")
                return result
            try:
                report = run_cases(cases, call, model, max_tokens=256)
            finally:
                self.stop_thinker()
            report["gates"] = check_gates(report, gates)
            report.update(fingerprint=self.think_fingerprint(backend, model), backend=backend, flags=thinking_server_flags(backend), model=model,
                          model_sha256=spec["sha"], revision=spec["revision"], runtime=f"{RUNTIME_TAG} {label}", quantization=spec["quantization"],
                          thinking=True, budget=THINK_BUDGET, evaluated_at=time.time(), passed=all(g["passed"] for g in report["gates"].values()))
            reports[backend] = report
            self.update(think_qualifications={**kept, **{f"{model}:{b}": r for b, r in reports.items()}})
        atomic(self.root/"think_qualification.json", {**kept, **{f"{model}:{b}": r for b, r in reports.items()}})
        passed = [BACKENDS[b]["label"] for b, r in reports.items() if r.get("passed")]
        self.update(think_qualifications={**kept, **{f"{model}:{b}": r for b, r in reports.items()}}, phase=phase,
                    stage=f"{spec['name']}: deep think passed on " + ", ".join(passed) if passed else f"{spec['name']}: deep think did not pass, so it stays off")

    def select(self, model):
        if model not in {"facts", *narrators()}: raise ValueError("Unknown narrator choice.")
        if model != "facts" and not self.qualified(model=model): raise ModelError("Narrator qualification has not passed.")
        if self.busy: raise ModelError("Wait for the current run before changing models.")
        if self.is_loaded() and model not in ("facts", self.loaded_model):
            self.stop_process()  # the next run loads the chosen narrator; never answer with the previous one
            self.update(phase="downloaded", stage="Previous narrator unloaded")
        self.update(selected=model, preferred=model, selection_note="")
        return self.snapshot()

    def unload(self, keep_choice=False):
        """Stop the narrator. A user unload also switches to tool-only; shutdown keeps the remembered choice."""
        if self.busy: raise ModelError("A research run is using the narrator.")
        self.stop_thinker()
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
    ap.add_argument("action",choices=["download","qualify","qualify_thinking","status"])
    ap.add_argument("--model",choices=sorted(narrators()))
    args=ap.parse_args()
    repo=Path(__file__).resolve().parents[1]
    manager=LocalModels(repo/"data/local_models",repo)
    try:
        if args.action=="download":manager.download(args.model)
        elif args.action=="qualify":manager.qualify(args.model)
        elif args.action=="qualify_thinking":manager.qualify_thinking(args.model)
        print(json.dumps(manager.snapshot(),indent=2))
    finally:
        if manager.process:manager.close()


if __name__=="__main__":
    main()
