"""Windows Job Object child-process binding and safe Cofinance orphan cleanup."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

_JOB_LOCK = threading.Lock()
_JOB_HANDLE: int | None = None
_ALT_JOB_HANDLE: int | None = None
_JOB_FAILED = False
_ERROR_ACCESS_DENIED = 5
_SLOT_LOCK = threading.Lock()
_ACTIVE_SLOTS: dict[str, str] = {}
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102


def _create_kill_on_close_job() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC_LIMIT),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            kernel32.CloseHandle(job)
            return None
        return int(job)
    except Exception:
        return None


def _ensure_job(*, alternate: bool = False) -> int | None:
    global _JOB_HANDLE, _ALT_JOB_HANDLE, _JOB_FAILED
    if os.name != "nt":
        return None
    with _JOB_LOCK:
        if alternate:
            if _ALT_JOB_HANDLE is None:
                _ALT_JOB_HANDLE = _create_kill_on_close_job()
            return _ALT_JOB_HANDLE
        if _JOB_HANDLE is not None or _JOB_FAILED:
            return _JOB_HANDLE
        job = _create_kill_on_close_job()
        if job is None:
            _JOB_FAILED = True
            return None
        _JOB_HANDLE = job
        return _JOB_HANDLE


def bind_child(process: subprocess.Popen | None) -> bool:
    """Attach a child Popen to a process-wide kill-on-close Windows Job Object."""
    if os.name != "nt" or process is None:
        return False
    handle = getattr(process, "_handle", None)
    if not handle:
        return False
    job = _ensure_job()
    if not job:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        if kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(handle))):
            return True
        if ctypes.get_last_error() == _ERROR_ACCESS_DENIED:
            alt_job = _ensure_job(alternate=True)
            if alt_job and kernel32.AssignProcessToJobObject(wintypes.HANDLE(alt_job), wintypes.HANDLE(int(handle))):
                return True
        return False
    except Exception:
        return False


def memory_status() -> dict[str, int] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MEMORYSTATUSEX)]
        kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        mb = 1024 * 1024
        out = {
            "total_phys_mb": int(stat.ullTotalPhys // mb),
            "avail_phys_mb": int(stat.ullAvailPhys // mb),
            "avail_pagefile_mb": int(stat.ullAvailPageFile // mb),
            "memory_load_pct": int(stat.dwMemoryLoad),
        }
        try:
            class _PERFORMANCE_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("CommitTotal", ctypes.c_size_t),
                    ("CommitLimit", ctypes.c_size_t),
                    ("CommitPeak", ctypes.c_size_t),
                    ("PhysicalTotal", ctypes.c_size_t),
                    ("PhysicalAvailable", ctypes.c_size_t),
                    ("SystemCache", ctypes.c_size_t),
                    ("KernelTotal", ctypes.c_size_t),
                    ("KernelPaged", ctypes.c_size_t),
                    ("KernelNonpaged", ctypes.c_size_t),
                    ("PageSize", ctypes.c_size_t),
                    ("HandleCount", wintypes.DWORD),
                    ("ProcessCount", wintypes.DWORD),
                    ("ThreadCount", wintypes.DWORD),
                ]

            perf = _PERFORMANCE_INFORMATION()
            perf.cb = ctypes.sizeof(perf)
            kernel32.K32GetPerformanceInfo.argtypes = [ctypes.POINTER(_PERFORMANCE_INFORMATION), wintypes.DWORD]
            kernel32.K32GetPerformanceInfo.restype = wintypes.BOOL
            if kernel32.K32GetPerformanceInfo(ctypes.byref(perf), perf.cb) and perf.CommitLimit > 0:
                page = max(1, int(perf.PageSize))
                out["commit_total_mb"] = int((perf.CommitTotal * page) // mb)
                out["commit_limit_mb"] = int((perf.CommitLimit * page) // mb)
                out["commit_pct"] = int(round((perf.CommitTotal / perf.CommitLimit) * 100))
        except Exception:
            pass
        return out
    except Exception:
        return None


def require_memory_headroom(
    min_avail_phys_mb: int,
    label: str,
    *,
    max_commit_pct: int = 85,
    error_type=OSError,
) -> None:
    if os.environ.get("COFINANCE_SKIP_MEM_CHECK", "").strip() == "1":
        return
    status = memory_status()
    if status is None:
        return
    avail = status["avail_phys_mb"]
    if avail < int(min_avail_phys_mb):
        raise error_type(
            f"Not enough free RAM to start {label} ({avail} MB free; need at least {int(min_avail_phys_mb)} MB). "
            "Close other heavy apps or stop another local model before retrying."
        )
    commit_pct = status.get("commit_pct")
    if isinstance(commit_pct, int) and commit_pct >= int(max_commit_pct):
        raise error_type(
            f"System memory commit is too high to start {label} ({commit_pct}% committed; limit {int(max_commit_pct)}%). "
            "Close other heavy apps or stop another local model before retrying."
        )


def global_lock_dir() -> Path:
    override = os.environ.get("COFINANCE_LOCK_DIR", "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "Cofinance" / "locks"


def global_slot_lock(slot_name: str, label: str, *, error_type=OSError) -> "ProcessSlotLock":
    safe = re.sub(r"[^a-z0-9_-]+", "_", (slot_name or "runtime").strip().lower()).strip("_") or "runtime"
    lock_dir = global_lock_dir()
    dir_hash = hashlib.sha256(str(lock_dir.resolve()).lower().encode("utf-8")).hexdigest()[:12]
    mutex_name = f"Local\\Cofinance_{dir_hash}_{safe}"
    return ProcessSlotLock(lock_dir / f"{safe}.lock", label, error_type=error_type, mutex_name=mutex_name)


def _acquire_windows_mutex(mutex_name: str, label: str, error_type) -> int | None:
    if os.name != "nt" or not mutex_name:
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        return None
    wait_res = kernel32.WaitForSingleObject(handle, 0)
    if wait_res in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        return int(handle)
    kernel32.CloseHandle(handle)
    if wait_res == _WAIT_TIMEOUT:
        raise error_type(
            f"Another {label} instance is already running on this machine. "
            "Stop it before starting a second instance."
        )
    return None


def _release_windows_mutex(handle: int | None) -> None:
    if os.name != "nt" or not handle:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.ReleaseMutex(wintypes.HANDLE(int(handle)))
        kernel32.CloseHandle(wintypes.HANDLE(int(handle)))
    except Exception:
        pass


def pid_is_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return ctypes.get_last_error() == 5
            try:
                code = wintypes.DWORD(0)
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return int(code.value) == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class ProcessSlotLock:
    def __init__(self, lock_path: Path, label: str, *, error_type=OSError, mutex_name: str | None = None) -> None:
        self.path = Path(lock_path)
        self.label = label
        self.error_type = error_type
        self.mutex_name = mutex_name
        self.owner_id = uuid.uuid4().hex
        self.acquired = False
        self._mutex_handle: int | None = None

    def _key(self) -> str:
        return str(self.path.resolve()).lower()

    def acquire(self) -> "ProcessSlotLock":
        if self.acquired:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = self._key()
        payload = json.dumps(
            {"pid": os.getpid(), "owner": self.owner_id, "label": self.label, "started_at": time.time()},
            ensure_ascii=True,
        )
        with _SLOT_LOCK:
            if key in _ACTIVE_SLOTS:
                raise self.error_type(
                    f"Another {self.label} instance is already active in this process. Stop it before starting a second one."
                )
            mutex_handle = _acquire_windows_mutex(self.mutex_name, self.label, self.error_type) if self.mutex_name else None
            try:
                for _ in range(2):
                    try:
                        fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    except FileExistsError:
                        holder = self._read_holder()
                        holder_pid = holder.get("pid") if isinstance(holder, dict) else None
                        if isinstance(holder_pid, int) and pid_is_alive(holder_pid):
                            raise self.error_type(
                                f"Another {self.label} process (PID {holder_pid}) is already running. "
                                "Stop it before starting a second instance."
                            )
                        try:
                            self.path.unlink(missing_ok=True)
                        except OSError:
                            raise self.error_type(
                                f"{self.label} lock file is held by another process. Stop it before retrying."
                            )
                        continue
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as fh:
                            fh.write(payload)
                    except BaseException:
                        try:
                            self.path.unlink(missing_ok=True)
                        except OSError:
                            pass
                        raise
                    self._mutex_handle = mutex_handle
                    _ACTIVE_SLOTS[key] = self.owner_id
                    self.acquired = True
                    return self
                raise self.error_type(f"Could not acquire exclusive startup lock for {self.label}.")
            except BaseException:
                _release_windows_mutex(mutex_handle)
                raise

    def _read_holder(self) -> dict | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if not self.acquired:
            return
        key = self._key()
        with _SLOT_LOCK:
            if _ACTIVE_SLOTS.get(key) == self.owner_id:
                _ACTIVE_SLOTS.pop(key, None)
            holder = self._read_holder()
            if holder is None or holder.get("owner") == self.owner_id:
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    pass
            _release_windows_mutex(self._mutex_handle)
            self._mutex_handle = None
            self.acquired = False


def spawn_managed(command, *, error_type=OSError, **options) -> subprocess.Popen:
    process = subprocess.Popen(command, **options)
    if os.name != "nt" or bind_child(process):
        return process
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    raise error_type("Local worker could not be contained; startup stopped.")


def _is_cofinance_child(proc: dict, repo_str: str, data_str: str) -> bool:
    name = str(proc.get("Name") or "").lower()
    exe = str(proc.get("ExecutablePath") or "").lower()
    cmd = str(proc.get("CommandLine") or "").lower()
    if name == "llama-server.exe" and exe.startswith(data_str):
        return True
    if name.startswith("python") and (
        ("hymemory_worker.py" in cmd and repo_str in cmd)
        or ("core.router_worker" in cmd and repo_str in cmd)
    ):
        return True
    return False


def cleanup_orphaned_servers(repo: Path, *, include_live_parents: bool = False) -> list[int]:
    """Terminate only verified orphaned Cofinance llama-server / worker processes under repo."""
    if os.name != "nt":
        return []
    repo_root = Path(repo).resolve()
    repo_str = str(repo_root).lower()
    data_str = str((repo_root / "data").resolve()).lower() + os.sep.lower()
    ps = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        if not out:
            return []
        rows = json.loads(out)
        if isinstance(rows, dict):
            rows = [rows]
    except Exception:
        return []

    alive_pids = {int(r["ProcessId"]) for r in rows if isinstance(r, dict) and r.get("ProcessId") is not None}
    my_pid = os.getpid()
    killed: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("ProcessId")
        ppid = row.get("ParentProcessId")
        if pid is None or int(pid) == my_pid:
            continue
        if not _is_cofinance_child(row, repo_str, data_str):
            continue
        if not include_live_parents and ppid is not None and int(ppid) in alive_pids:
            continue
        res = subprocess.run(["taskkill", "/PID", str(int(pid)), "/F"], capture_output=True)
        if res.returncode == 0:
            killed.append(int(pid))
    return killed
