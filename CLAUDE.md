# AnalystOS Agent Operating Rules

`CLAUDE.md` and `AGENTS.md` are the same document under two names so Claude
Code and Codex follow identical rules. Change both together.

[`README.md`](README.md) is the description of record for what this product
is. There is exactly one answer to "what is AnalystOS", and it is the README's.

[`.claude/CLAUDE.md`](.claude/CLAUDE.md) is **generated** from `.planning/` by
GSD. It carries the GSD workflow rules and the planning constraints; its
project blurb is a planning artefact and is not the product description. Never
hand-edit it; change the planning source and let GSD regenerate. This file
holds only the operating rules GSD does not generate.

## 1. Repository

Monorepo, Python 3.12+ (uv workspace) plus a Next.js 15 web app:

| Path | Package |
|---|---|
| `packages/engine` | `analystos_engine`: DuckDB store, ingest, connectors, profiling, quality, relationships, semantic model and compiler, SQL safety, calendar, analysis, Python sandbox |
| `packages/investigator` | `analystos_investigator`: interpretation, planning, execution, evidence, drill/rerun/diff, commands, summaries, optional LLM orchestration |
| `apps/api` | `analystos_api`: FastAPI, SQLAlchemy + Alembic, auth, jobs, persistence, exports, audit |
| `apps/mcp` | `analystos_mcp`: MCP server |
| `apps/web` | Next.js 15 workstation UI |
| `packages/demo-data` | `analystos_demo`: Summit Supply Co. demo data, answer key and semantic model |
| `tests/evals`, `tests/e2e` | Analytical evals and the Playwright acceptance flows |

`README.md` is the description of record (above); `docs/` holds the canonical
detail (architecture, semantic layer, investigation engine, AI architecture,
analytical correctness, security, connectors, MCP, demo scenarios, decisions).
When a README claim and the code disagree, the code wins; fix the docs.

## 2. Install, run, test

```bash
make setup      # uv sync + pnpm install
make demo       # generate and verify the Summit Supply Co. demo dataset
make dev        # API on :8000, web on :3000
make test       # every Python suite + web unit tests (includes the evals)
make evals      # analytical evals with the scorecard
make lint       # ruff check + ruff format --check + eslint
make typecheck  # mypy over all five Python packages + tsc --noEmit
make help       # every target
```

`make setup` and `make setup-e2e` download dependencies, so they need the
network; `make docker-up` needs Docker. After setup, the tests, the evals and
the demo run with no network, no Docker and no API key. A change that breaks
*that* invariant is a bug, not a trade-off.

## 3. Rules that do not bend

- **The LLM never produces a number.** Values, shares, percentages, evidence
  labels, rankings, node contents and tree shape come from executed queries and
  deterministic math only. Any model wording containing a number no executed
  query produced is discarded by the verifier; do not widen that gate.
- **Read-only SQL.** Every path that executes user-, model- or
  compiler-supplied SQL goes through `ensure_read_only`: `store.execute_read`
  and `store.export_arrow`, the SQL connectors, the semantic compiler and
  `run_metric_query`, the SQL editor and analysis endpoints, the quality rules,
  the investigator's custom-SQL steps, the LLM SQL-repair loop and the LLM tool
  loop. The warehouse is written only by the store's own table methods
  (`write_table`, `drop_table`, `rename_table`); they take a validated table
  name and never execute supplied SQL. Calling them is confined to ingest (file
  upload and connector pull), the demo load and dataset lifecycle. Do not add a
  query path that skips `ensure_read_only`, and do not add another writer.
- **Governed semantic model.** Do not add an unapproved relationship, an
  unguarded join, or a metric without a version. The compiler is grain-safe;
  do not suppress its warnings or refusals to make a query run.
- **Untrusted data stays framed.** Data-derived text reaches a model only
  inside the nonce-delimited untrusted block. Do not interpolate it into a
  system prompt.
- **Documentation claims are verified against code and tests**, never against
  the previous version of the document.
- **A change to the UI updates the README's screenshots in the same change.**
  `docs/images/` holds eight screenshots that carry the README's argument
  (plus the title card, rendered from `docs/src/` with
  `node docs/src/render-title-card.mjs`), and the captions and surrounding prose
  describe what is in them. Rename a tab, restyle the inspector, or redesign a
  page and they are wrong. Regenerate with
  `node tests/e2e/scripts/screenshots.mjs <web url>` against a freshly started
  stack, then re-read every sentence that describes one. A redesign that lands
  with the old screenshots is a broken README.
- **No em dashes.** Never write an em dash in this repository: docs, READMEs,
  comments, UI text, commit messages. Reword with a colon, semicolon, comma,
  parentheses or a new sentence; do not substitute a hyphen or en dash.

## 4. Git

- Single branch `main`, no prod branch. Commit directly on `main`.
- Test command before landing: `make test`.
- Commit with the repository's existing git identity; do not set a new one.
- Imperative subject, short body saying what and why, plus the session's
  attribution trailers. Never force-push.
