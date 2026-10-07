"""Actual vs plan: premise against plan, plan-variance tree, plan bridge, drills, reconciliation,
related signals, the variance brief and the operating review (synthetic fixture, no demo data)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pytest
from analystos_engine.semantic.models import Metric, SemanticModel
from analystos_engine.store import WorkspaceStore
from analystos_investigator import (
    ExecutionConfig,
    InvestigationRun,
    build_value_index,
    executive_summary,
    interpret,
    investigate,
    plan,
    rerun,
    set_node_status,
    variance_review,
)
from analystos_investigator.llm.verifier import causal_leaks
from analystos_investigator.summary import FindingInput
from plan_fixture import FORMAL_QUESTION, QUESTION, TODAY, expected, load_model, make_store


@pytest.fixture(scope="module")
def pmodel() -> SemanticModel:
    return load_model()


@pytest.fixture(scope="module")
def pstore(tmp_path_factory: pytest.TempPathFactory) -> Iterator[WorkspaceStore]:
    s = make_store(tmp_path_factory.mktemp("plan_store"))
    yield s
    s.close()


@pytest.fixture(scope="module")
def pindex(pstore: WorkspaceStore, pmodel: SemanticModel) -> dict[str, list[str]]:
    return build_value_index(pstore, pmodel)


@pytest.fixture(scope="module")
def prun(pstore: WorkspaceStore, pmodel: SemanticModel, pindex: dict[str, list[str]]) -> InvestigationRun:
    return investigate(QUESTION, pstore, pmodel, TODAY, value_index=pindex, auto_approve=True)


def _node(run: InvestigationRun, **match: object):  # type: ignore[no-untyped-def]
    for n in run.investigation.tree.nodes:
        if all(getattr(n, k) == v for k, v in match.items()):
            return n
    raise AssertionError(f"no node with {match}")


# --------------------------------------------------------------------------- interpretation


def test_premise_against_the_named_plan_metric(pmodel: SemanticModel, pindex: dict[str, list[str]]) -> None:
    i = interpret(FORMAL_QUESTION, pmodel, TODAY, value_index=pindex)
    assert i.metric_ids == ["operating_profit"]
    assert i.comparison_kind == "budget"
    # the root's baseline comes from plan_for, not from the other plan metric the question names
    assert i.baseline_metric_id == "plan_operating_profit"
    revenue = next(p for p in i.premises if p.metric_id == "revenue")
    assert revenue.expectation == "meets_plan"
    # "revenue target" names a plan metric without plan_for: its actual is found by name
    assert revenue.baseline_metric_id == "revenue_target"
    assert revenue.tolerance == pytest.approx(0.01)
    assert i.template_id == "plan_variance"
    assert i.window is not None and i.window.start.isoformat() == "2026-04-01"


def test_actual_named_in_a_premise_is_context_not_subject(pmodel: SemanticModel) -> None:
    i = interpret("Revenue met plan, but operating profit missed plan. Why?", pmodel, TODAY)
    assert i.metric_ids[0] == "operating_profit"
    revenue = next(p for p in i.premises if p.metric_id == "revenue")
    assert revenue.expectation == "meets_plan" and revenue.baseline_metric_id == "plan_revenue"


def test_plan_is_reviewable_and_needs_approval(pmodel: SemanticModel, pindex: dict[str, list[str]]) -> None:
    p = plan(interpret(QUESTION, pmodel, TODAY, value_index=pindex), pmodel)
    ids = [s.id for s in p.steps]
    assert ids[:2] == ["compare:operating_profit", "premise:revenue"]
    for sid in (
        "decompose:operating_profit",
        "margin_bridge:gross_margin",
        "variance_drill:operating_profit",
        "reconcile:operating_profit",
        "related_signals:operating_profit",
    ):
        assert sid in ids
    assert p.context is not None
    assert p.context.plan_metrics["gross_margin"] == "plan_gross_margin"
    assert p.context.plan_metrics["freight_cost"] == "plan_freight_cost"
    bridge = p.step("margin_bridge:gross_margin")
    assert bridge.params["mode"] == "plan"
    assert set(bridge.params["cell_dimensions"]) == {"category", "region", "segment"}
    assert bridge.params["cost_basis"] == "units"  # no governed cost-per-list-dollar ratio in this model
    assert p.requires_approval


# --------------------------------------------------------------------------- execution


def test_root_and_premise(prun: InvestigationRun) -> None:
    want = expected()
    inv = prun.investigation
    assert inv.status == "completed"
    root = inv.tree.root()
    assert root.current == pytest.approx(want["operating_profit"])
    assert root.baseline == pytest.approx(want["plan_operating_profit"])
    assert root.title == "Operating Profit missed plan" and root.baseline_label == "Plan"
    premise = _node(prun, role="premise")
    assert premise.verdict == "supported" and "premise holds" in premise.notes
    assert premise.current == pytest.approx(want["revenue"])
    assert premise.baseline == pytest.approx(want["plan_revenue"])


def test_variance_tree_reconciles_exactly(prun: InvestigationRun) -> None:
    want = expected()
    gm = _node(prun, kind="driver", metric_id="gross_margin")
    opex = _node(prun, kind="driver", metric_id="operating_expenses")
    assert gm.root_effect == pytest.approx(want["gross_margin"] - want["plan_gross_margin"])
    assert opex.root_effect == pytest.approx(-(want["operating_expenses"] - want["plan_operating_expenses"]))
    assert gm.title == "Gross Margin shortfall" and opex.title == "Operating Expenses overrun"
    freight = _node(prun, kind="driver", metric_id="freight_cost")
    assert freight.root_effect == pytest.approx(-want["freight_variance"])
    assert freight.contribution_to_parent is not None
    assert freight.contribution_to_parent.effect == pytest.approx(want["freight_variance"])
    rec_node = _node(prun, role="reconciliation")
    rec = rec_node.reconciliation
    assert rec is not None and rec.reconciled and "reconciled" in rec_node.notes
    top = rec.identities[0]
    assert {c.node_id for c in top.components} == {gm.id, opex.id}
    assert sum(c.amount for c in top.components) == pytest.approx(rec.gap)
    assert all(i.reconciled for i in rec.identities)
    # root, gross margin bridge, operating expense lines, freight = base + surcharge, labor = payroll + overtime
    assert len(rec.identities) == 5


def test_plan_bridge_matches_hand_arithmetic(prun: InvestigationRun) -> None:
    want = expected()
    tree = prun.investigation.tree
    gm = _node(prun, kind="driver", metric_id="gross_margin")
    kids = {k.title: k for k in tree.children_of(gm.id)}
    assert kids["Unit-cost inflation"].root_effect == pytest.approx(want["unit_cost_effect"])
    assert kids["Increased discounting"].root_effect == pytest.approx(want["discount_effect"])
    assert kids["Favorable volume and mix"].root_effect == pytest.approx(want["mix_effect"])
    assert want["mix_effect"] > 0


def test_bridge_effects_drill_into_the_governed_cells(prun: InvestigationRun) -> None:
    tree = prun.investigation.tree
    lumber = next(n for n in tree.nodes if n.segment is not None and n.segment.value == "Lumber")
    assert lumber.contribution_to_parent is not None and lumber.contribution_to_parent.share == pytest.approx(
        1.0
    )
    contractor = next(n for n in tree.nodes if n.segment is not None and n.segment.value == "Contractor")
    gulf = next(
        n
        for n in tree.nodes
        if n.segment is not None and n.segment.value == "Gulf" and len(n.segment_path) == 2
    )
    assert gulf.segment_path[0].value == "Contractor"
    assert gulf.root_effect == pytest.approx(contractor.root_effect)
    # Tools had no unit-cost change: it is not shown as a contributor
    assert not any(
        n.segment is not None and n.segment.value == "Tools" and n.root_effect == 0 for n in tree.nodes
    )


def test_material_opex_variances_are_drilled(prun: InvestigationRun) -> None:
    tree = prun.investigation.tree
    overtime = _node(prun, kind="driver", metric_id="overtime")
    groups = [g for g in tree.children_of(overtime.id) if g.kind == "dimension"]
    assert len(groups) == 1
    top = max(tree.children_of(groups[0].id), key=lambda n: abs(n.root_effect or 0.0))
    assert top.segment is not None and top.segment.value in ("Gulf Hub", "Gulf")
    assert top.root_effect == pytest.approx(-3000.0)


def test_related_signal_is_separate_and_not_established(prun: InvestigationRun) -> None:
    inv = prun.investigation
    assert len(inv.related_signals) == 1
    sig = inv.related_signals[0]
    assert sig.metric_id == "conversion_rate" and sig.relationship == "not_established"
    assert [(s.dimension, s.value) for s in sig.segment_path] == [
        ("region", "Gulf"),
        ("lead_channel", "Paid Search"),
    ]
    assert sig.current == pytest.approx(0.10) and sig.baseline == pytest.approx(0.50)
    assert "not established" in sig.statement and "not establish" in sig.relationship_note
    node = inv.tree.get(sig.node_id)
    assert node.role == "related_signal" and node.statement_type == "observation"
    assert node.parent_id == inv.tree.root_id
    rec = _node(prun, role="reconciliation").reconciliation
    assert rec is not None
    assert sig.node_id not in {c.node_id for i in rec.identities for c in i.components}
    support = [inv.tree.get(i) for i in sig.supporting_node_ids]
    assert [s.metric_id for s in support] == ["median_first_response_hours"]
    assert support[0].current == pytest.approx(50.0) and support[0].baseline == pytest.approx(4.0)
    assert support[0].metric_unit == "hours"


def test_brief_states_the_reconciled_and_not_established_sentences(prun: InvestigationRun) -> None:
    brief = prun.investigation.brief_answer or ""
    assert brief.startswith("Operating Profit missed plan by")
    assert "The premise holds" in brief
    assert "which reconciles exactly to the" in brief
    assert "Within Gross Margin, unit-cost inflation accounts for" in brief
    assert "partially offset by" in brief and "favorable volume and mix" in brief
    assert "Related signal, relationship not established" in brief
    assert "4.0 hours to 50.0 hours" in brief
    assert not causal_leaks(brief, ["Conversion Rate", "Median First Response"])
    assert any("conversion rate" in f for f in prun.investigation.followups)


def test_every_explanation_node_traces_to_sql(prun: InvestigationRun) -> None:
    arts = {a.id: a for a in prun.artifacts}

    def reaches(aid: str, seen: set[str]) -> bool:
        a = arts.get(aid)
        if a is None or aid in seen:
            return False
        seen.add(aid)
        return bool(a.sql) or any(reaches(p, seen) for p in a.parent_ids)

    for n in prun.investigation.tree.nodes:
        if n.statement_type == "hypothesis":
            continue
        assert any(reaches(a, set()) for a in n.artifact_ids), n.statement


def test_rerun_reproduces_with_an_empty_diff(
    prun: InvestigationRun, pstore: WorkspaceStore, pmodel: SemanticModel
) -> None:
    confirmed = prun.investigation
    target = _node(prun, role="related_signal", parent_id=confirmed.tree.root_id)
    run = InvestigationRun(
        investigation=set_node_status(confirmed, target.id, "confirmed"), artifacts=prun.artifacts
    )
    again, diff = rerun(run, pstore, pmodel)
    assert not diff.has_changes
    assert again.investigation.brief_answer == confirmed.brief_answer
    assert again.investigation.tree.get(target.id).status == "confirmed"
    assert [s.node_id for s in again.investigation.related_signals] == [target.id]


def test_premise_contradicted_when_revenue_misses(pstore: WorkspaceStore, pmodel: SemanticModel) -> None:
    tight = pmodel.model_copy(deep=True)
    for m in tight.metrics:
        if m.id == "revenue_target":
            m.expr = "plan_revenue * 1.05"
    run = investigate(FORMAL_QUESTION, pstore, tight, TODAY, auto_approve=True)
    premise = _node(run, role="premise")
    assert premise.verdict == "contradicted" and "premise contradicted" in premise.notes
    assert "The premise does not hold" in (run.investigation.brief_answer or "")


def test_cost_basis_per_list_dollar_when_the_model_governs_it(
    pstore: WorkspaceStore, pmodel: SemanticModel
) -> None:
    """With a planned ratio cost / list-price revenue, unit cost is compared per list dollar."""
    m = pmodel.model_copy(deep=True)
    m.metrics += [
        Metric(
            id="cost_to_list",
            name="COGS per List Dollar",
            kind="ratio",
            numerator="cogs",
            denominator="gross_revenue",
            format="percent",
        ),
        Metric(
            id="plan_cost_to_list",
            name="Plan COGS per List Dollar",
            kind="ratio",
            numerator="plan_cogs",
            denominator="plan_gross_revenue",
            format="percent",
            tags=["plan"],
            plan_for="cost_to_list",
        ),
    ]
    m.raise_for_errors()
    run = investigate(QUESTION, pstore, m, TODAY, auto_approve=True)
    step = run.investigation.plan.step("margin_bridge:gross_margin")  # type: ignore[union-attr]
    assert step.params["cost_basis"] == "gross_revenue"
    cost = _node(run, title="Unit-cost inflation")
    # the list price is as planned in every cell, so per-list-dollar and per-unit costs agree here
    assert cost.root_effect == pytest.approx(expected()["unit_cost_effect"])


def test_related_signal_maturity_follows_the_plan_reference_date(
    pstore: WorkspaceStore, pmodel: SemanticModel
) -> None:
    """A signal on a cohort metric is provisional only while its maturity window has not elapsed by
    the reference date frozen on the plan, never by the wall clock: the same data and definitions
    give a provisional, weak signal at one reference date and a settled, strong one later, and a
    rerun keeps the plan's date."""
    aged = pmodel.model_copy(deep=True)
    # Q2 2026 cohorts mature on 2026-09-28; both reference dates keep "last quarter" = Q2 2026.
    aged.get_metric("converted_leads").maturity_days = 90
    early = investigate(QUESTION, pstore, aged, TODAY, auto_approve=True)
    late = investigate(QUESTION, pstore, aged, dt.date(2026, 9, 30), auto_approve=True)
    assert early.investigation.plan is not None and early.investigation.plan.context.reference_date == TODAY
    s_early, s_late = early.investigation.related_signals[0], late.investigation.related_signals[0]
    assert s_early.provisional and s_early.evidence_strength == "weak"
    assert "provisional" in (early.investigation.brief_answer or "")
    assert not s_late.provisional and s_late.evidence_strength == "strong"
    assert "provisional" not in (late.investigation.brief_answer or "")
    again, diff = rerun(early, pstore, aged)
    assert not diff.has_changes and again.investigation.related_signals[0].provisional


