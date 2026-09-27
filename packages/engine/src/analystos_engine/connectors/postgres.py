"""PostgreSQL connector (psycopg 3). Install with the ``postgres`` extra.

Read-only: the session is opened with ``default_transaction_read_only=on`` and a
``statement_timeout``, the psycopg connection is marked ``read_only``, every statement is
validated by ``ensure_read_only(sql, "postgres")`` and each query ends with a rollback.
Parameters use psycopg's ``%(name)s`` style. Use a login with only ``SELECT`` grants.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .base import ConnectorConfig, register_connector
from .sql_base import DBAPIConnector, json_schema_for

__all__ = ["PostgresConnector", "PostgresConfig", "PostgresSecrets"]


class PostgresConfig(ConnectorConfig):
    host: str
    port: int = Field(default=5432, ge=1, le=65535)
    database: str
    user: str
    sslmode: str = Field(default="prefer", pattern="^(disable|allow|prefer|require|verify-ca|verify-full)$")
    connect_timeout_s: int = Field(default=10, ge=1, le=300)


class PostgresSecrets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr | None = None


class PostgresConnector(DBAPIConnector):
    kind: ClassVar[str] = "postgres"
    dialect: ClassVar[str] = "postgres"
    driver_module: ClassVar[str] = "psycopg"
    extra: ClassVar[str] = "postgres"
    Config: ClassVar[type[ConnectorConfig]] = PostgresConfig
    Secrets: ClassVar[type[BaseModel]] = PostgresSecrets
    SYSTEM_SCHEMAS: ClassVar[tuple[str, ...]] = ("information_schema", "pg_catalog", "pg_toast")

    config: PostgresConfig
    secrets: PostgresSecrets

    def _target(self) -> str:
        return f"{self.config.host}:{self.config.port}/{self.config.database}"

    def _open(self, drv: Any) -> Any:
        c = self.config
        timeout_ms = int(c.query_timeout_s * 1000)
        return drv.connect(
            host=c.host,
            port=c.port,
            dbname=c.database,
            user=c.user,
            password=self.secrets.password.get_secret_value() if self.secrets.password else None,
            sslmode=c.sslmode,
            connect_timeout=c.connect_timeout_s,
            application_name="AnalystOS",
            options=f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms}",
            autocommit=False,
        )

    def _after_connect(self, conn: Any) -> None:
        conn.read_only = True

    def _type_name(self, type_code: Any) -> str:
        conn = self._conn
        try:
            info = conn.adapters.types.get(type_code)
            if info is not None:
                return str(info.name)
        except AttributeError:
            pass
        return str(type_code)

    def _read_only_status(self) -> bool:
        rows = self._meta("SELECT current_setting('transaction_read_only')")
        return bool(rows) and str(rows[0][0]).lower() == "on"


register_connector(
    "postgres",
    lambda config, secrets: PostgresConnector(config, secrets),
    lambda: json_schema_for(PostgresConfig, PostgresSecrets),
)
