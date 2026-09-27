"""Historical lineage (review R-10) and dashboard tile lineage / filter context (R-30, R-31)."""

from __future__ import annotations

from typing import Any

import pandas as pd
from conftest import ApiClient, load_tables
from test_workbench import MODEL_YAML, _data

API = "/api/v1"


def _setup(c: ApiClient, app: Any) -> str:
    w = c.post(f"{API}/workspaces", json={"name": "Lineage"}).json()
    load_tables(app, w["id"], _data())
    assert (
        c.put(
            f"{API}/workspaces/{w['id']}/semantic-models/current/yaml", json={"yaml": MODEL_YAML}
        ).status_code
        == 200
    )
    c.patch(
        f"{API}/workspaces/{w['id']}/settings",
        json={"investigation": {"require_plan_approval": False, "reference_date": "2026-09-15"}},
    )
    return w["id"]


def _metric_node(graph: dict[str, Any], metric_id: str) -> dict[str, Any]:
    return next(n for n in graph["nodes"] if n["kind"] == "metric" and n["ref_id"] == metric_id)


def _dataset_node(graph: dict[str, Any], table: str) -> dict[str, Any]:
    return next(n for n in graph["nodes"] if n["kind"] == "dataset" and n["meta"].get("table") == table)


def test_historical_artifact_lineage_keeps_the_versions_it_used(local_client: ApiClient, app: Any) -> None:
    c = local_client
    ws = _setup(c, app)
    base = f"{API}/workspaces/{ws}"
    inv = c.post(f"{base}/investigations", json={"question": "Why did revenue change in August 2026?"}).json()
    assert inv["status"] == "completed"
    run1 = [
        a
        for a in c.get(f"{base}/investigations/{inv['id']}/artifacts").json()
        if "revenue" in a["metric_versions"] and a["dataset_versions"]
    ]
    assert run1
    art1 = run1[0]
    # Change the metric definition (v2) and append a row to the data (dataset v2).
    r = c.patch(
        f"{base}/metrics/revenue",
        json={
            "filters": [{"dimension": "channel", "op": "neq", "values": ["x"]}],
            "change_note": "exclude channel x",
        },
    )
    assert r.status_code == 200 and r.json()["version_no"] == 2
    sales = _data()["sales"]
    extra = pd.DataFrame(
        [
            {
                "sale_id": 999,
                "store_id": 1,
                "channel": "web",
                "sale_date": pd.Timestamp("2026-08-30").date(),
                "amount": 50.0,
            }
        ]
    )
    load_tables(app, ws, {"sales": pd.concat([sales, extra], ignore_index=True)})
    job = c.get(c.post(f"{base}/investigations/{inv['id']}/rerun", json={}).json()["poll_url"]).json()
    assert job["status"] == "succeeded", job

    old = c.get(f"{base}/artifacts/{art1['id']}/lineage").json()
    m = _metric_node(old, "revenue")
    assert (
        m["meta"]["version_no"] == 1
        and m["meta"]["is_current"] is False
        and m["meta"]["current_version_no"] == 2
    )
    assert m["id"] == "revenue".join(["metric:", "@v1"])
    d = _dataset_node(old, "sales")
    assert (
        d["meta"]["version_no"] == 1
        and d["meta"]["row_count"] == len(sales)
        and d["meta"]["is_current"] is False
    )

    run2 = [
        a
        for a in c.get(f"{base}/investigations/{inv['id']}/artifacts").json()
        if a["engine_id"] == art1["engine_id"]
        or ("revenue" in a["metric_versions"] and a["dataset_versions"])
    ]
    new = c.get(f"{base}/artifacts/{run2[0]['id']}/lineage").json()
    assert _metric_node(new, "revenue")["meta"]["version_no"] == 2
    d2 = _dataset_node(new, "sales")
    assert d2["meta"]["version_no"] == 2 and d2["meta"]["row_count"] == len(sales) + 1
    # The ratio metric's inputs are resolved at their recorded versions too.
    avg = [
        a
        for a in c.get(f"{base}/artifacts?investigation_id={inv['id']}").json()
        if "avg_sale" in a["metric_versions"]
    ]
    if avg:
        g = c.get(f"{base}/artifacts/{avg[-1]['id']}/lineage").json()
        assert {"metric:avg_sale@v1", "metric:sales_count@v1"} <= {n["id"] for n in g["nodes"]}


def test_tile_lineage_and_filter_context(local_client: ApiClient, app: Any) -> None:
    c = local_client
    ws = _setup(c, app)
    base = f"{API}/workspaces/{ws}"
    c.patch(
        f"{base}/metrics/revenue", json={"filters": [{"dimension": "channel", "op": "neq", "values": ["x"]}]}
    )
    d = c.post(
        f"{base}/dashboards",
        json={
            "name": "Exec",
            "date_range": {"text": "August 2026"},
            "filters": [
                {"dimension": "region", "values": ["North"]},
                {"dimension": "not_a_dim", "values": ["?"]},
            ],
        },
    ).json()
    kpi = c.post(
        f"{base}/dashboards/{d['id']}/tiles",
        json={
            "kind": "kpi",
            "title": "Executive Revenue",
            "binding": {"metric_query": {"metrics": ["revenue"]}},
        },
    ).json()
    data = c.post(f"{base}/dashboards/{d['id']}/tiles/{kpi['id']}/data", json={}).json()
    chips = data["filter_context"]
    kinds = {ch["kind"] for ch in chips}
    assert {"time", "filter", "metric"} <= kinds
    assert any(ch.get("ignored") and "not_a_dim" in ch["value"] for ch in chips)
    assert any(ch["label"] == "Compared with" for ch in chips)
    g = c.get(f"{base}/dashboards/{d['id']}/tiles/{kpi['id']}/lineage").json()
    by_kind = {n["kind"]: n for n in g["nodes"]}
    assert {"dashboard", "kpi", "query", "metric", "entity", "dataset"} <= set(by_kind)
    assert by_kind["kpi"]["label"] == "Executive Revenue" and "sum" in by_kind["query"]["meta"]["sql"].lower()
    edges = {(e["from"], e["to"]) for e in g["edges"]}
    assert (by_kind["kpi"]["id"], by_kind["dashboard"]["id"]) in edges
    assert (by_kind["query"]["id"], by_kind["kpi"]["id"]) in edges
    assert c.get(f"{base}/dashboards/{d['id']}/tiles/nope/lineage").status_code == 404
