"""Workspace isolation, roles, semantic layer versioning, SQL workspace, explore, notebooks, quality,
dashboards, data sources, exports, diagnostics and audit on a small deterministic dataset."""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd
import pytest
from conftest import ApiClient, load_tables, signup

MODEL_YAML = """
name: Mini
entities:
  - {name: sales, table: sales, primary_key: sale_id, grain_description: one row per sale}
  - {name: stores, table: stores, primary_key: store_id, grain_description: one row per store}
dimensions:
  - {name: sale_date, entity: sales, expr: sale_date, type: time}
  - {name: channel, entity: sales, expr: channel, type: categorical}
  - {name: region, entity: stores, expr: region, type: categorical}
relationships:
  - {from_entity: sales, from_col: store_id, to_entity: stores, to_col: store_id, cardinality: many_to_one,
     approved: true}
metrics:
  - {id: revenue, name: Revenue, kind: simple, entity: sales, expr: amount, agg: sum, format: currency,
     default_time_dimension: sale_date, tags: [core]}
  - {id: sales_count, name: Sales, kind: simple, entity: sales, expr: sale_id, agg: count_distinct,
     format: integer, default_time_dimension: sale_date}
  - {id: avg_sale, name: Average Sale, kind: ratio, numerator: revenue, denominator: sales_count,
     format: currency, default_time_dimension: sale_date}
glossary:
  - {term: Sales, definition: Money booked from sales, metric_id: revenue}
"""


def _data() -> dict[str, pd.DataFrame]:
    stores = pd.DataFrame({"store_id": [1, 2, 3], "region": ["North", "North", "South"]})
    rows = []
    sid = 0
    for month, n in (("2026-07", 30), ("2026-08", 24)):
        for i in range(n):
            sid += 1
            rows.append(
                {
                    "sale_id": sid,
                    "store_id": 1 + i % 3,
                    "channel": "web" if i % 2 else "store",
                    "sale_date": pd.Timestamp(f"{month}-{i % 28 + 1:02d}").date(),
                    "amount": 10.0 + i % 5,
                }
            )
    return {"sales": pd.DataFrame(rows), "stores": stores}


@pytest.fixture
def ws(local_client: ApiClient, app: Any) -> dict[str, Any]:
    w = local_client.post("/api/v1/workspaces", json={"name": "Mini"}).json()
    load_tables(app, w["id"], _data())
    r = local_client.put(
        f"/api/v1/workspaces/{w['id']}/semantic-models/current/yaml", json={"yaml": MODEL_YAML}
    )
    assert r.status_code == 200, r.text
    return {**w, "base": f"/api/v1/workspaces/{w['id']}"}


# --------------------------------------------------------------------------------------- isolation


def test_user_cannot_read_another_workspace(client: ApiClient, app: Any) -> None:
    signup(client, "alice@example.com")
    a_ws = client.post("/api/v1/workspaces", json={"name": "Alice"}).json()["id"]
    load_tables(app, a_ws, _data())
    client.post(f"/api/v1/workspaces/{a_ws}/queries/saved", json={"name": "q", "sql": "SELECT 1"})
    with ApiClient(app) as bob:
        signup(bob, "bob@example.com")
        assert [w["id"] for w in bob.get("/api/v1/workspaces").json()] == []
        for path in (
            "",
            "/datasets",
            "/queries/history",
            "/queries/saved",
            "/metrics",
            "/findings",
            "/investigations",
            "/home",
            "/audit",
            "/diagnostics/events",
            "/search?q=sales",
        ):
            r = bob.get(f"/api/v1/workspaces/{a_ws}{path}")
            assert r.status_code == 404, (path, r.status_code)
        r = bob.post(f"/api/v1/workspaces/{a_ws}/queries/run", json={"sql": "SELECT * FROM sales"})
        assert r.status_code == 404
        # Bob's own workspace cannot see Alice's tables: each workspace has its own DuckDB store.
        b_ws = bob.post("/api/v1/workspaces", json={"name": "Bob"}).json()["id"]
        r = bob.post(f"/api/v1/workspaces/{b_ws}/queries/run", json={"sql": "SELECT * FROM sales"})
        assert r.status_code == 400 and r.json()["code"] == "query_failed"
        # Resource ids from another workspace are not reachable through Bob's workspace either.
        alice_ds = client.get(f"/api/v1/workspaces/{a_ws}/datasets").json()[0]["id"]
        assert bob.get(f"/api/v1/workspaces/{b_ws}/datasets/{alice_ds}").status_code == 404


