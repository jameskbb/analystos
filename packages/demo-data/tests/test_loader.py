"""load_demo() puts every table into a WorkspaceStore and returns what the API persists."""

from __future__ import annotations

import datetime as dt

import pytest
from analystos_demo import TABLES, load_demo


@pytest.fixture(scope="module")
def loaded(demo):
    from analystos_engine.store import WorkspaceStore

    store = WorkspaceStore.in_memory()
    result = load_demo(store, cache_dir=demo.data_dir)
    yield store, result
    store.close()


def test_all_tables_loaded_with_manifest_row_counts(loaded, demo):
    store, result = loaded
    assert [t.table for t in result.tables] == list(TABLES)
    for t in result.tables:
        assert store.row_count(t.table) == t.rows == demo.manifest.file(t.table).rows
    assert result.workspace_name == "Summit Supply Co."
    assert result.content_hash == demo.manifest.content_hash


def test_raw_mess_survives_loading(loaded):
    store, _ = loaded
    assert store.scalar("SELECT count(*) FROM returns WHERE order_id <> trim(order_id)") > 100
    assert store.scalar("SELECT typeof(account_opened) FROM customers LIMIT 1") == "VARCHAR"
    assert store.scalar("SELECT typeof(order_date) FROM orders LIMIT 1") == "DATE"
    assert store.scalar("SELECT typeof(budget_month) FROM budgets_branch LIMIT 1") == "DATE"
    assert store.scalar("SELECT count(*) FROM budgets_branch") == 14 * 24


def test_compiled_revenue_matches_answer_key(loaded):
    from analystos_engine.semantic.compiler import MetricQuery, run_metric_query
    from analystos_engine.types import TimeWindow

    store, result = loaded
    key = result.scenarios["stories"]["revenue_decline_aug_2026"]["headline"]
    _, res = run_metric_query(
        store,
        result.semantic_model,
        MetricQuery(metrics=["revenue"], time=TimeWindow(start=dt.date(2026, 8, 1), end=dt.date(2026, 9, 1))),
    )
    assert res.rows[0][0] == pytest.approx(key["current"], abs=0.01)
