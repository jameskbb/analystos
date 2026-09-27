"""Connector over a workspace DuckDB store (the analytical store of a workspace).

Config: ``workspace_dir`` (the directory holding ``warehouse.duckdb``). All reads go through
:meth:`WorkspaceStore.execute_read` (read-only SQL, row cap, timeout) and DuckDB itself runs
with external access disabled.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..sqlsafety import UnsafeSQLError
from ..store import QueryTimeoutError, StoreError, WorkspaceStore, quote_ident
from ..types import ColumnInfo, ConnectionTestResult, QueryResult, TableInfo, TableProfile
from .base import ConnectorConfig, ConnectorError, SchemaInfo, register_connector
from .sql_base import json_schema_for

__all__ = ["DuckDBConnector", "DuckDBConfig"]


class DuckDBConfig(ConnectorConfig):
    workspace_dir: str


class _NoSecrets(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DuckDBConnector:
    kind = "duckdb"
    dialect = "duckdb"

    def __init__(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, Any] | None = None,
        *,
        store: WorkspaceStore | None = None,
    ) -> None:
        if store is not None and not config:
            self.config = DuckDBConfig(workspace_dir=str(store.workspace_dir or ":memory:"))
        else:
            self.config = DuckDBConfig.model_validate(dict(config))
        self._store = store
        self._owns = store is None

    def connect(self) -> None:
        if self._store is None:
            try:
                self._store = WorkspaceStore(
                    self.config.workspace_dir, default_timeout_s=self.config.query_timeout_s
                )
            except StoreError as exc:
                raise ConnectorError(str(exc), kind=self.kind, code="connection_failed") from exc

    @property
    def store(self) -> WorkspaceStore:
        self.connect()
        assert self._store is not None
        return self._store

    def close(self) -> None:
        if self._store is not None and self._owns:
            self._store.close()
            self._store = None

    def test(self) -> ConnectionTestResult:
        started = time.perf_counter()
        try:
            version = self.store.scalar("SELECT version()")
        except (ConnectorError, StoreError) as exc:
            return ConnectionTestResult(ok=False, message=str(exc))
        return ConnectionTestResult(
            ok=True,
            message="connected; read-only SQL enforced and DuckDB external access disabled",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            server_version=str(version),
            read_only_enforced=True,
        )

    def discover_schemas(self) -> list[SchemaInfo]:
        return [SchemaInfo(name="main", table_count=len(self.store.list_tables(include_row_counts=False)))]

    def _check_schema(self, schema: str) -> None:
        if schema != "main":
            raise ConnectorError(
                f"schema {schema!r} does not exist (the workspace store has only 'main')", kind=self.kind
            )

    def discover_tables(self, schema: str) -> list[TableInfo]:
        self._check_schema(schema)
        return self.store.list_tables()

    def inspect_columns(self, schema: str, table: str) -> list[ColumnInfo]:
        self._check_schema(schema)
        try:
            return self.store.columns(table)
        except StoreError as exc:
            raise ConnectorError(str(exc), kind=self.kind) from exc

    def preview(self, schema: str, table: str, limit: int = 100) -> QueryResult:
        self._check_schema(schema)
        try:
            return self.store.preview(table, limit)
        except StoreError as exc:
            raise ConnectorError(str(exc), kind=self.kind) from exc

    def query(
        self, sql: str, params: Mapping[str, Any] | None = None, limit: int | None = None
    ) -> QueryResult:
        try:
            return self.store.execute_read(
                sql,
                dict(params) if params else None,
                limit if limit is not None else self.config.default_row_limit,
            )
        except UnsafeSQLError:
            raise
        except QueryTimeoutError as exc:
            raise ConnectorError(str(exc), kind=self.kind, code="timeout") from exc
        except StoreError as exc:
            raise ConnectorError(str(exc), kind=self.kind, code="query_failed") from exc

    def profile(self, schema: str, table: str) -> TableProfile:
        from ..profiling import profile_table

        self._check_schema(schema)
        if not self.store.has_table(table):
            raise ConnectorError(f"table {table!r} does not exist", kind=self.kind)
        big = self.store.row_count(table) > self.config.profile_sample_rows
        return profile_table(self.store, table, sample_rows=self.config.profile_sample_rows if big else None)

    def qualified(self, table: str) -> str:
        return quote_ident(table)


register_connector(
    "duckdb",
    lambda config, secrets: DuckDBConnector(config, secrets),
    lambda: json_schema_for(DuckDBConfig, _NoSecrets),
)
