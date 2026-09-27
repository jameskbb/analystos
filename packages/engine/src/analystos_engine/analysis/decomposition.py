"""Change decomposition driven by metric trees.

For a parent metric ``P`` with driver children ``c_i`` (edges of a :class:`MetricTree`):

* **additive / subtractive** (``P = sum(+-c_i)``): the effect of ``c_i`` is ``+-(c_i1 - c_i0)``.
  Exact; any gap to ``P1 - P0`` (an incomplete tree) is reported as ``residual``.
* **multiplicative** (``P = prod(c_i)``): LMDI-I (logarithmic mean Divisia index):
  ``effect_i = L(Q1, Q0) * ln(c_i1 / c_i0)`` with ``Q = prod(c)`` and
  ``L(a, b) = (a - b) / (ln a - ln b)``. The effects sum *exactly* to ``Q1 - Q0``.
  When a value is zero or negative (logarithms undefined) the symmetric Shapley
  decomposition is used instead, which is also exact.
* **ratio** (``P = N / D``, one ``ratio_numerator`` and one ``ratio_denominator`` child):
  LMDI on ``N * D^-1``: ``effect_N = L(R1, R0) ln(N1/N0)``, ``effect_D = -L(R1, R0) ln(D1/D0)``;
  Shapley (average of the two orderings) when values are not positive.

Children that are themselves decomposed in the tree get a nested ``Decomposition``;
their effects are also expressed ``on the root`` by scaling with the child's share
of its parent effect (``effect_on_root``).
"""

from __future__ import annotations

import itertools
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..semantic.compiler import MetricQuery, run_metric_query
from ..semantic.models import Filter, MetricTree, SemanticModel
from ..store import WorkspaceStore
from ..types import TimeWindow

__all__ = [
    "DecompositionError",
    "DriverEffect",
    "Decomposition",
    "MetricDecomposition",
    "decompose",
    "decompose_metric",
    "logmean",
    "shapley_product",
]

_TOL = 1e-9


