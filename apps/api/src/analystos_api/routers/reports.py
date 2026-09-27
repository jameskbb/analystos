"""Reports: story blocks, executive summary, monthly business review and the review workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import utcnow
from ..deps import DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import Conflict, Unprocessable
from ..models import Finding, Report
from ..schemas.common import ERROR_RESPONSES, ApiModel, OkResponse
from ..services.investigations import get_investigation
from ..services.observability import audit
from ..services.reports import (
    block,
    business_review,
    executive_summary_report,
    get_report,
    report_from_investigation,
)
from .artifacts import resolve_artifact

router = APIRouter(prefix="/workspaces/{workspace_id}/reports", tags=["reports"], responses=ERROR_RESPONSES)

BLOCK_TYPES = {
    "heading",
    "narrative",
    "finding",
    "kpi",
    "kpi_overview",
    "chart",
    "table",
    "methodology",
    "sources",
    "summary",
    "anomalies",
    "open_questions",
    "image",
}


class ReportSummary(ApiModel):
    id: str
    title: str
    kind: str
    status: Literal["draft", "in_review", "published"]
    period: str | None
    investigation_id: str | None
    version_no: int
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReportOut(ReportSummary):
    blocks: list[dict[str, Any]] = Field(description="Ordered blocks; each has id and type (see API.md)")


class ReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    kind: Literal["custom", "business_review", "investigation"] = "custom"
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    investigation_id: str | None = None


class ReportUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=400)
    blocks: list[dict[str, Any]] | None = None


class ExecSummaryRequest(BaseModel):
    finding_ids: list[str] | None = None
    investigation_id: str | None = None
    title: str | None = None


class BusinessReviewRequest(BaseModel):
    period: str = Field(description="Month such as '2026-08' or 'August 2026' (any calendar expression)")
    title: str | None = None


class FromInvestigationRequest(BaseModel):
    investigation_id: str


def _normalise_blocks(db: DbDep, workspace_id: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check block types and that every referenced finding/artifact belongs to this workspace (review R-45)."""
    out = []
    for b in blocks:
        kind = b.get("type")
        if kind not in BLOCK_TYPES:
            raise Unprocessable(
                f"Unknown block type {kind!r}; expected one of {sorted(BLOCK_TYPES)}", code="invalid_block"
            )
        fid = b.get("finding_id")
        if fid is not None:
            f = db.get(Finding, fid)
            if f is None or f.workspace_id != workspace_id:
                raise Unprocessable(
                    f"Block {b.get('id') or kind}: finding {fid!r} not found in this workspace",
                    code="invalid_block_reference",
                )
        aid = b.get("artifact_id")
        if aid is not None and resolve_artifact(db, workspace_id, str(aid)) is None:
            raise Unprocessable(
                f"Block {b.get('id') or kind}: artifact {aid!r} not found in this workspace",
                code="invalid_block_reference",
            )
        out.append(b if b.get("id") else {**b, "id": block(kind)["id"]})
    return out


def unreviewed_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    """Blocks that still need the analyst's review: not excluded and not marked reviewed (spec §64)."""
    return [str(b.get("id")) for b in blocks or [] if not b.get("excluded") and not b.get("reviewed")]


def _audit(db: DbDep, ctx: EditorCtx, action: str, rid: str, detail: dict[str, Any] | None = None) -> None:
    audit(
        db,
        action=action,
        resource_type="report",
        resource_id=rid,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail=detail or {},
    )


@router.get("", response_model=list[ReportSummary], operation_id="listReports")
def list_reports(
    ctx: ViewerCtx, db: DbDep, status: str | None = None, kind: str | None = None
) -> list[ReportSummary]:
    stmt = select(Report).where(Report.workspace_id == ctx.workspace_id)
    if status:
        stmt = stmt.where(Report.status == status)
    if kind:
        stmt = stmt.where(Report.kind == kind)
    return [ReportSummary.model_validate(r) for r in db.scalars(stmt.order_by(Report.updated_at.desc()))]


@router.post("", response_model=ReportOut, status_code=201, operation_id="createReport")
def create(body: ReportCreate, ctx: EditorCtx, db: DbDep) -> ReportOut:
    if body.investigation_id:
        get_investigation(db, ctx.workspace_id, body.investigation_id)
    r = Report(
        workspace_id=ctx.workspace_id,
        title=body.title,
        kind=body.kind,
        blocks=_normalise_blocks(db, ctx.workspace_id, body.blocks),
        investigation_id=body.investigation_id,
        created_by=ctx.user_id,
    )
    db.add(r)
    db.flush()
    _audit(db, ctx, "report.create", r.id, {"title": r.title})
    return ReportOut.model_validate(r)


