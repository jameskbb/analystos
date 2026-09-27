from __future__ import annotations

import pytest
from analystos_engine.sqlsafety import UnsafeSQLError, ensure_read_only, is_read_only, referenced_tables

SAFE = [
    "SELECT 1",
    "select * from orders",
    "SELECT 1;",
    "  SELECT a FROM t WHERE b = 'DROP TABLE x; --'  ",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT a FROM t UNION ALL SELECT b FROM u",
    "SELECT a FROM t EXCEPT SELECT b FROM u",
    "VALUES (1, 2), (3, 4)",
    "FROM orders",
    "SELECT region, sum(amount) FROM orders GROUP BY ALL ORDER BY 2 DESC LIMIT 10",
    "SELECT * FROM orders /* a comment; DROP TABLE orders */",
    "SELECT 1 -- trailing comment",
    "SELECT date_trunc('month', order_date) AS m, count(*) FROM orders GROUP BY 1",
    "SELECT * FROM (SELECT 1) AS s",
    "SELECT row_number() OVER (PARTITION BY a ORDER BY b) FROM t",
    "SELECT readme_col, reader FROM t",
    "SELECT lower(x), split_part(x, ',', 1) FROM t",
]

ATTACKS = [
    ("SELECT 1; DROP TABLE orders", "multiple_statements"),
    ("SELECT 1; SELECT 2", "multiple_statements"),
    ("SELECT 1 -- hi\n; DELETE FROM orders", "multiple_statements"),
    ("SELECT 1 /* x */; /* y */ INSERT INTO t VALUES (1)", "multiple_statements"),
    ("DROP TABLE orders", "not_select"),
    ("CREATE TABLE x AS SELECT 1", "not_select"),
    ("CREATE OR REPLACE VIEW v AS SELECT 1", "not_select"),
    ("ALTER TABLE orders ADD COLUMN x INT", "not_select"),
    ("INSERT INTO orders VALUES (1)", "not_select"),
    ("UPDATE orders SET amount = 0", "not_select"),
    ("DELETE FROM orders", "not_select"),
    ("TRUNCATE orders", "not_select"),
    ("MERGE INTO t USING u ON t.id = u.id WHEN MATCHED THEN DELETE", "not_select"),
    ("COPY orders TO '/tmp/x.csv'", "not_select"),
    ("COPY (SELECT * FROM orders) TO '/tmp/x.parquet' (FORMAT PARQUET)", "not_select"),
    ("ATTACH '/tmp/other.db' AS other", "not_select"),
    ("DETACH other", "not_select"),
    ("INSTALL httpfs", "not_select"),
    ("LOAD httpfs", "not_select"),
    ("PRAGMA table_info('orders')", "not_select"),
    ("SET enable_external_access = true", "not_select"),
    ("SET memory_limit = '100GB'", "not_select"),
    ("RESET memory_limit", "not_select"),
    ("USE other", "not_select"),
    ("EXPORT DATABASE '/tmp/dump'", None),
    ("IMPORT DATABASE '/tmp/dump'", None),
    ("CALL pragma_version()", "not_select"),
    ("BEGIN TRANSACTION", "not_select"),
    ("COMMIT", "not_select"),
    ("CHECKPOINT", "not_select"),
    ("VACUUM", "not_select"),
    ("DESCRIBE orders", "not_select"),
    ("SHOW TABLES", "not_select"),
    ("SUMMARIZE orders", "not_select"),
    ("SELECT * FROM read_csv('/etc/passwd')", "blocked_function"),
    ("SELECT * FROM read_csv_auto('/etc/passwd')", "blocked_function"),
    ("SELECT * FROM read_parquet('s3://bucket/x.parquet')", "blocked_function"),
    ("SELECT * FROM read_json_auto('/tmp/x.json')", "blocked_function"),
    ("SELECT * FROM read_text('/etc/passwd')", "blocked_function"),
    ("SELECT * FROM read_blob('/etc/passwd')", "blocked_function"),
    ("SELECT * FROM glob('/home/*')", "blocked_function"),
    ("SELECT * FROM parquet_scan('x.parquet')", "blocked_function"),
    ("SELECT * FROM sniff_csv('/etc/passwd')", "blocked_function"),
    ("SELECT * FROM query('DROP TABLE orders')", "blocked_function"),
    ("SELECT getenv('HOME')", "blocked_function"),
    ("SELECT * FROM sqlite_scan('x.db', 't')", "blocked_function"),
    ("SELECT * FROM postgres_scan('dbname=x', 'public', 't')", "blocked_function"),
    ("SELECT * FROM 'data.csv'", "file_access"),
    ("SELECT * FROM '/etc/passwd'", "file_access"),
    ('SELECT * FROM "data.parquet"', "file_access"),
    ("SELECT * FROM 'https://example.com/x.csv'", "file_access"),
    ("SELECT * FROM t JOIN 'other.csv' USING (id)", "file_access"),
    ("WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x", "forbidden_statement"),
    ("WITH x AS (INSERT INTO orders VALUES (1) RETURNING *) SELECT * FROM x", "forbidden_statement"),
    ("WITH x AS (UPDATE orders SET a = 1 RETURNING *) SELECT * FROM x", "forbidden_statement"),
    ("SELECT * INTO backup FROM orders", "forbidden_statement"),
    ("SELECT * FROM orders FOR UPDATE", "forbidden_statement"),
    ("SELECT * FROM (SELECT * FROM read_csv('/etc/passwd')) s", "blocked_function"),
    ("SELECT (SELECT count(*) FROM read_parquet('x')) AS n", "blocked_function"),
    ("SELECT * FROM t WHERE a IN (SELECT * FROM glob('*'))", "blocked_function"),
    ("", "empty"),
    ("   ;  ", "empty"),
    ("SELECT /*! DROP TABLE t */ 1", "executable_comment"),
    ("SELECT FROM WHERE", "parse_error"),
]


