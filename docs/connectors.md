# Connectors and ingestion

## Files

`analystos_engine.ingest` (`inspect_file`, `ingest_file`) reads uploads into the workspace DuckDB
store. Source files are never modified.

| Format | Handling |
|---|---|
| CSV / TSV / TXT | Encoding from BOM, UTF-8, then `charset_normalizer`, falling back to Latin-1; delimiter sniffed among `, ; \t \|` |
| Excel (`.xlsx`, `.xlsm`) | All sheets scanned (hidden sheets reported); formulas read as cached values (formulas without a cached value are reported); header row detected by scoring the first 25 rows; title rows, blank separator rows, "Total" footers and trailing notes are skipped and reported; merged cells and multiple table blocks per sheet are detected; Excel date serials converted. `.xls` is rejected with a "save as .xlsx" message. Zip-bomb checks run first. |
| JSON | Array of records, a wrapper object with a list, or columnar JSON; nested values flattened (`a_b`) or stored as JSON text. NDJSON / JSONL too. |
| Parquet | Read directly. |

`IngestOptions`: `table_name`, `table_key` (which sheet/block), `sheet`, `header_row` (1-based),
`if_exists` (fail, replace, append), `column_types`, `column_names`, `delimiter`, `encoding`,
`max_bytes`. The web upload wizard previews exactly what will be ingested and lets the analyst
override the sheet and header row. The demo's `budgets.xlsx` (title rows, a blank row between fiscal
years, a notes row, two sheets) is a realistic test case.

## Databases

Connectors implement one protocol (`analystos_engine.connectors.base.Connector`): `connect`, `test`,
`discover_schemas`, `discover_tables`, `inspect_columns`, `preview`, `query`, `profile`. Obtain one
with `get_connector(kind, config, secrets)`.

| Kind | Driver (optional extra) | Read-only enforcement |
|---|---|---|
| `duckdb` | built in | The workspace store: read-only SQL, external access disabled, row cap, timeout |
| `postgres` | psycopg 3 | `default_transaction_read_only=on`, psycopg `read_only`, `statement_timeout`, rollback after every query |
| `mysql` | PyMySQL | `SET SESSION TRANSACTION READ ONLY`, `MAX_EXECUTION_TIME`, rollback |
| `sqlserver` | pyodbc or pymssql | No read-only session setting exists: statement validation plus rollback; use a read-only login |
| `snowflake` | snowflake-connector-python | Statement validation, tagged queries, rollback; use a role with SELECT only |
| `bigquery` | google-cloud-bigquery | Statement validation, then a dry run that must report `SELECT`, and `maximum_bytes_billed` on every job |

Common to all: every statement passes `ensure_read_only(sql, dialect)` for the connector's sqlglot
dialect before it reaches the driver; drivers are imported lazily; secrets are `SecretStr` and
redacted from errors and logs; defaults are a 60 s query timeout, a 10,000-row limit and 50,000-row
profiling samples. Install drivers with the engine's extras (for example
`uv sync --all-packages --all-extras`).

Through the API (`/workspaces/{ws}/data-sources`), credentials are sent in `secrets`, encrypted with
Fernet, never returned, logged or written to the audit log. Tables can be queried live or snapshotted
into the workspace store (`.../import`).

## Testing without credentials

No test needs a live database, Docker or network. Connector tests
(`packages/engine/tests/test_connectors.py`) run against fake DB-API drivers that record the session
settings and statements issued, which verifies read-only setup, validation before execution, timeouts,
secret redaction and result mapping. Before relying on a connector in production, run
`POST /data-sources/{id}/test`: it reports whether the session is actually read-only
(`read_only_enforced`).
