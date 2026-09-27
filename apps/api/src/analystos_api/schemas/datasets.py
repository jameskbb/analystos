from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from analystos_engine.ingest import FileInspection, IngestOptions
from pydantic import BaseModel, ConfigDict, Field

from .common import ApiModel


class UploadOut(ApiModel):
    id: str
    workspace_id: str
    filename: str
    size_bytes: int
    sha256: str
    file_kind: Literal["csv", "excel", "parquet", "json"]
    status: str
    inspection: FileInspection = Field(
        description="Detected dialect/encoding, sheets, table blocks with header rows, skipped title/blank/total "
        "rows, inferred column types, preview rows and warnings"
    )
    created_at: datetime


class FilePreviewRequest(BaseModel):
    options: IngestOptions = Field(
        default_factory=IngestOptions,
        description="sheet / table_key / header_row / delimiter / column_types overrides",
    )
    limit: int = Field(default=100, ge=1, le=1000)


class FilePreviewOut(BaseModel):
    upload_id: str
    options: IngestOptions
    preview: FileInspection


class IngestRequest(BaseModel):
    dataset_name: str | None = Field(default=None, max_length=200)
    table_name: str | None = Field(default=None, max_length=63, description="DuckDB table name (identifier)")
    description: str = ""
    if_exists: Literal["fail", "replace", "append"] = "fail"
    options: IngestOptions = Field(
        default_factory=IngestOptions,
        description="table_key (Excel block) / sheet / header_row / column_types ...",
    )


class DatasetColumn(BaseModel):
    name: str
    type: str
    nullable: bool = True


class DatasetOut(ApiModel):
    id: str
    workspace_id: str
    name: str
    table_name: str
    description: str
    source_kind: str
    source_ref: dict[str, Any]
    data_source_id: str | None
    row_count: int
    columns: list[DatasetColumn]
    profile_status: str
    profiled_at: datetime | None
    current_version_id: str | None
    tags: list[str]
    issue_count: int = 0
    created_at: datetime
    updated_at: datetime


class DatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] | None = None


class DatasetVersionOut(ApiModel):
    id: str
    dataset_id: str
    version_no: int
    content_hash: str
    row_count: int
    columns: list[DatasetColumn]
    ingest_options: dict[str, Any]
    upload_id: str | None
    captured_at: datetime


class LineageNode(BaseModel):
    id: str = Field(description="Graph-unique id, e.g. 'metric:revenue' or 'dataset:<id>'")
    kind: str = Field(
        description="finding | artifact | chart | query | metric | entity | dataset | source_file | "
        "source_table | investigation | kpi | dashboard"
    )
    label: str
    detail: str | None = None
    ref_id: str | None = Field(default=None, description="Id of the referenced resource for navigation")
    meta: dict[str, Any] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    label: str


def edge(*, from_: str, to: str, label: str) -> LineageEdge:
    """A lineage edge (the JSON field is ``from``, a Python keyword)."""
    return LineageEdge.model_validate({"from": from_, "to": to, "label": label})


class LineageGraph(BaseModel):
    nodes: list[LineageNode]
    edges: list[LineageEdge]


class DatasetUsage(BaseModel):
    entities: list[str]
    dimensions: list[str]
    metrics: list[str]
    quality_rules: list[str]
    relationships: list[str]
    saved_queries: list[str]
    lineage: LineageGraph


class SchemaColumn(BaseModel):
    name: str
    type: str


class SchemaTable(BaseModel):
    table: str
    dataset_id: str | None
    dataset_name: str | None
    row_count: int | None
    columns: list[SchemaColumn]


class WorkspaceSchema(BaseModel):
    tables: list[SchemaTable]
    metrics: list[str]
    dimensions: list[str]
