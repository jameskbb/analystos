"""Shared API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["owner", "editor", "viewer"]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ErrorResponse(BaseModel):
    detail: str
    code: str
    request_id: str | None = None
    errors: list[dict[str, Any]] | dict[str, Any] | None = None


class OkResponse(BaseModel):
    ok: bool = True


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ColumnMeta(BaseModel):
    name: str
    type: str


class TabularResult(BaseModel):
    """A (possibly truncated) tabular result; ``rows`` are positional lists matching ``columns``."""

    columns: list[ColumnMeta]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    elapsed_ms: float = 0.0
    sql: str | None = None


class JobOut(ApiModel):
    id: str
    workspace_id: str | None
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: float
    message: str
    params: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    resource_type: str | None
    resource_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobAccepted(BaseModel):
    job: JobOut
    poll_url: str = Field(description="GET this URL until status is succeeded or failed")


class FilterContextItem(BaseModel):
    dimension: str
    op: str = "eq"
    values: list[Any] = Field(default_factory=list)
    label: str | None = None


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Bad request"},
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Forbidden (role, CSRF or read-only token)"},
    404: {"model": ErrorResponse, "description": "Not found (or not visible to the caller)"},
    409: {"model": ErrorResponse, "description": "Conflict"},
    422: {"model": ErrorResponse, "description": "Validation error"},
}
