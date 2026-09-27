"""Grain-safe semantic compiler: ``MetricQuery`` -> SQL.

Algorithm
---------
1. Expand the requested metrics into the *atomic* (simple) metrics they depend on.
   Ratio metrics become ``numerator / NULLIF(denominator, 0)`` over aggregated
   atoms (ratio of sums, never an average of row-level ratios); derived metrics
   substitute their formula over atoms.
2. Group atoms by base entity (and metric-level filters). Each group becomes one
   CTE that aggregates **at the requested output grain inside the base entity's
   own rows**. From the base entity the CTE only joins *parents* (many-to-one /
   one-to-one steps over approved relationships), so base rows are never repeated.
3. Filters on entities that are only reachable through one-to-many steps (a child
   grain) become ``EXISTS`` semi-joins, which never multiply rows. Grouping by such
   a dimension is refused with :class:`GrainError` because it has no single value
   per base row.
4. CTEs are combined on the output dimensions with ``IS NOT DISTINCT FROM`` joins
   against a spine of all dimension combinations, so metrics from different grains
   (e.g. order lines and orders) are pre-aggregated before they meet.
5. A time window is always a row-level predicate on the base entity or one of its
   many-to-one parents, resolved per atom group: the query's explicit dimension when
   reachable that way, otherwise the metric's (or entity's) ``default_time_dimension``
   (with a warning when it replaces an explicit one), otherwise ``GrainError``. It is
   never a semi-join through another fact. ``period__{grain}`` (or a grained window
   without a dimension) groups every metric by its own time dimension, conformed.

All literal values are rendered through sqlglot (escaped), so the SQL artifact is
self-contained and replayable. The final SQL passes ``ensure_read_only``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlglot import exp

from ..sqlsafety import ensure_read_only
from ..types import TimeWindow
from .joins import JoinPath, JoinStep, explain_no_path, find_path
from .models import CalendarConfig, Dimension, Filter, Metric, SemanticModel, SemanticModelError

if TYPE_CHECKING:  # pragma: no cover
    from ..store import WorkspaceStore
    from ..types import QueryResult

__all__ = [
    "MetricQuery",
    "OrderBy",
    "CompiledQuery",
    "OutputColumn",
    "JoinPath",
    "JoinStep",
    "GrainError",
    "CompileError",
    "compile",
    "compile_query",
    "run_metric_query",
    "parse_dimension_ref",
    "join_key_warnings",
    "metric_maturity_days",
    "TimeWindow",
    "Filter",
]


class CompileError(ValueError):
    """The metric query is invalid (unknown metric/dimension, bad filter ...)."""


class GrainError(CompileError):
    """The requested query cannot be answered without double counting."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderBy(_Model):
    field: str
    desc: bool = False


class MetricQuery(_Model):
    """A request for metrics broken down by dimensions.

    Dimensions may carry a time grain suffix: ``order_date__month``.
    """

    metrics: list[str]
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time: TimeWindow | None = None
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)
    as_of: dt.date | None = None
    """Date the data is complete to; used to flag immature cohort metrics (``maturity_days``)."""

    @field_validator("order_by", mode="before")
    @classmethod
    def _order(cls, v: Any) -> Any:
        out = []
        for item in v or []:
            if isinstance(item, str):
                parts = item.strip().split()
                desc = item.strip().startswith("-") or (len(parts) > 1 and parts[-1].lower() == "desc")
                out.append({"field": parts[0].lstrip("-"), "desc": desc})
            else:
                out.append(item)
        return out


class OutputColumn(_Model):
    name: str
    role: Literal["dimension", "time", "metric"]
    source: str
    format: str | None = None


class CompiledQuery(_Model):
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    grain: list[str] = Field(default_factory=list)
    joins: list[JoinPath] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metric_versions: dict[str, str] = Field(default_factory=dict)
    columns: list[OutputColumn] = Field(default_factory=list)
    filters_applied: list[str] = Field(default_factory=list)
    dialect: str = "duckdb"
    query: MetricQuery | None = None
    immature_metrics: list[str] = Field(default_factory=list)
    """Metrics whose cohort had not matured by ``MetricQuery.as_of`` (values may still change)."""

    @property
    def metric_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.role == "metric"]

    @property
    def dimension_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.role != "metric"]


# --------------------------------------------------------------------------- helpers


def parse_dimension_ref(ref: str) -> tuple[str, str | None]:
    """``order_date__month`` -> (``order_date``, ``month``); ``region`` -> (``region``, None)."""
    if "__" in ref:
        name, grain = ref.rsplit("__", 1)
        return name, grain
    return ref, None


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_sql(table: str) -> str:
    return ".".join(_q(p) for p in table.split("."))


