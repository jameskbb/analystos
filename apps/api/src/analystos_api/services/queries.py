"""Read-only SQL execution with history, parameters and reproducibility records.

Every SQL string passes ``analystos_engine.sqlsafety.ensure_read_only`` before it reaches
DuckDB or an external database; rejected SQL is recorded in history with status
``rejected`` and a diagnostic event, and returns HTTP 400 ``unsafe_sql``.

Parameters use DuckDB's named-parameter syntax (``$region``) and are always bound, never
interpolated into SQL text.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..errors import ApiError, NotFound, Unprocessable, UnsafeQuery
from ..models import DataSource, QueryRun
from ..state import AppState
from .datasets import dataset_versions_for_tables
from .observability import record_event
from .writer import writer_for

_PARAM = re.compile(r"(?<![\w$])\$([A-Za-z_][A-Za-z0-9_]*)")


class QueryFailed(ApiError):
    status_code = 400
    code = "query_failed"


class QueryTimedOut(ApiError):
    status_code = 408
    code = "query_timeout"


def param_names(sql: str) -> list[str]:
    # Ignore $ inside single-quoted string literals.
    stripped = re.sub(r"'(?:[^']|'')*'", "''", sql)
    seen: list[str] = []
    for name in _PARAM.findall(stripped):
        if name not in seen:
            seen.append(name)
    return seen


def _coerce(value: Any, ptype: str, name: str) -> Any:
    if value is None:
        return None
    try:
        if ptype == "number":
            return float(value)
        if ptype == "integer":
            return int(value)
        if ptype == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "yes", "y"}
        if ptype == "date":
            return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
        if ptype == "string_list":
            return [str(v) for v in (value if isinstance(value, list) else [value])]
        return str(value)
    except (TypeError, ValueError) as exc:
        raise Unprocessable(f"Parameter ${name} must be a {ptype}", code="invalid_parameter") from exc


def bind_parameters(sql: str, definitions: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, Any]:
    defs = {d["name"]: d for d in definitions}
    bound: dict[str, Any] = {}
    for name in param_names(sql):
        d = defs.get(name)
        if d is None:  # undeclared parameter: bind the JSON value as given
            if name not in values:
                raise Unprocessable(f"Missing value for parameter ${name}", code="missing_parameter")
            bound[name] = values[name]
            continue
        raw = values.get(name, d.get("default"))
        if raw is None and d.get("required", True):
            raise Unprocessable(f"Missing value for parameter ${name}", code="missing_parameter")
        bound[name] = _coerce(raw, d.get("type", "string"), name)
    unknown = set(values) - set(bound)
    if unknown:
        raise Unprocessable(f"Unknown parameters: {sorted(unknown)}", code="unknown_parameter")
    return bound


@dataclass
class RunOutcome:
    run: QueryRun
    result: Any  # engine QueryResult


def _referenced_tables(sql: str) -> list[str]:
    from analystos_engine.sqlsafety import referenced_tables

    try:
        return referenced_tables(sql)
    except Exception:  # noqa: BLE001 - lineage is best effort; safety was already enforced
        return []


def run_sql(
    db: Session,
    state: AppState,
    *,
    workspace_id: str,
    user_id: str | None,
    sql: str,
    params: dict[str, Any] | None = None,
    param_definitions: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    origin: str = "sql",
    saved_query_id: str | None = None,
    data_source_id: str | None = None,
    investigation_id: str | None = None,
    record_history: bool = True,
) -> RunOutcome:
    from analystos_engine.sqlsafety import UnsafeSQLError, ensure_read_only
    from analystos_engine.store import QueryTimeoutError, StoreError

    limit = min(limit or state.settings.query_row_limit, state.settings.query_row_limit)
    bound = bind_parameters(sql, param_definitions or [], params or {})
    run = QueryRun(
        workspace_id=workspace_id,
        user_id=user_id,
        origin=origin,
        sql=sql,
        params=_jsonable(bound),
        saved_query_id=saved_query_id,
        data_source_id=data_source_id,
        investigation_id=investigation_id,
        status="running",
    )
    start = time.perf_counter()
    source: DataSource | None = None
    try:
        if data_source_id:
            source = db.get(DataSource, data_source_id)
            if source is None or source.workspace_id != workspace_id:
                raise NotFound("Data source not found")
            from .connectors import connector_for

            with connector_for(state.secret_box, source) as conn:
                ensure_read_only(sql, getattr(conn, "dialect", "duckdb"))
                result = conn.query(sql, bound or None, limit)
        else:
            result = state.stores.get(workspace_id).execute_read(
                sql, bound or None, limit, state.settings.query_timeout_s
            )
    except UnsafeSQLError as exc:
        _finish(db, state, run, start, record_history, status="rejected", error=str(exc))
        raise UnsafeQuery(
            f"Query rejected: {exc}", code="unsafe_sql", extra={"reason": getattr(exc, "code", "unsafe_sql")}
        ) from exc
    except QueryTimeoutError as exc:
        _finish(db, state, run, start, record_history, status="failed", error=str(exc))
        raise QueryTimedOut(str(exc)) from exc
    except StoreError as exc:
        _finish(db, state, run, start, record_history, status="failed", error=str(exc))
        raise QueryFailed(str(exc)) from exc
    except ApiError as exc:
        _finish(db, state, run, start, record_history, status="failed", error=exc.detail)
        raise
    run.status = "succeeded"
    run.row_count = result.row_count
    run.truncated = result.truncated
    run.elapsed_ms = result.elapsed_ms
    run.columns = [c.model_dump() for c in result.columns]
    run.result_snapshot = _jsonable(result.rows[: state.settings.result_snapshot_rows])
    if not data_source_id:
        run.dataset_versions = dataset_versions_for_tables(db, state, workspace_id, _referenced_tables(sql))
    _finish(
        db,
        state,
        run,
        start,
        record_history,
        status="succeeded",
        error=None,
        detail={
            "rows": result.row_count,
            "truncated": result.truncated,
            "origin": origin,
            "data_source": source.kind if source else "workspace",
        },
    )
    return RunOutcome(run=run, result=result)


def _finish(
    db: Session,
    state: AppState,
    run: QueryRun,
    start: float,
    record: bool,
    *,
    status: str,
    error: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    run.status = status
    run.error = error
    elapsed = (time.perf_counter() - start) * 1000
    if not run.elapsed_ms:
        run.elapsed_ms = elapsed
    if record:
        if status == "succeeded":
            db.add(run)
            db.flush()
        else:
            # Failed/rejected runs must survive the request's rollback: write them out of band.
            writer_for(state.session_factory).submit(lambda s: _merge(s, run))
    record_event(
        state.session_factory,
        category="sql",
        name=f"query:{run.origin}",
        status={"succeeded": "ok", "rejected": "rejected"}.get(status, "error"),
        duration_ms=elapsed,
        workspace_id=run.workspace_id,
        user_id=run.user_id,
        detail={**(detail or {}), "sql_preview": run.sql[:300]},
        error=error,
    )


def _merge(s: Session, run: QueryRun) -> None:
    s.merge(run)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    return value


def compare_results(
    left_cols: list[str],
    left_rows: list[list[Any]],
    right_cols: list[str],
    right_rows: list[list[Any]],
    keys: list[str] | None = None,
) -> dict[str, Any]:
    """Compare two tabular results on key columns; numeric columns get deltas."""
    common = [c for c in left_cols if c in right_cols]

    def is_num(col: str, cols: list[str], rows: list[list[Any]]) -> bool:
        i = cols.index(col)
        vals = [r[i] for r in rows if r[i] is not None]
        return bool(vals) and all(isinstance(v, int | float) and not isinstance(v, bool) for v in vals)

    numeric = [c for c in common if is_num(c, left_cols, left_rows) or is_num(c, right_cols, right_rows)]
    keys = keys or [c for c in common if c not in numeric]
    for k in keys:
        if k not in common:
            raise Unprocessable(f"Key column {k!r} is not in both results", code="invalid_compare_key")

    def index(cols: list[str], rows: list[list[Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
        out: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in rows:
            rec = dict(zip(cols, r, strict=False))
            out[tuple(str(rec.get(k)) for k in keys)] = rec
        return out

    li, ri = index(left_cols, left_rows), index(right_cols, right_rows)
    rows_out = []
    for key in list(dict.fromkeys([*li.keys(), *ri.keys()])):
        lrec, rrec = li.get(key), ri.get(key)
        row: dict[str, Any] = {k: v for k, v in zip(keys, key, strict=False)}
        row["_status"] = "both" if lrec and rrec else ("left_only" if lrec else "right_only")
        for c in numeric:
            lv = lrec.get(c) if lrec else None
            rv = rrec.get(c) if rrec else None
            row[f"{c}__left"] = lv
            row[f"{c}__right"] = rv
            if isinstance(lv, int | float) and isinstance(rv, int | float):
                row[f"{c}__delta"] = rv - lv
                row[f"{c}__pct"] = ((rv - lv) / abs(lv)) if lv else None
            else:
                row[f"{c}__delta"] = None
                row[f"{c}__pct"] = None
        rows_out.append(row)
    changed = sum(
        1
        for r in rows_out
        if r["_status"] != "both" or any((r.get(f"{c}__delta") or 0) != 0 for c in numeric)
    )
    return {
        "keys": keys,
        "measures": numeric,
        "rows": rows_out,
        "left_rows": len(left_rows),
        "right_rows": len(right_rows),
        "changed_rows": changed,
        "identical": changed == 0,
    }
