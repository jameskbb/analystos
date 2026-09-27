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
    "operating_margin",
    "conversion_rate",
    "leads",
    "inventory_value",
    "days_of_supply",
    "forecast_revenue",
    "budget_revenue",
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
        "operating_margin",
    }
    assert model.find_metrics("margin") == [], "no metric may silently claim the bare word 'margin'"


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
