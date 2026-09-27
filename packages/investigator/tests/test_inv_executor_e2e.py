"""End-to-end: the engine must discover the planted drivers in the fixture (see inv_fixture)."""

from __future__ import annotations

import math

import pytest
from analystos_engine.semantic.models import SemanticModel
from analystos_engine.store import WorkspaceStore
from analystos_investigator import (
    ExecutionConfig,
    InvestigationRun,
    TreeNode,
    add_step,
    execute,
    interpret,
    investigate,
    node_lineage,
    plan,
    run,
)
from inv_fixture import MALICIOUS, TODAY

TOL = 1e-6


def _root(r: InvestigationRun) -> TreeNode:
    return r.investigation.tree.root()


def _group(r: InvestigationRun, parent: TreeNode, dimension: str, metric: str | None = None) -> TreeNode:
    for c in r.investigation.tree.children_of(parent.id):
        if c.kind == "dimension" and c.dimension == dimension and (metric is None or c.metric_id == metric):
            return c
    raise AssertionError(f"no {dimension} group under {parent.statement}")


def _segment(r: InvestigationRun, group: TreeNode, value: str) -> TreeNode:
    for c in r.investigation.tree.children_of(group.id):
        if c.segment is not None and c.segment.value == value:
            return c
    raise AssertionError(f"no segment {value} in {group.statement}")


def test_root_measures_the_planted_decline(revenue_run: InvestigationRun) -> None:
    inv = revenue_run.investigation
    assert inv.status == "completed"
    root = _root(revenue_run)
    assert root.kind == "root" and root.statement_type == "observation"
    assert root.current == pytest.approx(900_000.0)
    assert root.baseline == pytest.approx(1_000_000.0)
    assert root.pct_change == pytest.approx(-0.10, abs=TOL)
    assert root.statement.startswith("Revenue declined 10.0%")
    assert root.evidence_strength == "strong"


def test_driver_decomposition_orders_times_aov(revenue_run: InvestigationRun) -> None:
    root = _root(revenue_run)
    drivers = {
        c.metric_id: c for c in revenue_run.investigation.tree.children_of(root.id) if c.kind == "driver"
    }
    assert set(drivers) == {"orders", "aov"}
    orders, aov = drivers["orders"], drivers["aov"]
    assert orders.contribution_to_parent is not None and aov.contribution_to_parent is not None
    assert orders.contribution_to_parent.method == "lmdi"
    assert orders.contribution_to_parent.share == pytest.approx(math.log(0.93) / math.log(0.9), abs=1e-6)
    assert orders.contribution_to_parent.share + aov.contribution_to_parent.share == pytest.approx(1.0)
    assert orders.pct_change == pytest.approx(-0.07)
    assert orders.statement_type == "supported_explanation"
    assert orders.evidence_strength == "strong"
    assert any("identity reproduces" in r for r in orders.evidence_reasons)
    # drivers come first among the root's children, largest first
    first = revenue_run.investigation.tree.children_of(root.id)[0]
    assert first.id == orders.id and first.rank == 1


def test_dallas_explains_sixty_percent(revenue_run: InvestigationRun) -> None:
    root = _root(revenue_run)
    branch = _group(revenue_run, root, "branch_name", "revenue")
    dallas = _segment(revenue_run, branch, "Dallas")
    assert dallas.contribution_to_parent is not None
    assert dallas.contribution_to_parent.share == pytest.approx(0.60, abs=TOL)
    assert dallas.pct_change == pytest.approx(-0.20, abs=TOL)
    assert dallas.rank == 1
    assert dallas.statement_type == "supported_explanation"
    assert dallas.evidence_strength == "strong"
    assert any("Orders change" in r for r in dallas.evidence_reasons)  # corroborated by Orders by branch
    shares = {
        c.segment.value: c.contribution_to_parent.share
        for c in revenue_run.investigation.tree.children_of(branch.id)
        if c.segment and c.contribution_to_parent
    }
    assert shares["Houston"] == pytest.approx(0.30)
    assert shares["San Antonio"] == pytest.approx(-0.10)


def test_shares_sum_to_one_within_each_additive_dimension(revenue_run: InvestigationRun) -> None:
    tree = revenue_run.investigation.tree
    checked = 0
    for g in tree.nodes:
        if g.kind != "dimension" or "partition the total" not in " ".join(g.notes):
            continue
        kids = tree.children_of(g.id)
        total = sum(
            k.contribution_to_parent.share
            for k in kids
            if k.contribution_to_parent and k.contribution_to_parent.share is not None
        )
        assert total == pytest.approx(1.0, abs=1e-9), g.statement
        checked += 1
    assert checked >= 5