def test_related_signals_can_be_switched_off(pstore: WorkspaceStore, pmodel: SemanticModel) -> None:
    run = investigate(
        QUESTION, pstore, pmodel, TODAY, auto_approve=True, config=ExecutionConfig(related_signals=0)
    )
    assert not run.investigation.related_signals
    assert not [n for n in run.investigation.tree.nodes if n.role == "related_signal"]


# --------------------------------------------------------------------------- review and summary


def test_operating_review_uses_confirmed_nodes_only(prun: InvestigationRun) -> None:
    inv = prun.investigation
    empty = variance_review(inv)
    assert empty is not None and not empty.explanation and "No findings" in empty.markdown
    assert not empty.complete and empty.bridge is not None
    assert [st.label for st in empty.bridge.steps] == ["Not yet confirmed"]
    explanation = [
        n.id for n in inv.tree.nodes if n.root_effect is not None or n.role in ("premise", "related_signal")
    ]
    full = variance_review(inv, explanation)
    assert full is not None and full.reconciled and full.complete
    assert "reconciles exactly" in " ".join(full.explanation)
    assert full.bridge is not None and full.bridge.steps[-1].running_total == pytest.approx(full.bridge.end)
    assert "Not yet confirmed" not in [st.label for st in full.bridge.steps]
    assert "| Step | Effect | Running total |" in full.markdown and "**What explains it**" in full.markdown
    assert full.causes[0].startswith("Unit-cost inflation") and "Pine Co" in full.causes[0]
    assert full.related and all("relationship not established" in r for r in full.related)
    assert any("Pine Co" in w for w in full.watch)
    gm = _node(prun, kind="driver", metric_id="gross_margin")
    kids = [c.id for c in inv.tree.children_of(gm.id)]
    partial = variance_review(inv, [inv.tree.root_id or "", gm.id, *kids])
    assert partial is not None and not partial.reconciled and not partial.complete
    assert "not yet confirmed" in partial.markdown.lower() and "Operating Expenses overrun" in partial.pending
    assert partial.bridge is not None and partial.bridge.steps[-1].label == "Not yet confirmed"
    assert partial.bridge.steps[-1].running_total == pytest.approx(partial.bridge.end)
    assert not partial.related
    rejected = variance_review(inv, [i for i in explanation if i != gm.id])
    assert rejected is not None and not rejected.complete