def literal_sql(value: Any) -> str:
    """Render a Python value as an escaped DuckDB SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise CompileError("filter values must be finite numbers")
        return exp.Literal.number(value).sql(dialect="duckdb")
    if isinstance(value, dt.datetime):
        return f"CAST({exp.Literal.string(value.isoformat(sep=' ')).sql(dialect='duckdb')} AS TIMESTAMP)"
    if isinstance(value, dt.date):
        return f"CAST({exp.Literal.string(value.isoformat()).sql(dialect='duckdb')} AS DATE)"
    if isinstance(value, str):
        return exp.Literal.string(value).sql(dialect="duckdb")
    raise CompileError(f"unsupported filter value type {type(value).__name__}")


def _qualify_expr(expr_sql: str, alias: str, *, what: str) -> str:
    """Parse an entity-level SQL expression and qualify bare columns with ``alias``."""
    if expr_sql.strip() == "*":
        return "*"
    try:
        tree = sqlglot.parse_one(expr_sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise CompileError(f"cannot parse {what} expression {expr_sql!r}: {exc}") from exc
    for node in tree.walk():
        if isinstance(node, exp.Query | exp.Subquery):
            raise CompileError(f"{what} expression {expr_sql!r} must not contain subqueries")
    for col in list(tree.find_all(exp.Column)):
        if not col.table:
            col.set("table", exp.to_identifier(alias, quoted=True))
        else:
            raise CompileError(
                f"{what} expression {expr_sql!r} must reference its entity's columns unqualified (found {col.sql()})"
            )
        ident = col.this
        if isinstance(ident, exp.Identifier):
            ident.set("quoted", True)
    return tree.sql(dialect="duckdb")


def _grain_sql(expr_sql: str, grain: str, cal: CalendarConfig) -> str:
    x = f"CAST({expr_sql} AS DATE)"
    if grain == "day":
        return x
    if grain == "week":
        if cal.week_start == "monday":
            return f"CAST(date_trunc('week', {x}) AS DATE)"
        return f"CAST(date_trunc('week', {x} + INTERVAL 1 DAY) - INTERVAL 1 DAY AS DATE)"
    if grain in ("month", "quarter", "year"):
        return f"CAST(date_trunc('{grain}', {x}) AS DATE)"
    shift = cal.fiscal_year_start_month - 1
    unit = "quarter" if grain == "fiscal_quarter" else "year"
    if grain in ("fiscal_quarter", "fiscal_year"):
        if shift == 0:
            return f"CAST(date_trunc('{unit}', {x}) AS DATE)"
        return f"CAST(date_trunc('{unit}', {x} - INTERVAL {shift} MONTH) + INTERVAL {shift} MONTH AS DATE)"
    raise CompileError(f"unknown time grain {grain!r}")


def _predicate(sql: str, f: Filter) -> str:
    v = f.values
    if f.op == "eq":
        return f"{sql} IS NULL" if v[0] is None else f"{sql} = {literal_sql(v[0])}"
    if f.op == "neq":
        return f"{sql} IS NOT NULL" if v[0] is None else f"({sql} <> {literal_sql(v[0])} OR {sql} IS NULL)"
    if f.op in ("in", "not_in"):
        non_null = [x for x in v if x is not None]
        has_null = len(non_null) != len(v)
        items = ", ".join(literal_sql(x) for x in non_null)
        if f.op == "in":
            parts = [f"{sql} IN ({items})"] if non_null else []
            if has_null:
                parts.append(f"{sql} IS NULL")
            return "(" + " OR ".join(parts) + ")"
        parts = [f"{sql} NOT IN ({items})"] if non_null else []
        parts.append(f"{sql} IS NOT NULL" if has_null else f"{sql} IS NULL")
        joiner = " AND " if has_null else " OR "
        if not non_null:
            return f"{sql} IS NOT NULL"
        return "(" + joiner.join(parts) + ")"
    ops = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    if f.op in ops:
        if v[0] is None:
            raise CompileError(f"filter {f.describe()} compares with NULL")
        return f"{sql} {ops[f.op]} {literal_sql(v[0])}"
    if f.op == "between":
        return f"{sql} BETWEEN {literal_sql(v[0])} AND {literal_sql(v[1])}"
    if f.op == "is_null":
        return f"{sql} IS NULL"
    if f.op == "not_null":
        return f"{sql} IS NOT NULL"
    if f.op == "contains":
        return f"strpos(lower(CAST({sql} AS VARCHAR)), lower({literal_sql(str(v[0]))})) > 0"
    if f.op == "starts_with":
        return f"starts_with(lower(CAST({sql} AS VARCHAR)), lower({literal_sql(str(v[0]))}))"
    raise CompileError(f"unsupported filter op {f.op!r}")  # pragma: no cover - Literal-validated


PERIOD = "period"


@dataclass
class _ParentJoin:
    """A many-to-one (or one-to-one) step from the base entity towards a parent."""

    table: str
    alias: str
    left_alias: str
    left_col: str
    right_col: str


def _render_joins(sql: str, joins: list[_ParentJoin]) -> str:
    """Insert the parent joins after the ``FROM`` line of ``sql``.

    A declared many-to-one relationship is only safe if the parent key really is unique.
    Parents are therefore joined through a subquery that keeps exactly one row per join key
    (the first by the columns the query reads, so the choice is deterministic). When the key
    is unique this is the table itself; when it is not (duplicate customer rows, say) fact
    rows are still counted once instead of once per duplicate. :func:`join_key_warnings`
    reports such keys at run time.
    """
    if not joins:
        return sql
    at = sql.index("\nFROM ")
    head, rest = sql[:at], sql[at + 1 :]
    from_line, sep, tail = rest.partition("\n")
    clauses = []
    for j in joins:
        cols = _columns_used(sql, j.alias, joins)
        cols = [j.right_col, *[c for c in cols if c != j.right_col]]
        col_list = ", ".join(_q(c) for c in cols)
        order = ", ".join(f"{_q(c)} NULLS LAST" for c in cols[1:]) or _q(j.right_col)
        source = (
            f"(SELECT {col_list} FROM (SELECT {col_list}, ROW_NUMBER() OVER (PARTITION BY {_q(j.right_col)} "
            f'ORDER BY {order}) AS "__key_rank" FROM {_table_sql(j.table)}) AS "__keyed" '
            f'WHERE "__key_rank" = 1)'
        )
        clauses.append(
            f"LEFT JOIN {source} AS {_q(j.alias)} "
            f"ON {_q(j.left_alias)}.{_q(j.left_col)} = {_q(j.alias)}.{_q(j.right_col)}"
        )
    return head + "\n" + from_line + "\n" + "\n".join(clauses) + (sep + tail if sep else "")


def _columns_used(sql: str, alias: str, joins: list[_ParentJoin]) -> list[str]:
    """Columns of ``alias`` referenced in ``sql`` or by the joins that start from it."""
    import re

    pattern = re.compile(re.escape(_q(alias)) + r'\.("(?:[^"]|"")+")')
    cols: list[str] = []
    for m in pattern.finditer(sql):
        name = m.group(1)[1:-1].replace('""', '"')
        if name not in cols:
            cols.append(name)
    for j in joins:
        if j.left_alias == alias and j.left_col not in cols:
            cols.append(j.left_col)
    return cols


@dataclass
class _Group:
    key: str
    entity: str
    metric_filters: list[Filter]
    time_dim: Dimension | None = None
    atoms: list[Metric] = field(default_factory=list)
    time_aggregation: str = "sum"

    @property
    def name(self) -> str:
        return f"agg_{self.entity}"


@dataclass
class _DimRef:
    ref: str
    dimension: Dimension | None  # None: the conformed ``period`` (each group's own time dimension)
    grain: str | None
    role: Literal["dimension", "time"]

    @property
    def source(self) -> str:
        return self.dimension.name if self.dimension is not None else PERIOD


# --------------------------------------------------------------------------- compiler


class _Compiler:
    def __init__(self, model: SemanticModel, q: MetricQuery) -> None:
        self.model = model
        self.q = q
        self.warnings: list[str] = []
        self.joins: list[JoinPath] = []
        self.filters_applied: list[str] = []

    # -- resolution -----------------------------------------------------------------
    def dimension_refs(self) -> list[_DimRef]:
        refs: list[_DimRef] = []
        seen: set[str] = set()
        for ref in self.q.dimensions:
            name, grain = parse_dimension_ref(ref)
            if name == PERIOD and not self.model.has_dimension(PERIOD):
                if grain is None or grain not in (
                    "day",
                    "week",
                    "month",
                    "quarter",
                    "year",
                    "fiscal_quarter",
                    "fiscal_year",
                ):
                    raise CompileError(f"'{PERIOD}' needs a time grain, e.g. {PERIOD}__month")
                if ref not in seen:
                    seen.add(ref)
                    refs.append(_DimRef(ref, None, grain, "time"))
                continue
            dim = self._dimension(name)
            if grain is not None:
                if dim.type != "time":
                    raise CompileError(f"time grain {grain!r} requested on non-time dimension {name!r}")
                if grain not in dim.time_grains and grain not in ("fiscal_quarter", "fiscal_year"):
                    raise CompileError(
                        f"dimension {name!r} does not support grain {grain!r} (supports {dim.time_grains})"
                    )
            out = ref if grain else name
            if out in seen:
                continue
            seen.add(out)
            refs.append(_DimRef(out, dim, grain, "time" if grain else "dimension"))
        t = self.q.time
        if t is not None and t.grain:
            if t.dimension:
                tdim = self._dimension(t.dimension)
                if tdim.type != "time":
                    raise CompileError(f"time window dimension {tdim.name!r} is not a time dimension")
                out, dref = f"{tdim.name}__{t.grain}", tdim
            else:
                out, dref = f"{PERIOD}__{t.grain}", None
            if out not in seen:
                refs.append(_DimRef(out, dref, t.grain, "time"))
        return refs

    def _dimension(self, name: str) -> Dimension:
        try:
            return self.model.get_dimension(name)
        except SemanticModelError as exc:
            raise CompileError(str(exc)) from exc

    def _reachable(self, entity: str, target: str) -> bool:
        return find_path(self.model, entity, target, safe_only=True) is not None

    def atom_time_dimension(self, atom: Metric) -> Dimension:
        """The time dimension that dates ``atom``'s rows.

        A time window is always a row-level predicate on the metric's own entity or one
        of its many-to-one parents, never a semi-join through another fact. Resolution:
        the query's explicit dimension when it is reachable that way, otherwise the
        metric's (or its entity's) ``default_time_dimension``; otherwise ``GrainError``.
        """
        entity = atom.entity or ""
        own = atom.default_time_dimension or self.model.get_entity(entity).default_time_dimension
        explicit = self.q.time.dimension if self.q.time is not None else None
        if explicit:
            dim = self._dimension(explicit)
            if dim.type != "time":
                raise CompileError(f"time window dimension {explicit!r} is not a time dimension")
            if self._reachable(entity, dim.entity):
                return dim
            if own:
                fallback = self._dimension(own)
                if fallback.type == "time" and self._reachable(entity, fallback.entity):
                    self.warnings.append(
                        f"{atom.id} cannot be dated by {explicit} ({dim.entity} is not a many-to-one parent of "
                        f"{entity}); the time window is applied to its own time dimension {fallback.name}"
                    )
                    return fallback
            raise GrainError(
                f"cannot apply a time window on {explicit!r} to {atom.id} ({entity}): {dim.entity} is not {entity} "
                f"or one of its many-to-one parents, and {atom.id} has no usable default_time_dimension"
            )
        if not own:
            raise CompileError(
                f"no time dimension for {atom.id}: set TimeWindow.dimension or define default_time_dimension "
                f"on metric {atom.id} or entity {entity}"
            )
        dim = self._dimension(own)
        if dim.type != "time":
            raise CompileError(f"default time dimension {own!r} of {atom.id} is not a time dimension")
        if not self._reachable(entity, dim.entity):
            raise GrainError(
                f"default time dimension {own!r} of {atom.id} is on {dim.entity}, which is not {entity} or one of "
                "its many-to-one parents"
            )
        return dim

    def atoms_and_exprs(self) -> tuple[list[Metric], dict[str, exp.Expression]]:
        atoms: dict[str, Metric] = {}

        def build(mid: str, stack: tuple[str, ...]) -> exp.Expression:
            if mid in stack:
                raise CompileError(f"circular metric definition: {' -> '.join((*stack, mid))}")
            try:
                m = self.model.get_metric(mid)
            except SemanticModelError as exc:
                raise CompileError(str(exc)) from exc
            if m.kind == "simple":
                atoms[m.id] = m
                return exp.column(f"__atom__{m.id}")
            if m.kind == "ratio":
                num = build(m.numerator or "", (*stack, mid))
                den = build(m.denominator or "", (*stack, mid))
                return _safe_div(num, den)
            try:
                tree = sqlglot.parse_one(m.formula or "", read="duckdb")
            except sqlglot.errors.ParseError as exc:
                raise CompileError(f"cannot parse formula of {mid!r}: {exc}") from exc
            if any(isinstance(n, exp.AggFunc | exp.Query) for n in tree.walk()):
                raise CompileError(f"derived metric {mid!r} formula must combine metric ids only")

            def guard_div(node: exp.Expression) -> exp.Expression:
                if isinstance(node, exp.Div):
                    return _safe_div(node.this, node.expression)
                return node

            def substitute(node: exp.Expression) -> exp.Expression:
                if isinstance(node, exp.Column):
                    if node.table:
                        raise CompileError(f"derived metric {mid!r} formula must reference metric ids")
                    return exp.paren(build(node.name, (*stack, mid)), copy=False)
                return node

            return tree.transform(guard_div).transform(substitute)

        exprs = {mid: build(mid, ()) for mid in dict.fromkeys(self.q.metrics)}
        return list(atoms.values()), exprs

    # -- per-entity CTE ----------------------------------------------------------------
    def build_group_sql(self, group: _Group, dims: list[_DimRef]) -> tuple[str, list[str]]:
        base = self.model.get_entity(group.entity)
        base_alias = base.name
        joined: dict[str, str] = {base.name: base_alias}
        join_clauses: list[_ParentJoin] = []

        def ensure_joined(entity: str) -> str | None:
            """Join ``entity`` along a fan-out safe path; None when only a child path exists."""
            if entity in joined:
                return joined[entity]
            path = find_path(self.model, base.name, entity, safe_only=True)
            if path is None:
                return None
            if path.ambiguous:
                self.warnings.append(
                    f"several relationship paths join {base.name} to {entity}; using {path.describe()}"
                )
            self.joins.append(path)
            for step in path.steps:
                if step.right_entity in joined:
                    continue
                right = self.model.get_entity(step.right_entity)
                alias = right.name
                left_alias = joined[step.left_entity]
                join_clauses.append(
                    _ParentJoin(right.table, alias, left_alias, step.left_col, step.right_col)
                )
                joined[step.right_entity] = alias
            return joined[entity]

        select_parts: list[str] = []
        group_exprs: list[str] = []
        for d in dims:
            ddim = d.dimension if d.dimension is not None else group.time_dim
            if (
                ddim is None
            ):  # pragma: no cover - groups always resolve a time dimension when period is requested
                raise CompileError(f"{d.ref} needs a time dimension for {group.entity}")
            alias = ensure_joined(ddim.entity)
            if alias is None:
                raise GrainError(self._grain_message(group, ddim))
            sql = _qualify_expr(ddim.expr, alias, what=f"dimension {ddim.name}")
            if d.grain:
                sql = _grain_sql(sql, d.grain, self.model.calendar)
            select_parts.append(f"{sql} AS {_q(d.ref)}")
            group_exprs.append(sql)

        where: list[str] = []
        row_filters: list[str] = []  # query filters (kept apart for semi-additive groups)
        all_filters = [(f, "query") for f in self.q.filters] + [(f, "metric") for f in group.metric_filters]
        for f, origin in all_filters:
            dim = self._dimension(f.dimension)
            pred = self._filter_sql(base.name, dim, lambda e, f=f: _predicate(e, f), ensure_joined)
            (row_filters if origin == "query" else where).append(pred)
            label = f.describe() + (" (metric definition)" if origin == "metric" else "")
            if label not in self.filters_applied:
                self.filters_applied.append(label)
        t = self.q.time
        if t is not None:
            tdim = group.time_dim
            assert tdim is not None
            alias = ensure_joined(tdim.entity)
            if alias is None:  # pragma: no cover - atom_time_dimension guarantees reachability
                raise GrainError(f"time dimension {tdim.name} is not reachable from {group.entity}")
            texpr = _qualify_expr(tdim.expr, alias, what=f"dimension {tdim.name}")
            start, end = literal_sql(t.start), literal_sql(t.end)
            where.append(f"CAST({texpr} AS DATE) >= {start} AND CAST({texpr} AS DATE) < {end}")
            label = f"{tdim.name} in [{t.start.isoformat()}, {t.end.isoformat()})"
            if label not in self.filters_applied:
                self.filters_applied.append(label)

        if group.time_aggregation != "sum":
            return self._semi_additive_sql(
                group, dims, base, select_parts, join_clauses, where, row_filters, ensure_joined
            )
        where.extend(row_filters)
        for atom in group.atoms:
            select_parts.append(f"{self._agg_sql(atom, base_alias)} AS {_q(atom.id)}")

        sql = "SELECT " + ", ".join(select_parts) + f"\nFROM {_table_sql(base.table)} AS {_q(base_alias)}"
        if where:
            sql += "\nWHERE " + "\n  AND ".join(f"({w})" for w in where)
        sql = _render_joins(sql, join_clauses)
        if group_exprs:
            sql += "\nGROUP BY " + ", ".join(group_exprs)
        return sql, [d.ref for d in dims]

    def _semi_additive_sql(
        self,
        group: _Group,
        dims: list[_DimRef],
        base: Any,
        select_parts: list[str],
        join_clauses: list[_ParentJoin],
        where: list[str],
        row_filters: list[str],
        ensure_joined: Any,
    ) -> tuple[str, list[str]]:
        """Aggregate a balance metric at its edge date (last/first) or averaged over dates.

        Rows are first restricted by the time window and the metric's own filters. The edge
        date (or the number of dates, for ``avg``) is then computed per output *time* bucket
        only, so every segment of a breakdown is read at the same date and segment values add
        up to the total. Query filters apply after that choice: a segment with no rows on the
        edge date contributes nothing rather than an older balance.
        """
        tdim = group.time_dim
        assert tdim is not None
        alias = ensure_joined(tdim.entity)
        if alias is None:  # pragma: no cover - atom_time_dimension guarantees reachability
            raise GrainError(f"time dimension {tdim.name} is not reachable from {group.entity}")
        at_sql = f"CAST({_qualify_expr(tdim.expr, alias, what=f'dimension {tdim.name}')} AS DATE)"
        refs = [d.ref for d in dims]
        buckets = [
            _q(d.ref)
            for d in dims
            if d.dimension is None or d.grain is not None or d.dimension.type == "time"
        ]
        inner = list(select_parts)
        for atom in group.atoms:
            expr_sql = _qualify_expr(atom.expr or "*", base.name, what=f"metric {atom.id}")
            inner.append(f"{'1' if expr_sql == '*' else expr_sql} AS {_q('__in_' + atom.id)}")
        inner.append(f'{at_sql} AS "__at"')
        keep = " AND ".join(f"({w})" for w in row_filters) if row_filters else "TRUE"
        inner.append(f'({keep}) AS "__keep"')
        sql = "SELECT " + ", ".join(inner) + f"\nFROM {_table_sql(base.table)} AS {_q(base.name)}"
        if where:
            sql += "\nWHERE " + "\n  AND ".join(f"({w})" for w in where)
        sql = _render_joins(sql, join_clauses)
        over = f"OVER (PARTITION BY {', '.join(buckets)})" if buckets else "OVER ()"
        mode = group.time_aggregation
        dim_cols = [_q(r) for r in refs]
        if mode in ("last", "first"):
            edge = "MAX" if mode == "last" else "MIN"
            middle = f'SELECT *, {edge}("__at") {over} AS "__edge"\nFROM (\n{_indent(sql)}\n) AS "__rows"'
            aggs = [f"{self._agg_input_sql(a)} AS {_q(a.id)}" for a in group.atoms]
            outer = (
                "SELECT "
                + ", ".join(dim_cols + aggs)
                + f'\nFROM (\n{_indent(middle)}\n) AS "__edged"\nWHERE "__keep" AND "__at" = "__edge"'
            )
            if dim_cols:
                outer += "\nGROUP BY " + ", ".join(dim_cols)
            rule = f"{mode} {tdim.name}"
        else:
            middle = (
                f'SELECT *, COUNT(DISTINCT "__at") {over} AS "__dates"\nFROM (\n{_indent(sql)}\n) AS "__rows"'
            )
            per_date = [f"{self._agg_input_sql(a)} AS {_q(a.id)}" for a in group.atoms]
            daily = (
                "SELECT "
                + ", ".join([*dim_cols, '"__at"', 'ANY_VALUE("__dates") AS "__dates"', *per_date])
                + f'\nFROM (\n{_indent(middle)}\n) AS "__counted"\nWHERE "__keep"\nGROUP BY '
                + ", ".join([*dim_cols, '"__at"'])
            )
            avgs = [f'SUM({_q(a.id)}) / ANY_VALUE("__dates") AS {_q(a.id)}' for a in group.atoms]
            outer = "SELECT " + ", ".join(dim_cols + avgs) + f'\nFROM (\n{_indent(daily)}\n) AS "__daily"'
            if dim_cols:
                outer += "\nGROUP BY " + ", ".join(dim_cols)
            rule = f"average over {tdim.name} dates"
        scope = "each period" if buckets else "the window" if self.q.time is not None else "the data"
        for atom in group.atoms:
            label = f"{atom.id}: {rule} in {scope} (semi-additive)"
            if label not in self.filters_applied:
                self.filters_applied.append(label)
        return outer, refs

    def _agg_input_sql(self, m: Metric) -> str:
        col = _q("__in_" + m.id)
        if m.agg == "count":
            return "COUNT(*)" if (m.expr or "*").strip() == "*" else f"COUNT({col})"
        if m.agg == "count_distinct":
            return f"COUNT(DISTINCT {col})"
        return f"{str(m.agg).upper()}({col})"

    def _agg_sql(self, m: Metric, alias: str) -> str:
        expr_sql = _qualify_expr(m.expr or "*", alias, what=f"metric {m.id}")
        if m.agg == "count":
            return "COUNT(*)" if expr_sql == "*" else f"COUNT({expr_sql})"
        if expr_sql == "*":
            raise CompileError(f"metric {m.id!r}: '*' is only valid with agg=count")
        if m.agg == "count_distinct":
            return f"COUNT(DISTINCT {expr_sql})"
        return f"{str(m.agg).upper()}({expr_sql})"

    def _filter_sql(self, base: str, dim: Dimension, make_pred: Any, ensure_joined: Any) -> str:
        alias = ensure_joined(dim.entity)
        if alias is not None:
            return make_pred(_qualify_expr(dim.expr, alias, what=f"dimension {dim.name}"))
        # Only reachable through a one-to-many (child) path: semi-join, never fan out.
        path = find_path(self.model, base, dim.entity, safe_only=False)
        if path is None:
            raise GrainError(
                f"cannot filter on {dim.name!r}: " + explain_no_path(self.model, base, dim.entity)
            )
        self.joins.append(path)
        self.warnings.append(
            f"filter on {dim.name} ({dim.entity}) is applied to {base} as an EXISTS semi-join "
            f"({path.describe()}): {base} rows are kept when at least one related {dim.entity} row matches"
        )
        aliases: dict[str, str] = {}
        from_parts: list[str] = []
        first = path.steps[0]
        for i, step in enumerate(path.steps):
            ent = self.model.get_entity(step.right_entity)
            a = f"_sj{i}_{ent.name}"
            aliases[step.right_entity] = a
            if i == 0:
                from_parts.append(f"FROM {_table_sql(ent.table)} AS {_q(a)}")
            else:
                prev = aliases[step.left_entity]
                from_parts.append(
                    f"JOIN {_table_sql(ent.table)} AS {_q(a)} ON {_q(prev)}.{_q(step.left_col)} = {_q(a)}.{_q(step.right_col)}"
                )
        target_alias = aliases[dim.entity]
        pred = make_pred(_qualify_expr(dim.expr, target_alias, what=f"dimension {dim.name}"))
        corr = f"{_q(aliases[first.right_entity])}.{_q(first.right_col)} = {_q(base)}.{_q(first.left_col)}"
        return "EXISTS (SELECT 1 " + " ".join(from_parts) + f" WHERE {corr} AND ({pred}))"

    def _grain_message(self, group: _Group, dim: Dimension) -> str:
        metrics = ", ".join(a.id for a in group.atoms)
        unsafe = find_path(self.model, group.entity, dim.entity, safe_only=False)
        if unsafe is None:
            return f"cannot group {metrics} by {dim.name!r}: " + explain_no_path(
                self.model, group.entity, dim.entity
            )
        return (
            f"cannot group {metrics} (grain: {group.entity}) by {dim.name!r} (grain: {dim.entity}): "
            f"{unsafe.describe()} is one-to-many, so a {group.entity} row has no single {dim.name} and "
            f"would be counted once per {dim.entity} row. Use a metric defined at the {dim.entity} grain, "
            f"or a dimension of {group.entity} or its parents."
        )

    # -- assembly --------------------------------------------------------------------
    def compile(self) -> tuple[str, list[_DimRef], dict[str, exp.Expression], list[Metric]]:
        if not self.q.metrics:
            raise CompileError("a metric query needs at least one metric")
        dims = self.dimension_refs()
        atoms, exprs = self.atoms_and_exprs()
        needs_time = self.q.time is not None or any(d.dimension is None for d in dims)
        groups: dict[str, _Group] = {}
        for a in atoms:
            tdim = self.atom_time_dimension(a) if needs_time or a.time_aggregation != "sum" else None
            fkey = json.dumps([f.model_dump(mode="json") for f in a.filters], sort_keys=True)
            key = hashlib.sha256(
                f"{a.entity}|{fkey}|{tdim.name if tdim else ''}|{a.time_aggregation}".encode()
            ).hexdigest()
            g = groups.setdefault(
                key,
                _Group(
                    key=key,
                    entity=a.entity or "",
                    metric_filters=list(a.filters),
                    time_dim=tdim,
                    time_aggregation=a.time_aggregation,
                ),
            )
            g.atoms.append(a)
            if a.agg == "avg":
                self.warnings.append(
                    f"{a.id} is an average of {a.entity} rows; it is not additive across segments"
                )
        ctes: list[tuple[str, _Group]] = []
        cte_sql: list[str] = []
        used_names: set[str] = set()
        for g in groups.values():
            name = g.name
            i = 2
            while name in used_names:
                name = f"{g.name}_{i}"
                i += 1
            used_names.add(name)
            body, _ = self.build_group_sql(g, dims)
            cte_sql.append(f"{_q(name)} AS (\n{_indent(body)}\n)")
            ctes.append((name, g))

        atom_ref: dict[str, str] = {}
        multi = len(ctes) > 1
        for name, g in ctes:
            for a in g.atoms:
                ref = f"{_q(name)}.{_q(a.id)}"
                if multi and dims and a.agg in ("sum", "count", "count_distinct"):
                    ref = f"COALESCE({ref}, 0)"
                atom_ref[a.id] = ref

        dim_cols = [d.ref for d in dims]
        if not dims:
            from_sql = "FROM " + " CROSS JOIN ".join(_q(n) for n, _ in ctes)
            dim_select: list[str] = []
        elif not multi:
            from_sql = f"FROM {_q(ctes[0][0])}"
            dim_select = [f"{_q(ctes[0][0])}.{_q(c)} AS {_q(c)}" for c in dim_cols]
        else:
            cols = ", ".join(_q(c) for c in dim_cols)
            spine = "\nUNION\n".join(f"SELECT {cols} FROM {_q(n)}" for n, _ in ctes)
            cte_sql.append(f'"spine" AS (\n{_indent(spine)}\n)')
            joins = []
            for n, _ in ctes:
                cond = " AND ".join(f'"spine".{_q(c)} IS NOT DISTINCT FROM {_q(n)}.{_q(c)}' for c in dim_cols)
                joins.append(f"LEFT JOIN {_q(n)} ON {cond}")
            from_sql = '"spine"\n' + "\n".join(joins)
            from_sql = "FROM " + from_sql
            dim_select = [f'"spine".{_q(c)} AS {_q(c)}' for c in dim_cols]

        metric_select = []
        for mid, e in exprs.items():
            rendered = e.sql(dialect="duckdb")
            for aid, ref in sorted(atom_ref.items(), key=lambda kv: -len(kv[0])):
                rendered = rendered.replace(f"__atom__{aid}", ref)
            metric_select.append(f"{rendered} AS {_q(mid)}")

        sql = (
            "WITH "
            + ",\n".join(cte_sql)
            + "\nSELECT "
            + ", ".join(dim_select + metric_select)
            + "\n"
            + from_sql
        )
        order = self._order_sql(dim_cols, list(exprs))
        if order:
            sql += "\nORDER BY " + order
        if self.q.limit:
            sql += f"\nLIMIT {int(self.q.limit)}"
        return sql, dims, exprs, atoms

    def _order_sql(self, dim_cols: list[str], metric_cols: list[str]) -> str:
        valid = set(dim_cols) | set(metric_cols)
        parts = []
        for o in self.q.order_by:
            if o.field not in valid:
                raise CompileError(f"cannot order by {o.field!r}; output columns are {sorted(valid)}")
            parts.append(f"{_q(o.field)} {'DESC' if o.desc else 'ASC'} NULLS LAST")
        # Always finish with every dimension so the row order is deterministic.
        for c in dim_cols:
            if not any(o.field == c for o in self.q.order_by):
                parts.append(f"{_q(c)} ASC NULLS LAST")
        return ", ".join(parts)


def _safe_div(num: exp.Expression, den: exp.Expression) -> exp.Expression:
    numerator = exp.cast(exp.paren(num.copy(), copy=False), "DOUBLE")
    denominator = exp.func("NULLIF", exp.paren(den.copy(), copy=False), exp.Literal.number(0))
    return exp.paren(exp.Div(this=numerator, expression=denominator), copy=False)


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


def compile(model: SemanticModel, q: MetricQuery, *, dialect: str = "duckdb") -> CompiledQuery:  # noqa: A001
    """Compile ``q`` against ``model`` into grain-safe SQL.

    Raises :class:`CompileError` for invalid queries and :class:`GrainError` when the
    query cannot be answered without double counting.
    """
    c = _Compiler(model, q)
    sql, dims, exprs, _atoms = c.compile()
    sql = ensure_read_only(sql, "duckdb")
    out_dialect = dialect.lower()
    if out_dialect != "duckdb":
        sql = sqlglot.transpile(sql, read="duckdb", write=out_dialect)[0]
        sql = ensure_read_only(sql, out_dialect)
    pretty = sqlglot.transpile(sql, read=out_dialect, write=out_dialect, pretty=True)[0]
    columns = [OutputColumn(name=d.ref, role=d.role, source=d.source) for d in dims]
    for mid in exprs:
        m = model.get_metric(mid)
        columns.append(OutputColumn(name=mid, role="metric", source=m.version_id, format=m.format))
    immature: list[str] = []
    if q.as_of is not None and q.time is not None:
        for mid in exprs:
            days = max(
                (model.get_metric(d).maturity_days or 0 for d in model.metric_dependencies(mid)), default=0
            )
            if days and q.time.end + dt.timedelta(days=days - 1) > q.as_of:
                immature.append(mid)
                c.warnings.append(
                    f"{mid} is immature for this period: cohorts need {days} days after the period ends "
                    f"({q.time.end + dt.timedelta(days=days - 1)}) but the data is as of {q.as_of}; the value "
                    "will still change"
                )
    warnings = list(dict.fromkeys(c.warnings))
    seen_paths: set[str] = set()
    joins: list[JoinPath] = []
    for p in c.joins:
        key = p.describe()
        if key not in seen_paths:
            seen_paths.add(key)
            joins.append(p)
    return CompiledQuery(
        sql=pretty,
        params={},
        grain=[d.ref for d in dims],
        joins=joins,
        warnings=warnings,
        metric_versions=model.metric_versions(list(exprs)),
        columns=columns,
        filters_applied=c.filters_applied,
        dialect=out_dialect,
        query=q,
        immature_metrics=immature,
    )


compile_query = compile


def metric_maturity_days(model: SemanticModel, metric_id: str) -> int:
    """Days after a period's end before ``metric_id`` is final (max over its inputs; 0 = at once)."""
    return max(
        (model.get_metric(d).maturity_days or 0 for d in model.metric_dependencies(metric_id)), default=0
    )