def test_dimensions_are_never_summed_across(revenue_run: InvestigationRun) -> None:
    root = _root(revenue_run)
    groups = [c for c in revenue_run.investigation.tree.children_of(root.id) if c.kind == "dimension"]
    assert len(groups) >= 4
    for g in groups:
        assert "shares are never added across different dimensions" in g.notes
        assert g.contribution_to_parent is None  # a dimension group claims no share of its own


def test_recursive_drill_finds_customer_concentration_inside_dallas(revenue_run: InvestigationRun) -> None:
    root = _root(revenue_run)
    dallas = _segment(revenue_run, _group(revenue_run, root, "branch_name", "revenue"), "Dallas")
    inner_drivers = {
        c.metric_id: c for c in revenue_run.investigation.tree.children_of(dallas.id) if c.kind == "driver"
    }
    assert inner_drivers["orders"].contribution_to_parent.share == pytest.approx(1.0, abs=TOL)  # type: ignore[union-attr]
    customers = _group(revenue_run, dallas, "customer_name")
    big = _segment(revenue_run, customers, "Big Build Co")
    assert big.contribution_to_parent is not None
    assert big.contribution_to_parent.share == pytest.approx(0.75, abs=TOL)
    assert [s.dimension for s in big.segment_path] == ["branch_name", "customer_name"]
    assert big.evidence_strength == "strong"
    # the drill's queries are filtered to Dallas
    query_arts = [a for a in node_lineage(revenue_run, big.id) if a.kind == "query"]
    assert query_arts and all(
        any(f.dimension == "branch_name" and f.values == ["Dallas"] for f in a.filters) for a in query_arts
    )


def test_customer_concentration_at_top_level(revenue_run: InvestigationRun) -> None:
    root = _root(revenue_run)
    customers = _group(revenue_run, root, "customer_name", "revenue")
    big = _segment(revenue_run, customers, "Big Build Co")
    assert big.contribution_to_parent.share == pytest.approx(0.45, abs=TOL)  # type: ignore[union-attr]
    # 7 customers exceed top_n=5, so the rest are grouped in an "other" node
    kinds = [c.kind for c in revenue_run.investigation.tree.children_of(customers.id)]
    assert kinds[-1] == "other" and kinds.count("segment") == 5


def test_ratio_driver_contribution_finds_houston_price_drop(revenue_run: InvestigationRun) -> None:
    root = _root(revenue_run)
    aov = next(c for c in revenue_run.investigation.tree.children_of(root.id) if c.metric_id == "aov")
    by_branch = _group(revenue_run, aov, "branch_name", "aov")
    houston = revenue_run.investigation.tree.children_of(by_branch.id)[0]
    assert houston.segment is not None and houston.segment.value == "Houston"
    c = houston.contribution_to_parent
    assert c is not None and c.method == "ratio_mix_rate"
    assert c.rate_effect is not None and c.rate_effect < 0
    assert c.share is not None and c.share > 0.9
    assert houston.current == pytest.approx(880.0)


def test_untested_hypotheses_are_marked_and_never_facts(revenue_run: InvestigationRun) -> None:
    hyps = [n for n in revenue_run.investigation.tree.nodes if n.kind == "hypothesis"]
    assert len(hyps) == 3
    for h in hyps:
        assert h.statement_type == "hypothesis"
        assert h.evidence_strength == "hypothesis_only"
        assert h.statement.startswith("Hypothesis (not tested):")
        assert not h.artifact_ids


def test_missing_prior_year_data_skips_seasonality_with_a_note(revenue_run: InvestigationRun) -> None:
    # the fixture has no 2025 data: the seasonality check is skipped and says why
    root = _root(revenue_run)
    assert any("seasonality check skipped" in n for n in root.notes)
    assert not any(n.step_id == "seasonality:revenue" for n in revenue_run.investigation.tree.nodes)


