# MCP server

`analystos_mcp` (in `apps/mcp`) exposes AnalystOS to external agents through the Model Context
Protocol, using the official `mcp` Python SDK. It is a thin client of the REST API: every tool call is
an authenticated API request, so the numbers an agent sees come from the AnalystOS engine and never
from the MCP layer.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_workspaces` | | Workspaces the token can access (id, name, role) |
| `inspect_dataset` | `workspace_id`, `dataset` (id or table name; omit to list), `preview_rows` | Columns, row count, versions, profile issues, a small preview |
| `inspect_metric` | `workspace_id`, `metric_id` (omit to list) | Governed definition, current version, version history, lineage |
| `run_analysis` | `workspace_id`, `question`, `approve` (default true; false returns only the plan), `choices` (resolve ambiguous terms), `timeout_s` | An investigation: status, brief answer, plan, compact tree with statement types and evidence strength, or the ambiguity to resolve |
| `run_sql` | `workspace_id`, `sql`, `params`, `limit` | Read-only query result (single SELECT, DuckDB dialect; anything else is rejected by the API) |
| `get_investigation` | `workspace_id`, `investigation_id`, `include_sql` | Status, brief answer, plan, tree, optionally each node's SQL |
| `get_findings` | `workspace_id`, `status`, `investigation_id`, `query` | Findings with status, statement type, evidence strength and reasons, values, filter context and artifact ids |
| `build_report` | `workspace_id`, `investigation_id` (investigation report), or `period` such as `2026-08` (business review), or neither (executive summary of confirmed findings), `title` | The report as Markdown |

The server's instructions tell agents to report numbers exactly as returned, keep the statement types
(observation, supported explanation, hypothesis) and never present a hypothesis as a fact.

## Running it

1. In AnalystOS, open **Settings > API tokens** and create a token. For an agent that should only
   read and analyse, choose *read-only* and restrict it to one workspace.
2. Run the server:

```bash
export AOS_API_URL=http://127.0.0.1:8000     # default
export AOS_API_TOKEN=aos_...
uv run analystos-mcp                          # stdio transport (default)
AOS_MCP_HTTP_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))") \
  uv run analystos-mcp --transport streamable-http --host 127.0.0.1 --port 8765
```

The HTTP transport refuses to start without `AOS_MCP_HTTP_TOKEN` (at least 16 characters): every
HTTP request must send `Authorization: Bearer <AOS_MCP_HTTP_TOKEN>`, because the server acts with the
configured AnalystOS API token and would otherwise lend it to anyone who can reach the port. It also
enables DNS-rebinding protection: only `Host`/`Origin` values for the bind address, `127.0.0.1`,
`localhost` and `[::1]` are accepted; add others with `--allowed-host name[:port]`. Tool calls that
reach the API run in worker threads, so concurrent calls do not serialize. Workspace ids and other
path arguments are validated before they are put into API URLs.

Example client configuration (stdio):

```json
{
  "mcpServers": {
    "analystos": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/analytics-workbench", "analystos-mcp"],
      "env": { "AOS_API_URL": "http://127.0.0.1:8000", "AOS_API_TOKEN": "aos_..." }
    }
  }
}
```

## Permissions

The MCP server has no privileges of its own. It sends `Authorization: Bearer <token>` on every call,
so the token's user, workspace role, workspace restriction and read-only flag apply exactly as in the
API: other workspaces return "not found", and a read-only token can run queries and analyses that are
marked read-only but cannot create or change content. Tokens are stored hashed server-side, can
expire, and can be revoked at any time.

## Tests

`apps/mcp/tests/test_mcp_server.py` runs the server's tools against an in-process API: tool
registration, workspace permission enforcement, dataset and metric inspection plus SQL, an analysis
through to findings and a report, and a read-only token that can query but cannot create.
