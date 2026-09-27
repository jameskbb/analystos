"""Python analysis through the engine sandbox (subprocess with rlimits, timeout, no network).

Inputs are named read-only SQL queries; the API executes them through the workspace store
(read-only policy, row cap) and hands the results to the sandbox as DataFrames. The sandbox
never receives credentials or a writable database handle.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

from ..errors import ApiError, Unprocessable
from ..state import AppState
from .observability import record_event

MAX_INPUT_ROWS = 200_000


class SandboxFailed(ApiError):
    status_code = 422
    code = "python_failed"


def run_python(
    state: AppState,
    *,
    workspace_id: str,
    user_id: str | None,
    code: str,
    inputs: dict[str, str],
    timeout_s: float | None = None,
) -> dict[str, Any]:
    from analystos_engine import sandbox

    if len(code) > 200_000:
        raise Unprocessable("Code is too long", code="code_too_long")
    store = state.stores.get(workspace_id)
    frames: dict[str, Any] = {}
    for name, sql in inputs.items():
        if not name.isidentifier():
            raise Unprocessable(f"Input name {name!r} must be a Python identifier", code="invalid_input")
        frames[name] = store.execute_read(sql, None, MAX_INPUT_ROWS, state.settings.query_timeout_s)
    timeout = min(float(timeout_s or state.settings.python_timeout_s), state.settings.python_timeout_s)
    kwargs: dict[str, Any] = {"timeout_s": timeout, "mem_mb": state.settings.python_mem_mb}
    params = inspect.signature(sandbox.run_python).parameters
    if "db_path" in params and getattr(store, "path", None) is not None:
        kwargs["db_path"] = store.path
    start = time.perf_counter()
    result = sandbox.run_python(code, frames, **{k: v for k, v in kwargs.items() if k in params})
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    record_event(
        state.session_factory,
        category="python",
        name="sandbox_run",
        status="error" if data.get("error") else "ok",
        duration_ms=(time.perf_counter() - start) * 1000,
        workspace_id=workspace_id,
        user_id=user_id,
        detail={
            "inputs": sorted(inputs),
            "figures": len(data.get("figures", []) or []),
            "dataframes": sorted((data.get("dataframes") or {}).keys()),
        },
        error=data.get("error"),
    )
    return data
