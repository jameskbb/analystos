"""Platform endpoints: health, system diagnostics, demo loading, workspace home, diagnostic events and audit."""

from __future__ import annotations

import datetime as dt
import importlib
import platform as py_platform
import statistics
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from .. import __version__
from ..db import utcnow
from ..deps import DbDep, EditorCtx, PrincipalDep, StateDep, ViewerCtx
from ..errors import Forbidden, NotFound, ServiceUnavailable
from ..models import (
    AuditLog,
    Dashboard,
    Dataset,
    DiagnosticEvent,
    Finding,
    Investigation,
    Job,
    Membership,
    MetricRecord,
    QualityRule,
    RelationshipRecord,
    SavedQuery,
    Workspace,
)
from ..schemas.common import ERROR_RESPONSES, ApiModel, JobOut, Page
from ..schemas.workspaces import WorkspaceOut
from ..services.demo import demo_available, load_job, wipe_workspace_content
from ..services.jobs_view import job_accepted
from ..services.observability import audit
from ..services.workspaces import create_workspace
from ..services.writer import writer_for

router = APIRouter(tags=["platform"], responses=ERROR_RESPONSES)


class Health(BaseModel):
    status: str
    version: str


class SystemDiagnostics(BaseModel):
    version: str
    python: str
    database_dialect: str
    migration_revision: str | None
    auth_mode: str
    ai_server_enabled: bool
    ai_key_configured: bool
    job_execution: str
    job_workers: int
    pdf_export: bool
    demo_available: bool
    limits: dict[str, Any]
    jobs: dict[str, int]


@router.get("/health", response_model=Health, operation_id="healthV1")
def health() -> Health:
    return Health(status="ok", version=__version__)


@router.get("/diagnostics", response_model=SystemDiagnostics, operation_id="systemDiagnostics")
def diagnostics(principal: PrincipalDep, db: DbDep, state: StateDep) -> SystemDiagnostics:
    from ..services.exports import weasyprint_available

    try:
        rev = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001
        rev = None
    jobs: dict[str, int] = {
        str(k): int(v) for k, v in db.execute(select(Job.status, func.count()).group_by(Job.status))
    }
    s = state.settings
    return SystemDiagnostics(
        version=__version__,
        python=py_platform.python_version(),
        database_dialect=state.engine.dialect.name,
        migration_revision=rev,
        auth_mode=s.auth_mode,
        ai_server_enabled=s.ai_enabled,
        ai_key_configured=s.ai_key_available(),
        job_execution=s.job_execution,
        job_workers=s.job_workers,
        pdf_export=weasyprint_available(),
        demo_available=demo_available(),
        limits={
            "max_upload_mb": s.max_upload_mb,
            "query_row_limit": s.query_row_limit,
            "query_timeout_s": s.query_timeout_s,
            "python_timeout_s": s.python_timeout_s,
            "python_mem_mb": s.python_mem_mb,
        },
        jobs={str(k): int(v) for k, v in jobs.items()},
    )


# ------------------------------------------------------------------------------------------ demo


class DemoStatus(BaseModel):
    available: bool
    workspaces: list[WorkspaceOut]


class DemoLoadRequest(BaseModel):
    workspace_id: str | None = Field(default=None, description="Load into this workspace (owner only)")
    reset: bool = Field(default=False, description="Wipe the workspace's data and definitions first")
    name: str | None = Field(default=None, max_length=200)
    seed: int = Field(default=42, ge=0, le=2**31)


class DemoLoadAccepted(BaseModel):
    workspace_id: str
    job: JobOut
    poll_url: str


def _demo_workspaces(db: DbDep, user_id: str) -> list[tuple[Workspace, str]]:
    rows = db.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id)
    ).all()
    return [(ws, role) for ws, role in rows if (ws.settings or {}).get("demo")]


@router.get("/demo/status", response_model=DemoStatus, operation_id="demoStatus", tags=["demo"])
def demo_status(principal: PrincipalDep, db: DbDep) -> DemoStatus:
    items = [
        WorkspaceOut(
            id=ws.id,
            name=ws.name,
            description=ws.description,
            role=role,  # type: ignore[arg-type]  # roles are validated on write
            created_at=ws.created_at,
            updated_at=ws.updated_at,
        )
        for ws, role in _demo_workspaces(db, principal.user.id)
    ]
    return DemoStatus(available=demo_available(), workspaces=items)


