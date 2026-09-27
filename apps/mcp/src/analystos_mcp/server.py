"""AnalystOS MCP server (official ``mcp`` Python SDK): the spec section 69 tools over the REST API.

Tools: list_workspaces, inspect_dataset, inspect_metric, run_analysis, run_sql, get_investigation,
get_findings, build_report. Each call uses the configured API token (``AOS_API_TOKEN``), so the
token's user, role, workspace restriction and read-only flag govern what the tools may do.
Numbers returned by tools come from the AnalystOS engine; tools never compute or invent results.
"""

from __future__ import annotations

import argparse
import hmac
import os
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import anyio
from mcp.server.mcpserver import MCPServer

from .client import AnalystOSClient, ApiError

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def seg(value: str, what: str = "id") -> str:
    """One URL path segment from a tool argument. Ids are validated and escaped, so an argument such as
    ``"<ws>/relationships/<id>/reject?x="`` can never reach a different endpoint (review R-17)."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ApiError(422, "invalid_argument", f"{what} must match [A-Za-z0-9_-]{{1,64}}")
    return quote(value, safe="")


INSTRUCTIONS = (
    "AnalystOS is an analytical workbench. Use list_workspaces first, then inspect_dataset / inspect_metric to learn "
    "the governed definitions. Use run_analysis for 'why did X change' questions: it builds a computed investigation "
    "tree where every number comes from executed SQL. Report numbers exactly as returned, keep the statement types "
    "(observation / supported explanation / hypothesis) and never present a hypothesis as a fact. run_sql only "
    "accepts read-only SELECT queries."
)


def _compact_tree(inv: dict[str, Any], max_nodes: int = 60) -> list[dict[str, Any]]:
    tree = inv.get("tree") or {}
    nodes = tree.get("nodes") or []
    out = []
    for n in sorted(nodes, key=lambda n: (n.get("depth", 0), n.get("rank") or 99))[:max_nodes]:
        c = n.get("contribution_to_parent") or {}
        out.append(
            {
                "id": n["id"],
                "parent_id": n.get("parent_id"),
                "depth": n.get("depth"),
                "kind": n.get("kind"),
                "statement": n["statement"],
                "statement_type": n.get("statement_type"),
                "evidence_strength": n.get("evidence_strength"),
                "status": n.get("status"),
                "current": n.get("current"),
                "baseline": n.get("baseline"),
                "abs_change": n.get("abs_change"),
                "pct_change": n.get("pct_change"),
                "share_of_parent_change": c.get("share"),
                "artifact_ids": n.get("artifact_ids", []),
            }
        )
    return out


def _investigation_view(inv: dict[str, Any], include_tree: bool = True) -> dict[str, Any]:
    view = {
        "id": inv["id"],
        "question": inv["question"],
        "status": inv["status"],
        "brief_answer": inv.get("brief_answer"),
        "followups": inv.get("followups", []),
        "failures": inv.get("failures", []),
        "metric_versions": inv.get("metric_version_ids", {}),
        "dataset_versions": inv.get("dataset_versions", {}),
        "run_count": inv.get("run_count"),
    }
    interp = inv.get("interpretation") or {}
    if interp.get("ambiguous"):
        view["ambiguous"] = [
            {"term": a["term"], "candidates": [c["metric_id"] for c in a["candidates"]]}
            for a in interp["ambiguous"]
        ]
    if inv.get("plan"):
        view["plan"] = [
            {"id": s["id"], "title": s["title"], "enabled": s["enabled"]} for s in inv["plan"]["steps"]
        ]
    if include_tree:
        view["tree"] = _compact_tree(inv)
    return view


def build_server(client: AnalystOSClient) -> MCPServer:
    server = MCPServer(name="analystos", title="AnalystOS", instructions=INSTRUCTIONS, version="0.1.0")

    def call(fn: Callable[[], Any]) -> Any:
        """Run a tool body; API errors and unexpected failures become a structured ``{"error": ...}`` result
        instead of an exception escaping into the MCP session."""
        try:
            return fn()
        except ApiError as exc:
            return {"error": {"status": exc.status, "code": exc.code, "detail": exc.detail}}
        except Exception as exc:  # noqa: BLE001 - a tool must always answer with a result the model can read
            return {"error": {"status": 500, "code": "tool_error", "detail": f"{type(exc).__name__}: {exc}"}}

    async def in_thread(fn: Callable[[], Any]) -> Any:
        """Tool bodies block on HTTP; run them in a worker thread so concurrent calls do not serialise."""
        return await anyio.to_thread.run_sync(lambda: call(fn))

    @server.tool(description="List the workspaces the API token can access (id, name, role).")
    async def list_workspaces() -> dict[str, Any]:
        return await in_thread(lambda: {"workspaces": client.get("/workspaces")})

    @server.tool(
        description="Inspect a dataset by id or table name: columns, row count, versions, profile "
        "summary and data-quality issues. Without 'dataset' lists all datasets."
    )
    async def inspect_dataset(
        workspace_id: str, dataset: str | None = None, preview_rows: int = 5
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            base = f"/workspaces/{seg(workspace_id, 'workspace_id')}"
            datasets = client.get(f"{base}/datasets")
            if not dataset:
                return {
                    "datasets": [
                        {
                            "id": d["id"],
                            "name": d["name"],
                            "table": d["table_name"],
                            "rows": d["row_count"],
                            "issues": d["issue_count"],
                        }
                        for d in datasets
                    ]
                }
            match = next((d for d in datasets if dataset in (d["id"], d["table_name"], d["name"])), None)
            if match is None:
                return {"error": {"status": 404, "code": "not_found", "detail": f"No dataset {dataset!r}"}}
            out: dict[str, Any] = {
                "dataset": match,
                "versions": client.get(f"{base}/datasets/{match['id']}/versions")[:5],
                "usage": client.get(f"{base}/datasets/{match['id']}/usage"),
            }
            out["usage"].pop("lineage", None)
            try:
                prof = client.get(f"{base}/datasets/{match['id']}/profile")
                out["profile"] = {
                    "row_count": prof["row_count"],
                    "duplicate_rows": prof.get("duplicate_row_count"),
                    "columns": [
                        {
                            k: c.get(k)
                            for k in (
                                "name",
                                "inferred_type",
                                "null_pct",
                                "distinct_count",
                                "min",
                                "max",
                                "semantic_roles",
                            )
                        }
                        for c in prof["columns"]
                    ],
                    "issues": prof.get("issues", [])[:25],
                }
            except ApiError as exc:
                out["profile"] = {"status": exc.detail}
            if preview_rows > 0:
                out["preview"] = client.get(
                    f"{base}/datasets/{match['id']}/preview", limit=min(preview_rows, 50)
                )
            return out

        return await in_thread(run)

    @server.tool(
        description="Inspect a governed metric: definition, current version, version history, lineage "
        "and recent example values. Without 'metric_id' lists all metrics."
    )
    async def inspect_metric(workspace_id: str, metric_id: str | None = None) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            base = f"/workspaces/{seg(workspace_id, 'workspace_id')}"
            if not metric_id:
                return {
                    "metrics": [
                        {
                            "id": m["id"],
                            "label": m.get("label") or m["name"],
                            "kind": m["kind"],
                            "format": m["format"],
                            "version_no": m["version_no"],
                            "description": m["description"],
                        }
                        for m in client.get(f"{base}/metrics")
                    ]
                }
            out: dict[str, Any] = {
                "metric": client.get(f"{base}/metrics/{seg(metric_id, 'metric_id')}"),
                "versions": client.get(f"{base}/metrics/{seg(metric_id, 'metric_id')}/versions")[:10],
                "lineage": client.get(f"{base}/metrics/{seg(metric_id, 'metric_id')}/lineage"),
            }
            try:
                ex = client.get(f"{base}/metrics/{seg(metric_id, 'metric_id')}/examples", periods=6)
                out["examples"] = {"points": ex["points"], "sql": ex["compiled"]["sql"]}
            except ApiError as exc:
                out["examples"] = {"status": exc.detail}
            return out

        return await in_thread(run)

    @server.tool(
        description="Investigate a business question (e.g. 'Why was August revenue down?'). Interprets it "
        "against the semantic model, plans, executes real SQL and returns the investigation "
        "tree with evidence. Ambiguous metrics return candidates: call again with 'choices'. "
        "Set approve=false to only return the plan."
    )
    async def run_analysis(
        workspace_id: str,
        question: str,
        approve: bool = True,
        choices: dict[str, str] | None = None,
        timeout_s: float = 300.0,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            base = f"/workspaces/{seg(workspace_id, 'workspace_id')}/investigations"
            inv = client.post(base, {"question": question, "choices": choices or {}, "auto_run": approve})
            if inv["status"] == "awaiting_approval" and approve:
                acc = client.post(f"{base}/{inv['id']}/run")
                job = client.wait_job(acc["poll_url"], timeout_s)
                if job["status"] == "failed":
                    return {
                        "investigation_id": inv["id"],
                        "error": {"status": 500, "code": "run_failed", "detail": job.get("error")},
                    }
                inv = client.get(f"{base}/{inv['id']}")
            return _investigation_view(inv)

        return await in_thread(run)

    @server.tool(
        description="Run a read-only SQL query (DuckDB dialect) against the workspace. Only a single "
        "SELECT/WITH query is accepted; parameters use $name markers."
    )
    async def run_sql(
        workspace_id: str, sql: str, params: dict[str, Any] | None = None, limit: int = 200
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            res = client.post(
                f"/workspaces/{seg(workspace_id, 'workspace_id')}/queries/run",
                {
                    "sql": sql,
                    "params": params or {},
                    "limit": max(1, min(limit, 5000)),
                    "suggest_chart": False,
                },
            )
            return {
                "query_run_id": res["id"],
                "columns": res["result"]["columns"],
                "rows": res["result"]["rows"],
                "row_count": res["result"]["row_count"],
                "truncated": res["result"]["truncated"],
                "elapsed_ms": res["elapsed_ms"],
                "warnings": res.get("warnings", []),
                "dataset_versions": res.get("dataset_versions", {}),
            }

        return await in_thread(run)

    @server.tool(
        description="Get an investigation: status, brief answer, plan, tree (statements with evidence "
        "strength and contributions) and optionally the SQL behind each node."
    )
    async def get_investigation(
        workspace_id: str, investigation_id: str, include_sql: bool = False
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            base = f"/workspaces/{seg(workspace_id, 'workspace_id')}/investigations/{seg(investigation_id, 'investigation_id')}"
            view = _investigation_view(client.get(base))
            if include_sql:
                view["artifacts"] = [
                    {
                        "id": a["id"],
                        "engine_id": a["engine_id"],
                        "title": a["title"],
                        "sql": a["sql"],
                        "filter_context": a["filter_context"],
                    }
                    for a in client.get(f"{base}/artifacts")
                    if a.get("sql")
                ]
            return view

        return await in_thread(run)

    @server.tool(
        description="List findings (evidence-backed statements) with status, statement type, evidence "
        "strength, values and filter context."
    )
    async def get_findings(
        workspace_id: str,
        status: str | None = None,
        investigation_id: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            rows = client.get(
                f"/workspaces/{seg(workspace_id, 'workspace_id')}/findings",
                status=status,
                investigation_id=investigation_id,
                q=query,
            )
            return {
                "findings": [
                    {
                        k: f.get(k)
                        for k in (
                            "id",
                            "statement",
                            "statement_type",
                            "evidence_strength",
                            "evidence_reasons",
                            "status",
                            "values",
                            "filter_context",
                            "investigation_id",
                            "artifact_ids",
                        )
                    }
                    for f in rows
                ]
            }

        return await in_thread(run)

    @server.tool(
        description="Build a report and return it as Markdown. Provide investigation_id (investigation "
        "report), or period like '2026-08' (monthly business review), or neither for an "
        "executive summary of confirmed findings."
    )
    async def build_report(
        workspace_id: str,
        investigation_id: str | None = None,
        period: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            base = f"/workspaces/{seg(workspace_id, 'workspace_id')}"
            if investigation_id:
                rep = client.post(
                    f"{base}/reports/from-investigation", {"investigation_id": investigation_id}
                )
            elif period:
                rep = client.post(f"{base}/reports/business-review", {"period": period, "title": title})
            else:
                rep = client.post(f"{base}/reports/executive-summary", {"title": title})
            md = client.request(
                "POST", f"{base}/exports", json={"target": "report", "id": rep["id"], "format": "md"}
            )
            return {"report_id": rep["id"], "title": rep["title"], "status": rep["status"], "markdown": md}

        return await in_thread(run)

    return server


class BearerAuth:
    """ASGI middleware for the HTTP transport: every request needs ``Authorization: Bearer <AOS_MCP_HTTP_TOKEN>``.

    The MCP server acts with the configured AnalystOS API token, so an unauthenticated HTTP endpoint would lend
    that token to anyone who can reach the port (review R-44)."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            got = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(got, self.expected):
                body = b'{"error": "unauthorized", "detail": "Bearer token required (AOS_MCP_HTTP_TOKEN)"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", b'Bearer realm="analystos-mcp"'),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def http_app(
    server: MCPServer,
    token: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    extra_hosts: list[str] | None = None,
) -> Any:
    """The streamable-HTTP ASGI app with bearer auth and DNS-rebinding protection (Host/Origin allow-list)."""
    from mcp.server.transport_security import TransportSecuritySettings

    if not token or len(token) < 16:
        raise ValueError("AOS_MCP_HTTP_TOKEN must be set (at least 16 characters) to serve MCP over HTTP")
    names = {host, "127.0.0.1", "localhost", "[::1]", *(extra_hosts or [])}
    hosts = sorted({f"{h}:{port}" for h in names} | names)
    origins = sorted({f"http://{h}" for h in hosts} | {f"https://{h}" for h in hosts})
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )
    app = server.streamable_http_app(transport_security=security, host=host)
    return BearerAuth(app, token)


def main(argv: list[str] | None = None) -> None:
    """Console script ``analystos-mcp``: stdio (default) or streamable HTTP transport."""
    parser = argparse.ArgumentParser(prog="analystos-mcp", description=__doc__.split("\n\n")[0])
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Extra Host header value accepted by the HTTP transport (DNS-rebinding protection)",
    )
    parser.add_argument("--api-url", default=None, help="AnalystOS API base URL (env AOS_API_URL)")
    args = parser.parse_args(argv)
    client = AnalystOSClient(base_url=args.api_url)
    if not client.token:
        parser.error("AOS_API_TOKEN is not set; create an API token in AnalystOS settings")
    server = build_server(client)
    if args.transport == "stdio":
        server.run("stdio")
        return
    token = os.environ.get("AOS_MCP_HTTP_TOKEN", "")
    try:
        app = http_app(server, token, args.host, args.port, args.allowed_host)
    except ValueError as exc:
        parser.error(str(exc))
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
