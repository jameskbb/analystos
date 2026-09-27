from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from analystos_engine.profiling import profile_table
from analystos_engine.quality import Rule, rule_failing_sql, run_rule, run_rules, suggest_fix, suggest_rules
from analystos_engine.relationships import discover, to_relationship
from analystos_engine.semantic.joins import JoinPlanningError, analyze_join, find_path, plan_joins
from analystos_engine.semantic.models import Relationship
from analystos_engine.sqlsafety import UnsafeSQLError

TODAY = dt.date(2026, 9, 18)


@pytest.fixture()
def dq_store(star_store):
    star_store.write_table(
        "items",
        pd.DataFrame(
            {
                "item_id": [1, 2, 2, 4, None],
                "qty": [1, -3, 2, 5, 1],
                "pct": [0.1, 0.5, 1.2, 0.0, 0.3],
                "status": ["open", "closed", "open", "weird", None],
                "created": [
                    dt.date(2026, 1, 1),
                    dt.date(2027, 1, 1),
                    dt.date(2026, 5, 1),
                    dt.date(2026, 6, 1),
                    dt.date(2026, 7, 1),
                ],
                "email": ["a@x.com", "bad", "c@y.org", None, "d@z.io"],
                "customer_id": pd.array([1, 2, 99, 3, None], dtype="Int64"),
            }
        ),
    )
    return star_store


@pytest.mark.parametrize(
    ("rule", "failing"),
    [
        (Rule(kind="not_null", table="items", column="item_id"), 1),
        (Rule(kind="unique", table="items", column="item_id"), 2),
        (Rule(kind="range", table="items", column="qty", min_value=0), 1),
        (Rule(kind="range", table="items", column="qty", max_value=4), 1),
        (Rule(kind="between", table="items", column="pct", min_value=0, max_value=1), 1),
        (Rule(kind="between", table="items", column="pct", min_value=0, max_value=1, inclusive=False), 2),
        (
            Rule(
                kind="fk_exists",
                table="items",
                column="customer_id",
                ref_table="customers",
                ref_column="customer_id",
            ),
            1,
        ),
        (Rule(kind="not_future", table="items", column="created"), 1),
        (Rule(kind="allowed_values", table="items", column="status", allowed_values=["open", "closed"]), 1),
        (Rule(kind="regex", table="items", column="email", pattern=r"[^@\s]+@[^@\s]+\.[a-z]+"), 1),
        (Rule(kind="custom_sql", table="items", sql="SELECT * FROM items WHERE qty > 4"), 1),
    ],
)
def test_rules(dq_store, rule, failing):
    res = run_rule(dq_store, rule, today=TODAY)
    assert res.error is None, res.error
    assert res.failing_count == failing
    assert res.passed is (failing == 0)
    assert res.total_count == 5
    assert res.sample_failing_rows.row_count == failing
    assert res.sql.startswith("SELECT")
    assert res.rule_id == rule.id and rule.name


def test_rule_passes(dq_store):
    res = run_rule(dq_store, Rule(kind="not_null", table="orders", column="order_id"))
    assert res.passed and res.failing_count == 0
    assert "No action" in suggest_fix(res)


def test_rules_never_mutate(dq_store):
    before = dq_store.table_version("items").content_hash
    run_rules(
        dq_store,
        [
            Rule(kind="unique", table="items", column="item_id"),
            Rule(kind="not_null", table="items", column="qty"),
        ],
    )
    assert dq_store.table_version("items").content_hash == before


def test_custom_sql_must_be_read_only(dq_store):
    with pytest.raises(UnsafeSQLError):
        rule_failing_sql(Rule(kind="custom_sql", table="items", sql="DELETE FROM items"))
    res = run_rule(dq_store, Rule(kind="custom_sql", table="items", sql="DROP TABLE items"))
    assert res.error and not res.passed
    assert dq_store.has_table("items")
    assert "could not run" in suggest_fix(res)


def test_rule_on_missing_table_reports_error(dq_store):
    res = run_rule(dq_store, Rule(kind="not_null", table="nope", column="x"))
    assert res.error


def test_rule_validation():
    with pytest.raises(ValueError):
        Rule(kind="not_null", table="t")
    with pytest.raises(ValueError):
        Rule(kind="range", table="t", column="c")
    with pytest.raises(ValueError):
        Rule(kind="between", table="t", column="c", min_value=1)
    with pytest.raises(ValueError):
        Rule(kind="fk_exists", table="t", column="c")
    with pytest.raises(ValueError):
        Rule(kind="allowed_values", table="t", column="c")
    with pytest.raises(ValueError):
        Rule(kind="regex", table="t", column="c", pattern="(")
    with pytest.raises(ValueError):
        Rule(kind="custom_sql", table="t")
    a = Rule(kind="not_null", table="t", column="c")
    b = Rule(kind="not_null", table="t", column="c")
    assert a.id == b.id  # stable ids


def test_suggest_rules_and_fixes(dq_store):
    prof = profile_table(dq_store, "items", today=TODAY)
    rels = discover(dq_store, ["items", "customers"])
    rules = suggest_rules(prof, rels)
    kinds = {(r.kind, r.column) for r in rules}
    assert ("between", "pct") in kinds
    assert ("range", "qty") in kinds
    assert ("not_future", "created") in kinds
    assert ("allowed_values", "status") in kinds
    assert ("fk_exists", "customer_id") in kinds
    assert ("regex", "email") in kinds
    assert all(r.suggested and r.rationale for r in rules)
    for r in rules:
        res = run_rule(dq_store, r, today=TODAY)
        text = suggest_fix(res)
        assert "never changes your data" in text or "No action" in text


