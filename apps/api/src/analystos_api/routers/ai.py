"""AI settings, connection test, usage/cost log and SQL generation (optional; never required)."""

from __future__ import annotations

import datetime as dt
import time
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import utcnow
from ..deps import DbDep, EditorCtx, OwnerCtx, StateDep, ViewerCtx
from ..errors import ApiError
from ..models import AIUsage
from ..schemas.common import ERROR_RESPONSES
from ..services.ai import ai_status, get_llm, month_spend_usd
from ..services.observability import audit, record_event
from ..services.semantic import build_model
from ..services.workspaces import merged_settings
from ..services.writer import writer_for

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["ai"], responses=ERROR_RESPONSES)


class AIDisabled(ApiError):
    status_code = 409
    code = "ai_disabled"


class AISettingsOut(BaseModel):
    enabled: bool
    provider: str
    model_large: str
    model_default: str
    model_small: str
    allow_result_samples: bool
    monthly_budget_usd: float | None
    tool_loop: bool = Field(
        default=False,
        description="Let the assistant run bounded exploratory tool calls "
        "(spec §52); every result is an executed artifact",
    )
    month_spend_usd: float = Field(default=0.0, description="Estimated spend this calendar month")
    over_budget: bool = Field(default=False, description="Monthly budget reached: AI calls are paused")
    has_api_key: bool
    key_source: str = Field(description="workspace | environment | none")
    server_ai_enabled: bool = Field(description="AI_ENABLED on the server; AI never runs when false")
    active: bool = Field(description="True when AI calls will actually be made")


class AISettingsIn(BaseModel):
    enabled: bool | None = None
    model_large: str | None = None
    model_default: str | None = None
    model_small: str | None = None
    allow_result_samples: bool | None = None
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    tool_loop: bool | None = None
    api_key: str | None = Field(
        default=None, description="Write-only. Empty string removes the workspace key."
    )


class AITestResult(BaseModel):
    ok: bool
    message: str
    model: str | None = None
    latency_ms: float | None = None


class UsageItem(BaseModel):
    id: str
    provider: str
    model: str
    task: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    est_cost_usd: float
    success: bool
    investigation_id: str | None
    created_at: dt.datetime


class UsageOut(BaseModel):
    items: list[UsageItem]
    totals: dict[str, float]
    by_task: list[dict[str, Any]]
    by_model: list[dict[str, Any]]


class GenerateSqlRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)


class GeneratedSql(BaseModel):
    sql: str
    explanation: str
    tables: list[str] = Field(default_factory=list)


class RepairAttemptOut(BaseModel):
    attempt: int
    sql: str
    ok: bool
    error: str | None = None
    row_count: int | None = None
    explanation: str | None = None


class GenerateSqlOut(GeneratedSql):
    model: str
    valid: bool = Field(description="Passes the read-only policy")
    validation_error: str | None = None
    executable: bool = Field(
        default=False, description="Ran successfully in a dry run (a few rows, not returned)"
    )
    repaired: bool = Field(default=False, description="The draft failed and the bounded repair loop fixed it")
    attempts: list[RepairAttemptOut] = Field(
        default_factory=list, description="Every dry-run attempt (spec §89: at most 3, never hidden)"
    )


def _settings_out(state: StateDep, ctx: ViewerCtx) -> AISettingsOut:
    ai = merged_settings(ctx.workspace)["ai"]
    status = ai_status(state, ctx.workspace)
    spend = month_spend_usd(state, ctx.workspace)
    budget = ai.get("monthly_budget_usd")
    over = budget is not None and spend >= float(budget)
    return AISettingsOut(
        enabled=bool(ai.get("enabled")),
        provider=ai.get("provider", "anthropic"),
        model_large=ai["model_large"],
        model_default=ai["model_default"],
        model_small=ai["model_small"],
        allow_result_samples=bool(ai.get("allow_result_samples")),
        monthly_budget_usd=ai.get("monthly_budget_usd"),
        tool_loop=bool(ai.get("tool_loop")),
        month_spend_usd=round(spend, 6),
        over_budget=over,
        has_api_key=status["has_api_key"],
        key_source=status["key_source"],
        server_ai_enabled=status["server_ai_enabled"],
        active=status["active"] and not over,
    )


