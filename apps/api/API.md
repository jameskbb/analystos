# AnalystOS REST API (v1)

Base path: `/api/v1`. Interactive docs: `/api/docs` (Swagger), `/api/redoc`; machine-readable
schema: `/api/openapi.json` (every operation has an `operationId`, tags and typed response
models, so a client can be generated). This file is the contract the web app codes against.
The OpenAPI document is the source of truth for field-level detail; this file lists every
endpoint, its purpose and its main shapes.

## Conventions

* **Workspace scoping.** Every resource lives in a workspace:
  `/api/v1/workspaces/{ws}/...`. A caller who is not a member gets `404` (not `403`) so the
  existence of other workspaces is not revealed.
* **Roles.** `viewer` (read, run read-only queries/analyses, export), `editor` (create and
  change content), `owner` (members, settings, data-source credentials, delete workspace).
  Insufficient role → `403 insufficient_role`.
* **Errors.** Always `{"detail": str, "code": str, "request_id": str|null, "errors": list|object|null}`.
  Common codes: `not_authenticated`, `csrf_failed`, `insufficient_role`, `not_found`,
  `validation_error` (422, `errors` = `[{loc, msg, type}]`), `unsafe_sql` (400),
  `query_failed` (400), `query_timeout` (408), `payload_too_large` (413),
  `unsupported_file_type` / `content_mismatch` (422), `compile_error` / `grain_error` (422),
  `ai_disabled` (409), `conflict` codes such as `metric_exists`, `table_exists`.
* **Request ids.** Send `X-Request-ID` (≤64 chars `[A-Za-z0-9._-]`) or one is generated; it is
  echoed in the response header and in error bodies, and attached to every log line, audit
  entry and diagnostic event.
* **Jobs.** Long operations return `202 JobAccepted {job: Job, poll_url}`. Poll
  `GET /workspaces/{ws}/jobs/{job_id}` until `status` is `succeeded` (see `result`) or
  `failed` (see `error`). `Job = {id, kind, status: queued|running|succeeded|failed, progress 0..1,
  message, params, result, error, resource_type, resource_id, created_at, started_at, finished_at}`.
* **Tabular results.** `TabularResult = {columns: [{name, type}], rows: [[...]], row_count,
  truncated, elapsed_ms, sql}`; rows are positional lists aligned with `columns`. Dates are ISO strings.
* **Lineage graphs.** `LineageGraph = {nodes: [{id, kind, label, detail, ref_id, meta}], edges: [{from, to, label}]}`.
  Node kinds: `finding, artifact, chart, query, metric, entity, dataset, source_file, source_table,
  investigation, kpi, dashboard, tile`.
* **Filter context.** Every artifact/finding/tile carries `filter_context: [{label, value, kind, dimension, op, values}]`
  (`kind`: `time | filter | segment | metric`) so no filter is hidden (spec §47).
* **Timestamps** are UTC ISO-8601 with `Z`/offset.

## Authentication and CSRF

| Mechanism | How | CSRF |
|---|---|---|
| Session cookie | `aos_session` (HttpOnly, SameSite=Lax, Path=/, `Secure` when `AOS_COOKIE_SECURE=true`), opaque 256-bit token; only its SHA-256 is stored server-side (`auth_sessions`), revocable, TTL `AOS_SESSION_TTL_HOURS` (default 14 days). | **Required** on POST/PUT/PATCH/DELETE: send header `X-CSRF-Token` equal to the `aos_csrf` cookie (readable by JS, SameSite=Lax). The server compares the header with both the cookie *and* the token stored for the session (double submit bound to the session). Missing/mismatch → `403 csrf_failed`. |
| API token | `Authorization: Bearer aos_...`. Created from an interactive session only; shown once; stored as SHA-256. Optional `workspace_id` restriction and `read_only` flag (read-only tokens may only GET plus the read-only POSTs marked `x-read-only-post` in OpenAPI: running queries, explore, python, charts suggest, validate). | Not needed (never sent automatically by browsers). |

`AUTH_MODE`: `auto` (default: local single-user mode while no password account exists), `local` (always the
local analyst), `password` (accounts required).

**Local mode is development-only and machine-local.** It exists only when `AOS_ENV=development`; in any other
environment `auto`/`local` behave as `password` (unless `AOS_ALLOW_INSECURE_LOCAL=true`). It signs a request in as
the local analyst only when all hold: the TCP peer is loopback; `Host` (plus `X-Forwarded-Host`, if set) is
`localhost`, `*.localhost`, `127.x.x.x` or `[::1]`; and either there is no `X-AOS-Client-Addr` header, or that
header is authenticated by `X-AOS-Proxy-Secret` equal to `AOS_PROXY_SECRET` and names a loopback client. The web
server's proxy sends `X-AOS-Client-Addr: <real browser address>` and `X-AOS-Proxy-Secret: <AOS_PROXY_SECRET>` on
every proxied request; an unauthenticated or non-loopback `X-AOS-Client-Addr` disables local mode for that
request. The client address used for login/sign-up/invite throttling, audit entries and request logs is the
authenticated `X-AOS-Client-Addr` (first address) when `X-AOS-Proxy-Secret` verifies, otherwise the socket peer,
so browsers behind the proxy are throttled individually and the header cannot be spoofed. Other callers get
`authenticated: false, local_mode_denied: true`, and a local-analyst session cookie is
refused (`401`) from a non-local request. `AOS_ALLOW_INSECURE_LOCAL=true` drops all of these checks; without it
the server refuses to start with `AUTH_MODE=auto|local` when `AOS_ENV=production`.

**First account.** It can always be created. In `auto` mode during development it must come from this machine or
carry `AOS_BOOTSTRAP_TOKEN` (`403 signup_requires_local_access`), and it takes over the local analyst's workspaces
only when the request carries the local analyst's session from this machine, or the bootstrap token; every local
session is then revoked. Otherwise it is a fresh account with no workspaces. When `AOS_BOOTSTRAP_TOKEN` is set,
the first account always needs it (`403 bootstrap_token_required`).

**Joining a team: invites.** After the first account, self-service sign-up is closed (`403 signup_disabled`);
owners invite people (below). `AOS_ALLOW_SIGNUP=true` is an escape hatch that reopens sign-up (default false).

**Throttling.** `POST /auth/login` counts failures per account and per client address. After
`AOS_LOGIN_MAX_FAILURES` (5) failures for an account (4x that for an address) further attempts get
`429 too_many_attempts` with `Retry-After`; the lock starts at `AOS_LOGIN_LOCKOUT_S` (30 s) and doubles per
further failure up to 15 min. Every failure is audited (`auth.login_failed`, `auth.login_throttled`). Sign-up
failures are throttled per address too.

