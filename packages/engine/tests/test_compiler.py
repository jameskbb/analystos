from __future__ import annotations

import datetime as dt

import pytest
from analystos_engine.semantic.compiler import (
    CompileError,
    GrainError,
    MetricQuery,
    compile,
    parse_dimension_ref,
    run_metric_query,
)
from analystos_engine.semantic.models import Dimension, Filter, Metric, Relationship
from analystos_engine.store import WorkspaceStore
from analystos_engine.types import TimeWindow

AUG = TimeWindow(dimension="order_date", start=dt.date(2026, 8, 1), end=dt.date(2026, 9, 1))


def rows(store: WorkspaceStore, model, **kw):
    _, res = run_metric_query(store, model, MetricQuery(**kw))
    return res.to_records()


def by(records, key):
    return {r[key]: r for r in records}


def test_parse_dimension_ref():
    assert parse_dimension_ref("order_date__month") == ("order_date", "month")
    assert parse_dimension_ref("region") == ("region", None)


def test_total_revenue_and_orders(star_store, model):
    r = rows(star_store, model, metrics=["revenue", "orders", "shipping", "units"])
    assert r == [{"revenue": 710.0, "orders": 5, "shipping": 42.0, "units": 16}]


def test_order_level_metric_not_double_counted_when_combined_with_line_metric(star_store, model):
    r = by(
        rows(star_store, model, metrics=["revenue", "shipping", "orders"], dimensions=["region"]), "region"
    )
    # naive join of orders to order_lines would give Dallas shipping 10*2 + 20*3 = 80
    assert r["Dallas"] == {"region": "Dallas", "revenue": 530.0, "shipping": 30.0, "orders": 2}
    assert r["Houston"] == {"region": "Houston", "revenue": 130.0, "shipping": 12.0, "orders": 2}
    assert r["Austin"] == {"region": "Austin", "revenue": 50.0, "shipping": 0.0, "orders": 1}
    assert sum(x["shipping"] for x in r.values()) == 42.0


def test_naive_join_would_double_count(star_store):
    naive = star_store.execute_read(
        "SELECT sum(o.shipping_fee) FROM orders o JOIN order_lines l ON l.order_id = o.order_id"
    ).scalar()
    assert naive == 10 * 2 + 20 * 3 + 5 + 0 + 7  # the bug the compiler avoids
    assert naive != 42.0


def test_ratio_metric_is_ratio_of_sums(star_store, model):
    r = by(rows(star_store, model, metrics=["aov"], dimensions=["region"]), "region")
    assert r["Dallas"]["aov"] == pytest.approx(530 / 2)
    total = rows(star_store, model, metrics=["aov", "revenue", "orders"])[0]
    assert total["aov"] == pytest.approx(710 / 5)
    # the average of per-order revenue ratios would differ from the ratio of sums in general;
    # the ratio of sums must equal revenue / orders exactly
    assert total["aov"] == pytest.approx(total["revenue"] / total["orders"])


def test_derived_metric(star_store, model):
    total = rows(star_store, model, metrics=["asp"])[0]
    assert total["asp"] == pytest.approx(710 / 16)


def test_line_metric_by_product_dimension(star_store, model):
    r = by(rows(star_store, model, metrics=["revenue", "units"], dimensions=["category"]), "category")
    assert r["Lumber"]["revenue"] == 400.0
    assert r["Roofing"]["revenue"] == 190.0
    assert r["Tools"]["revenue"] == 120.0


def test_order_metric_by_product_dimension_is_refused(model):
    with pytest.raises(GrainError) as err:
        compile(model, MetricQuery(metrics=["orders"], dimensions=["category"]))
    msg = str(err.value)
    assert "one-to-many" in msg and "category" in msg


def test_mixed_grain_query_refused_for_child_dimension(model):
    with pytest.raises(GrainError):
        compile(model, MetricQuery(metrics=["revenue", "shipping"], dimensions=["category"]))


def test_child_filter_becomes_semijoin(star_store, model):
    q = MetricQuery(
        metrics=["orders", "shipping"], filters=[Filter(dimension="category", op="eq", values=["Lumber"])]
    )
    compiled = compile(model, q)
    assert "EXISTS" in compiled.sql
    assert any("semi-join" in w for w in compiled.warnings)
    res = star_store.execute_read(compiled.sql).to_records()[0]
    # orders containing at least one Lumber line: 101, 102, 104
    assert res == {"orders": 3, "shipping": 30.0}