def test_failed_tests_are_recorded_honestly(store: WorkspaceStore, model: SemanticModel) -> None:
    p = plan(
        interpret("Why did revenue decline in August?", model, TODAY),
        model,
        config=ExecutionConfig(max_dimensions=1, driver_dimensions=0, drill_depth=0),
    )
    p = add_step(p, model, "custom_sql", sql="SELECT missing_col FROM orders", title="Broken check")
    r = execute(p, store, model)
    failed = [n for n in r.tree.nodes if n.status == "failed"]
    assert len(failed) == 1
    n = failed[0]
    assert n.statement.startswith("Test failed: Broken check") and "No conclusion drawn" in n.statement
    assert n.evidence_strength == "hypothesis_only" and n.current is None and n.statement_type == "hypothesis"
    assert any("test failed" in reason for reason in n.evidence_reasons)
    assert r.failures and "Broken check" in r.failures[0]


def test_artifacts_are_reproducible_records(revenue_run: InvestigationRun) -> None:
    queries = [a for a in revenue_run.artifacts if a.kind == "query" and a.error is None]
    assert len(queries) > 20
    for a in queries:
        assert a.sql and a.sql.lower().lstrip().startswith(("select", "with"))
        assert a.metric_versions and all("@v" in v for v in a.metric_versions.values())
        assert a.dataset_versions and all(len(d.content_hash) == 64 for d in a.dataset_versions)
        assert a.validation is not None and a.window is not None and a.result is not None
        if not a.validation.ok:
            # only the prior-year seasonality probes may fail validation (the fixture has no 2025 data)
            assert a.window.start.year == 2025, a.title
    tables = {d.table for a in queries for d in a.dataset_versions}
    assert {"order_lines", "orders", "branches", "customers"} <= tables
    results = [a for a in revenue_run.artifacts if a.kind == "metric_result"]
    assert results and all(a.parent_ids for a in results)
    assert any(
        a.chart_spec and a.chart_spec.get("meta", {}).get("chart_type") == "waterfall" for a in results
    )
    ids = [a.id for a in revenue_run.artifacts]
    assert len(ids) == len(set(ids))
    for n in revenue_run.investigation.tree.nodes:
        for aid in n.artifact_ids:
            assert aid in ids


def test_every_statement_number_comes_from_node_values(revenue_run: InvestigationRun) -> None:
    from analystos_investigator.llm.verifier import EvidenceNumbers, verify_text

    ctx = revenue_run.investigation.plan.context  # type: ignore[union-attr]
    assert ctx is not None
    ev = EvidenceNumbers.from_run(
        revenue_run.artifacts,
        revenue_run.investigation.tree.nodes,
        dates=[ctx.window.start, ctx.baseline.start],
    )
    for n in revenue_run.investigation.tree.nodes:
        if n.kind in ("hypothesis", "failed", "check"):
            continue
        res = verify_text(n.statement.replace(MALICIOUS, ""), ev)
        assert res.ok, (n.statement, res.rejected_texts)


def test_brief_answer_and_followups(revenue_run: InvestigationRun) -> None:
    brief = revenue_run.investigation.brief_answer or ""
    assert brief.startswith("Revenue declined 10.0%")
    assert "Orders (69%)" in brief and "of the Revenue change" in brief
    assert "Big Build Co" in brief or "Dallas" in brief
    assert revenue_run.investigation.followups
    assert revenue_run.investigation.hypotheses


def test_same_data_and_definitions_yield_the_same_tree(
    store: WorkspaceStore,
    model: SemanticModel,
    value_index: dict[str, list[str]],
    revenue_run: InvestigationRun,
) -> None:
    again = investigate(
        "Why did revenue decline in August?", store, model, TODAY, value_index=value_index, auto_approve=True
    )
    a = [n.model_dump() for n in revenue_run.investigation.tree.nodes]
    b = [n.model_dump() for n in again.investigation.tree.nodes]
    assert a == b
    assert sorted(x.id for x in revenue_run.artifacts) == sorted(x.id for x in again.artifacts)


def test_non_additive_metric_claims_no_shares(store: WorkspaceStore, model: SemanticModel) -> None:
    p = plan(interpret("Break down active customers by channel for August", model, TODAY), model)
    r = execute(p, store, model)
    group = next(n for n in r.tree.nodes if n.kind == "dimension")
    assert "segments overlap" in " ".join(group.notes)
    segs = [n for n in r.tree.nodes if n.kind == "segment"]
    assert segs and all(s.contribution_to_parent and s.contribution_to_parent.share is None for s in segs)
    assert all(s.statement_type == "observation" and s.evidence_strength == "weak" for s in segs)
    assert all("no share of the total is claimed" in s.statement for s in segs)


