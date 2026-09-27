"""Dashboards: layout JSON, tiles bound to metrics / saved queries / artifacts / findings, filters and dates."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from ..deps import READ_ONLY_POST, DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import ApiError, NotFound
from ..models import Dashboard, DashboardTile, DashboardVersion
from ..schemas.common import ERROR_RESPONSES, OkResponse
from ..schemas.dashboards import (
    DashboardCreate,
    DashboardOut,
    DashboardSummary,
    DashboardUpdate,
    DashboardVersionOut,
    TileData,
    TileDataRequest,
    TileIn,
    TileOut,
    TileUpdate,
)
from ..schemas.datasets import LineageGraph
from ..services.dashboards import (
    get_dashboard,
    snapshot_dashboard,
    tile_data,
    tile_lineage,
    tiles_of,
    validate_binding,
)
from ..services.observability import audit

router = APIRouter(
    prefix="/workspaces/{workspace_id}/dashboards", tags=["dashboards"], responses=ERROR_RESPONSES
)


def dashboard_out(db: DbDep, d: Dashboard) -> DashboardOut:
    out = DashboardOut.model_validate(d)
    out.tiles = [TileOut.model_validate(t) for t in tiles_of(db, d.id)]
    return out


def _get_tile(db: DbDep, d: Dashboard, tile_id: str) -> DashboardTile:
    t = db.get(DashboardTile, tile_id)
    if t is None or t.dashboard_id != d.id:
        raise NotFound("Tile not found")
    return t


@router.get("", response_model=list[DashboardSummary], operation_id="listDashboards")
def list_dashboards(ctx: ViewerCtx, db: DbDep) -> list[DashboardSummary]:
    rows = db.scalars(
        select(Dashboard)
        .where(Dashboard.workspace_id == ctx.workspace_id)
        .order_by(Dashboard.updated_at.desc())
    ).all()
    return [DashboardSummary.model_validate(r) for r in rows]


@router.post("", response_model=DashboardOut, status_code=201, operation_id="createDashboard")
def create(body: DashboardCreate, ctx: EditorCtx, db: DbDep) -> DashboardOut:
    d = Dashboard(
        workspace_id=ctx.workspace_id,
        name=body.name,
        description=body.description,
        filters=[f.model_dump() for f in body.filters],
        date_range=body.date_range.model_dump(exclude_none=True) if body.date_range else None,
        created_by=ctx.user_id,
        version_no=0,
    )
    db.add(d)
    db.flush()
    snapshot_dashboard(db, d, ctx.user_id)
    audit(
        db,
        action="dashboard.create",
        resource_type="dashboard",
        resource_id=d.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"name": d.name},
    )
    return dashboard_out(db, d)


@router.get("/{dashboard_id}", response_model=DashboardOut, operation_id="getDashboard")
def get(dashboard_id: str, ctx: ViewerCtx, db: DbDep) -> DashboardOut:
    return dashboard_out(db, get_dashboard(db, ctx.workspace_id, dashboard_id))


@router.patch("/{dashboard_id}", response_model=DashboardOut, operation_id="updateDashboard")
def update(dashboard_id: str, body: DashboardUpdate, ctx: EditorCtx, db: DbDep) -> DashboardOut:
    d = get_dashboard(db, ctx.workspace_id, dashboard_id)
    if body.name is not None:
        d.name = body.name
    if body.description is not None:
        d.description = body.description
    if body.layout is not None:
        tile_ids = {t.id for t in tiles_of(db, d.id)}
        d.layout = [item.model_dump() for item in body.layout if item.i in tile_ids]
    if body.filters is not None:
        d.filters = [f.model_dump() for f in body.filters]
    if "date_range" in body.model_fields_set:
        d.date_range = body.date_range.model_dump(exclude_none=True) if body.date_range else None
    snapshot_dashboard(db, d, ctx.user_id)
    return dashboard_out(db, d)


@router.delete("/{dashboard_id}", response_model=OkResponse, operation_id="deleteDashboard")
def delete(dashboard_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    d = get_dashboard(db, ctx.workspace_id, dashboard_id)
    audit(
        db,
        action="dashboard.delete",
        resource_type="dashboard",
        resource_id=d.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"name": d.name},
    )
    db.delete(d)
    return OkResponse()


@router.post(
    "/{dashboard_id}/tiles", response_model=TileOut, status_code=201, operation_id="addDashboardTile"
)
def add_tile(dashboard_id: str, body: TileIn, ctx: EditorCtx, db: DbDep) -> TileOut:
    d = get_dashboard(db, ctx.workspace_id, dashboard_id)
    binding = body.binding.model_dump(exclude_none=True)
    if not binding.get("params"):
        binding.pop("params", None)
    validate_binding(db, ctx.workspace, body.kind, binding)
    t = DashboardTile(
        dashboard_id=d.id, kind=body.kind, title=body.title, binding=binding, viz=body.viz, text=body.text
    )
    db.add(t)
    db.flush()
    bottom = max((item["y"] + item["h"] for item in d.layout or []), default=0)
    item = (
        body.layout.model_dump()
        if body.layout
        else {"x": 0, "y": bottom, "w": 6 if body.kind != "kpi" else 3, "h": 4 if body.kind != "kpi" else 3}
    )
    item["i"] = t.id
    d.layout = [*(d.layout or []), item]
    snapshot_dashboard(db, d, ctx.user_id)
    return TileOut.model_validate(t)


@router.patch("/{dashboard_id}/tiles/{tile_id}", response_model=TileOut, operation_id="updateDashboardTile")
def update_tile(dashboard_id: str, tile_id: str, body: TileUpdate, ctx: EditorCtx, db: DbDep) -> TileOut:
    d = get_dashboard(db, ctx.workspace_id, dashboard_id)
    t = _get_tile(db, d, tile_id)
    if body.binding is not None:
        binding = body.binding.model_dump(exclude_none=True)
        if not binding.get("params"):
            binding.pop("params", None)
        validate_binding(db, ctx.workspace, t.kind, binding)
        t.binding = binding
    for field in ("title", "viz", "text"):
        value = getattr(body, field)
        if value is not None:
            setattr(t, field, value)
    db.flush()
    snapshot_dashboard(db, d, ctx.user_id)
    return TileOut.model_validate(t)


@router.delete(
    "/{dashboard_id}/tiles/{tile_id}", response_model=OkResponse, operation_id="deleteDashboardTile"
)
def delete_tile(dashboard_id: str, tile_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    d = get_dashboard(db, ctx.workspace_id, dashboard_id)
    t = _get_tile(db, d, tile_id)
    db.delete(t)
    d.layout = [item for item in d.layout or [] if item.get("i") != tile_id]
    db.flush()
    snapshot_dashboard(db, d, ctx.user_id)
    return OkResponse()


def _data(
    db: DbDep, state: StateDep, ctx: ViewerCtx, d: Dashboard, t: DashboardTile, body: TileDataRequest
) -> TileData:
    try:
        data = tile_data(
            db,
            state,
            ctx.workspace,
            d,
            t,
            [f.model_dump() for f in body.filters],
            body.date_range.model_dump(exclude_none=True) if body.date_range else None,
            ctx.user_id,
        )
    except ApiError as exc:
        return TileData(
            tile_id=t.id, kind=t.kind, title=t.title, filter_context=[], provenance={}, error=exc.detail
        )
    return TileData.model_validate(data)


@router.post(
    "/{dashboard_id}/tiles/{tile_id}/data",
    response_model=TileData,
    operation_id="getTileData",
    openapi_extra=READ_ONLY_POST,
)
def get_tile_data(
    dashboard_id: str, tile_id: str, body: TileDataRequest, ctx: ViewerCtx, db: DbDep, state: StateDep
) -> TileData:
    d = get_dashboard(db, ctx.workspace_id, dashboard_id)
    return _data(db, state, ctx, d, _get_tile(db, d, tile_id), body)


@router.post(
    "/{dashboard_id}/data",
    response_model=list[TileData],
    operation_id="getDashboardData",
    openapi_extra=READ_ONLY_POST,
)
def get_all_data(
    dashboard_id: str, body: TileDataRequest, ctx: ViewerCtx, db: DbDep, state: StateDep
) -> list[TileData]:
    """Data for every tile; a failing tile returns ``error`` without failing the whole dashboard."""
    d = get_dashboard(db, ctx.workspace_id, dashboard_id)
    return [_data(db, state, ctx, d, t, body) for t in tiles_of(db, d.id)]


@router.get(
    "/{dashboard_id}/tiles/{tile_id}/lineage",
    response_model=LineageGraph,
    operation_id="getTileLineage",
    tags=["dashboards", "lineage"],
)
def tile_lineage_route(dashboard_id: str, tile_id: str, ctx: ViewerCtx, db: DbDep) -> LineageGraph:
    """Tile -> KPI/chart -> query -> metric (version) -> entity -> dataset (version) -> source (spec §62)."""
    d = get_dashboard(db, ctx.workspace_id, dashboard_id)
    return LineageGraph.model_validate(tile_lineage(db, ctx.workspace, d, _get_tile(db, d, tile_id)))


@router.get(
    "/{dashboard_id}/versions", response_model=list[DashboardVersionOut], operation_id="listDashboardVersions"
)
def versions(dashboard_id: str, ctx: ViewerCtx, db: DbDep) -> list[DashboardVersionOut]:
    d = get_dashboard(db, ctx.workspace_id, dashboard_id)
    rows = db.scalars(
        select(DashboardVersion)
        .where(DashboardVersion.dashboard_id == d.id)
        .order_by(DashboardVersion.version_no.desc())
    ).all()
    return [DashboardVersionOut.model_validate(r) for r in rows]