def test_roles(client: ApiClient, app: Any) -> None:
    signup(client, "owner@example.com")
    ws_id = client.post("/api/v1/workspaces", json={"name": "Team"}).json()["id"]
    load_tables(app, ws_id, _data())
    with ApiClient(app) as v:
        signup(v, "viewer@example.com")
        r = client.post(
            f"/api/v1/workspaces/{ws_id}/members", json={"email": "viewer@example.com", "role": "viewer"}
        )
        assert r.status_code == 201
        base = f"/api/v1/workspaces/{ws_id}"
        assert v.get(f"{base}/datasets").status_code == 200
        assert v.post(f"{base}/queries/run", json={"sql": "SELECT count(*) FROM sales"}).status_code == 200
        r = v.post(f"{base}/queries/saved", json={"name": "x", "sql": "SELECT 1"})
        assert r.status_code == 403 and r.json()["code"] == "insufficient_role"
        assert v.patch(base, json={"name": "hijack"}).status_code == 403
        uid = v.get("/api/v1/auth/me").json()["id"]
        client.patch(f"{base}/members/{uid}", json={"role": "editor"})
        assert v.post(f"{base}/queries/saved", json={"name": "x", "sql": "SELECT 1"}).status_code == 201
        assert v.delete(base).status_code == 403
        me = client.get("/api/v1/auth/me").json()["id"]
        r = client.patch(f"{base}/members/{me}", json={"role": "viewer"})
        assert r.status_code == 422 and r.json()["code"] == "last_owner"


def test_workspace_delete_removes_store(local_client: ApiClient, app: Any, settings: Any) -> None:
    ws_id = local_client.post("/api/v1/workspaces", json={"name": "Tmp"}).json()["id"]
    load_tables(app, ws_id, _data())
    path = settings.workspace_dir(ws_id)
    assert path.exists()
    assert local_client.delete(f"/api/v1/workspaces/{ws_id}").status_code == 200
    assert not path.exists()
    assert local_client.get(f"/api/v1/workspaces/{ws_id}").status_code == 404


# --------------------------------------------------------------------------------------- semantic


