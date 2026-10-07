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


class PlanVariance(BaseModel):
    """One metric's actual against its plan metric (``plan_for``) over the latest closed quarter."""

    metric_id: str
    label: str
    format: str
    plan_metric_id: str
    plan_label: str
    stakeholder_term: str = Field(
        default="",
        description="The plain word for the metric from the glossary and synonyms (see stakeholder_term)",
    )
    plan_role: str = Field(
        description="plan, budget, target or forecast (from the plan metric's tags), else other"
    )
    period: str
    window_start: dt.date
    window_end: dt.date = Field(description="Exclusive end of the half-open window")
    actual: float | None
    plan: float | None
    variance: float | None = Field(description="actual - plan")
    variance_pct: float | None = Field(description="(actual - plan) / |plan|")
    higher_is_better: bool
    status: str = Field(
        description="on_plan (within the tolerance), favorable or unfavorable, judged by higher_is_better"
    )
    tolerance: float
    sql_actual: str
    sql_plan: str
    metric_versions: dict[str, str] = Field(default_factory=dict)


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
    plan_variances: list[PlanVariance] = Field(default_factory=list)
    plan_note: str | None = None


# How close to plan still counts as "on plan": the same +/-2% the investigator uses for "flat".
PLAN_TOLERANCE = 0.02
# Preference when several metrics plan the same actual; any other role ranks after these.
_PLAN_ROLES = ("plan", "budget", "target", "forecast")


def plan_pairs(model: Any) -> dict[str, tuple[str, str]]:
    """Actual metric id -> (plan metric id, role) for every metric a plan metric declares ``plan_for``.

    When several metrics plan the same actual, a ``plan`` beats a ``budget``, a ``target`` and a
    ``forecast``, in that order; a metric with none of those tags ranks last."""

    def rank(role: str) -> int:
        return _PLAN_ROLES.index(role) if role in _PLAN_ROLES else len(_PLAN_ROLES)

    out: dict[str, tuple[str, str]] = {}
    for m in model.metrics:
        target = getattr(m, "plan_for", None)
        if not target or not model.has_metric(target):
            continue
        tags = {t.lower() for t in m.tags}
        role = next((r for r in _PLAN_ROLES if r in tags), "other")
        prev = out.get(target)
        if prev is None or rank(role) < rank(prev[1]):
            out[target] = (m.id, role)
    return out


def plan_metric_order(model: Any, pairs: dict[str, tuple[str, str]], kpis: list[str]) -> list[list[str]]:
    """Actual metrics with a plan, grouped in reading order:

    1. the canonical revenue metric;
    2. the lead metric: of the headline metrics that root a metric tree, the one whose tree has the most
       planned parts (the widest explanation of a variance, e.g. operating profit);
    3. the lead tree's direct parts (e.g. gross margin, operating expenses);
    4. the lead tree's deeper parts (the lines), which the caller ranks by the size of their variance;
    5. the other headline metrics.
    Each metric appears once, in its first group; the five groups are always returned (some may be empty)."""
    from ..services.reports import canonical_revenue_metric

    seen: set[str] = set()

    def keep(ids: list[str]) -> list[str]:
        out = []
        for mid in ids:
            if mid in pairs and mid not in seen:
                seen.add(mid)
                out.append(mid)
        return out

    revenue = canonical_revenue_metric(model, list(pairs))
    heads = [k for k in kpis if k in pairs]

    def planned_parts(head: str) -> int:
        tree = model.get_tree(head)
        return sum(1 for m in tree.metric_ids() if m in pairs) if tree else 0

    rooted = sorted((h for h in heads if planned_parts(h) > 1), key=lambda h: -planned_parts(h))
    lead = rooted[0] if rooted else None
    direct: list[str] = []
    deeper: list[str] = []
    if lead:
        tree = model.get_tree(lead)
        direct = [e.child for e in tree.children(lead)]
        queue, visited = list(direct), {lead, *direct}
        while queue:
            parent = queue.pop(0)
            for edge in tree.children(parent):
                if edge.child not in visited:
                    visited.add(edge.child)
                    deeper.append(edge.child)
                    queue.append(edge.child)
    groups = [
        keep([revenue] if revenue else []),
        keep([lead] if lead else []),
        keep(direct),
        keep(deeper),
        keep(heads),
    ]
    return groups


