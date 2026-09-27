"""Analysis planning (spec §16, §18, §53; INV-02, INV-03).

The plan is derived from three sources only:

* the metric tree of the question's metric (driver decomposition steps),
* the dimensions reachable from the metric's entities without fan-out (contribution steps,
  ordered by the template's dimension roles; requested dimensions first),
* the template's related checks (e.g. discount rate), seasonality check and anomaly check.

Plans are plain data. ``add_step``, ``remove_step``, ``set_step_enabled`` and
``update_step`` return edited copies, so the analyst can change a plan before running it.
"""

from __future__ import annotations

from typing import Any

from analystos_engine.semantic.models import SemanticModel

from .models import (
    AnalysisPlan,
    ExecutionConfig,
    Interpretation,
    PlanContext,
    PlanStep,
    StepKind,
)
from .semantic_graph import dimensions_for_metric, partitions, time_dimension_for_metric
from .templates import (
    InvestigationTemplate,
    choose_template,
    dimension_roles,
    get_template,
    metric_matches_role,
    rank_dimensions,
)


class PlanningError(ValueError):
    """The interpretation cannot be planned (no metric, unresolved ambiguity, no period)."""


def _metric_query_cost(model: SemanticModel, metric_id: str, *, ratio_split: bool = True) -> int:
    m = model.get_metric(metric_id)
    return 4 if (ratio_split and m.kind == "ratio") else 2


def driver_edges(model: SemanticModel, metric_id: str) -> list[tuple[str, str]]:
    """(child, relation) pairs of approved driver edges for ``metric_id``.

    Edges come from a single metric tree: the tree rooted at the metric if there is one,
    otherwise the first tree (in model order) that decomposes it. Edges from different trees
    are never mixed, because alternative decompositions (Orders x AOV, Units x ASP) are
    separate identities and their product is not the parent.
    """
    trees = sorted(model.metric_trees, key=lambda t: 0 if t.root_metric == metric_id else 1)
    for tree in trees:
        out: list[tuple[str, str]] = []
        for e in tree.children(metric_id):
            if model.has_metric(e.child) and (e.child, e.relation) not in out:
                out.append((e.child, e.relation))
        if out:
            return out
    return []


def pinned_dimensions(model: SemanticModel, metric_id: str) -> set[str]:
    """Dimensions fixed by the metric's own filters (e.g. status = completed): breaking the
    metric down by them yields a single segment that trivially explains 100%."""
    out: set[str] = set()
    for dep in model.metric_dependencies(metric_id):
        for f in model.get_metric(dep).filters:
            if f.op in ("eq", "in", "is_null", "not_null"):
                out.add(f.dimension)
    return out


def _finest_dimension(model: SemanticModel, dims: list[Any], role: str) -> Any | None:
    """The most granular dimension of a role: the entity key itself, else sku/product/item."""
    cands = [d for d in dims if role in dimension_roles(d)]
    if not cands:
        return None

    def key(d: Any) -> tuple[int, str]:
        ent = model.get_entity(d.entity)
        if d.expr.strip() in ent.key_columns and len(ent.key_columns) == 1:
            return (0, d.name)
        words = f"{d.name} {d.label or ''}".lower()
        for i, w in enumerate(("sku", "product", "item")):
            if w in words:
                return (1 + i, d.name)
        return (9, d.name)

    return sorted(cands, key=key)[0]


