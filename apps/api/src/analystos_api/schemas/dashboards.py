from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import ApiModel

TileKind = Literal["kpi", "chart", "table", "text"]


class LayoutItem(BaseModel):
    i: str = Field(description="Tile id")
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=24)
    h: int = Field(ge=1, le=40)


class DashboardFilter(BaseModel):
    dimension: str
    op: Literal["eq", "neq", "in", "not_in", "is_null", "not_null"] = "in"
    values: list[Any] = Field(default_factory=list)
    label: str | None = None


class DateRange(BaseModel):
    text: str | None = Field(
        default=None, description="Period expression, e.g. 'August 2026', 'last 3 months', 'YTD'"
    )
    start: str | None = Field(default=None, description="Inclusive ISO date")
    end: str | None = Field(default=None, description="Inclusive ISO date")


class TileBinding(BaseModel):
    metric_query: dict[str, Any] | None = Field(
        default=None, description="Engine MetricQuery (semantic tile)"
    )
    saved_query_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    artifact_id: str | None = None
    finding_id: str | None = None


class TileIn(BaseModel):
    kind: TileKind
    title: str = ""
    binding: TileBinding = Field(default_factory=TileBinding)
    viz: dict[str, Any] = Field(default_factory=dict, description="Display options, e.g. {type: 'bar'}")
    text: str = ""
    layout: LayoutItem | None = Field(default=None, description="Initial position (defaults to the bottom)")


class TileUpdate(BaseModel):
    title: str | None = None
    binding: TileBinding | None = None
    viz: dict[str, Any] | None = None
    text: str | None = None


class TileOut(ApiModel):
    id: str
    kind: TileKind
    title: str
    binding: dict[str, Any]
    viz: dict[str, Any]
    text: str


class DashboardSummary(ApiModel):
    id: str
    name: str
    description: str
    version_no: int
    created_at: datetime
    updated_at: datetime


class DashboardOut(DashboardSummary):
    layout: list[dict[str, Any]]
    filters: list[dict[str, Any]]
    date_range: dict[str, Any] | None
    tiles: list[TileOut] = Field(default_factory=list)


class DashboardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = ""
    filters: list[DashboardFilter] = Field(default_factory=list)
    date_range: DateRange | None = None


class DashboardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    layout: list[LayoutItem] | None = None
    filters: list[DashboardFilter] | None = None
    date_range: DateRange | None = None


class TileDataRequest(BaseModel):
    filters: list[DashboardFilter] = Field(
        default_factory=list, description="Extra filters on top of the dashboard's"
    )
    date_range: DateRange | None = None


class TileData(BaseModel):
    tile_id: str
    kind: str
    title: str
    result: dict[str, Any] | None = Field(default=None, description="TabularResult")
    kpi: dict[str, Any] | None = None
    chart: dict[str, Any] | None = Field(default=None, description="ChartSuggestion with an ECharts option")
    text: str | None = None
    filter_context: list[dict[str, Any]]
    provenance: dict[str, Any] = Field(description="metric versions, SQL, dataset versions, ignored filters")
    compiled: dict[str, Any] | None = None
    error: str | None = None


class DashboardVersionOut(ApiModel):
    id: str
    version_no: int
    snapshot: dict[str, Any]
    created_by: str | None
    created_at: datetime
