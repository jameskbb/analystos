"""The demo semantic model validates against the engine's SemanticModel schema."""

from __future__ import annotations

import yaml
from analystos_demo import dq_rules, semantic_model, semantic_model_yaml

REQUIRED_METRICS = {
    "revenue",
    "gross_revenue",
    "orders",
    "units",
    "aov",
    "asp",
    "discount_rate",
    "cogs",
    "gross_margin",
    "gross_margin_pct",
    "contribution_margin",
    "operating_profit",
    "operating_expenses",
    "freight_cost",
    "sales_commission",
    "payroll",
    "overtime",
    "occupancy",
    "other_opex",
    "conversion_rate",
    "median_first_response_hours",
    "leads",
    "inventory_value",
    "days_of_supply",
    "budget_revenue",
    "revenue_target",
    "return_rate",
}


def test_yaml_validates_against_engine_schema():
    from analystos_engine.semantic.models import SemanticModel

    model = SemanticModel.from_yaml(semantic_model_yaml())
    issues = model.validate_model()
    assert [i for i in issues if i.severity == "error"] == []
    assert [i for i in issues if i.severity == "warning"] == []
    # YAML round-trip is lossless.
    again = SemanticModel.from_yaml(model.to_yaml())
    assert again.content_hash() == model.content_hash()


def test_required_metrics_and_trees():
    model = semantic_model()
    ids = {m.id for m in model.metrics}
    assert ids >= REQUIRED_METRICS
    revenue = model.get_metric("revenue")
    assert revenue.filters[0].dimension == "order_status"
    assert revenue.filters[0].op == "neq" and revenue.filters[0].values == ["cancelled"]
    roots = [t.root_metric for t in model.metric_trees]
    assert len(roots) == len(set(roots)), "one tree per root metric; alternatives nest inside it"
    rev = model.get_tree("revenue")
    edges = {(e.parent, e.child, e.relation) for e in rev.nodes}
    assert {
        ("revenue", "orders", "multiplicative"),
        ("revenue", "aov", "multiplicative"),
        ("orders", "active_customers", "multiplicative"),
        ("orders", "orders_per_customer", "multiplicative"),
        ("aov", "units_per_order", "multiplicative"),
        ("aov", "asp", "multiplicative"),
    } == edges
    gm = model.get_tree("gross_margin")
    assert {(e.child, e.relation) for e in gm.nodes} == {("revenue", "additive"), ("cogs", "subtractive")}


def test_margin_is_deliberately_ambiguous():
    model = semantic_model()
    terms = model.find_glossary("margin")
    assert len(terms) == 1
    assert set(terms[0].candidate_metric_ids) == {
        "gross_margin",
        "gross_margin_pct",
        "contribution_margin",
        "operating_profit",
    }
    assert model.find_metrics("margin") == [], "no metric may silently claim the bare word 'margin'"


def test_operating_profit_tree_has_a_plan_metric_for_every_node():
    model = semantic_model()
    tree = model.get_tree("operating_profit")
    edges = {(e.parent, e.child, e.relation) for e in tree.nodes}
    lines = ["freight_cost", "labor_cost", "sales_commission", "occupancy", "other_opex"]
    assert edges == {
        ("operating_profit", "gross_margin", "additive"),
        ("operating_profit", "operating_expenses", "subtractive"),
        ("gross_margin", "revenue", "additive"),
        ("gross_margin", "cogs", "subtractive"),
        *{("operating_expenses", line, "additive") for line in lines},
        ("freight_cost", "base_freight", "additive"),
        ("freight_cost", "fuel_surcharge", "additive"),
        ("labor_cost", "payroll", "additive"),
        ("labor_cost", "overtime", "additive"),
    }
    assert model.get_metric("labor_cost").formula.replace(" ", "") == "payroll+overtime"
    assert (
        model.get_metric("plan_freight_cost").formula.replace(" ", "")
        == "plan_base_freight+plan_fuel_surcharge"
    )
    headcount = model.get_metric("headcount")
    assert headcount.time_aggregation == "avg" and model.get_metric("plan_headcount").plan_for == "headcount"
    opex = model.get_metric("operating_expenses")
    assert opex.kind == "derived" and set(opex.formula.replace(" ", "").split("+")) == set(lines)
    plans = {m.plan_for: m for m in model.metrics if m.plan_for and "plan" in m.tags}
    for node in tree.metric_ids():
        assert node in plans, f"no plan metric for {node}"
        assert plans[node].id == f"plan_{node}"
    # Budgets and targets are allocations of the same plan with their own roles, never tagged plan.
    for mid, role in (
        ("budget_revenue", "budget"),
        ("budget_gross_margin", "budget"),
        ("revenue_target", "target"),
    ):
        m = model.get_metric(mid)
        assert role in m.tags and "plan" not in m.tags and m.plan_for is not None