@router.post(
    "/demo/load", response_model=DemoLoadAccepted, status_code=202, operation_id="loadDemo", tags=["demo"]
)
def demo_load(body: DemoLoadRequest, principal: PrincipalDep, db: DbDep, state: StateDep) -> DemoLoadAccepted:
    """Generate Summit Supply Co. and bootstrap a workspace: datasets, versioned semantic model, metric trees,
    glossary, approved relationships, DQ rules and profiles. Returns the workspace id immediately."""
    if not demo_available():
        raise ServiceUnavailable("The analystos-demo package is not installed", code="demo_unavailable")
    if principal.read_only:
        raise Forbidden("This API token is read-only", code="token_read_only")
    analystos_demo = importlib.import_module("analystos_demo")

    user = principal.user
    ws: Workspace | None = None
    if body.workspace_id:
        row = db.execute(
            select(Workspace, Membership.role)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Workspace.id == body.workspace_id, Membership.user_id == user.id)
        ).first()
        if row is None:
            raise NotFound("Workspace not found")
        if row[1] != "owner":
            raise Forbidden("Loading demo data requires the owner role", code="insufficient_role")
        ws = row[0]
    elif not body.reset:
        existing = _demo_workspaces(db, user.id)
        ws = next((w for w, role in existing if role == "owner"), None)
    if principal.token_workspace_id and (ws is None or ws.id != principal.token_workspace_id):
        raise Forbidden("Workspace-scoped tokens cannot create workspaces", code="token_scope")
    if ws is None:
        ws = create_workspace(
            db,
            state,
            user,
            body.name or analystos_demo.DEMO_WORKSPACE_NAME,
            "Synthetic B2B building-materials distributor with planted analytical stories.",
        )
    elif body.reset:
        wipe_workspace_content(db, state, ws.id)
    audit(
        db,
        action="demo.load",
        resource_type="workspace",
        resource_id=ws.id,
        workspace_id=ws.id,
        user_id=user.id,
        actor=user.email,
        detail={"reset": body.reset, "seed": body.seed},
    )
    ws_id = ws.id
    job_id = state.jobs.submit(
        kind="demo_load",
        fn=load_job(state, ws_id, user.id, body.seed),
        workspace_id=ws_id,
        user_id=user.id,
        params=body.model_dump(),
        resource_type="workspace",
        resource_id=ws_id,
        db=db,
    )
    acc = job_accepted(state, ws_id, job_id)
    return DemoLoadAccepted(workspace_id=ws_id, job=acc.job, poll_url=acc.poll_url)


# ------------------------------------------------------------------------------------------ home


class MetricChange(BaseModel):
    metric_id: str
    label: str
    format: str
    period: str
    baseline_period: str
    current: float | None
    baseline: float | None
    abs_change: float | None
    pct_change: float | None
    higher_is_better: bool
    sql: str


class HomeSummary(BaseModel):
    workspace: WorkspaceOut
    counts: dict[str, int]
    datasets: list[dict[str, Any]]
    recent_investigations: list[dict[str, Any]]
    recent_findings: list[dict[str, Any]]
    quality_failures: list[dict[str, Any]]
    dashboards: list[dict[str, Any]]
    pending_relationships: int
    running_jobs: list[JobOut]
    changes: list[MetricChange]
    changes_note: str | None = None


def _changes(db: DbDep, state: StateDep, ws: Workspace) -> tuple[list[MetricChange], str | None]:
    from analystos_engine.analysis.compare import compare_periods
    from analystos_engine.calendar import month_window, previous_period
    from analystos_engine.store import quote_ident
    from analystos_investigator.semantic_graph import time_dimension_for_metric

    from ..services.investigations import reference_date
    from ..services.reports import _kpi_metrics, order_by_materiality
    from ..services.semantic import build_model

    built = build_model(db, ws)
    model = built.model
    store = state.stores.get(ws.id)
    out: list[MetricChange] = []
    max_dates: dict[str, dt.date | None] = {}
    # Nothing after the day before "today" (or the workspace's pinned reference date) counts: the current month
    # is in progress and rows dated in the future are not facts yet (review R-27).
    horizon = reference_date(ws) - dt.timedelta(days=1)
    for mid in _kpi_metrics(built, limit=8):
        tdim = time_dimension_for_metric(model, mid)
        if tdim is None:
            continue
        if tdim not in max_dates:
            d = model.get_dimension(tdim)
            table = model.get_entity(d.entity).table
            try:
                v = store.execute_read(
                    f"SELECT CAST(max({d.expr}) AS DATE) FROM {quote_ident(table)}",
                    None,
                    1,
                    state.settings.query_timeout_s,
                ).scalar()
            except Exception:  # noqa: BLE001
                v = None
            max_dates[tdim] = dt.date.fromisoformat(str(v)[:10]) if v else None
        last = max_dates[tdim]
        if last is None:
            continue
        last = min(last, horizon)
        if (
            last + dt.timedelta(days=1)
        ).month != last.month:  # the data ends on a month end: that month is full
            month = (last.year, last.month)
        else:  # the last month in the data is partial: use the one before
            prev = dt.date(last.year, last.month, 1) - dt.timedelta(days=1)
            month = (prev.year, prev.month)
        window = month_window(*month).with_dimension(tdim)
        base = previous_period(window, "pop").with_dimension(tdim)
        try:
            c = compare_periods(store, model, mid, window, base, kind="pop")
        except Exception:  # noqa: BLE001 - a metric that cannot be compared is skipped, not estimated
            continue
        m = model.get_metric(mid)
        out.append(
            MetricChange(
                metric_id=mid,
                label=m.display_name,
                format=m.format,
                period=window.display(),
                baseline_period=base.display(),
                current=c.current,
                baseline=c.baseline,
                abs_change=c.abs_change,
                pct_change=c.pct_change,
                higher_is_better=m.higher_is_better,
                sql=c.sql_current,
            )
        )
    ranked = order_by_materiality(model, [c.model_dump() for c in out])
    by_id = {c.metric_id: c for c in out}
    out = [by_id[k["metric_id"]] for k in ranked]
    note = (
        f"Last full month in the data up to {horizon.isoformat()} (the day before the reference date), "
        "compared with the month before; ordered by materiality."
        if out
        else None
    )
    return out, note


