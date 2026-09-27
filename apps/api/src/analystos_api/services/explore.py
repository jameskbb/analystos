"""Data Explorer and pivot tables.

Two sources:

* **Semantic** (``MetricQuery``): compiled by the engine's grain-safe semantic compiler.
* **Table** (``TableQuery``): a single dataset explored without SQL. SQL is generated with
  sqlglot from validated column names (quoted identifiers) and literal values; calculated
  fields are parsed and restricted to column references, literals, arithmetic and an allow-list
  of scalar functions.

Pivot subtotals and totals are *re-queried* at their own grain (never summed from detail
cells), so non-additive measures (ratios, averages, distinct counts) stay correct.
"""

from __future__ import annotations

from typing import Any, Literal

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from ..errors import Unprocessable

AggFn = Literal["sum", "count", "count_distinct", "avg", "min", "max", "median"]
FilterOp = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "is_null",
    "not_null",
    "contains",
    "starts_with",
]

_ALLOWED_FUNCS = {
    "abs",
    "round",
    "floor",
    "ceil",
    "ceiling",
    "coalesce",
    "nullif",
    "lower",
    "upper",
    "trim",
    "ltrim",
    "rtrim",
    "length",
    "substr",
    "substring",
    "concat",
    "replace",
    "date_trunc",
    "date_part",
    "extract",
    "year",
    "month",
    "day",
    "quarter",
    "week",
    "dayofweek",
    "greatest",
    "least",
    "if",
    "ln",
    "log",
    "log10",
    "exp",
    "sqrt",
    "power",
    "pow",
    "sign",
    "strftime",
    "left",
    "right",
    "try_cast",
    "cast",
    "case",
    "datediff",
    "date_diff",
}
_ALLOWED_NODES = (
    exp.Column,
    exp.Identifier,
    exp.Literal,
    exp.Null,
    exp.Boolean,
    exp.Paren,
    exp.Neg,
    exp.Not,
    exp.Add,
    exp.Sub,
    exp.Mul,
    exp.Div,
    exp.Mod,
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.And,
    exp.Or,
    exp.Is,
    exp.Case,
    exp.If,
    exp.Cast,
    exp.TryCast,
    exp.DataType,
    exp.Coalesce,
    exp.Between,
    exp.In,
    exp.Like,
    exp.ILike,
    exp.Concat,
    exp.DPipe,
    exp.Interval,
    exp.Var,
    exp.Star,
    exp.Tuple,
    exp.Distinct,
)


class ColumnFilter(BaseModel):
    column: str
    op: FilterOp = "eq"
    values: list[Any] = Field(default_factory=list)


class Aggregate(BaseModel):
    column: str = Field(description="Column or calculated field; '*' for count(*)")
    fn: AggFn = "sum"
    alias: str | None = None


class CalculatedField(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    expr: str = Field(max_length=2000)


class GroupBy(BaseModel):
    column: str
    grain: Literal["day", "week", "month", "quarter", "year"] | None = None


class OrderField(BaseModel):
    field: str
    desc: bool = False


class TableQuery(BaseModel):
    table: str
    filters: list[ColumnFilter] = Field(default_factory=list)
    group_by: list[GroupBy | str] = Field(default_factory=list)
    aggregates: list[Aggregate] = Field(default_factory=list)
    calculated: list[CalculatedField] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list, description="Columns to show when not aggregating")
    order_by: list[OrderField] = Field(default_factory=list)
    limit: int = Field(default=1000, ge=1, le=10_000)


def _q(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)  # type: ignore[return-value]


def _lit(v: Any) -> exp.Expr:
    return exp.convert(v)


