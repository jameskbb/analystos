"""Advanced analysis endpoints (review R-05): forecast with backtests, anomalies (+ investigate), segmentation,
statistical tests, correlation and regression. Each persists an artifact with SQL, versions and lineage."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import pandas as pd
import pytest
from conftest import ApiClient, load_tables

MODEL_YAML = """
name: Analysis
entities:
  - {name: sales, table: sales, primary_key: sale_id, grain_description: one row per sale}
  - {name: stores, table: stores, primary_key: store_id, grain_description: one row per store}
dimensions:
  - {name: sale_date, entity: sales, expr: sale_date, type: time}
  - {name: product, entity: sales, expr: product, type: categorical}
  - {name: status, entity: sales, expr: status, type: categorical}
  - {name: region, entity: stores, expr: region, type: categorical}
relationships:
  - {from_entity: sales, from_col: store_id, to_entity: stores, to_col: store_id, cardinality: many_to_one,
     approved: true}
metrics:
  - {id: revenue, name: Revenue, kind: simple, entity: sales, expr: amount, agg: sum, format: currency,
     default_time_dimension: sale_date, filters: [{dimension: status, op: neq, values: [cancelled]}]}
  - {id: cost, name: Cost, kind: simple, entity: sales, expr: cost, agg: sum, format: currency,
     default_time_dimension: sale_date}
  - {id: gross_margin, name: Gross Margin, kind: derived, formula: revenue - cost, format: currency,
     default_time_dimension: sale_date}
  - {id: margin_pct, name: Margin %, kind: ratio, numerator: gross_margin, denominator: revenue, format: percent,
     default_time_dimension: sale_date}
