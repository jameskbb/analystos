"""Sandboxed Python execution for analysis cells.

``run_python(code, inputs)`` runs ``code`` in a fresh worker subprocess
(:mod:`analystos_engine._sandbox_worker`) and returns stdout/stderr, errors, the
DataFrames the code produced (as :class:`~analystos_engine.types.QueryResult`) and
matplotlib figures (PNG, base64).

What the user code gets
    pandas/polars/numpy/scipy/statsmodels/scikit-learn/matplotlib, each input as a pandas
    DataFrame variable, and ``con``: an in-memory DuckDB connection with the inputs
    registered as tables (external access disabled). The workspace warehouse file is never
    opened by the sandbox.

Isolation (Linux; each layer is reported in ``PythonResult.isolation``)
    * separate process with a clean environment (no API keys or secrets), killed as a
      process group on timeout,
    * network namespace: no network interfaces at all,
    * Landlock: read-only access to the Python installation and system libraries,
      read-write access only to a per-run scratch directory, no ``execve`` of any file,
      no TCP connect/bind,
    * rlimits: CPU seconds, file size, open files, no core dumps, an address-space backstop,
      plus a resident-memory watchdog in the parent that enforces ``mem_mb``,
    * an audit hook blocking process creation, sockets, native libraries outside the Python
      installation and file access outside the allowed roots, and an import allowlist.

Limits, stated honestly: this is not a virtual machine. The kernel layers depend on the
host allowing unprivileged user namespaces and Landlock (both are checked at run time and
reported); in-process Python restrictions are defence in depth only. Run the API inside a
container for multi-tenant deployments.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .types import QueryResult

__all__ = ["PythonResult", "run_python", "SandboxError", "DEFAULT_TIMEOUT_S", "DEFAULT_MEM_MB"]

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MEM_MB = 1024
_STARTUP_GRACE_S = 20.0


class SandboxError(RuntimeError):
    """The sandbox could not be started (not raised for errors in user code)."""


class PythonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    dataframes: dict[str, QueryResult] = Field(default_factory=dict)
    figures: list[str] = Field(default_factory=list, description="PNG images, base64 encoded")
    result_repr: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    elapsed_ms: float = 0.0
    timed_out: bool = False
    memory_exceeded: bool = False
    output_truncated: bool = False
    exit_code: int | None = None
    isolation: dict[str, Any] = Field(default_factory=dict)


def _to_arrow(value: Any) -> Any:
    import pyarrow as pa

    if isinstance(value, QueryResult):
        df = value.to_pandas()
        return pa.Table.from_pandas(df, preserve_index=False)
    if isinstance(value, pa.Table):
        return value
    mod = type(value).__module__
    if mod.startswith("polars"):
        return value.to_arrow()
    if mod.startswith("pandas"):
        import pandas as pd

        if isinstance(value, pd.Series):
            value = value.to_frame()
        return pa.Table.from_pandas(value, preserve_index=False)
    if isinstance(value, list):
        return pa.Table.from_pylist(value)
    if isinstance(value, dict):
        return pa.Table.from_pydict(value)
    raise SandboxError(f"unsupported input type {type(value).__name__}")


def _clean_env(scratch: str) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": scratch,
        "TMPDIR": scratch,
        "MPLCONFIGDIR": os.path.join(scratch, ".mpl"),
        "MPLBACKEND": "Agg",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_MAX_THREADS": "1",
        "POLARS_MAX_THREADS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _rss_mb(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        return None
    return None


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


def run_python(
    code: str,
    inputs: dict[str, Any] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    mem_mb: int = DEFAULT_MEM_MB,
    *,
    max_rows: int = 10_000,
    max_output_bytes: int = 1_000_000,
    scratch_root: str | Path | None = None,
    keep_scratch: bool = False,
) -> PythonResult:
    """Run ``code`` in the sandbox with ``inputs`` exposed as DataFrame variables.

    ``inputs`` maps identifier names to QueryResult / pandas / polars / pyarrow / records.
    ``timeout_s`` bounds the user code's wall time, ``mem_mb`` its resident memory.
    Errors in the user code are returned in the result (``ok=False``), never raised.
    """
    if not isinstance(code, str):
        raise SandboxError("code must be a string")
    if timeout_s <= 0 or mem_mb < 64:
        raise SandboxError("timeout_s must be positive and mem_mb at least 64")
    inputs = inputs or {}
    for name in inputs:
        if not name.isidentifier() or name.startswith("_") or name in ("con", "pd", "np", "plt"):
            raise SandboxError(f"input name {name!r} must be a public Python identifier (not con/pd/np/plt)")
    scratch = tempfile.mkdtemp(prefix="aos-sandbox-", dir=str(scratch_root) if scratch_root else None)
    os.chmod(scratch, 0o700)
    started = time.perf_counter()
    try:
        inp_dir = os.path.join(scratch, "inputs")
        os.makedirs(inp_dir)
        os.makedirs(os.path.join(scratch, ".mpl"))
        import pyarrow.parquet as pq

        for name, value in inputs.items():
            pq.write_table(_to_arrow(value), os.path.join(inp_dir, f"{name}.parquet"))
        result_path = os.path.join(scratch, f".result-{os.urandom(8).hex()}.json")
        job = {
            "code": code,
            "inputs": list(inputs),
            "scratch": scratch,
            "result_path": result_path,
            "timeout_s": float(timeout_s),
            "mem_mb": int(mem_mb),
            "max_rows": int(max_rows),
            "max_output_bytes": int(max_output_bytes),
        }
        job_path = os.path.join(scratch, ".job.json")
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh)
        try:
            proc = subprocess.Popen(
                [sys.executable, "-I", "-m", "analystos_engine._sandbox_worker", job_path],
                cwd=scratch,
                env=_clean_env(scratch),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise SandboxError(f"could not start the sandbox worker: {exc}") from exc

        memory_killed = threading.Event()
        done = threading.Event()

        def watchdog() -> None:
            while not done.wait(0.05):
                rss = _rss_mb(proc.pid)
                if rss is not None and rss > mem_mb:
                    memory_killed.set()
                    _kill_group(proc)
                    return

        watcher = threading.Thread(target=watchdog, daemon=True)
        watcher.start()
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout_s + _STARTUP_GRACE_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            out, err = proc.communicate()
        finally:
            done.set()
            watcher.join(timeout=1)
            _kill_group(proc)  # reap anything the worker left behind in its process group
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        data: dict[str, Any] = {}
        if os.path.exists(result_path):
            with open(result_path, encoding="utf-8") as fh:
                data = json.load(fh)
        return _build_result(
            data, proc.returncode, timed_out, memory_killed.is_set(), out, err, elapsed, timeout_s, mem_mb
        )
    finally:
        if not keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def _build_result(
    data: dict[str, Any],
    returncode: int | None,
    timed_out: bool,
    memory_killed: bool,
    out: bytes,
    err: bytes,
    elapsed_ms: float,
    timeout_s: float,
    mem_mb: int,
) -> PythonResult:
    frames = {k: QueryResult.model_validate(v) for k, v in (data.get("dataframes") or {}).items()}
    res = PythonResult(
        ok=False,
        stdout=data.get("stdout", ""),
        stderr=data.get("stderr", ""),
        error=data.get("error"),
        error_type=data.get("error_type"),
        traceback=data.get("traceback"),
        dataframes=frames,
        figures=data.get("figures", []),
        result_repr=data.get("result_repr"),
        variables=data.get("variables", {}),
        elapsed_ms=elapsed_ms,
        timed_out=bool(data.get("timed_out")) or timed_out,
        memory_exceeded=bool(data.get("memory_exceeded")) or memory_killed,
        output_truncated=bool(data.get("output_truncated")),
        exit_code=returncode,
        isolation=data.get("isolation", {}),
    )
    if not data:
        # the worker died before reporting
        if memory_killed:
            res.error_type, res.error = (
                "MemoryError",
                f"memory limit exceeded ({mem_mb} MB); the process was stopped",
            )
        elif timed_out or (returncode is not None and returncode in (-signal.SIGXCPU, -signal.SIGKILL)):
            res.timed_out = True
            res.error_type, res.error = (
                "TimeoutError",
                f"execution exceeded the {timeout_s:g}s time limit; the process was stopped",
            )
        else:
            tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-5:]
            res.error_type = "SandboxError"
            res.error = f"the sandbox worker exited unexpectedly (code {returncode})" + (
                ": " + " | ".join(tail) if tail else ""
            )
    res.ok = res.error is None
    return res