def test_category_is_a_conformed_dimension_for_actuals_and_plan():
    from analystos_engine.semantic.joins import find_path

    model = semantic_model()
    assert model.get_dimension("category").entity == "product_categories"
    for dim_entity in ("product_categories", "regions", "customer_segments"):
        for fact in ("order_lines", "sales_plan"):
            assert find_path(model, fact, dim_entity, safe_only=True) is not None, (fact, dim_entity)
    assert find_path(model, "opex_plan", "regions", safe_only=True) is not None
    # Supplier is governed and reachable from order lines (a drill below the plan grain), not from the plan.
    assert model.get_dimension("supplier").entity == "suppliers"
    assert find_path(model, "order_lines", "suppliers", safe_only=True) is not None
    assert find_path(model, "sales_plan", "suppliers", safe_only=True) is None
    assert model.get_metric("median_first_response_hours").agg == "median"


def test_every_entity_has_grain_and_all_relationships_reference_known_columns(demo):
    import duckdb

    model = semantic_model()
    con = duckdb.connect()
    d = demo.data_dir
    for e in model.entities:
        assert e.grain_description
    cols: dict[str, set[str]] = {}
    for f in demo.manifest.files:
        if f.format == "csv":
            rel = con.sql(f"SELECT * FROM read_csv('{d / f.path}') LIMIT 0")
        elif f.format == "parquet":
            rel = con.sql(f"SELECT * FROM read_parquet('{d / f.path}') LIMIT 0")
        elif f.format == "json":
            rel = con.sql(f"SELECT * FROM read_json_auto('{d / f.path}') LIMIT 0")
        else:
            continue
        cols[f.table] = set(rel.columns)
    cols["budgets_branch"] = {
        "branch_id",
        "branch_name",
        "budget_month",
        "revenue_budget",
        "gross_margin_budget",
        "opex_budget",
    }
    cols["budgets_category"] = {"category", "budget_month", "revenue_budget", "units_budget"}
    assert {"revenue_forecast", "operating_expenses"} & set(cols) == {"operating_expenses"}
    assert "delivery_fleet" not in cols["operating_expenses"], "one thing called freight: orders.freight_cost"
    ent_table = {e.name: e.table for e in model.entities}
    for r in model.relationships:
        assert r.from_col in cols[ent_table[r.from_entity]], r.id
        assert r.to_col in cols[ent_table[r.to_entity]], r.id
    for e in model.entities:
        for k in e.key_columns:
            assert k in cols[e.table], (e.name, k)


def test_dq_rules_parse_and_cover_the_mess():
    rules = dq_rules()
    names = {r.name for r in rules}
    assert "customers_customer_id_unique" in names
    assert "returns_order_fk" in names
    assert {r.kind for r in rules} >= {
        "unique",
        "not_null",
        "range",
        "fk_exists",
        "allowed_values",
        "regex",
        "not_future",
        "custom_sql",
        "between",
    }
    from analystos_demo.loader import DQ_RULES_PATH

    raw = yaml.safe_load(DQ_RULES_PATH.read_text(encoding="utf-8"))
    assert len(raw["rules"]) == len(rules)
