"""Anomaly investigation flow (spec §40) on a planted single-day drop."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from analystos_engine.semantic.models import SemanticModel
from analystos_investigator import investigate, investigate_anomaly
from inv_fixture import TODAY, make_store

DAY = dt.date(2026, 8, 12)


@pytest.fixture()
def anomaly_store(tmp_path: Path):  # type: ignore[no-untyped-def]
    # Dallas (branch 1) records no orders on Aug 12
    s = make_store(tmp_path / "anomaly", drop_branch_day=(1, DAY))
    yield s
    s.close()


def test_single_day_drop_is_explained_by_dallas(anomaly_store, model: SemanticModel) -> None:  # type: ignore[no-untyped-def]
    run = investigate_anomaly(anomaly_store, model, "revenue", DAY)
    inv = run.investigation
    assert inv.status == "completed"
    root = inv.tree.root()
    assert root.baseline is not None and root.current is not None and root.current < root.baseline
    ctx = inv.plan.context  # type: ignore[union-attr]
    assert ctx and ctx.baseline.start == dt.date(2026, 8, 5)
    check = next(n for n in inv.tree.nodes if n.step_id == "anomaly:revenue")
    assert "robust z" in check.statement
    branch = next(
        n for n in inv.tree.children_of(root.id) if n.kind == "dimension" and n.dimension == "branch_name"
    )
    top = inv.tree.children_of(branch.id)[0]
    assert top.segment and top.segment.value == "Dallas"
    assert top.contribution_to_parent and top.contribution_to_parent.share is not None
    assert top.contribution_to_parent.share > 0.9
    assert top.pct_change == pytest.approx(-1.0)


def test_question_route_reaches_the_same_flow(anomaly_store, model: SemanticModel) -> None:  # type: ignore[no-untyped-def]
    run = investigate("Revenue unexpectedly fell on Aug 12", anomaly_store, model, TODAY, auto_approve=True)
    assert run.investigation.interpretation.intent == "anomaly"
    assert any(n.step_id == "anomaly:revenue" for n in run.investigation.tree.nodes)


def test_previous_day_baseline(anomaly_store, model: SemanticModel) -> None:  # type: ignore[no-untyped-def]
    run = investigate_anomaly(anomaly_store, model, "revenue", DAY, baseline="previous_day")
    assert run.investigation.plan.context.baseline.start == dt.date(2026, 8, 11)  # type: ignore[union-attr]