def test_other_lines_are_grouped_and_still_reconcile(prun: InvestigationRun) -> None:
    want = expected()
    other = _node(prun, title="Other lines, net")
    assert other.kind == "other"
    assert {m.metric_id for m in other.members} == {"other_opex", "supplies"}
    assert other.root_effect == pytest.approx(-want["other_lines_variance"])
    assert sum(m.amount for m in other.members) == pytest.approx(other.root_effect)
    opex = _node(prun, kind="driver", metric_id="operating_expenses")
    assert other.parent_id == opex.id
    assert not [
        n
        for n in prun.investigation.tree.nodes
        if n.metric_id in ("other_opex", "supplies") and n.kind == "driver"
    ]


def test_nested_plan_subtrees_and_same_branch_offsets(prun: InvestigationRun) -> None:
    want = expected()
    tree = prun.investigation.tree
    freight = _node(prun, kind="driver", metric_id="freight_cost")
    kids = {k.metric_id: k for k in tree.children_of(freight.id) if k.kind == "driver"}
    assert set(kids) == {"base_freight", "fuel_surcharge"}
    assert kids["fuel_surcharge"].root_effect == pytest.approx(-want["surcharge_variance"])
    assert kids["base_freight"].title == "Base Freight on plan"
    labor = _node(prun, kind="driver", metric_id="labor_cost")
    lk = {k.metric_id: k for k in tree.children_of(labor.id) if k.kind == "driver"}
    assert lk["overtime"].root_effect == pytest.approx(-want["overtime_variance"])
    assert lk["payroll"].root_effect == pytest.approx(-want["payroll_variance"])

    def branch_seg(line_id: str):  # type: ignore[no-untyped-def]
        group = next(g for g in tree.children_of(line_id) if g.kind == "dimension")
        assert group.dimension == "branch"
        return next(s for s in tree.children_of(group.id) if s.segment and s.segment.value == "Gulf Hub")

    ot, pay = branch_seg(lk["overtime"].id), branch_seg(lk["payroll"].id)
    assert ot.offset_node_id == pay.id and pay.offset_node_id == ot.id
    assert "partly offset in the same branch by Payroll saving" in ot.statement


