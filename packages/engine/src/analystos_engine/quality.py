"""Data quality rules: definitions, execution, suggestions and (text-only) fix advice.

Rules never modify data. ``run_rule`` executes read-only SQL that selects the failing
rows, counts them and returns a sample so analysts can inspect them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .semantic.compiler import literal_sql
from .sqlsafety import UnsafeSQLError, ensure_read_only
from .store import StoreError, WorkspaceStore, quote_ident
from .types import QueryResult, TableProfile

__all__ = [
    "Rule",
    "RuleKind",
    "RuleResult",
    "RuleError",
    "rule_failing_sql",
    "run_rule",
    "run_rules",
    "suggest_rules",
    "suggest_fix",
]

RuleKind = Literal[
    "not_null",
    "unique",
    "range",
    "between",
    "fk_exists",
    "not_future",
    "allowed_values",
    "regex",
    "custom_sql",
]

_PLANNED_DATE = re.compile(
    r"(due|expected|forecast|plan|target|budget|end|expir|renew|scheduled|promised|eta)", re.I
)


class RuleError(ValueError):
    """The rule definition is invalid."""


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    name: str = ""
    kind: RuleKind
    table: str
    column: str | None = None
    min_value: float | int | str | None = None
    max_value: float | int | str | None = None
    inclusive: bool = True
    allowed_values: list[Any] = Field(default_factory=list)
    pattern: str | None = None
    ref_table: str | None = None
    ref_column: str | None = None
    sql: str | None = Field(default=None, description="custom_sql: a SELECT returning the failing rows")
    severity: Literal["info", "warning", "error"] = "warning"
    description: str = ""
    enabled: bool = True
    suggested: bool = False
    rationale: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Rule:
        k = self.kind
        if k != "custom_sql" and not self.column:
            raise ValueError(f"{k} rule needs a column")
        if k in ("range", "between") and self.min_value is None and self.max_value is None:
            raise ValueError(f"{k} rule needs min_value and/or max_value")
        if k == "between" and (self.min_value is None or self.max_value is None):
            raise ValueError("between rule needs both min_value and max_value")
        if k == "fk_exists" and not (self.ref_table and self.ref_column):
            raise ValueError("fk_exists rule needs ref_table and ref_column")
        if k == "allowed_values" and not self.allowed_values:
            raise ValueError("allowed_values rule needs at least one allowed value")
        if k == "regex":
            if not self.pattern:
                raise ValueError("regex rule needs a pattern")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        if k == "custom_sql" and not self.sql:
            raise ValueError("custom_sql rule needs sql")
        if not self.id:
            payload = self.model_dump(
                mode="json",
                include={
                    "kind",
                    "table",
                    "column",
                    "min_value",
                    "max_value",
                    "inclusive",
                    "allowed_values",
                    "pattern",
                    "ref_table",
                    "ref_column",
                    "sql",
                },
            )
            self.id = (
                "rule_"
                + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]
            )
        if not self.name:
            self.name = _default_name(self)
        return self


def _default_name(r: Rule) -> str:
    col = f"{r.table}.{r.column}" if r.column else r.table
    return {
        "not_null": f"{col} is not null",
        "unique": f"{col} is unique",
        "range": f"{col} within [{r.min_value if r.min_value is not None else '-inf'}, {r.max_value if r.max_value is not None else 'inf'}]",
        "between": f"{col} between {r.min_value} and {r.max_value}",
        "fk_exists": f"{col} exists in {r.ref_table}.{r.ref_column}",
        "not_future": f"{col} is not in the future",
        "allowed_values": f"{col} in allowed set",
        "regex": f"{col} matches {r.pattern}",
        "custom_sql": f"custom check on {r.table}",
    }[r.kind]


class RuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    rule: Rule
    passed: bool
    failing_count: int
    total_count: int
    failing_pct: float
    sample_failing_rows: QueryResult
    sql: str
    executed_at: dt.datetime
    elapsed_ms: float
    error: str | None = None


def _q(name: str) -> str:
    return ".".join(quote_ident(p) for p in name.split("."))


def rule_failing_sql(rule: Rule, *, today: dt.date | None = None) -> str:
    """SQL selecting the rows that violate ``rule`` (read-only)."""
    t = _q(rule.table)
    if rule.kind == "custom_sql":
        return ensure_read_only(rule.sql or "", "duckdb")
    c = quote_ident(rule.column or "")
    if rule.kind == "not_null":
        where = f"{c} IS NULL"
    elif rule.kind == "unique":
        where = f"{c} IN (SELECT {c} FROM {t} WHERE {c} IS NOT NULL GROUP BY {c} HAVING count(*) > 1)"
    elif rule.kind in ("range", "between"):
        lo_op, hi_op = ("<", ">") if rule.inclusive else ("<=", ">=")
        parts = []
        if rule.min_value is not None:
            parts.append(f"{c} {lo_op} {literal_sql(rule.min_value)}")
        if rule.max_value is not None:
            parts.append(f"{c} {hi_op} {literal_sql(rule.max_value)}")
        where = "(" + " OR ".join(parts) + ")"
    elif rule.kind == "fk_exists":
        rt, rc = _q(rule.ref_table or ""), quote_ident(rule.ref_column or "")
        where = f"{c} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {rt} AS _ref WHERE _ref.{rc} = {t}.{c})"
    elif rule.kind == "not_future":
        day = (today or dt.date.today()).isoformat()
        where = f"CAST({c} AS DATE) > DATE '{day}'"
    elif rule.kind == "allowed_values":
        non_null = [v for v in rule.allowed_values if v is not None]
        where = f"{c} IS NOT NULL AND {c} NOT IN ({', '.join(literal_sql(v) for v in non_null)})"
    else:
        where = f"{c} IS NOT NULL AND NOT regexp_full_match(CAST({c} AS VARCHAR), {literal_sql(rule.pattern or '')})"
    return ensure_read_only(f"SELECT * FROM {t} WHERE {where}", "duckdb")


def run_rule(
    store: WorkspaceStore, rule: Rule, *, sample_limit: int = 20, today: dt.date | None = None
) -> RuleResult:
    """Execute ``rule`` and return pass/fail, failing count and a sample of failing rows."""
    started = time.perf_counter()
    executed_at = dt.datetime.now(dt.UTC)
    try:
        failing_sql = rule_failing_sql(rule, today=today)
        total = int(store.execute_read(f"SELECT count(*) FROM {_q(rule.table)}").scalar() or 0)
        failing = int(store.execute_read(f"SELECT count(*) FROM ({failing_sql}) AS _f").scalar() or 0)
        sample = store.execute_read(failing_sql, limit=sample_limit)
    except (StoreError, UnsafeSQLError) as exc:
        return RuleResult(
            rule_id=rule.id,
            rule=rule,
            passed=False,
            failing_count=0,
            total_count=0,
            failing_pct=0.0,
            sample_failing_rows=QueryResult(),
            sql="",
            executed_at=executed_at,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            error=str(exc),
        )
    return RuleResult(
        rule_id=rule.id,
        rule=rule,
        passed=failing == 0,
        failing_count=failing,
        total_count=total,
        failing_pct=round(failing / total, 6) if total else 0.0,
        sample_failing_rows=sample,
        sql=failing_sql,
        executed_at=executed_at,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
    )


def run_rules(store: WorkspaceStore, rules: list[Rule], **kwargs: Any) -> list[RuleResult]:
    return [run_rule(store, r, **kwargs) for r in rules if r.enabled]


def suggest_rules(profile: TableProfile, relationships: list[Any] | None = None) -> list[Rule]:
    """Suggest rules from a profile (and optional relationship suggestions). Suggestions are not active until accepted."""
    out: list[Rule] = []
    table = profile.table
    for col in profile.columns:
        roles = set(col.semantic_roles)
        nn = col.row_count - col.null_count
        if "identifier" in roles:
            out.append(
                Rule(
                    kind="unique",
                    table=table,
                    column=col.name,
                    severity="error",
                    suggested=True,
                    rationale=f"{col.name} looks like the table's identifier",
                )
            )
        if "identifier" in roles or "foreign_key" in roles:
            out.append(
                Rule(
                    kind="not_null",
                    table=table,
                    column=col.name,
                    severity="error" if "identifier" in roles else "warning",
                    suggested=True,
                    rationale=f"{col.name} is a key; rows without it cannot be joined or counted",
                )
            )
        elif col.row_count and col.null_count == 0 and col.row_count >= 20 and "measure" not in roles:
            out.append(
                Rule(
                    kind="not_null",
                    table=table,
                    column=col.name,
                    severity="info",
                    suggested=True,
                    rationale=f"{col.name} is fully populated today",
                )
            )
        if "percentage" in roles and nn:
            hi = 100 if (col.median or 0) > 1.5 else 1
            out.append(
                Rule(
                    kind="between",
                    table=table,
                    column=col.name,
                    min_value=0,
                    max_value=hi,
                    suggested=True,
                    rationale=f"{col.name} is a percentage (0-{hi} scale)",
                )
            )
        elif (
            ("currency" in roles or "count" in roles)
            and nn
            and re.search(r"(qty|quantity|units|price|cost|count|stock|on_hand|fee)", col.name, re.I)
        ):
            out.append(
                Rule(
                    kind="range",
                    table=table,
                    column=col.name,
                    min_value=0,
                    suggested=True,
                    rationale=f"{col.name} is a quantity or price and should not be negative",
                )
            )
        if (
            "date" in roles
            and col.inferred_type in ("date", "timestamp")
            and not _PLANNED_DATE.search(col.name)
        ):
            out.append(
                Rule(
                    kind="not_future",
                    table=table,
                    column=col.name,
                    suggested=True,
                    rationale=f"{col.name} records events that have already happened",
                )
            )
        if "category" in roles and 1 < col.distinct_count <= 12 and len(col.top_values) == col.distinct_count:
            values = sorted({v.value for v in col.top_values}, key=str)
            out.append(
                Rule(
                    kind="allowed_values",
                    table=table,
                    column=col.name,
                    allowed_values=values,
                    severity="info",
                    suggested=True,
                    rationale=f"{col.name} currently takes {len(values)} values; new values may indicate a data issue",
                )
            )
        if "email" in roles:
            out.append(
                Rule(
                    kind="regex",
                    table=table,
                    column=col.name,
                    pattern=r"[^@\s]+@[^@\s]+\.[^@\s]+",
                    severity="info",
                    suggested=True,
                    rationale=f"{col.name} holds email addresses",
                )
            )
    for rel in relationships or []:
        if getattr(rel, "from_table", None) == table and getattr(rel, "cardinality", "") != "many_to_many":
            out.append(
                Rule(
                    kind="fk_exists",
                    table=table,
                    column=rel.from_col,
                    ref_table=rel.to_table,
                    ref_column=rel.to_col,
                    suggested=True,
                    rationale=f"{table}.{rel.from_col} references {rel.to_table}.{rel.to_col}",
                )
            )
    seen: set[str] = set()
    unique_rules = []
    for r in out:
        if r.id not in seen:
            seen.add(r.id)
            unique_rules.append(r)
    return unique_rules


def suggest_fix(rule_result: RuleResult) -> str:
    """Plain-language remediation options. Never applied automatically."""
    r = rule_result.rule
    if rule_result.error:
        return (
            f"The rule could not run: {rule_result.error}. Check that {r.table} and its columns still exist."
        )
    if rule_result.passed:
        return "No action needed: every row passes this rule."
    n = rule_result.failing_count
    col = f"{r.table}.{r.column}" if r.column else r.table
    advice = {
        "not_null": (
            f"{n} rows have no {col}. Options: fix the upstream source so the value is always captured; "
            f"exclude these rows explicitly with a metric filter (and say so in reports); or relax the rule if blanks are legitimate."
        ),
        "unique": (
            f"{n} rows share a {col} value with another row. Check whether they are true duplicates (same content, "
            "re-exported) or distinct records reusing an id. For true duplicates, deduplicate in the source or define the "
            "entity on a deduplicated view; do not count them twice in metrics."
        ),
        "range": f"{n} rows have {col} outside the expected range. Inspect whether they are data-entry errors, returns entered as negative sales, or legitimate edge cases, then correct at the source or model them explicitly.",
        "between": f"{n} rows have {col} outside [{r.min_value}, {r.max_value}]. Check the scale (0-1 vs 0-100) and data entry, then correct at the source.",
        "fk_exists": f"{n} rows reference a {r.ref_table}.{r.ref_column} that does not exist (orphans). Load the missing {r.ref_table} rows or map the codes; until then metrics grouped by {r.ref_table} attributes put these rows under an empty (NULL) group.",
        "not_future": f"{n} rows have {col} in the future. They may be planned dates, time-zone shifts or typos; confirm the field's meaning and correct or exclude them.",
        "allowed_values": f"{n} rows have a {col} value outside the allowed set. Either a new legitimate value appeared (update the rule) or values need standardising (for example inconsistent casing or spelling).",
        "regex": f"{n} rows have {col} values that do not match the expected pattern {r.pattern!r}. Standardise the format at the source.",
        "custom_sql": f"{n} rows fail the custom check. Inspect the sample rows to decide on a correction at the source.",
    }[r.kind]
    return advice + " AnalystOS never changes your data automatically."