def margin_bridge(model: SemanticModel, metric_id: str, tpl: InvestigationTemplate) -> dict[str, Any] | None:
    """Inputs of the price/cost bridge for a margin metric, found through the metric trees.

    A margin is ``revenue - cost (- other costs)`` in dollars, or that over ``revenue`` as a
    rate. The bridge needs: the cost metric (a subtractive driver), a volume metric on the
    cost's entity (units), optionally a list-price revenue metric of which revenue and a
    discount metric are additive parts, and a product dimension at item grain. Returns None
    when the model does not carry that structure.
    """
    m = model.get_metric(metric_id)
    if m.kind == "ratio" and m.numerator and m.denominator:
        kind, numerator, revenue = "rate", m.numerator, m.denominator
    elif m.kind == "derived":
        kind, numerator, revenue = "amount", metric_id, None
    else:
        return None

    def find_cost(mid: str, depth: int = 0) -> tuple[str | None, str | None]:
        edges = driver_edges(model, mid)
        adds = [c for c, r in edges if r == "additive"]
        subs = [c for c, r in edges if r == "subtractive"]
        for a in adds:
            is_margin = any(r == "subtractive" for _, r in driver_edges(model, a))
            if a == revenue or (
                revenue is None and model.get_metric(a).format == "currency" and not is_margin
            ):
                return (subs[0] if subs else None), a
        for a in adds:
            if depth < 3:
                cost, rev = find_cost(a, depth + 1)
                if cost:
                    return cost, rev
        return None, None

    cost, rev = find_cost(numerator)
    revenue = revenue or rev
    if not cost or not revenue:
        return None
    cm = model.get_metric(cost)
    if cm.kind != "simple" or not cm.entity:
        return None
    volume = next(
        (
            v.id
            for v in sorted(model.metrics, key=lambda v: (not v.canonical, v.id))
            if v.kind == "simple"
            and v.entity == cm.entity
            and v.agg == "sum"
            and v.format == "integer"
            and any(metric_matches_role(v, r) for r in ("units", "quantity", "volume"))
        ),
        None,
    )
    if volume is None:
        return None
    gross = discount = None
    for t in model.metric_trees:
        edges = t.children(t.root_metric)
        if {(e.child, e.relation) for e in edges} >= {(revenue, "additive")} and len(edges) == 2:
            other = [e.child for e in edges if e.child != revenue]
            if other and all(e.relation == "additive" for e in edges):
                gross, discount = t.root_metric, other[0]
                break
    discount_rate = None
    if gross and discount:
        discount_rate = next(
            (
                x.id
                for x in model.metrics
                if x.kind == "ratio" and x.numerator == discount and x.denominator == gross
            ),
            None,
        )
    usable, _ = dimensions_for_metric(model, cost)
    item = _finest_dimension(model, usable, "product")
    if item is None:
        return None
    groups = [
        d for d in rank_dimensions(usable, tpl) if "product" in dimension_roles(d) and d.name != item.name
    ]
    unit_cost = next(
        (
            x.id
            for x in sorted(model.metrics, key=lambda x: x.id)
            if "unit_cost" in [t.lower() for t in x.tags] and x.id != cost
        ),
        None,
    )
    return {
        "metric_id": metric_id,
        "form": kind,
        "revenue": revenue,
        "cost": cost,
        "volume": volume,
        "gross": gross,
        "discount": discount,
        "discount_rate": discount_rate,
        "item_dimension": item.name,
        "group_dimension": groups[0].name if groups else None,
        "unit_cost_metric": unit_cost,
    }


def inventory_health(model: SemanticModel, metric_id: str) -> dict[str, Any] | None:
    """Days-of-supply metric (a ratio over the inventory metric) and its usage denominator."""
    dos = next(
        (
            m
            for m in sorted(model.metrics, key=lambda m: m.id)
            if m.kind == "ratio" and m.numerator == metric_id and m.denominator
        ),
        None,
    )
    if dos is None:
        return None
    usable, _ = dimensions_for_metric(model, metric_id)
    item = _finest_dimension(model, usable, "product")
    if item is None:
        return None
    labels = [
        d for d in usable if d.entity == item.entity and d.name != item.name and d.type == "categorical"
    ]
    label = next((d.name for d in labels if "product" in d.name or "name" in (d.label or "").lower()), None)
    return {
        "metric_id": metric_id,
        "days_metric": dos.id,
        "usage_metric": dos.denominator,
        "item_dimension": item.name,
        "label_dimension": label,
        "threshold_days": 180,
    }


