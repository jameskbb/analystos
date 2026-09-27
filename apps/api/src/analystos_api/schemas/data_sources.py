from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import ApiModel

# The engine's "duckdb" connector reads a server-side path; it is internal (workspace stores) and is not
# offered through the API, so users cannot point a data source at another workspace's files.
ConnectorKind = Literal["postgres", "mysql", "sqlserver", "snowflake", "bigquery"]


class ConnectorKindInfo(BaseModel):
    kind: str
    label: str
    config_schema: dict[str, Any] = Field(description="JSON schema of the connection form (config + secrets)")
    secret_fields: list[str]


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: ConnectorKind
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict, description="Non-secret connection options")
    secrets: dict[str, Any] = Field(
        default_factory=dict,
        description="Secret values (password, private key, service-account JSON). Encrypted at rest, never returned.",
    )


class DataSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    config: dict[str, Any] | None = None
    secrets: dict[str, Any | None] | None = Field(
        default=None, description="Keys set to a value replace the secret; keys set to null remove it"
    )


class DataSourceOut(ApiModel):
    id: str
    workspace_id: str
    name: str
    kind: str
    description: str
    config: dict[str, Any]
    secret_fields: list[str] = Field(description="Names of stored secrets (values are never returned)")
    status: str
    last_tested_at: datetime | None
    last_test_message: str | None
    created_at: datetime
    updated_at: datetime


class ConnectionTestOut(BaseModel):
    ok: bool
    message: str
    latency_ms: float | None = None
    server_version: str | None = None
    read_only_enforced: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class SchemaOut(BaseModel):
    name: str
    table_count: int | None = None


class RemoteColumn(BaseModel):
    name: str
    type: str
    nullable: bool = True


class RemoteTable(BaseModel):
    name: str
    schema_name: str
    kind: str = "table"
    row_count: int | None = None
    columns: list[RemoteColumn] = Field(default_factory=list)


class SnapshotTableRequest(BaseModel):
    schema_name: str
    table: str
    dataset_name: str | None = None
    table_name: str | None = Field(
        default=None, description="Target DuckDB table name (defaults to the source name)"
    )
    row_limit: int | None = Field(default=None, ge=1, le=10_000_000)
