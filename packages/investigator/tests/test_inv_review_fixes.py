"""Regression tests for the adversarial review findings owned by the investigator
(R-03, R-04, R-06, R-12, R-22, R-24, R-39, R-46)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from analystos_engine.semantic.models import GlossaryTerm, SemanticModel
from analystos_engine.store import WorkspaceStore
from analystos_investigator import (
    InvestigationRun,
    annotate_node,
    investigate,
    parse_command,
    plan,
    rerun,
    resolve_ambiguity,
    set_node_status,
)
from analystos_investigator.attribution import ratio_contribution
from analystos_investigator.commands import CommandContext
from analystos_investigator.formatting import fmt_currency
from analystos_investigator.interpret import interpret
from analystos_investigator.semantic_graph import partitions
from inv_fixture import TODAY, make_store

# --------------------------------------------------------------------------- R-03


def test_ratio_contribution_refuses_overlapping_segments() -> None:
    # Two orders: order 1 has a Lumber and a Roofing line, order 2 only Roofing.
    # AOV = revenue / orders: total 2 orders, but per category Lumber 1 + Roofing 2 = 3 orders.
    out = ratio_contribution(
        {"Lumber": 100.0, "Roofing": 300.0},
        {"Lumber": 1.0, "Roofing": 2.0},
        {"Lumber": 80.0, "Roofing": 200.0},
        {"Lumber": 1.0, "Roofing": 2.0},
        totals=(400.0, 2.0, 280.0, 2.0),
    )
    assert out.method == "non_additive"
    assert not out.additive_valid
    assert all(r.share is None for r in out.rows)
    assert out.total_current == pytest.approx(200.0)  # the true AOV, not 400/3
    assert "overlap" in out.notes[0]


def test_ratio_contribution_with_matching_totals_still_decomposes() -> None:
    out = ratio_contribution(
        {"a": 100.0, "b": 300.0},
        {"a": 1.0, "b": 1.0},
        {"a": 80.0, "b": 200.0},
        {"a": 1.0, "b": 1.0},
        totals=(400.0, 2.0, 280.0, 2.0),
    )
    assert out.method == "ratio_mix_rate" and out.additive_valid
    assert sum(r.share or 0 for r in out.rows) == pytest.approx(1.0)


def test_partition_rule_and_plan_skip_order_ratio_by_product(model: SemanticModel) -> None:
    assert partitions(model, "revenue", "category")
    assert partitions(model, "orders", "branch_name")  # order key reaches branch many-to-one
    assert not partitions(model, "aov", "category")  # an order spans several products
    assert not partitions(model, "active_customers", "channel")
    interp = interpret("Why did revenue decline in August?", model, TODAY)
    p = plan(interp, model)
    assert not [
        s for s in p.steps if s.params.get("metric_id") == "aov" and s.params.get("dimension") == "category"
    ]


# --------------------------------------------------------------------------- R-06


def _with_dimension_glossary(model: SemanticModel) -> SemanticModel:
    m = model.model_copy(deep=True)
    m.glossary.append(GlossaryTerm(term="Region", definition="Sales region", related=["Revenue", "Orders"]))
    m.glossary.append(
        GlossaryTerm(term="Product Tier", definition="Good/better/best", related=["AOV"], synonyms=["tier"])
    )
    return m


def test_dimension_names_win_over_glossary_terms(model: SemanticModel) -> None:
    m = _with_dimension_glossary(model)
    i = interpret("Break down August revenue by region", m, TODAY)
    assert i.metric_ids == ["revenue"]
    assert i.breakdown_dimensions == ["region"]
    assert not i.ambiguous
    i2 = interpret("Show revenue by product tier for August", m, TODAY)
    assert i2.breakdown_dimensions == ["product_tier"] and not i2.ambiguous
    cmd = parse_command("break this down by region", CommandContext(today=TODAY), m)
    assert cmd.kind == "breakdown" and cmd.dimension == "region" and not cmd.unresolved


def test_named_breakdown_is_planned_first(model: SemanticModel) -> None:
    interp = interpret("Which region drove the August revenue decline?", model, TODAY)
    assert interp.breakdown_dimensions == ["region"]
    p = plan(interp, model)
    assert p.steps[1].id == "contribution:revenue:region"


# --------------------------------------------------------------------------- R-12


def test_rerun_carries_analyst_decisions(
    revenue_run: InvestigationRun, store: WorkspaceStore, model: SemanticModel
) -> None:
    inv = revenue_run.investigation
    seg = next(n for n in inv.tree.nodes if n.kind == "segment" and n.segment and n.segment.value == "Dallas")
    other = next(
        n for n in inv.tree.nodes if n.kind == "segment" and n.segment and n.segment.value == "Houston"
    )
    inv = set_node_status(inv, other.id, "rejected")
    inv = annotate_node(inv, other.id, "not convinced")
    inv = set_node_status(inv, seg.id, "confirmed")
    inv = inv.model_copy(
        update={
            "tree": inv.tree.model_copy(
                update={
                    "nodes": [
                        n.model_copy(update={"finding_id": "f1"}) if n.id == seg.id else n
                        for n in inv.tree.nodes
                    ]
                }
            )
        }
    )
    new, diff = rerun(InvestigationRun(investigation=inv, artifacts=revenue_run.artifacts), store, model)
    nodes = new.investigation.tree.by_id()
    assert nodes[other.id].status == "rejected"
    assert nodes[other.id].annotations == ["not convinced"]
    assert nodes[seg.id].status == "confirmed"
    assert nodes[seg.id].finding_id == "f1"
    assert nodes[seg.id].decision_history[-1]["carried_as"] == "confirmed"
    assert any("decisions carried forward" in s for s in diff.summary)


def test_rerun_flags_confirmed_node_whose_numbers_changed(tmp_path: Path, model: SemanticModel) -> None:
    store = make_store(tmp_path / "a")
    try:
        run = investigate("Why did revenue decline in August?", store, model, TODAY, auto_approve=True)
        dallas = next(
            n
            for n in run.investigation.tree.nodes
            if n.kind == "segment"
            and n.metric_id == "revenue"
            and [s.value for s in n.segment_path] == ["Dallas"]
        )
        inv = set_node_status(run.investigation, dallas.id, "confirmed")
        # Change the data under the node: Dallas' August orders lose half their value.
        rows = store.execute_read("SELECT * FROM order_lines").to_records()
        orders = {r["order_id"]: r for r in store.execute_read("SELECT * FROM orders").to_records()}
        for r in rows:
            o = orders[r["order_id"]]
            if o["branch_id"] == 1 and o["order_date"] >= dt.date(2026, 8, 1):
                r["net_amount"] = r["net_amount"] / 2
        import pandas as pd

        store.write_table("order_lines", pd.DataFrame(rows), if_exists="replace")
        new, diff = rerun(InvestigationRun(investigation=inv, artifacts=run.artifacts), store, model)
        node = new.investigation.tree.get(dallas.id)
        assert node.status == "needs_review"
        assert any("needs review" in n for n in node.notes)
        assert any("needs review" in s for s in diff.summary)
    finally:
        store.close()


# --------------------------------------------------------------------------- R-22, R-39, R-46


def test_growth_question_without_metric_defaults_to_core_revenue(model: SemanticModel) -> None:
    m = model.model_copy(deep=True)
    m.get_metric("revenue").tags.append("core")
    i = interpret("Which product categories grew fastest?", m, TODAY)
    assert i.metric_ids == ["revenue"]
    assert i.ranking == "growth"
    assert i.breakdown_dimensions == ["category"]
    assert any("no metric named" in n for n in i.confidence_notes)
    p = plan(i, m)
    step = next(s for s in p.steps if s.kind == "contribution")
    assert step.params["rank_by"] == "growth"


def test_budget_comparison_is_recognised(model: SemanticModel) -> None:
    i = interpret("How did August revenue compare to budget?", model, TODAY)
    assert i.comparison_kind == "budget"
    assert i.metric_ids == ["revenue"]
    assert i.baseline_metric_id == "budget_revenue"


def test_segment_vs_segment_comparison(
    store: WorkspaceStore, model: SemanticModel, value_index: dict[str, list[str]]
) -> None:
    i = interpret("Compare Dallas vs Houston revenue in August", model, TODAY, value_index=value_index)
    assert i.comparison_kind == "segment"
    assert i.segment_comparison is not None
    assert (i.segment_comparison.current, i.segment_comparison.baseline) == ("Dallas", "Houston")
    assert not [f for f in i.filters if f.dimension == "branch_name"]
    run = investigate(
        "Compare Dallas vs Houston revenue in August",
        store,
        model,
        TODAY,
        value_index=value_index,
        auto_approve=True,
    )
    root = run.investigation.tree.root()
    assert root.current == pytest.approx(240_000.0)
    assert root.baseline == pytest.approx(220_000.0)
    assert "Dallas" in root.statement and "Houston" in root.statement
    assert not any(n.dimension == "branch_name" for n in run.investigation.tree.nodes)


def test_explicit_prior_year_baseline_is_yoy(model: SemanticModel) -> None:
    i = interpret("Why did revenue decline in Q2 2026 versus Q2 2025?", model, TODAY)
    assert i.comparison_kind == "yoy"


def test_adaptive_currency_precision() -> None:
    assert fmt_currency(18.4) == "$18.40"
    assert fmt_currency(0.12) == "$0.12"
    assert fmt_currency(1234.5) == "$1,234"
    assert fmt_currency(12_345.0) == "$12.3k"


def test_followups_unique_and_seasonality_capped(revenue_run: InvestigationRun) -> None:
    f = revenue_run.investigation.followups
    assert len(f) == len(set(f))
    seas = [
        n for n in revenue_run.investigation.tree.nodes if n.step_id and n.step_id.startswith("seasonality:")
    ]
    for n in seas:
        assert n.evidence_strength in ("moderate", "weak")


# --------------------------------------------------------------------------- R-04 (margin bridge, premises)

MARGIN_YAML = """
name: margin_fixture
entities:
  - {name: line, table: lines, primary_key: line_id, default_time_dimension: sale_date}
  - {name: product, table: products, primary_key: product_id}