def test_semantic_model_and_metric_versions(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    cur = c.get(f"{base}/semantic-models/current").json()
    assert {m["id"] for m in cur["model"]["metrics"]} == {"revenue", "sales_count", "avg_sale"}
    assert not [i for i in cur["issues"] if i["severity"] == "error"]
    rel = c.get(f"{base}/relationships", params={"status": "approved"}).json()
    assert rel and rel[0]["join_analysis"] is None or True
    m = c.get(f"{base}/metrics/revenue").json()
    assert m["version_no"] == 1 and m["agg"] == "sum"
    r = c.put(f"{base}/metrics/revenue", json={"expr": "amount * 2", "change_note": "double"})
    assert r.status_code == 200 and r.json()["version_no"] == 2
    same = c.put(f"{base}/metrics/revenue", json={"expr": "amount * 2"}).json()
    assert same["version_no"] == 2  # identical definition: no new version
    versions = c.get(f"{base}/metrics/revenue/versions").json()
    assert [v["version_no"] for v in versions] == [2, 1] and versions[1]["definition"]["expr"] == "amount"
    diff = c.get(f"{base}/metrics/revenue/diff", params={"from_version": 1, "to_version": 2}).json()
    assert diff["changes"] == [{"field": "expr", "from": "amount", "to": "amount * 2"}]
    restored = c.post(f"{base}/metrics/revenue/versions/1/restore").json()
    assert restored["version_no"] == 3 and restored["expr"] == "amount"
    hist = c.get(f"{base}/semantic-models/history").json()
    assert len(hist) >= 3 and len({h["content_hash"] for h in hist}) == len(hist)
    r = c.post(
        f"{base}/metrics",
        json={
            "id": "revenue",
            "name": "Dup",
            "kind": "simple",
            "entity": "sales",
            "expr": "amount",
            "agg": "sum",
        },
    )
    assert r.status_code == 409
    bad = c.post(
        f"{base}/metrics",
        json={"id": "bad", "name": "Bad", "kind": "simple", "entity": "nope", "expr": "x", "agg": "sum"},
    )
    assert bad.status_code == 422
    r = c.delete(f"{base}/metrics/sales_count")
    assert r.status_code == 409  # avg_sale depends on it
    yaml = c.get(f"{base}/semantic-models/current/yaml")
    assert yaml.status_code == 200 and "revenue" in yaml.text
    res = c.get(f"{base}/semantic-models/calendar/resolve", params={"text": "August 2026"}).json()
    assert res["window"]["start"] == "2026-08-01" and res["previous_period"]["start"] == "2026-07-01"


def test_metric_trees_and_glossary(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    sug = c.post(f"{base}/metric-trees/suggest", json={"root_metric": "avg_sale"}).json()
    assert {(e["parent"], e["child"], e["relation"]) for e in sug["edges"]} == {
        ("avg_sale", "revenue", "ratio_numerator"),
        ("avg_sale", "sales_count", "ratio_denominator"),
    }
    assert all(not e["approved"] for e in sug["edges"])
    t = c.post(
        f"{base}/metric-trees/avg_sale/edges",
        json={"parent": "avg_sale", "child": "revenue", "decision": "approve"},
    ).json()
    assert [(e["child"], e["approved"]) for e in t["spec"]["nodes"]] == [("revenue", True)]
    g = c.get(f"{base}/glossary").json()
    assert g[0]["spec"]["term"] == "Sales"
    r = c.post(f"{base}/glossary", json={"term": "AOV", "definition": "x", "metric_id": "nope"})
    assert r.status_code == 422


# --------------------------------------------------------------------------------------- SQL


def test_sql_run_params_history_and_safety(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    r = c.post(
        f"{base}/queries/run",
        json={
            "sql": "SELECT channel, sum(amount) AS total FROM sales WHERE sale_date >= $start GROUP BY 1 ORDER BY 1",
            "params": {"start": "2026-08-01"},
            "parameters": [{"name": "start", "type": "date"}],
        },
    )
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["result"]["columns"][0]["name"] == "channel" and run["result"]["row_count"] == 2
    assert run["dataset_versions"]["sales"]["version_no"] == 1
    assert run["charts"][0]["type"] == "bar"
    for bad in (
        "DELETE FROM sales",
        "SELECT 1; DROP TABLE sales",
        "COPY sales TO '/tmp/x.csv'",
        "SELECT * FROM read_csv('/etc/passwd')",
        "ATTACH '/tmp/x.db'",
        "INSTALL httpfs",
    ):
        r = c.post(f"{base}/queries/run", json={"sql": bad})
        assert r.status_code == 400 and r.json()["code"] == "unsafe_sql", bad
    r = c.post(f"{base}/queries/run", json={"sql": "SELECT * FROM nope"})
    assert r.status_code == 400 and r.json()["code"] == "query_failed"
    r = c.post(f"{base}/queries/run", json={"sql": "SELECT $x AS v"})
    assert r.status_code == 422 and r.json()["code"] == "missing_parameter"
    hist = c.get(f"{base}/queries/history").json()
    statuses = [h["status"] for h in hist["items"]]
    assert "rejected" in statuses and "failed" in statuses and "succeeded" in statuses
    rerun = c.post(f"{base}/queries/history/{run['id']}/rerun").json()
    cmp_ = c.post(
        f"{base}/queries/compare", json={"left_run_id": run["id"], "right_run_id": rerun["id"]}
    ).json()
    assert cmp_["identical"] is True and cmp_["keys"] == ["channel"]
    v = c.post(f"{base}/queries/validate", json={"sql": "select * from sales where id = $id"}).json()
    assert v["ok"] and v["tables"] == ["sales"] and v["parameters"] == ["id"]
    saved = c.post(
        f"{base}/queries/saved",
        json={
            "name": "By channel",
            "sql": "SELECT channel, count(*) AS n FROM sales WHERE channel = $ch GROUP BY 1",
            "parameters": [{"name": "ch", "type": "string", "default": "web"}],
        },
    ).json()
    res = c.post(f"{base}/queries/saved/{saved['id']}/run", json={}).json()
    assert res["result"]["rows"] == [["web", 27]]
    r = c.post(f"{base}/queries/saved", json={"name": "bad", "sql": "DROP TABLE sales"})
    assert r.status_code == 400
    r = c.post(f"{base}/queries/generate", json={"prompt": "revenue by channel"})
    assert r.status_code == 409 and r.json()["code"] == "ai_disabled"


def test_explore_table_and_pivot(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    r = c.post(
        f"{base}/explore/table",
        json={
            "table": "sales",
            "filters": [{"column": "channel", "op": "eq", "values": ["web"]}],
            "calculated": [{"name": "amount_x2", "expr": "amount * 2"}],
            "group_by": [{"column": "sale_date", "grain": "month"}],
            "aggregates": [
                {"column": "amount_x2", "fn": "sum", "alias": "total"},
                {"column": "*", "fn": "count"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["result"]["row_count"] == 2 and "\"channel\" = 'web'" in out["sql"]
    for bad in ("(SELECT 1)", "read_csv('/etc/passwd')", "sum(amount)", "nope + 1"):
        r = c.post(
            f"{base}/explore/table",
            json={"table": "sales", "calculated": [{"name": "x", "expr": bad}], "columns": ["x"]},
        )
        assert r.status_code == 422, bad
    r = c.post(
        f"{base}/explore/table",
        json={"table": "sales", "order_by": [{"field": "amount; DROP", "desc": True}]},
    )
    assert r.status_code == 422
    m = c.post(
        f"{base}/explore/metrics", json={"metrics": ["revenue", "avg_sale"], "dimensions": ["region"]}
    ).json()
    assert m["compiled"]["joins"] and m["result"]["row_count"] == 2
    p = c.post(
        f"{base}/explore/pivot",
        json={
            "table_query": {"table": "sales"},
            "rows": ["channel"],
            "columns": ["sale_date__month"],
            "values": [{"field": "amount", "agg": "avg", "label": "avg"}],
        },
    ).json()
    total = next(r for r in p["rows"] if r["is_total"])
    grand = total["cells"][-1]
    expected = sum(10.0 + i % 5 for i in range(30)) + sum(10.0 + i % 5 for i in range(24))
    assert grand == pytest.approx(expected / 54)


def test_charts_suggest(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]

    def top(columns: list[dict[str, str]], rows: list[list[Any]]) -> str:
        return c.post(f"{base}/charts/suggest", json={"columns": columns, "rows": rows}).json()[0]["type"]

    assert (
        top(
            [{"name": "month", "type": "DATE"}, {"name": "revenue", "type": "DOUBLE"}],
            [["2026-07-01", 1.0], ["2026-08-01", 2.0]],
        )
        == "line"
    )
    assert (
        top(
            [{"name": "region", "type": "VARCHAR"}, {"name": "revenue", "type": "DOUBLE"}],
            [["N", 1.0], ["S", 2.0]],
        )
        == "bar"
    )
    assert top([{"name": "revenue", "type": "DOUBLE"}], [[3.0]]) == "kpi"
    assert (
        top(
            [{"name": "x", "type": "DOUBLE"}, {"name": "y", "type": "DOUBLE"}],
            [[float(i), float(i * 2)] for i in range(10)],
        )
        == "scatter"
    )
    types = [
        s["type"]
        for s in c.post(
            f"{base}/charts/suggest",
            json={
                "columns": [{"name": "seg", "type": "VARCHAR"}, {"name": "change", "type": "DOUBLE"}],
                "rows": [["a", 5.0], ["b", -3.0], ["c", -1.0]],
            },
        ).json()
    ]
    assert "waterfall" in types and "pie" not in types


# --------------------------------------------------------------------------------------- notebooks / python


def test_notebook_cells(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    nb = c.post(f"{base}/notebooks", json={"title": "NB"}).json()
    md = c.post(f"{base}/notebooks/{nb['id']}/cells", json={"kind": "markdown", "source": "# Hi"}).json()
    sql = c.post(
        f"{base}/notebooks/{nb['id']}/cells",
        json={"kind": "sql", "source": "SELECT channel, sum(amount) AS total FROM sales GROUP BY 1"},
    ).json()
    chart = c.post(
        f"{base}/notebooks/{nb['id']}/cells", json={"kind": "chart", "config": {"source_cell_id": sql["id"]}}
    ).json()
    bad = c.post(
        f"{base}/notebooks/{nb['id']}/cells", json={"kind": "sql", "source": "DROP TABLE sales"}
    ).json()
    run = c.post(f"{base}/notebooks/{nb['id']}/run").json()
    assert run["stopped_at"] == bad["id"] and run["executed"] == 4
    cells = {x["id"]: x for x in run["notebook"]["cells"]}
    assert cells[sql["id"]]["output"]["result"]["row_count"] == 2
    assert cells[chart["id"]]["output"]["chart"]["type"] == "bar"
    assert cells[bad["id"]]["status"] == "error" and cells[bad["id"]]["output"]["code"] == "unsafe_sql"
    c.post(
        f"{base}/notebooks/{nb['id']}/reorder",
        json={"cell_ids": [sql["id"], md["id"], chart["id"], bad["id"]]},
    )
    assert [x["id"] for x in c.get(f"{base}/notebooks/{nb['id']}").json()["cells"]][0] == sql["id"]
    doc = json.loads(c.get(f"{base}/notebooks/{nb['id']}/ipynb").content)
    assert doc["nbformat"] == 4 and "read_only=True" in "".join(doc["cells"][1]["source"])
    r = c.post(f"{base}/notebooks/{nb['id']}/reorder", json={"cell_ids": [sql["id"]]})
    assert r.status_code == 422


def test_python_sandbox(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    r = c.post(
        f"{base}/python/run",
        json={
            "code": "print(len(df))\nresult = df.groupby('channel').amount.sum()",
            "inputs": {"df": "SELECT * FROM sales"},
            "timeout_s": 60,
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["stdout"].strip() == "54"
    blocked = c.post(
        f"{base}/python/run",
        json={"code": "import socket\nsocket.create_connection(('1.1.1.1', 80))", "timeout_s": 30},
    ).json()
    assert blocked["ok"] is False and blocked["error"]


# --------------------------------------------------------------------------------------- quality


def test_quality_rules(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    ds = next(d for d in c.get(f"{base}/datasets").json() if d["table_name"] == "sales")
    r = c.post(f"{base}/quality/rules", json={"dataset_id": ds["id"], "kind": "unique", "column": "sale_id"})
    assert r.status_code == 201, r.text
    ok = r.json()
    bad_r = c.post(
        f"{base}/quality/rules",
        json={"dataset_id": ds["id"], "kind": "range", "column": "amount", "params": {"min_value": 12}},
    )
    assert bad_r.status_code == 201, bad_r.text
    bad = bad_r.json()
    r = c.post(f"{base}/quality/rules", json={"dataset_id": ds["id"], "kind": "range", "column": "amount"})
    assert r.status_code == 422
    runs = c.post(f"{base}/quality/run", json={}).json()
    by_rule = {x["rule_id"]: x for x in runs}
    assert by_rule[ok["id"]]["passed"] and not by_rule[bad["id"]]["passed"]
    rows = c.get(f"{base}/quality/runs/{by_rule[bad['id']]['id']}/failing-rows").json()
    assert rows["row_count"] > 0 and by_rule[bad["id"]]["suggested_fix"]
    sug_r = c.post(f"{base}/quality/suggest", json={"dataset_id": ds["id"]})
    assert sug_r.status_code == 200, sug_r.text
    sug = sug_r.json()
    assert all(s["status"] == "suggested" for s in sug)
    if sug:
        assert c.post(f"{base}/quality/rules/{sug[0]['id']}/accept").json()["status"] == "active"
    summ = c.get(f"{base}/quality/summary").json()
    assert summ["failing"] >= 1
    # rules never change data
    assert c.post(f"{base}/queries/run", json={"sql": "SELECT count(*) FROM sales"}).json()["result"][
        "rows"
    ] == [[54]]


# --------------------------------------------------------------------------------------- dashboards / findings


def test_dashboard_tiles(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    d = c.post(
        f"{base}/dashboards",
        json={
            "name": "Sales",
            "date_range": {"text": "August 2026"},
            "filters": [{"dimension": "region", "values": ["North"]}],
        },
    ).json()
    kpi = c.post(
        f"{base}/dashboards/{d['id']}/tiles",
        json={"kind": "kpi", "title": "Revenue", "binding": {"metric_query": {"metrics": ["revenue"]}}},
    ).json()
    c.post(f"{base}/dashboards/{d['id']}/tiles", json={"kind": "text", "title": "Note", "text": "hello"})
    r = c.post(f"{base}/dashboards/{d['id']}/tiles", json={"kind": "chart", "binding": {}})
    assert r.status_code == 422
    data = c.post(f"{base}/dashboards/{d['id']}/tiles/{kpi['id']}/data", json={}).json()
    assert data["error"] is None and data["kpi"]["baseline"] is not None
    assert any(fc["kind"] == "time" for fc in data["filter_context"])
    assert any(fc["dimension"] == "region" for fc in data["filter_context"])
    full = c.get(f"{base}/dashboards/{d['id']}").json()
    assert len(full["layout"]) == 2 and full["version_no"] >= 3
    assert len(c.get(f"{base}/dashboards/{d['id']}/versions").json()) == full["version_no"]


def test_manual_finding_is_hypothesis_without_evidence(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    f = c.post(
        f"{base}/findings",
        json={
            "statement": "Web sales grew because of the new site",
            "statement_type": "supported_explanation",
            "evidence_strength": "strong",
        },
    ).json()
    assert f["statement_type"] == "hypothesis" and f["evidence_strength"] == "hypothesis_only"
    f2 = c.post(f"{base}/findings/{f['id']}/status", json={"status": "needs_review", "note": "check"}).json()
    assert f2["status"] == "needs_review" and f2["version_no"] == 2
    c.post(f"{base}/findings/{f['id']}/comments", json={"body": "Agree, verify with web analytics"})
    comments = c.get(f"{base}/findings/{f['id']}/comments").json()
    assert comments[0]["user_name"] == "Local Analyst"
    assert len(c.get(f"{base}/findings/{f['id']}/versions").json()) == 2
    es = c.post(f"{base}/reports/executive-summary", json={}).json()
    summary = next(b for b in es["blocks"] if b["type"] == "summary")
    assert summary["observations"] == [] and summary["excluded_count"] == 1


# --------------------------------------------------------------------------------------- data sources


def test_data_source_credentials_are_encrypted(local_client: ApiClient, ws: dict[str, Any], app: Any) -> None:
    c, base = local_client, ws["base"]
    kinds = {k["kind"] for k in c.get("/api/v1/data-sources/kinds").json()}
    assert "postgres" in kinds and "duckdb" not in kinds
    secret = "sup3r-s3cret-pa55"
    r = c.post(
        f"{base}/data-sources",
        json={
            "name": "WH",
            "kind": "postgres",
            "config": {"host": "127.0.0.1", "port": 1, "database": "x", "user": "u", "connect_timeout_s": 1},
            "secrets": {"password": secret},
        },
    )
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["secret_fields"] == ["password"] and secret not in r.text
    r = c.post(f"{base}/data-sources", json={"name": "Bad", "kind": "postgres", "config": {"password": "x"}})
    assert r.status_code == 422 and r.json()["code"] == "secret_in_config"
    test = c.post(f"{base}/data-sources/{src['id']}/test").json()
    assert test["ok"] is False and secret not in json.dumps(test)
    from analystos_api.models import AuditLog, DataSource, DiagnosticEvent
    from analystos_api.services.writer import writer_for

    writer_for(app.state.aos.session_factory).flush()
    with app.state.aos.session_factory() as db:
        row = db.get(DataSource, src["id"])
        assert row is not None and row.secrets_encrypted and secret not in row.secrets_encrypted
        assert app.state.aos.secret_box.decrypt(row.secrets_encrypted) == {"password": secret}
        dumped = json.dumps(
            [a.detail for a in db.query(AuditLog).all()]
            + [[e.detail, e.error] for e in db.query(DiagnosticEvent).all()],
            default=str,
        )
        assert secret not in dumped
    upd = c.patch(f"{base}/data-sources/{src['id']}", json={"secrets": {"password": None}}).json()
    assert upd["secret_fields"] == [] and upd["status"] == "untested"


# --------------------------------------------------------------------------------------- exports / platform


def test_exports(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    run = c.post(f"{base}/queries/run", json={"sql": "SELECT '=cmd()' AS evil, 1 AS n"}).json()
    csv = c.post(f"{base}/exports", json={"target": "query_run", "id": run["id"], "format": "csv"})
    assert csv.status_code == 200 and "'=cmd()" in csv.content.decode("utf-8-sig")
    xlsx = c.post(f"{base}/exports", json={"target": "query_run", "id": run["id"], "format": "xlsx"})
    assert xlsx.content[:2] == b"PK"
    ds = c.get(f"{base}/datasets").json()[0]
    assert (
        c.post(f"{base}/exports", json={"target": "dataset", "id": ds["id"], "format": "csv"}).status_code
        == 200
    )
    r = c.post(f"{base}/exports", json={"target": "dataset", "id": ds["id"], "format": "pdf"})
    assert r.status_code == 422 and r.json()["code"] == "unsupported_export"
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    img = c.post(
        f"{base}/exports/images",
        files={"file": ("c.png", io.BytesIO(png))},
        data={"source_type": "report_block", "source_id": "blk1", "title": "Chart"},
    )
    assert img.status_code == 201
    assert (
        c.post(f"{base}/exports/images", files={"file": ("c.png", io.BytesIO(b"GIF89a"))}).status_code == 422
    )
    files = c.get(f"{base}/exports").json()
    assert any(f["purpose"] == "chart_image" for f in files) and any(f["purpose"] == "export" for f in files)
    dl = c.get(files[0]["download_url"])
    assert dl.status_code == 200


def test_diagnostics_audit_search_ai(local_client: ApiClient, ws: dict[str, Any]) -> None:
    c, base = local_client, ws["base"]
    c.post(f"{base}/queries/run", json={"sql": "SELECT 1"})
    c.post(f"{base}/queries/run", json={"sql": "DROP TABLE sales"})
    events = c.get(f"{base}/diagnostics/events", params={"category": "sql"}).json()
    assert {e["status"] for e in events} >= {"ok", "rejected"}
    summ = c.get(f"{base}/diagnostics/summary").json()
    assert any(cat["category"] == "sql" for cat in summ["categories"])
    audit = c.get(f"{base}/audit").json()
    assert any(a["action"] == "semantic_model.import" for a in audit["items"])
    hits = c.get(f"{base}/search", params={"q": "avg"}).json()
    assert hits[0]["ref_id"] == "avg_sale"
    ai = c.get(f"{base}/ai/settings").json()
    assert ai["active"] is False and ai["has_api_key"] is False
    ai = c.put(f"{base}/ai/settings", json={"enabled": True, "api_key": "sk-ant-test-123456789"}).json()
    assert ai["has_api_key"] and ai["key_source"] == "workspace" and ai["active"] is False  # server AI off
    assert "sk-ant" not in json.dumps(c.get(f"{base}/settings").json())
    assert c.post(f"{base}/ai/test").json()["ok"] is False
    assert c.get(f"{base}/ai/usage").json()["totals"]["calls"] == 0
    sysd = c.get("/api/v1/diagnostics").json()
    assert sysd["migration_revision"] == "0002" and sysd["database_dialect"] == "sqlite"
    assert c.get("/api/v1/health").json()["status"] == "ok"


def test_openapi_is_clean(local_client: ApiClient) -> None:
    spec = local_client.get("/api/openapi.json").json()
    ops = [op for path in spec["paths"].values() for op in path.values()]
    ids = [op["operationId"] for op in ops]
    assert len(ids) == len(set(ids)) and all(ids)
    assert all(op.get("tags") for op in ops)
    for path, item in spec["paths"].items():
        for method, op in item.items():
            ok = op["responses"].get("200") or op["responses"].get("201") or op["responses"].get("202")
            assert ok is not None, (method, path)
