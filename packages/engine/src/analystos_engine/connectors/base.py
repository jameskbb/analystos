"""Connector protocol, shared configuration models and the connector registry.

Every connector is read-only: SQL passes :func:`analystos_engine.sqlsafety.ensure_read_only`
with the connector's sqlglot dialect *and* the session is put in read-only mode where
the database supports it. Secrets are held as :class:`pydantic.SecretStr`, never
logged and scrubbed from any error message raised to callers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..types import ColumnInfo, ConnectionTestResult, QueryResult, TableInfo, TableProfile

__all__ = [
    "Connector",
    "ConnectorError",
    "ConnectorConfig",
    "ConnectionTestResult",
    "SchemaInfo",
    "get_connector",
    "register_connector",
    "available_kinds",
    "connector_config_schema",
    "redact",
    "CONNECTOR_KINDS",
]

logger = logging.getLogger(__name__)

CONNECTOR_KINDS = ("duckdb", "postgres", "mysql", "sqlserver", "snowflake", "bigquery")


class ConnectorError(RuntimeError):
    """A connector failed. The message never contains secrets."""

    def __init__(self, message: str, *, kind: str | None = None, code: str = "connector_error") -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code


class ConnectorConfig(BaseModel):
    """Common non-secret options shared by all connectors."""

    model_config = ConfigDict(extra="forbid")

    query_timeout_s: float = Field(default=60.0, gt=0, le=3600)
    default_row_limit: int = Field(default=10_000, gt=0, le=1_000_000)
    profile_sample_rows: int = Field(default=50_000, gt=0, le=5_000_000)


class SchemaInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    table_count: int | None = None


@runtime_checkable
class Connector(Protocol):
    """Read-only access to a data source."""

    @property
    def kind(self) -> str: ...

    @property
    def dialect(self) -> str: ...

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def test(self) -> ConnectionTestResult: ...

    def discover_schemas(self) -> list[SchemaInfo]: ...

    def discover_tables(self, schema: str) -> list[TableInfo]: ...

    def inspect_columns(self, schema: str, table: str) -> list[ColumnInfo]: ...

    def preview(self, schema: str, table: str, limit: int = 100) -> QueryResult: ...

    def query(
        self, sql: str, params: Mapping[str, Any] | None = None, limit: int | None = None
    ) -> QueryResult: ...

    def profile(self, schema: str, table: str) -> TableProfile: ...


def redact(message: str, secrets: Iterable[str | SecretStr | None]) -> str:
    """Replace every secret value occurring in ``message`` with ``***``."""
    out = message
    for s in secrets:
        if s is None:
            continue
        raw = s.get_secret_value() if isinstance(s, SecretStr) else s
        if raw and len(raw) >= 3:
            out = out.replace(raw, "***")
    return out


ConnectorFactory = Callable[[Mapping[str, Any], Mapping[str, Any]], Connector]
ConfigSchemaFactory = Callable[[], dict[str, Any]]

_REGISTRY: dict[str, tuple[ConnectorFactory, ConfigSchemaFactory]] = {}


def register_connector(kind: str, factory: ConnectorFactory, schema: ConfigSchemaFactory) -> None:
    """Register a connector implementation under ``kind``."""
    _REGISTRY[kind] = (factory, schema)


def _ensure_builtin_registered() -> None:
    if all(k in _REGISTRY for k in CONNECTOR_KINDS):
        return
    from . import (  # noqa: F401  (registration side effect)
        bigquery,
        duckdb_store,
        mysql,
        postgres,
        snowflake,
        sqlserver,
    )


def available_kinds() -> list[str]:
    _ensure_builtin_registered()
    return sorted(_REGISTRY)


def connector_config_schema(kind: str) -> dict[str, Any]:
    """JSON schema of the combined (config + secrets) form for ``kind`` (UI forms)."""
    _ensure_builtin_registered()
    if kind not in _REGISTRY:
        raise ConnectorError(
            f"unknown connector kind {kind!r}; expected one of {sorted(_REGISTRY)}", kind=kind
        )
    return _REGISTRY[kind][1]()


def get_connector(
    kind: str,
    config: Mapping[str, Any] | BaseModel | None = None,
    secrets: Mapping[str, Any] | None = None,
) -> Connector:
    """Build a connector of ``kind`` from non-secret ``config`` and ``secrets``.

    Drivers are imported lazily, so building a connector whose optional driver is
    not installed raises :class:`ConnectorError` with install instructions only when
    it connects.
    """
    _ensure_builtin_registered()
    if kind not in _REGISTRY:
        raise ConnectorError(
            f"unknown connector kind {kind!r}; expected one of {sorted(_REGISTRY)}", kind=kind
        )
    cfg: Mapping[str, Any]
    if config is None:
        cfg = {}
    elif isinstance(config, BaseModel):
        cfg = config.model_dump()
    else:
        cfg = config
    return _REGISTRY[kind][0](cfg, secrets or {})
