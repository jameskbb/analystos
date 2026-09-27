"""MySQL / MariaDB connector (PyMySQL). Install with the ``mysql`` extra.

Read-only: every session runs ``SET SESSION TRANSACTION READ ONLY`` (writes fail inside
any transaction) and ``MAX_EXECUTION_TIME``; statements are validated with the ``mysql``
dialect (executable comments ``/*! */`` are rejected) and each query ends with a rollback.
Results are streamed with an unbuffered cursor. Parameters use ``%(name)s``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .base import ConnectorConfig, register_connector
from .sql_base import DBAPIConnector, json_schema_for

__all__ = ["MySQLConnector", "MySQLConfig", "MySQLSecrets"]

_FIELD_TYPES = {
    0: "DECIMAL", 1: "TINYINT", 2: "SMALLINT", 3: "INT", 4: "FLOAT", 5: "DOUBLE", 7: "TIMESTAMP", 8: "BIGINT",
    9: "MEDIUMINT", 10: "DATE", 11: "TIME", 12: "DATETIME", 13: "YEAR", 15: "VARCHAR", 16: "BIT", 245: "JSON",
    246: "DECIMAL", 252: "BLOB", 253: "VARCHAR", 254: "CHAR",
}  # fmt: skip


class MySQLConfig(ConnectorConfig):
    host: str
    port: int = Field(default=3306, ge=1, le=65535)
    database: str
    user: str
    ssl: bool = True
    connect_timeout_s: int = Field(default=10, ge=1, le=300)


class MySQLSecrets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr | None = None


class MySQLConnector(DBAPIConnector):
    kind: ClassVar[str] = "mysql"
    dialect: ClassVar[str] = "mysql"
    driver_module: ClassVar[str] = "pymysql"
    extra: ClassVar[str] = "mysql"
    Config: ClassVar[type[ConnectorConfig]] = MySQLConfig
    Secrets: ClassVar[type[BaseModel]] = MySQLSecrets
    VERSION_SQL: ClassVar[str] = "SELECT VERSION()"
    SYSTEM_SCHEMAS: ClassVar[tuple[str, ...]] = ("information_schema", "mysql", "performance_schema", "sys")

    config: MySQLConfig
    secrets: MySQLSecrets

    def _target(self) -> str:
        return f"{self.config.host}:{self.config.port}/{self.config.database}"

    def _open(self, drv: Any) -> Any:
        c = self.config
        kwargs: dict[str, Any] = {
            "host": c.host,
            "port": c.port,
            "user": c.user,
            "password": self.secrets.password.get_secret_value() if self.secrets.password else "",
            "database": c.database,
            "connect_timeout": c.connect_timeout_s,
            "read_timeout": int(c.query_timeout_s) + 5,
            "charset": "utf8mb4",
            "autocommit": False,
            "init_command": (
                "SET SESSION TRANSACTION READ ONLY, SESSION MAX_EXECUTION_TIME = "
                + str(int(c.query_timeout_s * 1000))
            ),
        }
        if c.ssl:
            kwargs["ssl"] = {"ssl": True}
        return drv.connect(**kwargs)

    def _cursor(self) -> Any:
        cursors = getattr(self._driver(), "cursors", None)
        ss = getattr(cursors, "SSCursor", None)
        return self._conn.cursor(ss) if ss is not None else self._conn.cursor()

    def _type_name(self, type_code: Any) -> str:
        return _FIELD_TYPES.get(type_code, str(type_code)) if isinstance(type_code, int) else str(type_code)

    def _read_only_status(self) -> bool:
        rows = self._meta("SELECT @@SESSION.transaction_read_only")
        return bool(rows) and str(rows[0][0]) in ("1", "True", "true")


register_connector(
    "mysql",
    lambda config, secrets: MySQLConnector(config, secrets),
    lambda: json_schema_for(MySQLConfig, MySQLSecrets),
)