def test_time_window_filters(star_store, model):
    r = rows(star_store, model, metrics=["revenue", "orders"], time=AUG)
    assert r == [{"revenue": 460.0, "orders": 3}]


def test_time_window_uses_default_time_dimension(star_store, model):
    w = AUG.model_copy(update={"dimension": None})
    r = rows(star_store, model, metrics=["revenue"], time=w)
    assert r == [{"revenue": 460.0}]


def test_time_grain_output(star_store, model):
    w = TimeWindow(dimension="order_date", start=dt.date(2026, 7, 1), end=dt.date(2026, 9, 1), grain="month")
    compiled = compile(model, MetricQuery(metrics=["revenue", "orders"], time=w))
    assert compiled.grain == ["order_date__month"]
    res = star_store.execute_read(compiled.sql).to_records()
    assert [(r["order_date__month"], r["revenue"], r["orders"]) for r in res] == [
        (dt.date(2026, 7, 1), 250.0, 2),
        (dt.date(2026, 8, 1), 460.0, 3),
    ]


def test_time_dimension_with_grain_in_dimensions(star_store, model):
    res = rows(star_store, model, metrics=["revenue"], dimensions=["order_date__quarter"])
    assert res == [{"order_date__quarter": dt.date(2026, 7, 1), "revenue": 710.0}]


def test_metric_level_filter(star_store, model):
    r = rows(star_store, model, metrics=["completed_revenue", "revenue"])
    assert r == [{"completed_revenue": 680.0, "revenue": 710.0}]


def test_query_filters_and_in_and_null(star_store, model):
    r = rows(
        star_store,
        model,
        metrics=["revenue"],
        filters=[Filter(dimension="region", op="in", values=["Dallas", "Austin"])],
    )
    assert r == [{"revenue": 580.0}]
    r = rows(
        star_store,
        model,
        metrics=["revenue"],
        filters=[Filter(dimension="region", op="neq", values=["Dallas"])],
    )
    assert r == [{"revenue": 180.0}]
    r = rows(
        star_store,
        model,
        metrics=["revenue"],
        filters=[Filter(dimension="region", op="contains", values=["ALL"])],
    )
    assert r == [{"revenue": 530.0}]
    r = rows(
        star_store,
        model,
        metrics=["revenue"],
        filters=[Filter(dimension="region", op="starts_with", values=["hou"])],
    )
    assert r == [{"revenue": 130.0}]


def test_filter_literals_are_escaped(star_store, model):
    q = MetricQuery(
        metrics=["revenue"],
        filters=[Filter(dimension="region", op="eq", values=["x'; DROP TABLE orders; --"])],
    )
    compiled = compile(model, q)
    res = star_store.execute_read(compiled.sql).to_records()
    assert res == [{"revenue": None}]
    assert star_store.has_table("orders")


def test_count_distinct_customers_across_regions_is_not_additive(star_store, model):
    total = rows(star_store, model, metrics=["active_customers"])[0]["active_customers"]
    seg = rows(star_store, model, metrics=["active_customers"], dimensions=["segment"])
    assert total == 3
    assert sum(r["active_customers"] for r in seg) == 3


def test_parent_metric_with_parent_dimension(star_store, model):
    r = by(rows(star_store, model, metrics=["customers"], dimensions=["segment"]), "segment")
    assert r["Retail"]["customers"] == 2
    assert r["Enterprise"]["customers"] == 1


def test_parent_metric_with_child_dimension_refused(model):
    with pytest.raises(GrainError):
        compile(model, MetricQuery(metrics=["customers"], dimensions=["status"]))


def test_unapproved_relationship_not_used(model):
    for r in model.relationships:
        if r.to_entity == "products":
            r.approved = False
    with pytest.raises(GrainError) as err:
        compile(model, MetricQuery(metrics=["revenue"], dimensions=["category"]))
    assert "not approved" in str(err.value) or "unapproved" in str(err.value)


def test_many_to_many_relationship_refused(model):
    model.entities.append(
        model.entities[0].model_copy(update={"name": "regions_targets", "table": "customers"})
    )
    model.dimensions.append(Dimension(name="target_region", entity="regions_targets", expr="region"))
    model.relationships.append(
        Relationship(
            from_entity="customers",
            from_col="region",
            to_entity="regions_targets",
            to_col="region",
            cardinality="many_to_many",
            approved=True,
        )
    )
    with pytest.raises(GrainError) as err:
        compile(model, MetricQuery(metrics=["customers"], dimensions=["target_region"]))
    assert "many-to-many" in str(err.value)