dimensions:
  - {name: sale_date, entity: line, expr: sale_date, type: time}
  - {name: sku, entity: product, expr: product_id, label: SKU}
  - {name: category, entity: product, expr: category, label: Product category}
metrics:
  - {id: revenue, name: Revenue, entity: line, agg: sum, expr: net, format: currency, tags: [revenue, core]}
  - {id: gross_revenue, name: Gross Revenue, entity: line, agg: sum, expr: gross, format: currency}
  - {id: discount_amount, name: Discounts, entity: line, agg: sum, expr: gross - net, format: currency,
     tags: [discount]}
  - {id: discount_rate, name: Discount Rate, kind: ratio, numerator: discount_amount, denominator: gross_revenue,
     format: percent, tags: [discount]}
  - {id: cogs, name: COGS, entity: line, agg: sum, expr: cost, format: currency, tags: [cost]}
  - {id: units, name: Units, entity: line, agg: sum, expr: qty, format: integer, tags: [volume]}
  - {id: gross_margin, name: Gross Margin, kind: derived, formula: revenue - cogs, format: currency,
     tags: [margin]}
  - {id: gross_margin_pct, name: Gross Margin %, kind: ratio, numerator: gross_margin, denominator: revenue,
     format: percent, tags: [margin], synonyms: [margin %]}