class DecompositionError(ValueError):
    """The tree or the values do not allow a decomposition."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DriverEffect(_Model):
    metric: str
    relation: Literal["additive", "multiplicative", "ratio_numerator", "ratio_denominator", "subtractive"]
    current: float
    baseline: float
    change: float
    pct_change: float | None
    effect: float
    share_of_change: float | None
    effect_on_root: float
    sub: Decomposition | None = None


class Decomposition(_Model):
    metric: str
    method: Literal["additive", "lmdi", "shapley", "ratio_lmdi", "ratio_shapley"]
    current: float
    baseline: float
    change: float
    pct_change: float | None
    effects: list[DriverEffect] = Field(default_factory=list)
    residual: float = 0.0
    exact: bool = True
    notes: list[str] = Field(default_factory=list)

    def flat_effects(self) -> list[DriverEffect]:
        """All effects in the tree, depth first."""
        out: list[DriverEffect] = []
        for e in self.effects:
            out.append(e)
            if e.sub is not None:
                out.extend(e.sub.flat_effects())
        return out


DriverEffect.model_rebuild()


class MetricDecomposition(_Model):
    decomposition: Decomposition
    values_current: dict[str, float | None]
    values_baseline: dict[str, float | None]
    sql_current: str
    sql_baseline: str
    metric_versions: dict[str, str] = Field(default_factory=dict)
    current_window: TimeWindow
    baseline_window: TimeWindow
    filters: list[Filter] = Field(default_factory=list)


def logmean(a: float, b: float) -> float:
    """Logarithmic mean L(a, b) for positive a, b (L(a, a) = a)."""
    if a <= 0 or b <= 0:
        raise DecompositionError("logarithmic mean needs positive values")
    if abs(a - b) <= 1e-12 * max(abs(a), abs(b)):
        return a
    return (a - b) / (math.log(a) - math.log(b))


def shapley_product(v0: list[float], v1: list[float]) -> list[float]:
    """Exact Shapley attribution of ``prod(v1) - prod(v0)`` to each factor."""
    n = len(v0)
    effects = [0.0] * n
    perms = list(itertools.permutations(range(n)))
    for perm in perms:
        state = list(v0)
        prev = math.prod(state)
        for i in perm:
            state[i] = v1[i]
            cur = math.prod(state)
            effects[i] += cur - prev
            prev = cur
    return [e / len(perms) for e in effects]


def _pct(cur: float, base: float) -> float | None:
    return (cur - base) / abs(base) if base else None


def _val(values: dict[str, float | None], metric: str, which: str) -> float:
    if metric not in values or values[metric] is None:
        raise DecompositionError(f"missing {which} value for metric {metric!r}")
    v = float(values[metric])  # type: ignore[arg-type]
    if math.isnan(v):
        raise DecompositionError(f"{which} value for metric {metric!r} is NaN")
    return v


def decompose(
    tree: MetricTree,
    values_current: dict[str, float | None],
    values_baseline: dict[str, float | None],
    *,
    root: str | None = None,
    approved_only: bool = True,
    _scale: float = 1.0,
    _seen: tuple[str, ...] = (),
) -> Decomposition:
    """Decompose the change of ``root`` (default: the tree root) into driver effects."""
    metric = root or tree.root_metric
    if metric in _seen:
        raise DecompositionError(f"metric tree has a cycle through {metric!r}")
    p1, p0 = _val(values_current, metric, "current"), _val(values_baseline, metric, "baseline")
    dp = p1 - p0
    edges = tree.children(metric, approved_only=approved_only)
    if not edges:
        raise DecompositionError(
            f"metric {metric!r} has no {'approved ' if approved_only else ''}drivers in the tree"
        )
    relations = {e.relation for e in edges}
    notes: list[str] = []
    c1 = [_val(values_current, e.child, "current") for e in edges]
    c0 = [_val(values_baseline, e.child, "baseline") for e in edges]

    if relations <= {"additive", "subtractive"}:
        method: Literal["additive", "lmdi", "shapley", "ratio_lmdi", "ratio_shapley"] = "additive"
        signs = [(-1.0 if e.relation == "subtractive" else 1.0) for e in edges]
        effects = [s * (a - b) for s, a, b in zip(signs, c1, c0, strict=True)]
        explained = sum(effects)
        implied1 = sum(s * a for s, a in zip(signs, c1, strict=True))
        implied0 = sum(s * b for s, b in zip(signs, c0, strict=True))
    elif relations == {"multiplicative"}:
        implied1, implied0 = math.prod(c1), math.prod(c0)
        if all(v > 0 for v in c1 + c0):
            method = "lmdi"
            weight = logmean(implied1, implied0)
            effects = [weight * math.log(a / b) for a, b in zip(c1, c0, strict=True)]
        else:
            method = "shapley"
            notes.append("non-positive driver values: used the Shapley decomposition instead of LMDI")
            effects = shapley_product(c0, c1)
        explained = sum(effects)
    elif relations == {"ratio_numerator", "ratio_denominator"}:
        nums = [i for i, e in enumerate(edges) if e.relation == "ratio_numerator"]
        dens = [i for i, e in enumerate(edges) if e.relation == "ratio_denominator"]
        if len(nums) != 1 or len(dens) != 1:
            raise DecompositionError(
                f"ratio metric {metric!r} needs exactly one numerator and one denominator driver"
            )
        i_n, i_d = nums[0], dens[0]
        n1, n0, d1, d0 = c1[i_n], c0[i_n], c1[i_d], c0[i_d]
        if d1 == 0 or d0 == 0:
            raise DecompositionError(f"denominator of {metric!r} is zero; the ratio is undefined")
        implied1, implied0 = n1 / d1, n0 / d0
        effects = [0.0] * len(edges)
        if min(n1, n0, d1, d0) > 0:
            method = "ratio_lmdi"
            weight = logmean(implied1, implied0)
            effects[i_n] = weight * math.log(n1 / n0)
            effects[i_d] = -weight * math.log(d1 / d0)
        else:
            method = "ratio_shapley"
            notes.append("non-positive values: used the Shapley decomposition instead of LMDI")
            effects[i_n] = 0.5 * ((n1 / d0 - n0 / d0) + (n1 / d1 - n0 / d1))
            effects[i_d] = 0.5 * ((n0 / d1 - n0 / d0) + (n1 / d1 - n1 / d0))
        explained = sum(effects)
    else:
        raise DecompositionError(
            f"drivers of {metric!r} mix incompatible relations {sorted(relations)}; split them into separate tree levels"
        )

    residual = dp - explained
    exact = abs(residual) <= 1e-6 * max(1.0, abs(dp), abs(p1), abs(p0))
    if not exact:
        notes.append(
            f"drivers imply {metric} = {implied1:.6g} (current) and {implied0:.6g} (baseline) but the metric is "
            f"{p1:.6g} and {p0:.6g}; the unexplained change {residual:.6g} is reported as residual"
        )
    out_effects: list[DriverEffect] = []
    for e, a, b, eff in zip(edges, c1, c0, effects, strict=True):
        share = eff / dp if abs(dp) > _TOL else None
        on_root = eff * _scale
        sub = None
        if tree.children(e.child, approved_only=approved_only):
            child_change = a - b
            child_scale = (on_root / child_change) if abs(child_change) > _TOL else 0.0
            sub = decompose(
                tree,
                values_current,
                values_baseline,
                root=e.child,
                approved_only=approved_only,
                _scale=child_scale,
                _seen=(*_seen, metric),
            )
        out_effects.append(
            DriverEffect(
                metric=e.child,
                relation=e.relation,
                current=a,
                baseline=b,
                change=a - b,
                pct_change=_pct(a, b),
                effect=eff,
                share_of_change=share,
                effect_on_root=on_root,
                sub=sub,
            )
        )
    if abs(dp) <= _TOL:
        notes.append(f"{metric} did not change; shares of change are undefined")
    return Decomposition(
        metric=metric,
        method=method,
        current=p1,
        baseline=p0,
        change=dp,
        pct_change=_pct(p1, p0),
        effects=out_effects,
        residual=0.0 if exact else residual,
        exact=exact,
        notes=notes,
    )


def decompose_metric(
    store: WorkspaceStore,
    model: SemanticModel,
    root_metric: str,
    current: TimeWindow,
    baseline: TimeWindow,
    filters: list[Filter] | None = None,
    *,
    tree: MetricTree | None = None,
) -> MetricDecomposition:
    """Compute every metric of the tree for both windows (one query each) and decompose."""
    t = tree or model.get_tree(root_metric)
    if t is None:
        raise DecompositionError(f"no metric tree for {root_metric!r}")
    metrics = t.metric_ids()
    if root_metric not in metrics:
        raise DecompositionError(f"{root_metric!r} is not part of the tree")
    cq, cres = run_metric_query(
        store, model, MetricQuery(metrics=metrics, filters=filters or [], time=current)
    )
    bq, bres = run_metric_query(
        store, model, MetricQuery(metrics=metrics, filters=filters or [], time=baseline)
    )
    vc = {m: (None if not cres.rows or cres.scalar(m) is None else float(cres.scalar(m))) for m in metrics}
    vb = {m: (None if not bres.rows or bres.scalar(m) is None else float(bres.scalar(m))) for m in metrics}
    dec = decompose(t, vc, vb, root=root_metric)
    return MetricDecomposition(
        decomposition=dec,
        values_current=vc,
        values_baseline=vb,
        sql_current=cq.sql,
        sql_baseline=bq.sql,
        metric_versions=cq.metric_versions,
        current_window=current,
        baseline_window=baseline,
        filters=filters or [],
    )
