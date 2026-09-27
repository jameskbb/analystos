"""Findings: evidence-backed statements with a review workflow, comments, versions and lineage."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from ..deps import DbDep, EditorCtx, ViewerCtx
from ..errors import Forbidden, NotFound, Unprocessable
from ..models import ArtifactRecord, Finding, FindingComment, FindingVersion, User
from ..schemas.common import ERROR_RESPONSES, OkResponse
from ..schemas.datasets import LineageGraph
from ..schemas.findings import (
    CommentIn,
    CommentOut,
    FindingCreate,
    FindingOut,
    FindingUpdate,
    FindingVersionOut,
    StatusChange,
)
from ..schemas.investigations import ArtifactOut
from ..services.findings import bump_version, comment_counts, get_finding, snapshot_finding
from ..services.investigations import get_investigation
from ..services.lineage import GraphBuilder, artifact_lineage
from ..services.observability import audit

router = APIRouter(prefix="/workspaces/{workspace_id}/findings", tags=["findings"], responses=ERROR_RESPONSES)


def finding_out(db: DbDep, f: Finding, counts: dict[str, int] | None = None) -> FindingOut:
    out = FindingOut.model_validate(f)
    out.comment_count = (counts if counts is not None else comment_counts(db, [f.id])).get(f.id, 0)
    return out


def _audit(db: DbDep, ctx: EditorCtx, action: str, fid: str, detail: dict[str, object] | None = None) -> None:
    audit(
        db,
        action=action,
        resource_type="finding",
        resource_id=fid,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail=detail or {},
    )


@router.get("", response_model=list[FindingOut], operation_id="listFindings")
def list_findings(
    ctx: ViewerCtx,
    db: DbDep,
    status: str | None = None,
    statement_type: str | None = None,
    investigation_id: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[FindingOut]:
    stmt = select(Finding).where(Finding.workspace_id == ctx.workspace_id)
    if status:
        stmt = stmt.where(Finding.status == status)
    if statement_type:
        stmt = stmt.where(Finding.statement_type == statement_type)
    if investigation_id:
        stmt = stmt.where(Finding.investigation_id == investigation_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Finding.statement.ilike(like), Finding.notes.ilike(like)))
    rows = db.scalars(stmt.order_by(Finding.updated_at.desc()).limit(limit)).all()
    counts = comment_counts(db, [f.id for f in rows])
    return [finding_out(db, f, counts) for f in rows]


@router.post("", response_model=FindingOut, status_code=201, operation_id="createFinding")
def create(body: FindingCreate, ctx: EditorCtx, db: DbDep) -> FindingOut:
    """Record an analyst-authored finding. Without supporting artifacts it is a hypothesis: numbers
    stated as fact must come from an executed artifact (spec section 87)."""
    if body.investigation_id:
        get_investigation(db, ctx.workspace_id, body.investigation_id)
    arts = db.scalars(
        select(ArtifactRecord).where(
            ArtifactRecord.id.in_(body.artifact_ids), ArtifactRecord.workspace_id == ctx.workspace_id
        )
    ).all()
    if len(arts) != len(set(body.artifact_ids)):
        raise Unprocessable("Unknown artifact ids", code="unknown_artifact")
    if not arts:
        statement_type, strength = "hypothesis", "hypothesis_only"
        reasons = ["Analyst-authored statement with no supporting computation attached"]
    else:
        statement_type = body.statement_type
        strength = body.evidence_strength or "weak"
        reasons = body.evidence_reasons or [
            f"Analyst-authored statement backed by {len(arts)} attached artifact(s)"
        ]
    f = Finding(
        workspace_id=ctx.workspace_id,
        investigation_id=body.investigation_id,
        statement=body.statement,
        statement_type=statement_type,
        evidence_strength=strength,
        evidence_reasons=reasons,
        status="draft",
        notes=body.notes,
        business_impact=body.business_impact,
        tags=body.tags,
        artifact_ids=[a.id for a in arts],
        filter_context=next((a.filter_context for a in arts if a.filter_context), []),
        metric_version_ids={
            k: {"engine_version_id": v} for a in arts for k, v in (a.metric_versions or {}).items()
        },
        created_by=ctx.user_id,
    )
    db.add(f)
    db.flush()
    snapshot_finding(db, f, ctx.user_id, "Created")
    _audit(db, ctx, "finding.create", f.id, {"statement_type": f.statement_type})
    return finding_out(db, f)


@router.get("/{finding_id}", response_model=FindingOut, operation_id="getFinding")
def get(finding_id: str, ctx: ViewerCtx, db: DbDep) -> FindingOut:
    return finding_out(db, get_finding(db, ctx.workspace_id, finding_id))


@router.patch("/{finding_id}", response_model=FindingOut, operation_id="updateFinding")
def update(finding_id: str, body: FindingUpdate, ctx: EditorCtx, db: DbDep) -> FindingOut:
    f = get_finding(db, ctx.workspace_id, finding_id)
    changed = body.model_dump(exclude_none=True, exclude={"change_note"})
    for key, value in changed.items():
        setattr(f, key, value)
    if changed:
        bump_version(db, f, ctx.user_id, body.change_note or f"Edited {', '.join(sorted(changed))}")
        _audit(db, ctx, "finding.update", f.id, {"fields": sorted(changed)})
    db.flush()
    return finding_out(db, f)


@router.delete("/{finding_id}", response_model=OkResponse, operation_id="deleteFinding")
def delete(finding_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    f = get_finding(db, ctx.workspace_id, finding_id)
    _audit(db, ctx, "finding.delete", f.id, {"statement": f.statement[:200]})
    db.delete(f)
    return OkResponse()


@router.post("/{finding_id}/status", response_model=FindingOut, operation_id="setFindingStatus")
def set_status(finding_id: str, body: StatusChange, ctx: EditorCtx, db: DbDep) -> FindingOut:
    """Review workflow: draft -> needs_review -> confirmed | rejected (any transition allowed, all audited)."""
    f = get_finding(db, ctx.workspace_id, finding_id)
    if f.status == body.status:
        return finding_out(db, f)
    old = f.status
    f.status = body.status
    if body.note:
        f.notes = (f.notes + "\n" if f.notes else "") + f"[{body.status}] {body.note}"
    bump_version(
        db, f, ctx.user_id, f"Status {old} -> {body.status}" + (f": {body.note}" if body.note else "")
    )
    _audit(db, ctx, "finding.status", f.id, {"from": old, "to": body.status, "note": body.note})
    db.flush()
    return finding_out(db, f)


@router.get("/{finding_id}/comments", response_model=list[CommentOut], operation_id="listFindingComments")
def list_comments(finding_id: str, ctx: ViewerCtx, db: DbDep) -> list[CommentOut]:
    f = get_finding(db, ctx.workspace_id, finding_id)
    rows = db.execute(
        select(FindingComment, User.name)
        .outerjoin(User, User.id == FindingComment.user_id)
        .where(FindingComment.finding_id == f.id)
        .order_by(FindingComment.created_at)
    ).all()
    out = []
    for c, name in rows:
        item = CommentOut.model_validate(c)
        item.user_name = name
        out.append(item)
    return out


@router.post(
    "/{finding_id}/comments", response_model=CommentOut, status_code=201, operation_id="addFindingComment"
)
def add_comment(finding_id: str, body: CommentIn, ctx: ViewerCtx, db: DbDep) -> CommentOut:
    """Any member (including viewers) can comment; comments are part of the review trail."""
    f = get_finding(db, ctx.workspace_id, finding_id)
    if ctx.principal.read_only:
        raise Forbidden("This API token is read-only", code="token_read_only")
    c = FindingComment(finding_id=f.id, user_id=ctx.user_id, body=body.body)
    db.add(c)
    db.flush()
    audit(
        db,
        action="finding.comment",
        resource_type="finding",
        resource_id=f.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
    )
    out = CommentOut.model_validate(c)
    out.user_name = ctx.user.name
    return out


@router.delete(
    "/{finding_id}/comments/{comment_id}", response_model=OkResponse, operation_id="deleteFindingComment"
)
def delete_comment(finding_id: str, comment_id: str, ctx: ViewerCtx, db: DbDep) -> OkResponse:
    f = get_finding(db, ctx.workspace_id, finding_id)
    c = db.get(FindingComment, comment_id)
    if c is None or c.finding_id != f.id:
        raise NotFound("Comment not found")
    if c.user_id != ctx.user_id and not ctx.can("owner"):
        raise Forbidden("Only the author or a workspace owner can delete a comment")
    db.delete(c)
    return OkResponse()


@router.get(
    "/{finding_id}/versions", response_model=list[FindingVersionOut], operation_id="listFindingVersions"
)
def versions(finding_id: str, ctx: ViewerCtx, db: DbDep) -> list[FindingVersionOut]:
    f = get_finding(db, ctx.workspace_id, finding_id)
    rows = db.scalars(
        select(FindingVersion)
        .where(FindingVersion.finding_id == f.id)
        .order_by(FindingVersion.version_no.desc())
    ).all()
    return [FindingVersionOut.model_validate(r) for r in rows]


@router.get("/{finding_id}/artifacts", response_model=list[ArtifactOut], operation_id="listFindingArtifacts")
def finding_artifacts(finding_id: str, ctx: ViewerCtx, db: DbDep) -> list[ArtifactOut]:
    from .artifacts import artifact_out

    f = get_finding(db, ctx.workspace_id, finding_id)
    rows = {a.id: a for a in db.scalars(select(ArtifactRecord).where(ArtifactRecord.id.in_(f.artifact_ids)))}
    return [artifact_out(rows[i]) for i in f.artifact_ids if i in rows]


@router.get(
    "/{finding_id}/lineage",
    response_model=LineageGraph,
    operation_id="getFindingLineage",
    tags=["findings", "lineage"],
)
def lineage(finding_id: str, ctx: ViewerCtx, db: DbDep) -> LineageGraph:
    """Finding -> chart -> query -> metric (version) -> entity -> dataset (version) -> source."""
    f = get_finding(db, ctx.workspace_id, finding_id)
    g = GraphBuilder()
    fid = g.node(
        f"finding:{f.id}",
        "finding",
        f.statement[:120],
        detail=f.statement_type,
        ref_id=f.id,
        meta={"status": f.status, "evidence_strength": f.evidence_strength},
    )
    if f.investigation_id:
        inv_id = g.node(
            f"investigation:{f.investigation_id}", "investigation", "Investigation", ref_id=f.investigation_id
        )
        g.edge(fid, inv_id, "part_of")
    rows = list(db.scalars(select(ArtifactRecord).where(ArtifactRecord.id.in_(f.artifact_ids))))
    artifact_lineage(db, ctx.workspace, rows, g, attach_to=fid)
    return LineageGraph.model_validate(g.graph())
