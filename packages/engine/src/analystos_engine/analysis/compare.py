"""Comparison engine: period over period, year over year, actual vs target, segment vs segment.

Every comparison executes compiled semantic SQL for each side and returns the SQL, so
each number is traceable to an executed query.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..calendar import previous_period
from ..semantic.compiler import CompiledQuery, MetricQuery, run_metric_query
from ..semantic.models import Filter, SemanticModel
from ..store import WorkspaceStore
from ..types import TimeWindow

__all__ = [
    "Comparison",
    "MetricValue",
    "metric_value",
    "compare_periods",
    "compare_to_previous",
    "compare_metrics",
    "compare_segments",
    "pct_change",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricValue(_Model):
    metric_id: str
    value: float | None
    window: TimeWindow | None = None
    filters: list[Filter] = Field(default_factory=list)
    sql: str
    metric_versions: dict[str, str] = Field(default_factory=dict)
    filters_applied: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Comparison(_Model):
    metric_id: str
    kind: Literal["pop", "yoy", "custom", "target", "segment"] = "custom"
    current: float | None
    baseline: float | None
    abs_change: float | None
    pct_change: float | None
    current_label: str = ""
    baseline_label: str = ""
    current_window: TimeWindow | None = None
    baseline_window: TimeWindow | None = None
    filters: list[Filter] = Field(default_factory=list)
    sql_current: str
    sql_baseline: str
    metric_versions: dict[str, str] = Field(default_factory=dict)
    baseline_metric_id: str | None = None
    notes: list[str] = Field(default_factory=list)


def pct_change(current: float | None, baseline: float | None) -> float | None:
    """Relative change; ``None`` when the baseline is zero or missing (never infinity)."""
    if current is None or baseline is None or baseline == 0:
        return None
    return (current - baseline) / abs(baseline)


def _num(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)


def metric_value(
    store: WorkspaceStore,
    model: SemanticModel,
    metric: str,
    window: TimeWindow | None,
    filters: list[Filter] | None = None,
) -> tuple[MetricValue, CompiledQuery]:
    compiled, res = run_metric_query(
        store, model, MetricQuery(metrics=[metric], filters=filters or [], time=window)
    )
    value = _num(res.scalar(metric)) if res.rows else None
    return (
        MetricValue(
            metric_id=metric,
            value=value,
            window=window,
            filters=filters or [],
            sql=compiled.sql,
            metric_versions=compiled.metric_versions,
            filters_applied=compiled.filters_applied,
            warnings=compiled.warnings,
        ),
        compiled,
    )


def _compare(
    cur: MetricValue,
    base: MetricValue,
    kind: str,
    *,
    current_label: str,
    baseline_label: str,
    notes: list[str] | None = None,
) -> Comparison:
    abs_change = None if cur.value is None or base.value is None else cur.value - base.value
    notes = list(notes or [])
    if base.value == 0:
        notes.append("baseline is zero; percentage change is undefined")
    for w in cur.warnings + base.warnings:
        if w not in notes:
            notes.append(w)
    return Comparison(
        metric_id=cur.metric_id,
        kind=kind,  # type: ignore[arg-type]
        current=cur.value,
        baseline=base.value,
        abs_change=abs_change,
        pct_change=pct_change(cur.value, base.value),
        current_label=current_label,
        baseline_label=baseline_label,
        current_window=cur.window,
        baseline_window=base.window,
        filters=cur.filters,
        sql_current=cur.sql,
        sql_baseline=base.sql,
        metric_versions={**base.metric_versions, **cur.metric_versions},
        baseline_metric_id=base.metric_id if base.metric_id != cur.metric_id else None,
        notes=notes,
    )


def compare_periods(
    store: WorkspaceStore,
    model: SemanticModel,
    metric: str,
    current: TimeWindow,
    baseline: TimeWindow,
    filters: list[Filter] | None = None,
    *,
    kind: Literal["pop", "yoy", "custom"] = "custom",
) -> Comparison:
    """``metric`` in ``current`` vs ``baseline`` under the same filters."""
    cur, _ = metric_value(store, model, metric, current, filters)
    base, _ = metric_value(store, model, metric, baseline, filters)
    notes = []
    if current.days != baseline.days:
        notes.append(
            f"windows differ in length ({current.days} vs {baseline.days} days); totals are not per-day comparable"
        )
    return _compare(
        cur, base, kind, current_label=current.display(), baseline_label=baseline.display(), notes=notes
    )


def compare_to_previous(
    store: WorkspaceStore,
    model: SemanticModel,
    metric: str,
    window: TimeWindow,
    kind: Literal["pop", "yoy"] = "pop",
    filters: list[Filter] | None = None,
) -> Comparison:
    """Period over period or year over year using the business calendar."""
    return compare_periods(store, model, metric, window, previous_period(window, kind), filters, kind=kind)


def compare_metrics(
    store: WorkspaceStore,
    model: SemanticModel,
    actual_metric: str,
    target_metric: str,
    window: TimeWindow,
    filters: list[Filter] | None = None,
) -> Comparison:
    """Actual vs budget/target/forecast: two metrics over the same window and filters.

    ``abs_change`` is actual minus target; ``pct_change`` is relative to the target.
    """
    cur, _ = metric_value(store, model, actual_metric, window, filters)
    base, _ = metric_value(store, model, target_metric, window, filters)
    return _compare(
        cur,
        base,
        "target",
        current_label=model.get_metric(actual_metric).display_name,
        baseline_label=model.get_metric(target_metric).display_name,
    )


def compare_segments(
    store: WorkspaceStore,
    model: SemanticModel,
    metric: str,
    window: TimeWindow | None,
    dimension: str,
    segment_a: Any,
    segment_b: Any,
    filters: list[Filter] | None = None,
) -> Comparison:
    """``metric`` for ``dimension = segment_a`` vs ``dimension = segment_b``."""
    base_filters = list(filters or [])
    fa = [*base_filters, Filter(dimension=dimension, op="eq", values=[segment_a])]
    fb = [*base_filters, Filter(dimension=dimension, op="eq", values=[segment_b])]
    cur, _ = metric_value(store, model, metric, window, fa)
    base, _ = metric_value(store, model, metric, window, fb)
    res = _compare(
        cur,
        base,
        "segment",
        current_label=f"{dimension} = {segment_a}",
        baseline_label=f"{dimension} = {segment_b}",
    )
    return res.model_copy(update={"filters": base_filters})