def plan(
    interp: Interpretation,
    model: SemanticModel,
    *,
    template: InvestigationTemplate | str | None = None,
    config: ExecutionConfig | None = None,
) -> AnalysisPlan:
    if interp.ambiguous:
        terms = ", ".join(a.term for a in interp.ambiguous)
        raise PlanningError(f"resolve ambiguous terms before planning: {terms}")
    metric_id = interp.primary_metric_id
    if metric_id is None:
        raise PlanningError("the question does not reference a metric from the semantic model")
    if interp.window is None or interp.baseline is None:
        raise PlanningError("the question has no resolvable period")
    cfg = config or ExecutionConfig()
    tpl = get_template(template) if isinstance(template, str) else template or choose_template(interp, model)
    metric = model.get_metric(metric_id)
    notes: list[str] = []
    steps: list[PlanStep] = []
    budget_mode = interp.comparison_kind in ("budget", "forecast")
    if budget_mode and interp.baseline_metric_id is None:
        raise PlanningError(
            f"no {interp.comparison_kind} metric resolved for {metric_id!r}; "
            "define one in the semantic model or pick it explicitly"
        )
    if time_dimension_for_metric(model, metric_id) is None:
        notes.append(f"{metric_id!r} has no time dimension; periods cannot be applied")

    ctx = PlanContext(
        question=interp.question,
        metric_id=metric_id,
        window=interp.window,
        baseline=interp.baseline,
        comparison_kind=interp.comparison_kind,
        baseline_metric_id=interp.baseline_metric_id,
        filters=list(interp.filters),
        intent=interp.intent,
        direction=interp.direction,
        premises=[p for p in interp.premises if p.metric_id and p.metric_id != metric_id],
        segment_comparison=interp.segment_comparison,
        ranking=interp.ranking,
        reference_date=interp.reference_date,
    )
    segment_mode = interp.segment_comparison is not None
    if segment_mode:
        sc = interp.segment_comparison
        assert sc is not None
        baseline_desc = f"{sc.dimension} = {sc.baseline}, {interp.window.display()}"
    else:
        baseline_desc = (
            f"{interp.baseline_metric_id} for {interp.window.display()}"
            if budget_mode
            else interp.baseline.display()
        )
    steps.append(
        PlanStep(
            id=f"compare:{metric_id}",
            kind="compare",
            title=(
                f"Measure {metric.display_name}: {interp.segment_comparison.dimension} = "
                f"{interp.segment_comparison.current} vs {baseline_desc}"
                if interp.segment_comparison
                else f"Measure {metric.display_name}: {interp.window.display()} vs {baseline_desc}"
            ),
            rationale="Establish the size and direction of the change before explaining it.",
            params={"metric_id": metric_id},
            estimated_queries=2,
            origin="template",
        )
    )

    for pr in ctx.premises:
        pm = model.get_metric(pr.metric_id or "")
        steps.append(
            PlanStep(
                id=f"premise:{pm.id}",
                kind="compare",
                title=f"Premise check: '{pr.text}'",
                rationale="The question takes this as given; measure it over the same periods before relying on it.",
                params={
                    "metric_id": pm.id,
                    "role": "premise",
                    "expectation": pr.expectation,
                    "tolerance": pr.tolerance,
                    "text": pr.text,
                },
                estimated_queries=2,
                origin="template",
            )
        )

    full = interp.intent in ("why_change", "anomaly")
    light_compare = interp.intent == "compare"

    if tpl.id == "inventory_health":
        health = inventory_health(model, metric_id)
        if health is not None:
            steps.append(
                PlanStep(
                    id=f"health:{metric_id}",
                    kind="segment",
                    title=f"Rank {health['item_dimension']} by days of supply at the latest snapshot",
                    rationale=f"Items with more than {health['threshold_days']} days of supply, or stock but no "
                    "sales in the usage window, are slow movers; measure how much of the inventory (and of its "
                    "change) they hold.",
                    params={"role": "inventory_health", **health},
                    estimated_queries=2,
                    origin="template",
                )
            )
        else:
            notes.append(
                f"no days-of-supply metric is defined over {metric.display_name}; health ranking skipped"
            )

    # Price/cost bridge for margins (replaces the definitional Margin / Revenue identity)
    bridge = (
        margin_bridge(model, metric_id, tpl)
        if tpl.id == "margin_variance" and not budget_mode and not segment_mode
        else None
    )
    if bridge is not None and (full or light_compare):
        steps.append(
            PlanStep(
                id=f"margin_bridge:{metric_id}",
                kind="decompose",
                title=f"Split the {metric.display_name} change into unit cost, discounting and mix",
                rationale="Counterfactuals per product: current volumes at baseline unit costs isolate cost "
                "inflation; current list-price sales at baseline discount rates isolate discounting; the rest "
                "is mix, list prices and other costs. The three effects add up exactly to the change.",
                params={"method": "margin_bridge", **bridge},
                estimated_queries=2 + (2 if bridge["unit_cost_metric"] else 0),
                origin="metric_tree",
            )
        )

    # Driver decomposition from the metric tree
    drivers = driver_edges(model, metric_id)
    if bridge is not None and drivers and metric.kind == "ratio":
        notes.append(
            f"{metric.display_name} = {' / '.join(model.get_metric(c).display_name for c, _ in drivers)} is "
            "definitional (the numerator explains almost all of the change by construction); the price/cost "
            "bridge is used instead"
        )
        drivers = []
    if drivers and not budget_mode and (full or light_compare):
        cost = 2 * len(drivers)
        steps.append(
            PlanStep(
                id=f"decompose:{metric_id}",
                kind="decompose",
                title=f"Decompose {metric.display_name} into "
                + " and ".join(model.get_metric(c).display_name for c, _ in drivers),
                rationale="Split the change exactly across the metric's approved drivers "
                "(metric tree) to see whether volume, price or cost moved.",
                params={"metric_id": metric_id, "depth": cfg.decomposition_depth},
                estimated_queries=cost * max(1, cfg.decomposition_depth),
                origin="metric_tree",
            )
        )
    elif not drivers and full:
        notes.append(f"{metric.display_name} has no approved metric tree; no driver decomposition")
    elif budget_mode and drivers:
        notes.append("driver decomposition is skipped for comparisons against budget or forecast")

    # Dimensions
    usable, unreachable = dimensions_for_metric(model, metric_id)
    if budget_mode and interp.baseline_metric_id:
        base_usable, _ = dimensions_for_metric(model, interp.baseline_metric_id)
        names = {d.name for d in base_usable}
        dropped = [d.name for d in usable if d.name not in names]
        usable = [d for d in usable if d.name in names]
        if dropped:
            notes.append(f"dimensions not available on {interp.baseline_metric_id}: {', '.join(dropped)}")
    if unreachable:
        notes.append(
            "dimensions not reachable from "
            f"{metric.display_name} without fan-out (skipped): {', '.join(d.name for d in unreachable)}"
        )
    filtered_eq = {f.dimension for f in interp.filters if f.op == "eq"}
    if segment_mode:
        filtered_eq.add(interp.segment_comparison.dimension)  # type: ignore[union-attr]
    pinned = pinned_dimensions(model, metric_id)
    if pinned & {d.name for d in usable}:
        notes.append(
            "dimensions fixed by the metric's own filters are not broken down: "
            + ", ".join(sorted(pinned & {d.name for d in usable}))
        )
    ranked = [
        d
        for d in rank_dimensions(usable, tpl, interp.breakdown_dimensions)
        if d.name not in filtered_eq and d.name not in pinned
    ]
    for req in interp.breakdown_dimensions:
        if req not in {d.name for d in usable}:
            notes.append(f"requested dimension {req!r} is not reachable from {metric.display_name}")
    max_dims = cfg.max_dimensions or tpl.max_dimensions
    if full:
        chosen = ranked[:max_dims]
        chosen += [d for d in ranked if d.name in interp.breakdown_dimensions and d not in chosen]
    elif interp.breakdown_dimensions:
        chosen = [d for d in ranked if d.name in interp.breakdown_dimensions]
    elif light_compare:
        chosen = ranked[: min(3, max_dims)]
    elif tpl.id == "inventory_health":
        chosen = [d for d in ranked if "product" in dimension_roles(d)][:1]
    else:
        chosen = []
    if metric.kind == "ratio" and not budget_mode:
        overlapping = [d for d in chosen if not partitions(model, metric_id, d.name)]
        if overlapping:
            notes.append(
                f"{metric.display_name} is not broken down by "
                + ", ".join(d.label or d.name for d in overlapping)
                + ": its denominator counts items that span several of those segments, so segment shares "
                "would not add up"
            )
            chosen = [d for d in chosen if d not in overlapping]

    contribution_steps: list[PlanStep] = []
    for d in chosen:
        roles = dimension_roles(d)
        params: dict[str, Any] = {
            "metric_id": metric_id,
            "dimension": d.name,
            "attach_to": metric_id,
            "drill": full
            and (
                tpl.drill_group_roles is None or any(r in tpl.drill_group_roles for r in roles) or not roles
            ),
        }
        if interp.ranking:
            params["rank_by"] = "growth"
            params["direction"] = "decrease" if interp.ranking == "decline" else "increase"
        contribution_steps.append(
            PlanStep(
                id=f"contribution:{metric_id}:{d.name}",
                kind="contribution",
                title=f"{metric.display_name} change by {d.label or d.name}"
                + (" (ranked by growth rate)" if interp.ranking else ""),
                rationale=(
                    "Requested breakdown."
                    if d.name in interp.breakdown_dimensions
                    else f"Locate which {', '.join(roles) or 'segments'} explain the change "
                    f"({tpl.name} strategy)."
                ),
                params=params,
                estimated_queries=_metric_query_cost(model, metric_id, ratio_split=not budget_mode),
                origin="dimension" if d.name not in interp.breakdown_dimensions else "user",
            )
        )
    # Dimensions the analyst named come right after the root measurement.
    requested_steps = [s for s in contribution_steps if s.origin == "user"]
    steps[1:1] = requested_steps
    steps.extend(s for s in contribution_steps if s.origin != "user")

    # Driver contributions: where did each driver move?
    if full and drivers and not budget_mode:
        planned: set[str] = {s.id for s in steps}

        def driver_step(mid: str, d: Any, why: str) -> None:
            sid = f"contribution:{mid}:{d.name}"
            if sid in planned:
                return
            planned.add(sid)
            steps.append(
                PlanStep(
                    id=sid,
                    kind="contribution",
                    title=f"{model.get_metric(mid).display_name} change by {d.label or d.name}",
                    rationale=why,
                    params={"metric_id": mid, "dimension": d.name, "attach_to": mid, "drill": False},
                    estimated_queries=_metric_query_cost(model, mid),
                    origin="metric_tree",
                )
            )

        for child, _rel in drivers:
            child_usable, _ = dimensions_for_metric(model, child)
            child_names = {d.name for d in child_usable}
            cm = model.get_metric(child)
            for d in [d for d in chosen if d.name in child_names][: cfg.driver_dimensions]:
                if cm.kind == "ratio" and not partitions(model, child, d.name):
                    continue
                driver_step(
                    child,
                    d,
                    "Locate where the driver moved, and corroborate segment findings with a second independent test.",
                )
            # Price drivers: product mix (category, tier) is measured on the line-grain price metric
            # (ASP = revenue / units), because order counts overlap across products.
            price_metrics = [
                x
                for x in [child, *(g for g, _ in driver_edges(model, child))]
                if model.get_metric(x).kind == "ratio"
                and any(metric_matches_role(model.get_metric(x), r) for r in ("price", "pricing"))
            ]
            for pm_id in price_metrics:
                pm_usable, _ = dimensions_for_metric(model, pm_id)
                product_dims = [
                    d
                    for d in rank_dimensions(pm_usable, tpl)
                    if "product" in dimension_roles(d)
                    and partitions(model, pm_id, d.name)
                    and d.name not in pinned
                    and d.name not in filtered_eq
                ][:2]
                for d in product_dims:
                    driver_step(
                        pm_id,
                        d,
                        "Price moves with product mix: split the price change into a mix effect (share of "
                        "units by segment) and a rate effect (price within each segment).",
                    )

    # Template checks
    if full:
        exclude = {metric_id, *(c for c, _ in drivers), *(p.metric_id or "" for p in ctx.premises)}
        checks = list(tpl.resolve_checks(model, exclude))
        if bridge is not None and bridge.get("unit_cost_metric"):
            from .templates import TemplateCheck

            uc = model.get_metric(bridge["unit_cost_metric"])
            checks.append(
                (
                    TemplateCheck(
                        title="Recorded unit costs",
                        metric_roles=["unit_cost"],
                        rationale="Recorded standard unit costs rise or fall independently of the sales mix.",
                    ),
                    uc,
                )
            )
        deps = set(model.metric_dependencies(metric_id))
        for hyp, found in tpl.resolve_conditional(model, exclude):
            if found is not None and found.id in deps:
                cm = found
                from .templates import TemplateCheck

                checks.append(
                    (
                        TemplateCheck(
                            title=hyp.text, metric_roles=hyp.metric_roles, rationale=f"Tests: {hyp.text}."
                        ),
                        cm,
                    )
                )
        seen_checks: set[str] = set()
        for check, cm in checks:
            if cm.id in seen_checks:
                continue
            seen_checks.add(cm.id)
            if time_dimension_for_metric(model, cm.id) is None:
                continue
            steps.append(
                PlanStep(
                    id=f"check:{cm.id}",
                    kind="compare",
                    title=f"Check {cm.display_name} ({check.title})",
                    rationale=check.rationale,
                    params={"metric_id": cm.id, "role": "check", "category": check.hypothesis_category},
                    estimated_queries=2,
                    origin="template",
                )
            )
        if (
            tpl.seasonality_check
            and interp.comparison_kind == "pop"
            and interp.window.kind != "day"
            and not segment_mode
        ):
            steps.append(
                PlanStep(
                    id=f"seasonality:{metric_id}",
                    kind="compare",
                    title=f"Seasonality: did {metric.display_name} move the same way a year earlier?",
                    rationale="A change that also happened in the same periods last year is likely seasonal.",
                    params={"metric_id": metric_id, "role": "seasonality"},
                    estimated_queries=2,
                    origin="template",
                )
            )
        if interp.window.kind == "day":
            steps.append(
                PlanStep(
                    id=f"anomaly:{metric_id}",
                    kind="anomaly",
                    title=f"Is the day unusual? {metric.display_name} vs the trailing 8 weeks",
                    rationale="Confirms the day deviates from its normal range before explaining it.",
                    params={"metric_id": metric_id, "lookback_days": 56},
                    estimated_queries=1,
                    origin="template",
                )
            )

    if interp.intent == "trend" or interp.intent == "forecast":
        tdim = time_dimension_for_metric(model, metric_id)
        if tdim:
            steps.append(
                PlanStep(
                    id=f"trend:{metric_id}",
                    kind="segment",
                    title=f"{metric.display_name} by month, trailing 12 months",
                    rationale="Shows the trajectory around the analysis period.",
                    params={"metric_id": metric_id, "time_dimension": tdim, "grain": "month", "periods": 12},
                    estimated_queries=1,
                    origin="template",
                )
            )
        if interp.intent == "forecast":
            notes.append("forecasting runs in the workbench (engine forecast models); the plan shows history")

    for uf in interp.unresolved_filters:
        notes.append(f"filter not applied: {uf.text} ({uf.reason})")

    drill_estimate = 0
    if full:
        drill_estimate = cfg.drill_top_n * cfg.drill_dimensions * 2 * max(1, cfg.drill_depth)
    estimated = sum(s.estimated_queries for s in steps) + drill_estimate
    reasons: list[str] = []
    if estimated > cfg.approval_query_threshold:
        reasons.append(
            f"about {estimated} queries (threshold {cfg.approval_query_threshold}); review the plan first"
        )
    return AnalysisPlan(
        steps=steps,
        requires_approval=bool(reasons),
        approval_reasons=reasons,
        template_id=tpl.id,
        estimated_queries=estimated,
        notes=notes,
        context=ctx,
        config=cfg,
    )