def test_below_plan_grain_split_by_supplier(prun: InvestigationRun) -> None:
    want = expected()
    tree = prun.investigation.tree
    lumber = next(n for n in tree.nodes if n.segment is not None and n.segment.value == "Lumber")
    groups = [g for g in tree.children_of(lumber.id) if g.kind == "dimension"]
    assert [g.dimension for g in groups] == ["supplier"]  # ties with product go to the coarser dimension
    pine = next(s for s in tree.children_of(groups[0].id) if s.segment and s.segment.value == "Pine Co")
    assert pine.root_effect == pytest.approx(want["pine_cost_effect"])
    assert sum(s.root_effect or 0.0 for s in tree.children_of(groups[0].id)) == pytest.approx(
        lumber.root_effect
    )
    assert pine.segment_path[0].value == "Lumber" and pine.baseline_label


def test_plan_to_actual_bridge(prun: InvestigationRun) -> None:
    inv = prun.investigation
    bridge = inv.bridge
    assert bridge is not None and bridge.reconciled
    root = inv.tree.root()
    assert bridge.start == pytest.approx(root.baseline) and bridge.end == pytest.approx(root.current)
    assert sum(st.amount for st in bridge.steps) == pytest.approx(bridge.gap)
    assert bridge.steps[-1].running_total == pytest.approx(bridge.end)
    labels = [st.label for st in bridge.steps]
    assert labels[:3] == ["Unit-cost inflation", "Increased discounting", "Favorable volume and mix"]
    assert labels[-1] == "Other lines, net" and "Freight overrun" in labels and "Labor overrun" in labels
    assert {st.group_label for st in bridge.steps} == {"Gross Margin", "Operating Expenses"}
    assert bridge.chart is not None and bridge.artifact_id in {a.id for a in prun.artifacts}