| Method | Path | Body → Response |
|---|---|---|
| GET | `/auth/session` | → `SessionInfo {authenticated, auth_mode: local\|password, user?, csrf_token?, signup_allowed, default_workspace_id?, local_mode_denied, bootstrap_token_required}`. In local mode this signs the local analyst in and sets cookies (local requests only). |
| GET | `/auth/invites/{token}` | No auth; the token is the credential → `InvitePreview {workspace_name, email, role, expires_at, account_exists}`. Unknown, used, revoked or expired tokens → `404 invite_invalid`; repeated bad tokens from one address → `429 too_many_attempts` |
| POST | `/auth/invites/accept` | `{token, name?, password?}`. New person: `name` + `password` (≥10) create the account (with the invited email), the membership with the invited role and a session → `SessionInfo` (201, `default_workspace_id` = the workspace). If an account with that email exists, sign in as it and send `{token}` with the session cookie and `X-CSRF-Token` → `SessionInfo` (200); otherwise `409 account_exists`. `400 password_required` when name/password are missing for a new account. The token works once. |
| POST | `/auth/signup` | `{email, name, password (≥10), bootstrap_token?}` → `SessionInfo` (201). Errors: `400 signup_failed` (generic; does not reveal whether the email exists), `403 signup_disabled \| signup_requires_local_access \| bootstrap_token_required`, `429 too_many_attempts` |
| POST | `/auth/login` | `{email, password}` → `SessionInfo`; `401 invalid_credentials`; `429 too_many_attempts` (+ `Retry-After`) |
| POST | `/auth/logout` | → `{ok}` (revokes the session) |
| GET / PATCH | `/auth/me` | → `User {id, email, name, is_local, created_at, last_login_at}`; PATCH `{name?, current_password?, new_password?, revoke_tokens?}`. A password change revokes every other session of the user; `revoke_tokens: true` also revokes all API tokens. |
| GET | `/auth/tokens` | → `ApiToken[] {id, name, prefix, workspace_id, read_only, created_at, last_used_at, expires_at, revoked_at}` |
| POST | `/auth/tokens` | `{name, workspace_id?, read_only?, expires_in_days?}` → `{token, meta: ApiToken}` (201; session only) |
| DELETE | `/auth/tokens/{token_id}` | → `{ok}` |

## Platform

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health`, `/api/v1/health` | `{status, version}` (no auth) |
| GET | `/api/v1/diagnostics` | System info: versions, DB dialect, migration head, AI configured, job pool, WeasyPrint availability |
| GET | `/data-sources/kinds` | Connector kinds `[{kind, label, config_schema (JSON schema), secret_fields}]` |
| GET | `/demo/status` | `{available, workspaces: [{id, name}]}` for the Summit Supply demo |
| POST | `/demo/load` | `{workspace_id?, reset?: bool, name?}` → `202 DemoLoadAccepted {workspace_id, job, poll_url}`. Creates (or reuses/resets) the "Summit Supply Co." workspace, ingests the generated data, persists the semantic model (entities, dimensions, versioned metrics, trees, glossary), approved relationships and DQ rules, profiles every dataset, measures every approved join and runs the rules. Job result `{workspace_id, tables, semantic, relationship_suggestions, dq_rules_failing, joins_analyzed, join_cardinality_mismatches, content_hash, reference_date, failures[{step, table, error}]}`: a table whose registration, profiling, join analysis or rule fails is listed in `failures` (its dataset gets `profile_status: failed`) and the load continues. |

## Workspaces, members, settings, jobs, home

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/workspaces` | → `Workspace[] {id, name, description, role, created_at, updated_at}`; POST `{name, description?}` |
| GET / PATCH / DELETE | `/workspaces/{ws}` | PATCH/DELETE: owner. DELETE removes the workspace's DuckDB store and files. |
| GET | `/workspaces/{ws}/home` | `HomeSummary {workspace, counts, datasets[{id, name, table_name, row_count, updated_at, profile_status, issue_count}], recent_investigations[], recent_findings[], quality_failures[], dashboards[], pending_relationships, running_jobs[], changes[{metric_id, label, format, period, current, baseline, abs_change, pct_change, sql}]}`. `changes` = headline metrics, last full month in the data up to the day before the reference date (workspace `investigation.reference_date`, else today; rows dated later are ignored) vs the prior month, computed by the engine and ordered by materiality (canonical revenue first); `changes_note` says which months |
| GET / PATCH | `/workspaces/{ws}/settings` | `{calendar, ai, investigation}` (PATCH: owner) |
| GET / POST | `/workspaces/{ws}/members` | → `Member[] {user_id, email, name, role, created_at}`; POST `{email, role}` (owner; the user must already have an account, otherwise use an invite) |
| PATCH / DELETE | `/workspaces/{ws}/members/{user_id}` | `{role}`; the last owner cannot be demoted/removed |
| GET | `/workspaces/{ws}/jobs?status&limit` / `/jobs/{job_id}` | Job polling |
| GET / POST | `/workspaces/{ws}/invites` | Owner. POST `{email, role: owner\|editor\|viewer = viewer, expires_in_days? (1..90, default AOS_INVITE_TTL_DAYS = 7)}` → `InviteCreated {token, accept_path: "/invite/{token}", invite: Invite}` (201; the token is shown once and only its SHA-256 is stored; a new invite replaces a pending one for the same email; `409 already_member`). GET → `Invite[] {id, email, role, status: pending\|accepted\|revoked\|expired, created_by, created_at, expires_at, accepted_at, revoked_at}`. Audited as `invite.create`, `invite.revoke`, `invite.accept`, `invite.accept_failed`. |
| DELETE | `/workspaces/{ws}/invites/{invite_id}` | Owner: revoke a pending invite (`409 invite_accepted` once used) |

## Data sources (external databases, read-only)