@router.get(
    "/workspaces/{workspace_id}/home", response_model=HomeSummary, operation_id="getHome", tags=["workspaces"]
)
def home(ctx: ViewerCtx, db: DbDep, state: StateDep) -> HomeSummary:
    """What data do I have, what changed, what needs attention, what was I investigating."""
    ws_id = ctx.workspace_id
    datasets = db.scalars(
        select(Dataset).where(Dataset.workspace_id == ws_id).order_by(Dataset.updated_at.desc())
    ).all()
    invs = db.scalars(
        select(Investigation)
        .where(Investigation.workspace_id == ws_id)
        .order_by(Investigation.updated_at.desc())
        .limit(8)
    ).all()
    finds = db.scalars(
        select(Finding).where(Finding.workspace_id == ws_id).order_by(Finding.updated_at.desc()).limit(8)
    ).all()
    failing = db.scalars(
        select(QualityRule)
        .where(
            QualityRule.workspace_id == ws_id,
            QualityRule.status == "active",
            QualityRule.last_passed.is_(False),
        )
        .order_by(QualityRule.severity, QualityRule.name)
        .limit(10)
    ).all()
    dashes = db.scalars(
        select(Dashboard)
        .where(Dashboard.workspace_id == ws_id)
        .order_by(Dashboard.updated_at.desc())
        .limit(8)
    ).all()
    pending = (
        db.scalar(
            select(func.count())
            .select_from(RelationshipRecord)
            .where(RelationshipRecord.workspace_id == ws_id, RelationshipRecord.status == "suggested")
        )
        or 0
    )
    jobs = db.scalars(
        select(Job)
        .where(Job.workspace_id == ws_id, Job.status.in_(["queued", "running"]))
        .order_by(Job.created_at.desc())
        .limit(10)
    ).all()

    def count(model: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == ws_id)) or 0)

    try:
        changes, note = _changes(db, state, ctx.workspace)
    except Exception as exc:  # noqa: BLE001 - home must render even if a metric cannot be computed
        changes, note = [], f"Changes unavailable: {exc}"
    ws = ctx.workspace
    return HomeSummary(
        workspace=WorkspaceOut(
            id=ws.id,
            name=ws.name,
            description=ws.description,
            role=ctx.role,  # type: ignore[arg-type]
            created_at=ws.created_at,
            updated_at=ws.updated_at,
        ),
        counts={
            "datasets": len(datasets),
            "metrics": count(MetricRecord),
            "investigations": count(Investigation),
            "findings": count(Finding),
            "dashboards": count(Dashboard),
            "saved_queries": count(SavedQuery),
            "quality_rules": count(QualityRule),
        },
        datasets=[
            {
                "id": d.id,
                "name": d.name,
                "table_name": d.table_name,
                "row_count": d.row_count,
                "updated_at": d.updated_at,
                "profile_status": d.profile_status,
                "issue_count": len((d.profile or {}).get("issues", []) or [])
                if isinstance(d.profile, dict)
                else 0,
            }
            for d in datasets
        ],
        recent_investigations=[
            {
                "id": i.id,
                "title": i.title,
                "question": i.question,
                "status": i.status,
                "brief_answer": (i.document or {}).get("brief_answer"),
                "updated_at": i.updated_at,
            }
            for i in invs
        ],
        recent_findings=[
            {
                "id": f.id,
                "statement": f.statement,
                "statement_type": f.statement_type,
                "evidence_strength": f.evidence_strength,
                "status": f.status,
                "updated_at": f.updated_at,
            }
            for f in finds
        ],
        quality_failures=[
            {
                "rule_id": r.id,
                "name": r.name,
                "table_name": r.table_name,
                "severity": r.severity,
                "dataset_id": r.dataset_id,
                "last_run_at": r.last_run_at,
            }
            for r in failing
        ],
        dashboards=[{"id": d.id, "name": d.name, "updated_at": d.updated_at} for d in dashes],
        pending_relationships=int(pending),
        running_jobs=[JobOut.model_validate(j) for j in jobs],
        changes=changes,
        changes_note=note,
    )