@pytest.mark.parametrize("sql", SAFE)
def test_safe_queries_pass(sql):
    out = ensure_read_only(sql)
    assert out
    assert ";" not in out.rstrip()[-1:]
    assert is_read_only(sql)


@pytest.mark.parametrize(("sql", "code"), ATTACKS)
def test_attacks_rejected(sql, code):
    with pytest.raises(UnsafeSQLError) as err:
        ensure_read_only(sql)
    if code is not None:
        assert err.value.code == code, str(err.value)
    assert not is_read_only(sql)


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        ("SELECT pg_read_file('/etc/passwd')", "postgres"),
        ("SELECT set_config('default_transaction_read_only', 'off', false)", "postgres"),
        ("SELECT pg_sleep(100)", "postgres"),
        ("SELECT nextval('seq')", "postgres"),
        ("SELECT lo_import('/etc/passwd')", "postgres"),
        ("SELECT * FROM dblink('host=x', 'DELETE FROM t') AS t(a int)", "postgres"),
        ("COPY t FROM PROGRAM 'rm -rf /'", "postgres"),
        ("SELECT LOAD_FILE('/etc/passwd')", "mysql"),
        ("SELECT SLEEP(100)", "mysql"),
        ("SELECT BENCHMARK(1000000, MD5('x'))", "mysql"),
        ("SELECT 1 /*!50000 , (SELECT 1) */", "mysql"),
        ("SELECT * FROM t INTO OUTFILE '/tmp/x'", "mysql"),
        ("EXEC xp_cmdshell 'dir'", "tsql"),
        ("SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'SELECT 1')", "tsql"),
        ("SELECT TOP 5 * INTO #tmp FROM t", "tsql"),
        ("WAITFOR DELAY '00:00:10'", "tsql"),
        ("SELECT SYSTEM$WAIT(10)", "snowflake"),
        ("PUT file:///etc/passwd @stage", "snowflake"),
        ("SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT 1')", "bigquery"),
        ("DECLARE x INT64; SET x = 1; SELECT x", "bigquery"),
    ],
)
def test_dialect_specific_attacks(sql, dialect):
    with pytest.raises(UnsafeSQLError):
        ensure_read_only(sql, dialect)


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        ("SELECT TOP 10 * FROM dbo.orders", "tsql"),
        ("SELECT * FROM `project.dataset.table` LIMIT 5", "bigquery"),
        ("SELECT * FROM public.orders WHERE created_at > now() - interval '1 day'", "postgres"),
        ("SELECT * FROM db.orders LIMIT 5", "mysql"),
        ("SELECT * FROM DB.SCHEMA.ORDERS LIMIT 5", "snowflake"),
    ],
)
def test_dialect_safe_queries(sql, dialect):
    assert ensure_read_only(sql, dialect)


