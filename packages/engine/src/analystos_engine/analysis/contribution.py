"""Contribution analysis: which segments of a dimension explain a change.

Methods
-------
* ``additive`` (sum / count metrics and linear combinations of them): each segment's
  contribution is its own change; contributions sum exactly to the total change.
* ``volume_mix_rate`` (an additive metric with a ``volume_metric``, e.g. revenue with
  units): per segment, change = volume effect + mix effect + rate effect where
  ``volume = (V1 - V0) * w0 * p0``, ``mix = V1 * (w1 - w0) * p0`` and the rate effect is
  the remainder (``= V1 * w1 * (p1 - p0)`` whenever volumes are positive). Exact.
* ``ratio_mix_rate`` (ratio metrics such as AOV or margin %): with weights
  ``w = denominator share`` and segment ratios ``r``, ``mix = (w1 - w0) * (r0 - R0)`` and
  ``rate = w1 * (r1 - r0)``; mix + rate over segments equals ``R1 - R0`` exactly.
* ``non_additive`` (averages, distinct counts whose segments overlap, non-linear derived
  metrics): changes are reported per segment but **not** as shares of the total.

``additive_valid`` is only true when segment contributions provably sum to the total
(checked numerically against an independent total query). Contributions from
*different* dimensions overlap and are never additive across dimensions.
"""

from __future__ import annotations

import math
from typing import Any, Literal

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from ..semantic.compiler import MetricQuery, run_metric_query
from ..semantic.models import Filter, SemanticModel
from ..store import WorkspaceStore
from ..types import TimeWindow

__all__ = [
    "ContributionRow",
    "ContributionResult",
    "DimensionRanking",
    "MultiDimensionContribution",
    "contribution_by_dimension",
    "contribution_across_dimensions",
    "metric_additivity",
]

_TOL = 1e-6


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContributionRow(_Model):
    segment: Any
    current: float | None
    baseline: float | None
    change: float | None
    pct_change: float | None = None
    share_of_change: float | None = None
    contribution: float | None = None
    mix_effect: float | None = None
    rate_effect: float | None = None
    volume_effect: float | None = None
    current_weight: float | None = None
    baseline_weight: float | None = None
    is_other: bool = False
    segment_count: int = 1


class ContributionResult(_Model):
    metric_id: str
    dimension: str
    method: Literal["additive", "volume_mix_rate", "ratio_mix_rate", "non_additive"]
    rows: list[ContributionRow] = Field(default_factory=list)
    total_current: float | None = None
    total_baseline: float | None = None
    total_change: float | None = None
    additive_valid: bool = False
    notes: list[str] = Field(default_factory=list)
    sql: str = ""
    queries: list[str] = Field(default_factory=list)
    metric_versions: dict[str, str] = Field(default_factory=dict)
    current_window: TimeWindow | None = None
    baseline_window: TimeWindow | None = None
    filters: list[Filter] = Field(default_factory=list)
    volume_metric: str | None = None

    def top(self, n: int = 5) -> list[ContributionRow]:
        key = (
            (lambda r: abs(r.contribution or 0.0))
            if self.method != "non_additive"
            else (lambda r: abs(r.change or 0.0))
        )
        return sorted([r for r in self.rows if not r.is_other], key=key, reverse=True)[:n]


class DimensionRanking(_Model):
    dimension: str
    top_segment: Any = None
    top_contribution: float | None = None
    top_share: float | None = None
    segments: int = 0
    additive_valid: bool = False


class MultiDimensionContribution(_Model):
    metric_id: str
    results: list[ContributionResult] = Field(default_factory=list)
    ranking: list[DimensionRanking] = Field(default_factory=list)
    additive_valid: bool = False
    notes: list[str] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


def _f(v: Any) -> float | None:
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) else f