def test_statements_are_plain_language(prun: InvestigationRun) -> None:
    inv = prun.investigation
    jargon = ("per list dollar", "plan cell", "counterfactual", "identity", "cost basis")
    texts = [n.statement for n in inv.tree.nodes if n.root_effect is not None or n.role]
    texts += [inv.brief_lead or ""]
    review = variance_review(inv, [n.id for n in inv.tree.nodes])
    assert review is not None
    texts.append(review.markdown)
    for t in texts:
        assert not any(j in t.lower() for j in jargon), t
    cost = _node(prun, title="Unit-cost inflation")
    assert cost.method and "Counterfactual" in cost.method


def test_brief_lead_is_short(prun: InvestigationRun) -> None:
    lead = prun.investigation.brief_lead or ""
    assert lead.count(". ") <= 2 and lead.startswith("Operating Profit missed plan by")
    assert "while Revenue met Plan Revenue" in lead
    assert (
        "Gross Margin explains" in lead
        and "The largest causes: unit-cost inflation (Lumber, Pine Co)" in lead
    )


def test_summary_lists_related_signals_apart(prun: InvestigationRun) -> None:
    inv = prun.investigation
    sig = inv.tree.get(inv.related_signals[0].node_id)
    gm = _node(prun, kind="driver", metric_id="gross_margin")
    items = [FindingInput.from_node(n).model_copy(update={"status": "confirmed"}) for n in (sig, gm)]
    s = executive_summary(items)
    assert [i.node_id for i in s.related_signals] == [sig.id]
    assert [i.node_id for i in s.supported_explanations] == [gm.id]
    assert "Related signals (relationship not established)" in s.to_markdown()
    assert "relationship not established" in (s.narrative or "").lower()


