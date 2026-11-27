"""Windows Job Object child-process binding and safe Cofinance orphan cleanup."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

_JOB_LOCK = threading.Lock()
_JOB_HANDLE: int | None = None
_JOB_FAILED = False


def _ensure_job() -> int | None:
    global _JOB_HANDLE, _JOB_FAILED
    if os.name != "nt":
        return None
    with _JOB_LOCK:
        if _JOB_HANDLE is not None or _JOB_FAILED:
            return _JOB_HANDLE
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
                _JOB_FAILED = True
                return None
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
            info = _EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                kernel32.CloseHandle(job)
                _JOB_FAILED = True
                return None
            _JOB_HANDLE = int(job)
            return _JOB_HANDLE
        except Exception:
            _JOB_FAILED = True
            return None


def bind_child(process: subprocess.Popen | None) -> bool:
    """Attach a child Popen to the process-wide kill-on-close Windows Job Object."""
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
        return bool(kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(handle))))
    except Exception:
        return False


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
