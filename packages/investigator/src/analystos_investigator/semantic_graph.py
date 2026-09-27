"""Which dimensions can slice a metric without fan-out.

A dimension is usable for a metric when its entity is reachable from every entity the
metric aggregates, walking only *approved* relationships in the many-to-one (or one-to-one)
direction. That is exactly the set of joins that cannot duplicate metric rows, so the plan
never asks the compiler for a grain-unsafe query. Dimensions that are not reachable are
reported in plan notes rather than silently dropped.
"""

from __future__ import annotations

from collections import deque

from analystos_engine.semantic.models import Dimension, SemanticModel, SemanticModelError


def metric_base_entities(model: SemanticModel, metric_id: str) -> set[str]:
    out: set[str] = set()
    for dep in model.metric_dependencies(metric_id):
        m = model.get_metric(dep)
        if m.kind == "simple" and m.entity:
            out.add(m.entity)
    return out


def _safe_edges(model: SemanticModel) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    for r in model.approved_relationships():
        if r.cardinality in ("many_to_one", "one_to_one"):
            edges.setdefault(r.from_entity, set()).add(r.to_entity)
        if r.cardinality in ("one_to_many", "one_to_one"):
            edges.setdefault(r.to_entity, set()).add(r.from_entity)
    return edges


def reachable_entities(model: SemanticModel, entity: str) -> set[str]:
    edges = _safe_edges(model)
    seen = {entity}
    queue = deque([entity])
    while queue:
        cur = queue.popleft()
        for nxt in sorted(edges.get(cur, ())):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def dimensions_for_metric(
    model: SemanticModel, metric_id: str, *, types: tuple[str, ...] = ("categorical",)
) -> tuple[list[Dimension], list[Dimension]]:
    """Return (usable, unreachable) dimensions of the given types for ``metric_id``."""
    try:
        bases = metric_base_entities(model, metric_id)
    except SemanticModelError:
        return [], []
    if not bases:
        return [], []
    reach = set.intersection(*(reachable_entities(model, b) for b in sorted(bases)))
    usable: list[Dimension] = []
    unreachable: list[Dimension] = []
    for d in sorted(model.dimensions, key=lambda d: d.name):
        if d.type not in types:
            continue
        (usable if d.entity in reach else unreachable).append(d)
    return usable, unreachable


def time_dimension_for_metric(model: SemanticModel, metric_id: str) -> str | None:
    """The time dimension used to window a metric: the metric's default, else its entity's
    default, else the first reachable time dimension (by name)."""
    m = model.get_metric(metric_id)
    if m.default_time_dimension:
        return m.default_time_dimension
    for dep in model.metric_dependencies(metric_id):
        dm = model.get_metric(dep)
        if dm.default_time_dimension:
            return dm.default_time_dimension
        if dm.kind == "simple" and dm.entity:
            ent = model.get_entity(dm.entity)
            if ent.default_time_dimension:
                return ent.default_time_dimension
    usable, _ = dimensions_for_metric(model, metric_id, types=("time",))
    return usable[0].name if usable else None


def partitions(model: SemanticModel, metric_id: str, dimension: str) -> bool:
    """True when the segments of ``dimension`` partition ``metric_id``: every row the metric
    counts belongs to exactly one segment, so segment values add up to the total.

    Sums and counts of rows partition along any fan-out safe dimension. A distinct count
    partitions only when the counted key determines the segment: the entity's own key, or a
    foreign key whose parent reaches the dimension through many-to-one steps (orders by
    branch). Orders counted from order lines do *not* partition by product category (one
    order spans several categories). Averages, minimums and maximums never partition. A
    ratio or derived metric partitions when all of its inputs do.
    """
    if not model.has_dimension(dimension):
        return False
    dim = model.get_dimension(dimension)
    try:
        deps = model.metric_dependencies(metric_id)
    except SemanticModelError:
        return False
    for dep in deps:
        m = model.get_metric(dep)
        if m.kind != "simple" or not m.entity:
            continue
        if dim.entity not in reachable_entities(model, m.entity):
            return False  # the metric cannot be sliced by this dimension at all
        if m.agg in ("sum", "count"):
            continue
        if m.agg != "count_distinct":
            return False
        entity = model.get_entity(m.entity)
        col = (m.expr or "").strip().strip('"')
        if col in entity.key_columns and len(entity.key_columns) == 1:
            continue
        parents = [
            r.to_entity
            for r in model.approved_relationships()
            if r.from_entity == entity.name
            and r.from_col == col
            and r.cardinality in ("many_to_one", "one_to_one")
        ]
        if not any(dim.entity in reachable_entities(model, p) for p in parents):
            return False
    return True
