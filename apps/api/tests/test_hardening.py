"""Regression tests for review findings in the API: secrets, quality errors, metric validation,
report block references and other hardening (R-19, R-45, R-51, R-52)."""

from __future__ import annotations

from typing import Any

from conftest import ApiClient

API = "/api/v1"


def test_unsaved_connection_test_never_echoes_config_values(
    local_client: ApiClient, workspace: dict[str, Any]
) -> None:
    base = f"{API}/workspaces/{workspace['id']}"
    r = local_client.post(
        f"{base}/data-sources/test",
        json={
            "name": "x",
            "kind": "postgres",
            "config": {"host": "db", "port": 5432, "database": "d", "user": "u", "password": "INCONFIGPW999"},
        },
    )
    assert r.status_code == 422 and r.json()["code"] == "secret_in_config"
    assert "INCONFIGPW999" not in r.text
    r = local_client.post(
        f"{base}/data-sources/test",
        json={
            "name": "x",
            "kind": "postgres",
            "config": {"host": "db", "port": "SECRETISH999", "database": "d", "user": "u"},
            "secrets": {"password": "pw-VALUE-123"},
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "SECRETISH999" not in r.text and "pw-VALUE-123" not in r.text
    events = local_client.get(f"{base}/diagnostics/events?category=connector").text
    assert "SECRETISH999" not in events and "INCONFIGPW999" not in events and "pw-VALUE-123" not in events


def test_metric_expression_is_validated_on_save(
    local_client: ApiClient, workspace: dict[str, Any], app: Any
) -> None:
    """R-52: a bad expression is refused when the metric is saved, not at first use."""
    import pandas as pd
    from conftest import load_tables

    base = f"{API}/workspaces/{workspace['id']}"
    load_tables(
        app,
        workspace["id"],
        {
            "sales": pd.DataFrame(
                {"sale_id": [1, 2], "amount": [5.0, 7.0], "day": ["2026-01-01", "2026-01-02"]}
            )
        },
    )
    yaml = (
        "name: m\nentities:\n  - {name: sales, table: sales, primary_key: sale_id}\n"
        "dimensions:\n  - {name: day, entity: sales, expr: day, type: time}\n"
    )
    r = local_client.put(f"{base}/semantic-models/current/yaml", json={"yaml": yaml})
    assert r.status_code == 200, r.text
    bad = {
        "id": "rev",
        "name": "Revenue",
        "kind": "simple",
        "entity": "sales",
        "expr": "amont",
        "agg": "sum",
        "default_time_dimension": "day",
    }
    issues = local_client.post(f"{base}/metrics/validate", json=bad).json()
    assert issues and issues[0]["severity"] == "error" and "amont" in issues[0]["message"]
    r = local_client.post(f"{base}/metrics", json=bad)
    assert r.status_code == 422 and r.json()["code"] == "invalid_metric"
    r = local_client.post(f"{base}/metrics", json={**bad, "expr": "amount"})
    assert r.status_code == 201, r.text
    r = local_client.patch(f"{base}/metrics/rev", json={"expr": "amount * nope"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_metric"
    r = local_client.patch(f"{base}/metrics/rev", json={"time_aggregation": "last", "change_note": "balance"})
    assert r.status_code == 200, r.text
    assert r.json()["time_aggregation"] == "last" and r.json()["version_no"] == 2


def test_quality_rule_that_cannot_run_is_an_error_not_a_failure(
    local_client: ApiClient, workspace: dict[str, Any], app: Any
) -> None:
    """R-51: an execution error is recorded as status=error, not passed=false with 0 failing rows."""
    import pandas as pd
    from conftest import load_tables

    base = f"{API}/workspaces/{workspace['id']}"
    load_tables(app, workspace["id"], {"t": pd.DataFrame({"a": [1, 2, 3]})})
    ds = local_client.get(f"{base}/datasets").json()[0]
    rule = local_client.post(
        f"{base}/quality/rules",
        json={
            "dataset_id": ds["id"],
            "kind": "custom_sql",
            "params": {"sql": "SELECT * FROM t WHERE nope > 1"},
            "severity": "error",
            "name": "broken",
        },
    ).json()
    run = local_client.post(f"{base}/quality/rules/{rule['id']}/run").json()
    assert run["status"] == "error" and run["error"] and run["suggested_fix"] is None
    summary = local_client.get(f"{base}/quality/summary").json()
    assert summary["errored"] == 1 and summary["failing"] == 0
    ok = local_client.post(
        f"{base}/quality/rules",
        json={
            "dataset_id": ds["id"],
            "kind": "custom_sql",
            "params": {"sql": "SELECT * FROM t WHERE a > 2"},
            "severity": "error",
            "name": "some",
        },
    ).json()
    run = local_client.post(f"{base}/quality/rules/{ok['id']}/run").json()
    assert run["status"] == "failed" and run["failing_count"] == 1 and run["error"] is None


def test_report_blocks_must_reference_this_workspace(client: ApiClient) -> None:
    """R-45: a block cannot point at another workspace's finding or artifact."""
    from conftest import signup

    signup(client, "a@example.com")
    ws_a = client.post(f"{API}/workspaces", json={"name": "A"}).json()["id"]
    ws_b = client.post(f"{API}/workspaces", json={"name": "B"}).json()["id"]
    f = client.post(
        f"{API}/workspaces/{ws_a}/findings",
        json={"statement": "xyz finding", "statement_type": "observation", "artifact_ids": []},
    ).json()
    r = client.post(
        f"{API}/workspaces/{ws_b}/reports",
        json={"title": "r", "blocks": [{"type": "finding", "finding_id": f["id"]}]},
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_block_reference"
    r = client.post(
        f"{API}/workspaces/{ws_a}/reports",
        json={"title": "r", "blocks": [{"type": "finding", "finding_id": f["id"]}]},
    )
    assert r.status_code == 201
    r = client.post(
        f"{API}/workspaces/{ws_a}/reports",
        json={"title": "r", "blocks": [{"type": "chart", "artifact_id": "art_missing"}]},
    )
    assert r.status_code == 422