def test_allow_functions_for_internal_use():
    with pytest.raises(UnsafeSQLError):
        ensure_read_only("SELECT * FROM glob('x')")
    assert ensure_read_only("SELECT * FROM glob('x')", allow_functions=["glob"])


def test_regenerated_sql_is_what_runs():
    out = ensure_read_only("select a from t -- comment\n")
    assert out == "SELECT a FROM t"


def test_too_long_and_nul():
    with pytest.raises(UnsafeSQLError):
        ensure_read_only("SELECT 1" + " " * 20, max_length=10)
    with pytest.raises(UnsafeSQLError):
        ensure_read_only("SELECT 1\x00")
    with pytest.raises(UnsafeSQLError):
        ensure_read_only(None)  # type: ignore[arg-type]


def test_referenced_tables():
    assert sorted(referenced_tables("WITH x AS (SELECT * FROM a) SELECT * FROM x JOIN b USING (id)")) == [
        "a",
        "b",
    ]


def test_store_blocks_file_access_even_without_sqlsafety(tmp_path):
    """Defense in depth: DuckDB itself has external access disabled."""
    from analystos_engine.store import StoreError, WorkspaceStore

    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    with WorkspaceStore(tmp_path / "ws") as store:
        with pytest.raises(StoreError):
            store._run(f"SELECT * FROM read_csv('{secret}')", None, 10, 5, display_sql="")
        with pytest.raises(StoreError):
            store._run(f"ATTACH '{tmp_path / 'x.db'}' AS x", None, 10, 5, display_sql="")
        with pytest.raises(StoreError):
            store._run("SET enable_external_access = true", None, 10, 5, display_sql="")


def test_comment_breakout_is_neutralised():
    out = ensure_read_only("SELECT 1 -- x */ ; DROP TABLE t; /*")
    assert out == "SELECT 1"


# ------------------------------------------------------------------ review R-16


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM json_execute_serialized_sql(json_serialize_sql('SELECT count(*) FROM query_table(''orders'')'))",
        "SELECT json_serialize_sql('SELECT 1')",
        "SELECT * FROM duckdb_databases()",
        "SELECT database_name, path FROM duckdb_databases()",
        "SELECT current_setting('temp_directory')",
        "SELECT checkpoint()",
        "SELECT force_checkpoint()",
        "SELECT getvariable('x')",
        "SELECT * FROM pragma_database_list",
        "SELECT * FROM pragma_table_info('orders')",
        "SELECT * FROM some_new_table_function(1)",
        "SELECT * FROM orders, LATERAL duckdb_settings()",
    ],
)
def test_introspection_and_sql_in_string_functions_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        ensure_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM range(10)",
        "SELECT * FROM generate_series(1, 3)",
        "SELECT * FROM unnest([1, 2]) AS t(x)",
        "SELECT o.* FROM orders AS o",
    ],
)
def test_allowlisted_table_functions_pass(sql):
    ensure_read_only(sql)


def test_duckdb_only_blocks_do_not_affect_postgres_settings_check():
    ensure_read_only("SELECT current_setting('transaction_read_only')", "postgres")


def test_blocked_function_rejected_by_live_store():
    from analystos_engine.store import WorkspaceStore

    with WorkspaceStore.in_memory() as store, pytest.raises(UnsafeSQLError):
        store.execute_read("SELECT * FROM duckdb_databases()")
