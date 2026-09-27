"""Snowflake connector (snowflake-connector-python). Install with the ``snowflake`` extra.

Snowflake has no read-only session flag: read-only is enforced by validating every
statement with the ``snowflake`` dialect (``PUT``/``GET``/``COPY``/DDL/DML/``CALL`` and
``SYSTEM$`` functions are rejected), running with ``AUTOCOMMIT=FALSE`` and rolling back
after each query, a ``STATEMENT_TIMEOUT_IN_SECONDS`` and a ``QUERY_TAG`` for auditing.
Configure a ``role`` that only has ``USAGE``/``SELECT`` grants. Parameters use ``%(name)s``.
Authentication: password or key pair (``private_key`` PEM, optional passphrase).
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..types import ColumnInfo, TableInfo
from .base import ConnectorConfig, SchemaInfo, register_connector
from .sql_base import DBAPIConnector, json_schema_for

__all__ = ["SnowflakeConnector", "SnowflakeConfig", "SnowflakeSecrets"]

_TYPE_CODES = {
    0: "NUMBER", 1: "REAL", 2: "TEXT", 3: "DATE", 4: "TIMESTAMP", 5: "VARIANT", 6: "TIMESTAMP_LTZ", 7: "TIMESTAMP_TZ",
    8: "TIMESTAMP_NTZ", 9: "OBJECT", 10: "ARRAY", 11: "BINARY", 12: "TIME", 13: "BOOLEAN",
}  # fmt: skip


class SnowflakeConfig(ConnectorConfig):
    account: str
    user: str
    warehouse: str | None = None
    database: str
    schema_name: str | None = Field(default=None, alias="schema")
    role: str | None = None
    login_timeout_s: int = Field(default=30, ge=1, le=600)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SnowflakeSecrets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr | None = None
    private_key: SecretStr | None = None
    private_key_passphrase: SecretStr | None = None


class SnowflakeConnector(DBAPIConnector):
    kind: ClassVar[str] = "snowflake"
    dialect: ClassVar[str] = "snowflake"
    driver_module: ClassVar[str] = "snowflake.connector"
    extra: ClassVar[str] = "snowflake"
    Config: ClassVar[type[ConnectorConfig]] = SnowflakeConfig
    Secrets: ClassVar[type[BaseModel]] = SnowflakeSecrets
    VERSION_SQL: ClassVar[str] = "SELECT CURRENT_VERSION()"
    SYSTEM_SCHEMAS: ClassVar[tuple[str, ...]] = ("information_schema",)

    config: SnowflakeConfig
    secrets: SnowflakeSecrets

    def _target(self) -> str:
        return f"{self.config.account}/{self.config.database}"

    def _private_key_der(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        assert self.secrets.private_key is not None
        pw = self.secrets.private_key_passphrase
        key = serialization.load_pem_private_key(
            self.secrets.private_key.get_secret_value().encode(),
            password=pw.get_secret_value().encode() if pw else None,
        )
        return key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def _open(self, drv: Any) -> Any:
        c = self.config
        kwargs: dict[str, Any] = {
            "account": c.account,
            "user": c.user,
            "database": c.database,
            "login_timeout": c.login_timeout_s,
            "network_timeout": int(c.query_timeout_s) + 30,
            "application": "AnalystOS",
            "autocommit": False,
            "session_parameters": {
                "QUERY_TAG": "AnalystOS read-only",
                "STATEMENT_TIMEOUT_IN_SECONDS": int(c.query_timeout_s),
                "AUTOCOMMIT": False,
            },
        }
        for key, val in (("warehouse", c.warehouse), ("schema", c.schema_name), ("role", c.role)):
            if val:
                kwargs[key] = val
        if self.secrets.private_key is not None:
            kwargs["private_key"] = self._private_key_der()
        elif self.secrets.password is not None:
            kwargs["password"] = self.secrets.password.get_secret_value()
        return drv.connect(**kwargs)

    def _type_name(self, type_code: Any) -> str:
        return _TYPE_CODES.get(type_code, str(type_code)) if isinstance(type_code, int) else str(type_code)

    def _schema_filter(self) -> str:
        return f"upper(table_catalog) = {self._lit(self.config.database.upper())}"

    def discover_schemas(self) -> list[SchemaInfo]:
        rows = self._meta(
            "SELECT table_schema, count(*) FROM information_schema.tables "
            f"WHERE {self._schema_filter()} GROUP BY table_schema ORDER BY table_schema"
        )
        return [
            SchemaInfo(name=str(r[0]), table_count=int(r[1]))
            for r in rows
            if str(r[0]).lower() not in self.SYSTEM_SCHEMAS
        ]

    def discover_tables(self, schema: str) -> list[TableInfo]:
        rows = self._meta(
            "SELECT table_name, table_type, row_count FROM information_schema.tables "
            f"WHERE {self._schema_filter()} AND table_schema = {self._lit(schema)} ORDER BY table_name"
        )
        return [
            TableInfo(
                name=str(r[0]),
                schema_name=schema,
                kind="view" if "VIEW" in str(r[1]).upper() else "table",
                row_count=int(r[2]) if r[2] is not None else None,
                source=self.kind,
            )
            for r in rows
        ]

    def inspect_columns(self, schema: str, table: str) -> list[ColumnInfo]:
        rows = self._meta(
            "SELECT column_name, data_type, is_nullable, ordinal_position FROM information_schema.columns "
            f"WHERE {self._schema_filter()} AND table_schema = {self._lit(schema)} AND table_name = {self._lit(table)} "
            "ORDER BY ordinal_position"
        )
        return [
            ColumnInfo(name=str(r[0]), type=str(r[1]), nullable=str(r[2]).upper() == "YES", ordinal=int(r[3]))
            for r in rows
        ]


register_connector(
    "snowflake",
    lambda config, secrets: SnowflakeConnector(config, secrets),
    lambda: json_schema_for(SnowflakeConfig, SnowflakeSecrets),
)
