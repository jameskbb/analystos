"""Core shared types for the AnalystOS engine.

Every model here is a Pydantic v2 model so that the API layer can serialise it
directly and the investigator can persist it as part of an artifact.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import math
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ColumnInfo",
    "TableInfo",
    "QueryColumn",
    "QueryResult",
    "DatasetVersion",
    "ConnectionTestResult",
    "ColumnProfile",
    "TopValue",
    "ProfileIssue",
    "TableProfile",
    "TimeWindow",
    "normalize_value",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ColumnInfo(_Model):
    """A column of a table or view as discovered from a store or connector."""

    name: str
    type: str
    nullable: bool = True
    ordinal: int | None = None
    description: str | None = None


class TableInfo(_Model):
    """A table (or view) discovered in a store or external source."""

    name: str
    schema_name: str = "main"
    kind: Literal["table", "view"] = "table"
    columns: list[ColumnInfo] = Field(default_factory=list)
    row_count: int | None = None
    source: str | None = None
    description: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"

    def column(self, name: str) -> ColumnInfo:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(f"column {name!r} not found in table {self.name!r}")


class QueryColumn(_Model):
    name: str
    type: str


def normalize_value(value: Any) -> Any:
    """Convert a database value to a JSON-friendly Python value.

    Dates, datetimes and times stay native (Pydantic serialises them as ISO
    strings). Decimals become floats, UUIDs strings, bytes base64, NaN None.
    """
    if value is None:
        return None
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, decimal.Decimal):
        f = float(value)
        return None if math.isnan(f) else f
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, list | tuple):
        return [normalize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}
    # numpy scalars and similar expose item()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return normalize_value(item())
        except (TypeError, ValueError):
            pass
    return str(value)


class QueryResult(_Model):
    """Tabular result of a read-only query."""

    columns: list[QueryColumn] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: float = 0.0
    sql: str = ""

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column_index(self, name: str) -> int:
        for i, c in enumerate(self.columns):
            if c.name == name:
                return i
        raise KeyError(f"column {name!r} not in result (have {self.column_names})")

    def column_values(self, name: str) -> list[Any]:
        idx = self.column_index(name)
        return [row[idx] for row in self.rows]

    def to_records(self) -> list[dict[str, Any]]:
        names = self.column_names
        return [dict(zip(names, row, strict=False)) for row in self.rows]

    def scalar(self, column: str | None = None) -> Any:
        """Return the first value of the first row (or of the named column)."""
        if not self.rows:
            return None
        if column is None:
            return self.rows[0][0]
        return self.rows[0][self.column_index(column)]

    def to_pandas(self) -> Any:
        import pandas as pd

        return pd.DataFrame(self.rows, columns=self.column_names)

    @classmethod
    def from_pandas(
        cls, df: Any, *, sql: str = "", limit: int | None = None, elapsed_ms: float = 0.0
    ) -> QueryResult:
        total = len(df)
        if limit is not None and total > limit:
            df = df.head(limit)
        columns = [QueryColumn(name=str(c), type=str(t)) for c, t in zip(df.columns, df.dtypes, strict=True)]
        rows = [
            [normalize_value(v) for v in rec] for rec in df.astype(object).itertuples(index=False, name=None)
        ]
        return cls(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=limit is not None and total > limit,
            elapsed_ms=elapsed_ms,
            sql=sql,
        )


class DatasetVersion(_Model):
    """Identity of a dataset's content at a point in time (for reproducibility)."""

    table: str | None = None
    content_hash: str
    row_count: int
    captured_at: dt.datetime


class ConnectionTestResult(_Model):
    ok: bool
    message: str
    latency_ms: float | None = None
    server_version: str | None = None
    read_only_enforced: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class TopValue(_Model):
    value: Any
    count: int
    pct: float


class ColumnProfile(_Model):
    name: str
    type: str
    inferred_type: Literal[
        "integer", "float", "decimal", "boolean", "date", "timestamp", "time", "string", "other"
    ]
    row_count: int
    null_count: int
    null_pct: float
    distinct_count: int
    distinct_pct: float
    min: Any = None
    max: Any = None
    mean: float | None = None
    median: float | None = None
    stddev: float | None = None
    quantiles: dict[str, float] = Field(default_factory=dict)
    top_values: list[TopValue] = Field(default_factory=list)
    min_length: int | None = None
    max_length: int | None = None
    zero_count: int | None = None
    negative_count: int | None = None
    semantic_roles: list[
        Literal[
            "identifier",
            "foreign_key",
            "category",
            "date",
            "currency",
            "percentage",
            "boolean",
            "measure",
            "count",
            "text",
            "geo",
            "email",
        ]
    ] = Field(default_factory=list)
    cardinality: Literal["constant", "low", "medium", "high", "unique"] = "medium"


class ProfileIssue(_Model):
    code: Literal[
        "duplicate_rows",
        "duplicate_ids",
        "unexpected_nulls",
        "mixed_types",
        "malformed_dates",
        "outliers",
        "constant_column",
        "high_cardinality",
        "impossible_values",
        "future_dates",
        "whitespace_padding",
        "inconsistent_casing",
        "empty_column",
    ]
    severity: Literal["info", "warning", "error"]
    column: str | None = None
    message: str
    count: int | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    sample_values: list[Any] = Field(default_factory=list)


class TableProfile(_Model):
    table: str
    row_count: int
    column_count: int
    columns: list[ColumnProfile] = Field(default_factory=list)
    issues: list[ProfileIssue] = Field(default_factory=list)
    duplicate_row_count: int = 0
    sampled: bool = False
    sample_size: int | None = None
    profiled_at: dt.datetime
    elapsed_ms: float = 0.0
    version: DatasetVersion | None = None

    def column(self, name: str) -> ColumnProfile:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(f"column {name!r} not profiled")


class TimeWindow(_Model):
    """A half-open time window ``[start, end)`` on an (optional) time dimension.

    ``end`` is exclusive: August 2026 is ``start=2026-08-01, end=2026-09-01``.
    ``kind`` records how the window was produced so ``previous_period`` can
    shift it correctly (a month shifts by a calendar month, not 31 days).
    """

    dimension: str | None = None
    start: dt.date
    end: dt.date
    grain: str | None = None
    label: str | None = None
    kind: Literal[
        "day",
        "week",
        "month",
        "quarter",
        "year",
        "fiscal_month",
        "fiscal_quarter",
        "fiscal_year",
        "mtd",
        "qtd",
        "ytd",
        "fytd",
        "rolling",
        "custom",
    ] = "custom"

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    @property
    def last_day(self) -> dt.date:
        return self.end - dt.timedelta(days=1)

    def display(self) -> str:
        if self.label:
            return self.label
        return f"{self.start.isoformat()} to {self.last_day.isoformat()}"

    def with_dimension(self, dimension: str | None, grain: str | None = None) -> TimeWindow:
        return self.model_copy(
            update={"dimension": dimension, "grain": grain if grain is not None else self.grain}
        )
