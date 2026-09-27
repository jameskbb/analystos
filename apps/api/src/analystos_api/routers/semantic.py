"""Semantic layer: model, entities, dimensions, versioned metrics, metric trees, glossary and calendar."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from analystos_engine.semantic.models import SemanticModelError, formula_metric_refs
from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from sqlalchemy import select

from ..db import utcnow
from ..deps import DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import Conflict, NotFound, Unprocessable
from ..models import (
    DimensionRecord,
    Entity,
    GlossaryTermRecord,
    Investigation,
    MetricRecord,
    MetricTreeRecord,
    MetricVersion,
    SemanticSnapshot,
)
from ..schemas.common import ERROR_RESPONSES, OkResponse, TabularResult
from ..schemas.datasets import LineageEdge, LineageGraph, LineageNode, edge
from ..schemas.semantic import (
    CalendarConfig,
    Dimension,
    DimensionOut,
    DimensionValues,
    EdgeDecision,
    EntityOut,
    GlossaryOut,
    GlossaryTerm,
    Metric,
    MetricCreate,
    MetricDiff,
    MetricExamples,
    MetricInvestigationRef,
    MetricOut,
    MetricTree,
    MetricTreeOut,
    MetricUpdate,
    MetricValuePoint,
    MetricVersionOut,
    ModelIssue,
    PeriodResolution,
    SemanticImport,
    SemanticImportResult,
    SemanticModel,
    SemanticModelOut,
    SnapshotDetail,
    SnapshotOut,
    TreeSuggestion,
    TreeSuggestRequest,
)
from ..schemas.semantic import Entity as EntitySpec
from ..services.datasets import dataset_by_table
from ..services.metric_trees import suggest_tree
from ..services.observability import audit
from ..services.semantic import (
    add_join_warnings,
    build_model,
    check_metric_executes,
    get_metric_record,
    import_model,
    metric_execution_issues,
    snapshot,
    upsert_dimension,
    upsert_entity,
    upsert_glossary,
    upsert_metric,
    upsert_tree,
    validate_metric_against_model,
)
from ..services.workspaces import merged_settings

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["semantic"], responses=ERROR_RESPONSES)


def _snap(db: DbDep, ctx: EditorCtx, reason: str) -> SemanticSnapshot:
    return snapshot(db, ctx.workspace, reason=reason, user_id=ctx.user_id)


def _audit(
    db: DbDep, ctx: EditorCtx, action: str, rtype: str, rid: str | None, detail: dict[str, Any]
) -> None:
    audit(
        db,
        action=action,
        resource_type=rtype,
        resource_id=rid,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail=detail,
    )


# ----------------------------------------------------------------------------- model


@router.get("/semantic-models/current", response_model=SemanticModelOut, operation_id="getSemanticModel")
def get_model(ctx: ViewerCtx, db: DbDep) -> SemanticModelOut:
    built = build_model(db, ctx.workspace)
    snap = snapshot(db, ctx.workspace, reason="read", user_id=ctx.user_id, built=built)
    return SemanticModelOut(
        model=built.model,
        content_hash=built.model.content_hash(),
        issues=built.model.validate_model(),
        snapshot_id=snap.id,
        snapshot_version=snap.version_no,
        metric_versions=built.version_map(),
    )


@router.get(
    "/semantic-models/current/yaml",
    response_class=PlainTextResponse,
    operation_id="exportSemanticModelYaml",
    responses={200: {"content": {"application/yaml": {}}}},
)
def export_yaml(ctx: ViewerCtx, db: DbDep) -> PlainTextResponse:
    model = build_model(db, ctx.workspace).model
    return PlainTextResponse(
        model.to_yaml(),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="semantic_model.yaml"'},
    )


@router.put(
    "/semantic-models/current/yaml", response_model=SemanticImportResult, operation_id="importSemanticModel"
)
def import_yaml(body: SemanticImport, ctx: EditorCtx, db: DbDep) -> SemanticImportResult:
    """Merge a YAML semantic model into the workspace. Changed metric definitions become new versions."""
    try:
        model = SemanticModel.from_yaml(body.yaml)
    except (SemanticModelError, ValidationError, ValueError) as exc:
        raise Unprocessable(f"Invalid semantic model: {exc}", code="invalid_semantic_model") from exc
    missing = [e.table for e in model.entities if dataset_by_table(db, ctx.workspace_id, e.table) is None]
    if missing:
        raise Unprocessable(
            f"Entities reference tables that do not exist: {sorted(set(missing))}", code="unknown_table"
        )
    try:
        counts = import_model(db, ctx.workspace, model, user_id=ctx.user_id, change_note=body.change_note)
    except SemanticModelError as exc:
        raise Unprocessable(str(exc), code="invalid_semantic_model") from exc
    built = build_model(db, ctx.workspace)
    snap = snapshot(db, ctx.workspace, reason=body.change_note, user_id=ctx.user_id, built=built)
    _audit(db, ctx, "semantic_model.import", "semantic_model", snap.id, counts)
    return SemanticImportResult(counts=counts, issues=built.model.validate_model(), snapshot_id=snap.id)


@router.post(
    "/semantic-models/current/validate", response_model=list[ModelIssue], operation_id="validateSemanticModel"
)
def validate_model(ctx: ViewerCtx, db: DbDep) -> list[ModelIssue]:
    return build_model(db, ctx.workspace).model.validate_model()


@router.get(
    "/semantic-models/history", response_model=list[SnapshotOut], operation_id="listSemanticSnapshots"
)
def list_snapshots(
    ctx: ViewerCtx, db: DbDep, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[SnapshotOut]:
    rows = db.scalars(
        select(SemanticSnapshot)
        .where(SemanticSnapshot.workspace_id == ctx.workspace_id)
        .order_by(SemanticSnapshot.version_no.desc())
        .limit(limit)
    ).all()
    return [SnapshotOut.model_validate(r) for r in rows]


@router.get(
    "/semantic-models/history/{snapshot_id}",
    response_model=SnapshotDetail,
    operation_id="getSemanticSnapshot",
)
def get_snapshot(snapshot_id: str, ctx: ViewerCtx, db: DbDep) -> SnapshotDetail:
    snap = db.get(SemanticSnapshot, snapshot_id)
    if snap is None or snap.workspace_id != ctx.workspace_id:
        raise NotFound("Snapshot not found")
    return SnapshotDetail.model_validate(snap)


# ----------------------------------------------------------------------------- entities


@router.get("/semantic-models/entities", response_model=list[EntityOut], operation_id="listEntities")
def list_entities(ctx: ViewerCtx, db: DbDep) -> list[EntityOut]:
    rows = db.scalars(
        select(Entity).where(Entity.workspace_id == ctx.workspace_id).order_by(Entity.name)
    ).all()
    return [EntityOut(id=r.id, spec=EntitySpec.model_validate(r.spec), updated_at=r.updated_at) for r in rows]


@router.put("/semantic-models/entities/{name}", response_model=EntityOut, operation_id="upsertEntity")
def put_entity(name: str, spec: EntitySpec, ctx: EditorCtx, db: DbDep) -> EntityOut:
    if spec.name != name:
        raise Unprocessable("Entity name in the path and body must match", code="name_mismatch")
    ds = dataset_by_table(db, ctx.workspace_id, spec.table)
    if ds is None:
        raise Unprocessable(f"Unknown table {spec.table!r}", code="unknown_table")
    cols = {c["name"] for c in ds.columns}
    for key in spec.key_columns:
        if key not in cols:
            raise Unprocessable(f"Primary key column {key!r} not in {spec.table}", code="unknown_column")
    rec = upsert_entity(db, ctx.workspace_id, spec)
    _snap(db, ctx, f"entity {name} saved")
    _audit(db, ctx, "entity.upsert", "entity", rec.id, spec.model_dump(mode="json"))
    return EntityOut(id=rec.id, spec=spec, updated_at=rec.updated_at or utcnow())


@router.delete("/semantic-models/entities/{name}", response_model=OkResponse, operation_id="deleteEntity")
def delete_entity(name: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    rec = db.scalar(select(Entity).where(Entity.workspace_id == ctx.workspace_id, Entity.name == name))
    if rec is None:
        raise NotFound("Entity not found")
    model = build_model(db, ctx.workspace).model
    users = [d.name for d in model.dimensions if d.entity == name] + [
        m.id for m in model.metrics if m.entity == name
    ]
    if users:
        raise Conflict(f"Entity is used by {users}; remove those first", code="entity_in_use")
    db.delete(rec)
    db.flush()
    _snap(db, ctx, f"entity {name} deleted")
    _audit(db, ctx, "entity.delete", "entity", rec.id, {"name": name})
    return OkResponse()


# ----------------------------------------------------------------------------- dimensions


@router.get("/dimensions", response_model=list[DimensionOut], operation_id="listDimensions")
def list_dimensions(ctx: ViewerCtx, db: DbDep) -> list[DimensionOut]:
    rows = db.scalars(
        select(DimensionRecord)
        .where(DimensionRecord.workspace_id == ctx.workspace_id)
        .order_by(DimensionRecord.name)
    ).all()
    return [
        DimensionOut(id=r.id, spec=Dimension.model_validate(r.spec), updated_at=r.updated_at) for r in rows
    ]


@router.post("/dimensions", response_model=DimensionOut, status_code=201, operation_id="createDimension")
def create_dimension(spec: Dimension, ctx: EditorCtx, db: DbDep, state: StateDep) -> DimensionOut:
    exists = db.scalar(
        select(DimensionRecord.id).where(
            DimensionRecord.workspace_id == ctx.workspace_id, DimensionRecord.name == spec.name
        )
    )
    if exists:
        raise Conflict(f"Dimension {spec.name!r} already exists", code="dimension_exists")
    return put_dimension(spec.name, spec, ctx, db, state)


@router.get("/dimensions/{name}/values", response_model=DimensionValues, operation_id="getDimensionValues")
def dimension_values(
    name: str,
    ctx: ViewerCtx,
    db: DbDep,
    state: StateDep,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> DimensionValues:
    """Distinct values of a dimension (for filter pickers), most frequent first."""
    from analystos_engine.store import quote_ident

    model = build_model(db, ctx.workspace).model
    if not model.has_dimension(name):
        raise NotFound(f"Dimension {name!r} not found")
    dim = model.get_dimension(name)
    table = model.get_entity(dim.entity).table
    sql = (
        f"SELECT v AS value, count(*) AS n FROM (SELECT CAST(({dim.expr}) AS VARCHAR) AS v "
        f"FROM {quote_ident(table)}) t WHERE v IS NOT NULL"
    )
    params: dict[str, Any] = {}
    if q:
        sql += " AND lower(v) LIKE $q"
        params["q"] = f"%{q.lower()}%"
    sql += f" GROUP BY v ORDER BY n DESC, v LIMIT {int(limit)}"
    res = state.stores.get(ctx.workspace_id).execute_read(
        sql, params or None, limit, state.settings.query_timeout_s
    )
    return DimensionValues(
        dimension=name, values=[r[0] for r in res.rows], counts=[int(r[1]) for r in res.rows]
    )


@router.put("/dimensions/{name}", response_model=DimensionOut, operation_id="upsertDimension")
def put_dimension(name: str, spec: Dimension, ctx: EditorCtx, db: DbDep, state: StateDep) -> DimensionOut:
    if spec.name != name:
        raise Unprocessable("Dimension name in the path and body must match", code="name_mismatch")
    built = build_model(db, ctx.workspace)
    if not any(e.name == spec.entity for e in built.model.entities):
        raise Unprocessable(f"Unknown entity {spec.entity!r}", code="unknown_entity")
    entity = built.model.get_entity(spec.entity)
    from analystos_engine.store import quote_ident

    try:  # validate the expression against the real table (read-only)
        state.stores.get(ctx.workspace_id).execute_read(
            f"SELECT {spec.expr} AS v FROM {quote_ident(entity.table)} LIMIT 1", None, 1, 10
        )
    except Exception as exc:
        raise Unprocessable(
            f"Dimension expression failed on {entity.table}: {exc}", code="invalid_expression"
        ) from exc
    rec = upsert_dimension(db, ctx.workspace_id, spec)
    _snap(db, ctx, f"dimension {name} saved")
    _audit(db, ctx, "dimension.upsert", "dimension", rec.id, spec.model_dump(mode="json"))
    return DimensionOut(id=rec.id, spec=spec, updated_at=rec.updated_at or utcnow())


@router.delete("/dimensions/{name}", response_model=OkResponse, operation_id="deleteDimension")
def delete_dimension(name: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    rec = db.scalar(
        select(DimensionRecord).where(
            DimensionRecord.workspace_id == ctx.workspace_id, DimensionRecord.name == name
        )
    )
    if rec is None:
        raise NotFound("Dimension not found")
    model = build_model(db, ctx.workspace).model
    users = [
        m.id
        for m in model.metrics
        if any(f.dimension == name for f in m.filters) or m.default_time_dimension == name
    ]
    if users:
        raise Conflict(f"Dimension is used by metrics {users}", code="dimension_in_use")
    db.delete(rec)
    db.flush()
    _snap(db, ctx, f"dimension {name} deleted")
    _audit(db, ctx, "dimension.delete", "dimension", rec.id, {"name": name})
    return OkResponse()


# ----------------------------------------------------------------------------- metrics


def _metric_out(rec: MetricRecord, ver: MetricVersion) -> MetricOut:
    metric = Metric.model_validate({**ver.definition, "id": rec.name, "version": ver.version_no})
    return MetricOut(
        **metric.model_dump(),
        record_id=rec.id,
        version_no=ver.version_no,
        current_version_id=ver.id,
        engine_version_id=metric.version_id,
        archived=rec.archived,
        change_note=ver.change_note,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


def _current(db: DbDep, rec: MetricRecord) -> MetricVersion:
    ver = db.get(MetricVersion, rec.current_version_id) if rec.current_version_id else None
    if ver is None:
        raise NotFound("Metric has no versions")
    return ver


@router.get("/metrics", response_model=list[MetricOut], operation_id="listMetrics", tags=["metrics"])
def list_metrics(
    ctx: ViewerCtx, db: DbDep, include_archived: bool = False, tag: str | None = None
) -> list[MetricOut]:
    stmt = (
        select(MetricRecord).where(MetricRecord.workspace_id == ctx.workspace_id).order_by(MetricRecord.name)
    )
    if not include_archived:
        stmt = stmt.where(MetricRecord.archived.is_(False))
    out = [_metric_out(r, _current(db, r)) for r in db.scalars(stmt).all() if r.current_version_id]
    if tag:
        out = [m for m in out if tag in m.tags]
    return out


@router.post(
    "/metrics", response_model=MetricOut, status_code=201, operation_id="createMetric", tags=["metrics"]
)
def create_metric(body: MetricCreate, ctx: EditorCtx, db: DbDep, state: StateDep) -> MetricOut:
    """Create a metric. The definition is compiled and run with LIMIT 0 on the data first (422 invalid_metric)."""
    metric = body.to_metric()
    validate_metric_against_model(db, ctx.workspace, metric)
    check_metric_executes(state, db, ctx.workspace, metric)
    rec, ver, _ = upsert_metric(
        db, ctx.workspace, metric, user_id=ctx.user_id, change_note=body.change_note, create_only=True
    )
    _snap(db, ctx, f"metric {rec.name} created")
    _audit(db, ctx, "metric.create", "metric", rec.name, {"version_no": ver.version_no})
    return _metric_out(rec, ver)


@router.get("/metrics/{metric_id}", response_model=MetricOut, operation_id="getMetric", tags=["metrics"])
def get_metric(metric_id: str, ctx: ViewerCtx, db: DbDep) -> MetricOut:
    rec = get_metric_record(db, ctx.workspace_id, metric_id)
    return _metric_out(rec, _current(db, rec))


@router.put("/metrics/{metric_id}", response_model=MetricOut, operation_id="updateMetric", tags=["metrics"])
def update_metric(
    metric_id: str, body: MetricUpdate, ctx: EditorCtx, db: DbDep, state: StateDep
) -> MetricOut:
    """Save a new immutable version of the metric. Previous versions stay available for reproducibility.

    ``PATCH`` accepts the same body. Fields omitted from the body keep their current value."""
    rec = get_metric_record(db, ctx.workspace_id, metric_id)
    if body.id is not None and body.id != metric_id:
        raise Unprocessable("Metric ids cannot be renamed; create a new metric instead", code="id_mismatch")
    current = _current(db, rec).definition
    patch = body.model_dump(exclude_unset=True, exclude={"change_note", "id"})
    try:
        metric = Metric.model_validate({**current, **patch, "id": metric_id})
    except ValidationError as exc:
        raise Unprocessable(f"Invalid metric definition: {exc}", code="invalid_metric") from exc
    if metric.definition_hash() != Metric.model_validate({**current, "id": metric_id}).definition_hash():
        validate_metric_against_model(db, ctx.workspace, metric)
        check_metric_executes(state, db, ctx.workspace, metric)
    rec, ver, changed = upsert_metric(
        db, ctx.workspace, metric, user_id=ctx.user_id, change_note=body.change_note
    )
    if changed:
        _snap(db, ctx, f"metric {metric_id} v{ver.version_no}")
        _audit(
            db,
            ctx,
            "metric.version",
            "metric",
            metric_id,
            {"version_no": ver.version_no, "change_note": body.change_note},
        )
    return _metric_out(rec, ver)


router.add_api_route(
    "/metrics/{metric_id}",
    update_metric,
    methods=["PATCH"],
    response_model=MetricOut,
    operation_id="patchMetric",
    tags=["metrics"],
)


@router.post(
    "/metrics/validate", response_model=list[ModelIssue], operation_id="validateMetric", tags=["metrics"]
)
def validate_metric(body: MetricCreate, ctx: ViewerCtx, db: DbDep, state: StateDep) -> list[ModelIssue]:
    """Validate a metric definition against the current model and the data (compiled, run with LIMIT 0) without
    saving it."""
    built = build_model(db, ctx.workspace)
    metric = body.to_metric()
    others = [m for m in built.model.metrics if m.id != metric.id]
    candidate = built.model.model_copy(update={"metrics": [*others, metric]})
    issues = [
        i for i in candidate.validate_model() if i.object in {f"metric:{metric.id}"} or metric.id in i.message
    ]
    if not any(i.severity == "error" for i in issues):
        issues += metric_execution_issues(state, ctx.workspace, candidate, metric.id)
    return issues


@router.delete(
    "/metrics/{metric_id}", response_model=OkResponse, operation_id="archiveMetric", tags=["metrics"]
)
def archive_metric(metric_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    """Archive (soft-delete) a metric. Its versions remain so past analyses stay reproducible."""
    rec = get_metric_record(db, ctx.workspace_id, metric_id)
    model = build_model(db, ctx.workspace).model
    dependents = [
        m.id for m in model.metrics if m.id != metric_id and metric_id in model.metric_dependencies(m.id)
    ]
    if dependents:
        raise Conflict(f"Metrics {dependents} depend on {metric_id}", code="metric_in_use")
    rec.archived = True
    db.flush()
    _snap(db, ctx, f"metric {metric_id} archived")
    _audit(db, ctx, "metric.archive", "metric", metric_id, {})
    return OkResponse()


@router.get(
    "/metrics/{metric_id}/versions",
    response_model=list[MetricVersionOut],
    operation_id="listMetricVersions",
    tags=["metrics"],
)
def list_metric_versions(metric_id: str, ctx: ViewerCtx, db: DbDep) -> list[MetricVersionOut]:
    rec = get_metric_record(db, ctx.workspace_id, metric_id)
    rows = db.scalars(
        select(MetricVersion)
        .where(MetricVersion.metric_id == rec.id)
        .order_by(MetricVersion.version_no.desc())
    ).all()
    return [
        MetricVersionOut(
            version_id=v.id,
            version_no=v.version_no,
            definition=v.definition,
            definition_hash=v.definition_hash,
            change_note=v.change_note,
            created_by=v.created_by,
            created_at=v.created_at,
            is_current=v.id == rec.current_version_id,
        )
        for v in rows
    ]


@router.get(
    "/metrics/{metric_id}/diff",
    response_model=MetricDiff,
    operation_id="diffMetricVersions",
    tags=["metrics"],
)
def diff_versions(
    metric_id: str, ctx: ViewerCtx, db: DbDep, from_version: int, to_version: int
) -> MetricDiff:
    rec = get_metric_record(db, ctx.workspace_id, metric_id)
    versions = {
        v.version_no: v for v in db.scalars(select(MetricVersion).where(MetricVersion.metric_id == rec.id))
    }
    if from_version not in versions or to_version not in versions:
        raise NotFound("Version not found")
    a, b = versions[from_version].definition, versions[to_version].definition
    changes = [
        {"field": k, "from": a.get(k), "to": b.get(k)}
        for k in sorted(set(a) | set(b))
        if a.get(k) != b.get(k)
    ]
    return MetricDiff(metric_id=metric_id, from_version=from_version, to_version=to_version, changes=changes)


@router.post(
    "/metrics/{metric_id}/versions/{version_no}/restore",
    response_model=MetricOut,
    operation_id="restoreMetricVersion",
    tags=["metrics"],
)
def restore_version(metric_id: str, version_no: int, ctx: EditorCtx, db: DbDep) -> MetricOut:
    """Make an old definition current again by creating a new version with the same definition."""
    rec = get_metric_record(db, ctx.workspace_id, metric_id)
    old = db.scalar(
        select(MetricVersion).where(MetricVersion.metric_id == rec.id, MetricVersion.version_no == version_no)
    )
    if old is None:
        raise NotFound("Version not found")
    metric = Metric.model_validate({**old.definition, "id": metric_id})
    rec, ver, _ = upsert_metric(
        db, ctx.workspace, metric, user_id=ctx.user_id, change_note=f"Restored definition of v{version_no}"
    )
    _snap(db, ctx, f"metric {metric_id} restored v{version_no}")
    _audit(
        db, ctx, "metric.restore", "metric", metric_id, {"restored": version_no, "version_no": ver.version_no}
    )
    return _metric_out(rec, ver)


@router.get(
    "/metrics/{metric_id}/examples",
    response_model=MetricExamples,
    operation_id="getMetricExamples",
    tags=["metrics"],
)
def metric_examples(
    metric_id: str,
    ctx: ViewerCtx,
    db: DbDep,
    state: StateDep,
    grain: Annotated[str, Query(pattern="^(day|week|month|quarter|year)$")] = "month",
    dimension: str | None = None,
    periods: Annotated[int, Query(ge=1, le=120)] = 12,
) -> MetricExamples:
    """Example values: the metric over its default time dimension (most recent periods) or by a dimension."""
    from analystos_engine.semantic.compiler import CompileError, MetricQuery, compile
    from analystos_investigator.semantic_graph import time_dimension_for_metric

    built = build_model(db, ctx.workspace)
    model = built.model
    if not model.has_metric(metric_id):
        raise NotFound(f"Metric {metric_id!r} not found")
    time_dim = time_dimension_for_metric(model, metric_id)
    if dimension:
        dims = [dimension]
        order = [{"field": metric_id, "desc": True}]
    elif time_dim:
        dims = [f"{time_dim}__{grain}"]
        order = [{"field": dims[0], "desc": True}]
    else:
        dims, order = [], []
    try:
        compiled = compile(
            model,
            MetricQuery.model_validate(
                {"metrics": [metric_id], "dimensions": dims, "order_by": order, "limit": periods}
            ),
        )
    except CompileError as exc:
        raise Unprocessable(str(exc), code="compile_error") from exc
    add_join_warnings(state.stores.get(ctx.workspace_id), model, compiled)
    res = state.stores.get(ctx.workspace_id).execute_read(
        compiled.sql, None, periods, state.settings.query_timeout_s
    )
    names = [c.name for c in res.columns]
    rows = list(reversed(res.rows)) if not dimension else res.rows
    points = [
        MetricValuePoint(
            period=dict(zip(names, r, strict=False)).get(dims[0]) if dims else None,
            value=dict(zip(names, r, strict=False)).get(metric_id),
        )
        for r in rows
    ]
    return MetricExamples(
        metric_id=metric_id,
        version_no=built.metric_versions[metric_id].version_no,
        time_dimension=None if dimension else time_dim,
        dimension=dimension,
        grain=grain,
        points=points,
        compiled=compiled.model_dump(mode="json"),
        result=TabularResult.model_validate(res.model_dump(mode="json")),
    )


@router.get(
    "/metrics/{metric_id}/lineage",
    response_model=LineageGraph,
    operation_id="getMetricLineage",
    tags=["metrics", "lineage"],
)
def metric_lineage(metric_id: str, ctx: ViewerCtx, db: DbDep) -> LineageGraph:
    """Metric (version) -> input metrics -> entities -> datasets -> sources."""
    built = build_model(db, ctx.workspace)
    model = built.model
    if not model.has_metric(metric_id):
        raise NotFound(f"Metric {metric_id!r} not found")
    nodes: dict[str, LineageNode] = {}
    edges: list[LineageEdge] = []
    for dep in model.metric_dependencies(metric_id):
        m = model.get_metric(dep)
        ref = built.metric_versions.get(dep)
        nodes[f"metric:{dep}"] = LineageNode(
            id=f"metric:{dep}",
            kind="metric",
            label=m.display_name,
            meta={
                "metric_id": dep,
                "version_no": ref.version_no if ref else None,
                "version_id": ref.version_id if ref else None,
                "kind": m.kind,
                "expr": m.expr,
                "formula": m.formula,
            },
        )
        if m.kind == "ratio":
            for child in (m.numerator, m.denominator):
                edges.append(edge(from_=f"metric:{child}", to=f"metric:{dep}", label="input_to"))
        elif m.kind == "derived":
            for child in formula_metric_refs(m.formula or "", model):
                edges.append(edge(from_=f"metric:{child}", to=f"metric:{dep}", label="input_to"))
        elif m.entity:
            ent = model.get_entity(m.entity)
            nodes[f"entity:{ent.name}"] = LineageNode(
                id=f"entity:{ent.name}",
                kind="entity",
                label=ent.name,
                meta={"table": ent.table, "grain": ent.grain_description},
            )
            edges.append(edge(from_=f"entity:{ent.name}", to=f"metric:{dep}", label="aggregated_by"))
            ds = dataset_by_table(db, ctx.workspace_id, ent.table)
            if ds is not None:
                nodes[f"dataset:{ds.id}"] = LineageNode(
                    id=f"dataset:{ds.id}",
                    kind="dataset",
                    label=ds.name,
                    meta={"table": ds.table_name, "version_id": ds.current_version_id},
                )
                edges.append(edge(from_=f"dataset:{ds.id}", to=f"entity:{ent.name}", label="backs"))
                src = ds.source_ref or {}
                if src.get("filename"):
                    sid = f"source:{ds.id}"
                    nodes[sid] = LineageNode(id=sid, kind="source_file", label=str(src["filename"]))
                    edges.append(edge(from_=sid, to=f"dataset:{ds.id}", label="ingested_into"))
                elif src.get("table"):
                    sid = f"source:{ds.id}"
                    nodes[sid] = LineageNode(
                        id=sid, kind="source_table", label=f"{src.get('schema')}.{src.get('table')}"
                    )
                    edges.append(edge(from_=sid, to=f"dataset:{ds.id}", label="snapshot_of"))
    unique_edges = {(e.from_, e.to, e.label): e for e in edges}
    return LineageGraph(nodes=list(nodes.values()), edges=list(unique_edges.values()))


@router.get(
    "/metrics/{metric_id}/investigations",
    response_model=list[MetricInvestigationRef],
    operation_id="listMetricInvestigations",
    tags=["metrics"],
)
def metric_investigations(metric_id: str, ctx: ViewerCtx, db: DbDep) -> list[MetricInvestigationRef]:
    """Investigations that used this metric, with the metric version each one used."""
    rows = db.scalars(
        select(Investigation)
        .where(Investigation.workspace_id == ctx.workspace_id)
        .order_by(Investigation.created_at.desc())
    ).all()
    out = []
    for inv in rows:
        used = (inv.metric_version_ids or {}).get(metric_id)
        if used:
            out.append(
                MetricInvestigationRef(
                    id=inv.id,
                    title=inv.title,
                    status=inv.status,
                    created_at=inv.created_at,
                    metric_version=used,
                )
            )
    return out


# ----------------------------------------------------------------------------- metric trees


def _tree_out(rec: MetricTreeRecord) -> MetricTreeOut:
    return MetricTreeOut(
        id=rec.id,
        version_no=rec.version_no,
        spec=MetricTree.model_validate(rec.spec),
        updated_at=rec.updated_at,
    )


@router.get(
    "/metric-trees", response_model=list[MetricTreeOut], operation_id="listMetricTrees", tags=["metric-trees"]
)
def list_trees(ctx: ViewerCtx, db: DbDep) -> list[MetricTreeOut]:
    rows = db.scalars(
        select(MetricTreeRecord)
        .where(MetricTreeRecord.workspace_id == ctx.workspace_id)
        .order_by(MetricTreeRecord.root_metric)
    ).all()
    return [_tree_out(r) for r in rows]


@router.get(
    "/metric-trees/{root_metric}",
    response_model=MetricTreeOut,
    operation_id="getMetricTree",
    tags=["metric-trees"],
)
def get_tree(root_metric: str, ctx: ViewerCtx, db: DbDep) -> MetricTreeOut:
    rec = db.scalar(
        select(MetricTreeRecord).where(
            MetricTreeRecord.workspace_id == ctx.workspace_id, MetricTreeRecord.root_metric == root_metric
        )
    )
    if rec is None:
        raise NotFound("Metric tree not found")
    return _tree_out(rec)


def _validate_tree(db: DbDep, ctx: EditorCtx, tree: MetricTree) -> None:
    model = build_model(db, ctx.workspace).model
    unknown = sorted(
        {
            m
            for m in [tree.root_metric, *[x for e in tree.nodes for x in (e.parent, e.child)]]
            if not model.has_metric(m)
        }
    )
    if unknown:
        raise Unprocessable(f"Unknown metrics in tree: {unknown}", code="unknown_metric")


@router.put(
    "/metric-trees/{root_metric}",
    response_model=MetricTreeOut,
    operation_id="saveMetricTree",
    tags=["metric-trees"],
)
def put_tree(root_metric: str, spec: MetricTree, ctx: EditorCtx, db: DbDep) -> MetricTreeOut:
    """Save a metric tree. Edges with ``approved=false`` are shown as suggestions and not used by investigations."""
    if spec.root_metric != root_metric:
        raise Unprocessable("Root metric in the path and body must match", code="name_mismatch")
    _validate_tree(db, ctx, spec)
    rec = upsert_tree(db, ctx.workspace_id, spec, ctx.user_id)
    _snap(db, ctx, f"metric tree {root_metric} saved")
    _audit(
        db, ctx, "metric_tree.save", "metric_tree", rec.id, {"root": root_metric, "edges": len(spec.nodes)}
    )
    return _tree_out(rec)


@router.post(
    "/metric-trees/suggest",
    response_model=TreeSuggestion,
    operation_id="suggestMetricTree",
    tags=["metric-trees"],
)
def suggest(body: TreeSuggestRequest, ctx: ViewerCtx, db: DbDep) -> TreeSuggestion:
    root_metric = body.root_metric
    """Suggest driver edges from metric definitions. Suggestions are not canonical until approved."""
    model = build_model(db, ctx.workspace).model
    if not model.has_metric(root_metric):
        raise NotFound(f"Metric {root_metric!r} not found")
    edges, notes = suggest_tree(model, root_metric)
    return TreeSuggestion(root_metric=root_metric, edges=edges, rationale=notes)


@router.post(
    "/metric-trees/{root_metric}/edges",
    response_model=MetricTreeOut,
    operation_id="decideMetricTreeEdge",
    tags=["metric-trees"],
)
def decide_edge(root_metric: str, body: EdgeDecision, ctx: EditorCtx, db: DbDep) -> MetricTreeOut:
    """Approve (adds/marks the edge approved) or reject (removes it) a driver edge."""
    rec = db.scalar(
        select(MetricTreeRecord).where(
            MetricTreeRecord.workspace_id == ctx.workspace_id, MetricTreeRecord.root_metric == root_metric
        )
    )
    tree = MetricTree.model_validate(rec.spec) if rec else MetricTree(root_metric=root_metric)
    nodes = list(tree.nodes)
    match = [e for e in nodes if e.parent == body.parent and e.child == body.child]
    if body.decision == "reject":
        nodes = [e for e in nodes if not (e.parent == body.parent and e.child == body.child)]
    elif match:
        nodes = [
            e.model_copy(update={"approved": True, "suggested": False})
            if (e.parent == body.parent and e.child == body.child)
            else e
            for e in nodes
        ]
    else:
        model = build_model(db, ctx.workspace).model
        candidates, _ = suggest_tree(model, root_metric) if model.has_metric(root_metric) else ([], [])
        cand = [e for e in candidates if e.parent == body.parent and e.child == body.child]
        if not cand:
            raise Unprocessable(
                "Edge is not in the tree or its suggestions; save the tree with the edge instead",
                code="unknown_edge",
            )
        nodes.append(cand[0].model_copy(update={"approved": True, "suggested": False}))
    new_tree = tree.model_copy(update={"nodes": nodes})
    _validate_tree(db, ctx, new_tree)
    rec = upsert_tree(db, ctx.workspace_id, new_tree, ctx.user_id)
    _snap(db, ctx, f"metric tree {root_metric} edge {body.decision}")
    _audit(
        db,
        ctx,
        f"metric_tree.edge_{body.decision}",
        "metric_tree",
        rec.id,
        {"parent": body.parent, "child": body.child},
    )
    return _tree_out(rec)


@router.delete(
    "/metric-trees/{root_metric}",
    response_model=OkResponse,
    operation_id="deleteMetricTree",
    tags=["metric-trees"],
)
def delete_tree(root_metric: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    rec = db.scalar(
        select(MetricTreeRecord).where(
            MetricTreeRecord.workspace_id == ctx.workspace_id, MetricTreeRecord.root_metric == root_metric
        )
    )
    if rec is None:
        raise NotFound("Metric tree not found")
    db.delete(rec)
    db.flush()
    _snap(db, ctx, f"metric tree {root_metric} deleted")
    return OkResponse()


# ----------------------------------------------------------------------------- glossary


def _gloss_out(rec: GlossaryTermRecord) -> GlossaryOut:
    return GlossaryOut(id=rec.id, spec=GlossaryTerm.model_validate(rec.spec), updated_at=rec.updated_at)


@router.get("/glossary", response_model=list[GlossaryOut], operation_id="listGlossary", tags=["glossary"])
def list_glossary(ctx: ViewerCtx, db: DbDep) -> list[GlossaryOut]:
    rows = db.scalars(
        select(GlossaryTermRecord)
        .where(GlossaryTermRecord.workspace_id == ctx.workspace_id)
        .order_by(GlossaryTermRecord.term)
    ).all()
    return [_gloss_out(r) for r in rows]


def _check_glossary_refs(db: DbDep, ctx: EditorCtx, term: GlossaryTerm) -> None:
    model = build_model(db, ctx.workspace).model
    refs = [x for x in [term.metric_id, *term.candidate_metric_ids] if x]
    unknown = [r for r in refs if not model.has_metric(r)]
    if unknown:
        raise Unprocessable(f"Unknown metrics referenced: {unknown}", code="unknown_metric")


@router.post(
    "/glossary",
    response_model=GlossaryOut,
    status_code=201,
    operation_id="createGlossaryTerm",
    tags=["glossary"],
)
def create_term(term: GlossaryTerm, ctx: EditorCtx, db: DbDep) -> GlossaryOut:
    existing = [
        r
        for r in db.scalars(
            select(GlossaryTermRecord).where(GlossaryTermRecord.workspace_id == ctx.workspace_id)
        )
        if r.term.lower() == term.term.lower()
    ]
    if existing:
        raise Conflict("A glossary term with this name exists", code="term_exists")
    _check_glossary_refs(db, ctx, term)
    rec = upsert_glossary(db, ctx.workspace_id, term, ctx.user_id)
    _snap(db, ctx, f"glossary {term.term} created")
    _audit(db, ctx, "glossary.create", "glossary_term", rec.id, {"term": term.term})
    return _gloss_out(rec)


@router.put(
    "/glossary/{term_id}", response_model=GlossaryOut, operation_id="updateGlossaryTerm", tags=["glossary"]
)
def update_term(term_id: str, term: GlossaryTerm, ctx: EditorCtx, db: DbDep) -> GlossaryOut:
    rec = db.get(GlossaryTermRecord, term_id)
    if rec is None or rec.workspace_id != ctx.workspace_id:
        raise NotFound("Glossary term not found")
    _check_glossary_refs(db, ctx, term)
    rec.term = term.term
    rec.spec = term.model_dump(mode="json")
    db.flush()
    _snap(db, ctx, f"glossary {term.term} updated")
    _audit(db, ctx, "glossary.update", "glossary_term", rec.id, {"term": term.term})
    return _gloss_out(rec)


@router.delete(
    "/glossary/{term_id}", response_model=OkResponse, operation_id="deleteGlossaryTerm", tags=["glossary"]
)
def delete_term(term_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    rec = db.get(GlossaryTermRecord, term_id)
    if rec is None or rec.workspace_id != ctx.workspace_id:
        raise NotFound("Glossary term not found")
    db.delete(rec)
    db.flush()
    _snap(db, ctx, f"glossary {rec.term} deleted")
    return OkResponse()


# ----------------------------------------------------------------------------- calendar


@router.get(
    "/semantic-models/calendar", response_model=CalendarConfig, operation_id="getCalendar", tags=["calendar"]
)
def get_calendar(ctx: ViewerCtx) -> CalendarConfig:
    cal = merged_settings(ctx.workspace).get("calendar", {})
    return CalendarConfig.model_validate({k: v for k, v in cal.items() if k in CalendarConfig.model_fields})


@router.put(
    "/semantic-models/calendar",
    response_model=CalendarConfig,
    operation_id="updateCalendar",
    tags=["calendar"],
)
def put_calendar(body: CalendarConfig, ctx: EditorCtx, db: DbDep) -> CalendarConfig:
    settings = merged_settings(ctx.workspace)
    settings["calendar"] = body.model_dump(mode="json")
    ctx.workspace.settings = settings
    db.flush()
    _snap(db, ctx, "calendar updated")
    _audit(db, ctx, "calendar.update", "calendar", None, body.model_dump(mode="json"))
    return body


@router.get(
    "/semantic-models/calendar/resolve",
    response_model=PeriodResolution,
    operation_id="resolvePeriod",
    tags=["calendar"],
)
def resolve(ctx: ViewerCtx, text: str, today: dt.date | None = None) -> PeriodResolution:
    """Resolve a period expression ("August", "2026-08", "Q3", "YTD", "last month", "rolling 30 days")."""
    from analystos_engine.calendar import CalendarError, previous_period, resolve_period

    cfg = get_calendar(ctx)
    try:
        window = resolve_period(text, today or dt.date.today(), cfg)
    except CalendarError as exc:
        raise Unprocessable(str(exc), code="invalid_period") from exc
    return PeriodResolution(
        input=text,
        window=window,
        previous_period=previous_period(window, "pop"),
        same_period_last_year=previous_period(window, "yoy"),
    )