Credentials go in `secrets` and are encrypted with Fernet (key derived from `AOS_SECRET_KEY`); they
are never returned, logged or included in audit details (`secret_fields` lists stored names only).

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/workspaces/{ws}/data-sources` | POST (owner) `{name, kind: postgres\|mysql\|sqlserver\|snowflake\|bigquery, description?, config, secrets}` → `DataSource {id, name, kind, description, config, secret_fields, status, last_tested_at, last_test_message, ...}` |
| POST | `/workspaces/{ws}/data-sources/test` | Test unsaved settings → `ConnectionTest {ok, message, latency_ms, server_version, read_only_enforced, details}`. Secret fields in `config` → `422 secret_in_config` (as on create); error messages never echo submitted values. |
| GET / PATCH / DELETE | `/workspaces/{ws}/data-sources/{id}` | PATCH `{name?, description?, config?, secrets?: {key: value\|null}}` (null removes a secret) |
| POST | `.../{id}/test` | Test saved source; updates `status` |
| GET | `.../{id}/schemas`, `.../{id}/tables?schema=`, `.../{id}/tables/{schema}/{table}/columns`, `.../{id}/tables/{schema}/{table}/preview?limit` | Discovery and preview (read-only) |
| POST | `.../{id}/import` | `{schema_name, table, dataset_name?, table_name?, row_limit?}` → `202 JobAccepted` (result `{dataset_id, table_name, rows, truncated}`); snapshots the table into the workspace store |

## Datasets (files, profile, versions, lineage)

Uploads: max `AOS_MAX_UPLOAD_MB` (default 200) → `413`; extension must be `.csv .tsv .txt .xlsx .xlsm
.parquet .pq .json .ndjson .jsonl` and content must match (magic bytes / text sniff) → `422`.
Files are stored under `DATA_DIR/workspaces/{ws}/uploads/{upload_id}/`, mode 0600, and only ever read.

| Method | Path | Notes |
|---|---|---|
| POST | `/workspaces/{ws}/datasets/uploads` | multipart field `file` → `Upload {id, filename, size_bytes, sha256, file_kind, status, inspection: FileInspection, created_at}` (201). `inspection` is the engine's `FileInspection` (dialect/encoding, sheets with header row, title/empty rows, table blocks, inferred types, preview rows, warnings). |
| GET | `/workspaces/{ws}/datasets/uploads`, `.../uploads/{id}` | |
| POST | `.../uploads/{id}/inspect` | `{options: IngestOptions (sheet, table_key, header_row, delimiter, encoding, column_types, ...), limit (1..1000)}` → `{upload_id, options, preview: FileInspection}`: the options are applied exactly as ingest applies them (so the preview equals what will be loaded) and each table has at most `limit` preview rows; nothing is ingested |
| POST | `.../uploads/{id}/ingest` | `{dataset_name?, table_name?, description?, if_exists: fail\|replace\|append, options: IngestOptions}` → `202 JobAccepted`; job result `{dataset_id, table_name, version_id, new_relationship_suggestions}`. Profiling and relationship discovery run as part of the job. |
| DELETE | `.../uploads/{id}` | |
| GET | `/workspaces/{ws}/datasets` | → `Dataset[] {id, name, table_name, description, source_kind: upload\|connector\|demo, source_ref, data_source_id, row_count, columns[{name, type, nullable}], profile_status: pending\|running\|ready\|failed, profiled_at, current_version_id, tags, issue_count, created_at, updated_at}` |
| GET / PATCH / DELETE | `/workspaces/{ws}/datasets/{id}` | PATCH `{name?, description?, tags?}`; DELETE refuses (`409 dataset_in_use`) while semantic entities use it |
| GET | `.../datasets/{id}/preview?limit&offset&order_by&direction=asc\|desc` | `TabularResult` |
| GET | `.../datasets/{id}/profile` | Engine `TableProfile` (per-column type, nulls, distinct, min/max/mean/median/quantiles, top values, semantic roles, cardinality; `issues[{code, severity, column, message, count, evidence, sample_values}]`). `404 profile_pending` until ready. |
| POST | `.../datasets/{id}/profile` | Re-profile → `202 JobAccepted` |
| GET | `.../datasets/{id}/versions` | `DatasetVersion[] {id, version_no, content_hash, row_count, columns, ingest_options, upload_id, captured_at}` (a new version is recorded whenever content changes) |
| GET | `.../datasets/{id}/lineage` | `LineageGraph` source → dataset → entities → metrics |
| GET | `.../datasets/{id}/metrics` | metric ids computed from this dataset |
| GET | `.../datasets/{id}/usage` | `{entities, dimensions, metrics, quality_rules, relationships, saved_queries, lineage}` |

## Relationships

`Relationship {id, from_table, from_col, to_table, to_col, cardinality: one_to_one|one_to_many|many_to_one|many_to_many,
confidence: high|medium|low, signals[], overlap_pct, status: suggested|approved|rejected, origin: discovered|manual,
join_analysis: JoinAnalysis|null, decided_by, decided_at, created_at}`. Only approved relationships are used by the semantic compiler.
`JoinAnalysis {observed_cardinality, left_rows, right_rows, joined_rows, fanout_factor, orphan_pct, orphan_count, left_key_unique, right_key_unique, warnings[], sql, declared_cardinality, cardinality_mismatch}` (a mismatch, e.g. a declared many_to_one whose "one" side has duplicate keys, is the first warning). Joins are measured on approve, on create/PATCH and for every approved relationship at demo load

| Method | Path | Notes |
|---|---|---|
| GET | `/workspaces/{ws}/relationships?status&table` | suggestions first, by confidence |
| POST | `/workspaces/{ws}/relationships` | manual `{from_table, from_col, to_table, to_col, cardinality, approve: true}` |
| POST | `.../relationships/discover` | `202 JobAccepted` (re-run discovery; decisions preserved) |
| POST | `.../relationships/{id}/approve`, `.../relationships/{id}/reject` | `{cardinality?, note?}`; approve measures the join on the data |
| PATCH | `.../relationships/{id}` | `{cardinality?, note?}` |
| POST | `.../relationships/{id}/analyze` | re-measure join behaviour |
| DELETE | `.../relationships/{id}` | |

## Data quality

`Rule {id, dataset_id, table_name, name, kind: not_null|unique|range|between|fk_exists|not_future|allowed_values|regex|custom_sql,
column, params, severity: info|warning|error, origin: manual|suggested|demo, status: active|suggested|disabled, description, last_run_at, last_passed}`.
`QualityRun {id, rule_id, status: passed|failed|error, passed, failing_count, total_count, sql, suggested_fix, error, dataset_version_id, duration_ms, created_at}`. `status: error` means the rule could not execute (see `error`; `suggested_fix` is null); it is not a data failure, and the rule's `last_passed` stays null. `/quality/summary` counts these in `errored`.
Rules never modify data; `suggested_fix` is text only.

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/workspaces/{ws}/quality/rules?dataset_id&status` | POST `{dataset_id, name?, kind, column?, params, severity, description?}` |
| GET / PATCH / DELETE | `.../quality/rules/{id}` | PATCH `{name?, params?, severity?, status?, description?}` |
| POST | `.../quality/rules/{id}/accept` | suggested → active |
| POST | `.../quality/rules/{id}/run` | → `QualityRun` |
| POST | `.../quality/run` | `{dataset_id?}` run all active rules → `QualityRun[]` |
| POST | `.../quality/suggest` | `{dataset_id}` → new suggested `Rule[]` (from the profile) |
| GET | `.../quality/runs?rule_id&dataset_id&limit` | history |
| GET | `.../quality/runs/{id}`, `.../quality/runs/{id}/failing-rows` | failing-row sample as `TabularResult` |
| GET | `.../quality/summary` | `{rules, active, suggested, failing, passing, never_run, errored, by_dataset[{dataset_id, name, failing, passing, rules}]}` |

## Semantic layer

Engine models are used directly in bodies/responses: `Entity`, `Dimension`, `Metric`, `MetricTree`/`DriverEdge`,
`GlossaryTerm`, `CalendarConfig`, `SemanticModel`, `ModelIssue`, `TimeWindow` (see OpenAPI).

