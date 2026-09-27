# Security

AnalystOS treats every dataset as sensitive and every piece of data as untrusted input.

## Deployment model

```
browser ──> web (Next.js, server.mjs, :3000) ──/api proxy──> API (FastAPI, :8000) ──> DuckDB / metadata DB
             records the socket peer            adds X-AOS-Client-Addr + X-AOS-Proxy-Secret
```

* **One entry point.** Browsers talk to the web server only; it proxies `/api/*` to the API
  (`API_URL`, read at runtime). The API port only needs to be reachable by the web server and by
  API/MCP clients.
* **The API knows who the browser is.** `apps/web/server.mjs` records each request's TCP peer
  address (overwriting any client-supplied copy), and the proxy route sends it to the API as
  `X-AOS-Client-Addr`, authenticated with `X-AOS-Proxy-Secret` = `AOS_PROXY_SECRET`. Client-supplied
  `X-AOS-*`, `X-Forwarded-*`, `X-Real-IP` and `Forwarded` headers are dropped. Without the shared
  secret the API ignores the claim; an unauthenticated or non-loopback claim disables local mode for
  that request. Set the same `AOS_PROXY_SECRET` for web and API: `make dev`, `make docker-env` and the
  E2E server generate one. Without it the web server warns and sends no claim, and only binding it to
  loopback keeps other machines out of local mode.
* **Loopback by default.** `pnpm dev`/`pnpm start` bind the web server to `127.0.0.1`; the API binds
  `127.0.0.1` (`AOS_HOST`); Docker Compose publishes both ports on `127.0.0.1` (`AOS_PUBLISH_HOST`).
* **Docker Compose** runs with `AUTH_MODE=password` and `AOS_ENV=production`, and refuses to start
  without `AOS_SECRET_KEY` and `AOS_PROXY_SECRET`. `make docker-env` writes `.env` (mode 0600) with a
  random secret key, proxy secret and first-account **setup token** (`AOS_BOOTSTRAP_TOKEN`).
* The API image defaults to `AOS_ENV=production` and `AUTH_MODE=password`. No-password local mode
  exists only with `AOS_ENV=development`; in production the API refuses to start with `AUTH_MODE=auto`
  or `local` unless `AOS_ALLOW_INSECURE_LOCAL=true`.
* **Serving other machines**: put a TLS reverse proxy in front of the web server, set
  `AOS_PUBLISH_HOST=0.0.0.0` (or publish through the proxy), `AOS_COOKIE_SECURE=true`, and keep
  `AOS_BOOTSTRAP_TOKEN` set until the first account exists. People then join through workspace
  invites.

## Authentication and sessions

* `AUTH_MODE=password` requires accounts. `auto` (the development default) runs as a single
  "Local Analyst" while no password account exists; `local` always does. Both no-password modes only
  sign in requests whose peer is loopback and whose `Host`/`X-Forwarded-Host` is `localhost`,
  `127.0.0.1` or `[::1]`; any other request gets `local_mode_denied` and must sign in.
* In `auto` mode the first sign-up takes over the local analyst's workspaces only when it comes from
  a local session or carries the bootstrap token; otherwise it is refused
  (`signup_requires_local_access`). Claiming revokes the local analyst's sessions.