def _calc_expr(field: CalculatedField, allowed_cols: set[str]) -> exp.Expr:
    try:
        tree = sqlglot.parse_one(field.expr, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise Unprocessable(
            f"Calculated field {field.name!r}: cannot parse expression ({exc})", code="invalid_expression"
        ) from exc
    for node in tree.walk():
        if isinstance(node, exp.AggFunc):
            raise Unprocessable(
                f"Calculated field {field.name!r} must be row-level (use aggregates for sums)",
                code="invalid_expression",
            )
        if isinstance(node, exp.Func) and not isinstance(node, _ALLOWED_NODES):
            name = (node.sql_name() or "").lower()
            if name not in _ALLOWED_FUNCS:
                raise Unprocessable(
                    f"Function {name or type(node).__name__} is not allowed in calculated fields",
                    code="invalid_expression",
                )
            continue
        if isinstance(node, exp.Anonymous):
            if str(node.name).lower() not in _ALLOWED_FUNCS:
                raise Unprocessable(
                    f"Function {node.name} is not allowed in calculated fields", code="invalid_expression"
                )
            continue
        if isinstance(node, exp.Column):
            if node.table or node.name not in allowed_cols:
                raise Unprocessable(
                    f"Calculated field {field.name!r} references unknown column {node.sql()}",
                    code="unknown_column",
                )
            node.set("this", _q(node.name))
            continue
        if isinstance(node, exp.Select | exp.Subquery | exp.Table | exp.Query):
            raise Unprocessable("Calculated fields cannot contain queries", code="invalid_expression")
        if not isinstance(node, (*_ALLOWED_NODES, exp.Func)):
            raise Unprocessable(
                f"Unsupported construct in calculated field: {type(node).__name__}", code="invalid_expression"
            )
    return tree


def _predicate(col: exp.Expr, f: ColumnFilter) -> exp.Expr:
    v = f.values
    need = {"is_null": 0, "not_null": 0, "between": 2}.get(f.op)
    if need is not None and len(v) != need:
        raise Unprocessable(f"Filter {f.op} on {f.column} needs {need} value(s)", code="invalid_filter")
    if need is None and f.op not in ("in", "not_in") and len(v) != 1:
        raise Unprocessable(f"Filter {f.op} on {f.column} needs exactly one value", code="invalid_filter")
    if f.op in ("in", "not_in") and not v:
        raise Unprocessable(f"Filter {f.op} on {f.column} needs values", code="invalid_filter")
    op = f.op
    if op == "eq":
        return exp.EQ(this=col, expression=_lit(v[0]))
    if op == "neq":
        return exp.NEQ(this=col, expression=_lit(v[0]))
    if op in ("gt", "gte", "lt", "lte"):
        cls = {"gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}[op]
        return cls(this=col, expression=_lit(v[0]))
    if op == "between":
        return exp.Between(this=col, low=_lit(v[0]), high=_lit(v[1]))
    if op in ("in", "not_in"):
        node = exp.In(this=col, expressions=[_lit(x) for x in v])
        return exp.Not(this=node) if op == "not_in" else node
    if op == "is_null":
        return exp.Is(this=col, expression=exp.Null())
    if op == "not_null":
        return exp.Not(this=exp.Is(this=col, expression=exp.Null()))
    if op == "contains":
        return exp.ILike(this=exp.cast(col, "VARCHAR"), expression=_lit(f"%{v[0]}%"))
    return exp.ILike(this=exp.cast(col, "VARCHAR"), expression=_lit(f"{v[0]}%"))


def _agg(fn: str, col: exp.Expr | None) -> exp.Expr:
    if fn == "count":
        return exp.Count(this=col if col is not None else exp.Star())
    if fn == "count_distinct":
        return exp.Count(this=exp.Distinct(expressions=[col]))
    cls = {"sum": exp.Sum, "avg": exp.Avg, "min": exp.Min, "max": exp.Max, "median": exp.Median}[fn]
    return cls(this=col)


def build_table_sql(
    q: TableQuery,
    columns: list[str],
    *,
    group_override: list[GroupBy] | None = None,
    include_limit: bool = True,
) -> tuple[str, list[str], list[str]]:
    """Return (sql, dimension output columns, measure output columns)."""
    cols = set(columns)
    calc_names = {c.name for c in q.calculated}
    clash = calc_names & cols
    if clash:
        raise Unprocessable(f"Calculated field names clash with columns: {sorted(clash)}", code="name_clash")
    known = cols | calc_names
    base_select: list[exp.Expr] = [exp.Star()]
    for c in q.calculated:
        base_select.append(exp.alias_(_calc_expr(c, cols), c.name, quoted=True))
    base = exp.select(*base_select).from_(exp.Table(this=_q(q.table)))

    def col(name: str) -> exp.Expr:
        if name not in known:
            raise Unprocessable(f"Unknown column {name!r}", code="unknown_column")
        return exp.Column(this=_q(name))

    groups = (
        group_override
        if group_override is not None
        else [g if isinstance(g, GroupBy) else GroupBy(column=g) for g in q.group_by]
    )
    select: list[exp.Expr] = []
    dims: list[str] = []
    for g in groups:
        gcol = col(g.column)
        alias = g.column if g.grain is None else f"{g.column}__{g.grain}"
        expr = (
            gcol
            if g.grain is None
            else exp.Cast(this=exp.func("date_trunc", _lit(g.grain), gcol), to=exp.DataType.build("DATE"))
        )
        select.append(exp.alias_(expr, alias, quoted=True))
        dims.append(alias)
    measures: list[str] = []
    for a in q.aggregates:
        target = None if a.column == "*" else col(a.column)
        if a.column == "*" and a.fn != "count":
            raise Unprocessable("'*' is only valid with count", code="invalid_aggregate")
        alias = a.alias or (f"{a.fn}_{a.column}" if a.column != "*" else "row_count")
        select.append(exp.alias_(_agg(a.fn, target), alias, quoted=True))
        measures.append(alias)
    if not select:
        shown = q.columns or [*columns, *calc_names]
        select = [col(c) for c in shown]
        dims = list(shown)
    stmt = exp.select(*select).from_(exp.Subquery(this=base, alias=exp.TableAlias(this=_q("base"))))
    for f in q.filters:
        stmt = stmt.where(_predicate(col(f.column), f))
    if q.aggregates and dims:
        stmt = stmt.group_by(*[exp.Literal.number(i + 1) for i in range(len(dims))])
    outputs = set(dims) | set(measures)
    orders = []
    for o in q.order_by:
        if o.field not in outputs and o.field not in known:
            raise Unprocessable(f"Cannot sort by {o.field!r}", code="invalid_order")
        orders.append(exp.Ordered(this=exp.Column(this=_q(o.field)), desc=o.desc, nulls_first=False))
    if orders:
        stmt = stmt.order_by(*orders)
    elif dims and q.aggregates:
        stmt = stmt.order_by(*[exp.Literal.number(i + 1) for i in range(len(dims))])
    if include_limit:
        stmt = stmt.limit(q.limit)
    return stmt.sql(dialect="duckdb", pretty=True), dims, measures


def table_filter_context(q: TableQuery) -> list[dict[str, Any]]:
    out = [
        {
            "label": "Table",
            "value": q.table,
            "kind": "filter",
            "dimension": None,
            "op": "from",
            "values": [q.table],
        }
    ]
    for f in q.filters:
        out.append(
            {
                "label": f.column,
                "value": f"{f.op} {', '.join(map(str, f.values))}".strip(),
                "kind": "filter",
                "dimension": f.column,
                "op": f.op,
                "values": f.values,
            }
        )
    return out


# ----------------------------------------------------------------------------------- pivot


class PivotValue(BaseModel):
    field: str = Field(description="Metric id (semantic source) or column/calculated field (table source)")
    agg: AggFn = "sum"
    label: str | None = None


class PivotRequest(BaseModel):
    metric_query: dict[str, Any] | None = Field(
        default=None, description="Semantic source: MetricQuery without dimensions (filters/time only)"
    )
    table_query: TableQuery | None = Field(
        default=None, description="Table source (group_by/aggregates ignored)"
    )
    rows: list[str] = Field(
        default_factory=list, description="Row dimensions (e.g. 'region', 'order_date__month')"
    )
    columns: list[str] = Field(default_factory=list)
    values: list[PivotValue] = Field(min_length=1)
    subtotals: bool = True
    grand_totals: bool = True
    sort: Literal["label", "value_desc", "value_asc"] = "label"
    max_columns: int = Field(default=100, ge=1, le=500)


class PivotRow(BaseModel):
    keys: list[Any]
    cells: list[Any] = Field(description="Values per (column key, value field), then row totals if enabled")
    level: int = Field(description="Number of row keys set (subtotal rows have fewer)")
    is_subtotal: bool = False
    is_total: bool = False


class PivotOut(BaseModel):
    row_fields: list[str]
    column_fields: list[str]
    value_fields: list[str]
    column_keys: list[list[Any]]
    header: list[str]
    rows: list[PivotRow]
    formats: dict[str, str | None] = Field(default_factory=dict)
    sql: list[str]
    filter_context: list[dict[str, Any]]
    truncated_columns: bool = False


def _key(v: Any) -> Any:
    return "" if v is None else v


def assemble_pivot(req: PivotRequest, fetch: Any, formats: dict[str, str | None]) -> PivotOut:
    """``fetch(dims) -> (rows as dicts, sql)`` runs one grouped query at the given grain."""
    value_names = [
        v.field if req.metric_query is not None else (v.label or f"{v.agg}_{v.field}") for v in req.values
    ]
    R, C = list(req.rows), list(req.columns)
    sqls: list[str] = []

    def run(dims: list[str]) -> dict[tuple[Any, ...], dict[str, Any]]:
        recs, sql = fetch(dims)
        sqls.append(sql)
        return {tuple(_key(r.get(d)) for d in dims): r for r in recs}

    detail = run(R + C)
    col_keys = sorted({k[len(R) :] for k in detail}, key=lambda t: tuple(str(x) for x in t)) if C else [()]
    truncated = len(col_keys) > req.max_columns
    col_keys = col_keys[: req.max_columns]
    row_totals = run(R) if C and req.grand_totals else {}

    def cells_for(
        prefix: tuple[Any, ...],
        src: dict[tuple[Any, ...], dict[str, Any]],
        totals: dict[tuple[Any, ...], dict[str, Any]] | None,
    ) -> list[Any]:
        out: list[Any] = []
        for ck in col_keys:
            rec = src.get(prefix + ck)
            out.extend(rec.get(v) if rec else None for v in value_names)
        if C and req.grand_totals and totals is not None:
            rec = totals.get(prefix)
            out.extend(rec.get(v) if rec else None for v in value_names)
        return out

    row_keys = sorted({k[: len(R)] for k in detail}, key=lambda t: tuple(str(x) for x in t))
    if req.sort != "label" and R:
        first = value_names[0]
        total_src = row_totals if row_totals else detail
        reverse = req.sort == "value_desc"
        row_keys.sort(
            key=lambda rk: (
                total_src.get(rk, {}).get(first) is None,
                (total_src.get(rk, {}).get(first) or 0) * (-1 if reverse else 1),
            )
        )
    subtotal_levels: dict[
        int, tuple[dict[tuple[Any, ...], dict[str, Any]], dict[tuple[Any, ...], dict[str, Any]]]
    ] = {}
    if req.subtotals and len(R) > 1:
        for level in range(1, len(R)):
            subtotal_levels[level] = (run(R[:level] + C), run(R[:level]) if C and req.grand_totals else {})
    rows: list[PivotRow] = []
    prev: tuple[Any, ...] | None = None
    for rk in row_keys:
        if prev is not None and subtotal_levels:
            for level in range(len(R) - 1, 0, -1):
                if prev[:level] != rk[:level]:
                    src, tot = subtotal_levels[level]
                    rows.append(
                        PivotRow(
                            keys=list(prev[:level]) + [None] * (len(R) - level),
                            cells=cells_for(prev[:level], src, tot),
                            level=level,
                            is_subtotal=True,
                        )
                    )
        rows.append(PivotRow(keys=list(rk), cells=cells_for(rk, detail, row_totals), level=len(R)))
        prev = rk
    if prev is not None and subtotal_levels:
        for level in range(len(R) - 1, 0, -1):
            src, tot = subtotal_levels[level]
            rows.append(
                PivotRow(
                    keys=list(prev[:level]) + [None] * (len(R) - level),
                    cells=cells_for(prev[:level], src, tot),
                    level=level,
                    is_subtotal=True,
                )
            )
    if req.grand_totals and R:
        col_totals = run(C) if C else {}
        grand = run([])
        cells: list[Any] = []
        for ck in col_keys:
            rec = col_totals.get(ck) if C else grand.get(())
            cells.extend(rec.get(v) if rec else None for v in value_names)
        if C:
            rec = grand.get(())
            cells.extend(rec.get(v) if rec else None for v in value_names)
        rows.append(PivotRow(keys=[None] * len(R), cells=cells, level=0, is_total=True))
    header = [f"{' / '.join(map(str, ck))} · {v}" if C else v for ck in col_keys for v in value_names]
    if C and req.grand_totals:
        header += [f"Total · {v}" for v in value_names]
    return PivotOut(
        row_fields=R,
        column_fields=C,
        value_fields=value_names,
        column_keys=[list(c) for c in col_keys],
        header=header,
        rows=rows,
        formats={v: formats.get(v) for v in value_names},
        sql=sqls,
        filter_context=[],
        truncated_columns=truncated,
    )