| Method | Path | Notes |
|---|---|---|
| GET | `/workspaces/{ws}/semantic-models/current` | `{model: SemanticModel, content_hash, issues[], snapshot_id, snapshot_version, metric_versions{metric_id: {version_id, version_no, engine_version_id}}}` |
| GET / PUT | `.../semantic-models/current/yaml` | GET → `application/yaml`; PUT `{yaml, change_note}` merges (changed metrics get new versions) → `{counts, issues, snapshot_id}` |
| POST | `.../semantic-models/current/validate` | → `ModelIssue[]` |
| GET | `.../semantic-models/history`, `.../history/{snapshot_id}` | content-addressed snapshots `{id, version_no, content_hash, reason, metric_version_ids, created_at}` (+`model`) |
| GET / PUT | `.../semantic-models/calendar` | `CalendarConfig {fiscal_year_start_month, week_start, fiscal_year_naming}` |
| GET | `.../semantic-models/calendar/resolve?text=August&today=` | `{input, window, previous_period, same_period_last_year}` |
| GET | `.../semantic-models/entities`; PUT / DELETE `.../semantic-models/entities/{name}` | `{id, spec: Entity, updated_at}` |
| GET / POST | `/workspaces/{ws}/dimensions`; PUT / DELETE `.../dimensions/{name}` | `{id, spec: Dimension, updated_at}`; expressions are validated against the table |
| GET | `.../dimensions/{name}/values?q&limit` | `{dimension, values[], counts[]}` |
| GET / POST | `/workspaces/{ws}/metrics?include_archived&tag` | `Metric` = all definition fields flattened + `{record_id, version_no, current_version_id, engine_version_id, archived, change_note, created_at, updated_at}`. POST body = `Metric` fields + `change_note`. Create and definition-changing PUT/PATCH are compiled and run with `LIMIT 0` first → `422 invalid_metric` with the issues in `errors`. Definition fields include `time_aggregation: sum|last|first|avg` (semi-additive balances). |
| POST | `.../metrics/validate` | validate a definition without saving → `ModelIssue[]`: model checks, then the metric is compiled and run with `LIMIT 0` on the data (a bad `expr`, filter or join is an error issue) |
| GET / PUT / PATCH / DELETE | `.../metrics/{metric_id}` | PUT/PATCH = partial or full definition + `change_note` → **new immutable version** (no-op if unchanged). DELETE archives (refused while other metrics depend on it). |
| GET | `.../metrics/{metric_id}/versions` | `[{version_id, version_no, definition, definition_hash, change_note, created_by, created_at, is_current}]` |
| GET | `.../metrics/{metric_id}/diff?from_version&to_version` | field-level changes |
| POST | `.../metrics/{metric_id}/versions/{n}/restore` | new version equal to version n |
| GET | `.../metrics/{metric_id}/examples?grain&dimension&periods` | `{metric_id, version_no, time_dimension, dimension, grain, points[{period, value}], compiled: CompiledQuery, result: TabularResult}` |
| GET | `.../metrics/{metric_id}/lineage` | metric(version) → input metrics → entity → dataset → source |
| GET | `.../metrics/{metric_id}/investigations` | investigations that used the metric and which version |
| GET | `/workspaces/{ws}/metric-trees`; GET / PUT / DELETE `.../metric-trees/{root_metric}` | `{id, version_no, spec: MetricTree, updated_at}`; PUT replaces nodes |
| POST | `.../metric-trees/suggest` | `{root_metric}` → `{root_metric, edges: DriverEdge[] (approved=false, suggested=true), rationale[]}` |
| POST | `.../metric-trees/{root_metric}/edges` | `{parent, child, decision: approve\|reject}` |
| GET / POST | `/workspaces/{ws}/glossary`; PUT / DELETE `.../glossary/{term_id}` | `{id, spec: GlossaryTerm, updated_at}` |

## SQL workspace

| Method | Path | Notes |
|---|---|---|
| POST | `/workspaces/{ws}/queries/run` | `{sql, params?, parameters?: [{name, type, label, default, required, options}], limit?, data_source_id?, suggest_chart?}` → `QueryRunResult = QueryRun + {result: TabularResult, warnings[], charts: ChartSuggestion[]}`. Params are `$name` placeholders, always bound. Unsafe SQL → `400 unsafe_sql` (recorded in history as `rejected`). |
| POST | `.../queries/validate` | `{sql, dialect?}` → `{ok, normalized_sql, error, code, tables[], parameters[]}` |
| GET | `.../queries/schema` | `{tables[{table, dataset_id, dataset_name, row_count, columns[{name, type}]}], metrics[], dimensions[]}` for the schema browser/autocomplete |
| GET | `.../queries/history?limit&offset&origin&status&saved_query_id&mine` | `Page<QueryRun> {items, total, limit, offset}`; `QueryRun {id, status: succeeded\|failed\|rejected, origin, sql, params, saved_query_id, data_source_id, error, row_count, truncated, elapsed_ms, columns, dataset_versions, created_at}` |
| GET | `.../queries/history/{run_id}` | + `result_snapshot` (first `AOS_RESULT_SNAPSHOT_ROWS` rows) |
| POST | `.../queries/history/{run_id}/rerun` | → `QueryRunResult` |
| POST | `.../queries/compare` | `{left_run_id, right_run_id, keys?}` → `{keys, measures, rows[{keys..., _status, m__left, m__right, m__delta, m__pct}], changed_rows, identical}` |
| GET / POST | `.../queries/saved`; GET / PUT / DELETE `.../queries/saved/{id}` | `SavedQuery {id, name, description, sql, parameters, data_source_id, tags, chart, version_no, ...}` |
| POST | `.../queries/saved/{id}/run` | `{params?, limit?}` → `QueryRunResult` |
| POST | `.../queries/generate` | `{prompt}` → `{sql, explanation, tables[], model, valid, validation_error, executable, repaired, attempts[{attempt, sql, ok, error, row_count, explanation}]}`; needs AI active (server `AI_ENABLED`, workspace setting, a key, budget not reached), else `409 ai_disabled`. The draft is validated read-only (unsafe drafts are returned with `valid: false` and never executed), then dry-run through the bounded repair loop (spec §89): on failure the model sees the error and the referenced tables' schema and proposes a fix, at most 3 executions of 5 rows; rows are never returned and nothing runs automatically. Every attempt is reported and every model call is in the usage log. |

## Explore, pivot, charts, Python

| Method | Path | Notes |
|---|---|---|
| POST | `/workspaces/{ws}/explore/metrics` | Engine `MetricQuery {metrics[], dimensions[] (e.g. "order_date__month"), filters[{dimension, op, values}], time?: {dimension?, start, end, grain?}, order_by[], limit?}` → `{compiled: CompiledQuery, result: TabularResult, filter_context[], metric_versions, charts[]}`. Grain-safe; fan-out → `422 grain_error`.. `time` is half-open `[start, end)`: August is `start=2026-08-01, end=2026-09-01` (a UI with an inclusive end date must send end + 1 day). `compiled.warnings` includes non-unique join key warnings; `filter_context` includes the metric definitions' own filters (`kind: "metric"`). |
| POST | `.../explore/compile` | same body → `CompiledQuery` only (SQL preview) |
| POST | `.../explore/table` | `{table, filters[{column, op, values}], group_by[], aggregates[{column, fn: sum\|count\|count_distinct\|avg\|min\|max\|median, alias?}], calculated[{name, expr}], order_by[{field, desc}], limit}` → `{sql, result, filter_context, charts[]}`. SQL is generated with quoted identifiers; `calculated.expr` is parsed with sqlglot and must be a scalar expression over the table's columns. |
| POST | `.../explore/pivot` | `{source: {metric_query} \| {table, ...}, rows[], columns[], values[{field, agg}], subtotals, grand_totals, sort?}` → `Pivot {row_fields, column_fields, value_fields, header_rows[][], rows[{keys[], cells[], is_subtotal, is_total}], column_totals, grand_total, sql, filter_context}` |
| POST | `.../charts/suggest` | `{columns[{name,type}], rows[][], title?}` → `ChartSuggestion[] {type: kpi\|line\|area\|bar\|stacked_bar\|heatmap\|box\|waterfall\|scatter\|histogram\|table, title, reason, score, x, y[], series, option (ECharts)}` best first. Never pie. |
| POST | `.../python/run` | `{code, inputs: {name: sql}, timeout_s?}` → `{ok, stdout, stderr, error, error_type, traceback, result_repr, timed_out, memory_exceeded, output_truncated, isolation, dataframes{name: TabularResult}, figures[png base64], elapsed_ms}` in the engine sandbox (subprocess, rlimits, no network, scratch dir only, read-only DuckDB `con`). |