relationships:
  - {from_entity: line, from_col: product_id, to_entity: product, to_col: product_id, cardinality: many_to_one,
     approved: true}
metric_trees:
  - root_metric: gross_margin_pct
    nodes:
      - {parent: gross_margin_pct, child: gross_margin, relation: ratio_numerator}
      - {parent: gross_margin_pct, child: revenue, relation: ratio_denominator}
      - {parent: gross_margin, child: revenue, relation: additive}
      - {parent: gross_margin, child: cogs, relation: subtractive}
  - root_metric: gross_revenue
    nodes:
      - {parent: gross_revenue, child: revenue, relation: additive}
      - {parent: gross_revenue, child: discount_amount, relation: additive}
"""


@pytest.fixture()
def margin_store(tmp_path: Path) -> WorkspaceStore:
    import pandas as pd

    store = WorkspaceStore(tmp_path / "margin")
    d0, d1 = dt.date(2025, 5, 15), dt.date(2026, 5, 15)
    lines = [
        # line_id, date, product, qty, gross, net, cost
        (1, d0, "A", 100, 1000.0, 900.0, 500.0),
        (2, d0, "B", 100, 2000.0, 2000.0, 1000.0),
        (3, d1, "A", 100, 1000.0, 800.0, 600.0),  # unit cost 5 -> 6, discount 10% -> 20%
        (4, d1, "B", 105, 2100.0, 2100.0, 1050.0),  # unchanged unit cost and discount, more volume
    ]
    store.write_table(
        "lines",
        pd.DataFrame(lines, columns=["line_id", "sale_date", "product_id", "qty", "gross", "net", "cost"]),
    )
    store.write_table("products", pd.DataFrame({"product_id": ["A", "B"], "category": ["Lumber", "Tools"]}))
    yield store
    store.close()


def test_premise_picks_the_period_where_revenue_is_flat_and_bridge_splits_margin(
    margin_store: WorkspaceStore,
) -> None:
    model = SemanticModel.from_yaml(MARGIN_YAML)
    today = dt.date(2026, 7, 15)
    run = investigate(
        "Revenue was flat. Why did margin % decline?", margin_store, model, today, auto_approve=True
    )
    inv = run.investigation
    if inv.status == "needs_disambiguation":
        run = resolve_ambiguity(
            run,
            margin_store,
            model,
            {a.term: "gross_margin_pct" for a in inv.interpretation.ambiguous},
            auto_approve=True,
        )
        inv = run.investigation
    assert inv.status == "completed", inv.failures
    interp = inv.interpretation
    assert interp.metric_ids[0] == "gross_margin_pct"
    assert [p.metric_id for p in interp.premises] == ["revenue"]
    # The default (June vs May 2026) has no data; Q2 2026 vs Q2 2025 is where revenue is flat.
    assert (interp.window.start, interp.baseline.start) == (dt.date(2026, 4, 1), dt.date(2025, 4, 1))
    assert interp.comparison_kind == "yoy"
    assert any(c.holds for c in interp.period_candidates)
    nodes = inv.tree.nodes
    premise = next(n for n in nodes if n.step_id == "premise:revenue")
    assert "the premise holds" in premise.statement
    # No circular "Gross Margin explains GM%" driver.
    assert not [n for n in nodes if n.kind == "driver" and n.metric_id == "gross_margin"]
    by_label = {
        n.metric_label: n
        for n in nodes
        if n.kind in ("driver", "other") and n.step_id and n.step_id.startswith("margin_bridge")
    }
    cost, disc, rest = by_label["Unit cost"], by_label["Discounting"], by_label["Mix and other"]
    m0, m1 = 1400 / 2900, 1250 / 2900
    assert cost.contribution_to_parent.effect == pytest.approx((1550 - 1650) / 2900)
    assert disc.contribution_to_parent.effect == pytest.approx(1350 / 2900 - 1450 / 3000)
    total = (
        cost.contribution_to_parent.effect
        + disc.contribution_to_parent.effect
        + rest.contribution_to_parent.effect
    )
    assert total == pytest.approx(m1 - m0)
    assert cost.statement_type == "supported_explanation"
    # The cost effect is located in Lumber (product A), the only item whose unit cost rose.
    lumber = next(
        n
        for n in nodes
        if n.parent_id and n.segment and n.segment.value == "Lumber" and n.metric_label == "Unit cost"
    )
    assert lumber.contribution_to_parent.share == pytest.approx(1.0)
    assert "Unit cost" in (inv.brief_answer or "")


def test_contradicted_premise_is_reported(margin_store: WorkspaceStore) -> None:
    model = SemanticModel.from_yaml(MARGIN_YAML)
    run = investigate(
        "Revenue grew strongly in Q2 2026 vs Q2 2025. Why did margin % decline?",
        margin_store,
        model,
        dt.date(2026, 7, 15),
        auto_approve=True,
    )
    inv = run.investigation
    premise = next(n for n in inv.tree.nodes if n.step_id == "premise:revenue")
    assert "does NOT hold" in premise.statement
    assert "does not hold" in (inv.brief_answer or "")
