"""Relationship discovery review: suggestions with confidence and signals, approve/reject, join analysis."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..db import utcnow
from ..deps import DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import NotFound, Unprocessable
from ..jobs import JobContext
from ..models import RelationshipRecord
from ..schemas.common import ERROR_RESPONSES, JobAccepted, OkResponse
from ..schemas.relationships import RelationshipCreate, RelationshipDecision, RelationshipOut
from ..services.datasets import dataset_by_table, measure_relationship, refresh_relationship_suggestions
from ..services.jobs_view import job_accepted
from ..services.observability import audit
from ..services.semantic import snapshot, upsert_relationship

router = APIRouter(
    prefix="/workspaces/{workspace_id}/relationships", tags=["relationships"], responses=ERROR_RESPONSES
)


def _get(db: DbDep, workspace_id: str, rel_id: str) -> RelationshipRecord:
    rel = db.get(RelationshipRecord, rel_id)
    if rel is None or rel.workspace_id != workspace_id:
        raise NotFound("Relationship not found")
    return rel


def _analyze(state: StateDep, workspace_id: str, rel: RelationshipRecord) -> None:
    measure_relationship(state, workspace_id, rel)


@router.get("", response_model=list[RelationshipOut], operation_id="listRelationships")
def list_relationships(
    ctx: ViewerCtx,
    db: DbDep,
    status: Annotated[str | None, Query(pattern="^(suggested|approved|rejected)$")] = None,
    table: str | None = None,
) -> list[RelationshipOut]:
    stmt = select(RelationshipRecord).where(RelationshipRecord.workspace_id == ctx.workspace_id)
    if status:
        stmt = stmt.where(RelationshipRecord.status == status)
    if table:
        stmt = stmt.where((RelationshipRecord.from_table == table) | (RelationshipRecord.to_table == table))
    order = {"high": 0, "medium": 1, "low": 2}
    rows = sorted(
        db.scalars(stmt).all(),
        key=lambda r: (r.status != "suggested", order.get(r.confidence, 3), r.from_table, r.to_table),
    )
    return [RelationshipOut.model_validate(r) for r in rows]


@router.post("", response_model=RelationshipOut, status_code=201, operation_id="createRelationship")
def create_relationship(
    body: RelationshipCreate, ctx: EditorCtx, db: DbDep, state: StateDep
) -> RelationshipOut:
    for table, col in ((body.from_table, body.from_col), (body.to_table, body.to_col)):
        ds = dataset_by_table(db, ctx.workspace_id, table)
        if ds is None:
            raise Unprocessable(f"Unknown table {table!r}", code="unknown_table")
        if col not in {c["name"] for c in ds.columns}:
            raise Unprocessable(f"Unknown column {table}.{col}", code="unknown_column")
    rel = upsert_relationship(
        db,
        ctx.workspace_id,
        from_table=body.from_table,
        from_col=body.from_col,
        to_table=body.to_table,
        to_col=body.to_col,
        cardinality=body.cardinality,
        status="approved" if body.approve else "suggested",
        origin="manual",
        user_id=ctx.user_id,
        signals=["defined manually"],
    )
    _analyze(state, ctx.workspace_id, rel)
    audit(
        db,
        action="relationship.create",
        resource_type="relationship",
        resource_id=rel.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail=body.model_dump(),
    )
    if body.approve:
        snapshot(db, ctx.workspace, reason="relationship approved", user_id=ctx.user_id)
    return RelationshipOut.model_validate(rel)


@router.post("/discover", response_model=JobAccepted, status_code=202, operation_id="discoverRelationships")
def discover(ctx: EditorCtx, db: DbDep, state: StateDep) -> JobAccepted:
    """Re-run relationship discovery across all datasets. Existing decisions are preserved."""
    ws_id, user_id = ctx.workspace_id, ctx.user_id

    def run(_job: JobContext) -> dict[str, int]:
        return {"new_suggestions": refresh_relationship_suggestions(state, ws_id, user_id)}

    job_id = state.jobs.submit(
        kind="discover_relationships", fn=run, workspace_id=ws_id, user_id=user_id, db=db
    )
    return job_accepted(state, ws_id, job_id)


def _decide(
    rel_id: str, body: RelationshipDecision, ctx: EditorCtx, db: DbDep, state: StateDep, status: str
) -> RelationshipOut:
    rel = _get(db, ctx.workspace_id, rel_id)
    if body.cardinality:
        rel.cardinality = body.cardinality
    rel.status = status
    rel.decided_by = ctx.user_id
    rel.decided_at = utcnow()
    if status == "approved":
        _analyze(state, ctx.workspace_id, rel)
    audit(
        db,
        action=f"relationship.{'approve' if status == 'approved' else 'reject'}",
        resource_type="relationship",
        resource_id=rel.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={
            "relationship": f"{rel.from_table}.{rel.from_col}->{rel.to_table}.{rel.to_col}",
            "cardinality": rel.cardinality,
            "note": body.note,
        },
    )
    db.flush()
    snapshot(db, ctx.workspace, reason=f"relationship {status}", user_id=ctx.user_id)
    return RelationshipOut.model_validate(rel)


@router.post("/{relationship_id}/approve", response_model=RelationshipOut, operation_id="approveRelationship")
def approve(
    relationship_id: str, body: RelationshipDecision, ctx: EditorCtx, db: DbDep, state: StateDep
) -> RelationshipOut:
    """Approve a relationship so the semantic compiler may join on it. Join behaviour is measured on the data."""
    return _decide(relationship_id, body, ctx, db, state, "approved")


@router.post("/{relationship_id}/reject", response_model=RelationshipOut, operation_id="rejectRelationship")
def reject(
    relationship_id: str, body: RelationshipDecision, ctx: EditorCtx, db: DbDep, state: StateDep
) -> RelationshipOut:
    return _decide(relationship_id, body, ctx, db, state, "rejected")


@router.patch("/{relationship_id}", response_model=RelationshipOut, operation_id="updateRelationship")
def update_relationship(
    relationship_id: str, body: RelationshipDecision, ctx: EditorCtx, db: DbDep, state: StateDep
) -> RelationshipOut:
    """Change the cardinality of a relationship without changing its review status."""
    rel = _get(db, ctx.workspace_id, relationship_id)
    if body.cardinality:
        rel.cardinality = body.cardinality
        _analyze(state, ctx.workspace_id, rel)
    audit(
        db,
        action="relationship.update",
        resource_type="relationship",
        resource_id=rel.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"cardinality": rel.cardinality, "note": body.note},
    )
    db.flush()
    if rel.status == "approved":
        snapshot(db, ctx.workspace, reason="relationship updated", user_id=ctx.user_id)
    return RelationshipOut.model_validate(rel)


@router.post("/{relationship_id}/analyze", response_model=RelationshipOut, operation_id="analyzeRelationship")
def analyze(relationship_id: str, ctx: ViewerCtx, db: DbDep, state: StateDep) -> RelationshipOut:
    """Measure observed cardinality, fan-out and orphan rate for this join on the current data."""
    rel = _get(db, ctx.workspace_id, relationship_id)
    _analyze(state, ctx.workspace_id, rel)
    out = RelationshipOut.model_validate(rel)
    if not ctx.can("editor"):
        db.rollback()  # viewers may measure a join but not persist the result
    return out


@router.delete("/{relationship_id}", response_model=OkResponse, operation_id="deleteRelationship")
def delete_relationship(relationship_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    rel = _get(db, ctx.workspace_id, relationship_id)
    db.delete(rel)
    audit(
        db,
        action="relationship.delete",
        resource_type="relationship",
        resource_id=relationship_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
    )
    db.flush()
    snapshot(db, ctx.workspace, reason="relationship deleted", user_id=ctx.user_id)
    return OkResponse()