## Analysis: forecasting, anomalies, segmentation, statistical tests, correlation

Advanced analyses (spec §39–44) run the engine's `analystos_engine.analysis.*` functions on workspace data and the
semantic model. Every call **persists an Artifact** (`origin: "analysis"`) with the SQL that produced its input, the
parameters, metric versions, dataset versions, filter context and the full engine result, so it appears in
`/artifacts`, has lineage (`/artifacts/{id}/lineage`) and can be re-run. All are viewer-level, read-only POSTs
(allowed for read-only API tokens).

Inputs come in two shapes:

* `TimeSeriesSource = {metric_id, start, end, grain: day|week|month|quarter, time_dimension?, filters?: [{dimension, op, values}]}`:
  the metric per period over `[start, end)` (**end exclusive**), compiled by the semantic layer (grain-safe).
* `TabularSource` = exactly one of `{sql, params?}` (read-only SQL, same rules as `/queries/run`),
  `{saved_query_id, params?}`, `{metric_query: MetricQuery}` (columns are the dimension refs and metric ids), or
  `{table}` (a workspace table). Input rows are capped at `AOS_ANALYSIS_ROW_LIMIT` (default 100000); when the cap
  is hit the result carries a note and `input_truncated: true`.

Every endpoint returns **`AnalysisOut`**:

```
{id (artifact id), kind: forecast|anomalies|segments|stats_test|correlation|regression, method, title,
 label: "Exploratory" | "Descriptive" | "Model estimate" | "Statistical test",
 exploratory: bool,            // true for correlation, regression, k-means: associations, not causes
 summary: str,                 // one deterministic sentence built from the computed numbers
 assumptions: [{name, passed: bool|null, detail}], caveats: [str], notes: [str],
 params, sql, input_row_count, input_truncated, metric_versions {metric_id: engine_version_id},
 dataset_versions [{table, content_hash, row_count}], filter_context [FilterChip], result (engine model, below),
 table: TabularResult (display rows), created_at}
```

| Method | Path | Body → `result` |
|---|---|---|
| POST | `/workspaces/{ws}/analysis/forecast` | `TimeSeriesSource + {horizon: 1..120 = 6, models?: [naive, seasonal_naive, ets, arima] (default all), interval: 0.5..0.99 = 0.8, seasonal_period?: int (default auto), backtest_folds: 1..10 = 3}` → `ForecastResult {history[{timestamp, value}], horizon, frequency, seasonal_period, interval, models[{name, params, points[{timestamp, mean, lower, upper}], backtest{folds[{origin, horizon, mae, mape, coverage}], mae, mape, coverage}, aic, error}], best_model, selection_metric, notes}`. The best model is the lowest backtest MAE; `table` = one row per future period with each model's mean/lower/upper. Label "Model estimate". |
| POST | `.../analysis/anomalies` | `TimeSeriesSource + {sensitivity: low\|medium\|high = medium (or a z threshold 1.5..10), seasonal_period?, min_relative_deviation: 0..1 = 0.05, change_points: true}` → `AnomalyResult {points[{timestamp, value, expected, lower, upper, score, is_anomaly, direction}], anomalies[], change_points[{index, timestamp, mean_before, mean_after, magnitude, pct_change}], method, sensitivity, threshold, seasonal_period, window, frequency, notes}`. Label "Descriptive". |
| POST | `.../analysis/anomalies/investigate` | `{metric_id, date, grain: day\|month = day, filters?, baseline: same_weekday_last_week\|previous_day}` → `Investigation` (201): the anomaly day vs its baseline (day), or the month vs the previous month (month), broken down by the metric's dimensions (spec §40). |
| POST | `.../analysis/segments` | `{method: rfm, table? \| entity?, customer_col, date_col, amount_col, order_col?, as_of? (default: workspace reference date), quantiles: 2..10 = 5}` → `RFMResult {as_of, customers[{customer, recency_days, frequency, monetary, r, f, m, segment}] (first 1000), segments[{segment, count, share, value, value_share}], quantiles, rules[[name, rule]], notes}` · `{method: product_quadrants, dimension, growth_metric_id, profitability_metric_id, volume_metric_id?, current: {start, end}, baseline?: {start, end} (default: the same length immediately before), time_dimension?, filters?, growth_threshold?, profitability_threshold?}` → `QuadrantResult {rows[{item, growth, profitability, volume, quadrant}], growth_threshold, profitability_threshold, segments[], notes}` · `{method: kmeans, source: TabularSource, features[], id_column?, k?: 2..10, standardize: true}` → `ClusterResult {k, labels, ids, centroids[{feature: mean}], sizes, silhouette, features, exploratory, notes}` (exploratory). Rule-based methods are "Descriptive". |
| POST | `.../analysis/stats-test` | `{test, source: TabularSource, alpha: 0.001..0.2 = 0.05, confidence: 0.5..0.999 = 0.95, ...}` with per-test fields: `t_test {value_column, group_column, group_a, group_b, equal_var: false}` · `chi_square {row_column, column_column, count_column?}` · `proportion {group_column, group_a, group_b, success_column, trials_column?}` (without `trials_column` each row is one trial and `success_column` is 0/1 or boolean; with it both columns are summed per group) · `mean_ci {value_column}` · `bootstrap_ci {value_column, stat: mean\|median\|sum}` · `proportion_ci {success_column, trials_column?}` → `TestResult {test, statistic, p_value, df, alpha, significant, effect_size, effect_size_name, estimate, ci_low, ci_high, confidence, n{}, assumptions[{name, passed, detail}], interpretation, caveats[]}` or, for the `*_ci` tests, `ConfidenceInterval {estimate, low, high, confidence, method, n}`. Assumption checks are also copied to `AnalysisOut.assumptions`. Label "Statistical test". |
| POST | `.../analysis/correlation` | `{source: TabularSource, columns?: [numeric columns] (default all numeric, max 30), method: pearson\|spearman = pearson}` → `CorrelationResult {method, columns, matrix[][], pairs[{a, b, r, p_value, n, strength}], exploratory: true, caveat}`. Label "Exploratory". |
| POST | `.../analysis/regression` | `{source: TabularSource, target, features[] (1..20), model: ols\|importance = ols}` → `RegressionResult {target, features, coefficients[{name, coef, std_err, t, p_value, ci_low, ci_high}], r_squared, adj_r_squared, f_pvalue, n, vif{}, warnings[], exploratory, caveat}` or (`importance`) `ImportanceResult {target, model, task, features[{name, importance_mean, importance_std}], score_name, holdout_score, n, exploratory, caveat, notes}`. Label "Exploratory". |
| GET | `/workspaces/{ws}/analysis?kind&limit` | `AnalysisOut[]`, newest first |
| GET | `.../analysis/{artifact_id}` | `AnalysisOut` |