@router.post(
    "/executive-summary", response_model=ReportOut, status_code=201, operation_id="createExecutiveSummary"
)
def exec_summary(body: ExecSummaryRequest, ctx: EditorCtx, db: DbDep, state: StateDep) -> ReportOut:
    """Summary built only from confirmed findings, separating observed facts, supported explanations and
    unresolved hypotheses. No explanation is added that a confirmed finding does not contain."""
    r = executive_summary_report(
        db,
        ctx.workspace,
        finding_ids=body.finding_ids,
        investigation_id=body.investigation_id,
        title=body.title,
        user_id=ctx.user_id,
        state=state,
    )
    _audit(db, ctx, "report.executive_summary", r.id)
    return ReportOut.model_validate(r)


@router.post(
    "/business-review", response_model=ReportOut, status_code=201, operation_id="createBusinessReview"
)
def create_business_review(
    body: BusinessReviewRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> ReportOut:
    """Draft monthly business review: KPI overview, largest changes, positive/negative drivers, anomalies,
    segment movements, confirmed findings and unresolved questions. The analyst reviews it before publishing."""
    r = business_review(db, state, ctx.workspace, body.period, body.title, ctx.user_id)
    _audit(db, ctx, "report.business_review", r.id, {"period": body.period})
    return ReportOut.model_validate(r)


@router.post(
    "/from-investigation", response_model=ReportOut, status_code=201, operation_id="reportFromInvestigation"
)
def from_investigation(
    body: FromInvestigationRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> ReportOut:
    inv = get_investigation(db, ctx.workspace_id, body.investigation_id)
    r = report_from_investigation(db, state, ctx.workspace, inv, ctx.user_id)
    _audit(db, ctx, "report.from_investigation", r.id, {"investigation_id": inv.id})
    return ReportOut.model_validate(r)


@router.get("/{report_id}", response_model=ReportOut, operation_id="getReport")
def get(report_id: str, ctx: ViewerCtx, db: DbDep) -> ReportOut:
    return ReportOut.model_validate(get_report(db, ctx.workspace_id, report_id))


@router.patch("/{report_id}", response_model=ReportOut, operation_id="updateReport")
def update(report_id: str, body: ReportUpdate, ctx: EditorCtx, db: DbDep) -> ReportOut:
    r = get_report(db, ctx.workspace_id, report_id)
    if r.status == "published":
        raise Conflict("Published reports are read-only; unpublish to edit", code="report_published")
    if body.title is not None:
        r.title = body.title
    if body.blocks is not None:
        r.blocks = _normalise_blocks(db, ctx.workspace_id, body.blocks)
    r.version_no += 1
    db.flush()
    return ReportOut.model_validate(r)


@router.delete("/{report_id}", response_model=OkResponse, operation_id="deleteReport")
def delete(report_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    r = get_report(db, ctx.workspace_id, report_id)
    _audit(db, ctx, "report.delete", r.id, {"title": r.title})
    db.delete(r)
    return OkResponse()


def _transition(report_id: str, ctx: EditorCtx, db: DbDep, allowed: set[str], to: str) -> ReportOut:
    r = get_report(db, ctx.workspace_id, report_id)
    if r.status not in allowed:
        raise Conflict(f"Cannot move a {r.status} report to {to}", code="invalid_transition")
    old = r.status
    r.status = to
    r.published_at = utcnow() if to == "published" else (None if to == "draft" else r.published_at)
    _audit(db, ctx, f"report.{to}", r.id, {"from": old})
    db.flush()
    return ReportOut.model_validate(r)


@router.post("/{report_id}/submit", response_model=ReportOut, operation_id="submitReport")
def submit(report_id: str, ctx: EditorCtx, db: DbDep) -> ReportOut:
    return _transition(report_id, ctx, db, {"draft"}, "in_review")


@router.post("/{report_id}/publish", response_model=ReportOut, operation_id="publishReport")
def publish(report_id: str, ctx: EditorCtx, db: DbDep) -> ReportOut:
    """Publish a report that is in review and whose every block (except excluded ones) is marked
    ``reviewed: true``. Otherwise ``409 not_in_review`` or ``409 unreviewed_blocks`` with
    ``errors.block_ids`` (spec §64: the analyst reviews before publication)."""
    r = get_report(db, ctx.workspace_id, report_id)
    if r.status != "in_review":
        raise Conflict(
            f"Only a report in review can be published (this one is {r.status}); submit it first",
            code="not_in_review",
        )
    pending = unreviewed_blocks(r.blocks)
    if pending:
        raise Conflict(
            f"{len(pending)} block(s) are not reviewed; review or exclude them before publishing",
            code="unreviewed_blocks",
            extra={"block_ids": pending},
        )
    return _transition(report_id, ctx, db, {"in_review"}, "published")


@router.post("/{report_id}/unpublish", response_model=ReportOut, operation_id="unpublishReport")
def unpublish(report_id: str, ctx: EditorCtx, db: DbDep) -> ReportOut:
    return _transition(report_id, ctx, db, {"published", "in_review"}, "draft")