def metric_additivity(model: SemanticModel, metric_id: str) -> Literal["additive", "ratio", "non_additive"]:
    """Whether segment values of ``metric_id`` add up to its total."""
    m = model.get_metric(metric_id)
    if m.kind == "simple":
        if m.agg in ("sum", "count"):
            return "additive"
        if m.agg == "count_distinct" and m.entity:
            # distinct count of the entity's own key: every row sits in exactly one segment
            # (the compiler only allows dimensions with one value per entity row)
            key = model.get_entity(m.entity).key_columns
            if len(key) == 1 and (m.expr or "").strip().strip('"') == key[0]:
                return "additive"
        return "non_additive"
    if m.kind == "ratio":
        num = metric_additivity(model, m.numerator or "")
        den = metric_additivity(model, m.denominator or "")
        return "ratio" if num == den == "additive" else "non_additive"
    tree = sqlglot.parse_one(m.formula or "", read="duckdb")
    for node in tree.walk():
        if isinstance(node, exp.Column):
            if metric_additivity(model, node.name) != "additive":
                return "non_additive"
        elif isinstance(node, exp.Add | exp.Sub | exp.Neg | exp.Paren | exp.Literal):
            continue
        elif isinstance(node, exp.Mul):
            # scaling by a constant keeps additivity
            if not (isinstance(node.this, exp.Literal) or isinstance(node.expression, exp.Literal)):
                return "non_additive"
        elif isinstance(node, exp.Identifier):
            continue
        else:
            return "non_additive"
    return "additive"


