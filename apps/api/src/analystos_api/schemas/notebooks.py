from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import ApiModel

CellKind = Literal["sql", "python", "markdown", "chart", "finding"]


class CellOut(ApiModel):
    id: str
    position: int
    kind: CellKind
    source: str
    config: dict[str, Any]
    output: dict[str, Any] | None
    status: Literal["idle", "ok", "error", "stale"]
    execution_count: int
    last_run_at: datetime | None
    updated_at: datetime


class NotebookSummary(ApiModel):
    id: str
    title: str
    description: str
    investigation_id: str | None
    created_at: datetime
    updated_at: datetime


class NotebookOut(NotebookSummary):
    cells: list[CellOut] = Field(default_factory=list)


class NotebookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""


class NotebookUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None


class CellCreate(BaseModel):
    kind: CellKind
    source: str = ""
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="python: {inputs: {name: sql | 'cell:<id>'}}; chart: {source_cell_id, type?}; "
        "finding: {finding_id}; sql: {params?, parameters?, limit?}",
    )
    position: int | None = Field(default=None, ge=0, description="Insert position (default: append)")


class CellUpdate(BaseModel):
    kind: CellKind | None = None
    source: str | None = None
    config: dict[str, Any] | None = None


class ReorderRequest(BaseModel):
    cell_ids: list[str]


class RunAllResult(BaseModel):
    notebook: NotebookOut
    executed: int
    stopped_at: str | None = Field(default=None, description="Cell id where execution stopped on an error")
