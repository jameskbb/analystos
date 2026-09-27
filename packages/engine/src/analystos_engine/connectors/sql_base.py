"""Shared implementation for DB-API 2.0 connectors (Postgres, MySQL, SQL Server, Snowflake).

Read-only is enforced twice: every statement passes
:func:`~analystos_engine.sqlsafety.ensure_read_only` for the connector's dialect before it
reaches the driver, and each connector configures the session/transaction as read-only
where the database supports it (and always rolls back after a query).
Drivers are imported lazily; secrets never appear in errors or logs.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, SecretStr
from sqlglot import exp

from ..sqlsafety import UnsafeSQLError, ensure_read_only
from ..types import (
    ColumnInfo,
    ConnectionTestResult,
    QueryColumn,
    QueryResult,
    TableInfo,
    TableProfile,
    normalize_value,
)
from .base import ConnectorConfig, ConnectorError, SchemaInfo, redact

__all__ = ["DBAPIConnector", "profile_from_sample", "json_schema_for"]

logger = logging.getLogger(__name__)


def json_schema_for(config_cls: type[BaseModel], secrets_cls: type[BaseModel]) -> dict[str, Any]:
    """Combined JSON schema for a connection form; secret fields are ``writeOnly`` passwords."""
    cfg = config_cls.model_json_schema()
    sec = secrets_cls.model_json_schema()
    props = dict(cfg.get("properties", {}))
    for name, prop in sec.get("properties", {}).items():
        p = dict(prop)
        p["writeOnly"] = True
        p.setdefault("format", "password")
        props[name] = p
    return {
        "title": cfg.get("title", config_cls.__name__),
        "type": "object",
        "properties": props,
        "required": list(cfg.get("required", [])) + list(sec.get("required", [])),
        "x-secret-fields": list(sec.get("properties", {})),
    }


def profile_from_sample(result: QueryResult, name: str, *, sampled: bool) -> TableProfile:
    """Profile a sample of rows fetched from an external source in a private in-memory store."""
    from ..profiling import profile_table
    from ..store import WorkspaceStore

    store = WorkspaceStore.in_memory()
    try:
        import pandas as pd

        df = pd.DataFrame(result.rows, columns=result.column_names)
        for col in df.columns:
            if df[col].dtype == object:
                kinds = {type(v).__name__ for v in df[col] if v is not None}
                if len(kinds) > 1:
                    df[col] = df[col].map(lambda v: None if v is None else str(v))
        store.write_table("sample", df)
        prof = profile_table(store, "sample")
    finally:
        store.close()
    return prof.model_copy(
        update={"table": name, "sampled": sampled, "sample_size": result.row_count if sampled else None}
    )


class DBAPIConnector:
    """Base class; subclasses define the config models, driver and session setup."""

    kind: ClassVar[str] = ""
    dialect: ClassVar[str] = ""
    driver_module: ClassVar[str] = ""
    extra: ClassVar[str] = ""
    Config: ClassVar[type[ConnectorConfig]] = ConnectorConfig
    Secrets: ClassVar[type[BaseModel]] = BaseModel
    VERSION_SQL: ClassVar[str] = "SELECT version()"
    SYSTEM_SCHEMAS: ClassVar[tuple[str, ...]] = ("information_schema",)

    def __init__(self, config: Mapping[str, Any], secrets: Mapping[str, Any]) -> None:
        try:
            self.config = self.Config.model_validate(dict(config))
            self.secrets = self.Secrets.model_validate(dict(secrets))
        except Exception as exc:
            raise ValueError(
                redact(str(exc), [str(v) for v in secrets.values() if isinstance(v, str)])
            ) from None
        self._conn: Any = None
        self._drv: Any = None

    # ------------------------------------------------------------------ plumbing
    def __repr__(self) -> str:  # never show secrets
        return f"<{type(self).__name__} {self.kind} {self._target()}>"

    def _target(self) -> str:
        return ""

    def _secret_values(self) -> list[str]:
        out = []
        for v in self.secrets.__dict__.values():
            if isinstance(v, SecretStr):
                out.append(v.get_secret_value())
            elif isinstance(v, str):
                out.append(v)
        return [s for s in out if s]

    def _err(self, exc: BaseException, action: str, code: str = "connector_error") -> ConnectorError:
        msg = redact(f"{action} failed: {type(exc).__name__}: {exc}", self._secret_values())
        logger.warning("connector %s %s failed: %s", self.kind, action, type(exc).__name__)
        return ConnectorError(msg, kind=self.kind, code=code)

    def _driver(self) -> Any:
        if self._drv is None:
            try:
                self._drv = importlib.import_module(self.driver_module)
            except ImportError as exc:
                raise ConnectorError(
                    f"the {self.kind} connector needs the optional driver '{self.driver_module}'. Install it with "
                    f"`uv sync --extra {self.extra}` (or `pip install 'analystos-engine[{self.extra}]'`).",
                    kind=self.kind,
                    code="driver_missing",
                ) from exc
        return self._drv

    def _open(self, drv: Any) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def _after_connect(self, conn: Any) -> None:
        """Put the session into read-only mode (per database)."""

    def _begin(self, cur: Any) -> None:
        """Statements run before each query in the same transaction."""

    def connect(self) -> None:
        if self._conn is not None:
            return
        drv = self._driver()
        try:
            conn = self._open(drv)
            self._after_connect(conn)
        except ConnectorError:
            raise
        except Exception as exc:
            raise self._err(exc, "connection", code="connection_failed") from None
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # closing a broken connection is best effort
                logger.debug("error closing %s connection", self.kind)
            self._conn = None

    def __enter__(self) -> DBAPIConnector:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _cursor(self) -> Any:
        return self._conn.cursor()

    def _type_name(self, type_code: Any) -> str:
        return getattr(type_code, "__name__", None) or str(type_code)

    def _run(
        self, sql: str, params: Mapping[str, Any] | Sequence[Any] | None, limit: int | None
    ) -> QueryResult:
        self.connect()
        started = time.perf_counter()
        cur = None
        try:
            cur = self._cursor()
            self._begin(cur)
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            desc = cur.description or []
            if limit is None:
                raw = cur.fetchall()
                truncated = False
            else:
                raw = cur.fetchmany(limit + 1)
                truncated = len(raw) > limit
                raw = raw[:limit]
        except ConnectorError:
            raise
        except Exception as exc:
            self._rollback()
            raise self._err(exc, "query", code="query_failed") from None
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:  # cursor close errors are irrelevant after fetching
                    logger.debug("cursor close failed")
        self._rollback()
        cols = [QueryColumn(name=str(d[0]), type=self._type_name(d[1])) for d in desc]
        rows = [[normalize_value(v) for v in r] for r in raw]
        return QueryResult(
            columns=cols,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            sql=sql,
        )

    def _rollback(self) -> None:
        """End the (read-only) transaction; nothing a query did can persist."""
        if self._conn is None:
            return
        try:
            self._conn.rollback()
        except Exception:  # connection may already be closed after an error
            logger.debug("rollback failed")

    # ------------------------------------------------------------------ protocol
    def query(
        self, sql: str, params: Mapping[str, Any] | None = None, limit: int | None = None
    ) -> QueryResult:
        """Run read-only ``sql`` (parameters use the driver's paramstyle)."""
        try:
            safe = ensure_read_only(sql, self.dialect)
        except UnsafeSQLError:
            raise
        return self._run(safe, params, limit if limit is not None else self.config.default_row_limit)

    def _meta(self, sql: str) -> list[list[Any]]:
        return self._run(ensure_read_only(sql, self.dialect), None, None).rows

    def _lit(self, value: str) -> str:
        return exp.Literal.string(value).sql(dialect=self.dialect)

    def _server_version(self) -> str | None:
        rows = self._meta(self.VERSION_SQL)
        return str(rows[0][0]) if rows and rows[0] else None

    def _read_only_status(self) -> bool:
        return False

    def test(self) -> ConnectionTestResult:
        started = time.perf_counter()
        try:
            self.connect()
            version = self._server_version()
            ro = self._read_only_status()
        except ConnectorError as exc:
            return ConnectionTestResult(
                ok=False, message=str(exc), latency_ms=round((time.perf_counter() - started) * 1000, 3)
            )
        return ConnectionTestResult(
            ok=True,
            message="connected"
            + ("; session is read-only" if ro else "; read-only enforced by SQL validation and rollback"),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            server_version=version,
            read_only_enforced=ro,
            details={"target": self._target(), "dialect": self.dialect},
        )

    def discover_schemas(self) -> list[SchemaInfo]:
        rows = self._meta(
            "SELECT table_schema, count(*) FROM information_schema.tables GROUP BY table_schema ORDER BY table_schema"
        )
        return [
            SchemaInfo(name=str(r[0]), table_count=int(r[1]))
            for r in rows
            if str(r[0]).lower() not in self.SYSTEM_SCHEMAS
        ]

    def discover_tables(self, schema: str) -> list[TableInfo]:
        rows = self._meta(
            "SELECT table_name, table_type FROM information_schema.tables "
            f"WHERE table_schema = {self._lit(schema)} ORDER BY table_name"
        )
        return [
            TableInfo(
                name=str(r[0]),
                schema_name=schema,
                kind="view" if "VIEW" in str(r[1]).upper() else "table",
                source=self.kind,
            )
            for r in rows
        ]

    def inspect_columns(self, schema: str, table: str) -> list[ColumnInfo]:
        rows = self._meta(
            "SELECT column_name, data_type, is_nullable, ordinal_position FROM information_schema.columns "
            f"WHERE table_schema = {self._lit(schema)} AND table_name = {self._lit(table)} ORDER BY ordinal_position"
        )
        return [
            ColumnInfo(
                name=str(r[0]),
                type=str(r[1]),
                nullable=str(r[2]).upper() in ("YES", "Y", "TRUE"),
                ordinal=int(r[3]),
            )
            for r in rows
        ]

    def _select_all_sql(self, schema: str, table: str, limit: int) -> str:
        tbl = exp.Table(this=exp.to_identifier(table, quoted=True), db=exp.to_identifier(schema, quoted=True))
        return exp.select("*").from_(tbl).limit(int(limit)).sql(dialect=self.dialect)

    def preview(self, schema: str, table: str, limit: int = 100) -> QueryResult:
        # fetch one extra row so ``truncated`` is accurate
        return self.query(self._select_all_sql(schema, table, limit + 1), limit=limit)

    def profile(self, schema: str, table: str) -> TableProfile:
        n = self.config.profile_sample_rows
        sample = self.query(self._select_all_sql(schema, table, n + 1), limit=n)
        return profile_from_sample(sample, f"{schema}.{table}", sampled=sample.truncated)
