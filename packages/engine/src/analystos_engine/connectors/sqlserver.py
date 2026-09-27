"""SQL Server connector (pyodbc by default, pymssql as an alternative).

Install with the ``sqlserver`` extra (plus Microsoft's ODBC driver) or ``sqlserver-pymssql``.

SQL Server has no read-only session setting, so read-only is enforced by validating every
statement with the ``tsql`` dialect (``EXEC``, ``SELECT INTO``, ``xp_*``, ``OPENROWSET`` ...
are rejected), running each query in a transaction that is always rolled back, and
connecting with ``ApplicationIntent=ReadOnly`` (routes to readable secondaries in
availability groups). Use a login that only has ``db_datareader``. Parameters use ``?``
(pyodbc) or ``%(name)s`` (pymssql).
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .base import ConnectorConfig, register_connector
from .sql_base import DBAPIConnector, json_schema_for

__all__ = ["SQLServerConnector", "SQLServerConfig", "SQLServerSecrets", "odbc_escape"]


class SQLServerConfig(ConnectorConfig):
    host: str
    port: int = Field(default=1433, ge=1, le=65535)
    database: str
    user: str
    driver: Literal["pyodbc", "pymssql"] = "pyodbc"
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    encrypt: bool = True
    trust_server_certificate: bool = False
    connect_timeout_s: int = Field(default=10, ge=1, le=300)


class SQLServerSecrets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr | None = None


def odbc_escape(value: str) -> str:
    """Quote an ODBC connection-string value: ``{...}`` with ``}`` doubled."""
    return "{" + value.replace("}", "}}") + "}"


class SQLServerConnector(DBAPIConnector):
    kind: ClassVar[str] = "sqlserver"
    dialect: ClassVar[str] = "tsql"
    driver_module: ClassVar[str] = "pyodbc"
    extra: ClassVar[str] = "sqlserver"
    Config: ClassVar[type[ConnectorConfig]] = SQLServerConfig
    Secrets: ClassVar[type[BaseModel]] = SQLServerSecrets
    VERSION_SQL: ClassVar[str] = "SELECT @@VERSION"
    SYSTEM_SCHEMAS: ClassVar[tuple[str, ...]] = (
        "information_schema",
        "sys",
        "guest",
        "db_owner",
        "db_datareader",
    )

    config: SQLServerConfig
    secrets: SQLServerSecrets

    def __init__(self, config: Any, secrets: Any) -> None:
        super().__init__(config, secrets)
        if self.config.driver == "pymssql":
            self.driver_module = "pymssql"  # type: ignore[misc]
            self.extra = "sqlserver-pymssql"  # type: ignore[misc]

    def _target(self) -> str:
        return f"{self.config.host}:{self.config.port}/{self.config.database}"

    def connection_string(self) -> str:
        c = self.config
        pwd = self.secrets.password.get_secret_value() if self.secrets.password else ""
        parts = [
            f"DRIVER={odbc_escape(c.odbc_driver)}",
            f"SERVER={odbc_escape(f'{c.host},{c.port}')}",
            f"DATABASE={odbc_escape(c.database)}",
            f"UID={odbc_escape(c.user)}",
            f"PWD={odbc_escape(pwd)}",
            f"Encrypt={'yes' if c.encrypt else 'no'}",
            f"TrustServerCertificate={'yes' if c.trust_server_certificate else 'no'}",
            "ApplicationIntent=ReadOnly",
            "APP=AnalystOS",
        ]
        return ";".join(parts)

    def _open(self, drv: Any) -> Any:
        c = self.config
        if self.config.driver == "pymssql":
            return drv.connect(
                server=c.host,
                port=str(c.port),
                user=c.user,
                password=self.secrets.password.get_secret_value() if self.secrets.password else "",
                database=c.database,
                login_timeout=c.connect_timeout_s,
                timeout=int(c.query_timeout_s),
                appname="AnalystOS",
                autocommit=False,
            )
        conn = drv.connect(self.connection_string(), autocommit=False, timeout=c.connect_timeout_s)
        conn.timeout = int(c.query_timeout_s)
        return conn

    def _begin(self, cur: Any) -> None:
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")

    def _type_name(self, type_code: Any) -> str:
        return getattr(type_code, "__name__", None) or str(type_code)

    def _read_only_status(self) -> bool:
        return False


register_connector(
    "sqlserver",
    lambda config, secrets: SQLServerConnector(config, secrets),
    lambda: json_schema_for(SQLServerConfig, SQLServerSecrets),
)