def test_causal_leak_verifier() -> None:
    names = ["Conversion Rate"]
    assert causal_leaks("Profit fell because conversion rate collapsed in Gulf Coast.", names) == names
    assert causal_leaks("Lower conversion rate drove the miss.", names) == names
    assert not causal_leaks(
        "Conversion rate fell in Gulf Coast; the data does not establish it as a cause of the miss.", names
    )
    assert not causal_leaks("Discounting drove the miss. Conversion rate also fell in Gulf Coast.", names)


def test_semantic_model_validates_new_fields(pmodel: SemanticModel) -> None:
    bad = pmodel.model_copy(deep=True)
    bad.metrics[0].related_metrics = ["nope"]
    assert any("related_metrics" in i.message for i in bad.validate_model())
    chained = pmodel.model_copy(deep=True)
    for m in chained.metrics:
        if m.id == "plan_revenue":
            m.plan_for = "plan_gross_margin"
    assert any("itself a plan metric" in i.message for i in chained.validate_model())
    base = Metric(id="x", name="X", entity="orders", agg="count")
    tagged = base.model_copy(update={"related_metrics": ["y"], "unit": "hours"})
    assert base.definition_hash() == tagged.definition_hash()


def test_fixture_store_path(tmp_path: Path) -> None:
    s = make_store(tmp_path / "ws")
    try:
        assert s.table_version("sales_plan").row_count == 8
    finally:
        s.close()


def test_segment_statements_lead_with_the_segment(prun: InvestigationRun) -> None:
    """Executive wording: a segment's statement starts with its own name, a breakdown's title
    names the dimension in lower-case words, and the review's watch list adds the path."""
    inv = prun.investigation
    tree = inv.tree
    lumber = next(n for n in tree.nodes if n.segment is not None and n.segment.value == "Lumber")
    assert lumber.statement.startswith("Lumber: costs ")
    group = next(g for g in tree.children_of(lumber.id) if g.kind == "dimension")
    assert group.title == "Unit-cost inflation by supplier"
    assert group.statement.startswith(
        "Unit-cost inflation in Lumber by supplier: the largest part is Pine Co"
    )
    pine = next(s for s in tree.children_of(group.id) if s.segment and s.segment.value == "Pine Co")
    assert pine.statement.startswith("Pine Co: costs ") and "above plan;" in pine.statement
    assert not any(" = " in n.statement for n in tree.nodes if n.root_effect is not None)
    review = variance_review(inv, [n.id for n in tree.nodes])
    assert review is not None and any(w.startswith("Lumber / Pine Co: costs ") for w in review.watch)
    other = _node(prun, title="Other lines, net")
    assert "+$0.00" not in other.statement and other.statement.startswith("Two smaller lines net to ")
