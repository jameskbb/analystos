"""Adapter over optional parts of ``analystos_engine``.

The investigator depends on the engine's semantic models, compiler, calendar, store and
sqlsafety directly. Two engine modules are consumed through this adapter so that the
investigator keeps working, and fails honestly, whichever engine build is installed:

* ``analystos_engine.validation.validate_result``: its checks are merged with the
  investigator's own investigation-specific checks.
* ``analystos_engine.sandbox.run_python``: used by ``python`` plan steps. If it is not
  available, the step is recorded as a failed node that says so.
"""

from __future__ import annotations

import importlib
from typing import Any

from analystos_engine.semantic.compiler import CompiledQuery
from analystos_engine.types import QueryResult

from .models import ValidationCheck


def engine_validation_checks(
    result: QueryResult, compiled: CompiledQuery, expected_rows: str
) -> list[ValidationCheck]:
    """Run the engine's reusable validation if the installed engine provides it."""
    try:
        mod = importlib.import_module("analystos_engine.validation")
    except ImportError:
        return []
    validate = getattr(mod, "validate_result", None)
    expectations_cls = getattr(mod, "QueryExpectations", None)
    if validate is None or expectations_cls is None:
        return []
    kwargs: dict[str, Any] = {}
    fields = getattr(expectations_cls, "model_fields", {})
    metric_cols = [c.name for c in compiled.columns if c.role == "metric"]
    if "metric_columns" in fields:
        kwargs["metric_columns"] = metric_cols
    if "required_columns" in fields:
        kwargs["required_columns"] = [c.name for c in compiled.columns]
    if "allow_empty" in fields:
        kwargs["allow_empty"] = False
    if "non_empty" in fields:
        kwargs["non_empty"] = True
    if "min_rows" in fields:
        kwargs["min_rows"] = 1
    if "max_rows" in fields and expected_rows == "one":
        kwargs["max_rows"] = 1
    if "grain" in fields:
        kwargs["grain"] = list(compiled.grain)
    try:
        report = validate(result, expectations_cls(**kwargs))
    except Exception as exc:  # engine validation must never crash an investigation
        return [
            ValidationCheck(name="engine_validation", passed=False, detail=f"engine validation error: {exc}")
        ]
    checks: list[ValidationCheck] = []
    for c in getattr(report, "checks", []) or []:
        data = c if isinstance(c, dict) else c.model_dump() if hasattr(c, "model_dump") else vars(c)
        severity = str(data.get("severity", "error"))
        passed = bool(data.get("passed", False))
        detail = str(data.get("detail", "") or "")
        if not passed and severity != "error":
            # warnings and info are recorded on the artifact but do not fail the test
            passed, detail = True, f"{severity}: {detail}"
        checks.append(
            ValidationCheck(name=f"engine:{data.get('name', 'check')}", passed=passed, detail=detail)
        )
    return checks


def run_python_sandbox(code: str, timeout_s: float, store: Any) -> Any:
    """Call the engine sandbox; raises ``RuntimeError`` if the sandbox is unavailable."""
    try:
        mod = importlib.import_module("analystos_engine.sandbox")
    except ImportError as exc:
        raise RuntimeError("the Python sandbox is not available in this engine build") from exc
    run = getattr(mod, "run_python", None)
    if run is None:
        raise RuntimeError("the engine sandbox does not expose run_python")
    db_path = getattr(store, "path", None)
    attempts: list[dict[str, Any]] = []
    if db_path is not None:
        attempts.append({"code": code, "inputs": {}, "timeout_s": timeout_s, "db_path": str(db_path)})
    attempts.append({"code": code, "inputs": {}, "timeout_s": timeout_s})
    last: Exception | None = None
    for kwargs in attempts:
        try:
            return run(**kwargs)
        except TypeError as exc:
            last = exc
    raise RuntimeError(f"could not call the engine sandbox: {last}")
