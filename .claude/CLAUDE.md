<!-- GSD:project-start source:PROJECT.md -->

## Project

**AnalystOS**

AnalystOS is an analytical investigation workspace for BI, data, operations and finance teams. You connect your data, define what your metrics mean, and ask questions like "Why did revenue fall?"; AnalystOS turns the question into a reviewable investigation plan, runs it as real SQL against your data, decomposes the change with exact arithmetic, and returns a tree of findings where every number links back to the query that produced it. It is a workstation where AI and human analysts investigate together, not a chatbot on top of a CSV uploader.

**Core Value:** Every analytical conclusion is traceable (Conclusion → Evidence → Calculation → Query → Source Data), reproducible through stored executable logic, and understandable by a human analyst. The LLM never sits between the data and the truth; the analytical engine does.

### Constraints

- **Tech stack**: Next.js + TS + Tailwind + shadcn/ui; FastAPI; SQLAlchemy + Alembic (Postgres in prod, SQLite dev/test); DuckDB; Polars/Pandas; ECharts; sqlglot for SQL parsing and safety (owner spec §5 plus proxy decisions)
- **Reproducibility**: the deterministic, semantic-model-driven planner is the core, with the LLM as an optional enhancement; an investigation must yield the same tree for the same data and definitions
- **Security**: read-only SQL enforcement, sandboxed Python, encrypted secrets, workspace isolation, prompt-injection separation
- **Verifiability**: all tests pass without network, Docker or an API key

<!-- GSD:project-end -->

<!-- GSD:stack-start source:STACK.md -->

## Technology Stack

Technology stack not yet documented. Will populate after codebase mapping or first phase.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