def test_unknown_metric_and_dimension(model):
    with pytest.raises(CompileError):
        compile(model, MetricQuery(metrics=["nope"]))
    with pytest.raises(CompileError):
        compile(model, MetricQuery(metrics=["revenue"], dimensions=["nope"]))
    with pytest.raises(CompileError):
        compile(model, MetricQuery(metrics=["revenue"], dimensions=["region__month"]))
    with pytest.raises(CompileError):
        compile(model, MetricQuery(metrics=[]))


def test_order_by_limit_and_determinism(star_store, model):
    q = MetricQuery(metrics=["revenue"], dimensions=["region"], order_by=["-revenue"], limit=2)
    c1, c2 = compile(model, q), compile(model, q)
    assert c1.sql == c2.sql
    res = star_store.execute_read(c1.sql).to_records()
    assert [r["region"] for r in res] == ["Dallas", "Houston"]
    with pytest.raises(CompileError):
        compile(model, MetricQuery(metrics=["revenue"], order_by=["nope"]))


def test_compiled_metadata(model):
    c = compile(model, MetricQuery(metrics=["aov"], dimensions=["region"], time=AUG))
    assert set(c.metric_versions) == {"aov", "revenue", "orders"}
    assert c.metric_versions["aov"].startswith("aov@v1:")
    assert [col.role for col in c.columns] == ["dimension", "metric"]
    assert any("order_date in [2026-08-01, 2026-09-01)" in f for f in c.filters_applied)
    assert c.joins and all(p.fanout_safe for p in c.joins)
    assert c.query is not None


def test_metric_version_changes_with_definition(model):
    before = compile(model, MetricQuery(metrics=["revenue"])).metric_versions["revenue"]
    model.metrics[0] = model.metrics[0].model_copy(update={"expr": "net_amount * 1.0"})
    after = compile(model, MetricQuery(metrics=["revenue"])).metric_versions["revenue"]
    assert before != after


def test_avg_metric_warns(model):
    c = compile(model, MetricQuery(metrics=["avg_line"], dimensions=["region"]))
    assert any("not additive" in w for w in c.warnings)


def test_other_dialect_transpiles(model):
    c = compile(model, MetricQuery(metrics=["revenue"], dimensions=["region"], limit=5), dialect="postgres")
    assert c.dialect == "postgres"
    assert "LIMIT 5" in c.sql


def test_nulls_in_dimension_kept(star_store, model):
    import pandas as pd

    star_store.write_table(
        "customers",
        pd.DataFrame(
            {
                "customer_id": [1, 2, 3],
                "customer_name": ["a", "b", "c"],
                "region": ["Dallas", None, "Austin"],
                "segment": ["Enterprise", "Retail", "Retail"],
            }
        ),
        if_exists="replace",
    )
    r = rows(star_store, model, metrics=["revenue", "shipping"], dimensions=["region"])
    assert sum(x["revenue"] for x in r) == 710.0
    assert sum(x["shipping"] for x in r) == 42.0
    null_row = next(x for x in r if x["region"] is None)
    assert null_row["revenue"] == 130.0 and null_row["shipping"] == 12.0


def test_orphan_rows_kept_with_left_join(star_store, model):
    import pandas as pd

    lines = star_store.execute_read("SELECT * FROM order_lines").to_pandas()
    extra = pd.DataFrame(
        {"line_id": [9], "order_id": [999], "product_id": ["P9"], "qty": [1], "net_amount": [5.0]}
    )
    star_store.write_table("order_lines", pd.concat([lines, extra]), if_exists="replace")
    total = rows(star_store, model, metrics=["revenue"])[0]["revenue"]
    by_region = rows(star_store, model, metrics=["revenue"], dimensions=["region"])
    assert total == 715.0
    assert sum(x["revenue"] for x in by_region) == 715.0


def test_expression_with_subquery_rejected(model):
    model.metrics.append(Metric(id="bad", name="Bad", entity="orders", expr="(SELECT 1)", agg="sum"))
    with pytest.raises(CompileError):
        compile(model, MetricQuery(metrics=["bad"]))


