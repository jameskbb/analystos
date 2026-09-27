"""Reproducibility: rerun the stored plan, diff node values, dataset versions and metric versions."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from analystos_engine.semantic.models import SemanticModel
from analystos_engine.store import WorkspaceStore
from analystos_investigator import (
    ExecutionConfig,
    InvestigationError,
    add_step,
    annotate_node,
    drill,
    investigate,
    rerun,
    run_investigation,
    set_node_status,
    update_plan,
)
from inv_fixture import TODAY

CFG = ExecutionConfig(max_dimensions=3, driver_dimensions=1, drill_depth=1)


def _dallas_node_id(run) -> str:  # type: ignore[no-untyped-def]
    for n in run.investigation.tree.nodes:
        if (
            n.segment
            and n.segment.value == "Dallas"
            and len(n.segment_path) == 1
            and n.metric_id == "revenue"
        ):
            return n.id
    raise AssertionError("no Dallas node")


def test_rerun_without_changes_reports_no_differences(
    fresh_store: WorkspaceStore, model: SemanticModel
) -> None:
    first = investigate(
        "Why did revenue decline in August?", fresh_store, model, TODAY, config=CFG, auto_approve=True
    )
    second, diff = rerun(first, fresh_store, model)
    assert second.investigation.id != first.investigation.id
    assert not diff.has_changes
    assert diff.unchanged_nodes == len(first.investigation.tree.nodes)
    assert [n.id for n in second.investigation.tree.nodes] == [n.id for n in first.investigation.tree.nodes]


def test_rerun_after_data_change_reports_what_changed(
    fresh_store: WorkspaceStore, model: SemanticModel
) -> None:
    first = investigate(
        "Why did revenue decline in August?", fresh_store, model, TODAY, config=CFG, auto_approve=True
    )
    dallas_before = first.investigation.tree.get(_dallas_node_id(first))
    # late-arriving Dallas orders for August (customer 2 = Dallas Roofing, branch 1)
    new_orders = pd.DataFrame(
        {
            "order_id": list(range(100_000, 100_040)),
            "order_date": [dt.date(2026, 8, 20)] * 40,
            "customer_id": [2] * 40,
            "branch_id": [1] * 40,
            "channel": ["Online"] * 40,
        }
    )
    new_lines = pd.DataFrame(
        {
            "line_id": list(range(100_000, 100_040)),
            "order_id": list(range(100_000, 100_040)),
            "product_id": [1] * 40,
            "quantity": [10] * 40,
            "net_amount": [1000.0] * 40,
            "unit_cost": [700.0] * 40,
            "shipping_cost": [20.0] * 40,
        }
    )
    fresh_store.write_table("orders", new_orders, if_exists="append")
    fresh_store.write_table("order_lines", new_lines, if_exists="append")

    second, diff = rerun(first, fresh_store, model)
    assert diff.has_changes
    changed = {c.node_id: c for c in diff.node_changes if c.change == "changed"}
    assert dallas_before.id in changed
    before_pct, after_pct = changed[dallas_before.id].fields["pct_change"]
    assert before_pct == pytest.approx(-0.20)
    assert after_pct == pytest.approx((280_000 - 300_000) / 300_000)
    root_change = changed[first.investigation.tree.root_id or ""]
    assert root_change.fields["current"][1] == pytest.approx(940_000.0)
    tables = {c.table: c for c in diff.dataset_version_changes}
    assert set(tables) >= {"orders", "order_lines"}
    assert tables["orders"].before and tables["orders"].after
    assert tables["orders"].after.row_count == tables["orders"].before.row_count + 40
    assert "branches" not in tables  # unchanged table
    assert not diff.metric_version_changes
    assert any("Branch" not in s and "branch_name = Dallas" in s for s in diff.summary)


def test_rerun_reports_metric_definition_changes(fresh_store: WorkspaceStore, model: SemanticModel) -> None:
    first = investigate("What was revenue in August?", fresh_store, model, TODAY, auto_approve=True)
    changed_model = model.model_copy(deep=True)
    rev = changed_model.get_metric("revenue")
    rev.version = 2
    rev.expr = "net_amount - shipping_cost"
    second, diff = rerun(first, fresh_store, changed_model)
    ids = {c.metric_id: c for c in diff.metric_version_changes}
    assert "revenue" in ids
    assert ids["revenue"].before and ids["revenue"].before.startswith("revenue@v1")
    assert ids["revenue"].after and ids["revenue"].after.startswith("revenue@v2")
    root = second.investigation.tree.root()
    assert root.current == pytest.approx(900_000.0 - 930 * 20.0)


def test_drill_branches_from_a_node_and_is_replayed_on_rerun(
    fresh_store: WorkspaceStore, model: SemanticModel
) -> None:
    cfg = ExecutionConfig(max_dimensions=2, driver_dimensions=0, drill_depth=0)
    first = investigate(
        "Why did revenue decline in August?", fresh_store, model, TODAY, config=cfg, auto_approve=True
    )
    dallas = _dallas_node_id(first)
    drilled = drill(first, fresh_store, model, dallas, "channel")
    kids = drilled.investigation.tree.children_of(dallas)
    group = next(k for k in kids if k.kind == "dimension" and k.dimension == "channel")
    shares = [
        c.contribution_to_parent.share
        for c in drilled.investigation.tree.children_of(group.id)
        if c.contribution_to_parent
    ]
    assert sum(s or 0 for s in shares) == pytest.approx(1.0)
    assert len(drilled.artifacts) > len(first.artifacts)
    again, diff = rerun(drilled, fresh_store, model)
    assert group.id in again.investigation.tree.by_id()
    assert not diff.has_changes
    with pytest.raises(ValueError, match="already"):
        drill(
            drilled,
            fresh_store,
            model,
            next(c.id for c in drilled.investigation.tree.children_of(group.id)),
            "channel",
        )


def test_node_actions(fresh_store: WorkspaceStore, model: SemanticModel) -> None:
    cfg = ExecutionConfig(max_dimensions=1, driver_dimensions=0, drill_depth=0)
    r = investigate(
        "Why did revenue decline in August?", fresh_store, model, TODAY, config=cfg, auto_approve=True
    )
    inv = r.investigation
    root_id = inv.tree.root_id or ""
    inv2 = set_node_status(inv, root_id, "confirmed")
    assert inv2.tree.get(root_id).status == "confirmed" and inv.tree.get(root_id).status == "proposed"
    inv3 = annotate_node(inv2, root_id, "Checked with finance")
    assert inv3.tree.get(root_id).annotations == ["Checked with finance"]
    broken = add_step(inv.plan, model, "custom_sql", sql="SELECT missing_col FROM orders", title="Broken")  # type: ignore[arg-type]
    rerun_inv = run_investigation(update_plan(inv, broken, model), fresh_store, model).investigation
    failed = next(n for n in rerun_inv.tree.nodes if n.status == "failed")
    with pytest.raises(InvestigationError):
        set_node_status(rerun_inv, failed.id, "confirmed")
    rejected = set_node_status(inv, root_id, "rejected")
    assert rejected.tree.get(root_id).status == "rejected"