def order_plan_variances(groups: list[list[str]], measured: dict[str, Any]) -> list[Any]:
    """Flatten ``plan_metric_order`` groups into display order; the lead tree's lines (the fourth group)
    are ranked by the size of their variance, largest first."""
    out: list[Any] = []
    for i, group in enumerate(groups):
        items = [measured[m] for m in group if m in measured]
        if i == 3:
            items.sort(key=lambda v: -abs(v.variance or 0.0))
        out.extend(items)
    return out


def stakeholder_term(model: Any, metric_id: str) -> str:
    """The word a business stakeholder uses for a metric, read from the governed vocabulary:

    1. a glossary term that points at the metric under a name other than its label ("Net Profit" for
       Operating Profit);
    2. else the first synonym of the glossary term that carries the metric's own name ("sales" for Revenue);
    3. else the metric's first synonym;
    4. else its label.
    Lower-cased, for use inside a sentence. Used to phrase the home page's question in plain words; the
    question still goes through the interpreter, which resolves the word back through the same vocabulary.
    """
    m = model.get_metric(metric_id)
    label = (m.display_name or metric_id).strip()
    terms = [g for g in model.glossary if g.metric_id == metric_id]
    for g in terms:
        if g.term.strip().lower() != label.lower():
            return g.term.strip().lower()
    for g in terms:
        if g.synonyms:
            return g.synonyms[0].strip().lower()
    if m.synonyms:
        return m.synonyms[0].strip().lower()
    return label.lower()


def plan_status(variance_pct: float | None, variance: float | None, higher_is_better: bool) -> str:
    if variance_pct is not None and abs(variance_pct) <= PLAN_TOLERANCE:
        return "on_plan"
    if variance is None or variance == 0:
        return "on_plan"
    return "favorable" if (variance > 0) == higher_is_better else "unfavorable"


def _plan_variances(
    db: DbDep, state: StateDep, ws: Workspace, limit: int = 6
) -> tuple[list[PlanVariance], str | None]:
    """Actual vs plan for the latest closed quarter, each side an executed semantic query."""
    from analystos_engine.analysis.compare import compare_metrics
    from analystos_engine.calendar import CalendarError, resolve_period
    from analystos_investigator.semantic_graph import time_dimension_for_metric

    from ..services.investigations import reference_date
    from ..services.reports import _kpi_metrics
    from ..services.semantic import build_model

    built = build_model(db, ws)
    model = built.model
    pairs = plan_pairs(model)
    if not pairs:
        return [], None
    try:
        # "last quarter" relative to the reference date is always a closed quarter.
        window = resolve_period("last quarter", reference_date(ws), model.calendar)
    except CalendarError:
        return [], None
    store = state.stores.get(ws.id)
    groups = plan_metric_order(model, pairs, _kpi_metrics(built, limit=20))
    measured: dict[str, PlanVariance] = {}
    for mid in [m for g in groups for m in g][:16]:
        plan_id, role = pairs[mid]
        tdim = time_dimension_for_metric(model, mid)
        w = window.with_dimension(tdim) if tdim else window
        try:
            c = compare_metrics(store, model, mid, plan_id, w)
        except Exception:  # noqa: BLE001 - a metric that cannot be compared is skipped, never estimated
            continue
        if c.current is None or c.baseline is None:
            continue
        m = model.get_metric(mid)
        measured[mid] = PlanVariance(
            metric_id=mid,
            label=m.display_name,
            format=m.format,
            stakeholder_term=stakeholder_term(model, mid),
            plan_metric_id=plan_id,
            plan_label=model.get_metric(plan_id).display_name,
            plan_role=role,
            period=window.display(),
            window_start=window.start,
            window_end=window.end,
            actual=c.current,
            plan=c.baseline,
            variance=c.abs_change,
            variance_pct=c.pct_change,
            higher_is_better=m.higher_is_better,
            status=plan_status(c.pct_change, c.abs_change, m.higher_is_better),
            tolerance=PLAN_TOLERANCE,
            sql_actual=c.sql_current,
            sql_plan=c.sql_baseline,
            metric_versions=c.metric_versions,
        )
    out = order_plan_variances(groups, measured)[:limit]
    note = (
        f"{window.display()} (the latest closed quarter) against plan. On plan means within "
        f"±{PLAN_TOLERANCE:.0%} of the plan value."
        if out
        else None
    )
    return out, note


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
    try:
        plan_vars, plan_note = _plan_variances(db, state, ctx.workspace)
    except Exception as exc:  # noqa: BLE001 - same: home renders without the plan comparison
        plan_vars, plan_note = [], f"Plan comparison unavailable: {exc}"
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
        plan_variances=plan_vars,
        plan_note=plan_note,
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
