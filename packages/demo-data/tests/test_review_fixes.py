"""Demo-side regression tests for review findings R-02 and R-27."""

from __future__ import annotations

import datetime as dt

import pytest
from analystos_demo import REFERENCE_DATE, load_demo
from analystos_demo.stories import END
from analystos_engine.semantic.compiler import MetricQuery, run_metric_query
from analystos_engine.store import WorkspaceStore
from analystos_engine.types import TimeWindow
from conftest import demo_cache_dir


@pytest.fixture(scope="module")
def loaded(demo):
    store = WorkspaceStore.in_memory()
    result = load_demo(store, cache_dir=demo_cache_dir())
    yield store, result.semantic_model
    store.close()


def test_reference_date_matches_the_data_end():
    assert REFERENCE_DATE.isoformat() == END


def test_no_row_is_dated_after_the_reference_date(loaded):
    store, model = loaded
    # Plans (budgets, targets, forecasts) cover the rest of the fiscal year by design; actuals
    # must stop at the reference date.
    plan_tables = {
        model.get_entity(m.entity).table
        for m in model.metrics
        if m.entity and {"plan", "budget", "forecast", "target"} & {t.lower() for t in m.tags}
    } | {"targets", "budgets_category"}
    late = []
    for t in store.list_tables(include_row_counts=False):
        if t.name in plan_tables:
            continue
        for c in t.columns:
            if c.type.upper() in ("DATE", "TIMESTAMP"):
                q = f'SELECT max("{c.name}") FROM "{t.name}"'
                v = store.execute_read(q).rows[0][0]
                if v is not None and (v.date() if isinstance(v, dt.datetime) else v) > REFERENCE_DATE:
                    late.append(f"{t.name}.{c.name}={v}")
    assert late == [], late


def test_inventory_value_is_read_at_the_snapshot_not_summed(loaded):
    store, model = loaded
    q2_26 = TimeWindow(start=dt.date(2026, 4, 1), end=dt.date(2026, 7, 1))
    q2_25 = TimeWindow(start=dt.date(2025, 4, 1), end=dt.date(2025, 7, 1))
    _, cur = run_metric_query(store, model, MetricQuery(metrics=["inventory_value"], time=q2_26))
    _, base = run_metric_query(store, model, MetricQuery(metrics=["inventory_value"], time=q2_25))
    assert cur.rows[0][0] == pytest.approx(17_297_065.93, abs=1)
    assert base.rows[0][0] == pytest.approx(13_190_987.31, abs=1)
    assert cur.rows[0][0] / base.rows[0][0] - 1 == pytest.approx(0.3113, abs=5e-4)


def test_recent_conversion_cohorts_are_flagged_immature(loaded):
    _, model = loaded
    from analystos_engine.semantic.compiler import compile as compile_query

    aug = TimeWindow(start=dt.date(2026, 8, 1), end=dt.date(2026, 9, 1))
    q2 = TimeWindow(start=dt.date(2026, 4, 1), end=dt.date(2026, 7, 1))
    c = compile_query(model, MetricQuery(metrics=["conversion_rate"], time=aug, as_of=REFERENCE_DATE))
    assert c.immature_metrics == ["conversion_rate"]
    c2 = compile_query(model, MetricQuery(metrics=["conversion_rate"], time=q2, as_of=REFERENCE_DATE))
    assert c2.immature_metrics == []


def test_standard_unit_cost_measures_product_costs(loaded):
    store, model = loaded
    q2_26 = TimeWindow(start=dt.date(2026, 4, 1), end=dt.date(2026, 7, 1))
    q2_25 = TimeWindow(start=dt.date(2025, 4, 1), end=dt.date(2025, 7, 1))
    _, cur = run_metric_query(store, model, MetricQuery(metrics=["standard_unit_cost"], time=q2_26))
    _, base = run_metric_query(store, model, MetricQuery(metrics=["standard_unit_cost"], time=q2_25))
    assert cur.rows[0][0] > base.rows[0][0]