@router.get("/ai/settings", response_model=AISettingsOut, operation_id="getAiSettings")
def get_settings(ctx: ViewerCtx, state: StateDep) -> AISettingsOut:
    return _settings_out(state, ctx)


@router.put("/ai/settings", response_model=AISettingsOut, operation_id="updateAiSettings")
def put_settings(body: AISettingsIn, ctx: OwnerCtx, db: DbDep, state: StateDep) -> AISettingsOut:
    settings = merged_settings(ctx.workspace)
    ai = dict(settings["ai"])
    for key, value in body.model_dump(exclude_unset=True, exclude={"api_key"}).items():
        ai[key] = value
    if body.api_key is not None:
        if body.api_key.strip():
            ai["api_key_encrypted"] = state.secret_box.encrypt({"api_key": body.api_key.strip()})
        else:
            ai.pop("api_key_encrypted", None)
    settings["ai"] = ai
    ctx.workspace.settings = settings
    audit(
        db,
        action="ai.settings_update",
        resource_type="workspace",
        resource_id=ctx.workspace_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={
            **body.model_dump(exclude_unset=True, exclude={"api_key"}),
            "api_key_changed": body.api_key is not None,
        },
    )
    db.flush()
    return _settings_out(state, ctx)


@router.post("/ai/test", response_model=AITestResult, operation_id="testAi")
def test_ai(ctx: EditorCtx, state: StateDep) -> AITestResult:
    """Make one tiny structured call to verify the key and model (no workspace data is sent)."""
    from analystos_investigator.llm.base import LLMError, Message

    llm = get_llm(state, ctx.workspace)
    if llm is None:
        st = ai_status(state, ctx.workspace)
        reason = (
            "AI_ENABLED is false on this server"
            if not st["server_ai_enabled"]
            else "AI is disabled in this workspace's settings"
            if not st["workspace_enabled"]
            else "no API key"
        )
        return AITestResult(ok=False, message=f"AI is not active: {reason}")

    class Pong(BaseModel):
        reply: str

    start = time.perf_counter()
    try:
        _obj, usage = llm.complete_structured(
            "connection_test",
            "Reply with the word pong.",
            [Message(role="user", content=["ping"])],
            Pong,
            "small",
        )
    except LLMError as exc:
        return AITestResult(ok=False, message=str(exc))
    return AITestResult(
        ok=True,
        message="Connected",
        model=usage.model,
        latency_ms=round((time.perf_counter() - start) * 1000, 1),
    )


