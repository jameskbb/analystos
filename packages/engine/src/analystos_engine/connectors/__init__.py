"""Read-only connectors: the workspace DuckDB store and external databases.

External drivers are optional extras (``postgres``, ``mysql``, ``sqlserver``,
``sqlserver-pymssql``, ``snowflake``, ``bigquery``) imported only when a connector connects.
"""

from .base import (
    CONNECTOR_KINDS,
    ConnectionTestResult,
    Connector,
    ConnectorConfig,
    ConnectorError,
    SchemaInfo,
    available_kinds,
    connector_config_schema,
    get_connector,
    redact,
    register_connector,
)

__all__ = [
    "CONNECTOR_KINDS",
    "ConnectionTestResult",
    "Connector",
    "ConnectorConfig",
    "ConnectorError",
    "SchemaInfo",
    "available_kinds",
    "connector_config_schema",
    "get_connector",
    "redact",
    "register_connector",
]