def _segment_values(
    store: WorkspaceStore,
    model: SemanticModel,
    metrics: list[str],
    dimension: str,
    window: TimeWindow,
    filters: list[Filter],
) -> tuple[dict[Any, dict[str, float | None]], dict[str, float | None], list[str], dict[str, str], list[str]]:
    compiled, res = run_metric_query(
        store,
        model,
        MetricQuery(metrics=metrics, dimensions=[dimension], filters=filters, time=window),
        limit=None,
    )
    seg: dict[Any, dict[str, float | None]] = {}
    for rec in res.to_records():
        seg[rec[dimension]] = {m: _f(rec[m]) for m in metrics}
    tot_compiled, tot = run_metric_query(
        store, model, MetricQuery(metrics=metrics, filters=filters, time=window), limit=None
    )
    totals = {m: _f(tot.scalar(m)) for m in metrics} if tot.rows else dict.fromkeys(metrics)
    return seg, totals, [compiled.sql, tot_compiled.sql], compiled.metric_versions, compiled.warnings


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def contribution_by_dimension(
    store: WorkspaceStore,
    model: SemanticModel,
    metric: str,
    dimension: str,
    current: TimeWindow,
    baseline: TimeWindow,
    filters: list[Filter] | None = None,
    *,
    volume_metric: str | None = None,
    top_n: int | None = None,
) -> ContributionResult:
    """Explain the change of ``metric`` between ``baseline`` and ``current`` by segments of ``dimension``."""
    filters = list(filters or [])
    kind = metric_additivity(model, metric)
    m = model.get_metric(metric)
    notes: list[str] = []
    if kind == "ratio":
        num, den = m.numerator or "", m.denominator or ""
        metrics = [metric, num, den]
    elif volume_metric and kind == "additive":
        if metric_additivity(model, volume_metric) != "additive":
            raise ValueError(f"volume metric {volume_metric!r} must be additive (a sum or count)")
        metrics = [metric, volume_metric]
    else:
        metrics = [metric]
        if volume_metric:
            notes.append(f"volume/mix/rate split skipped: {metric} is not additive")
            volume_metric = None
    cur_seg, cur_tot, q1, versions, warn1 = _segment_values(
        store, model, metrics, dimension, current, filters
    )
    base_seg, base_tot, q2, _, warn2 = _segment_values(store, model, metrics, dimension, baseline, filters)
    for w in warn1 + warn2:
        if w not in notes:
            notes.append(w)
    segments = sorted(set(cur_seg) | set(base_seg), key=lambda s: (s is None, str(s)))
    total_cur, total_base = cur_tot.get(metric), base_tot.get(metric)
    total_change = (
        None if total_cur is None and total_base is None else (total_cur or 0.0) - (total_base or 0.0)
    )
    rows: list[ContributionRow] = []
    method: Literal["additive", "volume_mix_rate", "ratio_mix_rate", "non_additive"]

    if kind == "ratio":
        method = "ratio_mix_rate"
        num, den = m.numerator or "", m.denominator or ""
        n0, d0 = base_tot.get(num) or 0.0, base_tot.get(den) or 0.0
        n1, d1 = cur_tot.get(num) or 0.0, cur_tot.get(den) or 0.0
        r_all0, r_all1 = _safe_div(n0, d0), _safe_div(n1, d1)
        if r_all0 is None or r_all1 is None:
            notes.append("a period has a zero denominator; the ratio change cannot be decomposed")
            method = "non_additive"
        for s in segments:
            c = cur_seg.get(s, {})
            b = base_seg.get(s, {})
            sn1, sd1 = c.get(num) or 0.0, c.get(den) or 0.0
            sn0, sd0 = b.get(num) or 0.0, b.get(den) or 0.0
            r1, r0 = _safe_div(sn1, sd1), _safe_div(sn0, sd0)
            row = ContributionRow(
                segment=s,
                current=r1,
                baseline=r0,
                change=None if r1 is None or r0 is None else r1 - r0,
                pct_change=None if r1 is None or r0 in (None, 0) else (r1 - r0) / abs(r0),  # type: ignore[operator]
            )
            if method == "ratio_mix_rate" and r_all0 is not None:
                w1 = sd1 / d1 if d1 else 0.0
                w0 = sd0 / d0 if d0 else 0.0
                rr0 = r0 if r0 is not None else r_all0
                rr1 = r1 if r1 is not None else rr0
                if sd0 == 0:  # new segment: its whole effect is a mix effect
                    mix, rate = w1 * (rr1 - r_all0), 0.0
                else:
                    mix, rate = (w1 - w0) * (rr0 - r_all0), w1 * (rr1 - rr0)
                row.mix_effect, row.rate_effect = mix, rate
                row.contribution = mix + rate
                row.current_weight, row.baseline_weight = w1, w0
            rows.append(row)
        if method == "ratio_mix_rate":
            total_change = (r_all1 or 0.0) - (r_all0 or 0.0)
            total_cur, total_base = r_all1, r_all0
            s_sum = sum(r.contribution or 0.0 for r in rows)
            additive_valid = abs(s_sum - total_change) <= _TOL * max(1.0, abs(total_change))
            notes.append(
                "ratio change split into mix (shift of weight between segments) and rate (change within segments); "
                "weights are each segment's share of the denominator"
            )
        else:
            additive_valid = False
    elif kind == "additive":
        method = "volume_mix_rate" if volume_metric else "additive"
        seg_sum_cur = sum((cur_seg.get(s, {}).get(metric) or 0.0) for s in segments)
        seg_sum_base = sum((base_seg.get(s, {}).get(metric) or 0.0) for s in segments)
        additive_valid = abs(seg_sum_cur - (total_cur or 0.0)) <= _TOL * max(
            1.0, abs(total_cur or 0.0)
        ) and abs(seg_sum_base - (total_base or 0.0)) <= _TOL * max(1.0, abs(total_base or 0.0))
        if not additive_valid:
            notes.append("segment values do not add up to the total; shares of change are not reported")
        v1_all = cur_tot.get(volume_metric) or 0.0 if volume_metric else 0.0
        v0_all = base_tot.get(volume_metric) or 0.0 if volume_metric else 0.0
        for s in segments:
            c1 = cur_seg.get(s, {}).get(metric) or 0.0
            c0 = base_seg.get(s, {}).get(metric) or 0.0
            row = ContributionRow(
                segment=s,
                current=c1,
                baseline=c0,
                change=c1 - c0,
                pct_change=(c1 - c0) / abs(c0) if c0 else None,
                contribution=c1 - c0,
            )
            if volume_metric:
                u1 = cur_seg.get(s, {}).get(volume_metric) or 0.0
                u0 = base_seg.get(s, {}).get(volume_metric) or 0.0
                w1 = u1 / v1_all if v1_all else 0.0
                w0 = u0 / v0_all if v0_all else 0.0
                p0 = c0 / u0 if u0 else (c1 / u1 if u1 else 0.0)
                vol = (v1_all - v0_all) * w0 * p0
                mix = v1_all * (w1 - w0) * p0
                row.volume_effect, row.mix_effect = vol, mix
                row.rate_effect = (c1 - c0) - vol - mix
                row.current_weight, row.baseline_weight = w1, w0
            rows.append(row)
        if volume_metric:
            notes.append(
                f"volume effect: change in total {volume_metric} at baseline mix and rate; mix effect: shift of "
                f"{volume_metric} between segments at baseline rate; rate effect: change of {metric} per {volume_metric} "
                "within segments (computed as the remainder, so effects sum exactly)"
            )
    else:
        method = "non_additive"
        additive_valid = False
        for s in segments:
            m1 = cur_seg.get(s, {}).get(metric)
            m0 = base_seg.get(s, {}).get(metric)
            rows.append(
                ContributionRow(
                    segment=s,
                    current=m1,
                    baseline=m0,
                    change=None if m1 is None or m0 is None else m1 - m0,
                    pct_change=None if m1 is None or not m0 else (m1 - m0) / abs(m0),
                )
            )
        notes.append(
            f"{metric} is not additive across {dimension} segments (an average, a distinct count or a non-linear "
            "formula): segment changes are shown but are not shares of the total change"
        )
        if m.kind == "simple" and m.agg == "count_distinct":
            seg_sum = sum((cur_seg.get(s, {}).get(metric) or 0.0) for s in segments)
            if total_cur is not None and seg_sum > total_cur + _TOL:
                notes.append(
                    f"segments overlap: they sum to {seg_sum:g} against a total of {total_cur:g} "
                    f"(the same {m.expr} appears in several segments)"
                )

    if additive_valid and total_change:
        for r in rows:
            if r.contribution is not None:
                r.share_of_change = r.contribution / total_change
    elif additive_valid and total_change == 0:
        notes.append("total change is zero; shares of change are undefined")
    if method != "non_additive" and not additive_valid:
        for r in rows:
            r.share_of_change = None

    key = (
        (lambda r: abs(r.contribution or 0.0))
        if method != "non_additive"
        else (lambda r: abs(r.change or 0.0))
    )
    rows.sort(key=lambda r: (-key(r), str(r.segment)))
    if top_n is not None and len(rows) > top_n:
        head, tail = rows[:top_n], rows[top_n:]
        if method in ("additive", "volume_mix_rate", "ratio_mix_rate"):
            other = ContributionRow(
                segment=f"(other {len(tail)} segments)",
                current=sum(r.current or 0.0 for r in tail) if method != "ratio_mix_rate" else None,
                baseline=sum(r.baseline or 0.0 for r in tail) if method != "ratio_mix_rate" else None,
                change=sum(r.change or 0.0 for r in tail) if method != "ratio_mix_rate" else None,
                contribution=sum(r.contribution or 0.0 for r in tail),
                mix_effect=_sum_opt([r.mix_effect for r in tail]),
                rate_effect=_sum_opt([r.rate_effect for r in tail]),
                volume_effect=_sum_opt([r.volume_effect for r in tail]),
                share_of_change=_sum_opt([r.share_of_change for r in tail]),
                is_other=True,
                segment_count=len(tail),
            )
            rows = [*head, other]
        else:
            rows = head
            notes.append(f"showing the {top_n} largest changes of {len(head) + len(tail)} segments")
    return ContributionResult(
        metric_id=metric,
        dimension=dimension,
        method=method,
        rows=rows,
        total_current=total_cur,
        total_baseline=total_base,
        total_change=total_change,
        additive_valid=additive_valid,
        notes=notes,
        sql=q1[0],
        queries=q1 + q2,
        metric_versions=versions,
        current_window=current,
        baseline_window=baseline,
        filters=filters,
        volume_metric=volume_metric,
    )