@router.get("/ai/usage", response_model=UsageOut, operation_id="getAiUsage")
def usage(
    ctx: ViewerCtx, db: DbDep, state: StateDep, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> UsageOut:
    writer_for(state.session_factory).flush(5)
    since = utcnow() - dt.timedelta(days=days)
    rows = db.scalars(
        select(AIUsage)
        .where(AIUsage.workspace_id == ctx.workspace_id, AIUsage.created_at >= since)
        .order_by(AIUsage.created_at.desc())
    ).all()
    items = [UsageItem.model_validate(r, from_attributes=True) for r in rows]

    def group(key: str) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            g = out.setdefault(
                getattr(r, key),
                {key: getattr(r, key), "calls": 0, "tokens_in": 0, "tokens_out": 0, "est_cost_usd": 0.0},
            )
            g["calls"] += 1
            g["tokens_in"] += r.tokens_in
            g["tokens_out"] += r.tokens_out
            g["est_cost_usd"] = round(g["est_cost_usd"] + r.est_cost_usd, 6)
        return sorted(out.values(), key=lambda g: -g["est_cost_usd"])

    totals = {
        "calls": float(len(rows)),
        "tokens_in": float(sum(r.tokens_in for r in rows)),
        "tokens_out": float(sum(r.tokens_out for r in rows)),
        "est_cost_usd": round(sum(r.est_cost_usd for r in rows), 6),
    }
    return UsageOut(items=items[:500], totals=totals, by_task=group("task"), by_model=group("model"))


@router.post(
    "/queries/generate", response_model=GenerateSqlOut, operation_id="generateSql", tags=["queries", "ai"]
)
def generate_sql(body: GenerateSqlRequest, ctx: EditorCtx, db: DbDep, state: StateDep) -> GenerateSqlOut:
    """Draft DuckDB SQL from a request using the schema and semantic model (names and types only; no data rows).
    The SQL is validated against the read-only policy, dry-run through a bounded repair loop (at most three
    executions of a few rows, none returned) and handed back for review; the analyst runs it explicitly."""
    from analystos_engine.sqlsafety import UnsafeSQLError, ensure_read_only
    from analystos_investigator.llm import UNTRUSTED_DATA_POLICY, render_untrusted, render_user_request
    from analystos_investigator.llm.base import LLMError, Message
    from analystos_investigator.llm.sql_repair import execute_with_repair

    llm = get_llm(state, ctx.workspace)
    if llm is None:
        raise AIDisabled(
            "AI is not enabled for this workspace (server AI_ENABLED, workspace setting and an API key "
            "are all required)"
        )
    store = state.stores.get(ctx.workspace_id)
    schema = [
        {"table": t.name, "columns": [{"name": c.name, "type": c.type} for c in t.columns]}
        for t in store.list_tables(include_row_counts=False)
    ]
    model = build_model(db, ctx.workspace).model
    metrics = [
        {
            "id": m.id,
            "name": m.display_name,
            "kind": m.kind,
            "expr": m.expr,
            "entity": m.entity,
            "agg": m.agg,
            "numerator": m.numerator,
            "denominator": m.denominator,
            "formula": m.formula,
        }
        for m in model.metrics
    ]
    joins = [
        {
            "from": f"{r.from_entity}.{r.from_col}",
            "to": f"{r.to_entity}.{r.to_col}",
            "cardinality": r.cardinality,
        }
        for r in model.relationships
        if r.approved
    ]
    system = (
        "You write a single read-only DuckDB SELECT for an analyst. Use only the tables and columns given. "
        "Respect metric definitions exactly and join only on the approved relationships, aggregating to "
        "the correct grain before joining to avoid double counting. " + UNTRUSTED_DATA_POLICY
    )
    messages = [
        Message(
            role="user",
            content=[
                render_untrusted("schema", schema),
                render_untrusted("semantic_metrics", metrics),
                render_untrusted("approved_relationships", joins),
                render_user_request(body.prompt),
            ],
        )
    ]
    try:
        obj, used = llm.complete_structured("generate_sql", system, messages, GeneratedSql, "default")
    except LLMError as exc:
        raise ApiError(f"The AI provider failed: {exc}", code="ai_error") from exc
    try:
        sql = ensure_read_only(obj.sql, "duckdb")
    except UnsafeSQLError as exc:
        # Unsafe drafts are never executed, not even in a dry run.
        return GenerateSqlOut(
            sql=obj.sql,
            explanation=obj.explanation,
            tables=obj.tables,
            model=used.model,
            valid=False,
            validation_error=str(exc),
        )
    # Dry-run through the bounded repair loop (spec §89): on failure the model sees the error and the schema of
    # the referenced tables and may propose a corrected read-only query, at most 3 executions in total. Only a
    # handful of rows are read and none are returned; the analyst still runs the query explicitly.
    outcome = execute_with_repair(sql, store, llm, question=body.prompt, max_attempts=3, limit=5)
    attempts = [RepairAttemptOut.model_validate(a.model_dump()) for a in outcome.attempts]
    record_event(
        state.session_factory,
        category="ai",
        name="generate_sql",
        status="ok" if outcome.succeeded else "error",
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        detail={"attempts": len(attempts), "repaired": outcome.succeeded and len(attempts) > 1},
        error=None if outcome.succeeded else outcome.final_error,
    )
    if outcome.succeeded and outcome.final_sql:
        explanation = obj.explanation
        if len(attempts) > 1 and attempts[-1].explanation:
            explanation = f"{obj.explanation}\n\nRepaired after a failed dry run: {attempts[-1].explanation}"
        return GenerateSqlOut(
            sql=outcome.final_sql,
            explanation=explanation,
            tables=obj.tables,
            model=used.model,
            valid=True,
            executable=True,
            repaired=len(attempts) > 1,
            attempts=attempts,
        )
    return GenerateSqlOut(
        sql=sql,
        explanation=obj.explanation,
        tables=obj.tables,
        model=used.model,
        valid=True,
        validation_error=outcome.final_error,
        executable=False,
        repaired=False,
        attempts=attempts,
    )
