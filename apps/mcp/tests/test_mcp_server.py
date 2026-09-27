"""MCP tools against a real AnalystOS API (in-process), authenticated with an API token."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "tests"))

from analystos_api.main import create_app  # noqa: E402
from analystos_mcp import AnalystOSClient, build_server  # noqa: E402
from conftest import ApiClient, load_tables, make_settings  # noqa: E402

MODEL_YAML = """
name: Mini
entities:
  - {name: sales, table: sales, primary_key: sale_id, grain_description: one row per sale}
dimensions:
  - {name: sale_date, entity: sales, expr: sale_date, type: time}
  - {name: region, entity: sales, expr: region, type: categorical}
metrics:
  - {id: revenue, name: Revenue, kind: simple, entity: sales, expr: amount, agg: sum, format: currency,
     default_time_dimension: sale_date}
  - {id: sales_count, name: Sales Count, kind: simple, entity: sales, expr: sale_id, agg: count_distinct,
     format: integer, default_time_dimension: sale_date}
"""


def _sales() -> pd.DataFrame:
    rows = []
    sid = 0
    for month, factor in (("2026-07", 1.0), ("2026-08", 0.8)):
        for region, base in (("North", 100.0), ("South", 60.0)):
            for i in range(20):
                sid += 1
                amt = base * (factor if region == "North" else 1.0)
                rows.append(
                    {
                        "sale_id": sid,
                        "sale_date": pd.Timestamp(f"{month}-{(i % 27) + 1:02d}").date(),
                        "region": region,
                        "amount": amt,
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture
def env(tmp_path: Path) -> Any:
    app = create_app(make_settings(tmp_path))
    with ApiClient(app) as session:
        session.post(
            "/api/v1/auth/signup", json={"email": "mcp@example.com", "name": "M", "password": "x" * 12}
        )
        ws = session.post("/api/v1/workspaces", json={"name": "Mini"}).json()["id"]
        other = session.post("/api/v1/workspaces", json={"name": "Other"}).json()["id"]
        load_tables(app, ws, {"sales": _sales()})
        r = session.put(f"/api/v1/workspaces/{ws}/semantic-models/current/yaml", json={"yaml": MODEL_YAML})
        assert r.status_code == 200, r.text
        token = session.post("/api/v1/auth/tokens", json={"name": "mcp", "workspace_id": ws}).json()["token"]
        ro = session.post("/api/v1/auth/tokens", json={"name": "ro", "read_only": True}).json()["token"]
        with ApiClient(app) as http:
            yield {
                "ws": ws,
                "other": other,
                "server": build_server(AnalystOSClient(token=token, http=http, poll_interval=0.01)),
                "ro_server": build_server(AnalystOSClient(token=ro, http=http)),
                "bad_server": build_server(AnalystOSClient(token="aos_invalid", http=http)),
            }


def call(server: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = asyncio.run(server.call_tool(name, args))
    if getattr(result, "structured_content", None) is not None:
        data = result.structured_content
        return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
    return json.loads(result.content[0].text)


def test_tools_are_registered(env: dict[str, Any]) -> None:
    tools = {t.name for t in asyncio.run(env["server"].list_tools())}
    assert tools == {
        "list_workspaces",
        "inspect_dataset",
        "inspect_metric",
        "run_analysis",
        "run_sql",
        "get_investigation",
        "get_findings",
        "build_report",
    }


def test_workspace_permissions_apply(env: dict[str, Any]) -> None:
    out = call(env["server"], "list_workspaces", {})
    assert [w["id"] for w in out["workspaces"]] == [env["ws"]]  # token restricted to one workspace
    denied = call(env["server"], "run_sql", {"workspace_id": env["other"], "sql": "SELECT 1"})
    assert denied["error"]["status"] == 404
    bad = call(env["bad_server"], "list_workspaces", {})
    assert bad["error"]["status"] == 401


def test_inspect_and_sql(env: dict[str, Any]) -> None:
    ws = env["ws"]
    ds = call(env["server"], "inspect_dataset", {"workspace_id": ws, "dataset": "sales"})
    assert ds["dataset"]["row_count"] == 80 and ds["preview"]["row_count"] == 5
    assert ds["usage"]["metrics"] == ["revenue", "sales_count"]
    m = call(env["server"], "inspect_metric", {"workspace_id": ws, "metric_id": "revenue"})
    assert m["metric"]["version_no"] == 1 and m["examples"]["points"]
    res = call(
        env["server"],
        "run_sql",
        {
            "workspace_id": ws,
            "sql": "SELECT region, sum(amount) AS s FROM sales WHERE region = $r GROUP BY 1",
            "params": {"r": "North"},
        },
    )
    assert res.get("rows") == [["North", 3600.0]], res
    unsafe = call(env["server"], "run_sql", {"workspace_id": ws, "sql": "DROP TABLE sales"})
    assert unsafe["error"]["code"] == "unsafe_sql"


def test_analysis_findings_report(env: dict[str, Any]) -> None:
    ws = env["ws"]
    inv = call(
        env["server"], "run_analysis", {"workspace_id": ws, "question": "Why was August 2026 revenue down?"}
    )
    assert inv["status"] == "completed", inv
    root = inv["tree"][0]
    assert root["pct_change"] == pytest.approx(-0.125, abs=1e-9)  # 3200 -> 2800
    assert any(n["statement"].startswith("Region = North") for n in inv["tree"] if n["kind"] == "segment")
    got = call(
        env["server"],
        "get_investigation",
        {"workspace_id": ws, "investigation_id": inv["id"], "include_sql": True},
    )
    assert got["artifacts"] and all(a["sql"] for a in got["artifacts"])
    assert call(env["server"], "get_findings", {"workspace_id": ws})["findings"] == []
    rep = call(env["server"], "build_report", {"workspace_id": ws, "investigation_id": inv["id"]})
    assert "Revenue" in rep["markdown"] and rep["status"] == "draft"


def test_read_only_token_can_query_but_not_create(env: dict[str, Any]) -> None:
    ws = env["ws"]
    ok = call(env["ro_server"], "run_sql", {"workspace_id": ws, "sql": "SELECT count(*) AS n FROM sales"})
    assert ok["rows"] == [[80]]
    denied = call(
        env["ro_server"], "run_analysis", {"workspace_id": ws, "question": "Why was August revenue down?"}
    )
    assert denied["error"]["code"] == "token_read_only"


def test_path_injection_is_refused_before_any_request(env: dict[str, Any]) -> None:
    """R-17: an id argument cannot smuggle another path or query string into the API call."""
    ws = env["ws"]
    for bad in (f"{ws}/relationships/abc/reject?x=", "../workspaces", f"{ws}?x=1", "a b", ""):
        out = call(env["server"], "run_sql", {"workspace_id": bad, "sql": "select 1"})
        assert out["error"]["code"] == "invalid_argument", out
    out = call(env["server"], "get_investigation", {"workspace_id": ws, "investigation_id": "x/../../y"})
    assert out["error"]["code"] == "invalid_argument"
    out = call(env["server"], "inspect_metric", {"workspace_id": ws, "metric_id": "revenue/versions"})
    assert out["error"]["code"] == "invalid_argument"


def test_unexpected_errors_become_tool_errors(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from analystos_mcp.client import AnalystOSClient as Client

    monkeypatch.setattr(Client, "post", lambda self, path, body=None: {"unexpected": "shape"})
    out = call(env["server"], "run_sql", {"workspace_id": env["ws"], "sql": "select 1"})
    assert out["error"]["code"] == "tool_error" and "KeyError" in out["error"]["detail"]


def test_tools_run_concurrently(env: dict[str, Any]) -> None:
    """R-44: tool bodies run in worker threads, so parallel calls all complete."""

    async def many() -> list[Any]:
        return await asyncio.gather(
            *[
                env["server"].call_tool("run_sql", {"workspace_id": env["ws"], "sql": f"SELECT {i} AS n"})
                for i in range(8)
            ]
        )

    results = asyncio.run(many())
    assert len(results) == 8


def test_http_transport_requires_bearer_token(env: dict[str, Any]) -> None:
    """R-44: the streamable-HTTP transport refuses callers without the shared secret and foreign Host headers."""
    from analystos_mcp.server import http_app
    from starlette.testclient import TestClient

    with pytest.raises(ValueError):
        http_app(env["server"], "")
    app = http_app(env["server"], "s" * 32, port=8765)
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as http:
        assert http.post("/mcp", json=init, headers=headers).status_code == 401
        r = http.post("/mcp", json=init, headers={**headers, "Authorization": "Bearer wrong"})
        assert r.status_code == 401
        r = http.post(
            "/mcp",
            json=init,
            headers={**headers, "Authorization": "Bearer " + "s" * 32, "Host": "evil.example.com"},
        )
        assert r.status_code in (400, 403, 421)
        r = http.post("/mcp", json=init, headers={**headers, "Authorization": "Bearer " + "s" * 32})
        assert r.status_code == 200, r.text