def test_circular_derived_metric(model):
    model.metrics.append(Metric(id="a1", name="A1", kind="derived", formula="b1 + 1"))
    model.metrics.append(Metric(id="b1", name="B1", kind="derived", formula="a1 + 1"))
    with pytest.raises(CompileError):
        compile(model, MetricQuery(metrics=["a1"]))


# ------------------------------------------------------------------ per-entity time windows


@pytest.fixture()
def dated_model(model, star_store):
    import pandas as pd
    from analystos_engine.semantic.models import Entity

    star_store.write_table(
        "returns",
        pd.DataFrame(
            {
                "return_id": [1, 2, 3],
                "order_id": [101, 102, 104],  # 101 ordered in July, returned in August
                "return_date": [dt.date(2026, 8, 2), dt.date(2026, 9, 5), dt.date(2026, 8, 20)],
                "amount": [15.0, 40.0, 10.0],
            }
        ),
    )
    star_store.write_table(
        "budgets",
        pd.DataFrame(
            {
                "budget_month": [dt.date(2026, 7, 1), dt.date(2026, 8, 1), dt.date(2026, 9, 1)],
                "amount": [300.0, 500.0, 400.0],
            }
        ),
    )
    model.entities.append(
        Entity(name="returns", table="returns", primary_key="return_id", default_time_dimension="return_date")
    )
    model.entities.append(
        Entity(
            name="budgets", table="budgets", primary_key="budget_month", default_time_dimension="budget_month"
        )
    )
    model.dimensions.append(Dimension(name="return_date", entity="returns", expr="return_date", type="time"))
    model.dimensions.append(
        Dimension(name="budget_month", entity="budgets", expr="budget_month", type="time")
    )
    model.relationships.append(
        Relationship(
            from_entity="returns", from_col="order_id", to_entity="orders", to_col="order_id", approved=True
        )
    )
    model.metrics.append(Metric(id="returned", name="Returned", entity="returns", expr="amount", agg="sum"))
    model.metrics.append(
        Metric(
            id="return_rate", name="Return Rate", kind="ratio", numerator="returned", denominator="revenue"
        )
    )
    model.metrics.append(Metric(id="budget", name="Budget", entity="budgets", expr="amount", agg="sum"))
    model.metrics.append(
        Metric(id="budget_gap", name="Budget Gap", kind="derived", formula="revenue - budget")
    )
    return model


def test_time_window_uses_each_metrics_own_time_dimension(star_store, dated_model):
    w = AUG.model_copy(update={"dimension": None})
    c = compile(dated_model, MetricQuery(metrics=["return_rate", "returned", "revenue"], time=w))
    assert "EXISTS" not in c.sql
    assert any("return_date in [2026-08-01" in f for f in c.filters_applied)
    assert any("order_date in [2026-08-01" in f for f in c.filters_applied)
    r = star_store.execute_read(c.sql).to_records()[0]
    assert r["returned"] == 25.0  # returns dated in August, whatever their order date
    assert r["revenue"] == 460.0
    assert r["return_rate"] == pytest.approx(25 / 460)


def test_explicit_time_dimension_reachable_through_parent(star_store, dated_model):
    c = compile(dated_model, MetricQuery(metrics=["returned"], time=AUG))
    r = star_store.execute_read(c.sql).to_records()[0]
    # returns of orders placed in August: order 102 (40) and 104 (10)
    assert r["returned"] == 50.0
    assert "EXISTS" not in c.sql


def test_explicit_dimension_unreachable_falls_back_with_warning(star_store, dated_model):
    c = compile(dated_model, MetricQuery(metrics=["revenue", "budget", "budget_gap"], time=AUG))
    assert "EXISTS" not in c.sql
    assert any("budget_month" in w for w in c.warnings)
    r = star_store.execute_read(c.sql).to_records()[0]
    assert r == {"revenue": 460.0, "budget": 500.0, "budget_gap": -40.0}


