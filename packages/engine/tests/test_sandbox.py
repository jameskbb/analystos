from __future__ import annotations

import base64
import sys

import pandas as pd
import pytest
from analystos_engine.sandbox import SandboxError, run_python
from analystos_engine.types import QueryColumn, QueryResult

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="sandbox isolation layers are Linux-specific"
)


def test_basic_dataframes_figures_and_inputs():
    inp = QueryResult(
        columns=[QueryColumn(name="region", type="VARCHAR"), QueryColumn(name="revenue", type="DOUBLE")],
        rows=[["Dallas", 100.0], ["Austin", 50.0], ["Dallas", 25.0]],
        row_count=3,
    )
    code = """
import numpy as np
summary = sales.groupby('region', as_index=False)['revenue'].sum().sort_values('region')
total = float(summary['revenue'].sum())
via_sql = con.execute("SELECT region, sum(revenue) AS r FROM sales GROUP BY 1 ORDER BY 1").df()
fig, ax = plt.subplots()
ax.bar(summary['region'], summary['revenue'])
print('total', total)
summary
"""
    r = run_python(code, {"sales": inp}, timeout_s=30)
    assert r.ok, r.error
    assert r.stdout.strip() == "total 175.0"
    assert r.dataframes["summary"].to_records() == [
        {"region": "Austin", "revenue": 50.0},
        {"region": "Dallas", "revenue": 125.0},
    ]
    assert r.dataframes["via_sql"].row_count == 2
    assert "result" in r.dataframes
    assert "sales" not in r.dataframes  # unchanged inputs are not echoed back
    assert r.variables["total"] == "175.0"
    assert len(r.figures) == 1 and base64.b64decode(r.figures[0])[:4] == b"\x89PNG"
    assert r.isolation.get("network_namespace") is True
    assert str(r.isolation.get("landlock", "")).startswith("enforced")
    assert r.isolation.get("audit_hook") is True


def test_scientific_stack_available():
    code = """
import scipy.stats as st
import statsmodels.api as sm
from sklearn.cluster import KMeans
import polars as pl
x = np.arange(20.0)
res = sm.OLS(2 * x + 1, sm.add_constant(x)).fit()
coef = float(res.params[1])
km = KMeans(n_clusters=2, n_init=2, random_state=0).fit(np.array([[0.0], [0.1], [5.0], [5.1]]))
p = float(st.ttest_ind([1, 2, 3, 4], [2, 3, 4, 5]).pvalue)
frame = pl.DataFrame({"a": [1, 2]})
"""
    r = run_python(code, timeout_s=60)
    assert r.ok, (r.error, r.traceback)
    assert float(r.variables["coef"]) == pytest.approx(2.0)
    assert "frame" in r.dataframes


def test_user_error_reported_with_traceback():
    r = run_python("x = 1\ny = 1 / 0\n")
    assert not r.ok and r.error_type == "ZeroDivisionError"
    assert "line 2" in r.traceback
    s = run_python("def (:\n")
    assert not s.ok and s.error_type == "SyntaxError"


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        ("import os\nos.system('echo pwned')", "not allowed"),
        ("import subprocess\nsubprocess.run(['id'])", "not allowed"),
        ("import socket", "not allowed"),
        ("import ctypes", "not allowed"),
        ("import importlib", "not allowed"),
        ("open('/etc/passwd').read()", "blocked"),
        ("open('/home/x.txt', 'w').write('x')", "blocked"),
        ("pd.io.common.os.system('echo pwned')", "blocked"),
        ("pd.read_csv('/etc/passwd')", "blocked"),
        ("np.loadtxt('/etc/passwd', dtype=str)", "blocked"),
        ("pd.io.common.os.listdir('/home')", "blocked"),
        ("pd.io.common.os.fork()", "blocked"),
        ("__import__('os')", "not allowed"),
        ("from . import x", "relative"),
    ],
)
def test_escape_attempts_blocked(code, fragment):
    r = run_python(code, timeout_s=20)
    assert not r.ok
    assert fragment in (r.error or "").lower(), r.error
    assert "pwned" not in r.stdout


def test_subclass_walk_to_os_still_blocked():
    code = """
os_mod = [c for c in ().__class__.__base__.__subclasses__() if c.__name__ == '_wrap_close'][0].__init__.__globals__['sys'].modules['os']
os_mod.system('echo pwned')
"""
    r = run_python(code)
    assert not r.ok and "blocked" in r.error
    assert "pwned" not in r.stdout


def test_polars_native_file_read_blocked_by_kernel():
    r = run_python("import polars as pl\npl.read_csv('/etc/passwd')")
    assert not r.ok  # Rust-level open is denied by Landlock (PermissionError), not only by the audit hook


def test_network_blocked():
    code = "sock = pd.io.common.socket if hasattr(pd.io.common, 'socket') else None\nimport urllib\n"
    r = run_python(code)
    assert not r.ok
    r2 = run_python("pd.read_csv('http://example.com/data.csv')")
    assert not r2.ok


def test_infinite_loop_times_out():
    r = run_python("while True:\n    pass\n", timeout_s=1.5)
    assert r.timed_out and not r.ok and r.error_type == "TimeoutError"


def test_timeout_cannot_be_swallowed():
    code = """
while True:
    try:
        while True:
            pass
    except BaseException:
        pass
"""
    r = run_python(code, timeout_s=1.0)
    assert r.timed_out and not r.ok


def test_memory_limit():
    r = run_python("x = np.ones(400_000_000)\n", mem_mb=300, timeout_s=20)
    assert not r.ok and r.memory_exceeded


def test_scratch_writes_allowed_and_output_capped():
    code = "open('notes.txt', 'w').write('hello')\nprint(open('notes.txt').read())\nprint('x' * 5000)\n"
    r = run_python(code, max_output_bytes=100)
    assert r.ok, r.error
    assert r.stdout.startswith("hello") and r.output_truncated


def test_row_cap_and_input_types():
    code = "big = pd.DataFrame({'a': range(50)})\n"
    r = run_python(
        code,
        {"p": pd.DataFrame({"x": [1]}), "rec": [{"y": 2}], "d": {"z": [3]}},
        max_rows=10,
    )
    assert r.ok and r.dataframes["big"].row_count == 10 and r.dataframes["big"].truncated


def test_secrets_not_in_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    monkeypatch.setenv("AOS_SECRET_KEY", "another-secret")
    r = run_python("import os")  # os import blocked; check via pandas' reference instead
    assert not r.ok
    r = run_python("env = dict(pd.io.common.os.environ)\nkeys = ','.join(sorted(env))")
    assert r.ok, r.error
    assert "ANTHROPIC_API_KEY" not in r.variables["keys"] and "AOS_SECRET_KEY" not in r.variables["keys"]


def test_invalid_arguments():
    with pytest.raises(SandboxError):
        run_python("1", {"bad name": []})
    with pytest.raises(SandboxError):
        run_python("1", {"con": []})
    with pytest.raises(SandboxError):
        run_python("1", timeout_s=0)
    with pytest.raises(SandboxError):
        run_python(123)  # type: ignore[arg-type]