def test_budget_variance_by_branch(store: WorkspaceStore, model: SemanticModel) -> None:
    p = plan(interpret("Why is revenue below budget in August?", model, TODAY), model)
    r = execute(p, store, model)
    root = r.tree.root()
    assert root.current == pytest.approx(900_000.0) and root.baseline == pytest.approx(1_050_000.0)
    assert "below Revenue Budget" in root.statement
    branch = next(n for n in r.tree.nodes if n.kind == "dimension" and n.dimension == "branch_name")
    shares = {
        n.segment.value: n.contribution_to_parent.share
        for n in r.tree.children_of(branch.id)
        if n.segment and n.contribution_to_parent
    }
    assert shares["Dallas"] == pytest.approx(90_000 / 150_000)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_filters_flow_into_every_query(
    store: WorkspaceStore, model: SemanticModel, value_index: dict[str, list[str]]
) -> None:
    i = interpret(
        "Why did revenue fall in August excluding new stores?", model, TODAY, value_index=value_index
    )
    p = plan(i, model, config=ExecutionConfig(max_dimensions=2, drill_depth=0))
    r = execute(p, store, model)
    root = r.tree.root()
    assert root.current == pytest.approx(690_000.0) and root.baseline == pytest.approx(800_000.0)
    assert "filters: branch_type excludes New" in root.statement
    for a in r.artifacts:
        if a.kind == "query" and a.error is None:
            assert any(f.dimension == "branch_type" and f.op == "neq" for f in a.filters)


def test_query_failure_becomes_failed_node(store: WorkspaceStore, model: SemanticModel) -> None:
    p = plan(interpret("What was revenue in August?", model, TODAY), model)
    p = add_step(p, model, "custom_sql", sql="SELECT no_such_column FROM orders", title="Broken")
    tree, artifacts = run(p, store, model)
    failed = [n for n in tree.nodes if n.status == "failed"]
    assert len(failed) == 1 and "Broken" in failed[0].statement
    assert any(a.error for a in artifacts)
    assert tree.root().status != "failed"


def test_query_budget_is_enforced(store: WorkspaceStore, model: SemanticModel) -> None:
    p = plan(
        interpret("Why did revenue decline in August?", model, TODAY),
        model,
        config=ExecutionConfig(max_queries=8),
    )
    r = execute(p, store, model)
    assert r.query_count <= 8
    assert any("query budget" in n.statement for n in r.tree.nodes if n.status == "failed")


def test_custom_sql_step_records_artifact(store: WorkspaceStore, model: SemanticModel) -> None:
    p = plan(interpret("What was revenue in August?", model, TODAY), model)
    p = add_step(
        p,
        model,
        "custom_sql",
        sql="SELECT channel, count(*) AS n FROM orders GROUP BY 1 ORDER BY 1",
        title="Orders by channel",
    )
    r = execute(p, store, model)
    node = next(n for n in r.tree.nodes if n.step_id and n.step_id.startswith("custom_sql"))
    assert "returned 2 rows" in node.statement
    art = next(a for a in r.artifacts if a.id in node.artifact_ids)
    assert art.result is not None and art.result.row_count == 2 and art.warnings


def test_trend_step(store: WorkspaceStore, model: SemanticModel) -> None:
    r = execute(plan(interpret("Revenue trend over time through August", model, TODAY), model), store, model)
    trend = next(n for n in r.tree.nodes if n.step_id == "trend:revenue")
    assert "by month over 2 months" in trend.statement
    assert "high $1.00M (2026-07)" in trend.statement


def test_ambiguous_question_does_not_run(
    store: WorkspaceStore, model: SemanticModel, value_index: dict[str, list[str]]
) -> None:
    r = investigate("Why did margin fall in August?", store, model, TODAY, value_index=value_index)
    assert r.investigation.status == "needs_disambiguation"
    assert r.investigation.plan is None and not r.artifacts


def test_expensive_plan_waits_for_approval(
    store: WorkspaceStore, model: SemanticModel, value_index: dict[str, list[str]]
) -> None:
    r = investigate("Why did revenue decline in August?", store, model, TODAY, value_index=value_index)
    assert r.investigation.status == "awaiting_approval"
    assert r.investigation.plan is not None and not r.artifacts
