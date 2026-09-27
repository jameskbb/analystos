# Architecture decisions

A condensed, ADR-style summary of the decisions made while building AnalystOS. The full,
append-only decision log is a private planning artefact and is not part of this repository.

## ADR-001: The deterministic engine is the core; the LLM is optional
**Context:** Conclusions must be traceable and reproducible, and the product must work with no API key.
**Decision:** Interpretation, planning, execution, attribution and summaries are deterministic.
The LLM only suggests within known candidates, adds validated plan steps, drives a bounded tool loop
(opt-in per workspace) and rewords text that is kept only when a verifier finds every number in the
executed artifacts. These paths run only with AI enabled and a key; see
[ai-architecture.md](ai-architecture.md) for exactly where each is wired.
**Consequence:** The same question on the same data yields the same tree; AI can be disabled
entirely.

## ADR-002: One DuckDB file per workspace; metadata in SQLite or Postgres
**Decision:** Analytical data lives in `DATA_DIR/workspaces/{id}/warehouse.duckdb` (external access
disabled); application metadata in SQLAlchemy with Alembic migrations that run on SQLite (dev, tests)
and Postgres (docker-compose). One initial migration is regenerated until v1 ships.
**Consequence:** Workspace isolation and simple self-hosting; no distributed infrastructure.

## ADR-003: Grain-safe semantic compiler over approved relationships only
**Decision:** Metrics aggregate on their own entity and only join parents; child-grain filters become
semi-joins; impossible groupings raise `GrainError`; time windows apply per metric to its own date
column; only analyst-approved relationships are used.
**Consequence:** No fan-out double counting, at the cost of refusing some queries with an explanation.

## ADR-004: Immutable, versioned definitions
**Decision:** Metric identity is the engine metric id; each definition change creates an immutable
version (`{id}@v{n}:{hash}`); every semantic change writes a content-addressed snapshot. Semantic
objects are stored as the engine's Pydantic JSON so there is one schema.
**Consequence:** Every investigation records exactly which definitions it used, and reruns report
definition changes.

## ADR-005: Verified attribution and categorical evidence
**Decision:** Shares of a change are claimed only when additivity is verified on the data;
multiplicative trees use LMDI; one metric tree is used per metric; evidence strength follows
documented rules (strong / moderate / weak / hypothesis only) with reasons, never a numeric
confidence. Untestable ideas are hypothesis nodes, and failed tests are explicit failed nodes.
**Consequence:** The tree never implies additive attribution across overlapping segments.

## ADR-006: Investigations are self-contained and content-addressed
**Decision:** A plan freezes absolute windows, baseline, filters, comparison kind and limits. Node and
artifact ids are content-derived hashes. Plans above 40 estimated queries need approval.
**Consequence:** Rerun and diff work without the original request context.

## ADR-007: Security defaults
**Decision:** Password sign-in by default for any deployment (Docker Compose and the API image use
`AUTH_MODE=password` and `AOS_ENV=production`, with ports published on loopback); the no-password
`auto`/`local` modes are for a personal machine, admit only loopback requests, and are refused in
production unless `AOS_ALLOW_INSECURE_LOCAL=true`. The first account claims the local workspaces only
from a local session or with the bootstrap token. Server-side sessions with a double-submit CSRF token bound to the session; hashed,
optionally read-only and workspace-scoped API tokens; 404 for non-members; Fernet-encrypted secrets
from `AOS_SECRET_KEY`; upload content sniffing; every SQL string validated read-only.

## ADR-008: In-process jobs, no broker
**Decision:** A thread pool backed by a persisted `jobs` table; interrupted jobs are marked failed on
start; `AOS_JOB_EXECUTION=inline` for tests.

## ADR-009: Workspace-scoped URLs and a dense workstation UI
**Decision:** Web routes are `/w/{workspace}/...`; 13 px base, neutral palette with one accent,
statement types rendered with distinct rules and labels (hypotheses dashed and italic); the
investigation screen uses resizable tree / analysis / evidence / inspector panes that become tabs on
narrow screens; ECharts for every chart, no pie charts.

## ADR-010: Demo data is generated, calibrated and self-verifying
**Context:** The evals need known answers without hand-tuned data.
**Decision:** Summit Supply Co. is generated deterministically; stories are pinned to explicit
periods (Aug 2026 vs Jul 2026, Q2 2026 vs Q2 2025, Q2 vs Q1 2026 lead cohorts, June snapshots, Q2
plan) so each question has one answer; headline magnitudes are calibrated by bisection on pre-drawn
uniforms, then re-measured with DuckDB into `scenarios.json`.
**Consequence:** The answer key cannot drift from the data, and stories hold for other seeds.

## ADR-011: Conformed dimensions for actual-vs-plan
**Decision:** Region and customer segment are small reference entities that facts, leads, budgets and
the forecast all reach.
**Consequence:** "Revenue vs forecast by segment" compiles in one grain-safe query.

## ADR-012: Messy data stays messy
**Decision:** The loader never cleans source data (duplicates, padded IDs, casing, negative
quantities, malformed dates). Canonical reporting attributes come from clean dimensions (branch
region); problems are surfaced by profiling and DQ rules, which never mutate data.

## ADR-013: One pytest process per suite
**Decision:** `scripts/test-python.sh` runs each package's tests in its own process, so per-package
`conftest.py` modules cannot shadow each other. CI and `make test-py` use it.
