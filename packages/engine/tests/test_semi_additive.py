"""Semi-additive (balance) metrics are never summed across dates (review finding R-02)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from analystos_engine.semantic.compiler import MetricQuery, compile, run_metric_query
from analystos_engine.semantic.models import Dimension, Entity, Filter, Metric, Relationship, SemanticModel
from analystos_engine.store import WorkspaceStore
from analystos_engine.types import TimeWindow

D = dt.date


@pytest.fixture()
def inv_store() -> WorkspaceStore:
    store = WorkspaceStore.in_memory()
    # Month-end snapshots for two products at two branches.
    rows = []
    values = {
        # (date, product): value per branch A, B
        (D(2026, 4, 30), "P1"): (100.0, 50.0),
        (D(2026, 4, 30), "P2"): (10.0, 5.0),
        (D(2026, 5, 31), "P1"): (120.0, 60.0),
        (D(2026, 5, 31), "P2"): (20.0, 0.0),
        (D(2026, 6, 30), "P1"): (130.0, 70.0),
        (D(2026, 6, 30), "P2"): (30.0, 10.0),
        (D(2026, 7, 31), "P1"): (90.0, 40.0),
        (D(2026, 7, 31), "P2"): (35.0, 15.0),
    }
    for (day, product), (a, b) in values.items():
        rows.append({"snapshot_date": day, "product_id": product, "branch": "A", "value": a, "units": 1})
        rows.append({"snapshot_date": day, "product_id": product, "branch": "B", "value": b, "units": 2})
    store.write_table("inventory", pd.DataFrame(rows))
    store.write_table(
        "products", pd.DataFrame({"product_id": ["P1", "P2"], "category": ["Lumber", "Roofing"]})
    )
    yield store
    store.close()


def inv_model(**metric_kw) -> SemanticModel:
    return SemanticModel(
        entities=[
            Entity(
                name="inventory",
                table="inventory",
                primary_key=["snapshot_date", "product_id", "branch"],
                default_time_dimension="snapshot_date",
            ),
            Entity(name="products", table="products", primary_key="product_id"),
        ],
        dimensions=[
            Dimension(name="snapshot_date", entity="inventory", expr="snapshot_date", type="time"),
            Dimension(name="branch", entity="inventory", expr="branch"),
            Dimension(name="category", entity="products", expr="category"),
        ],
        metrics=[
            Metric(
                id="inventory_value",
                name="Inventory Value",
                entity="inventory",
                expr="value",
                agg="sum",
                **metric_kw,
            ),
            Metric(
                id="stock_rows", name="Stock rows", entity="inventory", agg="count", time_aggregation="last"
            ),
            Metric(id="receipts", name="Flow", entity="inventory", expr="units", agg="sum"),
            Metric(
                id="value_per_unit",
                name="Value per unit",
                kind="ratio",
                numerator="inventory_value",
                denominator="stock_units",
            ),
            Metric(
                id="stock_units",
                name="Stock units",
                entity="inventory",
                expr="units",
                agg="sum",
                time_aggregation="last",
            ),
        ],
        relationships=[
            Relationship(
                from_entity="inventory",
                from_col="product_id",
                to_entity="products",
                to_col="product_id",
                approved=True,
            )
        ],
    )


Q2 = TimeWindow(start=D(2026, 4, 1), end=D(2026, 7, 1))


def run(store, model, **kw):
    compiled, res = run_metric_query(store, model, MetricQuery(**kw))
    return compiled, res.to_records()


def test_default_sum_multiplies_the_balance(inv_store):
    # The bug R-02 describes: summing three month-end snapshots.
    _, r = run(inv_store, inv_model(), metrics=["inventory_value"], time=Q2)
    assert r[0]["inventory_value"] == pytest.approx(165 + 200 + 240)


def test_last_takes_the_latest_snapshot_in_the_window(inv_store):
    c, r = run(inv_store, inv_model(time_aggregation="last"), metrics=["inventory_value"], time=Q2)
    assert r[0]["inventory_value"] == pytest.approx(130 + 70 + 30 + 10)
    assert any("last snapshot_date" in f for f in c.filters_applied)


def test_first_takes_the_earliest_snapshot(inv_store):
    _, r = run(inv_store, inv_model(time_aggregation="first"), metrics=["inventory_value"], time=Q2)
    assert r[0]["inventory_value"] == pytest.approx(165)


def test_avg_averages_the_per_date_totals(inv_store):
    _, r = run(inv_store, inv_model(time_aggregation="avg"), metrics=["inventory_value"], time=Q2)
    assert r[0]["inventory_value"] == pytest.approx((165 + 200 + 240) / 3)


def test_avg_counts_dates_where_a_segment_is_absent_as_zero(inv_store):
    # Branch B / P2 has a zero row in May; averaging per segment still divides by 3 dates.
    _, r = run(
        inv_store,
        inv_model(time_aggregation="avg"),
        metrics=["inventory_value"],
        dimensions=["category"],
        time=Q2,
    )
    by = {x["category"]: x["inventory_value"] for x in r}
    assert by["Roofing"] == pytest.approx((15 + 20 + 40) / 3)
    assert by["Lumber"] + by["Roofing"] == pytest.approx((165 + 200 + 240) / 3)


def test_last_per_time_bucket(inv_store):
    _, r = run(
        inv_store,
        inv_model(time_aggregation="last"),
        metrics=["inventory_value"],
        dimensions=["snapshot_date__quarter"],
        time=TimeWindow(start=D(2026, 4, 1), end=D(2026, 10, 1)),
    )
    assert [(x["snapshot_date__quarter"], x["inventory_value"]) for x in r] == [
        (D(2026, 4, 1), pytest.approx(240.0)),
        (D(2026, 7, 1), pytest.approx(180.0)),
    ]


def test_breakdown_segments_add_up_to_the_total(inv_store):
    model = inv_model(time_aggregation="last")
    _, total = run(inv_store, model, metrics=["inventory_value"], time=Q2)
    for dim in ("category", "branch"):
        _, parts = run(inv_store, model, metrics=["inventory_value"], dimensions=[dim], time=Q2)
        assert sum(p["inventory_value"] for p in parts) == pytest.approx(total[0]["inventory_value"])


def test_query_filter_does_not_move_the_edge_date(inv_store):
    # Every segment is read at the window's last date; a filter never falls back to an older balance.
    kept = inv_store.execute_read(
        "SELECT * FROM inventory WHERE NOT (snapshot_date = DATE '2026-06-30' AND branch = 'B')"
    ).to_records()
    inv_store.write_table("inventory", pd.DataFrame(kept), if_exists="replace")
    _, r = run(
        inv_store,
        inv_model(time_aggregation="last"),
        metrics=["inventory_value"],
        filters=[Filter(dimension="branch", op="eq", values=["B"])],
        time=Q2,
    )
    assert r == [] or r[0]["inventory_value"] in (None, 0)


def test_no_window_uses_latest_date_in_data(inv_store):
    _, r = run(inv_store, inv_model(time_aggregation="last"), metrics=["inventory_value"])
    assert r[0]["inventory_value"] == pytest.approx(180.0)


def test_ratio_inherits_semi_additive_atoms_and_mixes_with_flows(inv_store):
    model = inv_model(time_aggregation="last")
    _, r = run(inv_store, model, metrics=["value_per_unit", "receipts", "stock_rows"], time=Q2)
    assert r[0]["value_per_unit"] == pytest.approx(240.0 / (1 + 2 + 1 + 2))
    assert r[0]["receipts"] == 3 * 6  # flows still sum over every snapshot row
    assert r[0]["stock_rows"] == 4


def test_definition_hash_unchanged_for_default_sum():
    plain = Metric(id="m", name="M", entity="e", expr="x", agg="sum")
    explicit = Metric(id="m", name="M", entity="e", expr="x", agg="sum", time_aggregation="sum")
    last = Metric(id="m", name="M", entity="e", expr="x", agg="sum", time_aggregation="last")
    assert plain.definition_hash() == explicit.definition_hash()
    assert "time_aggregation" not in plain.model_dump(mode="json", exclude_defaults=True)
    assert last.definition_hash() != plain.definition_hash()


def test_time_aggregation_validation():
    with pytest.raises(ValueError, match="simple metrics"):
        Metric(id="r", name="R", kind="ratio", numerator="a", denominator="b", time_aggregation="last")
    with pytest.raises(ValueError, match="avg"):
        Metric(id="m", name="M", entity="e", expr="x", agg="max", time_aggregation="avg")


def test_yaml_round_trip_keeps_time_aggregation():
    model = inv_model(time_aggregation="last")
    again = SemanticModel.from_yaml(model.to_yaml())
    assert again.get_metric("inventory_value").time_aggregation == "last"
    assert again.content_hash() == model.content_hash()


def test_compiled_sql_is_read_only_and_replayable(inv_store):
    c = compile(inv_model(time_aggregation="last"), MetricQuery(metrics=["inventory_value"], time=Q2))
    assert inv_store.execute_read(c.sql).rows[0][0] == pytest.approx(240.0)