def test_discover_star_schema(star_store):
    sugg = discover(star_store)
    by_id = {s.id: s for s in sugg}
    a = by_id["orders.customer_id->customers.customer_id"]
    assert a.confidence == "high" and a.cardinality == "many_to_one" and a.overlap_pct == 1.0
    assert any("name" in s for s in a.signals)
    b = by_id["order_lines.order_id->orders.order_id"]
    assert b.confidence == "high"
    c = by_id["order_lines.product_id->products.product_id"]
    assert c.cardinality == "many_to_one"
    rel = to_relationship(a, {"orders": "orders", "customers": "customers"})
    assert rel.approved is False and rel.cardinality == "many_to_one"


def test_discover_orphans_and_padding(star_store):
    star_store.write_table(
        "returns",
        pd.DataFrame(
            {
                "return_id": [1, 2, 3, 4, 5],
                "order_id": [101, 102, 999, 103, 103],
                "sku": [" P1", "P2 ", "P3", "P1", "P3"],
            }
        ),
    )
    star_store.write_table("skus", pd.DataFrame({"sku": ["P1", "P2", "P3"], "name": ["a", "b", "c"]}))
    sugg = {s.id: s for s in discover(star_store, ["returns", "orders", "skus"])}
    r = sugg["returns.order_id->orders.order_id"]
    assert r.orphan_count == 1 and r.overlap_pct == 0.75 and r.confidence != "high"
    s = sugg["returns.sku->skus.sku"]
    assert s.requires_trim and any("trimming" in x for x in s.signals)


def test_discover_many_to_many(star_store):
    star_store.write_table("a_tbl", pd.DataFrame({"region_code": ["N", "N", "S"], "v": [1, 2, 3]}))
    star_store.write_table("b_tbl", pd.DataFrame({"region_code": ["N", "S", "S"], "w": [1, 2, 3]}))
    sugg = discover(star_store, ["a_tbl", "b_tbl"])
    assert sugg and sugg[0].cardinality == "many_to_many" and sugg[0].confidence == "low"


def test_analyze_join(star_store, model):
    rel = next(r for r in model.relationships if r.from_entity == "order_lines" and r.to_entity == "orders")
    ja = analyze_join(star_store, rel, model)
    assert ja.observed_cardinality == "many_to_one"
    assert ja.fanout_factor == 1.0 and ja.orphan_count == 0
    assert ja.left_rows == 8 and ja.right_rows == 5
    reverse = Relationship(
        from_entity="orders",
        from_col="order_id",
        to_entity="order_lines",
        to_col="order_id",
        cardinality="many_to_one",
    )
    ja2 = analyze_join(star_store, reverse, model)
    assert ja2.observed_cardinality == "one_to_many"
    assert ja2.fanout_factor == pytest.approx(8 / 5)
    assert any("declared cardinality" in w for w in ja2.warnings)
    assert any("multiplies" in w for w in ja2.warnings)


def test_analyze_join_many_to_many_and_type_mismatch(star_store):
    star_store.write_table("a_tbl", pd.DataFrame({"k": ["1", "1", "2"]}))
    star_store.write_table("b_tbl", pd.DataFrame({"k": [1, 1, 2]}))
    rel = Relationship(
        from_entity="a_tbl", from_col="k", to_entity="b_tbl", to_col="k", cardinality="many_to_one"
    )
    ja = analyze_join(star_store, rel)
    assert ja.observed_cardinality == "many_to_many"
    assert any("many-to-many" in w for w in ja.warnings)
    assert any("types differ" in w for w in ja.warnings)
    assert ja.fanout_factor == pytest.approx(5 / 3)


def test_plan_joins(model):
    paths = plan_joins(model, ["order_lines", "customers", "products"])
    assert {p.to_entity for p in paths} == {"customers", "products"}
    assert all(p.fanout_safe for p in paths)
    assert "N:1" in paths[0].describe()
    paths = plan_joins(model, ["customers", "order_lines"])  # base auto-chosen: order_lines
    assert paths[0].from_entity == "order_lines"
    with pytest.raises(JoinPlanningError):
        plan_joins(model, ["customers", "order_lines"], base="customers")
    with pytest.raises(JoinPlanningError):
        plan_joins(model, ["orders", "products"], base="orders")
    assert find_path(model, "orders", "orders").steps == []
    assert find_path(model, "customers", "products") is None
    assert find_path(model, "customers", "products", safe_only=False) is not None


def test_fact_to_fact_many_to_many_dropped_when_a_shared_dimension_exists(star_store):
    # R-48: order_lines.product_id and a second fact's product_id both reach products.product_id.
    star_store.write_table(
        "stock",
        pd.DataFrame({"stock_id": [1, 2, 3, 4], "product_id": ["P1", "P1", "P2", "P3"], "qty": [1, 2, 3, 4]}),
    )
    found = discover(star_store, ["order_lines", "stock", "products"])
    sugg = {s.id for s in found}
    assert "stock.product_id->products.product_id" in sugg
    assert "order_lines.product_id->products.product_id" in sugg
    assert not [s for s in found if s.cardinality == "many_to_many" and s.from_col == "product_id"]
