"""Reusable query-result validation (spec section 50).

Every analytical test is checked for: successful execution, unexpected emptiness,
presence of the metric columns, a plausible row count, the expected aggregation
grain (unique dimension combinations), applied filters and non-zero denominators.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .semantic.models import Filter
from .types import QueryResult

__all__ = ["QueryExpectations", "ValidationCheck", "ValidationReport", "validate_result", "expectations_for"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationCheck(_Model):
    name: str
    passed: bool
    detail: str = ""
    severity: Literal["error", "warning", "info"] = "error"


class ValidationReport(_Model):
    checks: list[ValidationCheck] = Field(default_factory=list)
    ok: bool = True

    @property
    def failures(self) -> list[ValidationCheck]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        bad = self.failures
        if not bad:
            return f"all {len(self.checks)} checks passed"
        return "; ".join(f"{c.name}: {c.detail}" for c in bad)


class QueryExpectations(_Model):
    """What a result should look like. Every field is optional."""

    allow_empty: bool = False
    min_rows: int | None = None
    max_rows: int | None = None
    required_columns: list[str] = Field(default_factory=list, description="e.g. the metric ids")
    non_null_columns: list[str] = Field(default_factory=list)
    grain: list[str] | None = Field(default=None, description="columns whose combination must be unique")
    filters: list[Filter] = Field(
        default_factory=list, description="filters whose columns appear in the result"
    )
    required_filters: list[str] = Field(
        default_factory=list, description="filter labels that must have been applied"
    )
    applied_filters: list[str] = Field(
        default_factory=list, description="filter labels the query reports as applied"
    )
    nonzero_columns: list[str] = Field(
        default_factory=list, description="denominators that must not be all zero/null"
    )
    numeric_columns: list[str] = Field(default_factory=list)
    allow_truncated: bool = False


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _filter_ok(value: Any, f: Filter) -> bool:
    vals = f.values
    if f.op == "eq":
        return value == vals[0]
    if f.op == "neq":
        return value != vals[0]
    if f.op == "in":
        return value in vals
    if f.op == "not_in":
        return value not in vals
    if f.op == "is_null":
        return value is None
    if f.op == "not_null":
        return value is not None
    if f.op == "contains":
        return value is not None and str(vals[0]).lower() in str(value).lower()
    if f.op == "starts_with":
        return value is not None and str(value).lower().startswith(str(vals[0]).lower())
    if value is None:
        return False
    try:
        if f.op == "gt":
            return value > vals[0]
        if f.op == "gte":
            return value >= vals[0]
        if f.op == "lt":
            return value < vals[0]
        if f.op == "lte":
            return value <= vals[0]
        if f.op == "between":
            return vals[0] <= value <= vals[1]
    except TypeError:
        return False
    return True


def validate_result(
    result: QueryResult | None,
    expectations: QueryExpectations | None = None,
    *,
    error: str | None = None,
) -> ValidationReport:
    """Validate a query result against ``expectations``.

    Pass ``result=None`` and ``error`` when execution failed: the report then contains
    a failed ``executed`` check and nothing else.
    """
    exp_ = expectations or QueryExpectations()
    checks: list[ValidationCheck] = []
    if result is None or error:
        checks.append(ValidationCheck(name="executed", passed=False, detail=error or "no result"))
        return ValidationReport(checks=checks, ok=False)
    checks.append(
        ValidationCheck(
            name="executed", passed=True, detail=f"{result.row_count} rows in {result.elapsed_ms:.1f} ms"
        )
    )

    n = result.row_count
    if n == 0:
        checks.append(
            ValidationCheck(
                name="not_empty",
                passed=exp_.allow_empty,
                detail="result is empty"
                + (" (allowed)" if exp_.allow_empty else "; check filters and time window"),
                severity="error" if not exp_.allow_empty else "info",
            )
        )
    else:
        checks.append(ValidationCheck(name="not_empty", passed=True, detail=f"{n} rows"))

    cols = set(result.column_names)
    missing = [c for c in exp_.required_columns if c not in cols]
    if exp_.required_columns:
        checks.append(
            ValidationCheck(
                name="metric_present",
                passed=not missing,
                detail="missing columns: " + ", ".join(missing) if missing else "all metric columns present",
            )
        )

    if exp_.min_rows is not None or exp_.max_rows is not None:
        ok = (exp_.min_rows is None or n >= exp_.min_rows) and (exp_.max_rows is None or n <= exp_.max_rows)
        checks.append(
            ValidationCheck(
                name="row_count_plausible",
                passed=ok,
                detail=f"{n} rows (expected {exp_.min_rows if exp_.min_rows is not None else 0}.."
                f"{exp_.max_rows if exp_.max_rows is not None else 'any'})",
            )
        )
    if result.truncated:
        checks.append(
            ValidationCheck(
                name="complete",
                passed=exp_.allow_truncated,
                detail="result was truncated by the row limit; totals computed from it would be wrong",
                severity="error" if not exp_.allow_truncated else "warning",
            )
        )

    if exp_.grain is not None:
        g_missing = [c for c in exp_.grain if c not in cols]
        if g_missing:
            checks.append(
                ValidationCheck(
                    name="grain", passed=False, detail="grain columns missing: " + ", ".join(g_missing)
                )
            )
        else:
            idx = [result.column_index(c) for c in exp_.grain]
            keys = [tuple(row[i] for i in idx) for row in result.rows]
            dupes = len(keys) - len(set(keys))
            checks.append(
                ValidationCheck(
                    name="grain",
                    passed=dupes == 0,
                    detail=(
                        f"one row per ({', '.join(exp_.grain) or 'total'})"
                        if dupes == 0
                        else f"{dupes} duplicate ({', '.join(exp_.grain)}) combinations: rows are not at the expected grain"
                    ),
                )
            )
            if not exp_.grain and n > 1:
                checks[-1] = ValidationCheck(
                    name="grain", passed=False, detail=f"expected a single total row, got {n}"
                )

    for c in exp_.non_null_columns:
        if c in cols:
            nulls = sum(1 for v in result.column_values(c) if v is None)
            checks.append(
                ValidationCheck(
                    name=f"non_null:{c}",
                    passed=nulls == 0,
                    detail=f"{nulls} null values in {c}" if nulls else f"{c} has no nulls",
                    severity="warning",
                )
            )

    for c in exp_.numeric_columns:
        if c in cols:
            bad = [v for v in result.column_values(c) if v is not None and _num(v) is None]
            checks.append(
                ValidationCheck(
                    name=f"numeric:{c}",
                    passed=not bad,
                    detail=f"{len(bad)} non-numeric values" if bad else "numeric",
                )
            )

    for f in exp_.filters:
        if f.dimension not in cols:
            continue
        values = result.column_values(f.dimension)
        violations = [v for v in values if not _filter_ok(v, f)]
        checks.append(
            ValidationCheck(
                name=f"filter:{f.dimension}",
                passed=not violations,
                detail=(
                    f"{len(violations)} rows violate {f.describe()}"
                    if violations
                    else f"{f.describe()} holds for all rows"
                ),
            )
        )
    if exp_.required_filters:
        missing_f = [f for f in exp_.required_filters if f not in exp_.applied_filters]
        checks.append(
            ValidationCheck(
                name="filters_applied",
                passed=not missing_f,
                detail="not applied: " + ", ".join(missing_f)
                if missing_f
                else "all requested filters applied",
            )
        )

    for c in exp_.nonzero_columns:
        if c not in cols:
            checks.append(
                ValidationCheck(
                    name=f"denominator:{c}", passed=False, detail=f"denominator column {c} missing"
                )
            )
            continue
        values = [_num(v) for v in result.column_values(c)]
        zero = sum(1 for v in values if v is None or v == 0)
        checks.append(
            ValidationCheck(
                name=f"denominator:{c}",
                passed=zero == 0,
                detail=f"{zero} of {len(values)} rows have a zero or missing {c}; ratios there are undefined"
                if zero
                else f"{c} is non-zero",
                severity="error" if values and zero == len(values) else "warning",
            )
        )
    ok = all(c.passed or c.severity != "error" for c in checks)
    return ValidationReport(checks=checks, ok=ok)


def expectations_for(
    compiled: Any, *, allow_empty: bool = False, denominators: list[str] | None = None
) -> QueryExpectations:
    """Expectations derived from a :class:`~analystos_engine.semantic.compiler.CompiledQuery`."""
    q = getattr(compiled, "query", None)
    filters = list(q.filters) if q is not None else []
    return QueryExpectations(
        allow_empty=allow_empty,
        required_columns=list(compiled.metric_columns),
        grain=list(compiled.grain),
        filters=filters,
        required_filters=[f.describe() for f in filters],
        applied_filters=list(compiled.filters_applied),
        nonzero_columns=denominators or [],
        numeric_columns=list(compiled.metric_columns),
    )
