"""Tool registry for the optional agent (spec §52).

Tools are the only way a model can obtain data. A model *requests* a tool call (name plus
JSON arguments); the registry validates the arguments against the tool's schema, executes
the call against the engine, records an artifact, and returns a ``ToolResult``. The model
never supplies results: the orchestrator injects them, wrapped as untrusted data.

Tools: list_tables, inspect_schema, inspect_metric, run_sql, run_python, profile_column,
compare_periods, segment_metric, create_chart, save_finding.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from analystos_engine.calendar import previous_period, resolve_period
from analystos_engine.semantic.models import SemanticModel
from analystos_engine.sqlsafety import ensure_read_only
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import charts
from .._engine_compat import run_python_sandbox
from ..ids import canonical_json, stable_id
from ..models import (
    AnalysisPlan,
    Artifact,
    FilterSpec,
    PlanContext,
    ResultSnapshot,
    ValidationCheck,
    ValidationSummary,
)
from ..planner import driver_edges
from ..semantic_graph import dimensions_for_metric
from .verifier import EvidenceNumbers, verify_text

MAX_TOOL_ROWS = 200


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    ok: bool
    data: Any = None
    error: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class TableArgs(_Args):
    table: str


class MetricArgs(_Args):
    metric_id: str


class SqlArgs(_Args):
    sql: str
    purpose: str = ""


class PythonArgs(_Args):
    code: str


class ColumnArgs(_Args):
    table: str
    column: str


class CompareArgs(_Args):
    metric_id: str
    period: str
    comparison: str = "pop"
    filters: list[FilterSpec] = Field(default_factory=list)


class SegmentArgs(_Args):
    metric_id: str
    dimension: str
    period: str
    comparison: str = "pop"
    filters: list[FilterSpec] = Field(default_factory=list)


class ChartArgs(_Args):
    artifact_id: str
    chart_type: str = "bar"
    x: str | None = None
    y: str | None = None


class FindingArgs(_Args):
    statement: str
    artifact_ids: list[str]


@dataclass
class ProposedFinding:
    statement: str
    artifact_ids: list[str]
    status: str = "needs_review"


@dataclass
class ToolState:
    """Everything tools produced in one orchestration: artifacts and proposed findings."""

    store: Any
    model: SemanticModel
    today: dt.date
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    findings: list[ProposedFinding] = field(default_factory=list)
    calls: list[ToolResult] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str
    args: type[_Args]
    handler: Callable[[Any, ToolState], ToolResult]


def _snapshot(res: Any) -> ResultSnapshot:
    return ResultSnapshot(
        columns=[{"name": c.name, "type": c.type} for c in res.columns],
        rows=[list(r) for r in res.rows[:MAX_TOOL_ROWS]],
        row_count=res.row_count,
        truncated=res.truncated or res.row_count > MAX_TOOL_ROWS,
        elapsed_ms=res.elapsed_ms,
    )


def _list_tables(_: NoArgs, st: ToolState) -> ToolResult:
    tables = st.store.list_tables(include_row_counts=True)
    return ToolResult(
        tool="list_tables",
        ok=True,
        data=[{"table": t.name, "rows": t.row_count, "columns": len(t.columns)} for t in tables],
    )


def _inspect_schema(a: TableArgs, st: ToolState) -> ToolResult:
    info = st.store.describe(a.table)
    return ToolResult(
        tool="inspect_schema",
        ok=True,
        data={
            "table": info.name,
            "row_count": info.row_count,
            "columns": [{"name": c.name, "type": c.type, "nullable": c.nullable} for c in info.columns],
        },
    )


def _inspect_metric(a: MetricArgs, st: ToolState) -> ToolResult:
    m = st.model.get_metric(a.metric_id)
    usable, _ = dimensions_for_metric(st.model, m.id)
    return ToolResult(
        tool="inspect_metric",
        ok=True,
        data={
            "id": m.id,
            "name": m.display_name,
            "kind": m.kind,
            "expr": m.expr,
            "agg": m.agg,
            "entity": m.entity,
            "numerator": m.numerator,
            "denominator": m.denominator,
            "formula": m.formula,
            "format": m.format,
            "version": m.version_id,
            "description": m.description,
            "drivers": [{"metric_id": c, "relation": r} for c, r in driver_edges(st.model, m.id)],
            "dimensions": [d.name for d in usable],
        },
    )


def _run_sql(a: SqlArgs, st: ToolState) -> ToolResult:
    safe = ensure_read_only(a.sql)
    res = st.store.execute_read(safe, limit=MAX_TOOL_ROWS)
    checks = [
        ValidationCheck(name="executed", passed=True, detail=f"{res.row_count} rows"),
        ValidationCheck(name="non_empty", passed=res.row_count > 0),
    ]
    art = Artifact(
        id=stable_id("art", "query", safe, canonical_json(res.rows)),
        kind="query",
        title=a.purpose or "Agent SQL",
        sql=safe,
        result=_snapshot(res),
        validation=ValidationSummary(ok=all(c.passed for c in checks), checks=checks),
        warnings=["ad-hoc SQL requested by the assistant; not governed by the semantic model"],
    )
    st.artifacts[art.id] = art
    return ToolResult(
        tool="run_sql",
        ok=True,
        artifact_ids=[art.id],
        data={"columns": [c.name for c in res.columns], "rows": res.rows[:50], "row_count": res.row_count},
    )


def _run_python(a: PythonArgs, st: ToolState) -> ToolResult:
    result = run_python_sandbox(a.code, 30.0, st.store)
    err = getattr(result, "error", None)
    art = Artifact(
        id=stable_id("art", "dataframe", a.code, str(getattr(result, "stdout", ""))),
        kind="dataframe",
        title="Agent Python",
        python=a.code,
        data={"stdout": str(getattr(result, "stdout", ""))[:5000], "error": err},
        error=err,
    )
    st.artifacts[art.id] = art
    return ToolResult(
        tool="run_python",
        ok=err is None,
        error=err,
        artifact_ids=[art.id],
        data={"stdout": str(getattr(result, "stdout", ""))[:2000]},
    )


def _profile_column(a: ColumnArgs, st: ToolState) -> ToolResult:
    info = st.store.describe(a.table)
    if a.column not in {c.name for c in info.columns}:
        return ToolResult(tool="profile_column", ok=False, error=f"column {a.column!r} not in {a.table!r}")
    col = '"' + a.column.replace('"', '""') + '"'
    tbl = '"' + a.table.replace('"', '""') + '"'
    sql = (
        f"SELECT count(*) AS row_count, count(*) - count({col}) AS null_count, "
        f"count(DISTINCT {col}) AS distinct_count, min({col})::VARCHAR AS min_value, "
        f"max({col})::VARCHAR AS max_value FROM {tbl}"
    )
    res = st.store.execute_read(ensure_read_only(sql), limit=1)
    top = st.store.execute_read(
        ensure_read_only(
            f"SELECT {col}::VARCHAR AS value, count(*) AS n FROM {tbl} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10"
        ),
        limit=10,
    )
    art = Artifact(
        id=stable_id("art", "query", sql, canonical_json(res.rows)),
        kind="query",
        title=f"Profile {a.table}.{a.column}",
        sql=sql,
        result=_snapshot(res),
        data={"top_values": top.to_records()},
    )
    st.artifacts[art.id] = art
    return ToolResult(
        tool="profile_column",
        ok=True,
        artifact_ids=[art.id],
        data={**res.to_records()[0], "top_values": top.to_records()},
    )


def _context(
    st: ToolState, metric_id: str, period: str, comparison: str, filters: list[FilterSpec]
) -> PlanContext:
    window = resolve_period(period, st.today, st.model.calendar)
    kind: Literal["pop", "yoy"] = "yoy" if comparison == "yoy" else "pop"
    return PlanContext(
        question="tool call",
        metric_id=metric_id,
        window=window,
        baseline=previous_period(window, kind),
        comparison_kind=kind,
        filters=filters,
    )


def _executor_for(st: ToolState, ctx: PlanContext) -> Any:
    from ..executor import _Executor

    return _Executor(AnalysisPlan(steps=[], context=ctx), st.store, st.model)


def _compare_periods(a: CompareArgs, st: ToolState) -> ToolResult:
    st.model.get_metric(a.metric_id)
    ctx = _context(st, a.metric_id, a.period, a.comparison, a.filters)
    ex = _executor_for(st, ctx)
    cur, base, ids, err = ex.totals(a.metric_id, [])
    st.artifacts.update(ex.artifacts)
    if err or cur is None or base is None:
        return ToolResult(tool="compare_periods", ok=False, error=err or "no value", artifact_ids=ids)
    pct = None if not base else (cur - base) / abs(base)
    return ToolResult(
        tool="compare_periods",
        ok=True,
        artifact_ids=ids,
        data={
            "metric_id": a.metric_id,
            "current_period": ctx.window.display(),
            "baseline_period": ctx.baseline.display(),
            "current": cur,
            "baseline": base,
            "abs_change": cur - base,
            "pct_change": pct,
        },
    )


def _segment_metric(a: SegmentArgs, st: ToolState) -> ToolResult:
    usable, _ = dimensions_for_metric(st.model, a.metric_id)
    if a.dimension not in {d.name for d in usable}:
        return ToolResult(
            tool="segment_metric",
            ok=False,
            error=f"dimension {a.dimension!r} cannot slice {a.metric_id!r} without fan-out",
        )
    ctx = _context(st, a.metric_id, a.period, a.comparison, a.filters)
    ex = _executor_for(st, ctx)
    cur, cur_id, err1 = ex.by_segment(a.metric_id, ctx.window, a.dimension, [], ctx.window.display())
    base, base_id, err2 = ex.by_segment(a.metric_id, ctx.baseline, a.dimension, [], ctx.baseline.display())
    st.artifacts.update(ex.artifacts)
    if cur is None or base is None:
        return ToolResult(tool="segment_metric", ok=False, error=err1 or err2, artifact_ids=[cur_id, base_id])
    rows = [
        {
            "segment": k,
            "current": cur.get(k),
            "baseline": base.get(k),
            "change": (cur.get(k) or 0.0) - (base.get(k) or 0.0),
        }
        for k in sorted(set(cur) | set(base), key=str)
    ]
    rows.sort(key=lambda r: r["change"])
    return ToolResult(
        tool="segment_metric", ok=True, artifact_ids=[cur_id, base_id], data={"rows": rows[:50]}
    )


def _create_chart(a: ChartArgs, st: ToolState) -> ToolResult:
    art = st.artifacts.get(a.artifact_id)
    if art is None or art.result is None:
        return ToolResult(tool="create_chart", ok=False, error=f"no tabular artifact {a.artifact_id!r}")
    cols = [c["name"] for c in art.result.columns]
    x = a.x or cols[0]
    y = a.y or (cols[1] if len(cols) > 1 else cols[0])
    if x not in cols or y not in cols:
        return ToolResult(tool="create_chart", ok=False, error=f"columns must be among {cols}")
    xi, yi = cols.index(x), cols.index(y)
    labels = [str(r[xi]) for r in art.result.rows]
    values = [r[yi] if isinstance(r[yi], int | float) else None for r in art.result.rows]
    spec = (
        charts.line_chart(art.title, labels, values)
        if a.chart_type == "line"
        else charts.contribution_chart(
            art.title, [(lbl, float(v or 0.0)) for lbl, v in zip(labels, values, strict=True)]
        )
    )
    chart = Artifact(
        id=stable_id("art", "chart", a.artifact_id, a.chart_type, x, y),
        kind="chart",
        title=f"Chart of {art.title}",
        chart_spec=spec,
        parent_ids=[art.id],
    )
    st.artifacts[chart.id] = chart
    return ToolResult(
        tool="create_chart", ok=True, artifact_ids=[chart.id], data={"chart_artifact_id": chart.id}
    )


def _save_finding(a: FindingArgs, st: ToolState) -> ToolResult:
    arts = [st.artifacts[i] for i in a.artifact_ids if i in st.artifacts]
    if not arts:
        return ToolResult(
            tool="save_finding", ok=False, error="a finding must cite at least one executed artifact"
        )
    check = verify_text(a.statement, EvidenceNumbers.from_run(arts))
    if not check.ok:
        return ToolResult(
            tool="save_finding",
            ok=False,
            error="statement contains numbers not found in the cited artifacts: "
            + ", ".join(check.rejected_texts),
        )
    st.findings.append(ProposedFinding(statement=a.statement, artifact_ids=[x.id for x in arts]))
    return ToolResult(tool="save_finding", ok=True, data={"status": "needs_review"})


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "arguments_schema": t.args.model_json_schema()}
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    def call(self, name: str, arguments_json: str | None, state: ToolState) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            res = ToolResult(tool=name, ok=False, error=f"unknown tool {name!r}; available: {self.names}")
            state.calls.append(res)
            return res
        try:
            raw = json.loads(arguments_json or "{}")
            args = tool.args.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            res = ToolResult(tool=name, ok=False, error=f"invalid arguments: {exc}")
            state.calls.append(res)
            return res
        try:
            res = tool.handler(args, state)
        except Exception as exc:  # tool failures are reported to the model, never hidden
            res = ToolResult(tool=name, ok=False, error=f"{type(exc).__name__}: {exc}")
        state.calls.append(res)
        return res


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for t in [
        Tool("list_tables", "List tables in the workspace store with row counts.", NoArgs, _list_tables),
        Tool("inspect_schema", "Columns and types of one table.", TableArgs, _inspect_schema),
        Tool(
            "inspect_metric",
            "Governed definition, version, drivers and usable dimensions of a metric.",
            MetricArgs,
            _inspect_metric,
        ),
        Tool(
            "run_sql",
            "Run one read-only SELECT against the workspace store (max 200 rows).",
            SqlArgs,
            _run_sql,
        ),
        Tool(
            "run_python",
            "Run Python in the sandbox (no network, time and memory limits).",
            PythonArgs,
            _run_python,
        ),
        Tool(
            "profile_column",
            "Row count, nulls, distinct count, min, max and top values of a column.",
            ColumnArgs,
            _profile_column,
        ),
        Tool(
            "compare_periods",
            "Metric value in a period vs the previous period ('pop') or prior year ('yoy').",
            CompareArgs,
            _compare_periods,
        ),
        Tool(
            "segment_metric",
            "Metric by segment of a dimension for a period and its baseline.",
            SegmentArgs,
            _segment_metric,
        ),
        Tool("create_chart", "Chart spec for a tabular artifact.", ChartArgs, _create_chart),
        Tool(
            "save_finding",
            "Propose a finding citing executed artifacts; numbers are verified; the analyst reviews it.",
            FindingArgs,
            _save_finding,
        ),
    ]:
        reg.register(t)
    return reg
