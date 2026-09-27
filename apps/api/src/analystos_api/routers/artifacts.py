"""Artifacts: every analytical test with its SQL/Python, parameters, filters, versions, result and chart."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import READ_ONLY_POST, DbDep, StateDep, ViewerCtx
from ..errors import NotFound, Unprocessable
from ..models import ArtifactRecord
from ..schemas.common import ERROR_RESPONSES, TabularResult
from ..schemas.datasets import LineageGraph
from ..schemas.investigations import ArtifactOut, ArtifactRerun
from ..services.lineage import artifact_lineage
from ..services.observability import record_event

router = APIRouter(
    prefix="/workspaces/{workspace_id}/artifacts", tags=["artifacts"], responses=ERROR_RESPONSES
)


def artifact_out(a: ArtifactRecord) -> ArtifactOut:
    doc = a.document or {}
    return ArtifactOut(
        id=a.id,
        engine_id=a.engine_artifact_id,
        kind=a.kind,
        title=a.title,
        investigation_id=a.investigation_id,
        run_id=a.run_id,
        sql=a.sql,
        python=a.python,
        params=a.params or {},
        filters=a.filters or [],
        filter_context=a.filter_context or [],
        metric_versions=a.metric_versions or {},
        dataset_versions=a.dataset_versions or [],
        result=a.result,
        data=doc.get("data") or None,
        chart_spec=a.chart_spec,
        validation=a.validation,
        warnings=list(doc.get("warnings", [])),
        error=doc.get("error"),
        parent_ids=a.parent_ids or [],
        origin=a.origin,
        created_at=a.created_at,
    )


def resolve_artifact(db: Session, ws: str, artifact_id: str) -> ArtifactRecord | None:
    """An artifact by database id, or by the engine id used in ``TreeNode.artifact_ids`` / ``parent_ids``
    (``art_...``; the latest run holding that id in this workspace wins)."""
    a = db.get(ArtifactRecord, artifact_id)
    if a is not None and a.workspace_id == ws:
        return a
    return db.scalar(
        select(ArtifactRecord)
        .where(ArtifactRecord.workspace_id == ws, ArtifactRecord.engine_artifact_id == artifact_id)
        .order_by(ArtifactRecord.created_at.desc(), ArtifactRecord.id.desc())
        .limit(1)
    )


def _get(db: DbDep, ws: str, artifact_id: str) -> ArtifactRecord:
    a = resolve_artifact(db, ws, artifact_id)
    if a is None:
        raise NotFound("Artifact not found")
    return a


@router.get("", response_model=list[ArtifactOut], operation_id="listArtifacts")
def list_artifacts(
    ctx: ViewerCtx,
    db: DbDep,
    investigation_id: str | None = None,
    kind: str | None = None,
    origin: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[ArtifactOut]:
    stmt = select(ArtifactRecord).where(ArtifactRecord.workspace_id == ctx.workspace_id)
    if origin:
        stmt = stmt.where(ArtifactRecord.origin == origin)
    if investigation_id:
        stmt = stmt.where(ArtifactRecord.investigation_id == investigation_id)
    if kind:
        stmt = stmt.where(ArtifactRecord.kind == kind)
    return [artifact_out(a) for a in db.scalars(stmt.order_by(ArtifactRecord.created_at.desc()).limit(limit))]


@router.get("/{artifact_id}", response_model=ArtifactOut, operation_id="getArtifact")
def get_artifact(artifact_id: str, ctx: ViewerCtx, db: DbDep) -> ArtifactOut:
    return artifact_out(_get(db, ctx.workspace_id, artifact_id))


@router.get(
    "/{artifact_id}/lineage",
    response_model=LineageGraph,
    operation_id="getArtifactLineage",
    tags=["artifacts", "lineage"],
)
def lineage(artifact_id: str, ctx: ViewerCtx, db: DbDep) -> LineageGraph:
    a = _get(db, ctx.workspace_id, artifact_id)
    return LineageGraph.model_validate(artifact_lineage(db, ctx.workspace, [a]).graph())


@router.post(
    "/{artifact_id}/rerun",
    response_model=ArtifactRerun,
    operation_id="rerunArtifact",
    openapi_extra=READ_ONLY_POST,
)
def rerun(artifact_id: str, ctx: ViewerCtx, db: DbDep, state: StateDep) -> ArtifactRerun:
    """Re-execute the artifact's stored SQL on the current data and compare with the stored snapshot."""
    from ..services.investigations import _rows_equal

    a = _get(db, ctx.workspace_id, artifact_id)
    if not a.sql:
        raise Unprocessable("This artifact has no SQL to re-execute", code="no_sql")
    prev = a.result or {}
    prev_rows = prev.get("rows", [])
    res = state.stores.get(ctx.workspace_id).execute_read(
        a.sql, None, max(len(prev_rows), 1) + 1000, state.settings.query_timeout_s
    )
    rows = res.model_dump(mode="json")["rows"]
    identical = _rows_equal(rows[: len(prev_rows)], prev_rows) and (
        prev.get("truncated") or len(rows) == prev.get("row_count", len(prev_rows))
    )
    changes = []
    if not identical:
        changes.append(f"row count {prev.get('row_count', len(prev_rows))} -> {res.row_count}")
        changes.append(
            "values differ from the stored snapshot" if len(rows) >= len(prev_rows) else "rows missing"
        )
    record_event(
        state.session_factory,
        category="sql",
        name="artifact_rerun",
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        detail={"artifact_id": a.id, "identical": bool(identical)},
    )
    return ArtifactRerun(
        artifact_id=a.id,
        identical=bool(identical),
        previous_row_count=int(prev.get("row_count", 0)),
        result=TabularResult.model_validate(res.model_dump(mode="json")),
        changes=changes,
    )
