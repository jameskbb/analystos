"""Relationship discovery between tables in the workspace store.

Suggestions combine name signals, key uniqueness, type compatibility and measured
value overlap. They are *suggestions*: the semantic compiler only uses relationships
an analyst has approved.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .semantic.models import Relationship
from .store import WorkspaceStore, quote_ident
from .types import ColumnInfo

__all__ = ["RelationshipSuggestion", "discover", "to_relationship"]

_KEY_TYPES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "VARCHAR",
    "UUID",
)
_ID_NAME = re.compile(r"(^id$|_id$|_key$|_code$|^code$|^sku$|_sku$|_number$|_no$|_uuid$)", re.I)


class RelationshipSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_col: str
    to_table: str
    to_col: str
    cardinality: Literal["one_to_one", "many_to_one", "many_to_many"]
    confidence: Literal["high", "medium", "low"]
    signals: list[str] = Field(default_factory=list)
    overlap_pct: float
    orphan_count: int = 0
    from_distinct: int = 0
    to_distinct: int = 0
    requires_trim: bool = False

    @property
    def id(self) -> str:
        return f"{self.from_table}.{self.from_col}->{self.to_table}.{self.to_col}"


def _singular(name: str) -> str:
    n = name.lower()
    if n.endswith("ies"):
        return n[:-3] + "y"
    if n.endswith("sses"):
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss"):
        return n[:-1]
    return n


def _name_signal(from_table: str, from_col: str, to_table: str, to_col: str) -> tuple[int, str | None]:
    """0 = none, 1 = weak, 2 = strong."""
    fc, tc = from_col.lower(), to_col.lower()
    single = _singular(to_table)
    if fc == tc and _ID_NAME.search(tc):
        return 2, f"same column name {from_col!r}"
    if tc in ("id", "key", "code") and fc in (f"{single}_{tc}", f"{to_table.lower()}_{tc}", f"{single}{tc}"):
        return 2, f"{from_col!r} is named after table {to_table!r}"
    if fc == tc:
        return 1, f"same column name {from_col!r}"
    if fc.endswith(tc) and _ID_NAME.search(tc) and len(tc) >= 4:
        return 1, f"{from_col!r} ends with {to_col!r}"
    if single in fc and _ID_NAME.search(fc):
        return 1, f"{from_col!r} mentions {to_table!r}"
    return 0, None


def _type_family(t: str) -> str:
    u = t.upper()
    if u.startswith("VARCHAR") or u == "UUID":
        return "text"
    return "int"


def _stats(store: WorkspaceStore, table: str, cols: list[ColumnInfo]) -> dict[str, tuple[int, int, int]]:
    if not cols:
        return {}
    parts = ["count(*) AS _n"]
    for i, c in enumerate(cols):
        q = quote_ident(c.name)
        parts.append(f"count({q}) AS nn{i}")
        parts.append(f"count(DISTINCT {q}) AS nd{i}")
    rec = store.execute_read(f"SELECT {', '.join(parts)} FROM {quote_ident(table)}", limit=1).rows[0]
    n = int(rec[0])
    return {c.name: (n, int(rec[1 + 2 * i]), int(rec[2 + 2 * i])) for i, c in enumerate(cols)}


def discover(
    store: WorkspaceStore,
    tables: list[str] | None = None,
    *,
    min_overlap: float = 0.5,
) -> list[RelationshipSuggestion]:
    """Suggest join relationships between ``tables`` (default: every table in the store)."""
    names = tables or [t.name for t in store.list_tables(include_row_counts=False)]
    cols: dict[str, list[ColumnInfo]] = {}
    stats: dict[str, dict[str, tuple[int, int, int]]] = {}
    for t in names:
        key_cols = [c for c in store.columns(t) if c.type.upper().split("(")[0] in _KEY_TYPES]
        cols[t] = key_cols
        stats[t] = _stats(store, t, key_cols)
    out: list[RelationshipSuggestion] = []
    for to_t in names:
        for to_c in cols[to_t]:
            n_to, nn_to, nd_to = stats[to_t][to_c.name]
            if nn_to == 0:
                continue
            to_unique = nd_to == nn_to
            for from_t in names:
                if from_t == to_t:
                    continue
                for from_c in cols[from_t]:
                    n_from, nn_from, nd_from = stats[from_t][from_c.name]
                    if nn_from == 0:
                        continue
                    strength, reason = _name_signal(from_t, from_c.name, to_t, to_c.name)
                    idish = bool(_ID_NAME.search(from_c.name)) and bool(_ID_NAME.search(to_c.name))
                    if strength == 0 and not idish:
                        continue
                    if not to_unique and strength < 2:
                        continue
                    from_unique = nd_from == nn_from
                    sug = _measure(
                        store,
                        from_t,
                        from_c,
                        to_t,
                        to_c,
                        nd_from,
                        nd_to,
                        to_unique,
                        from_unique,
                        strength,
                        reason,
                        min_overlap,
                    )
                    if sug is not None:
                        out.append(sug)
    # Two fact tables that share a key (inventory.product_id and order_lines.product_id) are
    # related through the dimension where that key is unique (products); a direct fact-to-fact
    # many-to-many join would multiply rows, so such suggestions are dropped when the shared
    # dimension is found. Other many-to-many pairs stay as low-confidence suggestions.
    parents: dict[tuple[str, str], set[str]] = {}
    for s in out:
        if s.cardinality in ("many_to_one", "one_to_one"):
            parents.setdefault((s.from_table, s.from_col), set()).add(s.to_table)
    out = [
        s
        for s in out
        if s.cardinality != "many_to_many"
        or not (parents.get((s.from_table, s.from_col), set()) & parents.get((s.to_table, s.to_col), set()))
    ]
    # keep one direction per column pair: the table that owns the key name wins
    # (orders.customer_id -> customers.customer_id), then confidence, then overlap
    order = {"high": 0, "medium": 1, "low": 2}
    best: dict[frozenset[str], RelationshipSuggestion] = {}

    def rank(s: RelationshipSuggestion) -> tuple[int, int, float]:
        owns = int(s.to_col.lower() in (f"{_singular(s.to_table)}_id", "id", f"{_singular(s.to_table)}_key"))
        return (-owns, order[s.confidence], -s.overlap_pct)

    for s in out:
        key = frozenset({f"{s.from_table}.{s.from_col}", f"{s.to_table}.{s.to_col}"})
        if key not in best or rank(s) < rank(best[key]):
            best[key] = s
    return sorted(
        best.values(),
        key=lambda s: (order[s.confidence], -s.overlap_pct, s.from_table, s.from_col, s.to_table),
    )


def _measure(
    store: WorkspaceStore,
    from_t: str,
    from_c: ColumnInfo,
    to_t: str,
    to_c: ColumnInfo,
    nd_from: int,
    nd_to: int,
    to_unique: bool,
    from_unique: bool,
    strength: int,
    reason: str | None,
    min_overlap: float,
) -> RelationshipSuggestion | None:
    fq, tq = quote_ident(from_c.name), quote_ident(to_c.name)
    ff, tf = _type_family(from_c.type), _type_family(to_c.type)
    signals: list[str] = []
    if ff != tf:
        cond = "CAST(f.v AS VARCHAR) = CAST(t.k AS VARCHAR)"
        signals.append(f"types differ ({from_c.type} vs {to_c.type}); compared as text")
    else:
        cond = "f.v = t.k"
        signals.append(f"compatible types ({from_c.type} / {to_c.type})")
    trim_clause = trim_join = ""
    if "text" in (ff, tf):
        # also measure matches after trimming whitespace (padded ids are a common mess)
        trim_clause = ", count(DISTINCT f.v) FILTER (WHERE tt.k IS NOT NULL) AS matched_trim"
        trim_join = (
            f" LEFT JOIN (SELECT DISTINCT trim(CAST({tq} AS VARCHAR)) AS k FROM {quote_ident(to_t)}) AS tt "
            "ON trim(CAST(f.v AS VARCHAR)) = tt.k"
        )
    sql = (
        f"SELECT count(DISTINCT f.v) AS nd, count(DISTINCT f.v) FILTER (WHERE t.k IS NOT NULL) AS matched{trim_clause} "
        f"FROM (SELECT DISTINCT {fq} AS v FROM {quote_ident(from_t)} WHERE {fq} IS NOT NULL) AS f "
        f"LEFT JOIN (SELECT DISTINCT {tq} AS k FROM {quote_ident(to_t)}) AS t ON {cond}"
        f"{trim_join}"
    )
    rec = store.execute_read(sql, limit=1).rows[0]
    nd, matched = int(rec[0]), int(rec[1])
    matched_trim = int(rec[2]) if len(rec) > 2 else matched
    if nd == 0:
        return None
    requires_trim = matched_trim > matched
    best = max(matched, matched_trim)
    overlap = best / nd
    if overlap < min_overlap:
        return None
    if strength == 0 and (overlap < 0.95 or nd < 5):
        return None
    orphans = nd - best
    if reason:
        signals.insert(0, f"name: {reason}")
    signals.append(
        f"{to_t}.{to_c.name} is {'unique' if to_unique else 'NOT unique'} ({nd_to} distinct values)"
    )
    signals.append(f"{overlap:.1%} of distinct {from_t}.{from_c.name} values found in {to_t}.{to_c.name}")
    if orphans:
        signals.append(f"{orphans} distinct {from_t}.{from_c.name} values have no match (orphans)")
    if requires_trim:
        signals.append("values only match after trimming whitespace")
    if to_unique:
        cardinality: Literal["one_to_one", "many_to_one", "many_to_many"] = (
            "one_to_one" if from_unique else "many_to_one"
        )
    else:
        cardinality = "many_to_many"
        signals.append("neither side is unique: joining would multiply rows (many-to-many)")
    if cardinality == "many_to_many":
        confidence: Literal["high", "medium", "low"] = "low"
    elif strength == 2 and overlap >= 0.98 and not requires_trim:
        confidence = "high"
    elif (strength >= 1 and overlap >= 0.9) or (overlap >= 0.98 and nd >= 5):
        confidence = "medium"
    else:
        confidence = "low"
    return RelationshipSuggestion(
        from_table=from_t,
        from_col=from_c.name,
        to_table=to_t,
        to_col=to_c.name,
        cardinality=cardinality,
        confidence=confidence,
        signals=signals,
        overlap_pct=round(overlap, 6),
        orphan_count=orphans,
        from_distinct=nd,
        to_distinct=nd_to,
        requires_trim=requires_trim,
    )


def to_relationship(s: RelationshipSuggestion, entity_by_table: dict[str, str] | None = None) -> Relationship:
    """Convert a suggestion into an *unapproved* semantic relationship."""
    ent = entity_by_table or {}
    return Relationship(
        from_entity=ent.get(s.from_table, s.from_table),
        from_col=s.from_col,
        to_entity=ent.get(s.to_table, s.to_table),
        to_col=s.to_col,
        cardinality=s.cardinality,
        approved=False,
        notes="; ".join(s.signals),
    )