# ------------------------------------------------------------------------------------------ diagnostics / audit


class EventOut(ApiModel):
    id: str
    category: str
    name: str
    status: str
    duration_ms: float
    detail: dict[str, Any]
    error: str | None
    request_id: str | None
    user_id: str | None
    created_at: dt.datetime


class CategoryStats(BaseModel):
    category: str
    count: int
    errors: int
    error_rate: float
    p50_ms: float | None
    p95_ms: float | None


class DiagnosticsSummary(BaseModel):
    hours: int
    total: int
    errors: int
    categories: list[CategoryStats]
    recent_failures: list[EventOut]


class AuditOut(ApiModel):
    id: str
    actor: str
    user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    detail: dict[str, Any]
    ip: str | None
    request_id: str | None
    created_at: dt.datetime


@router.get(
    "/workspaces/{workspace_id}/diagnostics/events",
    response_model=list[EventOut],
    operation_id="listDiagnosticEvents",
    tags=["diagnostics"],
)
def events(
    ctx: ViewerCtx,
    db: DbDep,
    state: StateDep,
    category: str | None = None,
    status: str | None = None,
    since: dt.datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[EventOut]:
    """Structured events: imports, profiling, SQL, Python, AI calls, investigation steps, jobs, failures."""
    writer_for(state.session_factory).flush(5)
    stmt = select(DiagnosticEvent).where(DiagnosticEvent.workspace_id == ctx.workspace_id)
    if category:
        stmt = stmt.where(DiagnosticEvent.category == category)
    if status:
        stmt = stmt.where(DiagnosticEvent.status == status)
    if since:
        stmt = stmt.where(DiagnosticEvent.created_at >= since)
    return [
        EventOut.model_validate(e)
        for e in db.scalars(stmt.order_by(DiagnosticEvent.created_at.desc()).limit(limit))
    ]


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(q * 100) - 1], 2)


@router.get(
    "/workspaces/{workspace_id}/diagnostics/summary",
    response_model=DiagnosticsSummary,
    operation_id="diagnosticsSummary",
    tags=["diagnostics"],
)
def diagnostics_summary(
    ctx: ViewerCtx, db: DbDep, state: StateDep, hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24
) -> DiagnosticsSummary:
    writer_for(state.session_factory).flush(5)
    since = utcnow() - dt.timedelta(hours=hours)
    rows = db.scalars(
        select(DiagnosticEvent).where(
            DiagnosticEvent.workspace_id == ctx.workspace_id, DiagnosticEvent.created_at >= since
        )
    ).all()
    cats: dict[str, list[DiagnosticEvent]] = {}
    for r in rows:
        cats.setdefault(r.category, []).append(r)
    stats = []
    for cat, items in sorted(cats.items()):
        durations = sorted(i.duration_ms for i in items)
        errors = sum(1 for i in items if i.status != "ok")
        stats.append(
            CategoryStats(
                category=cat,
                count=len(items),
                errors=errors,
                error_rate=round(errors / len(items), 4),
                p50_ms=_pct(durations, 0.5),
                p95_ms=_pct(durations, 0.95),
            )
        )
    failures = sorted([r for r in rows if r.status != "ok"], key=lambda r: r.created_at, reverse=True)[:20]
    return DiagnosticsSummary(
        hours=hours,
        total=len(rows),
        errors=sum(s.errors for s in stats),
        categories=stats,
        recent_failures=[EventOut.model_validate(f) for f in failures],
    )


@router.get(
    "/workspaces/{workspace_id}/audit",
    response_model=Page[AuditOut],
    operation_id="listAudit",
    tags=["audit"],
)
def audit_log(
    ctx: EditorCtx,
    db: DbDep,
    action: str | None = None,
    resource_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AuditOut]:
    stmt = select(AuditLog).where(AuditLog.workspace_id == ctx.workspace_id)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)).all()
    return Page[AuditOut](
        items=[AuditOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )
