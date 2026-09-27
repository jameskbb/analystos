"""Datasets: upload, inspect, preview, ingest, profile, versions, lineage and the schema browser."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any

from analystos_engine.types import TableProfile
from fastapi import APIRouter, File, Query, UploadFile
from sqlalchemy import select

from ..deps import DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import NotFound, Unprocessable
from ..models import (
    Dataset,
    DatasetVersion,
    MetricRecord,
    MetricVersion,
    QualityRule,
    RelationshipRecord,
    SavedQuery,
    Upload,
)
from ..schemas.common import ERROR_RESPONSES, JobAccepted, OkResponse, TabularResult
from ..schemas.datasets import (
    DatasetOut,
    DatasetUpdate,
    DatasetUsage,
    DatasetVersionOut,
    FilePreviewOut,
    FilePreviewRequest,
    IngestRequest,
    LineageEdge,
    LineageGraph,
    LineageNode,
    SchemaColumn,
    SchemaTable,
    UploadOut,
    WorkspaceSchema,
    edge,
)
from ..services import engine_bridge as eng
from ..services.datasets import (
    drop_dataset,
    ensure_table_name_free,
    get_dataset,
    ingest_upload_job,
    profile_job,
    table_name_for,
)
from ..services.jobs_view import job_accepted
from ..services.observability import audit, timed_event
from ..services.semantic import build_model
from ..services.uploads import store_upload

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["datasets"], responses=ERROR_RESPONSES)


def _dataset_out(ds: Dataset) -> DatasetOut:
    out = DatasetOut.model_validate(ds)
    issues = (ds.profile or {}).get("issues") if isinstance(ds.profile, dict) else None
    out.issue_count = len(issues) if isinstance(issues, list) else 0
    return out


def _get_upload(db: DbDep, workspace_id: str, upload_id: str) -> Upload:
    up = db.get(Upload, upload_id)
    if up is None or up.workspace_id != workspace_id:
        raise NotFound("Upload not found")
    return up


# ------------------------------------------------------------------------ uploads


@router.post("/datasets/uploads", response_model=UploadOut, status_code=201, operation_id="uploadFile")
def upload_file(ctx: EditorCtx, db: DbDep, state: StateDep, file: Annotated[UploadFile, File()]) -> UploadOut:
    """Upload a CSV/TSV, Excel (.xlsx), Parquet or JSON/NDJSON file and inspect it. Nothing is ingested yet."""
    up = Upload(
        workspace_id=ctx.workspace_id,
        filename=file.filename or "upload",
        stored_path="",
        size_bytes=0,
        sha256="",
        file_kind="csv",
        created_by=ctx.user_id,
    )
    db.add(up)
    db.flush()
    target_dir = state.stores.uploads_dir(ctx.workspace_id) / up.id
    try:
        stored = store_upload(
            file.file, file.filename or "upload", target_dir, state.settings.max_upload_bytes
        )
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    up.stored_path = str(stored.path)
    up.size_bytes = stored.size_bytes
    up.sha256 = stored.sha256
    up.file_kind = stored.file_kind
    try:
        with timed_event(
            state.session_factory,
            category="import",
            name=f"inspect:{stored.safe_name}",
            workspace_id=ctx.workspace_id,
            user_id=ctx.user_id,
            detail={"upload_id": up.id, "bytes": stored.size_bytes, "kind": stored.file_kind},
        ):
            up.inspection = eng.inspect_file(stored.path, max_bytes=state.settings.max_upload_bytes)
    except Exception as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise Unprocessable(f"Could not read the file: {exc}", code="inspection_failed") from exc
    up.status = "inspected"
    audit(
        db,
        action="upload.create",
        resource_type="upload",
        resource_id=up.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"filename": up.filename, "bytes": up.size_bytes, "sha256": up.sha256},
    )
    db.flush()
    return UploadOut.model_validate(up)


@router.get("/datasets/uploads", response_model=list[UploadOut], operation_id="listUploads")
def list_uploads(ctx: ViewerCtx, db: DbDep) -> list[UploadOut]:
    rows = db.scalars(
        select(Upload).where(Upload.workspace_id == ctx.workspace_id).order_by(Upload.created_at.desc())
    ).all()
    return [UploadOut.model_validate(r) for r in rows]


@router.get("/datasets/uploads/{upload_id}", response_model=UploadOut, operation_id="getUpload")
def get_upload(upload_id: str, ctx: ViewerCtx, db: DbDep) -> UploadOut:
    return UploadOut.model_validate(_get_upload(db, ctx.workspace_id, upload_id))


@router.post(
    "/datasets/uploads/{upload_id}/inspect", response_model=FilePreviewOut, operation_id="inspectUpload"
)
def preview_upload(
    upload_id: str, body: FilePreviewRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> FilePreviewOut:
    """Preview how the file parses with explicit options (sheet, header row, delimiter...) before ingesting. The
    options are applied exactly as ingest applies them, so the preview equals what will be loaded."""
    up = _get_upload(db, ctx.workspace_id, upload_id)
    try:
        preview = eng.preview_file(
            Path(up.stored_path),
            body.options.model_dump(exclude_defaults=True),
            body.limit,
            max_bytes=state.settings.max_upload_bytes,
        )
    except Exception as exc:
        raise Unprocessable(f"Could not preview with these options: {exc}", code="preview_failed") from exc
    return FilePreviewOut.model_validate({"upload_id": up.id, "options": body.options, "preview": preview})


@router.delete("/datasets/uploads/{upload_id}", response_model=OkResponse, operation_id="deleteUpload")
def delete_upload(upload_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    up = _get_upload(db, ctx.workspace_id, upload_id)
    shutil.rmtree(Path(up.stored_path).parent, ignore_errors=True)
    db.delete(up)
    return OkResponse()


@router.post(
    "/datasets/uploads/{upload_id}/ingest",
    response_model=JobAccepted,
    status_code=202,
    operation_id="ingestUpload",
)
def ingest_upload(
    upload_id: str, body: IngestRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> JobAccepted:
    """Ingest the file into the workspace store, then profile it and refresh relationship suggestions."""
    up = _get_upload(db, ctx.workspace_id, upload_id)
    table = body.table_name or table_name_for(body.dataset_name or up.filename)
    ensure_table_name_free(db, ctx.workspace_id, table, body.if_exists)
    request = body.model_dump(mode="json")
    request["options"] = body.options.model_dump(mode="json", exclude_defaults=True)
    request["table_name"] = table
    job_id = state.jobs.submit(
        kind="ingest_upload",
        fn=ingest_upload_job(state, ctx.workspace_id, up.id, request, ctx.user_id),
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        params=request,
        resource_type="upload",
        resource_id=up.id,
        db=db,
    )
    audit(
        db,
        action="dataset.ingest",
        resource_type="upload",
        resource_id=up.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"table": table, "job_id": job_id},
    )
    return job_accepted(state, ctx.workspace_id, job_id)


# ------------------------------------------------------------------------ datasets


@router.get("/datasets", response_model=list[DatasetOut], operation_id="listDatasets")
def list_datasets(ctx: ViewerCtx, db: DbDep) -> list[DatasetOut]:
    rows = db.scalars(
        select(Dataset).where(Dataset.workspace_id == ctx.workspace_id).order_by(Dataset.name)
    ).all()
    return [_dataset_out(r) for r in rows]


@router.get("/datasets/{dataset_id}", response_model=DatasetOut, operation_id="getDataset")
def get_dataset_route(dataset_id: str, ctx: ViewerCtx, db: DbDep) -> DatasetOut:
    return _dataset_out(get_dataset(db, ctx.workspace_id, dataset_id))


@router.patch("/datasets/{dataset_id}", response_model=DatasetOut, operation_id="updateDataset")
def update_dataset(dataset_id: str, body: DatasetUpdate, ctx: EditorCtx, db: DbDep) -> DatasetOut:
    ds = get_dataset(db, ctx.workspace_id, dataset_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(ds, field, value)
    audit(
        db,
        action="dataset.update",
        resource_type="dataset",
        resource_id=ds.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail=body.model_dump(exclude_none=True),
    )
    db.flush()
    return _dataset_out(ds)


@router.delete("/datasets/{dataset_id}", response_model=OkResponse, operation_id="deleteDataset")
def delete_dataset(dataset_id: str, ctx: EditorCtx, db: DbDep, state: StateDep) -> OkResponse:
    ds = get_dataset(db, ctx.workspace_id, dataset_id)
    audit(
        db,
        action="dataset.delete",
        resource_type="dataset",
        resource_id=ds.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"table": ds.table_name},
    )
    drop_dataset(db, state, ds)
    return OkResponse()


@router.get("/datasets/{dataset_id}/preview", response_model=TabularResult, operation_id="previewDataset")
def preview_dataset(
    dataset_id: str,
    ctx: ViewerCtx,
    db: DbDep,
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=5000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    order_by: Annotated[str | None, Query(description="Column to sort by")] = None,
    direction: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
) -> TabularResult:
    from analystos_engine.store import quote_ident

    ds = get_dataset(db, ctx.workspace_id, dataset_id)
    columns = {c["name"] for c in ds.columns}
    sql = f"SELECT * FROM {quote_ident(ds.table_name)}"
    if order_by:
        if order_by not in columns:
            raise Unprocessable(f"Unknown column {order_by!r}", code="unknown_column")
        sql += f" ORDER BY {quote_ident(order_by)} {'DESC' if direction == 'desc' else 'ASC'} NULLS LAST"
    sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    res = state.stores.get(ctx.workspace_id).execute_read(sql, None, limit, state.settings.query_timeout_s)
    return TabularResult.model_validate(res.model_dump(mode="json"))


@router.get("/datasets/{dataset_id}/profile", response_model=TableProfile, operation_id="getDatasetProfile")
def get_profile(dataset_id: str, ctx: ViewerCtx, db: DbDep) -> Any:
    """Per-column statistics, semantic role guesses and data issues with evidence."""
    ds = get_dataset(db, ctx.workspace_id, dataset_id)
    if ds.profile_status == "failed":
        raise Unprocessable(
            f"Profiling failed: {(ds.profile or {}).get('error', 'unknown error')}", code="profile_failed"
        )
    if ds.profile is None or ds.profile_status != "ready":
        raise NotFound(f"Dataset has no profile yet (status: {ds.profile_status})", code="profile_pending")
    return ds.profile


@router.post(
    "/datasets/{dataset_id}/profile",
    response_model=JobAccepted,
    status_code=202,
    operation_id="profileDataset",
)
def reprofile(dataset_id: str, ctx: EditorCtx, db: DbDep, state: StateDep) -> JobAccepted:
    ds = get_dataset(db, ctx.workspace_id, dataset_id)
    job_id = state.jobs.submit(
        kind="profile_dataset",
        fn=profile_job(state, ctx.workspace_id, ds.id, ctx.user_id),
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        resource_type="dataset",
        resource_id=ds.id,
        db=db,
    )
    return job_accepted(state, ctx.workspace_id, job_id)


@router.get(
    "/datasets/{dataset_id}/versions",
    response_model=list[DatasetVersionOut],
    operation_id="listDatasetVersions",
)
def list_versions(dataset_id: str, ctx: ViewerCtx, db: DbDep) -> list[DatasetVersionOut]:
    ds = get_dataset(db, ctx.workspace_id, dataset_id)
    rows = db.scalars(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == ds.id)
        .order_by(DatasetVersion.version_no.desc())
    ).all()
    return [DatasetVersionOut.model_validate(r) for r in rows]


@router.get("/datasets/{dataset_id}/usage", response_model=DatasetUsage, operation_id="getDatasetUsage")
def dataset_usage(dataset_id: str, ctx: ViewerCtx, db: DbDep) -> DatasetUsage:
    """Where the dataset is used (semantic objects, rules, relationships, saved queries) plus a lineage graph."""
    ds = get_dataset(db, ctx.workspace_id, dataset_id)
    model = build_model(db, ctx.workspace).model
    entities = [e.name for e in model.entities if e.table == ds.table_name]
    dims = [d.name for d in model.dimensions if d.entity in entities]
    metrics = [
        m.id
        for m in model.metrics
        if (m.kind == "simple" and m.entity in entities)
        or (
            m.kind != "simple"
            and any(
                model.get_metric(dep).entity in entities
                for dep in model.metric_dependencies(m.id)
                if model.get_metric(dep).kind == "simple"
            )
        )
    ]
    rules = [
        r.name
        for r in db.scalars(
            select(QualityRule).where(
                QualityRule.workspace_id == ctx.workspace_id, QualityRule.table_name == ds.table_name
            )
        )
    ]
    rels = [
        f"{r.from_table}.{r.from_col} -> {r.to_table}.{r.to_col} ({r.status})"
        for r in db.scalars(
            select(RelationshipRecord).where(
                RelationshipRecord.workspace_id == ctx.workspace_id,
                (RelationshipRecord.from_table == ds.table_name)
                | (RelationshipRecord.to_table == ds.table_name),
            )
        )
    ]
    saved = [
        q.name
        for q in db.scalars(select(SavedQuery).where(SavedQuery.workspace_id == ctx.workspace_id))
        if ds.table_name.lower() in q.sql.lower()
    ]
    nodes = [LineageNode(id=f"dataset:{ds.id}", kind="dataset", label=ds.name, meta={"table": ds.table_name})]
    edges: list[LineageEdge] = []
    if ds.source_kind == "upload":
        src = f"upload:{ds.source_ref.get('upload_id')}"
        nodes.append(
            LineageNode(id=src, kind="source_file", label=str(ds.source_ref.get("filename", "file")))
        )
        edges.append(edge(from_=src, to=f"dataset:{ds.id}", label="ingested_into"))
    elif ds.data_source_id:
        src = f"data_source:{ds.data_source_id}"
        nodes.append(
            LineageNode(
                id=src,
                kind="data_source",
                label=f"{ds.source_ref.get('schema')}.{ds.source_ref.get('table')}",
            )
        )
        edges.append(edge(from_=src, to=f"dataset:{ds.id}", label="snapshot_of"))
    for e in entities:
        nodes.append(LineageNode(id=f"entity:{e}", kind="entity", label=e))
        edges.append(edge(from_=f"dataset:{ds.id}", to=f"entity:{e}", label="backs"))
    for m in metrics:
        rec = db.scalar(
            select(MetricRecord).where(MetricRecord.workspace_id == ctx.workspace_id, MetricRecord.name == m)
        )
        ver = db.get(MetricVersion, rec.current_version_id) if rec and rec.current_version_id else None
        nodes.append(
            LineageNode(
                id=f"metric:{m}", kind="metric", label=m, meta={"version_no": ver.version_no if ver else None}
            )
        )
        metric = model.get_metric(m)
        if metric.kind == "simple" and metric.entity:
            edges.append(edge(from_=f"entity:{metric.entity}", to=f"metric:{m}", label="aggregated_by"))
        else:
            for dep in model.metric_dependencies(m)[1:]:
                edges.append(edge(from_=f"metric:{dep}", to=f"metric:{m}", label="input_to"))
    return DatasetUsage(
        entities=entities,
        dimensions=dims,
        metrics=metrics,
        quality_rules=rules,
        relationships=rels,
        saved_queries=saved,
        lineage=LineageGraph(nodes=nodes, edges=edges),
    )


@router.get(
    "/queries/schema", response_model=WorkspaceSchema, operation_id="getWorkspaceSchema", tags=["queries"]
)
def workspace_schema(ctx: ViewerCtx, db: DbDep, state: StateDep) -> WorkspaceSchema:
    """All tables and columns in the workspace store (for the SQL editor's schema browser and autocomplete)."""
    datasets = {
        d.table_name: d for d in db.scalars(select(Dataset).where(Dataset.workspace_id == ctx.workspace_id))
    }
    tables: list[SchemaTable] = []
    for t in state.stores.get(ctx.workspace_id).list_tables(include_row_counts=True):
        ds = datasets.get(t.name)
        tables.append(
            SchemaTable(
                table=t.name,
                dataset_id=ds.id if ds else None,
                dataset_name=ds.name if ds else None,
                row_count=t.row_count,
                columns=[SchemaColumn(name=c.name, type=c.type) for c in t.columns],
            )
        )
    model = build_model(db, ctx.workspace).model
    return WorkspaceSchema(
        tables=sorted(tables, key=lambda x: x.table),
        metrics=[m.id for m in model.metrics],
        dimensions=[d.name for d in model.dimensions],
    )


@router.get(
    "/datasets/{dataset_id}/lineage",
    response_model=LineageGraph,
    operation_id="getDatasetLineage",
    tags=["datasets", "lineage"],
)
def dataset_lineage(dataset_id: str, ctx: ViewerCtx, db: DbDep) -> LineageGraph:
    """Source -> dataset -> entities -> metrics."""
    return dataset_usage(dataset_id, ctx, db).lineage


@router.get("/datasets/{dataset_id}/metrics", response_model=list[str], operation_id="getDatasetMetrics")
def dataset_metrics(dataset_id: str, ctx: ViewerCtx, db: DbDep) -> list[str]:
    """Ids of metrics computed from this dataset (directly or through ratio/derived metrics)."""
    return dataset_usage(dataset_id, ctx, db).metrics