# --------------------------------------------------------------------------- editing


def _recount(p: AnalysisPlan) -> AnalysisPlan:
    est = sum(s.estimated_queries for s in p.steps if s.enabled)
    if p.context and p.context.intent in ("why_change", "anomaly"):
        est += p.config.drill_top_n * p.config.drill_dimensions * 2 * max(1, p.config.drill_depth)
    return p.model_copy(update={"estimated_queries": est})


def remove_step(p: AnalysisPlan, step_id: str) -> AnalysisPlan:
    p.step(step_id)
    if step_id.startswith("compare:") and p.context and step_id == f"compare:{p.context.metric_id}":
        raise PlanningError("the root comparison cannot be removed; every other step explains it")
    return _recount(p.model_copy(update={"steps": [s for s in p.steps if s.id != step_id]}))


def set_step_enabled(p: AnalysisPlan, step_id: str, enabled: bool) -> AnalysisPlan:
    p.step(step_id)
    if not enabled and p.context and step_id == f"compare:{p.context.metric_id}":
        raise PlanningError("the root comparison cannot be disabled")
    steps = [s.model_copy(update={"enabled": enabled}) if s.id == step_id else s for s in p.steps]
    return _recount(p.model_copy(update={"steps": steps}))


def update_step(
    p: AnalysisPlan, step_id: str, *, title: str | None = None, params: dict[str, Any] | None = None
) -> AnalysisPlan:
    p.step(step_id)
    steps = []
    for s in p.steps:
        if s.id == step_id:
            upd: dict[str, Any] = {}
            if title is not None:
                upd["title"] = title
            if params is not None:
                upd["params"] = {**s.params, **params}
            s = s.model_copy(update=upd)
        steps.append(s)
    return _recount(p.model_copy(update={"steps": steps}))


