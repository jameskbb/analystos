"""SQL workspace: run read-only SQL, history, rerun, compare, saved queries with parameters."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..deps import READ_ONLY_POST, DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import NotFound, UnsafeQuery
from ..models import QueryRun, SavedQuery
from ..schemas.common import ERROR_RESPONSES, OkResponse, Page, TabularResult
from ..schemas.queries import (
    ChartSuggestion,
    CompareRequest,
    CompareResult,
    QueryRunDetail,
    QueryRunOut,
    QueryRunResult,
    RunQueryRequest,
    RunSavedQueryRequest,
    SavedQueryIn,
    SavedQueryOut,
    ValidateSqlRequest,
    ValidateSqlResult,
)
from ..services.charts import suggest_charts
from ..services.observability import audit
from ..services.queries import RunOutcome, compare_results, param_names, run_sql
from ..services.writer import writer_for

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["queries"], responses=ERROR_RESPONSES)


def _result(outcome: RunOutcome, charts: bool) -> QueryRunResult:
    run, res = outcome.run, outcome.result
    tab = TabularResult.model_validate(res.model_dump(mode="json"))
    out = QueryRunResult.model_validate({**QueryRunOut.model_validate(run).model_dump(), "result": tab})
    if res.row_count == 0:
        out.warnings.append("The query returned no rows.")
    if res.truncated:
        out.warnings.append(f"Result truncated to {res.row_count} rows.")
    if charts and res.row_count:
        cols = [c.model_dump() for c in res.columns]
        out.charts = [ChartSuggestion.model_validate(s) for s in suggest_charts(cols, tab.rows)]
    return out


@router.post(
    "/queries/run", response_model=QueryRunResult, operation_id="runQuery", openapi_extra=READ_ONLY_POST
)
def run_query(body: RunQueryRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> QueryRunResult:
    """Run read-only SQL (DuckDB dialect on the workspace store, or the source's dialect for a data source).

    Non-SELECT statements, multiple statements, file/URL access and extension loading are rejected
    with ``400 unsafe_sql``. Parameters use ``$name`` and are bound, never interpolated.
    """
    outcome = run_sql(
        db,
        state,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        sql=body.sql,
        params=body.params,
        param_definitions=[p.model_dump() for p in body.parameters],
        limit=body.limit,
        origin="sql",
        data_source_id=body.data_source_id,
    )
    return _result(outcome, body.suggest_chart)


@router.post(
    "/queries/validate",
    response_model=ValidateSqlResult,
    operation_id="validateSql",
    openapi_extra=READ_ONLY_POST,
)
def validate_sql(body: ValidateSqlRequest, ctx: ViewerCtx) -> ValidateSqlResult:
    """Check SQL against the read-only policy without running it."""
    from analystos_engine.sqlsafety import UnsafeSQLError, ensure_read_only, referenced_tables

    try:
        normalized = ensure_read_only(body.sql, body.dialect)
    except UnsafeSQLError as exc:
        return ValidateSqlResult(
            ok=False,
            error=str(exc),
            code=getattr(exc, "code", "unsafe_sql"),
            parameters=param_names(body.sql),
        )
    try:
        tables = referenced_tables(normalized, body.dialect)
    except Exception:  # noqa: BLE001
        tables = []
    return ValidateSqlResult(
        ok=True, normalized_sql=normalized, tables=tables, parameters=param_names(body.sql)
    )


@router.get("/queries/history", response_model=Page[QueryRunOut], operation_id="listQueryHistory")
def history(
    ctx: ViewerCtx,
    db: DbDep,
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    origin: str | None = None,
    status: str | None = None,
    saved_query_id: str | None = None,
    mine: bool = False,
) -> Page[QueryRunOut]:
    # Failed and rejected runs are written by the background writer: make them visible before listing.
    writer_for(state.session_factory).flush(5)
    stmt = select(QueryRun).where(QueryRun.workspace_id == ctx.workspace_id)
    if origin:
        stmt = stmt.where(QueryRun.origin == origin)
    if status:
        stmt = stmt.where(QueryRun.status == status)
    if saved_query_id:
        stmt = stmt.where(QueryRun.saved_query_id == saved_query_id)
    if mine:
        stmt = stmt.where(QueryRun.user_id == ctx.user_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(QueryRun.created_at.desc()).limit(limit).offset(offset)).all()
    return Page[QueryRunOut](
        items=[QueryRunOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


def _get_run(db: DbDep, workspace_id: str, run_id: str) -> QueryRun:
    run = db.get(QueryRun, run_id)
    if run is None or run.workspace_id != workspace_id:
        raise NotFound("Query run not found")
    return run


@router.get("/queries/history/{run_id}", response_model=QueryRunDetail, operation_id="getQueryRun")
def get_run(run_id: str, ctx: ViewerCtx, db: DbDep) -> QueryRunDetail:
    return QueryRunDetail.model_validate(_get_run(db, ctx.workspace_id, run_id))


@router.post(
    "/queries/history/{run_id}/rerun",
    response_model=QueryRunResult,
    operation_id="rerunQuery",
    openapi_extra=READ_ONLY_POST,
)
def rerun(run_id: str, ctx: ViewerCtx, db: DbDep, state: StateDep) -> QueryRunResult:
    """Re-execute a historical query with the same SQL and parameters."""
    old = _get_run(db, ctx.workspace_id, run_id)
    saved = db.get(SavedQuery, old.saved_query_id) if old.saved_query_id else None
    outcome = run_sql(
        db,
        state,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        sql=old.sql,
        params=old.params,
        param_definitions=saved.parameters if saved else None,
        limit=None,
        origin=old.origin,
        saved_query_id=old.saved_query_id,
        data_source_id=old.data_source_id,
    )
    return _result(outcome, True)


@router.post(
    "/queries/compare",
    response_model=CompareResult,
    operation_id="compareQueryRuns",
    openapi_extra=READ_ONLY_POST,
)
def compare(body: CompareRequest, ctx: ViewerCtx, db: DbDep) -> CompareResult:
    """Compare two historical results (e.g. before/after an edit, or a rerun) on key columns."""
    left = _get_run(db, ctx.workspace_id, body.left_run_id)
    right = _get_run(db, ctx.workspace_id, body.right_run_id)
    res = compare_results(
        [c["name"] for c in left.columns],
        left.result_snapshot,
        [c["name"] for c in right.columns],
        right.result_snapshot,
        body.keys,
    )
    return CompareResult(**res, left_run_id=left.id, right_run_id=right.id)


# ------------------------------------------------------------------------- saved queries


def _get_saved(db: DbDep, workspace_id: str, query_id: str) -> SavedQuery:
    q = db.get(SavedQuery, query_id)
    if q is None or q.workspace_id != workspace_id:
        raise NotFound("Saved query not found")
    return q


@router.get("/queries/saved", response_model=list[SavedQueryOut], operation_id="listSavedQueries")
def list_saved(ctx: ViewerCtx, db: DbDep, tag: str | None = None) -> list[SavedQueryOut]:
    rows = db.scalars(
        select(SavedQuery).where(SavedQuery.workspace_id == ctx.workspace_id).order_by(SavedQuery.name)
    ).all()
    out = [SavedQueryOut.model_validate(r) for r in rows]
    return [q for q in out if tag in q.tags] if tag else out


def _validate_saved(body: SavedQueryIn) -> None:
    """Workspace queries are checked against the read-only policy at save time; external-source
    queries are checked in the source's dialect when they run."""
    from analystos_engine.sqlsafety import UnsafeSQLError, ensure_read_only

    if body.data_source_id is not None:
        return
    try:
        ensure_read_only(body.sql, "duckdb")
    except UnsafeSQLError as exc:
        raise UnsafeQuery(f"Query rejected: {exc}") from exc


@router.post("/queries/saved", response_model=SavedQueryOut, status_code=201, operation_id="createSavedQuery")
def create_saved(body: SavedQueryIn, ctx: EditorCtx, db: DbDep) -> SavedQueryOut:
    _validate_saved(body)
    q = SavedQuery(
        workspace_id=ctx.workspace_id,
        created_by=ctx.user_id,
        **{**body.model_dump(), "parameters": [p.model_dump() for p in body.parameters]},
    )
    db.add(q)
    db.flush()
    audit(
        db,
        action="saved_query.create",
        resource_type="saved_query",
        resource_id=q.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"name": q.name},
    )
    return SavedQueryOut.model_validate(q)


