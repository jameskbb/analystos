from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from analystos_engine.profiling import profile_table
from analystos_engine.store import WorkspaceStore

TODAY = dt.date(2026, 9, 18)


@pytest.fixture()
def messy_store():
    s = WorkspaceStore.in_memory()
    n = 40
    df = pd.DataFrame(
        {
            "customer_id": list(range(1, n)) + [5],  # duplicate id 5
            "region": ["Dallas", "dallas", " Houston", "Austin"] * 10,
            "qty": [3 + i % 4 for i in range(n - 2)] + [-2, 500],
            "unit_price": [10.0 + (i % 5) for i in range(n)],
            "discount_pct": [0.1] * (n - 1) + [1.7],
            "order_date": [dt.date(2026, 8, 1) + dt.timedelta(days=i % 20) for i in range(n - 1)]
            + [dt.date(2027, 1, 1)],
            "ship_date_text": ["2026-08-01"] * (n - 2) + ["2026-13-01", "soon"],
            "amount_text": ["12.5"] * (n - 3) + ["n/a", "tbd", "7"],
            "source": ["erp"] * n,
            "notes": [None] * n,
            "email": [f"user{i}@example.com" for i in range(n)],
            "is_active": ["Y", "N"] * (n // 2),
        }
    )
    s.write_table("customers", df)
    s.write_table("dupes", pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]}))
    s.write_table("empty_t", pd.DataFrame({"a": pd.Series([], dtype="int64")}))
    yield s
    s.close()


def issues_by(profile, code):
    return [i for i in profile.issues if i.code == code]


def test_column_stats(messy_store):
    p = profile_table(messy_store, "customers", today=TODAY)
    assert p.row_count == 40 and p.column_count == 12
    price = p.column("unit_price")
    assert price.inferred_type == "float"
    assert price.min == 10.0 and price.max == 14.0
    assert price.mean == pytest.approx(12.0)
    assert set(price.quantiles) == {"p05", "p25", "p50", "p75", "p95"}
    assert "currency" in price.semantic_roles
    region = p.column("region")
    assert region.distinct_count == 4
    assert region.top_values[0].count == 10
    assert "geo" in region.semantic_roles and "category" in region.semantic_roles
    assert p.column("customer_id").cardinality == "high"
    assert "identifier" in p.column("customer_id").semantic_roles
    assert "percentage" in p.column("discount_pct").semantic_roles
    assert "count" in p.column("qty").semantic_roles
    assert "date" in p.column("order_date").semantic_roles
    assert "email" in p.column("email").semantic_roles
    assert "boolean" in p.column("is_active").semantic_roles
    assert p.column("source").cardinality == "constant"
    assert p.version is not None and p.version.row_count == 40
    assert p.elapsed_ms > 0


def test_issues_detected(messy_store):
    p = profile_table(messy_store, "customers", today=TODAY)
    codes = {i.code for i in p.issues}
    for expected in (
        "duplicate_ids",
        "whitespace_padding",
        "inconsistent_casing",
        "impossible_values",
        "future_dates",
        "malformed_dates",
        "mixed_types",
        "constant_column",
        "empty_column",
        "outliers",
    ):
        assert expected in codes, expected
    dup = issues_by(p, "duplicate_ids")[0]
    assert dup.column == "customer_id" and dup.count == 1 and dup.sample_values == [5]
    neg = [i for i in issues_by(p, "impossible_values") if i.column == "qty"][0]
    assert neg.sample_values == [-2]
    pct = [i for i in issues_by(p, "impossible_values") if i.column == "discount_pct"][0]
    assert pct.sample_values == [1.7]
    mal = issues_by(p, "malformed_dates")[0]
    assert set(mal.sample_values) == {"2026-13-01", "soon"}
    mixed = issues_by(p, "mixed_types")[0]
    assert mixed.column == "amount_text" and set(mixed.sample_values) == {"n/a", "tbd"}
    casing = issues_by(p, "inconsistent_casing")[0]
    assert casing.column == "region"
    out = [i for i in issues_by(p, "outliers") if i.column == "qty"][0]
    assert 500 in out.sample_values
    fut = issues_by(p, "future_dates")[0]
    assert fut.count == 1
    for issue in p.issues:
        assert issue.message and issue.severity in ("info", "warning", "error")


def test_duplicate_rows(messy_store):
    p = profile_table(messy_store, "dupes", today=TODAY)
    assert p.duplicate_row_count == 1
    assert p.issues[0].code == "duplicate_rows"
    assert p.issues[0].sample_values[0]["_copies"] == 2


def test_empty_table(messy_store):
    p = profile_table(messy_store, "empty_t", today=TODAY)
    assert p.row_count == 0 and p.issues == []


def test_sampling(messy_store):
    p = profile_table(messy_store, "customers", sample_rows=10, today=TODAY)
    assert p.sampled and p.sample_size == 10
    assert p.row_count == 40
    p2 = profile_table(messy_store, "customers", sample_rows=10, today=TODAY)
    assert p.column("unit_price").mean == p2.column("unit_price").mean  # reproducible sample


def test_star_schema_foreign_keys(star_store):
    p = profile_table(star_store, "orders", today=TODAY)
    assert "identifier" in p.column("order_id").semantic_roles
    assert "foreign_key" in p.column("customer_id").semantic_roles
    assert not issues_by(p, "duplicate_ids")
