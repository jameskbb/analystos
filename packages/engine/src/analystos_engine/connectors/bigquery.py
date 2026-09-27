"""Google BigQuery connector (google-cloud-bigquery). Install with the ``bigquery`` extra.

Read-only: statements are validated with the ``bigquery`` dialect (scripts, DML, DDL,
``EXPORT DATA``, ``EXTERNAL_QUERY`` are rejected) and then *dry-run* first: the dry run must
report ``statement_type == "SELECT"`` before the real job runs. Every job has
``maximum_bytes_billed`` so a query can never cost more than the configured cap.
Previews use the table-data API (no query cost). Parameters use ``@name`` and are passed
as typed query parameters. Credentials are a service-account JSON key (secret) or
application default credentials.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..sqlsafety import ensure_read_only
from ..types import (
    ColumnInfo,
    ConnectionTestResult,
    QueryColumn,
    QueryResult,
    TableInfo,
    TableProfile,
    normalize_value,
)
from .base import ConnectorConfig, ConnectorError, SchemaInfo, redact, register_connector
from .sql_base import json_schema_for, profile_from_sample

__all__ = ["BigQueryConnector", "BigQueryConfig", "BigQuerySecrets"]

logger = logging.getLogger(__name__)


class BigQueryConfig(ConnectorConfig):
    project: str
    location: str | None = None
    default_dataset: str | None = None
    maximum_bytes_billed: int = Field(
        default=10 * 1024**3, gt=0, description="hard cost cap per query, in bytes"
    )


class BigQuerySecrets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credentials_json: SecretStr | None = Field(
        default=None, description="service-account key JSON; empty = ADC"
    )


def _param_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    if isinstance(value, dt.datetime):
        return "TIMESTAMP"
    if isinstance(value, dt.date):
        return "DATE"
    return "STRING"


class BigQueryConnector:
    kind = "bigquery"
    dialect = "bigquery"

    def __init__(self, config: Mapping[str, Any], secrets: Mapping[str, Any]) -> None:
        try:
            self.config = BigQueryConfig.model_validate(dict(config))
            self.secrets = BigQuerySecrets.model_validate(dict(secrets))
        except Exception as exc:
            raise ValueError(
                redact(str(exc), [str(v) for v in secrets.values() if isinstance(v, str)])
            ) from None
        self._client: Any = None
        self._bq: Any = None

    def __repr__(self) -> str:
        return f"<BigQueryConnector project={self.config.project}>"

    def _secret_values(self) -> list[str]:
        if self.secrets.credentials_json is None:
            return []
        raw = self.secrets.credentials_json.get_secret_value()
        out = [raw]
        try:
            info = json.loads(raw)
            out += [str(info.get(k)) for k in ("private_key", "private_key_id", "client_id") if info.get(k)]
        except ValueError:
            pass
        return out

    def _err(self, exc: BaseException, action: str, code: str = "connector_error") -> ConnectorError:
        logger.warning("connector bigquery %s failed: %s", action, type(exc).__name__)
        return ConnectorError(
            redact(f"{action} failed: {type(exc).__name__}: {exc}", self._secret_values()),
            kind=self.kind,
            code=code,
        )

    def _module(self) -> Any:
        if self._bq is None:
            try:
                self._bq = importlib.import_module("google.cloud.bigquery")
            except ImportError as exc:
                raise ConnectorError(
                    "the bigquery connector needs the optional driver 'google-cloud-bigquery'. Install it with "
                    "`uv sync --extra bigquery` (or `pip install 'analystos-engine[bigquery]'`).",
                    kind=self.kind,
                    code="driver_missing",
                ) from exc
        return self._bq

    def connect(self) -> None:
        if self._client is not None:
            return
        bq = self._module()
        try:
            credentials = None
            if self.secrets.credentials_json is not None:
                sa = importlib.import_module("google.oauth2.service_account")
                info = json.loads(self.secrets.credentials_json.get_secret_value())
                credentials = sa.Credentials.from_service_account_info(info)
            self._client = bq.Client(
                project=self.config.project, credentials=credentials, location=self.config.location
            )
        except ConnectorError:
            raise
        except Exception as exc:
            raise self._err(exc, "connection", "connection_failed") from None

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # best effort
                logger.debug("bigquery client close failed")
            self._client = None

    def _job_config(self, params: Mapping[str, Any] | None, *, dry_run: bool) -> Any:
        bq = self._module()
        kwargs: dict[str, Any] = {
            "use_legacy_sql": False,
            "maximum_bytes_billed": self.config.maximum_bytes_billed,
        }
        if dry_run:
            kwargs.update(dry_run=True, use_query_cache=False)
        if self.config.default_dataset:
            kwargs["default_dataset"] = f"{self.config.project}.{self.config.default_dataset}"
        if params:
            kwargs["query_parameters"] = [
                bq.ScalarQueryParameter(k, _param_type(v), v) for k, v in params.items()
            ]
        return bq.QueryJobConfig(**kwargs)

    def query(
        self, sql: str, params: Mapping[str, Any] | None = None, limit: int | None = None
    ) -> QueryResult:
        safe = ensure_read_only(sql, self.dialect)
        limit = limit if limit is not None else self.config.default_row_limit
        self.connect()
        started = time.perf_counter()
        try:
            dry = self._client.query(safe, job_config=self._job_config(params, dry_run=True))
            stype = getattr(dry, "statement_type", None)
            if stype is not None and str(stype).upper() != "SELECT":
                raise ConnectorError(
                    f"BigQuery reports a {stype} statement; only SELECT is allowed",
                    kind=self.kind,
                    code="unsafe_sql",
                )
            job = self._client.query(safe, job_config=self._job_config(params, dry_run=False))
            rows_iter = job.result(max_results=limit + 1, timeout=self.config.query_timeout_s)
            schema = list(getattr(rows_iter, "schema", None) or [])
            raw = [list(r.values()) for r in rows_iter]
        except ConnectorError:
            raise
        except Exception as exc:
            raise self._err(exc, "query", "query_failed") from None
        truncated = len(raw) > limit
        raw = raw[:limit]
        cols = [QueryColumn(name=f.name, type=str(f.field_type)) for f in schema]
        return QueryResult(
            columns=cols,
            rows=[[normalize_value(v) for v in r] for r in raw],
            row_count=len(raw),
            truncated=truncated,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            sql=safe,
        )

    def test(self) -> ConnectionTestResult:
        started = time.perf_counter()
        try:
            self.connect()
            datasets = list(self._client.list_datasets(max_results=1))
        except ConnectorError as exc:
            return ConnectionTestResult(
                ok=False, message=str(exc), latency_ms=round((time.perf_counter() - started) * 1000, 3)
            )
        except Exception as exc:
            return ConnectionTestResult(
                ok=False,
                message=str(self._err(exc, "test")),
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        return ConnectionTestResult(
            ok=True,
            message="connected; read-only enforced by SQL validation, SELECT-only dry runs and a bytes-billed cap",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            read_only_enforced=True,
            details={"project": self.config.project, "datasets_visible": len(datasets) > 0},
        )

    def discover_schemas(self) -> list[SchemaInfo]:
        self.connect()
        try:
            return [SchemaInfo(name=d.dataset_id) for d in self._client.list_datasets()]
        except Exception as exc:
            raise self._err(exc, "list datasets") from None

    def discover_tables(self, schema: str) -> list[TableInfo]:
        self.connect()
        try:
            tables = list(self._client.list_tables(f"{self.config.project}.{schema}"))
        except Exception as exc:
            raise self._err(exc, "list tables") from None
        return [
            TableInfo(
                name=t.table_id,
                schema_name=schema,
                kind="view" if str(getattr(t, "table_type", "")).upper() == "VIEW" else "table",
                source=self.kind,
            )
            for t in tables
        ]

    def _table(self, schema: str, table: str) -> Any:
        self.connect()
        try:
            return self._client.get_table(f"{self.config.project}.{schema}.{table}")
        except Exception as exc:
            raise self._err(exc, "get table") from None

    def inspect_columns(self, schema: str, table: str) -> list[ColumnInfo]:
        t = self._table(schema, table)
        return [
            ColumnInfo(
                name=f.name,
                type=str(f.field_type),
                nullable=str(getattr(f, "mode", "NULLABLE")).upper() != "REQUIRED",
                ordinal=i + 1,
                description=getattr(f, "description", None),
            )
            for i, f in enumerate(t.schema)
        ]

    def preview(self, schema: str, table: str, limit: int = 100) -> QueryResult:
        t = self._table(schema, table)
        started = time.perf_counter()
        try:
            rows = list(self._client.list_rows(t, max_results=limit + 1))
        except Exception as exc:
            raise self._err(exc, "preview") from None
        truncated = len(rows) > limit
        rows = rows[:limit]
        cols = [QueryColumn(name=f.name, type=str(f.field_type)) for f in t.schema]
        return QueryResult(
            columns=cols,
            rows=[[normalize_value(v) for v in r.values()] for r in rows],
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            sql=f"-- table data API: {self.config.project}.{schema}.{table}",
        )

    def profile(self, schema: str, table: str) -> TableProfile:
        sample = self.preview(schema, table, self.config.profile_sample_rows)
        return profile_from_sample(sample, f"{schema}.{table}", sampled=sample.truncated)


register_connector(
    "bigquery",
    lambda config, secrets: BigQueryConnector(config, secrets),
    lambda: json_schema_for(BigQueryConfig, BigQuerySecrets),
)
