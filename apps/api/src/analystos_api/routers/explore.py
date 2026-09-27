"""Explore (semantic metric queries and table-level exploration without SQL), pivot tables, chart
suggestions and the Python sandbox."""

from __future__ import annotations

from typing import Any

from analystos_engine.semantic.compiler import CompiledQuery, MetricQuery
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import READ_ONLY_POST, DbDep, StateDep, ViewerCtx
from ..errors import ApiError, NotFound, Unprocessable
from ..schemas.common import ERROR_RESPONSES, TabularResult
from ..schemas.queries import ChartSuggestion
from ..services.charts import suggest_charts
from ..services.datasets import dataset_by_table
from ..services.explore import (
    PivotOut,
    PivotRequest,
    TableQuery,
    assemble_pivot,
    build_table_sql,
    table_filter_context,
)
from ..services.investigations import filter_context
from ..services.python_sandbox import run_python
from ..services.queries import run_sql
from ..services.semantic import BuiltModel, add_join_warnings, build_model

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["explore"], responses=ERROR_RESPONSES)


class GrainError(ApiError):
    status_code = 422
    code = "grain_error"


class ExploreResult(BaseModel):
    compiled: CompiledQuery
    result: TabularResult
    filter_context: list[dict[str, Any]]
    metric_versions: dict[str, Any]
    query_run_id: str
    charts: list[ChartSuggestion] = Field(default_factory=list)


class TableExploreResult(BaseModel):
    sql: str
    result: TabularResult
    filter_context: list[dict[str, Any]]
    query_run_id: str
    charts: list[ChartSuggestion] = Field(default_factory=list)


class ChartSuggestRequest(BaseModel):
    columns: list[dict[str, Any]]
    rows: list[list[Any]]
    title: str = ""


class PythonRunRequest(BaseModel):
    code: str = Field(min_length=1, max_length=200_000)
    inputs: dict[str, str] = Field(
        default_factory=dict, description="name -> read-only SQL; exposed as DataFrames"
    )
    timeout_s: float | None = Field(default=None, gt=0, le=600)


class PythonRunResult(BaseModel):
    ok: bool = True
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    result_repr: str | None = None
    timed_out: bool = False
    memory_exceeded: bool = False
    output_truncated: bool = False
    isolation: Any = Field(
        default=None, description="Which isolation layers were active (network, filesystem)"
    )
    dataframes: dict[str, Any] = Field(default_factory=dict)
    figures: list[str] = Field(default_factory=list, description="PNG images, base64")
    elapsed_ms: float = 0.0


def compile_metric_query(built: BuiltModel, q: MetricQuery) -> CompiledQuery:
    from analystos_engine.semantic.compiler import CompileError, compile
    from analystos_engine.semantic.compiler import GrainError as EngineGrainError

    try:
        return compile(built.model, q)
    except EngineGrainError as exc:
        raise GrainError(str(exc)) from exc
    except (CompileError, ValueError) as exc:
        raise Unprocessable(str(exc), code="compile_error") from exc


def metric_filter_context(q: MetricQuery) -> list[dict[str, Any]]:
    items = filter_context(
        window=q.time, filters=[{"dimension": f.dimension, "op": f.op, "values": f.values} for f in q.filters]
    )
    return items


@router.post(
    "/explore/compile",
    response_model=CompiledQuery,
    operation_id="compileMetricQuery",
    openapi_extra=READ_ONLY_POST,
)
def compile_only(q: MetricQuery, ctx: ViewerCtx, db: DbDep) -> CompiledQuery:
    """Compile a semantic query to grain-safe SQL without running it (joins, grain and warnings included)."""
    return compile_metric_query(build_model(db, ctx.workspace), q)


@router.post(
    "/explore/metrics",
    response_model=ExploreResult,
    operation_id="exploreMetrics",
    openapi_extra=READ_ONLY_POST,
)
def explore_metrics(q: MetricQuery, ctx: ViewerCtx, db: DbDep, state: StateDep) -> ExploreResult:
    built = build_model(db, ctx.workspace)
    compiled = compile_metric_query(built, q)
    add_join_warnings(state.stores.get(ctx.workspace_id), built.model, compiled)
    outcome = run_sql(
        db,
        state,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        sql=compiled.sql,
        origin="explore",
        limit=q.limit,
    )
    versions = built.version_map(q.metrics)
    outcome.run.metric_versions = versions
    res = TabularResult.model_validate(outcome.result.model_dump(mode="json"))
    charts = (
        [
            ChartSuggestion.model_validate(s)
            for s in suggest_charts([c.model_dump() for c in res.columns], res.rows)
        ]
        if res.row_count
        else []
    )
    return ExploreResult(
        compiled=compiled,
        result=res,
        filter_context=metric_filter_context(q),
        metric_versions=versions,
        query_run_id=outcome.run.id,
        charts=charts,
    )


