from __future__ import annotations

import pytest
from analystos_engine.semantic.models import SemanticModel
from analystos_engine.sqlsafety import UnsafeSQLError
from analystos_investigator import (
    TEMPLATES,
    ExecutionConfig,
    PlanningError,
    add_step,
    choose_template,
    generate_hypotheses,
    interpret,
    plan,
    remove_step,
    set_step_enabled,
    update_step,
)
from analystos_investigator.semantic_graph import dimensions_for_metric, time_dimension_for_metric
from analystos_investigator.templates import dimension_roles, rank_dimensions
from inv_fixture import TODAY


def _plan(q: str, model: SemanticModel, **kw: object):  # type: ignore[no-untyped-def]
    return plan(interpret(q, model, TODAY), model, **kw)  # type: ignore[arg-type]


def test_why_plan_derives_steps_from_tree_dimensions_and_template(model: SemanticModel) -> None:
    p = _plan("Why did revenue decline in August?", model)
    ids = [s.id for s in p.steps]
    assert ids[0] == "compare:revenue"
    assert "decompose:revenue" in ids
    contrib = [s for s in p.steps if s.kind == "contribution" and s.params["metric_id"] == "revenue"]
    assert {s.params["dimension"] for s in contrib} >= {"branch_name", "customer_name", "channel", "category"}
    assert any(s.id.startswith("contribution:orders:") for s in p.steps)
    assert any(s.id.startswith("contribution:aov:") for s in p.steps)
    assert "seasonality:revenue" in ids
    assert p.template_id == "revenue_decline"
    assert p.context is not None and p.context.metric_id == "revenue"
    assert p.requires_approval and p.approval_reasons
    assert p.estimated_queries > p.config.approval_query_threshold


def test_lightweight_lookup_needs_no_approval(model: SemanticModel) -> None:
    p = _plan("What was revenue in July?", model)
    assert [s.kind for s in p.steps] == ["compare"]
    assert not p.requires_approval


def test_breakdown_plan_only_has_requested_dimension(model: SemanticModel) -> None:
    p = _plan("Break down revenue by channel for August", model)
    assert [s.id for s in p.steps] == ["compare:revenue", "contribution:revenue:channel"]


def test_ambiguous_interpretation_cannot_be_planned(model: SemanticModel) -> None:
    with pytest.raises(PlanningError, match="ambiguous"):
        _plan("Why did margin fall?", model)


def test_budget_plan_uses_dimensions_shared_with_budget(model: SemanticModel) -> None:
    p = _plan("Why is revenue below budget in August?", model)
    dims = {s.params["dimension"] for s in p.steps if s.kind == "contribution"}
    assert dims <= {"branch_name", "region", "branch_type"}
    assert not any(s.kind == "decompose" for s in p.steps)
    assert any("not available on budget_revenue" in n for n in p.notes)


def test_plan_editing(model: SemanticModel) -> None:
    p = _plan("Why did revenue decline in August?", model)
    n = len(p.steps)
    p2 = remove_step(p, "seasonality:revenue")
    assert len(p2.steps) == n - 1 and len(p.steps) == n
    p3 = set_step_enabled(p2, "contribution:revenue:channel", False)
    assert not p3.step("contribution:revenue:channel").enabled
    assert p3.estimated_queries < p2.estimated_queries
    p4 = update_step(p3, "decompose:revenue", title="Volume vs price")
    assert p4.step("decompose:revenue").title == "Volume vs price"
    p4 = remove_step(p4, "contribution:revenue:region")  # planned by default for revenue (geography)
    p5 = add_step(p4, model, "contribution", dimension="region")
    assert p5.step("contribution:revenue:region").origin == "user"
    p6 = add_step(p5, model, "custom_sql", sql="SELECT count(*) FROM orders", title="Order count")
    assert p6.steps[-1].params["sql"].lower().startswith("select")
    with pytest.raises(PlanningError, match="cannot be removed"):
        remove_step(p, "compare:revenue")
    with pytest.raises(PlanningError, match="already has"):
        add_step(p5, model, "contribution", dimension="region")


def test_add_step_rejects_unsafe_sql_and_unreachable_dimension(model: SemanticModel) -> None:
    p = _plan("Why did revenue decline in August?", model)
    with pytest.raises(UnsafeSQLError):
        add_step(p, model, "custom_sql", sql="DELETE FROM orders")
    with pytest.raises(PlanningError, match="fan-out"):
        add_step(p, model, "contribution", metric_id="budget_revenue", dimension="customer_name")


def test_reachable_dimensions_follow_many_to_one_only(model: SemanticModel) -> None:
    usable, unreachable = dimensions_for_metric(model, "budget_revenue")
    assert {d.name for d in usable} == {"branch_name", "region", "branch_type"}
    assert "customer_name" in {d.name for d in unreachable}
    ratio_usable, _ = dimensions_for_metric(model, "aov")
    assert "customer_name" in {d.name for d in ratio_usable}
    assert time_dimension_for_metric(model, "aov") == "order_date"
    assert time_dimension_for_metric(model, "budget_revenue") == "budget_month"


def test_hypotheses_cover_the_generic_framework(model: SemanticModel) -> None:
    i = interpret("Why did revenue decline in August?", model, TODAY)
    hyps = generate_hypotheses(i, model)
    cats = {h.category for h in hyps}
    assert {
        "driver_decomposition",
        "dimension_mix",
        "customer_concentration",
        "seasonality",
        "untestable",
    } <= cats
    for h in hyps:
        if h.category == "untestable":
            assert not h.testable and h.statement.startswith("Hypothesis (not tested)")
        else:
            assert h.testable and h.test_step_ids
    assert any("Fewer Orders" in h.statement for h in hyps)


def test_template_selection_and_role_ranking(model: SemanticModel) -> None:
    assert choose_template(interpret("Why did revenue fall?", model, TODAY), model).id == "revenue_decline"
    assert (
        choose_template(interpret("Why did gross margin fall?", model, TODAY), model).id == "margin_variance"
    )
    assert choose_template(interpret("Why did AOV fall?", model, TODAY), model).id == "general_change"
    assert (
        choose_template(interpret("Compare revenue by region", model, TODAY), model).id
        == "regional_performance"
    )
    assert dimension_roles(model.get_dimension("customer_segment")) == ["customer_segment"]
    assert "geography" in dimension_roles(model.get_dimension("branch_name"))
    usable, _ = dimensions_for_metric(model, "revenue")
    ranked = [d.name for d in rank_dimensions(usable, TEMPLATES["revenue_decline"])]
    # round robin: one dimension of each role before a second geography dimension
    assert ranked.index("customer_name") < ranked.index("region")
    assert ranked[0] == "branch_name"


def test_every_template_is_a_strategy_not_a_schema() -> None:
    assert set(TEMPLATES) >= {
        "revenue_decline",
        "margin_variance",
        "conversion_decline",
        "churn",
        "inventory_spike",
        "forecast_miss",
        "regional_performance",
    }
    for t in TEMPLATES.values():
        assert t.dimension_roles and t.untestable_hypotheses
        dumped = t.model_dump_json()
        for table_word in ("order_lines", "SELECT", "FROM "):
            assert table_word not in dumped


def test_config_limits_are_stored_on_plan(model: SemanticModel) -> None:
    cfg = ExecutionConfig(top_n=3, drill_depth=1, approval_query_threshold=1000)
    p = _plan("Why did revenue decline in August?", model, config=cfg)
    assert p.config.top_n == 3 and not p.requires_approval