@router.get("/queries/saved/{query_id}", response_model=SavedQueryOut, operation_id="getSavedQuery")
def get_saved(query_id: str, ctx: ViewerCtx, db: DbDep) -> SavedQueryOut:
    return SavedQueryOut.model_validate(_get_saved(db, ctx.workspace_id, query_id))


@router.put("/queries/saved/{query_id}", response_model=SavedQueryOut, operation_id="updateSavedQuery")
def update_saved(query_id: str, body: SavedQueryIn, ctx: EditorCtx, db: DbDep) -> SavedQueryOut:
    q = _get_saved(db, ctx.workspace_id, query_id)
    _validate_saved(body)
    for key, value in body.model_dump().items():
        setattr(q, key, value)
    q.parameters = [p.model_dump() for p in body.parameters]
    q.version_no += 1
    audit(
        db,
        action="saved_query.update",
        resource_type="saved_query",
        resource_id=q.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"name": q.name, "version_no": q.version_no},
    )
    db.flush()
    return SavedQueryOut.model_validate(q)


@router.delete("/queries/saved/{query_id}", response_model=OkResponse, operation_id="deleteSavedQuery")
def delete_saved(query_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    q = _get_saved(db, ctx.workspace_id, query_id)
    db.delete(q)
    audit(
        db,
        action="saved_query.delete",
        resource_type="saved_query",
        resource_id=query_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
    )
    return OkResponse()


@router.post(
    "/queries/saved/{query_id}/run",
    response_model=QueryRunResult,
    operation_id="runSavedQuery",
    openapi_extra=READ_ONLY_POST,
)
def run_saved(
    query_id: str, body: RunSavedQueryRequest, ctx: ViewerCtx, db: DbDep, state: StateDep
) -> QueryRunResult:
    q = _get_saved(db, ctx.workspace_id, query_id)
    outcome = run_sql(
        db,
        state,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        sql=q.sql,
        params=body.params,
        param_definitions=q.parameters,
        limit=body.limit,
        origin="saved_query",
        saved_query_id=q.id,
        data_source_id=q.data_source_id,
    )
    return _result(outcome, True)