def add_step(
    p: AnalysisPlan,
    model: SemanticModel,
    kind: StepKind,
    *,
    title: str | None = None,
    metric_id: str | None = None,
    dimension: str | None = None,
    sql: str | None = None,
    code: str | None = None,
    rationale: str = "Added by the analyst.",
) -> AnalysisPlan:
    """Add a user-defined step. Contribution steps are validated against the semantic model
    (the dimension must be reachable from the metric without fan-out)."""
    if p.context is None:
        raise PlanningError("plan has no context")
    mid = metric_id or p.context.metric_id
    if not model.has_metric(mid):
        raise PlanningError(f"unknown metric {mid!r}")
    if kind == "contribution":
        if dimension is None:
            raise PlanningError("a contribution step needs a dimension")
        usable, _ = dimensions_for_metric(model, mid)
        if dimension not in {d.name for d in usable}:
            raise PlanningError(f"dimension {dimension!r} cannot slice {mid!r} without fan-out")
        step = PlanStep(
            id=f"contribution:{mid}:{dimension}",
            kind="contribution",
            title=title or f"{model.get_metric(mid).display_name} change by {dimension}",
            rationale=rationale,
            params={
                "metric_id": mid,
                "dimension": dimension,
                "attach_to": mid if mid != p.context.metric_id else p.context.metric_id,
                "drill": False,
            },
            estimated_queries=_metric_query_cost(model, mid),
            origin="user",
        )
    elif kind == "compare":
        step = PlanStep(
            id=f"check:{mid}",
            kind="compare",
            title=title or f"Check {model.get_metric(mid).display_name}",
            rationale=rationale,
            params={"metric_id": mid, "role": "check", "category": "driver_decomposition"},
            estimated_queries=2,
            origin="user",
        )
    elif kind == "custom_sql":
        if not sql:
            raise PlanningError("a custom SQL step needs SQL")
        from analystos_engine.sqlsafety import ensure_read_only

        safe = ensure_read_only(sql)
        n = sum(1 for s in p.steps if s.kind == "custom_sql") + 1
        step = PlanStep(
            id=f"custom_sql:{n}",
            kind="custom_sql",
            title=title or f"Custom SQL {n}",
            rationale=rationale,
            params={"sql": safe},
            estimated_queries=1,
            origin="user",
        )
    elif kind == "python":
        if not code:
            raise PlanningError("a Python step needs code")
        n = sum(1 for s in p.steps if s.kind == "python") + 1
        step = PlanStep(
            id=f"python:{n}",
            kind="python",
            title=title or f"Python analysis {n}",
            rationale=rationale,
            params={"code": code},
            estimated_queries=0,
            origin="user",
        )
    elif kind == "decompose":
        if not driver_edges(model, mid):
            raise PlanningError(f"{mid!r} has no approved metric tree")
        step = PlanStep(
            id=f"decompose:{mid}",
            kind="decompose",
            title=title or f"Decompose {mid}",
            rationale=rationale,
            params={"metric_id": mid, "depth": p.config.decomposition_depth},
            estimated_queries=2 * len(driver_edges(model, mid)),
            origin="user",
        )
    else:
        raise PlanningError(f"steps of kind {kind!r} are added by the planner, not by hand")
    if any(s.id == step.id for s in p.steps):
        raise PlanningError(f"the plan already has step {step.id!r}")
    return _recount(p.model_copy(update={"steps": [*p.steps, step]}))