def test_conformed_period_grouping(star_store, dated_model):
    w = TimeWindow(start=dt.date(2026, 7, 1), end=dt.date(2026, 10, 1), grain="month")
    c = compile(dated_model, MetricQuery(metrics=["revenue", "budget"], time=w))
    assert c.grain == ["period__month"]
    assert c.columns[0].source == "period"
    res = star_store.execute_read(c.sql).to_records()
    assert [(r["period__month"], r["revenue"], r["budget"]) for r in res] == [
        (dt.date(2026, 7, 1), 250.0, 300.0),
        (dt.date(2026, 8, 1), 460.0, 500.0),
        (dt.date(2026, 9, 1), 0.0, 400.0),
    ]
    c2 = compile(dated_model, MetricQuery(metrics=["revenue", "budget"], dimensions=["period__quarter"]))
    res2 = star_store.execute_read(c2.sql).to_records()
    assert res2 == [{"period__quarter": dt.date(2026, 7, 1), "revenue": 710.0, "budget": 1200.0}]
    with pytest.raises(CompileError):
        compile(dated_model, MetricQuery(metrics=["revenue"], dimensions=["period"]))


def test_no_time_dimension_anywhere(dated_model):
    w = AUG.model_copy(update={"dimension": None})
    with pytest.raises(CompileError, match="no time dimension"):
        compile(dated_model, MetricQuery(metrics=["customers"], time=w))
    with pytest.raises(GrainError):
        compile(dated_model, MetricQuery(metrics=["customers"], time=AUG))


def test_explicit_grain_dimension_must_be_reachable(dated_model):
    with pytest.raises(GrainError):
        compile(dated_model, MetricQuery(metrics=["budget"], dimensions=["order_date__month"]))


# ------------------------------------------------------------------ R-07: non-unique "one" side


def _with_duplicate_customer(star_store, *, conflicting: bool):
    import pandas as pd

    customers = star_store.execute_read("SELECT * FROM customers").to_records()
    dup = dict(customers[0])  # customer 1, Enterprise, Dallas
    if conflicting:
        dup["segment"] = "Retail"
    star_store.write_table("customers", pd.DataFrame([*customers, dup]), if_exists="replace")


def test_duplicate_parent_rows_do_not_fan_out(star_store, model):
    _with_duplicate_customer(star_store, conflicting=False)
    compiled, res = run_metric_query(
        star_store, model, MetricQuery(metrics=["revenue"], dimensions=["segment"])
    )
    seg = {r["segment"]: r["revenue"] for r in res.to_records()}
    total = rows(star_store, model, metrics=["revenue"])[0]["revenue"]
    assert sum(seg.values()) == pytest.approx(total) == 710.0
    assert seg["Enterprise"] == 530.0
    assert any("not unique in customers" in w and "identical" in w for w in compiled.warnings)


def test_conflicting_duplicate_parent_rows_counted_once_with_warning(star_store, model):
    _with_duplicate_customer(star_store, conflicting=True)
    compiled, res = run_metric_query(
        star_store, model, MetricQuery(metrics=["revenue"], dimensions=["segment"])
    )
    seg = {r["segment"]: r["revenue"] for r in res.to_records()}
    assert sum(seg.values()) == pytest.approx(710.0)
    assert any("disagree" in w for w in compiled.warnings)


def test_unique_parent_keys_give_no_warning(star_store, model):
    compiled, _ = run_metric_query(
        star_store, model, MetricQuery(metrics=["revenue"], dimensions=["segment"])
    )
    assert not any("not unique" in w for w in compiled.warnings)


# ------------------------------------------------------------------ R-27: cohort maturity


def test_immature_cohort_metric_is_flagged(model):
    m = model.model_copy(deep=True)
    m.metrics.append(
        Metric(id="cohort_orders", name="Cohort orders", entity="orders", agg="count", maturity_days=90)
    )
    m.metrics.append(
        Metric(
            id="cohort_ratio",
            name="Cohort ratio",
            kind="ratio",
            numerator="cohort_orders",
            denominator="orders",
        )
    )
    q = MetricQuery(metrics=["cohort_ratio", "revenue"], time=AUG, as_of=dt.date(2026, 9, 30))
    c = compile(m, q)
    assert c.immature_metrics == ["cohort_ratio"]
    assert any("immature" in w for w in c.warnings)
    mature = compile(m, q.model_copy(update={"as_of": dt.date(2026, 12, 31)}))
    assert mature.immature_metrics == []
    assert compile(m, q.model_copy(update={"as_of": None})).immature_metrics == []
    assert (
        Metric(id="x", name="X", entity="orders", agg="count").definition_hash()
        == Metric(id="x", name="X", entity="orders", agg="count", maturity_days=None).definition_hash()
    )