def _table_columns(db: DbDep, workspace_id: str, table: str) -> list[str]:
    ds = dataset_by_table(db, workspace_id, table)
    if ds is None:
        raise NotFound(f"Dataset table {table!r} not found")
    return [c["name"] for c in ds.columns]


@router.post(
    "/explore/table",
    response_model=TableExploreResult,
    operation_id="exploreTable",
    openapi_extra=READ_ONLY_POST,
)
def explore_table(q: TableQuery, ctx: ViewerCtx, db: DbDep, state: StateDep) -> TableExploreResult:
    """Filter, sort, group, aggregate and add calculated fields on one dataset, without writing SQL."""
    sql, _dims, _measures = build_table_sql(q, _table_columns(db, ctx.workspace_id, q.table))
    outcome = run_sql(
        db,
        state,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        sql=sql,
        origin="explore",
        limit=q.limit,
    )
    res = TabularResult.model_validate(outcome.result.model_dump(mode="json"))
    charts = (
        [
            ChartSuggestion.model_validate(s)
            for s in suggest_charts([c.model_dump() for c in res.columns], res.rows)
        ]
        if res.row_count
        else []
    )
    return TableExploreResult(
        sql=sql,
        result=res,
        filter_context=table_filter_context(q),
        query_run_id=outcome.run.id,
        charts=charts,
    )


@router.post("/explore/pivot", response_model=PivotOut, operation_id="pivot", openapi_extra=READ_ONLY_POST)
def pivot(req: PivotRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> PivotOut:
    """Pivot table with rows, columns, values, subtotals and grand totals. Every subtotal and total is
    computed by its own grain-correct query (never by adding up cells)."""
    if (req.metric_query is None) == (req.table_query is None):
        raise Unprocessable("Provide exactly one of metric_query or table_query", code="invalid_pivot")
    store = state.stores.get(ctx.workspace_id)
    formats: dict[str, str | None] = {}
    if req.metric_query is not None:
        built = build_model(db, ctx.workspace)
        base = MetricQuery.model_validate(
            {
                **req.metric_query,
                "metrics": [v.field for v in req.values],
                "dimensions": [],
                "order_by": [],
                "limit": None,
            }
        )
        for v in req.values:
            if built.model.has_metric(v.field):
                formats[v.field] = built.model.get_metric(v.field).format
        fctx = metric_filter_context(base)

        def fetch(dims: list[str]) -> tuple[list[dict[str, Any]], str]:
            compiled = compile_metric_query(built, base.model_copy(update={"dimensions": dims}))
            res = store.execute_read(compiled.sql, None, 100_000, state.settings.query_timeout_s)
            return res.to_records(), compiled.sql
    else:
        tq = req.table_query
        assert tq is not None
        cols = _table_columns(db, ctx.workspace_id, tq.table)
        from ..services.explore import Aggregate, GroupBy

        aggs = [
            Aggregate(column=v.field, fn=v.agg, alias=v.label or f"{v.agg}_{v.field}") for v in req.values
        ]
        fctx = table_filter_context(tq)

        def fetch(dims: list[str]) -> tuple[list[dict[str, Any]], str]:
            groups = []
            for d in dims:
                name, _, grain = d.partition("__")
                groups.append(GroupBy(column=name, grain=grain or None))  # type: ignore[arg-type]
            q = tq.model_copy(
                update={"aggregates": aggs, "group_by": groups, "order_by": [], "limit": 10_000}
            )
            sql, _, _ = build_table_sql(q, cols, include_limit=False)
            res = store.execute_read(sql, None, 100_000, state.settings.query_timeout_s)
            return res.to_records(), sql

    out = assemble_pivot(req, fetch, formats)
    out.filter_context = fctx
    return out


@router.post(
    "/charts/suggest",
    response_model=list[ChartSuggestion],
    operation_id="suggestCharts",
    tags=["charts"],
    openapi_extra=READ_ONLY_POST,
)
def charts_suggest(body: ChartSuggestRequest, ctx: ViewerCtx) -> list[ChartSuggestion]:
    """Choose chart types from the result shape (time + measure -> line, category + measure -> bar, ...)."""
    return [
        ChartSuggestion.model_validate(s) for s in suggest_charts(body.columns, body.rows, title=body.title)
    ]


@router.post(
    "/python/run",
    response_model=PythonRunResult,
    operation_id="runPython",
    tags=["python"],
    openapi_extra=READ_ONLY_POST,
)
def python_run(body: PythonRunRequest, ctx: ViewerCtx, state: StateDep) -> PythonRunResult:
    """Run Python in the sandbox (subprocess, CPU/memory limits, timeout, no network, scratch dir only).
    ``inputs`` are read-only SQL queries whose results are provided as pandas DataFrames."""
    data = run_python(
        state,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        code=body.code,
        inputs=body.inputs,
        timeout_s=body.timeout_s,
    )
    return PythonRunResult.model_validate(
        {k: v for k, v in data.items() if k in PythonRunResult.model_fields}
    )
