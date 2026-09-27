"""Regressions for gaps found by running the investigator end to end on the Summit Supply demo
(the planted stories are described in docs/demo-scenarios.md)."""

from __future__ import annotations

from analystos_engine.semantic.models import DriverEdge, Filter, Metric, MetricTree, SemanticModel
from analystos_engine.store import WorkspaceStore
from analystos_investigator import (
    ExecutionConfig,
    apply_choices,
    execute,
    interpret,
    investigate,
    plan,
)
from analystos_investigator.planner import driver_edges
from inv_fixture import TODAY


def test_subject_of_why_clause_is_the_primary_metric(model: SemanticModel) -> None:
    q = "Revenue was roughly flat in Q2 2026 versus Q2 2025. Why did margin decline?"
    i = interpret(q, model, TODAY)
    assert i.ambiguous and i.ambiguous[0].term == "margin"
    j = apply_choices(i, model, {"margin": "gross_margin"})
    assert j.metric_ids == ["gross_margin", "revenue"]
    assert j.template_id == "margin_variance"
    k = interpret("Revenue was flat in Q2. Why did gross margin decline?", model, TODAY)
    assert k.metric_ids[0] == "gross_margin"


def test_plan_miss_without_an_actual_metric_pairs_the_actual(model: SemanticModel) -> None:
    for q in ("What caused the budget miss in August?", "Why did we miss budget in August?"):
        i = interpret(q, model, TODAY)
        assert i.metric_ids == ["revenue"], q
        assert i.baseline_metric_id == "budget_revenue" and i.comparison_kind == "budget"


def test_dimensions_pinned_by_metric_filters_are_not_broken_down(
    store: WorkspaceStore, model: SemanticModel
) -> None:
    m = model.model_copy(deep=True)
    m.metrics.append(
        Metric(
            id="online_revenue",
            name="Online Revenue",
            kind="simple",
            entity="order_line",
            agg="sum",
            expr="net_amount",
            format="currency",
            default_time_dimension="order_date",
            filters=[Filter(dimension="channel", op="eq", values=["Online"])],
        )
    )
    p = plan(interpret("Why did online revenue decline in August?", m, TODAY), m)
    dims = {s.params.get("dimension") for s in p.steps if s.kind == "contribution"}
    assert "channel" not in dims
    assert any("fixed by the metric's own filters" in n for n in p.notes)


def test_single_value_dimension_is_skipped_with_a_note(
    store: WorkspaceStore, model: SemanticModel, value_index: dict[str, list[str]]
) -> None:
    i = interpret("Why did revenue decline in Dallas in August?", model, TODAY, value_index=value_index)
    p = plan(i, model, config=ExecutionConfig(drill_depth=0, driver_dimensions=0))
    r = execute(p, store, model)
    root = r.tree.root()
    assert not any(n.kind == "dimension" and n.dimension == "region" for n in r.tree.nodes)
    assert any("single value" in n for n in root.notes)


def test_one_metric_tree_per_root_and_identity_gate(store: WorkspaceStore, model: SemanticModel) -> None:
    m = model.model_copy(deep=True)
    m.metrics.append(
        Metric(
            id="asp", name="ASP", kind="ratio", numerator="revenue", denominator="units", format="currency"
        )
    )
    # a second, alternative tree for revenue must not be mixed into the first
    m.metric_trees.append(
        MetricTree(
            root_metric="revenue",
            nodes=[
                DriverEdge(parent="revenue", child="units", relation="multiplicative"),
                DriverEdge(parent="revenue", child="asp", relation="multiplicative"),
            ],
        )
    )
    assert driver_edges(m, "revenue") == [("orders", "multiplicative"), ("aov", "multiplicative")]
    # a wrong tree (orders x units is not revenue) is refused instead of producing huge shares
    bad = model.model_copy(deep=True)
    bad.metric_trees = [
        MetricTree(
            root_metric="revenue",
            nodes=[
                DriverEdge(parent="revenue", child="orders", relation="multiplicative"),
                DriverEdge(parent="revenue", child="units", relation="multiplicative"),
            ],
        )
    ]
    p = plan(
        interpret("Why did revenue decline in August?", bad, TODAY),
        bad,
        config=ExecutionConfig(max_dimensions=1, driver_dimensions=0, drill_depth=0),
    )
    r = execute(p, store, bad)
    assert not any(n.kind == "driver" for n in r.tree.nodes)
    failed = [n for n in r.tree.nodes if n.status == "failed"]
    assert any("do not reproduce" in n.statement for n in failed)


def test_budget_mode_checks_use_the_previous_period_and_drills_stay_reachable(
    store: WorkspaceStore, model: SemanticModel, value_index: dict[str, list[str]]
) -> None:
    m = model.model_copy(deep=True)
    m.get_metric("shipping").tags.append("discount")  # gives the revenue template a related check
    r = investigate(
        "Why is revenue below budget in August?", store, m, TODAY, value_index=value_index, auto_approve=True
    )
    tree = r.investigation.tree
    check = next(n for n in tree.nodes if n.step_id == "check:shipping")
    assert "August 2026 vs July 2026" in check.statement
    assert check.current != check.baseline
    assert not [n for n in tree.nodes if n.status == "failed" and "Grain" in n.statement]
    drilled_dims = {s.dimension for n in tree.nodes for s in n.segment_path}
    assert drilled_dims <= {"branch_name", "region", "branch_type"}
