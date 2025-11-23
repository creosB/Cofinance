import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List


def execute_python(code: str, timeout_sec: int = 8) -> Dict[str, Any]:
    """
    Execute a Python snippet in an isolated subprocess with a soft timeout.

    Captures:
    - stdout/stderr
    - Plotly figures present as top-level variables (via to_json)

    Returns a dict with keys: ok (bool), stdout, stderr, figures (list of json strings), error (optional).
    """
    tmpdir = tempfile.mkdtemp(prefix="exec_")
    script_path = os.path.join(tmpdir, "runner.py")
    artifacts_path = os.path.join(tmpdir, "artifacts.json")

    # Simple indentation helper for embedding user code under try:
    def _indent_block(txt: str, spaces: int = 8) -> str:
        pad = " " * spaces
        return "\n".join(pad + line for line in txt.splitlines())

    def indent(txt: str) -> str:
        return _indent_block(txt)

    wrapper = f"""
import sys, json, io
import contextlib

code_stdout = io.StringIO()
code_stderr = io.StringIO()

@contextlib.contextmanager
def capture():
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = code_stdout, code_stderr
    try:
        yield
    finally:
        sys.stdout, sys.stderr = old_out, old_err

artifacts = {{"stdout": "", "stderr": "", "figures": []}}

with capture():
    try:
{indent(code)}
    except Exception:
        import traceback
        traceback.print_exc()

# Try to collect Plotly figures from globals
try:
    import plotly.io as pio
    figs = []
    for name, val in globals().items():
        try:
            # Plotly figures usually have to_plotly_json method
            if hasattr(val, "to_plotly_json"):
                figs.append(val)
        except Exception:
            pass
    if figs:
        for f in figs:
            try:
                artifacts["figures"].append(pio.to_json(f, pretty=False))
            except Exception:
                pass
except Exception:
    pass

artifacts["stdout"] = code_stdout.getvalue()
artifacts["stderr"] = code_stderr.getvalue()

json.dump(artifacts, open(r"{artifacts_path}", "w"))
"""

    def _write(path: str, data: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)

    # Write the runner script
    _write(script_path, wrapper)

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )

        # Prefer artifacts from inside the code; fall back to process-level
        if os.path.exists(artifacts_path):
            with open(artifacts_path, "r", encoding="utf-8") as f:
                artifacts = json.load(f)
        else:
            artifacts = {"stdout": proc.stdout, "stderr": proc.stderr, "figures": []}

        return {
            "ok": True,
            "stdout": artifacts.get("stdout", proc.stdout),
            "stderr": artifacts.get("stderr", proc.stderr),
            "figures": artifacts.get("figures", []),
            "tmpdir": tmpdir,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Execution timed out after {timeout_sec}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        # Best effort cleanup of temp dir; keep if desired for debugging
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass
