"""Connector tests against fake drivers.

The fake DB-API driver executes statements on an in-memory DuckDB after transpiling them
from the connector's dialect, so metadata discovery, previews and profiling run real SQL.
Session/transaction setup statements are recorded instead of executed.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import types

import duckdb
import pytest
import sqlglot
from analystos_engine.connectors import (
    ConnectorError,
    available_kinds,
    connector_config_schema,
    get_connector,
)
from analystos_engine.connectors.duckdb_store import DuckDBConnector
from analystos_engine.connectors.sqlserver import odbc_escape
from analystos_engine.sqlsafety import UnsafeSQLError
from analystos_engine.store import WorkspaceStore

PASSWORD = "s3cr3t-P@ss}word"


def make_backend() -> duckdb.DuckDBPyConnection:
    db = duckdb.connect(":memory:")
    db.execute("CREATE SCHEMA sales")
    db.execute(
        "CREATE TABLE sales.orders AS SELECT range AS order_id, range % 3 AS customer_id, range * 10.5 AS amount, DATE '2026-08-01' + CAST(range AS INTEGER) AS order_date FROM range(25)"
    )
    db.execute("CREATE VIEW sales.big_orders AS SELECT * FROM sales.orders WHERE amount > 100")
    return db


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.description = None
        self._rows: list[tuple] = []

    def execute(self, sql, params=None):
        self.conn.log.append((sql, params))
        upper = sql.upper()
        if upper.startswith("SET ") or "TRANSACTION" in upper and not upper.startswith("SELECT"):
            self.conn.session_statements.append(sql)
            self.description, self._rows = None, []
            return
        if self.conn.fail_with is not None:
            raise self.conn.fail_with
        if "TRANSACTION_READ_ONLY" in upper:
            self.description, self._rows = (
                [("ro", "text")],
                [("on" if self.conn.dialect == "postgres" else "1",)],
            )
            return
        # emulate server-specific metadata the DuckDB backend lacks (Snowflake's tables.row_count)
        sql = sql.replace("table_type, row_count FROM", "table_type, NULL AS row_count FROM")
        sql = sql.replace("@@VERSION", "version()").replace("CURRENT_VERSION()", "version()")
        duck_sql = sqlglot.transpile(sql, read=self.conn.dialect, write="duckdb")[0]
        rel = self.conn.backend.execute(duck_sql)
        self.description = [(d[0], d[1]) for d in rel.description]
        self._rows = rel.fetchall()

    def fetchmany(self, n):
        out, self._rows = self._rows[:n], self._rows[n:]
        return out

    def fetchall(self):
        out, self._rows = self._rows, []
        return out

    def close(self):
        self.conn.cursors_closed += 1


class FakeConn:
    def __init__(self, dialect: str, backend, kwargs) -> None:
        self.dialect = dialect
        self.backend = backend
        self.kwargs = kwargs
        self.log: list = []
        self.session_statements: list[str] = []
        self.rollbacks = 0
        self.cursors_closed = 0
        self.closed = False
        self.read_only = False
        self.fail_with: Exception | None = None
        self.timeout = None

    def cursor(self, *args):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def fake_driver(monkeypatch, module_name: str, dialect: str, *, connect_error: Exception | None = None):
    backend = make_backend()
    mod = types.ModuleType(module_name)
    mod.connections = []  # type: ignore[attr-defined]

    def connect(*args, **kwargs):
        if connect_error is not None:
            raise connect_error
        conn = FakeConn(dialect, backend, {"args": args, **kwargs})
        mod.connections.append(conn)  # type: ignore[attr-defined]
        return conn

    mod.connect = connect  # type: ignore[attr-defined]
    cursors = types.ModuleType(f"{module_name}.cursors")
    cursors.SSCursor = object  # type: ignore[attr-defined]
    mod.cursors = cursors  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, mod)
    return mod


CASES = {
    "postgres": ("psycopg", "postgres", {"host": "db.internal", "database": "shop", "user": "analyst"}),
    "mysql": ("pymysql", "mysql", {"host": "db.internal", "database": "shop", "user": "analyst"}),
    "sqlserver": ("pyodbc", "tsql", {"host": "db.internal", "database": "shop", "user": "analyst"}),
    "snowflake": (
        "snowflake.connector",
        "snowflake",
        {"account": "acme-xy", "user": "analyst", "database": "memory", "warehouse": "WH", "role": "READER"},
    ),
}


@pytest.fixture(params=list(CASES))
def sql_connector(request, monkeypatch):
    kind = request.param
    module, dialect, cfg = CASES[kind]
    mod = fake_driver(monkeypatch, module, dialect)
    conn = get_connector(kind, cfg, {"password": PASSWORD})
    yield kind, conn, mod
    conn.close()


def test_query_discovery_preview_profile(sql_connector):
    kind, conn, mod = sql_connector
    res = conn.query("SELECT order_id, amount FROM sales.orders ORDER BY order_id", limit=10)
    assert res.row_count == 10 and res.truncated and res.column_names == ["order_id", "amount"]
    fake = mod.connections[0]
    assert fake.rollbacks >= 1  # every query ends its transaction with a rollback
    schemas = {s.name: s for s in conn.discover_schemas()}
    assert "sales" in schemas and "information_schema" not in schemas
    tables = {t.name: t for t in conn.discover_tables("sales")}
    assert tables["orders"].kind == "table" and tables["big_orders"].kind == "view"
    cols = conn.inspect_columns("sales", "orders")
    assert [c.name for c in cols] == ["order_id", "customer_id", "amount", "order_date"]
    prev = conn.preview("sales", "orders", limit=5)
    assert prev.row_count == 5 and prev.truncated
    prof = conn.profile("sales", "orders")
    assert prof.table == "sales.orders" and prof.row_count == 25 and not prof.sampled
    assert {c.name for c in prof.columns} == {"order_id", "customer_id", "amount", "order_date"}


def test_unsafe_sql_never_reaches_driver(sql_connector):
    kind, conn, mod = sql_connector
    conn.connect()
    fake = mod.connections[0]
    before = len(fake.log)
    for bad in (
        "DELETE FROM sales.orders",
        "SELECT 1; DROP TABLE sales.orders",
        "UPDATE sales.orders SET amount = 0",
    ):
        with pytest.raises(UnsafeSQLError):
            conn.query(bad)
    assert len(fake.log) == before
    assert fake.backend.execute("SELECT count(*) FROM sales.orders").fetchone()[0] == 25


def test_read_only_session_settings(sql_connector):
    kind, conn, mod = sql_connector
    result = conn.test()
    assert result.ok, result.message
    fake = mod.connections[0]
    if kind == "postgres":
        assert "default_transaction_read_only=on" in fake.kwargs["options"]
        assert "statement_timeout=" in fake.kwargs["options"]
        assert fake.read_only is True and fake.kwargs["autocommit"] is False
        assert result.read_only_enforced
    elif kind == "mysql":
        assert "TRANSACTION READ ONLY" in fake.kwargs["init_command"]
        assert "MAX_EXECUTION_TIME" in fake.kwargs["init_command"]
        assert result.read_only_enforced
    elif kind == "sqlserver":
        cs = fake.kwargs["args"][0]
        assert "ApplicationIntent=ReadOnly" in cs
        assert "PWD={s3cr3t-P@ss}}word}" in cs  # braces escaped
        assert fake.kwargs["autocommit"] is False
        assert any("ISOLATION LEVEL" in s for s in fake.session_statements)
        assert not result.read_only_enforced  # honest: enforced by validation + rollback only
    else:
        params = fake.kwargs["session_parameters"]
        assert params["AUTOCOMMIT"] is False and params["STATEMENT_TIMEOUT_IN_SECONDS"] == 60
        assert fake.kwargs["role"] == "READER" and fake.kwargs["autocommit"] is False
    assert result.server_version
    assert PASSWORD not in repr(conn)


def test_query_error_is_redacted(sql_connector):
    kind, conn, mod = sql_connector
    conn.connect()
    mod.connections[0].fail_with = RuntimeError(f"server said password {PASSWORD} is wrong")
    with pytest.raises(ConnectorError) as err:
        conn.query("SELECT 1")
    assert PASSWORD not in str(err.value) and "***" in str(err.value)
    assert err.value.code == "query_failed"


@pytest.mark.parametrize("kind", list(CASES))
def test_connect_error_is_redacted(monkeypatch, kind):
    module, dialect, cfg = CASES[kind]
    fake_driver(
        monkeypatch, module, dialect, connect_error=RuntimeError(f"login failed; password={PASSWORD}")
    )
    conn = get_connector(kind, cfg, {"password": PASSWORD})
    with pytest.raises(ConnectorError) as err:
        conn.connect()
    assert PASSWORD not in str(err.value)
    t = conn.test()
    assert not t.ok and PASSWORD not in t.message


@pytest.mark.parametrize("kind", [*CASES, "bigquery"])
def test_missing_driver(monkeypatch, kind):
    module = {**{k: v[0] for k, v in CASES.items()}, "bigquery": "google.cloud.bigquery"}[kind]
    monkeypatch.setitem(sys.modules, module, None)
    cfg = CASES[kind][2] if kind in CASES else {"project": "p"}
    conn = get_connector(kind, cfg, {})
    with pytest.raises(ConnectorError) as err:
        conn.connect()
    assert err.value.code == "driver_missing" and "--extra" in str(err.value)


def test_invalid_config_does_not_leak_secrets():
    with pytest.raises(ValueError) as err:
        get_connector(
            "postgres", {"host": "h", "database": "d", "user": "u", "port": "nope"}, {"password": PASSWORD}
        )
    assert PASSWORD not in str(err.value)
    with pytest.raises(ConnectorError):
        get_connector("oracle", {}, {})


def test_registry_and_schemas():
    assert available_kinds() == sorted(["bigquery", "duckdb", "mysql", "postgres", "snowflake", "sqlserver"])
    for kind in available_kinds():
        schema = connector_config_schema(kind)
        assert schema["type"] == "object" and "properties" in schema
    pg = connector_config_schema("postgres")
    assert pg["properties"]["password"]["writeOnly"] is True and pg["x-secret-fields"] == ["password"]
    assert "host" in pg["required"]
    sf = connector_config_schema("snowflake")
    assert "schema" in sf["properties"] and set(sf["x-secret-fields"]) == {
        "password",
        "private_key",
        "private_key_passphrase",
    }
    with pytest.raises(ConnectorError):
        connector_config_schema("oracle")


def test_odbc_escape():
    assert odbc_escape("a}b;c") == "{a}}b;c}"


def test_pymssql_variant(monkeypatch):
    mod = fake_driver(monkeypatch, "pymssql", "tsql")
    conn = get_connector("sqlserver", {**CASES["sqlserver"][2], "driver": "pymssql"}, {"password": PASSWORD})
    assert conn.query("SELECT TOP 3 order_id FROM sales.orders").row_count == 3
    assert (
        mod.connections[0].kwargs["password"] == PASSWORD and mod.connections[0].kwargs["autocommit"] is False
    )


def test_tsql_specific_attacks_rejected(monkeypatch):
    fake_driver(monkeypatch, "pyodbc", "tsql")
    conn = get_connector("sqlserver", CASES["sqlserver"][2], {"password": PASSWORD})
    for bad in (
        "EXEC xp_cmdshell 'dir'",
        "SELECT * INTO #t FROM sales.orders",
        "SELECT * FROM OPENROWSET('x','y','z')",
    ):
        with pytest.raises(UnsafeSQLError):
            conn.query(bad)


# ----------------------------------------------------------------------------- BigQuery


class _Field:
    def __init__(self, name, field_type, mode="NULLABLE"):
        self.name, self.field_type, self.mode, self.description = name, field_type, mode, None


class _Row:
    def __init__(self, values):
        self._v = values

    def values(self):
        return list(self._v)


class _RowIter(list):
    schema: list


def fake_bigquery(monkeypatch):
    backend = make_backend()
    calls: dict[str, list] = {"queries": [], "configs": []}

    class QueryJobConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ScalarQueryParameter:
        def __init__(self, name, typ, value):
            self.name, self.type_, self.value = name, typ, value

    def _duck_type(t: str) -> str:
        t = str(t).upper()
        return {"BIGINT": "INT64", "DOUBLE": "FLOAT64", "DATE": "DATE", "VARCHAR": "STRING"}.get(t, t)

    class Job:
        def __init__(self, sql, cfg):
            self.sql, self.cfg = sql, cfg
            self.statement_type = "SELECT"

        def result(self, max_results=None, timeout=None):
            duck = sqlglot.transpile(self.sql, read="bigquery", write="duckdb")[0]
            params = getattr(self.cfg, "query_parameters", None) or []
            for p in params:
                duck = duck.replace(f"@{p.name}", f"${p.name}")
            rel = (
                backend.execute(duck, {p.name: p.value for p in params}) if params else backend.execute(duck)
            )
            it = _RowIter(_Row(r) for r in rel.fetchall()[:max_results])
            it.schema = [_Field(d[0], _duck_type(d[1])) for d in rel.description]
            return it

    class Table:
        def __init__(self, ref):
            _, ds, name = ref.split(".")
            self.ref, self.dataset, self.table_id = ref, ds, name
            self.table_type = "VIEW" if name.startswith("big") else "TABLE"
            rel = backend.execute(f"SELECT * FROM {ds}.{name} LIMIT 0")
            self.schema = [_Field(d[0], _duck_type(d[1])) for d in rel.description]

    class Client:
        def __init__(self, project, credentials=None, location=None):
            calls["client"] = {"project": project, "credentials": credentials, "location": location}

        def query(self, sql, job_config=None):
            calls["queries"].append(sql)
            calls["configs"].append(job_config)
            return Job(sql, job_config)

        def list_datasets(self, max_results=None):
            return [types.SimpleNamespace(dataset_id="sales")]

        def list_tables(self, ref):
            ds = ref.split(".")[1]
            names = [
                r[0]
                for r in backend.execute(
                    f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{ds}' ORDER BY 1"
                ).fetchall()
            ]
            return [Table(f"p.{ds}.{n}") for n in names]

        def get_table(self, ref):
            return Table(ref)

        def list_rows(self, table, max_results=None):
            rows = backend.execute(
                f"SELECT * FROM {table.dataset}.{table.table_id} LIMIT {int(max_results)}"
            ).fetchall()
            return [_Row(r) for r in rows]

        def close(self):
            calls["closed"] = True

    bq = types.ModuleType("google.cloud.bigquery")
    bq.Client, bq.QueryJobConfig, bq.ScalarQueryParameter = Client, QueryJobConfig, ScalarQueryParameter
    sa = types.ModuleType("google.oauth2.service_account")

    class Credentials:
        @staticmethod
        def from_service_account_info(info):
            return ("creds", info["client_email"])

    sa.Credentials = Credentials
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq)
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa)
    return calls


KEY = json.dumps(
    {
        "type": "service_account",
        "client_email": "svc@p.iam",
        "private_key": "-----BEGIN KEY-----abc-----END KEY-----",
    }
)


def test_bigquery(monkeypatch):
    calls = fake_bigquery(monkeypatch)
    conn = get_connector(
        "bigquery", {"project": "p", "maximum_bytes_billed": 1000}, {"credentials_json": KEY}
    )
    t = conn.test()
    assert t.ok and t.read_only_enforced
    assert calls["client"]["credentials"] == ("creds", "svc@p.iam")
    res = conn.query(
        "SELECT order_id FROM sales.orders WHERE order_id < @n ORDER BY order_id", {"n": 5}, limit=3
    )
    assert res.row_count == 3 and res.truncated
    dry, real = calls["configs"][-2], calls["configs"][-1]
    assert dry.dry_run is True and dry.maximum_bytes_billed == 1000 and not hasattr(real, "dry_run")
    assert real.query_parameters[0].type_ == "INT64"
    assert [s.name for s in conn.discover_schemas()] == ["sales"]
    tables = {x.name: x.kind for x in conn.discover_tables("sales")}
    assert tables == {"big_orders": "view", "orders": "table"}
    assert [c.name for c in conn.inspect_columns("sales", "orders")][:2] == ["order_id", "customer_id"]
    prev = conn.preview("sales", "orders", limit=4)
    assert prev.row_count == 4 and prev.truncated and "table data API" in prev.sql
    prof = conn.profile("sales", "orders")
    assert prof.row_count == 25
    with pytest.raises(UnsafeSQLError):
        conn.query("DELETE FROM sales.orders WHERE true")
    with pytest.raises(UnsafeSQLError):
        conn.query("SELECT 1; SELECT 2")
    conn.close()
    assert calls["closed"]


def test_bigquery_dry_run_rejects_non_select_and_redacts(monkeypatch):
    calls = fake_bigquery(monkeypatch)
    bq = sys.modules["google.cloud.bigquery"]
    original = bq.Client.query

    def query(self, sql, job_config=None):
        job = original(self, sql, job_config)
        job.statement_type = "SCRIPT"
        return job

    monkeypatch.setattr(bq.Client, "query", query)
    conn = get_connector("bigquery", {"project": "p"}, {"credentials_json": KEY})
    with pytest.raises(ConnectorError) as err:
        conn.query("SELECT 1")
    assert "only SELECT" in str(err.value)
    assert len(calls["queries"]) == 1  # the real job never ran

    def boom(self, sql, job_config=None):
        raise RuntimeError("bad key -----BEGIN KEY-----abc-----END KEY-----")

    monkeypatch.setattr(bq.Client, "query", boom)
    with pytest.raises(ConnectorError) as err2:
        conn.query("SELECT 1")
    assert "BEGIN KEY" not in str(err2.value)


# ------------------------------------------------------------------------------- DuckDB


def test_duckdb_connector(tmp_path, star_frames_store):
    conn = get_connector("duckdb", {"workspace_dir": str(star_frames_store)}, {})
    assert conn.test().ok
    assert [s.name for s in conn.discover_schemas()] == ["main"]
    assert {t.name for t in conn.discover_tables("main")} >= {"orders", "customers"}
    assert [c.name for c in conn.inspect_columns("main", "orders")][0] == "order_id"
    assert conn.preview("main", "orders", 2).row_count == 2
    assert conn.query("SELECT count(*) AS n FROM orders").scalar() == 5
    with pytest.raises(UnsafeSQLError):
        conn.query("DROP TABLE orders")
    prof = conn.profile("main", "orders")
    assert prof.row_count == 5
    with pytest.raises(ConnectorError):
        conn.discover_tables("other")
    with pytest.raises(ConnectorError):
        conn.profile("main", "missing")
    with pytest.raises(ConnectorError):
        conn.query("SELECT nope FROM orders")
    conn.close()


def test_duckdb_connector_from_store(star_store):
    conn = DuckDBConnector({}, store=star_store)
    assert conn.query("SELECT sum(net_amount) FROM order_lines").scalar() == 710.0
    conn.close()
    assert star_store.row_count("orders") == 5  # borrowed store stays open


@pytest.fixture()
def star_frames_store(tmp_path):
    from conftest import star_frames

    ws = tmp_path / "ws"
    with WorkspaceStore(ws) as s:
        for name, df in star_frames().items():
            s.write_table(name, df)
    return ws


def test_date_values_roundtrip(sql_connector):
    kind, conn, _ = sql_connector
    res = conn.query("SELECT order_date FROM sales.orders ORDER BY order_date", limit=1)
    assert res.rows[0][0] == dt.date(2026, 8, 1)