"""

SPIKE = dt.date(2026, 6, 15)


def _data() -> dict[str, pd.DataFrame]:
    stores = pd.DataFrame({"store_id": [1, 2, 3], "region": ["North", "North", "South"]})
    rows = []
    sid = 0
    day = dt.date(2024, 1, 1)
    products = ["alpha", "beta", "gamma", "delta"]
    while day < dt.date(2026, 9, 1):
        season = 1 + 0.3 * math.sin(2 * math.pi * (day.month - 1) / 12)
        trend = 1 + (day - dt.date(2024, 1, 1)).days / 2000
        for _ in range(3):
            sid += 1
            product = products[(sid + day.month) % 4]
            growth = 1.5 if product == "alpha" and day.year == 2026 else 1.0
            amount = round(100 * season * trend * growth * (1 + 0.1 * ((sid * 7919) % 11 - 5) / 5), 2)
            if day == SPIKE:
                amount *= 12
            rows.append(
                {
                    "sale_id": sid,
                    "store_id": 1 + (sid % 3),
                    "sale_date": day,
                    "product": product,
                    "customer_id": f"c{(sid * 31) % 40:02d}",
                    "amount": amount,
                    "cost": round(amount * (0.55 if product in ("alpha", "beta") else 0.7), 2),
                    "status": "cancelled" if sid % 50 == 0 else "complete",
                    "units": 1 + (sid % 4),
                    "converted": sid % 3 == 0,
                }
            )
        day += dt.timedelta(days=1)
    return {"sales": pd.DataFrame(rows), "stores": stores}


@pytest.fixture(scope="module")
def data() -> dict[str, pd.DataFrame]:
    return _data()


@pytest.fixture
def ws(local_client: ApiClient, app: Any, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    w = local_client.post("/api/v1/workspaces", json={"name": "Analysis"}).json()
    load_tables(app, w["id"], data)
    r = local_client.put(
        f"/api/v1/workspaces/{w['id']}/semantic-models/current/yaml", json={"yaml": MODEL_YAML}
    )
    assert r.status_code == 200, r.text
    return {**w, "base": f"/api/v1/workspaces/{w['id']}"}


def _check_artifact(c: ApiClient, base: str, out: dict[str, Any], *, metric: str | None = None) -> None:
    """Every analysis is a stored artifact with SQL, versions, data and a lineage chain."""
    art = c.get(f"{base}/artifacts/{out['id']}").json()
    assert art["origin"] == "analysis" and art["kind"] == out["kind"]
    assert art["sql"] and art["sql"] == out["sql"]
    assert art["data"] == out["result"]
    assert art["dataset_versions"] and art["dataset_versions"][0]["table"] == "sales"
    assert c.get(f"{base}/analysis/{out['id']}").json()["summary"] == out["summary"]
    lin = c.get(f"{base}/artifacts/{out['id']}/lineage").json()
    kinds = {n["kind"] for n in lin["nodes"]}
    assert "dataset" in kinds
    if metric:
        assert "metric" in kinds and any(n["ref_id"] == metric for n in lin["nodes"])
        assert out["metric_versions"][metric].startswith(f"{metric}@v1:")


def test_forecast_with_backtests_and_model_comparison(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    r = c.post(
        f"{base}/analysis/forecast",
        json={
            "metric_id": "revenue",
            "start": "2024-01-01",
            "end": "2026-09-01",
            "grain": "month",
            "horizon": 3,
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "forecast" and out["label"] == "Model estimate" and out["exploratory"] is False
    res = out["result"]
    assert res["horizon"] == 3 and res["frequency"] == "month" and len(res["history"]) == 32
    names = {m["name"] for m in res["models"]}
    assert names == {"naive", "seasonal_naive", "ets", "arima"}
    fitted = [m for m in res["models"] if not m["error"]]
    assert all(m["backtest"] and m["backtest"]["mae"] is not None for m in fitted)
    best = min(fitted, key=lambda m: m["backtest"]["mae"])
    assert res["best_model"] == best["name"]
    assert out["table"]["row_count"] == 3 and out["table"]["columns"][0]["name"] == "period"
    assert any(a["name"] == "interval calibration" for a in out["assumptions"])
    # The revenue definition's filter is visible, not hidden.
    assert any(ch["kind"] == "metric" and "cancelled" in ch["value"] for ch in out["filter_context"])
    assert "status" in out["sql"]
    _check_artifact(c, base, out, metric="revenue")
    # A model subset and a short series.
    r = c.post(
        f"{base}/analysis/forecast",
        json={
            "metric_id": "revenue",
            "start": "2026-06-01",
            "end": "2026-09-01",
            "grain": "month",
            "horizon": 2,
            "models": ["ets"],
        },
    )
    assert r.status_code == 422 and r.json()["code"] == "analysis_failed"


def test_forecast_flags_incomplete_last_period(local_client: ApiClient, ws: dict[str, Any]) -> None:
    r = local_client.post(
        f"{ws['base']}/analysis/forecast",
        json={
            "metric_id": "revenue",
            "start": "2024-01-01",
            "end": "2026-08-20",
            "grain": "month",
            "horizon": 1,
            "models": ["naive"],
        },
    )
    assert r.status_code == 200, r.text
    assert any("incomplete" in n for n in r.json()["notes"])


def test_anomalies_sensitivity_and_investigate(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    body = {"metric_id": "revenue", "start": "2026-04-01", "end": "2026-08-01", "grain": "day"}
    high = c.post(f"{base}/analysis/anomalies", json={**body, "sensitivity": "high"}).json()
    low = c.post(f"{base}/analysis/anomalies", json={**body, "sensitivity": "low"}).json()
    assert high["kind"] == "anomalies" and high["label"] == "Descriptive"
    flagged = {p["timestamp"] for p in high["result"]["anomalies"]}
    assert SPIKE.isoformat() in flagged
    assert SPIKE.isoformat() in {p["timestamp"] for p in low["result"]["anomalies"]}
    assert len(low["result"]["anomalies"]) <= len(high["result"]["anomalies"])
    assert high["result"]["threshold"] < low["result"]["threshold"]
    assert high["table"]["row_count"] == 122
    _check_artifact(c, base, high, metric="revenue")
    listed = c.get(f"{base}/analysis?kind=anomalies").json()
    assert {a["id"] for a in listed} == {high["id"], low["id"]}
    # Launch an investigation of the anomalous day (spec §40).
    r = c.post(
        f"{base}/analysis/anomalies/investigate", json={"metric_id": "revenue", "date": SPIKE.isoformat()}
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["status"] == "completed" and inv["tree"]["nodes"]
    assert inv["interpretation"]["intent"] == "anomaly"
    root = next(n for n in inv["tree"]["nodes"] if n["id"] == inv["tree"]["root_id"])
    assert root["current"] > root["baseline"] * 5
    r = c.post(
        f"{base}/analysis/anomalies/investigate",
        json={"metric_id": "revenue", "date": "2026-06-01", "grain": "month"},
    )
    assert r.status_code == 201 and r.json()["status"] == "completed"
    assert "June 2026" in r.json()["question"]


def test_segments_rfm_quadrants_and_kmeans(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    r = c.post(
        f"{base}/analysis/segments",
        json={
            "method": "rfm",
            "entity": "sales",
            "customer_col": "customer_id",
            "date_col": "sale_date",
            "amount_col": "amount",
            "order_col": "sale_id",
            "as_of": "2026-08-31",
        },
    )
    assert r.status_code == 200, r.text
    rfm = r.json()
    assert rfm["label"] == "Descriptive" and rfm["method"] == "rfm"
    assert len(rfm["result"]["customers"]) == 40
    assert abs(sum(s["share"] for s in rfm["result"]["segments"]) - 1) < 1e-9
    assert "2026-08-31" in rfm["sql"]
    _check_artifact(c, base, rfm)

    r = c.post(
        f"{base}/analysis/segments",
        json={
            "method": "product_quadrants",
            "dimension": "product",
            "growth_metric_id": "revenue",
            "profitability_metric_id": "margin_pct",
            "current": {"start": "2026-01-01", "end": "2026-07-01"},
            "baseline": {"start": "2025-01-01", "end": "2025-07-01"},
        },
    )
    assert r.status_code == 200, r.text
    quad = r.json()
    rows = {q["item"]: q for q in quad["result"]["rows"]}
    assert set(rows) == {"alpha", "beta", "gamma", "delta"}
    assert rows["alpha"]["growth"] > rows["gamma"]["growth"]
    assert rows["alpha"]["quadrant"].startswith("Stars")
    assert set(quad["metric_versions"]) >= {"revenue", "margin_pct"}
    _check_artifact(c, base, quad, metric="margin_pct")

    r = c.post(
        f"{base}/analysis/segments",
        json={
            "method": "kmeans",
            "features": ["revenue", "cost"],
            "id_column": "product",
            "k": 2,
            "source": {"metric_query": {"metrics": ["revenue", "cost"], "dimensions": ["product"]}},
        },
    )
    assert r.status_code == 200, r.text
    km = r.json()
    assert km["exploratory"] is True and km["label"] == "Exploratory" and km["result"]["k"] == 2
    assert sum(km["result"]["sizes"]) == 4
    r = c.post(
        f"{base}/analysis/segments",
        json={
            "method": "rfm",
            "customer_col": "customer_id",
            "date_col": "sale_date",
            "amount_col": "amount",
            "order_col": "sale_id",
            "as_of": "2026-08-31",
            "source": {
                "sql": "SELECT s.customer_id, s.sale_date, s.sale_id, s.amount FROM sales s "
                "JOIN stores st USING (store_id) WHERE s.status <> 'cancelled'"
            },
        },
    )
    assert r.status_code == 200, r.text
    via_sql = r.json()
    assert len(via_sql["result"]["customers"]) == 40 and "JOIN stores" in via_sql["sql"]
    assert {d["table"] for d in via_sql["dataset_versions"]} == {"sales", "stores"}
    r = c.post(
        f"{base}/analysis/segments",
        json={
            "method": "rfm",
            "table": "sales",
            "customer_col": "nope",
            "date_col": "sale_date",
            "amount_col": "amount",
        },
    )
    assert r.status_code == 422 and r.json()["code"] == "analysis_failed"
    r = c.post(f"{base}/analysis/segments", json={"method": "product_quadrants", "dimension": "product"})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


def test_statistical_tests_with_assumptions(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    daily = {
        "metric_query": {
            "metrics": ["revenue"],
            "dimensions": ["sale_date__day", "region"],
            "time": {"start": "2026-01-01", "end": "2026-04-01"},
        }
    }
    r = c.post(
        f"{base}/analysis/stats-test",
        json={
            "test": "t_test",
            "source": daily,
            "value_column": "revenue",
            "group_column": "region",
            "group_a": "North",
            "group_b": "South",
        },
    )
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["label"] == "Statistical test" and t["kind"] == "stats_test"
    # Days whose only South sale was cancelled have no revenue row (the metric filters cancelled sales).
    assert t["result"]["n"]["a"] == 90 and 85 <= t["result"]["n"]["b"] <= 90
    assert {a["name"] for a in t["assumptions"]} >= {"equal variances", "independent samples"}
    assert t["result"]["p_value"] is not None and t["summary"]
    assert any(ch["kind"] == "time" for ch in t["filter_context"])
    _check_artifact(c, base, t, metric="revenue")

    r = c.post(
        f"{base}/analysis/stats-test",
        json={
            "test": "chi_square",
            "source": {"table": "sales"},
            "row_column": "product",
            "column_column": "status",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["df"] == 3

    r = c.post(
        f"{base}/analysis/stats-test",
        json={
            "test": "proportion",
            "source": {"sql": "SELECT s.converted, st.region FROM sales s JOIN stores st USING (store_id)"},
            "group_column": "region",
            "group_a": "North",
            "group_b": "South",
            "success_column": "converted",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["n"]["a"] > r.json()["result"]["n"]["b"]

    r = c.post(
        f"{base}/analysis/stats-test",
        json={"test": "mean_ci", "source": {"table": "sales"}, "value_column": "amount", "confidence": 0.9},
    )
    ci = r.json()["result"]
    assert r.status_code == 200 and ci["low"] < ci["estimate"] < ci["high"] and ci["confidence"] == 0.9
    r = c.post(
        f"{base}/analysis/stats-test",
        json={
            "test": "t_test",
            "source": {"table": "sales"},
            "value_column": "amount",
            "group_column": "product",
            "group_a": "alpha",
            "group_b": "nothing",
        },
    )
    assert r.status_code == 422 and r.json()["code"] == "analysis_failed"


def test_correlation_and_regression_are_exploratory(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    src = {
        "sql": "SELECT amount, cost, units FROM sales WHERE sale_date >= $since",
        "params": {"since": "2026-01-01"},
    }
    r = c.post(f"{base}/analysis/correlation", json={"source": src, "method": "spearman"})
    assert r.status_code == 200, r.text
    corr = r.json()
    assert corr["exploratory"] is True and corr["label"] == "Exploratory"
    assert "not show that one variable causes another" in " ".join(corr["caveats"])
    top = corr["result"]["pairs"][0]
    assert {top["a"], top["b"]} == {"amount", "cost"} and top["r"] > 0.8
    assert corr["params"]["source"]["params"] == {"since": "2026-01-01"}
    _check_artifact(c, base, corr)

    r = c.post(
        f"{base}/analysis/regression",
        json={"source": {"table": "sales"}, "target": "cost", "features": ["amount", "units"]},
    )
    assert r.status_code == 200, r.text
    reg = r.json()
    assert reg["exploratory"] is True and reg["result"]["r_squared"] > 0.8
    assert {x["name"] for x in reg["result"]["coefficients"]} == {"const", "amount", "units"}
    r = c.post(
        f"{base}/analysis/regression",
        json={
            "source": {"table": "sales"},
            "target": "cost",
            "features": ["amount", "units"],
            "model": "importance",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["features"][0]["name"] == "amount"


def test_analysis_errors_and_safety(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    r = c.post(f"{base}/analysis/correlation", json={"source": {"sql": "DELETE FROM sales"}})
    assert r.status_code == 400 and r.json()["code"] == "unsafe_sql"
    r = c.post(
        f"{base}/analysis/forecast", json={"metric_id": "nope", "start": "2024-01-01", "end": "2025-01-01"}
    )
    assert r.status_code == 404
    r = c.post(
        f"{base}/analysis/forecast", json={"metric_id": "revenue", "start": "2025-01-01", "end": "2024-01-01"}
    )
    assert r.status_code == 422
    r = c.post(f"{base}/analysis/correlation", json={"source": {"table": "sales", "sql": "select 1"}})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = c.post(f"{base}/analysis/correlation", json={"source": {"table": "missing_table"}})
    assert r.status_code == 404
    r = c.post(f"{base}/analysis/correlation", json={"source": {"saved_query_id": "nope"}})
    assert r.status_code == 404
    assert c.get(f"{base}/analysis/nope").status_code == 404


def test_viewer_and_read_only_token_can_run_analyses(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    tok = c.post("/api/v1/auth/tokens", json={"name": "ro", "read_only": True}).json()["token"]
    r = c.post(
        f"{base}/analysis/stats-test",
        headers={"Authorization": f"Bearer {tok}"},
        json={"test": "mean_ci", "source": {"table": "sales"}, "value_column": "amount"},
    )
    assert r.status_code == 200, r.text
    r = c.post(
        f"{base}/analysis/anomalies/investigate",
        headers={"Authorization": f"Bearer {tok}"},
        json={"metric_id": "revenue", "date": SPIKE.isoformat()},
    )
    assert r.status_code == 403 and r.json()["code"] == "token_read_only"
