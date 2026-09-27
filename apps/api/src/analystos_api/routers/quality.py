"""Data Quality Center: rules (manual and suggested), runs, history and failing-row inspection.

Rules only *read* data. Suggested fixes are plain-language text; nothing is ever changed
automatically (spec section 11).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select

from ..db import utcnow
from ..deps import READ_ONLY_POST, DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import NotFound, Unprocessable
from ..models import Dataset, QualityRule, QualityRun
from ..schemas.common import ERROR_RESPONSES, ApiModel, OkResponse, TabularResult
from ..services.datasets import get_dataset
from ..services.observability import audit, record_event
from ..state import AppState

router = APIRouter(prefix="/workspaces/{workspace_id}/quality", tags=["quality"], responses=ERROR_RESPONSES)

RuleKind = Literal[
    "not_null",
    "unique",
    "range",
    "between",
    "fk_exists",
    "not_future",
    "allowed_values",
    "regex",
    "custom_sql",
]
_PARAM_FIELDS = (
    "min_value",
    "max_value",
    "inclusive",
    "allowed_values",
    "pattern",
    "ref_table",
    "ref_column",
    "sql",
)


class RuleParams(BaseModel):
    min_value: float | int | str | None = None
    max_value: float | int | str | None = None
    inclusive: bool = True
    allowed_values: list[Any] = Field(default_factory=list)
    pattern: str | None = None
    ref_table: str | None = None
    ref_column: str | None = None
    sql: str | None = Field(
        default=None, description="custom_sql: a read-only SELECT returning the failing rows"
    )


class RuleCreate(BaseModel):
    dataset_id: str
    name: str | None = Field(default=None, max_length=300)
    kind: RuleKind
    column: str | None = None
    params: RuleParams = Field(default_factory=RuleParams)
    severity: Literal["info", "warning", "error"] = "warning"
    description: str = ""


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    column: str | None = None
    params: RuleParams | None = None
    severity: Literal["info", "warning", "error"] | None = None
    status: Literal["active", "suggested", "disabled"] | None = None
    description: str | None = None


class RuleOut(ApiModel):
    id: str
    dataset_id: str | None
    table_name: str
    name: str
    kind: str
    column: str | None
    params: dict[str, Any]
    severity: str
    origin: str
    status: str
    description: str
    last_run_at: datetime | None
    last_passed: bool | None
    created_at: datetime
    updated_at: datetime


class RunOut(ApiModel):
    id: str
    rule_id: str
    status: Literal["passed", "failed", "error"] = Field(
        default="passed",
        description="error = the rule could not be executed (see `error`); not a data failure",
    )
    passed: bool
    failing_count: int
    total_count: int | None
    sql: str
    suggested_fix: str | None
    error: str | None
    dataset_version_id: str | None
    duration_ms: float
    created_at: datetime

    @model_validator(mode="after")
    def _status(self) -> RunOut:
        self.status = "error" if self.error else ("passed" if self.passed else "failed")
        return self


class RunAllRequest(BaseModel):
    dataset_id: str | None = None


class SuggestRequest(BaseModel):
    dataset_id: str


class DatasetQuality(BaseModel):
    dataset_id: str | None
    name: str
    rules: int
    failing: int
    passing: int


class QualitySummary(BaseModel):
    rules: int
    active: int
    suggested: int
    failing: int
    passing: int
    never_run: int
    errored: int = Field(default=0, description="Active rules whose last run could not execute")
    by_dataset: list[DatasetQuality]


def _get_rule(db: DbDep, ws: str, rule_id: str) -> QualityRule:
    r = db.get(QualityRule, rule_id)
    if r is None or r.workspace_id != ws:
        raise NotFound("Rule not found")
    return r


def _engine_rule(rule: QualityRule) -> Any:
    from analystos_engine.quality import Rule

    params = {k: v for k, v in (rule.params or {}).items() if k in _PARAM_FIELDS and v is not None}
    try:
        return Rule.model_validate(
            {
                "id": rule.id or "",
                "name": rule.name,
                "kind": rule.kind,
                "table": rule.table_name,
                "column": rule.column,
                "severity": rule.severity,
                "description": rule.description,
                **params,
            }
        )
    except (ValidationError, ValueError) as exc:
        raise Unprocessable(f"Invalid rule: {exc}", code="invalid_rule") from exc


def _default_name(kind: str, table: str, column: str | None, params: dict[str, Any]) -> str:
    target = f"{table}.{column}" if column else table
    if kind == "range" or kind == "between":
        return f"{target} in [{params.get('min_value')}, {params.get('max_value')}]"
    if kind == "fk_exists":
        return f"{target} exists in {params.get('ref_table')}.{params.get('ref_column')}"
    return f"{target} {kind.replace('_', ' ')}"


def execute_rule(db: DbDep, state: AppState, rule: QualityRule, user_id: str | None) -> QualityRun:
    from analystos_engine.quality import run_rule, suggest_fix

    engine_rule = _engine_rule(rule)
    start = time.perf_counter()
    result = run_rule(state.stores.get(rule.workspace_id), engine_rule)
    ds = db.get(Dataset, rule.dataset_id) if rule.dataset_id else None
    run = QualityRun(
        workspace_id=rule.workspace_id,
        rule_id=rule.id,
        passed=result.passed,
        failing_count=result.failing_count,
        total_count=result.total_count,
        sample_failing_rows=result.sample_failing_rows.model_dump(mode="json"),
        sql=result.sql,
        suggested_fix=None if result.error else suggest_fix(result),
        error=result.error,
        dataset_version_id=ds.current_version_id if ds else None,
        duration_ms=(time.perf_counter() - start) * 1000,
        run_by=user_id,
    )
    db.add(run)
    rule.last_run_at = utcnow()
    # A rule that could not run says nothing about the data: it is "error", neither passed nor failed (R-51).
    rule.last_passed = None if result.error else result.passed
    db.flush()
    record_event(
        state.session_factory,
        category="quality",
        name=f"rule:{rule.kind}",
        status="error" if result.error else "ok",
        duration_ms=run.duration_ms,
        workspace_id=rule.workspace_id,
        user_id=user_id,
        detail={"rule_id": rule.id, "passed": result.passed, "failing": result.failing_count},
        error=result.error,
    )
    return run


@router.get("/rules", response_model=list[RuleOut], operation_id="listQualityRules")
def list_rules(
    ctx: ViewerCtx, db: DbDep, dataset_id: str | None = None, status: str | None = None
) -> list[RuleOut]:
    stmt = select(QualityRule).where(QualityRule.workspace_id == ctx.workspace_id)
    if dataset_id:
        stmt = stmt.where(QualityRule.dataset_id == dataset_id)
    if status:
        stmt = stmt.where(QualityRule.status == status)
    return [
        RuleOut.model_validate(r) for r in db.scalars(stmt.order_by(QualityRule.table_name, QualityRule.name))
    ]


@router.post("/rules", response_model=RuleOut, status_code=201, operation_id="createQualityRule")
def create_rule(body: RuleCreate, ctx: EditorCtx, db: DbDep) -> RuleOut:
    ds = get_dataset(db, ctx.workspace_id, body.dataset_id)
    if body.column and body.column not in {c["name"] for c in ds.columns}:
        raise Unprocessable(f"Unknown column {body.column!r} in {ds.table_name}", code="unknown_column")
    params = body.params.model_dump(exclude_defaults=True)
    rule = QualityRule(
        workspace_id=ctx.workspace_id,
        dataset_id=ds.id,
        table_name=ds.table_name,
        name=body.name or _default_name(body.kind, ds.table_name, body.column, params),
        kind=body.kind,
        column=body.column,
        params=params,
        severity=body.severity,
        origin="manual",
        status="active",
        description=body.description,
        created_by=ctx.user_id,
    )
    _engine_rule(rule)  # validate shape (e.g. range needs bounds, regex must compile)
    db.add(rule)
    db.flush()
    audit(
        db,
        action="quality_rule.create",
        resource_type="quality_rule",
        resource_id=rule.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"kind": rule.kind, "table": rule.table_name, "column": rule.column},
    )
    return RuleOut.model_validate(rule)


@router.get("/rules/{rule_id}", response_model=RuleOut, operation_id="getQualityRule")
def get_rule(rule_id: str, ctx: ViewerCtx, db: DbDep) -> RuleOut:
    return RuleOut.model_validate(_get_rule(db, ctx.workspace_id, rule_id))


@router.patch("/rules/{rule_id}", response_model=RuleOut, operation_id="updateQualityRule")
def update_rule(rule_id: str, body: RuleUpdate, ctx: EditorCtx, db: DbDep) -> RuleOut:
    rule = _get_rule(db, ctx.workspace_id, rule_id)
    if body.name is not None:
        rule.name = body.name
    if body.column is not None:
        rule.column = body.column
    if body.params is not None:
        rule.params = body.params.model_dump(exclude_defaults=True)
    if body.severity is not None:
        rule.severity = body.severity
    if body.status is not None:
        rule.status = body.status
    if body.description is not None:
        rule.description = body.description
    _engine_rule(rule)
    audit(
        db,
        action="quality_rule.update",
        resource_type="quality_rule",
        resource_id=rule.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail=body.model_dump(exclude_none=True),
    )
    db.flush()
    return RuleOut.model_validate(rule)


@router.delete("/rules/{rule_id}", response_model=OkResponse, operation_id="deleteQualityRule")
def delete_rule(rule_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    rule = _get_rule(db, ctx.workspace_id, rule_id)
    db.delete(rule)
    audit(
        db,
        action="quality_rule.delete",
        resource_type="quality_rule",
        resource_id=rule_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
    )
    return OkResponse()


@router.post("/rules/{rule_id}/accept", response_model=RuleOut, operation_id="acceptQualityRule")
def accept_rule(rule_id: str, ctx: EditorCtx, db: DbDep) -> RuleOut:
    """Accept a suggested rule so it runs with the others. Accepting never changes data."""
    rule = _get_rule(db, ctx.workspace_id, rule_id)
    rule.status = "active"
    audit(
        db,
        action="quality_rule.accept",
        resource_type="quality_rule",
        resource_id=rule.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
    )
    db.flush()
    return RuleOut.model_validate(rule)


@router.post(
    "/rules/{rule_id}/run", response_model=RunOut, operation_id="runQualityRule", openapi_extra=READ_ONLY_POST
)
def run_one(rule_id: str, ctx: ViewerCtx, db: DbDep, state: StateDep) -> RunOut:
    rule = _get_rule(db, ctx.workspace_id, rule_id)
    return RunOut.model_validate(execute_rule(db, state, rule, ctx.user_id))


@router.post(
    "/run", response_model=list[RunOut], operation_id="runQualityRules", openapi_extra=READ_ONLY_POST
)
def run_all(body: RunAllRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> list[RunOut]:
    """Run every active rule (optionally for one dataset)."""
    stmt = select(QualityRule).where(
        QualityRule.workspace_id == ctx.workspace_id, QualityRule.status == "active"
    )
    if body.dataset_id:
        stmt = stmt.where(QualityRule.dataset_id == body.dataset_id)
    return [RunOut.model_validate(execute_rule(db, state, r, ctx.user_id)) for r in db.scalars(stmt).all()]


@router.post("/suggest", response_model=list[RuleOut], operation_id="suggestQualityRules")
def suggest(body: SuggestRequest, ctx: EditorCtx, db: DbDep) -> list[RuleOut]:
    """Create suggested rules from the dataset profile. They stay inactive until accepted."""
    from analystos_engine.quality import suggest_rules
    from analystos_engine.types import TableProfile

    ds = get_dataset(db, ctx.workspace_id, body.dataset_id)
    if not ds.profile or ds.profile_status != "ready":
        raise Unprocessable("The dataset has no profile yet; profile it first", code="profile_pending")
    existing = {
        (r.kind, r.column, r.table_name)
        for r in db.scalars(
            select(QualityRule).where(
                QualityRule.workspace_id == ctx.workspace_id, QualityRule.dataset_id == ds.id
            )
        )
    }
    created: list[QualityRule] = []
    for s in suggest_rules(TableProfile.model_validate(ds.profile)):
        key = (s.kind, s.column, ds.table_name)
        if key in existing:
            continue
        existing.add(key)
        params = {
            k: getattr(s, k)
            for k in _PARAM_FIELDS
            if getattr(s, k) not in (None, [], True) or (k == "inclusive" and getattr(s, k) is False)
        }
        rule = QualityRule(
            workspace_id=ctx.workspace_id,
            dataset_id=ds.id,
            table_name=ds.table_name,
            name=s.name or _default_name(s.kind, ds.table_name, s.column, params),
            kind=s.kind,
            column=s.column,
            params=params,
            severity=s.severity,
            origin="suggested",
            status="suggested",
            description=s.rationale or s.description,
            created_by=ctx.user_id,
        )
        db.add(rule)
        created.append(rule)
    db.flush()
    return [RuleOut.model_validate(r) for r in created]


@router.get("/runs", response_model=list[RunOut], operation_id="listQualityRuns")
def list_runs(
    ctx: ViewerCtx,
    db: DbDep,
    rule_id: str | None = None,
    dataset_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[RunOut]:
    stmt = select(QualityRun).where(QualityRun.workspace_id == ctx.workspace_id)
    if rule_id:
        stmt = stmt.where(QualityRun.rule_id == rule_id)
    if dataset_id:
        stmt = stmt.join(QualityRule, QualityRule.id == QualityRun.rule_id).where(
            QualityRule.dataset_id == dataset_id
        )
    return [
        RunOut.model_validate(r) for r in db.scalars(stmt.order_by(QualityRun.created_at.desc()).limit(limit))
    ]


def _get_run(db: DbDep, ws: str, run_id: str) -> QualityRun:
    run = db.get(QualityRun, run_id)
    if run is None or run.workspace_id != ws:
        raise NotFound("Run not found")
    return run


@router.get("/runs/{run_id}", response_model=RunOut, operation_id="getQualityRun")
def get_run(run_id: str, ctx: ViewerCtx, db: DbDep) -> RunOut:
    return RunOut.model_validate(_get_run(db, ctx.workspace_id, run_id))


@router.get("/runs/{run_id}/failing-rows", response_model=TabularResult, operation_id="getQualityFailingRows")
def failing_rows(run_id: str, ctx: ViewerCtx, db: DbDep) -> TabularResult:
    """A sample of the rows that violated the rule when it ran (inspect, never auto-fixed)."""
    run = _get_run(db, ctx.workspace_id, run_id)
    sample = run.sample_failing_rows or {}
    return TabularResult.model_validate(
        {
            "columns": sample.get("columns", []),
            "rows": sample.get("rows", []),
            "row_count": sample.get("row_count", 0),
            "truncated": run.failing_count > sample.get("row_count", 0),
            "elapsed_ms": sample.get("elapsed_ms", 0.0),
            "sql": run.sql,
        }
    )


@router.get("/summary", response_model=QualitySummary, operation_id="getQualitySummary")
def summary(ctx: ViewerCtx, db: DbDep) -> QualitySummary:
    rules = db.scalars(select(QualityRule).where(QualityRule.workspace_id == ctx.workspace_id)).all()
    names = {
        d.id: d.name for d in db.scalars(select(Dataset).where(Dataset.workspace_id == ctx.workspace_id))
    }
    active = [r for r in rules if r.status == "active"]
    by: dict[str | None, DatasetQuality] = {}
    for r in active:
        item = by.setdefault(
            r.dataset_id,
            DatasetQuality(
                dataset_id=r.dataset_id,
                name=names.get(r.dataset_id or "", r.table_name),
                rules=0,
                failing=0,
                passing=0,
            ),
        )
        item.rules += 1
        if r.last_passed is False:
            item.failing += 1
        elif r.last_passed is True:
            item.passing += 1
    return QualitySummary(
        rules=len(rules),
        active=len(active),
        suggested=sum(1 for r in rules if r.status == "suggested"),
        failing=sum(1 for r in active if r.last_passed is False),
        passing=sum(1 for r in active if r.last_passed is True),
        never_run=sum(1 for r in active if r.last_run_at is None),
        errored=sum(1 for r in active if r.last_run_at is not None and r.last_passed is None),
        by_dataset=sorted(by.values(), key=lambda d: (-d.failing, d.name)),
    )


def failing_rules(db: DbDep, workspace_id: str, limit: int = 10) -> list[QualityRule]:
    return list(
        db.scalars(
            select(QualityRule)
            .where(
                QualityRule.workspace_id == workspace_id,
                QualityRule.status == "active",
                QualityRule.last_passed.is_(False),
            )
            .order_by(QualityRule.last_run_at.desc())
            .limit(limit)
        )
    )