def _sum_opt(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def contribution_across_dimensions(
    store: WorkspaceStore,
    model: SemanticModel,
    metric: str,
    dimensions: list[str],
    current: TimeWindow,
    baseline: TimeWindow,
    filters: list[Filter] | None = None,
    **kwargs: Any,
) -> MultiDimensionContribution:
    """Contribution for several dimensions, ranked by the explanatory power of their top segment.

    Each dimension partitions the change on its own; the results overlap (the same
    order is in one region *and* one category), so they must never be added together.
    """
    from ..semantic.compiler import CompileError

    out = MultiDimensionContribution(
        metric_id=metric,
        additive_valid=False,
        notes=[
            "each dimension explains the same total change separately; contributions from different dimensions "
            "overlap and must not be added together"
        ],
    )
    for dim in dimensions:
        try:
            res = contribution_by_dimension(store, model, metric, dim, current, baseline, filters, **kwargs)
        except CompileError as exc:
            out.errors[dim] = str(exc)
            continue
        out.results.append(res)
        top = res.top(1)
        t = top[0] if top else None
        out.ranking.append(
            DimensionRanking(
                dimension=dim,
                top_segment=t.segment if t else None,
                top_contribution=(t.contribution if res.method != "non_additive" else t.change)
                if t
                else None,
                top_share=t.share_of_change if t else None,
                segments=len([r for r in res.rows if not r.is_other]),
                additive_valid=res.additive_valid,
            )
        )
    out.ranking.sort(key=lambda r: (-(abs(r.top_share) if r.top_share is not None else -1.0), r.dimension))
    return out
