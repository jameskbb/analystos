"""Pairing actual metrics with their governed plan metrics (``Metric.plan_for``).

A comparison against plan decomposes through the same metric tree as a change over time,
with every node compared with its own plan counterpart. The counterpart of a tree child is
the plan metric that declares ``plan_for: <child>``; when several do (an operating plan and a
sales quota, say), the one inside the dependency closure of the root's plan metric wins, so
the children always come from the same plan as the root. Nothing here names a metric.
"""

from __future__ import annotations

from analystos_engine.semantic.models import SemanticModel, SemanticModelError

from .semantic_graph import dimensions_for_metric


def plan_counterpart(model: SemanticModel, actual_id: str, root_plan_id: str | None = None) -> str | None:
    """The plan metric of ``actual_id`` from the same plan as ``root_plan_id`` (or the only one)."""
    cands = model.plan_metrics_for(actual_id)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0].id
    if root_plan_id and model.has_metric(root_plan_id):
        try:
            closure = model.metric_dependencies(root_plan_id)
        except SemanticModelError:
            closure = []
        inside = [c for c in cands if c.id in closure]
        if len(inside) == 1:
            return inside[0].id
        entities = {model.get_metric(d).entity for d in closure if model.get_metric(d).kind == "simple"} - {
            None
        }
        same_source = [c for c in cands if c.kind == "simple" and c.entity in entities]
        if len(same_source) == 1:
            return same_source[0].id
    return None


def variance_edges(
    model: SemanticModel, metric_id: str, root_plan_id: str | None
) -> list[tuple[str, str, str]] | None:
    """(child, relation, child_plan) for an additive identity whose every child has a plan
    counterpart; None when the metric has no such identity (then the variance stays whole)."""
    from .planner import driver_edges

    edges = driver_edges(model, metric_id)
    if not edges or any(r not in ("additive", "subtractive") for _c, r in edges):
        return None
    out: list[tuple[str, str, str]] = []
    for child, relation in edges:
        plan = plan_counterpart(model, child, root_plan_id)
        if plan is None:
            return None
        out.append((child, relation, plan))
    return out


def shared_dimensions(model: SemanticModel, metric_ids: list[str]) -> list[str]:
    """Categorical dimensions that can slice every metric in ``metric_ids`` without fan-out
    (for an actual and its plan: the conformed dimensions both carry). One dimension per
    entity (its key column when it has one), so the list is a grain, not a set of aliases."""
    common: set[str] | None = None
    for mid in metric_ids:
        usable, _ = dimensions_for_metric(model, mid)
        names = {d.name for d in usable}
        common = names if common is None else common & names
    if not common:
        return []
    by_entity: dict[str, list[str]] = {}
    for name in sorted(common):
        by_entity.setdefault(model.get_dimension(name).entity, []).append(name)
    out: list[str] = []
    for entity, dims in sorted(by_entity.items()):
        keys = model.get_entity(entity).key_columns
        keyed = [n for n in dims if model.get_dimension(n).expr.strip() in keys]
        out.append((keyed or dims)[0])
    return sorted(out)


def has_plan_tree(model: SemanticModel, metric_id: str, plan_id: str | None) -> bool:
    """True when the metric's tree can be compared with plan node by node."""
    return plan_id is not None and variance_edges(model, metric_id, plan_id) is not None