def join_key_warnings(store: WorkspaceStore, model: SemanticModel, compiled: CompiledQuery) -> list[str]:
    """Warnings for parent join keys that are not unique in the data.

    The compiler keeps one row per parent key, so duplicates never multiply fact rows, but
    a non-unique "one" side means the declared many-to-one relationship does not hold and,
    if the duplicate rows disagree, which attribute value is used is arbitrary (the first
    by value). Results are cached per table row count, so repeated queries stay cheap.
    """
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in compiled.joins:
        for step in path.steps:
            if not step.fanout_safe:
                continue
            try:
                table = model.get_entity(step.right_entity).table
            except SemanticModelError:
                continue
            key = (table, step.right_col)
            if key in seen:
                continue
            seen.add(key)
            stats = _key_stats(store, table, step.right_col)
            if stats is None:
                continue
            rows, keys, conflicting = stats
            if rows > keys:
                detail = (
                    f"{conflicting} key(s) have rows that disagree, so one value per key was chosen"
                    if conflicting
                    else "the duplicates are identical rows"
                )
                out.append(
                    f"join key {step.right_col} is not unique in {table} ({rows} rows, {keys} distinct keys) "
                    f"although {step.relationship_id} is declared {step.cardinality}; duplicates were collapsed "
                    f"to one row per key so {step.left_entity} rows are counted once ({detail})"
                )
    return out