* Sign-up is open only while no password account exists (`AOS_ALLOW_SIGNUP=true` opens it) and does
  not reveal whether an email is registered. After that, people join through **workspace invites**:
  an owner invites an email with a role (Settings > Members) and hands out a single-use link
  (`/invite/{token}`, only the token's SHA-256 is stored, default expiry `AOS_INVITE_TTL_DAYS` = 7);
  the invitee sets their own password, or signs in first if the email already has an account. Owners
  can list and revoke invites; invalid-token attempts are throttled like failed logins.
* Login throttling: after `AOS_LOGIN_MAX_FAILURES` (5) failures per account or per IP, further
  attempts get `429 too_many_attempts` with `Retry-After`; the lockout starts at
  `AOS_LOGIN_LOCKOUT_S` (30 s) and doubles up to 15 minutes. Failures are audited.
* Passwords: argon2 (`argon2-cffi`), minimum 10 characters, rehashed on login when parameters change,
  constant-time handling for unknown users. Changing the password revokes the user's other sessions
  (and, if asked, their API tokens).
* Sessions are server-side (`auth_sessions` stores only the SHA-256 of a 256-bit token), revocable,
  with a TTL (`AOS_SESSION_TTL_HOURS`, default 14 days). Cookie `aos_session` is HttpOnly,
  SameSite=Lax, and Secure when `AOS_COOKIE_SECURE=true` (set it behind HTTPS).
* CSRF: unsafe requests must send `X-CSRF-Token` equal to the `aos_csrf` cookie **and** to the token
  stored with the session.
* API tokens (for MCP and automation): `aos_` + 256 random bits, stored as SHA-256, shown once,
  minted only from an interactive session, optionally restricted to one workspace, read-only and
  expiring. Read-only tokens can only GET plus explicitly marked read-only POSTs (running queries,
  explore, analyses, interpret).
* The MCP server's HTTP transport requires a bearer token (`AOS_MCP_HTTP_TOKEN`) and checks the
  `Host` header (see [mcp.md](mcp.md)).

## Authorization and isolation

Roles per workspace: viewer, editor, owner. Every resource lives under
`/api/v1/workspaces/{ws}/...`; non-members get 404, so workspace existence is not revealed. Each
workspace has its own DuckDB file under `DATA_DIR/workspaces/{id}/`. The last owner cannot be removed.

## Secrets

The master secret `AOS_SECRET_KEY` is required when `AOS_ENV=production` (docker-compose enforces it;
`make docker-env` generates one). In development a key is generated once into `DATA_DIR/.secret_key`
(mode 0600). Connector credentials and workspace AI keys are encrypted with Fernet (key derived from
the master secret), are write-only in the API and never appear in responses, logs, audit entries or
diagnostics. Logs pass through a redaction processor for secret-looking keys and values (API tokens,
Anthropic keys, PEM keys, credentials in URLs).

## Read-only data access

* Every SQL string, from the SQL editor, the compiler, the investigator, the LLM or a connector,
  passes `ensure_read_only` (sqlglot): exactly one `SELECT` / set operation / `VALUES` statement;
  DDL, DML, `COPY`, `ATTACH`, `INSTALL`/`LOAD`, `PRAGMA`, `SET`, transactions, `CALL`, file paths used
  as tables, and file or system functions (`read_*`, `pg_*`, `xp_*`, `openrowset`, ...) are rejected
  with `UnsafeSQLError`. User SQL may only call the table functions `range`, `generate_series`,
  `unnest` and `generate_subscripts`, and functions that execute or expose SQL or server state
  (`json_execute_serialized_sql`, `json_serialize_sql`, `checkpoint`, `current_setting`,
  `getvariable`, `duckdb_*`, `pragma_*`) are blocked (`blocked_function`). Rejections are recorded in
  query history.
* Workspace stores run DuckDB with external access disabled, extension autoloading off and the
  configuration locked, a 30 s default timeout (interrupted) and a row cap.
* External databases get read-only sessions where the database supports it (see
  [connectors.md](connectors.md)).
* Data-quality rules and suggested fixes never modify data.

## File uploads

Size cap (`AOS_MAX_UPLOAD_MB`, default 200, streamed), extension allow-list, content sniffing
(Parquet magic bytes, `.xlsx` must be a ZIP with `xl/` parts and bounded expansion, text files must
not contain NUL bytes), sanitised filenames, files stored with mode 0600 and opened exclusively.

## Python sandbox

`analystos_engine.sandbox.run_python` runs code in a separate `python -I` process in its own session
with a clean environment and a private 0700 scratch directory:

* user and network namespaces (no network), Landlock (read-only access to Python and system
  libraries, read-write only to scratch, no exec, no TCP), an audit hook that blocks process creation,
  sockets and out-of-root file access, and an import allowlist;
* rlimits on CPU, address space, file size (64 MB), open files (256) and no core dumps, a wall-clock
  timeout (default 30 s) and a resident-memory watchdog (default 1024 MB), output capped at 1 MB;
* data arrives as a read-only DuckDB connection.

This is defence in depth on one host, **not a virtual machine**. Kernel features (Landlock, user
namespaces) depend on the host; for multi-tenant deployments run the API in a container with its own
limits.

## Prompt injection

Data values are delimited, encoded and labelled as untrusted before any LLM sees them; the model
cannot supply tool results; generated text is checked against executed numbers. See
[ai-architecture.md](ai-architecture.md). No data is sent to an external model unless an operator
enables AI and configures a key.

## HTTP hardening and audit

Responses carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: same-origin` and `Cache-Control: no-store`. CORS allows only configured origins.
Every request has a request id that appears in logs, audit entries and error bodies. The audit log
records sign-ins (including failures) and sign-outs, API token and membership changes, workspace,
data-source, upload, dataset and data-quality rule changes, semantic model and metric versions,
investigation actions (create, disambiguate, plan edits, run, drill, node actions), finding status and
comments, reports, dashboards, saved queries, AI settings, demo loads and exports.

## Known gaps

No Content-Security-Policy or HSTS header is set by the API (set them at your reverse proxy), and
there is no SSO. Throttling covers sign-in (per account and per address), failed sign-ups and invalid
invite tokens (per address); other endpoints have no rate limits.
