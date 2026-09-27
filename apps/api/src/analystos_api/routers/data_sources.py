"""External data sources (database connectors) with encrypted credentials."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..db import utcnow
from ..deps import DbDep, EditorCtx, OwnerCtx, PrincipalDep, StateDep, ViewerCtx
from ..errors import NotFound
from ..models import DataSource
from ..schemas.common import ERROR_RESPONSES, JobAccepted, OkResponse, TabularResult
from ..schemas.data_sources import (
    ConnectionTestOut,
    ConnectorKindInfo,
    DataSourceCreate,
    DataSourceOut,
    DataSourceUpdate,
    RemoteColumn,
    RemoteTable,
    SchemaOut,
    SnapshotTableRequest,
)
from ..services import connectors as svc
from ..services.datasets import snapshot_remote_table_job
from ..services.jobs_view import job_accepted
from ..services.observability import audit, record_event

router = APIRouter(
    prefix="/workspaces/{workspace_id}/data-sources", tags=["data-sources"], responses=ERROR_RESPONSES
)


def _get(db: DbDep, workspace_id: str, source_id: str) -> DataSource:
    ds = db.get(DataSource, source_id)
    if ds is None or ds.workspace_id != workspace_id:
        raise NotFound("Data source not found")
    return ds


def _out(ds: DataSource) -> DataSourceOut:
    return DataSourceOut.model_validate(ds)


EXTERNAL_KINDS = {"postgres", "mysql", "sqlserver", "snowflake", "bigquery"}

kinds_router = APIRouter(prefix="/data-sources", tags=["data-sources"], responses=ERROR_RESPONSES)


@kinds_router.get("/kinds", response_model=list[ConnectorKindInfo], operation_id="listConnectorKinds")
def list_kinds(principal: PrincipalDep) -> list[ConnectorKindInfo]:
    """Supported connector kinds with the JSON schema of their connection form."""
    from analystos_engine.connectors.base import available_kinds, connector_config_schema

    out = []
    for kind in available_kinds():
        if kind not in EXTERNAL_KINDS:
            continue
        out.append(
            ConnectorKindInfo(
                kind=kind,
                label=svc.KIND_LABELS.get(kind, kind),
                config_schema=connector_config_schema(kind),
                secret_fields=svc.secret_field_names(kind),
            )
        )
    return out


@router.get("", response_model=list[DataSourceOut], operation_id="listDataSources")
def list_sources(ctx: ViewerCtx, db: DbDep) -> list[DataSourceOut]:
    rows = db.scalars(
        select(DataSource).where(DataSource.workspace_id == ctx.workspace_id).order_by(DataSource.created_at)
    ).all()
    return [_out(r) for r in rows]


def _clean_secrets(secrets: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in secrets.items() if v not in (None, "")}


@router.post("", response_model=DataSourceOut, status_code=201, operation_id="createDataSource")
def create_source(body: DataSourceCreate, ctx: OwnerCtx, db: DbDep, state: StateDep) -> DataSourceOut:
    secrets = _clean_secrets(body.secrets)
    svc.check_no_secrets_in_config(body.kind, body.config)
    ds = DataSource(
        workspace_id=ctx.workspace_id,
        name=body.name,
        kind=body.kind,
        description=body.description,
        config=body.config,
        secrets_encrypted=state.secret_box.encrypt(secrets) if secrets else None,
        secret_fields=sorted(secrets),
        created_by=ctx.user_id,
    )
    db.add(ds)
    db.flush()
    audit(
        db,
        action="data_source.create",
        resource_type="data_source",
        resource_id=ds.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={
            "name": body.name,
            "kind": body.kind,
            "config": body.config,
            "secret_fields": sorted(secrets),
        },
    )
    return _out(ds)


@router.post("/test", response_model=ConnectionTestOut, operation_id="testDataSourceSettings")
def test_unsaved(body: DataSourceCreate, ctx: OwnerCtx, state: StateDep) -> ConnectionTestOut:
    """Test connection settings before saving them. Nothing is persisted."""
    svc.check_no_secrets_in_config(body.kind, body.config)
    start = time.perf_counter()
    result = svc.test_connection(state.secret_box, body.kind, body.config, _clean_secrets(body.secrets))
    record_event(
        state.session_factory,
        category="connector",
        name=f"test:{body.kind}",
        status="ok" if result.get("ok") else "error",
        duration_ms=(time.perf_counter() - start) * 1000,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        error=None if result.get("ok") else result.get("message"),
    )
    return ConnectionTestOut.model_validate(result)


@router.get("/{source_id}", response_model=DataSourceOut, operation_id="getDataSource")
def get_source(source_id: str, ctx: ViewerCtx, db: DbDep) -> DataSourceOut:
    return _out(_get(db, ctx.workspace_id, source_id))


@router.patch("/{source_id}", response_model=DataSourceOut, operation_id="updateDataSource")
def update_source(
    source_id: str, body: DataSourceUpdate, ctx: OwnerCtx, db: DbDep, state: StateDep
) -> DataSourceOut:
    ds = _get(db, ctx.workspace_id, source_id)
    if body.name is not None:
        ds.name = body.name
    if body.description is not None:
        ds.description = body.description
    if body.config is not None:
        svc.check_no_secrets_in_config(ds.kind, body.config)
        ds.config = body.config
    changed_secrets: list[str] = []
    if body.secrets is not None:
        current = svc.decrypt_secrets(state.secret_box, ds)
        for key, value in body.secrets.items():
            changed_secrets.append(key)
            if value is None or value == "":
                current.pop(key, None)
            else:
                current[key] = value
        ds.secrets_encrypted = state.secret_box.encrypt(current) if current else None
        ds.secret_fields = sorted(current)
    if body.config is not None or body.secrets is not None:
        ds.status = "untested"
    audit(
        db,
        action="data_source.update",
        resource_type="data_source",
        resource_id=ds.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"name": body.name, "config": body.config, "secrets_changed": changed_secrets},
    )
    db.flush()
    return _out(ds)


@router.delete("/{source_id}", response_model=OkResponse, operation_id="deleteDataSource")
def delete_source(source_id: str, ctx: OwnerCtx, db: DbDep) -> OkResponse:
    ds = _get(db, ctx.workspace_id, source_id)
    db.delete(ds)
    audit(
        db,
        action="data_source.delete",
        resource_type="data_source",
        resource_id=source_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"name": ds.name},
    )
    return OkResponse()


@router.post("/{source_id}/test", response_model=ConnectionTestOut, operation_id="testDataSource")
def test_source(source_id: str, ctx: EditorCtx, db: DbDep, state: StateDep) -> ConnectionTestOut:
    ds = _get(db, ctx.workspace_id, source_id)
    start = time.perf_counter()
    result = svc.test_connection(
        state.secret_box, ds.kind, dict(ds.config or {}), svc.decrypt_secrets(state.secret_box, ds)
    )
    ds.status = "connected" if result.get("ok") else "error"
    ds.last_tested_at = utcnow()
    ds.last_test_message = str(result.get("message", ""))[:2000]
    record_event(
        state.session_factory,
        category="connector",
        name=f"test:{ds.kind}",
        status="ok" if result.get("ok") else "error",
        duration_ms=(time.perf_counter() - start) * 1000,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        detail={"data_source_id": ds.id},
        error=None if result.get("ok") else ds.last_test_message,
    )
    return ConnectionTestOut.model_validate(result)


@router.get("/{source_id}/schemas", response_model=list[SchemaOut], operation_id="listRemoteSchemas")
def list_schemas(source_id: str, ctx: ViewerCtx, db: DbDep, state: StateDep) -> list[SchemaOut]:
    ds = _get(db, ctx.workspace_id, source_id)
    with svc.connector_for(state.secret_box, ds) as conn:
        return [
            SchemaOut(name=s.name, table_count=getattr(s, "table_count", None))
            for s in conn.discover_schemas()
        ]


@router.get("/{source_id}/tables", response_model=list[RemoteTable], operation_id="listRemoteTables")
def list_tables(
    source_id: str, ctx: ViewerCtx, db: DbDep, state: StateDep, schema: Annotated[str, Query(alias="schema")]
) -> list[RemoteTable]:
    ds = _get(db, ctx.workspace_id, source_id)
    with svc.connector_for(state.secret_box, ds) as conn:
        tables = conn.discover_tables(schema)
    return [
        RemoteTable(
            name=t.name,
            schema_name=t.schema_name,
            kind=t.kind,
            row_count=t.row_count,
            columns=[RemoteColumn(name=c.name, type=c.type, nullable=c.nullable) for c in t.columns],
        )
        for t in tables
    ]


@router.get(
    "/{source_id}/tables/{schema_name}/{table}/columns",
    response_model=list[RemoteColumn],
    operation_id="listRemoteColumns",
)
def list_columns(
    source_id: str, schema_name: str, table: str, ctx: ViewerCtx, db: DbDep, state: StateDep
) -> list[RemoteColumn]:
    ds = _get(db, ctx.workspace_id, source_id)
    with svc.connector_for(state.secret_box, ds) as conn:
        cols = conn.inspect_columns(schema_name, table)
    return [RemoteColumn(name=c.name, type=c.type, nullable=c.nullable) for c in cols]


@router.get(
    "/{source_id}/tables/{schema_name}/{table}/preview",
    response_model=TabularResult,
    operation_id="previewRemoteTable",
)
def preview_table(
    source_id: str,
    schema_name: str,
    table: str,
    ctx: ViewerCtx,
    db: DbDep,
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> TabularResult:
    ds = _get(db, ctx.workspace_id, source_id)
    start = time.perf_counter()
    with svc.connector_for(state.secret_box, ds) as conn:
        res = conn.preview(schema_name, table, limit)
    record_event(
        state.session_factory,
        category="sql",
        name=f"preview:{ds.kind}",
        duration_ms=(time.perf_counter() - start) * 1000,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        detail={"data_source_id": ds.id, "table": f"{schema_name}.{table}"},
    )
    return TabularResult.model_validate(res.model_dump(mode="json"))


@router.post(
    "/{source_id}/import", response_model=JobAccepted, status_code=202, operation_id="importRemoteTable"
)
def snapshot_table(
    source_id: str, body: SnapshotTableRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> JobAccepted:
    """Copy a remote table (read-only SELECT) into the workspace DuckDB store as a dataset."""
    ds = _get(db, ctx.workspace_id, source_id)
    job_id = state.jobs.submit(
        kind="snapshot_remote_table",
        fn=snapshot_remote_table_job(state, ctx.workspace_id, ds.id, body.model_dump(), ctx.user_id),
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        params=body.model_dump(),
        resource_type="data_source",
        resource_id=ds.id,
        db=db,
    )
    return job_accepted(state, ctx.workspace_id, job_id)
