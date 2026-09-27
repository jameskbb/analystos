from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .common import ApiModel, ColumnMeta, TabularResult


class QueryParameterDef(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", description="Referenced in SQL as $name")
    type: Literal["string", "number", "integer", "date", "boolean", "string_list"] = "string"
    label: str | None = None
    default: Any = None
    required: bool = True
    options: list[Any] | None = Field(default=None, description="Allowed values for a dropdown")


class ChartSuggestion(BaseModel):
    type: Literal[
        "kpi",
        "line",
        "area",
        "bar",
        "stacked_bar",
        "heatmap",
        "box",
        "waterfall",
        "scatter",
        "histogram",
        "table",
    ]
    title: str
    reason: str
    score: float
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    series: str | None = None
    option: dict[str, Any] = Field(description="ECharts option (dataset-based) ready to render")


class RunQueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=1_000_000)
    params: dict[str, Any] = Field(default_factory=dict)
    parameters: list[QueryParameterDef] = Field(
        default_factory=list, description="Types/defaults for $params"
    )
    limit: int | None = Field(default=None, ge=1, le=100_000)
    data_source_id: str | None = Field(default=None, description="Run against an external source (read-only)")
    suggest_chart: bool = True


class QueryRunOut(ApiModel):
    id: str
    status: Literal["succeeded", "failed", "rejected", "running"]
    origin: str
    sql: str
    params: dict[str, Any]
    saved_query_id: str | None
    data_source_id: str | None
    error: str | None
    row_count: int
    truncated: bool
    elapsed_ms: float
    columns: list[ColumnMeta]
    dataset_versions: dict[str, Any]
    created_at: datetime


class QueryRunResult(QueryRunOut):
    result: TabularResult
    warnings: list[str] = Field(default_factory=list)
    charts: list[ChartSuggestion] = Field(default_factory=list)


class QueryRunDetail(QueryRunOut):
    result_snapshot: list[list[Any]]


class SavedQueryIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = ""
    sql: str = Field(min_length=1, max_length=1_000_000)
    parameters: list[QueryParameterDef] = Field(default_factory=list)
    data_source_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    chart: dict[str, Any] | None = Field(default=None, description="Saved chart configuration")

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return [t.strip() for t in v if t.strip()][:50]


class SavedQueryOut(ApiModel):
    id: str
    name: str
    description: str
    sql: str
    parameters: list[QueryParameterDef]
    data_source_id: str | None
    tags: list[str]
    chart: dict[str, Any] | None
    version_no: int
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class RunSavedQueryRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    limit: int | None = Field(default=None, ge=1, le=100_000)


class CompareRequest(BaseModel):
    left_run_id: str
    right_run_id: str
    keys: list[str] | None = Field(
        default=None, description="Join columns; default = shared non-numeric columns"
    )


class CompareResult(BaseModel):
    keys: list[str]
    measures: list[str]
    rows: list[dict[str, Any]]
    left_rows: int
    right_rows: int
    changed_rows: int
    identical: bool
    left_run_id: str
    right_run_id: str


class ValidateSqlRequest(BaseModel):
    sql: str
    dialect: str = "duckdb"


class ValidateSqlResult(BaseModel):
    ok: bool
    normalized_sql: str | None = None
    error: str | None = None
    code: str | None = None
    tables: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)
