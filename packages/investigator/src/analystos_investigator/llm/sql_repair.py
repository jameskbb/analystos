"""Bounded SQL repair loop (spec §89).

On failure: capture the error, re-inspect the schema of the referenced tables (or list
tables when a table is unknown), ask the model for a corrected read-only query, and retry,
at most ``max_attempts`` executions in total. Every attempt is recorded. Unsafe SQL returned
by the model is rejected by ``ensure_read_only`` and counts as a failed attempt. The final
failure is surfaced to the caller, never hidden, and the loop never runs unbounded.
"""

from __future__ import annotations

from typing import Any

from analystos_engine.sqlsafety import ensure_read_only, referenced_tables
from pydantic import BaseModel, ConfigDict, Field

from .base import LLMError, LLMProvider, Message
from .untrusted import UNTRUSTED_DATA_POLICY, render_untrusted, render_user_request

DEFAULT_MAX_ATTEMPTS = 3


class RepairAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt: int
    sql: str
    ok: bool
    error: str | None = None
    row_count: int | None = None
    explanation: str | None = None


class RepairOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    succeeded: bool
    final_sql: str | None = None
    attempts: list[RepairAttempt] = Field(default_factory=list)
    final_error: str | None = None
    result: Any = None


class _RepairProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str
    explanation: str


_SYSTEM = (
    "You repair a failed read-only DuckDB SQL query. Use only tables and columns that appear in the "
    "schema you are given. Return a single SELECT (or WITH ... SELECT) statement; never modify data. "
    "If the query cannot be repaired with the given schema, return the original query unchanged and say "
    "why in the explanation. " + UNTRUSTED_DATA_POLICY
)


def _schema_context(store: Any, sql: str) -> dict[str, Any]:
    try:
        tables = referenced_tables(sql)
    except Exception:
        tables = []
    known = {t.name for t in store.list_tables(include_row_counts=False)}
    ctx: dict[str, Any] = {"tables": {}}
    for t in tables:
        name = t.split(".")[-1]
        if name in known:
            info = store.describe(name, include_row_count=False)
            ctx["tables"][name] = [f"{c.name} {c.type}" for c in info.columns]
    if not ctx["tables"] or any(t.split(".")[-1] not in known for t in tables):
        ctx["available_tables"] = sorted(known)
    return ctx


def execute_with_repair(
    sql: str,
    store: Any,
    llm: LLMProvider | None,
    *,
    question: str = "",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    limit: int = 1000,
) -> RepairOutcome:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    attempts: list[RepairAttempt] = []
    current = sql
    explanation: str | None = None
    for n in range(1, max_attempts + 1):
        try:
            safe = ensure_read_only(current)
            res = store.execute_read(safe, limit=limit)
            attempts.append(
                RepairAttempt(attempt=n, sql=safe, ok=True, row_count=res.row_count, explanation=explanation)
            )
            return RepairOutcome(succeeded=True, final_sql=safe, attempts=attempts, result=res)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            attempts.append(
                RepairAttempt(attempt=n, sql=current, ok=False, error=error, explanation=explanation)
            )
        if n == max_attempts:
            break
        if llm is None or not getattr(llm, "available", False):
            attempts[-1].error = (
                attempts[-1].error or ""
            ) + " (no LLM configured; automatic repair unavailable)"
            break
        schema = _schema_context(store, current)
        messages = [
            Message(
                role="user",
                content=[
                    render_user_request(question or "Repair this query."),
                    "The query below failed. Propose a corrected query.",
                    render_untrusted("failed_query", {"sql": current, "error": attempts[-1].error}),
                    render_untrusted("schema", schema),
                ],
            )
        ]
        try:
            proposal, _usage = llm.complete_structured(
                "repair_sql", _SYSTEM, messages, _RepairProposal, "default"
            )
        except LLMError as exc:
            attempts[-1].error = (attempts[-1].error or "") + f" (repair request failed: {exc})"
            break
        if proposal.sql.strip() == current.strip():
            attempts[-1].error = (
                attempts[-1].error or ""
            ) + f" (model could not repair: {proposal.explanation})"
            break
        current = proposal.sql
        explanation = proposal.explanation
    return RepairOutcome(
        succeeded=False,
        final_sql=None,
        attempts=attempts,
        final_error=attempts[-1].error if attempts else "not executed",
    )
