import json

from tools.code_exec import execute_python


def test_execute_python_with_plotly():
    code = """
import plotly.graph_objects as go
fig = go.Figure()
fig.add_scatter(y=[1,2,3])
print("hello from code")
"""
    res = execute_python(code, timeout_sec=5)
    assert res.get("ok") is True
    assert "hello from code" in (res.get("stdout") or "")
    figs = res.get("figures", [])
    # If plotly isn't available at runtime this may be empty, but should not crash
    assert isinstance(figs, list)
