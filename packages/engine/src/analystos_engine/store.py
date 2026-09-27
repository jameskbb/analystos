"""Workspace analytical store: one DuckDB file per workspace.

Security model
--------------
* The DuckDB database is opened with ``enable_external_access = false`` and
  ``lock_configuration = true``: DuckDB itself refuses to read or write files,
  attach databases, install/load extensions or reach the network, even if a query
  slipped past :mod:`analystos_engine.sqlsafety`. Ingestion therefore reads files in
  Python (pyarrow / pandas / openpyxl) and hands DuckDB in-memory Arrow tables.
* Every query from outside the engine goes through :meth:`WorkspaceStore.execute_read`,
  which enforces :func:`~analystos_engine.sqlsafety.ensure_read_only`, a row cap and a
  timeout (interrupting the query).

Within one process a database file is opened once and shared (DuckDB does not allow
two differently configured handles on the same file); each operation uses its own
cursor, so concurrent readers in threads are safe.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import re
import threading
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from .sqlsafety import UnsafeSQLError, ensure_read_only
from .types import ColumnInfo, DatasetVersion, QueryColumn, QueryResult, TableInfo, normalize_value

__all__ = [
    "WorkspaceStore",
    "StoreError",
    "UnsafeSQLError",
    "QueryTimeoutError",
    "quote_ident",
    "validate_table_name",
    "DEFAULT_ROW_LIMIT",
]

DEFAULT_ROW_LIMIT = 10_000
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_RESERVED_PREFIX = "_aos_"


class StoreError(RuntimeError):
    """A store operation failed (bad table name, query error, ...)."""


class QueryTimeoutError(StoreError):
    """The query was interrupted because it exceeded its timeout."""


def quote_ident(name: str) -> str:
    """Quote a SQL identifier for DuckDB."""
    return '"' + name.replace('"', '""') + '"'


def validate_table_name(name: str) -> str:
    if not _TABLE_NAME_RE.match(name):
        raise StoreError(
            f"invalid table name {name!r}: use letters, digits and underscores, starting with a letter or underscore (max 63)"
        )
    if name.lower().startswith(_RESERVED_PREFIX):
        raise StoreError(f"table names starting with {_RESERVED_PREFIX!r} are reserved")
    return name


class _SharedDatabase:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con
        self.write_lock = threading.RLock()
        self.refs = 0
        self.token = uuid.uuid4().hex
        self.generation = 0


_DATABASES: dict[str, _SharedDatabase] = {}
_DATABASES_LOCK = threading.Lock()


class WorkspaceStore:
    """DuckDB-backed analytical store for one workspace.

    ``WorkspaceStore(workspace_dir)`` uses ``workspace_dir/warehouse.duckdb``.
    ``WorkspaceStore.in_memory()`` gives a private in-memory store (used for
    profiling samples from external connectors and in tests).
    """

    DB_FILENAME = "warehouse.duckdb"

    def __init__(
        self,
        workspace_dir: str | Path | None,
        *,
        memory_limit: str = "2GB",
        threads: int | None = None,
        default_timeout_s: float = 30.0,
    ) -> None:
        self.default_timeout_s = default_timeout_s
        self._memory_limit = memory_limit
        self._threads = threads
        if workspace_dir is None:
            self.workspace_dir: Path | None = None
            self.path: Path | None = None
            self._key = f":memory:{id(self)}"
        else:
            self.workspace_dir = Path(workspace_dir).resolve()
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            self.path = self.workspace_dir / self.DB_FILENAME
            self._key = str(self.path)
        self._closed = False
        self._db = self._acquire()

    @classmethod
    def in_memory(cls, **kwargs: Any) -> WorkspaceStore:
        return cls(None, **kwargs)

    @property
    def data_version(self) -> tuple[str, int]:
        """Changes whenever a table is written, dropped or renamed through this database; for
        caching derived facts (key uniqueness) without re-reading the data."""
        return self._db.token, self._db.generation

    # ----------------------------------------------------------------- plumbing
    def _config(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "enable_external_access": False,
            "memory_limit": self._memory_limit,
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        }
        if self._threads:
            cfg["threads"] = self._threads
        return cfg

    def _acquire(self) -> _SharedDatabase:
        with _DATABASES_LOCK:
            shared = _DATABASES.get(self._key)
            if shared is None:
                target = ":memory:" if self.path is None else str(self.path)
                try:
                    con = duckdb.connect(target, config=self._config())
                except duckdb.Error as exc:
                    raise StoreError(f"cannot open workspace store: {exc}") from exc
                con.execute("SET lock_configuration = true")
                shared = _SharedDatabase(con)
                _DATABASES[self._key] = shared
            shared.refs += 1
            return shared

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with _DATABASES_LOCK:
            shared = _DATABASES.get(self._key)
            if shared is None:
                return
            shared.refs -= 1
            if shared.refs <= 0:
                shared.con.close()
                del _DATABASES[self._key]

    def __enter__(self) -> WorkspaceStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise StoreError("store is closed")

    @contextmanager
    def _cursor(self) -> Iterator[duckdb.DuckDBPyConnection]:
        self._check_open()
        cur = self._db.con.cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ------------------------------------------------------------------- reads
    def execute_read(
        self,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] | None = None,
        limit: int | None = DEFAULT_ROW_LIMIT,
        timeout_s: float | None = None,
    ) -> QueryResult:
        """Run a read-only query and return at most ``limit`` rows.

        Raises :class:`~analystos_engine.sqlsafety.UnsafeSQLError` for non-read-only
        SQL, :class:`QueryTimeoutError` on timeout and :class:`StoreError` for any
        database error.
        """
        safe_sql = ensure_read_only(sql, "duckdb")
        return self._run(safe_sql, params, limit, timeout_s, display_sql=safe_sql)

    def _run(
        self,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] | None,
        limit: int | None,
        timeout_s: float | None,
        *,
        display_sql: str,
    ) -> QueryResult:
        timeout = self.default_timeout_s if timeout_s is None else timeout_s
        if limit is not None and limit < 0:
            raise StoreError("limit must be non-negative")
        started = time.perf_counter()
        timed_out = threading.Event()
        with self._cursor() as cur:

            def _interrupt() -> None:
                timed_out.set()
                with contextlib.suppress(duckdb.Error):
                    cur.interrupt()

            timer = threading.Timer(timeout, _interrupt) if timeout and timeout > 0 else None
            if timer:
                timer.daemon = True
                timer.start()
            try:
                if params:
                    cur.execute(sql, params)
                else:
                    cur.execute(sql)
                description = cur.description or []
                if limit is None:
                    raw_rows = cur.fetchall()
                    truncated = False
                else:
                    raw_rows = cur.fetchmany(limit + 1)
                    truncated = len(raw_rows) > limit
                    raw_rows = raw_rows[:limit]
            except duckdb.InterruptException as exc:
                raise QueryTimeoutError(f"query exceeded the {timeout:g}s timeout and was cancelled") from exc
            except duckdb.Error as exc:
                if timed_out.is_set():
                    raise QueryTimeoutError(
                        f"query exceeded the {timeout:g}s timeout and was cancelled"
                    ) from exc
                raise StoreError(_clean_duckdb_error(exc)) from exc
            finally:
                if timer:
                    timer.cancel()
        elapsed = (time.perf_counter() - started) * 1000
        columns = [QueryColumn(name=d[0], type=str(d[1])) for d in description]
        rows = [[normalize_value(v) for v in r] for r in raw_rows]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=round(elapsed, 3),
            sql=display_sql,
        )

    def scalar(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> Any:
        return self.execute_read(sql, params, limit=1).scalar()

    # ---------------------------------------------------------------- metadata
    def list_tables(self, *, include_row_counts: bool = True) -> list[TableInfo]:
        with self._cursor() as cur:
            tables = cur.execute(
                """
                SELECT table_name, table_type FROM information_schema.tables
                WHERE table_schema = 'main' AND table_catalog = current_database()
                ORDER BY table_name
                """
            ).fetchall()
        out = []
        for name, ttype in tables:
            if name.lower().startswith(_RESERVED_PREFIX):
                continue
            out.append(
                self.describe(
                    name, include_row_count=include_row_counts, _kind="view" if "VIEW" in ttype else "table"
                )
            )
        return out

    def has_table(self, table: str) -> bool:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
                [table],
            ).fetchone()
        return bool(row and row[0])

    def describe(self, table: str, *, include_row_count: bool = True, _kind: str | None = None) -> TableInfo:
        if not self.has_table(table):
            raise StoreError(f"table {table!r} does not exist")
        with self._cursor() as cur:
            cols = cur.execute(
                """
                SELECT column_name, data_type, is_nullable, ordinal_position
                FROM information_schema.columns
                WHERE table_schema = 'main' AND table_name = ?
                ORDER BY ordinal_position
                """,
                [table],
            ).fetchall()
            kind = _kind
            if kind is None:
                t = cur.execute(
                    "SELECT table_type FROM information_schema.tables WHERE table_schema='main' AND table_name = ?",
                    [table],
                ).fetchone()
                kind = "view" if t and "VIEW" in t[0] else "table"
            row_count = None
            if include_row_count:
                row_count = int(cur.execute(f"SELECT count(*) FROM {quote_ident(table)}").fetchone()[0])  # type: ignore[index]
        return TableInfo(
            name=table,
            schema_name="main",
            kind="view" if kind == "view" else "table",
            columns=[
                ColumnInfo(name=c[0], type=c[1], nullable=c[2] == "YES", ordinal=int(c[3])) for c in cols
            ],
            row_count=row_count,
            source="duckdb",
        )

    def columns(self, table: str) -> list[ColumnInfo]:
        return self.describe(table, include_row_count=False).columns

    def preview(self, table: str, limit: int = 100) -> QueryResult:
        if not self.has_table(table):
            raise StoreError(f"table {table!r} does not exist")
        return self.execute_read(f"SELECT * FROM {quote_ident(table)}", limit=limit)

    def row_count(self, table: str) -> int:
        if not self.has_table(table):
            raise StoreError(f"table {table!r} does not exist")
        return int(self.scalar(f"SELECT count(*) FROM {quote_ident(table)}"))

    def table_version(self, table: str) -> DatasetVersion:
        """Content hash (schema + order-independent row hash) and row count of ``table``."""
        info = self.describe(table, include_row_count=False)
        q = quote_ident(table)
        res = self.execute_read(
            f"SELECT count(*) AS n, coalesce(sum(hash(t)::HUGEINT), 0)::VARCHAR AS h, "
            f"coalesce(bit_xor(hash(t)), 0)::VARCHAR AS x FROM {q} AS t",
            limit=1,
        )
        n, h, x = res.rows[0]
        schema_sig = ";".join(f"{c.name}:{c.type}" for c in info.columns)
        digest = hashlib.sha256(f"{schema_sig}|{n}|{h}|{x}".encode()).hexdigest()
        return DatasetVersion(
            table=table, content_hash=digest, row_count=int(n), captured_at=dt.datetime.now(dt.UTC)
        )

    # ------------------------------------------------------------------ writes
    def write_table(self, table: str, data: Any, *, if_exists: str = "fail") -> TableInfo:
        """Create (or replace/append to) ``table`` from a pyarrow Table or pandas/polars DataFrame.

        This is the only write path into the store. It never executes user SQL.
        """
        validate_table_name(table)
        if if_exists not in ("fail", "replace", "append"):
            raise StoreError("if_exists must be one of fail, replace, append")
        arrow = _to_arrow(data)
        view = f"_aos_ingest_{abs(hash((table, time.time_ns()))) % 10**12}"
        with self._db.write_lock, self._cursor() as cur:
            exists = cur.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_name = ?",
                [table],
            ).fetchone()[0]  # type: ignore[index]
            if exists and if_exists == "fail":
                raise StoreError(f"table {table!r} already exists")
            cur.register(view, arrow)
            try:
                cur.execute("BEGIN TRANSACTION")
                if exists and if_exists == "append":
                    cur.execute(f"INSERT INTO {quote_ident(table)} BY NAME SELECT * FROM {quote_ident(view)}")
                else:
                    cur.execute(
                        f"CREATE OR REPLACE TABLE {quote_ident(table)} AS SELECT * FROM {quote_ident(view)}"
                    )
                cur.execute("COMMIT")
                self._db.generation += 1
            except duckdb.Error as exc:
                cur.execute("ROLLBACK")
                raise StoreError(_clean_duckdb_error(exc)) from exc
            finally:
                cur.unregister(view)
        return self.describe(table)

    def drop_table(self, table: str) -> None:
        validate_table_name(table)
        with self._db.write_lock, self._cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {quote_ident(table)}")
            self._db.generation += 1

    def rename_table(self, old: str, new: str) -> TableInfo:
        validate_table_name(new)
        if not self.has_table(old):
            raise StoreError(f"table {old!r} does not exist")
        if self.has_table(new):
            raise StoreError(f"table {new!r} already exists")
        with self._db.write_lock, self._cursor() as cur:
            cur.execute(f"ALTER TABLE {quote_ident(old)} RENAME TO {quote_ident(new)}")
            self._db.generation += 1
        return self.describe(new)

    def export_arrow(self, sql: str, limit: int | None = None) -> Any:
        """Run a read-only query and return a pyarrow Table (for the sandbox and exports)."""
        safe = ensure_read_only(sql, "duckdb")
        if limit is not None:
            safe = f"SELECT * FROM ({safe}) AS _q LIMIT {int(limit)}"
        with self._cursor() as cur:
            try:
                rel = cur.execute(safe)
                fetch = getattr(rel, "to_arrow_table", None) or rel.fetch_arrow_table
                return fetch()
            except duckdb.Error as exc:
                raise StoreError(_clean_duckdb_error(exc)) from exc


def _to_arrow(data: Any) -> Any:
    import pyarrow as pa

    if isinstance(data, pa.Table):
        return data
    mod = type(data).__module__
    if mod.startswith("polars"):
        return data.to_arrow()
    if mod.startswith("pandas"):
        return pa.Table.from_pandas(data, preserve_index=False)
    if isinstance(data, list):
        return pa.Table.from_pylist(data)
    if isinstance(data, dict):
        return pa.Table.from_pydict(data)
    raise StoreError(f"cannot write data of type {type(data).__name__}")


def _clean_duckdb_error(exc: Exception) -> str:
    msg = str(exc).strip()
    return msg.split("\n\nLINE")[0] if msg else type(exc).__name__