_KEY_STATS_CACHE: dict[tuple[str, str, str, int], tuple[int, int, int]] = {}


def _key_stats(store: WorkspaceStore, table: str, col: str) -> tuple[int, int, int] | None:
    from ..store import StoreError, quote_ident

    try:
        version = getattr(store, "data_version", None)
        cache_key = (
            repr(version) if version is not None else f"{id(store)}:{store.row_count(table)}",
            table,
            col,
            0,
        )
        if cache_key not in _KEY_STATS_CACHE:
            t, c = quote_ident(table), quote_ident(col)
            r = store.execute_read(
                f"SELECT count(*), count(DISTINCT {c}), "
                f"(SELECT count(*) FROM (SELECT {c} FROM (SELECT DISTINCT * FROM {t}) AS d "
                f"GROUP BY {c} HAVING count(*) > 1) AS x) FROM {t}",
                limit=1,
            ).rows[0]
            if len(_KEY_STATS_CACHE) > 4096:
                _KEY_STATS_CACHE.clear()
            _KEY_STATS_CACHE[cache_key] = (int(r[0]), int(r[1]), int(r[2]))
        return _KEY_STATS_CACHE[cache_key]
    except StoreError:
        return None


def run_metric_query(
    store: WorkspaceStore,
    model: SemanticModel,
    q: MetricQuery,
    *,
    limit: int | None = 100_000,
    timeout_s: float | None = None,
) -> tuple[CompiledQuery, QueryResult]:
    """Compile ``q`` and execute it read-only on ``store``.

    Parent join keys that turn out not to be unique are reported in ``compiled.warnings``.
    """
    compiled = compile(model, q)
    result = store.execute_read(compiled.sql, limit=limit, timeout_s=timeout_s)
    extra = [w for w in join_key_warnings(store, model, compiled) if w not in compiled.warnings]
    if extra:
        compiled = compiled.model_copy(update={"warnings": [*compiled.warnings, *extra]})
    return compiled, result