Errors: `422 analysis_failed` (the engine's reason, e.g. "ETS needs at least 6 observations" or "missing columns"),
`422 compile_error`/`grain_error` (metric inputs), `400 unsafe_sql`, `404` for unknown metrics, saved queries or tables.
A model that cannot be fitted inside a forecast does not fail the call: it appears in `models[]` with `error` set.

## Notebooks

`Notebook {id, title, description, investigation_id, cells: Cell[], created_at, updated_at}`;
`Cell {id, position, kind: sql|python|markdown|chart|finding, source, config, output, status: idle|ok|error, execution_count, last_run_at}`.
SQL cell output = `{result: TabularResult, charts}`; python = sandbox result; chart = `{chart: ChartSuggestion}` built from `config.source_cell_id`; finding = the finding referenced by `config.finding_id`.

| Method | Path |
|---|---|
| GET / POST | `/workspaces/{ws}/notebooks` (`{title, description?}`) |
| GET / PATCH / DELETE | `.../notebooks/{id}` |
| POST | `.../notebooks/{id}/cells` (`{kind, source, config?, position?}`) |
| PATCH / DELETE | `.../notebooks/{id}/cells/{cell_id}` |
| POST | `.../notebooks/{id}/cells/{cell_id}/run` → Cell |
| POST | `.../notebooks/{id}/run` (run all in order; stops at the first error) → `RunAllResult {notebook: Notebook, executed, stopped_at: cell id | null}` |
| POST | `.../notebooks/{id}/reorder` (`{cell_ids}`) |
| GET | `.../notebooks/{id}/ipynb` → `.ipynb` download (nbformat 4) |

## Investigations

`Investigation {id, question, title, template, status: needs_disambiguation|awaiting_approval|ready|running|completed|failed,
interpretation: Interpretation, plan: AnalysisPlan|null, hypotheses[], tree: {root_id, nodes: TreeNode[]}|null, brief_answer, followups[],
failures[], metric_version_ids{metric_id: {version_id, version_no, engine_version_id}}, dataset_versions, semantic_snapshot_id,
engine_version, run_count, current_run_id, error, created_at, updated_at}`.
`TreeNode {id, parent_id, kind, statement, statement_type: observation|supported_explanation|hypothesis, metric_id, metric_label,
metric_format, segment{dimension, value}, segment_path[], dimension, current, baseline, abs_change, pct_change,
contribution_to_parent{effect, share, method, mix_effect, rate_effect}, evidence_strength: strong|moderate|weak|hypothesis_only,
evidence_reasons[], artifact_ids[], status: proposed|confirmed|rejected|needs_review|failed, children[], annotations[], notes[], depth, rank, explanatory_power, step_id, finding_id, decision_history[]}`. `finding_id` is filled from saved findings on every response; `decision_history` lists analyst decisions carried forward from earlier runs (a rerun keeps confirmed/rejected/needs_review status, annotations and finding ids).
`Investigation.orchestration` is null unless the optional AI orchestrator ran: `{stages[{stage, source: deterministic|llm|deterministic+llm, ok, detail, duration_ms}], narrative_source: template|llm_verified, rejected_outputs[], tool_calls[{tool, ok, error, artifact_ids}], plan_steps_added[], deterministic_brief}`. With AI active (and no explicit template) a new investigation runs the staged orchestrator (spec §51-53): the tree is still built by the deterministic executor from executed queries; the model may add plan steps (only existing, fan-out-safe dimensions; `origin: "llm"`), run bounded tool calls when the workspace enables `ai.tool_loop`, and word follow-ups and `brief_answer`, which are kept only if every number is found in an artifact and no hypothesis is stated as fact (otherwise the refusal is in `rejected_outputs`).

| Method | Path | Notes |
|---|---|---|
| GET | `/workspaces/{ws}/investigations?status&q` | summaries |
| GET | `.../investigations/templates` | `[{id, name, description, metric_roles, checks}]` |
| POST | `.../investigations/interpret` | `{question}` → `Interpretation` (no persistence) |
| POST | `.../investigations` | `{question, template?, choices?: {term: metric_id}, auto_run?: bool}` → `Investigation` (201). Interprets and plans. If ambiguous → `status=needs_disambiguation` (resolve with `/disambiguate`). Lightweight intents with `auto_run` (default true) execute immediately; expensive plans stop at `awaiting_approval`. |
| GET / PATCH / DELETE | `.../investigations/{id}` | PATCH `{title?}` |
| POST | `.../investigations/{id}/disambiguate` | `{choices: {term: metric_id}}` → re-interpreted + planned |
| POST | `.../investigations/{id}/plan` | re-plan from the interpretation |
| PUT | `.../investigations/{id}/plan` | edited `AnalysisPlan` (enable/disable/remove/add steps, edit params) |
| POST | `.../investigations/{id}/run` | `202 JobAccepted` (result `{run_id, status}`); records metric versions, dataset versions and a semantic snapshot |
| POST | `.../investigations/{id}/rerun` | `202 JobAccepted`; same plan + pinned periods on current data; result includes the diff |
| GET | `.../investigations/{id}/runs` | `[{id, run_no, status, started_at, finished_at, duration_ms, metric_version_ids, dataset_versions, diff_summary}]` |
| GET | `.../investigations/{id}/diff?from_run&to_run` | `InvestigationDiff {node_changes[{node_id, change, statement_before, statement_after, fields}], dataset_version_changes[], metric_version_changes[], unchanged_nodes, summary[]}` (defaults: previous vs latest run) |
| GET | `.../investigations/{id}/artifacts` | `Artifact[]` of the current run |
| POST | `.../investigations/{id}/nodes/{node_id}/drill` | `{dimension}` → updated `Investigation` (new child nodes computed by real queries) |
| POST | `.../investigations/{id}/nodes/{node_id}/actions` | `{action: confirm\|reject\|needs_review\|annotate\|rerun, note?}` → updated `Investigation` |
| POST | `.../investigations/{id}/nodes/{node_id}/finding` | `{statement?, notes?}` → `Finding` promoted from the node with its evidence: `201` when created, `200` with the existing finding when the node was already saved |
| POST | `.../investigations/{id}/command`, `.../investigations/command` | `{text, node_id?}` → `CommandResult {command (parsed), message, action: drilled\|branched\|created_investigation\|saved_finding\|show_sql\|built_report\|built_dashboard\|rerun_started\|node_updated\|listed_contributors\|clarify\|none, investigation_id?, finding_id?, report_id?, dashboard_id?, job_id?, sql[{artifact_id, title, sql}], items[]}`. `branched`/`created_investigation` return the new `investigation_id` (navigate to it); `drilled`/`node_updated` change the current investigation (refetch it). Commands: "break this down by region", "compare to last year", "exclude new stores", "use gross revenue instead", "show the SQL", "save this as a finding", "build me a report", "turn this into a dashboard" |
| POST | `.../investigations/{id}/notebook` | export to a notebook → `Notebook` |
| GET | `.../investigations/{id}/summary` | `{investigation_id, summary: Summary {observations[], supported_explanations[], hypotheses[], narrative, narrative_source: template\|llm_verified, notes[], excluded_count}}` from confirmed nodes/findings only. With AI active the narrative may be reworded by the model; it is kept only if every number is found in the evidence and no hypothesis is stated as fact (otherwise the template narrative stays and `notes` says why). |

## Artifacts and findings

`Artifact {id, kind, title, investigation_id, run_id, sql, python, params, filters, filter_context[], metric_versions, dataset_versions,
result: TabularResult-like snapshot, chart_spec (ECharts), validation{ok, checks[{name, passed, detail}]}, warnings, error, parent_ids, origin, created_at}`.
`ArtifactOut` = `Artifact` + `engine_id` (the id used in `TreeNode.artifact_ids` / `parent_ids`) and `data`: the calculation behind the artifact (engine `Artifact.data`), e.g. for a contribution `{method, additive_valid, total_current, total_baseline, total_change, notes, rows[{segment, current, baseline, effect, share_of_change, mix_effect, rate_effect, pct_change}]}`; for analyses the engine result. `origin`: `investigation | analysis`.

| Method | Path | Notes |
|---|---|---|
| GET | `/workspaces/{ws}/artifacts?investigation_id&kind&origin&limit` | |
| GET | `.../artifacts/{id}` | `{id}` is the database id or an engine id (`art_...`); an engine id resolves to the latest run holding it in this workspace. Same for `/lineage` and `/rerun`. |
| GET | `.../artifacts/{id}/lineage` | artifact → parent query artifacts → metric at the **version recorded on the artifact** (node id `metric:{id}@v{n}`, meta `version_no, version_id, engine_version_id, is_current, current_version_no`) → input metrics at their recorded versions → entity → dataset at the **recorded content hash** (node id `dataset:{id}:{version_id}`, meta `version_no, row_count, content_hash, is_current`) → source |
| POST | `.../artifacts/{id}/rerun` | re-execute the stored SQL → `{artifact_id, identical, result, previous_row_count, changes}` |

`Finding {id, statement, statement_type, evidence_strength, evidence_reasons[], status: draft|confirmed|rejected|needs_review, notes,
business_impact, investigation_id, node_id, metric_id, metric_version_ids, values{current, baseline, abs_change, pct_change, share},
filter_context[], segment, artifact_ids[], tags[], version_no, created_by, created_at, updated_at, comment_count}`.

| Method | Path | Notes |
|---|---|---|
| GET | `/workspaces/{ws}/findings?status&statement_type&investigation_id&q` | |
| POST | `.../findings` | manual `{statement, statement_type, artifact_ids[], notes?, investigation_id?}`. Evidence strength is `hypothesis_only` unless artifacts back it. |
| GET / PATCH / DELETE | `.../findings/{id}` | PATCH `{statement?, notes?, business_impact?, tags?}` → new finding version |
| POST | `.../findings/{id}/status` | `{status, note?}` |
| GET / POST | `.../findings/{id}/comments`; DELETE `.../comments/{comment_id}` | `{id, user_id, user_name, body, created_at}` |
| GET | `.../findings/{id}/versions`, `.../findings/{id}/lineage`, `.../findings/{id}/artifacts` | lineage: finding → chart → query → metric(version) → dataset → source |

## Dashboards

`Dashboard {id, name, description, layout[{i: tile_id, x, y, w, h}], filters[{dimension, op, values, label}], date_range{text?|start,end}, tiles: Tile[], version_no}`.
`Tile {id, kind: kpi|chart|table|text, title, binding, viz, text}`; `binding` is one of
`{metric_query: MetricQuery}` (semantic, inherits dashboard filters/date range), `{saved_query_id, params}`, `{artifact_id}`, `{finding_id}`.

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/workspaces/{ws}/dashboards` | |
| GET / PATCH / DELETE | `.../dashboards/{id}` | PATCH `{name?, description?, layout?, filters?, date_range?}` (each save snapshots a version) |
| POST | `.../dashboards/{id}/tiles`; PATCH / DELETE `.../tiles/{tile_id}` | |
| POST | `.../dashboards/{id}/tiles/{tile_id}/data` | `{filters?, date_range?}` → `TileData {tile_id, kind, result, kpi{value, baseline, abs_change, pct_change, format, label}, chart, filter_context[], provenance{metric_versions, sql, dataset_versions}, compiled?}`. `filter_context` lists the period, the applied filters, the metric definitions' own filters (`kind: "metric"`), the comparison period of KPI tiles and every dashboard filter the tile could not apply (`label: "Not applied", op: "ignored", ignored: true`); `provenance.warnings` carries compile/join warnings. |
| POST | `.../dashboards/{id}/data` | all tiles at once → `TileData[]` |
| GET | `.../dashboards/{id}/tiles/{tile_id}/lineage` | `LineageGraph`: dashboard ← tile (`kpi` or `tile`) ← query (compiled SQL, filter context) ← metric (current version) ← entity ← dataset ← source; artifact/finding tiles chain to their artifacts (spec §62) |
| GET | `.../dashboards/{id}/versions` | |

## Reports

`Report {id, title, kind: custom|business_review|investigation, status: draft|in_review|published, period, investigation_id, blocks[], version_no, published_at, ...}`.
`Block {id, type: narrative|finding|kpi|chart|table|methodology|sources|summary|heading, ...}`:
narrative `{markdown}`, finding `{finding_id, snapshot}`, kpi `{metric_id, period, value, baseline, pct_change, format, sql}`,
chart `{title, option, artifact_id?, provenance}`, table `{title, result: TabularResult, sql}`, methodology `{markdown}`,
sources `{items[{kind, label, ref_id, version}]}`, summary `{observations[], supported_explanations[], hypotheses[],
narrative, narrative_source: template|llm_verified}`. Every block may carry `reviewed: bool` and `excluded: bool` (review
workflow; excluded blocks never appear in exports or MCP `build_report`). `finding_id` / `artifact_id` references must
belong to the workspace (`422 invalid_block_reference`). In investigation reports the brief answer is a narrative block
labelled "Unconfirmed analysis"; chart blocks are unique per artifact and `provenance` carries `{artifact_id, sql,
metric_versions, dataset_versions, filter_context, parent_artifact_ids, queries[{artifact_id, title, sql}]}`.

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/workspaces/{ws}/reports` | POST `{title, kind?, blocks?, investigation_id?}` |
| GET / PATCH / DELETE | `.../reports/{id}` | PATCH `{title?, blocks?}` (published reports are read-only until unpublished) |
| POST | `.../reports/executive-summary` | `{finding_ids?, investigation_id?, title?}` → `Report` built only from **confirmed** findings (observed facts / supported explanations / hypotheses) |
| POST | `.../reports/business-review` | `{period: "2026-08", title?}` → `Report (kind=business_review, status=draft)`: narrative and KPI overview ordered by materiality (canonical revenue first, then currency metrics by absolute change, then others by relative change), "Largest changes" (`sql` + per-metric `queries`), one "Drivers of <metric> by <dimension>" table per dimension (`dimension, sql, queries, method, additive_valid`), segment mix movements, anomalies, confirmed findings (with a verified AI-worded summary when AI is active), open questions |
| POST | `.../reports/{id}/submit`, `.../reports/{id}/publish`, `.../reports/{id}/unpublish` | draft → in_review → published. Publish needs `in_review` (`409 not_in_review`) and every block without `excluded: true` marked `reviewed: true` (`409 unreviewed_blocks`, `errors.block_ids`). Unpublish returns to draft. |
| POST | `.../reports/from-investigation` | `{investigation_id}` → Report |

## Exports

| Method | Path | Notes |
|---|---|---|
| POST | `/workspaces/{ws}/exports` | `{target: query_run\|saved_query\|dataset\|artifact\|finding\|report\|dashboard\|notebook\|investigation, id, format: csv\|xlsx\|md\|html\|pdf\|ipynb\|json, params?}` → file download (`Content-Disposition: attachment`). PDF uses WeasyPrint when installed; otherwise `409 pdf_unavailable` with `errors.fallback="html"`; the HTML export carries print CSS, so the browser's Print → PDF produces the document. |
| GET | `.../exports` | stored exports and chart images `[{id, purpose, format, filename, size_bytes, source_type, source_id, created_at, download_url}]` |
| GET | `.../exports/{file_id}/download` | |
| POST | `.../exports/images` | multipart `file` (PNG from ECharts `getDataURL`), fields `source_type`, `source_id`, `title` → StoredFile (201). PNG magic-checked, ≤ 10 MB. Report HTML/PDF exports embed stored chart images when present. |

## Search, AI, diagnostics, audit

| Method | Path | Notes |
|---|---|---|
| GET | `/workspaces/{ws}/search?q&kinds&limit` | `SearchHit[] {id, kind: dataset\|column\|metric\|dimension\|glossary\|investigation\|finding\|dashboard\|report\|saved_query\|notebook, title, subtitle, snippet, ref_id, parent_id, score}` |
| GET / PUT | `/workspaces/{ws}/ai/settings` | `{enabled, provider, model_large, model_default, model_small, allow_result_samples, monthly_budget_usd, tool_loop, month_spend_usd, over_budget, has_api_key, key_source: workspace\|environment\|none, server_ai_enabled, active}`; PUT (owner) accepts `api_key` (write-only, encrypted; `""` clears) and `tool_loop`. AI pauses (`active: false`) once `monthly_budget_usd` is reached. |
| POST | `.../ai/test` | → `{ok, message, model, latency_ms}` |
| GET | `.../ai/usage?days` | `{items[{id, provider, model, task, tokens_in, tokens_out, latency_ms, est_cost_usd, success, created_at}], totals{calls, tokens_in, tokens_out, est_cost_usd}, by_task[], by_model[]}` |
| GET | `/workspaces/{ws}/diagnostics/events?category&status&limit&since` | structured events `{id, category: import\|profiling\|sql\|python\|ai\|investigation\|job\|quality\|connector\|export, name, status: ok\|error\|rejected, duration_ms, detail, error, request_id, user_id, created_at}` |
| GET | `/workspaces/{ws}/diagnostics/summary?hours` | counts, error rates and p50/p95 durations per category |
| GET | `/workspaces/{ws}/audit?action&resource_type&limit&offset` | `Page<AuditEntry {id, actor, user_id, action, resource_type, resource_id, detail, ip, request_id, created_at}>` (owner or editor) |

## Running

```bash
uv run analystos-api               # uvicorn on AOS_HOST:AOS_PORT (default 127.0.0.1:8000); migrates on start
uv run analystos-api-migrate       # alembic upgrade head for DATABASE_URL (or: uv run alembic -c apps/api/alembic.ini upgrade head)
uv run pytest apps -q              # API + MCP tests (SQLite temp DB, temp DATA_DIR, no network)
```

Settings (env or `.env`): `DATABASE_URL` (default `sqlite:///./data/analystos.db`; Postgres: install
`analystos-api[postgres]`), `DATA_DIR`, `AOS_SECRET_KEY` (required when `AOS_ENV=production`; otherwise one is
generated into `DATA_DIR/.secret_key`), `AUTH_MODE` (`auto|local|password`), `AOS_ALLOW_INSECURE_LOCAL`,
`AOS_BOOTSTRAP_TOKEN`, `AOS_PROXY_SECRET`, `AOS_INVITE_TTL_DAYS`, `AOS_ALLOW_SIGNUP` (default false: only the first
account; people join by invite), `AOS_LOGIN_MAX_FAILURES`,
`AOS_LOGIN_LOCKOUT_S`, `AOS_ANALYSIS_ROW_LIMIT`,
`AOS_COOKIE_SECURE`, `AOS_CORS_ORIGINS`, `ANTHROPIC_API_KEY`, `AI_ENABLED`, `AOS_MAX_UPLOAD_MB`,
`AOS_QUERY_ROW_LIMIT`, `AOS_QUERY_TIMEOUT_S`, `AOS_PYTHON_TIMEOUT_S`, `AOS_PYTHON_MEM_MB`, `AOS_JOB_WORKERS`,
`AOS_JOB_EXECUTION` (`thread|inline`), `AOS_AUTO_MIGRATE`, `AOS_LOG_LEVEL`, `AOS_LOG_JSON`.

## MCP server (`apps/mcp`, package `analystos_mcp`)

`AOS_API_URL=http://127.0.0.1:8000 AOS_API_TOKEN=aos_... uv run analystos-mcp` (stdio) or
`AOS_MCP_HTTP_TOKEN=<secret, 16+ chars> uv run analystos-mcp --transport streamable-http --port 8765 [--allowed-host
name]`. Built on the official `mcp` SDK (`MCPServer`). The HTTP transport refuses to start without `AOS_MCP_HTTP_TOKEN`;
every request needs `Authorization: Bearer <AOS_MCP_HTTP_TOKEN>` (else 401) and a Host/Origin on the allow-list
(localhost, 127.0.0.1, [::1], `--host` and `--allowed-host`; DNS-rebinding protection). Tools run in worker threads, so
concurrent calls do not serialise. Id arguments (`workspace_id`, `investigation_id`, `metric_id`) must match
`[A-Za-z0-9_-]{1,64}` (else `{"error": {code: "invalid_argument"}}`) and are URL-escaped.
Every tool calls this REST API with the bearer token, so membership, roles, workspace-restricted and read-only
tokens apply. Tools: `list_workspaces`, `inspect_dataset(workspace_id, dataset?)`, `inspect_metric(workspace_id,
metric_id?)`, `run_analysis(workspace_id, question, approve=true, choices?)`, `run_sql(workspace_id, sql, params?,
limit)`, `get_investigation(workspace_id, investigation_id, include_sql)`, `get_findings(workspace_id, status?,
investigation_id?, query?)`, `build_report(workspace_id, investigation_id? | period? , title?)` (returns Markdown).
API errors are returned as `{"error": {status, code, detail}}`; unexpected failures as
`{"error": {status: 500, code: "tool_error", detail}}`.
