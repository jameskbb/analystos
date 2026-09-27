"""Automatic dataset profiling.

``profile_table(store, table)`` computes per-column statistics with read-only SQL,
guesses semantic roles and reports data quality issues, each with severity and the
evidence (counts, sample values, the rule applied) that triggered it.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any

from .store import WorkspaceStore, quote_ident
from .types import ColumnProfile, ProfileIssue, TableProfile, TopValue

__all__ = ["profile_table", "guess_roles", "TableProfile", "ColumnProfile", "ProfileIssue"]

_ID_NAME = re.compile(
    r"(^id$|_id$|^id_|_key$|^key$|_code$|^code$|^sku$|_sku$|_number$|_no$|^uuid$|_uuid$)", re.I
)
_CURRENCY_NAME = re.compile(
    r"(amount|amt|revenue|price|cost|sales|total|value|budget|fee|spend|profit|income|expense|net|gross|payment|balance|margin(?!.*(pct|percent|rate)))",
    re.I,
)
_PCT_NAME = re.compile(r"(pct|percent|rate|ratio|share|_perc$|margin_pct|discount_pct|conversion)", re.I)
_COUNT_NAME = re.compile(r"(qty|quantity|count|units|^num_|number_of|_count$|headcount|stock|on_hand)", re.I)
_GEO_NAME = re.compile(
    r"(region|city|state|country|zip|postal|branch|territory|latitude|longitude|^lat$|^lon$|^lng$|address)",
    re.I,
)
_DATE_NAME = re.compile(r"(date|_dt$|^dt_|_at$|time|day|month|period|created|updated|timestamp)", re.I)
_NONNEG_NAME = re.compile(
    r"(qty|quantity|units|price|cost|count|stock|on_hand|fee|list_price|unit_cost|headcount)", re.I
)

_NUMERIC_TYPES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
)


def _inferred(t: str) -> str:
    u = t.upper()
    if u.startswith("DECIMAL"):
        return "decimal"
    if u in ("FLOAT", "DOUBLE", "REAL"):
        return "float"
    if u in _NUMERIC_TYPES:
        return "integer"
    if u == "BOOLEAN":
        return "boolean"
    if u == "DATE":
        return "date"
    if u.startswith("TIMESTAMP"):
        return "timestamp"
    if u.startswith("TIME"):
        return "time"
    if u in ("VARCHAR", "TEXT", "STRING") or u.startswith("VARCHAR"):
        return "string"
    return "other"


def _is_numeric(inferred: str) -> bool:
    return inferred in ("integer", "float", "decimal")


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def guess_roles(
    name: str, prof: ColumnProfile, *, table: str | None = None, date_parse_ratio: float | None = None
) -> list[str]:
    """Heuristic semantic roles for a column (identifier, currency, date, ...)."""
    roles: list[str] = []
    t = prof.inferred_type
    non_null = prof.row_count - prof.null_count
    unique = non_null > 0 and prof.distinct_count == non_null
    idish = bool(_ID_NAME.search(name))
    if idish and t in ("integer", "string"):
        roles.append("identifier" if unique or _is_primary_key_name(name, table) else "foreign_key")
    elif (
        t == "string"
        and unique
        and non_null >= 20
        and (prof.max_length or 0) <= 40
        and prof.distinct_count > 0
    ):
        roles.append("identifier")
    if t in ("date", "timestamp") or (t == "string" and (date_parse_ratio or 0.0) >= 0.8):
        roles.append("date")
    if t == "boolean" or (prof.distinct_count == 2 and _two_valued_boolean(prof)):
        roles.append("boolean")
    if _is_numeric(t) and "identifier" not in roles and "foreign_key" not in roles:
        if _PCT_NAME.search(name):
            roles.append("percentage")
        elif _CURRENCY_NAME.search(name):
            roles.append("currency")
        elif _COUNT_NAME.search(name) and t == "integer":
            roles.append("count")
        if not any(r in roles for r in ("boolean",)):
            roles.append("measure")
    emails = [
        v
        for v in prof.top_values
        if isinstance(v.value, str) and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v.value)
    ]
    if t == "string" and prof.top_values and len(emails) >= 0.6 * len(prof.top_values):
        roles.append("email")
    if t == "string" and not ({"identifier", "date", "boolean", "email"} & set(roles)):
        if _GEO_NAME.search(name):
            roles.append("geo")
        if (prof.max_length or 0) > 60 and prof.distinct_pct > 0.5:
            roles.append("text")
        elif prof.distinct_count <= 100 or prof.distinct_pct <= 0.05:
            roles.append("category")
    if _GEO_NAME.search(name) and "geo" not in roles and t == "string":
        roles.append("geo")
    return list(dict.fromkeys(roles))


def _is_primary_key_name(name: str, table: str | None) -> bool:
    n = name.lower()
    if n == "id":
        return True
    if not table:
        return False
    t = table.lower()
    singular = t[:-3] + "y" if t.endswith("ies") else (t[:-1] if t.endswith("s") else t)
    return n in (f"{singular}_id", f"{t}_id", f"{singular}_key", f"{singular}_code")


def _two_valued_boolean(prof: ColumnProfile) -> bool:
    vals = {str(v.value).strip().lower() for v in prof.top_values}
    return (
        vals <= {"0", "1"}
        or vals <= {"y", "n"}
        or vals <= {"yes", "no"}
        or vals <= {"true", "false"}
        or vals <= {"t", "f"}
    )


def profile_table(
    store: WorkspaceStore,
    table: str,
    *,
    sample_rows: int | None = None,
    top_n: int = 10,
    today: dt.date | None = None,
) -> TableProfile:
    """Profile every column of ``table``. ``sample_rows`` profiles a reproducible sample."""
    started = time.perf_counter()
    today = today or dt.date.today()
    info = store.describe(table)
    total_rows = info.row_count or 0
    q = quote_ident(table)
    sampled = sample_rows is not None and total_rows > sample_rows
    src = (
        f"(SELECT * FROM {q} USING SAMPLE reservoir({int(sample_rows or 0)} ROWS) REPEATABLE (42)) AS _s"
        if sampled
        else f"{q} AS _s"
    )
    n = int(store.execute_read(f"SELECT count(*) FROM {src}").scalar() or 0)

    columns: list[ColumnProfile] = []
    issues: list[ProfileIssue] = []
    for col in info.columns:
        prof, col_issues = _profile_column(store, src, col.name, col.type, n, top_n, today, table)
        columns.append(prof)
        issues.extend(col_issues)

    dup_rows = 0
    if n and columns:
        dup_rows = int(
            store.execute_read(
                f"SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT * FROM {src}) AS d) FROM {src}"
            ).scalar()
            or 0
        )
        if dup_rows > 0:
            sample = store.execute_read(
                f"SELECT *, count(*) AS _copies FROM {src} GROUP BY ALL HAVING count(*) > 1 ORDER BY _copies DESC LIMIT 5"
            )
            issues.insert(
                0,
                ProfileIssue(
                    code="duplicate_rows",
                    severity="warning",
                    message=f"{dup_rows} rows are exact duplicates of another row ({dup_rows / n:.1%})",
                    count=dup_rows,
                    evidence={"rule": "count(*) - count(DISTINCT *)", "rows": n},
                    sample_values=sample.to_records(),
                ),
            )
    version = store.table_version(table)
    return TableProfile(
        table=table,
        row_count=total_rows,
        column_count=len(info.columns),
        columns=columns,
        issues=issues,
        duplicate_row_count=dup_rows,
        sampled=sampled,
        sample_size=n if sampled else None,
        profiled_at=dt.datetime.now(dt.UTC),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        version=version,
    )


def _profile_column(
    store: WorkspaceStore,
    src: str,
    name: str,
    typ: str,
    n: int,
    top_n: int,
    today: dt.date,
    table: str,
) -> tuple[ColumnProfile, list[ProfileIssue]]:
    c = quote_ident(name)
    inferred = _inferred(typ)
    numeric = _is_numeric(inferred)
    is_str = inferred == "string"
    is_temporal = inferred in ("date", "timestamp")
    parts = ["count(*) AS n", f"count({c}) AS nn", f"count(DISTINCT {c}) AS nd"]
    if numeric or is_temporal or is_str:
        parts += [f"min({c}) AS mn", f"max({c}) AS mx"]
    if numeric:
        parts += [
            f"avg({c}::DOUBLE) AS mean",
            f"median({c}::DOUBLE) AS med",
            f"stddev_samp({c}::DOUBLE) AS sd",
            f"quantile_cont({c}::DOUBLE, [0.05, 0.25, 0.5, 0.75, 0.95]) AS qs",
            f"count(*) FILTER (WHERE {c} = 0) AS zeros",
            f"count(*) FILTER (WHERE {c} < 0) AS negs",
        ]
    if is_str:
        parts += [
            f"min(length({c})) AS minlen",
            f"max(length({c})) AS maxlen",
            f"count(*) FILTER (WHERE {c} <> trim({c})) AS padded",
            f"count(DISTINCT lower(trim({c}))) AS nd_norm",
            f"count(DISTINCT trim({c})) AS nd_trim",
            f"count(*) FILTER (WHERE TRY_CAST(replace(replace(trim({c}), ',', ''), '$', '') AS DOUBLE) IS NOT NULL) AS num_ok",
            "count(*) FILTER (WHERE coalesce("
            f"TRY_CAST(trim({c}) AS DATE), CAST(try_strptime(trim({c}), '%m/%d/%Y') AS DATE), "
            f"CAST(try_strptime(trim({c}), '%d-%b-%Y') AS DATE), CAST(try_strptime(trim({c}), '%Y/%m/%d') AS DATE)) IS NOT NULL) AS date_ok",
        ]
    if is_temporal:
        parts += [
            f"count(*) FILTER (WHERE CAST({c} AS DATE) > DATE '{today.isoformat()}') AS future",
            f"count(*) FILTER (WHERE CAST({c} AS DATE) < DATE '1900-01-01') AS ancient",
        ]
    rec = store.execute_read(f"SELECT {', '.join(parts)} FROM {src}", limit=1).to_records()[0]
    nn = int(rec["nn"] or 0)
    nd = int(rec["nd"] or 0)
    null_count = n - nn
    top = store.execute_read(
        f"SELECT {c} AS v, count(*) AS k FROM {src} WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(top_n)}"
    )
    top_values = [
        TopValue(value=r[0], count=int(r[1]), pct=round(int(r[1]) / n, 6) if n else 0.0) for r in top.rows
    ]
    quantiles: dict[str, float] = {}
    if numeric and rec.get("qs"):
        for label, v in zip(("p05", "p25", "p50", "p75", "p95"), rec["qs"], strict=False):
            if v is not None:
                quantiles[label] = float(v)
    distinct_pct = nd / nn if nn else 0.0
    if nd <= 1:
        card = "constant"
    elif nn and nd == nn:
        card = "unique"
    elif nd <= 20:
        card = "low"
    elif distinct_pct < 0.5:
        card = "medium"
    else:
        card = "high"
    prof = ColumnProfile(
        name=name,
        type=typ,
        inferred_type=inferred,  # type: ignore[arg-type]
        row_count=n,
        null_count=null_count,
        null_pct=round(null_count / n, 6) if n else 0.0,
        distinct_count=nd,
        distinct_pct=round(distinct_pct, 6),
        min=rec.get("mn"),
        max=rec.get("mx"),
        mean=_f(rec.get("mean")),
        median=_f(rec.get("med")),
        stddev=_f(rec.get("sd")),
        quantiles=quantiles,
        top_values=top_values,
        min_length=rec.get("minlen"),
        max_length=rec.get("maxlen"),
        zero_count=rec.get("zeros"),
        negative_count=rec.get("negs"),
        cardinality=card,  # type: ignore[arg-type]
    )
    date_ratio = (int(rec.get("date_ok") or 0) / nn) if (is_str and nn) else None
    prof.semantic_roles = guess_roles(name, prof, table=table, date_parse_ratio=date_ratio)  # type: ignore[assignment]
    issues = _column_issues(store, src, prof, rec, today, table)
    return prof, issues


def _samples(store: WorkspaceStore, src: str, expr: str, where: str, limit: int = 5) -> list[Any]:
    res = store.execute_read(f"SELECT DISTINCT {expr} AS v FROM {src} WHERE {where} LIMIT {limit}")
    return [r[0] for r in res.rows]


def _column_issues(
    store: WorkspaceStore, src: str, p: ColumnProfile, rec: dict[str, Any], today: dt.date, table: str
) -> list[ProfileIssue]:
    issues: list[ProfileIssue] = []
    c = quote_ident(p.name)
    n = p.row_count
    nn = n - p.null_count
    roles = set(p.semantic_roles)
    if n == 0:
        return issues
    if nn == 0:
        issues.append(
            ProfileIssue(
                code="empty_column",
                severity="warning",
                column=p.name,
                message="column is entirely empty",
                count=n,
            )
        )
        return issues
    if p.distinct_count == 1 and n > 1:
        issues.append(
            ProfileIssue(
                code="constant_column",
                severity="info",
                column=p.name,
                message=f"column has a single value ({p.top_values[0].value!r})",
                evidence={"distinct": 1},
            )
        )
    # duplicate identifiers
    key_like = (
        "identifier" in roles
        or _is_primary_key_name(p.name, table)
        or (bool(_ID_NAME.search(p.name)) and p.distinct_pct >= 0.95)
    )
    if key_like and "foreign_key" not in roles and p.distinct_count < nn:
        dups = nn - p.distinct_count
        issues.append(
            ProfileIssue(
                code="duplicate_ids",
                severity="error",
                column=p.name,
                message=f"identifier {p.name} has {dups} duplicate values",
                count=dups,
                evidence={"non_null": nn, "distinct": p.distinct_count},
                sample_values=_samples(
                    store, src, c, f"{c} IN (SELECT {c} FROM {src} GROUP BY {c} HAVING count(*) > 1)"
                ),
            )
        )
    # unexpected nulls
    if p.null_count > 0:
        if key_like or "foreign_key" in roles:
            issues.append(
                ProfileIssue(
                    code="unexpected_nulls",
                    severity="warning",
                    column=p.name,
                    message=f"key column {p.name} has {p.null_count} nulls ({p.null_pct:.1%})",
                    count=p.null_count,
                    evidence={"null_pct": p.null_pct},
                )
            )
        elif p.null_pct < 0.05:
            issues.append(
                ProfileIssue(
                    code="unexpected_nulls",
                    severity="info",
                    column=p.name,
                    message=f"{p.null_count} nulls in an otherwise populated column ({p.null_pct:.2%})",
                    count=p.null_count,
                    evidence={"null_pct": p.null_pct},
                )
            )
    if p.inferred_type == "string":
        padded = int(rec.get("padded") or 0)
        if padded:
            issues.append(
                ProfileIssue(
                    code="whitespace_padding",
                    severity="warning",
                    column=p.name,
                    message=f"{padded} values have leading or trailing whitespace",
                    count=padded,
                    sample_values=_samples(store, src, c, f"{c} <> trim({c})"),
                    evidence={"rule": "value <> trim(value)"},
                )
            )
        nd_trim, nd_norm = int(rec.get("nd_trim") or 0), int(rec.get("nd_norm") or 0)
        if nd_norm < nd_trim:
            groups = store.execute_read(
                f"SELECT lower(trim({c})) AS k, list(DISTINCT trim({c})) AS variants FROM {src} WHERE {c} IS NOT NULL "
                f"GROUP BY 1 HAVING count(DISTINCT trim({c})) > 1 ORDER BY 1 LIMIT 5"
            )
            issues.append(
                ProfileIssue(
                    code="inconsistent_casing",
                    severity="warning",
                    column=p.name,
                    message=f"{nd_trim - nd_norm} values differ from another only by letter case",
                    count=nd_trim - nd_norm,
                    sample_values=[r[1] for r in groups.rows],
                    evidence={"distinct": nd_trim, "distinct_case_insensitive": nd_norm},
                )
            )
        num_ok, date_ok = int(rec.get("num_ok") or 0), int(rec.get("date_ok") or 0)
        if nn and 0.5 <= num_ok / nn < 1.0:
            issues.append(
                ProfileIssue(
                    code="mixed_types",
                    severity="warning",
                    column=p.name,
                    message=f"text column is {num_ok / nn:.0%} numeric; {nn - num_ok} values are not numbers",
                    count=nn - num_ok,
                    sample_values=_samples(
                        store,
                        src,
                        c,
                        f"{c} IS NOT NULL AND TRY_CAST(replace(replace(trim({c}), ',', ''), '$', '') AS DOUBLE) IS NULL",
                    ),
                    evidence={"numeric_values": num_ok, "non_null": nn},
                )
            )
        date_named = bool(_DATE_NAME.search(p.name))
        if nn and date_ok < nn and (date_ok / nn >= 0.5 or (date_named and date_ok > 0)):
            bad_where = (
                f"{c} IS NOT NULL AND coalesce(TRY_CAST(trim({c}) AS DATE), CAST(try_strptime(trim({c}), '%m/%d/%Y') AS DATE), "
                f"CAST(try_strptime(trim({c}), '%d-%b-%Y') AS DATE), CAST(try_strptime(trim({c}), '%Y/%m/%d') AS DATE)) IS NULL"
            )
            issues.append(
                ProfileIssue(
                    code="malformed_dates",
                    severity="warning",
                    column=p.name,
                    message=f"{nn - date_ok} values in a date-like column do not parse as dates",
                    count=nn - date_ok,
                    sample_values=_samples(store, src, c, bad_where),
                    evidence={"parsable": date_ok, "non_null": nn},
                )
            )
        if (
            "category" not in roles
            and "identifier" not in roles
            and "text" not in roles
            and p.distinct_count > 50
            and p.distinct_pct > 0.5
        ):
            issues.append(
                ProfileIssue(
                    code="high_cardinality",
                    severity="info",
                    column=p.name,
                    message=f"{p.distinct_count} distinct values ({p.distinct_pct:.0%} of rows); not useful as a grouping dimension",
                    count=p.distinct_count,
                )
            )
    if _is_numeric(p.inferred_type) and "identifier" not in roles and "foreign_key" not in roles:
        q1, q3 = p.quantiles.get("p25"), p.quantiles.get("p75")
        if q1 is not None and q3 is not None and q3 > q1:
            iqr = q3 - q1
            lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
            cnt = int(
                store.execute_read(f"SELECT count(*) FROM {src} WHERE {c} < {lo!r} OR {c} > {hi!r}").scalar()
                or 0
            )
            if cnt and cnt / nn < 0.05:
                issues.append(
                    ProfileIssue(
                        code="outliers",
                        severity="info",
                        column=p.name,
                        message=f"{cnt} values fall outside 3x the interquartile range [{lo:.4g}, {hi:.4g}]",
                        count=cnt,
                        sample_values=_samples(store, src, c, f"{c} < {lo!r} OR {c} > {hi!r}"),
                        evidence={
                            "q1": q1,
                            "q3": q3,
                            "lower_fence": lo,
                            "upper_fence": hi,
                            "rule": "Tukey fences, k=3",
                        },
                    )
                )
        negs = int(p.negative_count or 0)
        if negs and (_NONNEG_NAME.search(p.name) or "count" in roles):
            issues.append(
                ProfileIssue(
                    code="impossible_values",
                    severity="warning",
                    column=p.name,
                    message=f"{negs} negative values in {p.name}, which should not be negative",
                    count=negs,
                    sample_values=_samples(store, src, c, f"{c} < 0"),
                    evidence={"rule": f"{p.name} >= 0"},
                )
            )
        if "percentage" in roles:
            # the scale is decided by the typical value: a median above 1.5 means 0-100 percentages
            scale_100 = (p.median or 0.0) > 1.5
            bad_where = f"{c} < 0 OR {c} > 100" if scale_100 else f"{c} < 0 OR {c} > 1"
            bad = int(store.execute_read(f"SELECT count(*) FROM {src} WHERE {bad_where}").scalar() or 0)
            if bad:
                issues.append(
                    ProfileIssue(
                        code="impossible_values",
                        severity="warning",
                        column=p.name,
                        message=f"{bad} percentage values outside the valid range",
                        count=bad,
                        sample_values=_samples(store, src, c, bad_where),
                        evidence={"rule": bad_where, "scale": "0-100" if scale_100 else "0-1"},
                    )
                )
    if p.inferred_type in ("date", "timestamp"):
        fut, anc = int(rec.get("future") or 0), int(rec.get("ancient") or 0)
        if fut:
            issues.append(
                ProfileIssue(
                    code="future_dates",
                    severity="warning",
                    column=p.name,
                    message=f"{fut} dates are after {today.isoformat()}",
                    count=fut,
                    sample_values=_samples(store, src, c, f"CAST({c} AS DATE) > DATE '{today.isoformat()}'"),
                    evidence={"today": today.isoformat()},
                )
            )
        if anc:
            issues.append(
                ProfileIssue(
                    code="impossible_values",
                    severity="warning",
                    column=p.name,
                    message=f"{anc} dates are before 1900",
                    count=anc,
                    sample_values=_samples(store, src, c, f"CAST({c} AS DATE) < DATE '1900-01-01'"),
                )
            )
    return issues
