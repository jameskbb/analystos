"""Join planning over *approved* relationships and empirical join analysis.

Terminology: a join step from entity ``L`` to entity ``R`` is *fan-out safe* when each
``L`` row matches at most one ``R`` row (``many_to_one`` or ``one_to_one``). Metric
queries only ever join from a metric's base entity along safe steps; anything else
is either rewritten as a semi-join (filters) or refused (group-by dimensions).
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import Relationship, SemanticModel

if TYPE_CHECKING:  # pragma: no cover
    from ..store import WorkspaceStore

__all__ = [
    "JoinStep",
    "JoinPath",
    "JoinAnalysis",
    "JoinPlanningError",
    "analyze_join",
    "plan_joins",
    "find_path",
    "invert_cardinality",
]

Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]
_SAFE = ("many_to_one", "one_to_one")


class JoinPlanningError(ValueError):
    """No acceptable join path exists between entities."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JoinStep(_Model):
    left_entity: str
    left_col: str
    right_entity: str
    right_col: str
    cardinality: Cardinality
    relationship_id: str

    @property
    def fanout_safe(self) -> bool:
        return self.cardinality in _SAFE


class JoinPath(_Model):
    from_entity: str
    to_entity: str
    steps: list[JoinStep] = Field(default_factory=list)
    fanout_safe: bool = True
    ambiguous: bool = False

    def describe(self) -> str:
        if not self.steps:
            return self.from_entity
        parts = [self.from_entity]
        for s in self.steps:
            arrow = {"many_to_one": "N:1", "one_to_one": "1:1", "one_to_many": "1:N", "many_to_many": "N:M"}[
                s.cardinality
            ]
            parts.append(f"-[{s.left_col}={s.right_col} {arrow}]-> {s.right_entity}")
        return " ".join(parts)


class JoinAnalysis(_Model):
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    declared_cardinality: str | None = None
    observed_cardinality: Cardinality
    left_rows: int
    right_rows: int
    joined_rows: int
    fanout_factor: float
    orphan_pct: float
    orphan_count: int
    left_key_unique: bool
    right_key_unique: bool
    warnings: list[str] = Field(default_factory=list)
    sql: str = ""


def invert_cardinality(c: Cardinality) -> Cardinality:
    return {"one_to_many": "many_to_one", "many_to_one": "one_to_many"}.get(c, c)  # type: ignore[return-value]


def _edges(model: SemanticModel) -> dict[str, list[JoinStep]]:
    graph: dict[str, list[JoinStep]] = {}
    for r in model.relationships:
        if not r.approved or r.cardinality == "many_to_many":
            continue
        graph.setdefault(r.from_entity, []).append(
            JoinStep(
                left_entity=r.from_entity,
                left_col=r.from_col,
                right_entity=r.to_entity,
                right_col=r.to_col,
                cardinality=r.cardinality,
                relationship_id=r.id,
            )
        )
        graph.setdefault(r.to_entity, []).append(
            JoinStep(
                left_entity=r.to_entity,
                left_col=r.to_col,
                right_entity=r.from_entity,
                right_col=r.from_col,
                cardinality=invert_cardinality(r.cardinality),
                relationship_id=r.id,
            )
        )
    return graph


def find_path(model: SemanticModel, start: str, target: str, *, safe_only: bool = True) -> JoinPath | None:
    """Shortest path from ``start`` to ``target`` over approved, non many-to-many relationships.

    With ``safe_only`` only fan-out safe steps (N:1, 1:1) are traversed. Returns
    ``None`` when no path exists. ``ambiguous`` is set when several distinct
    shortest paths exist (the first in model order is chosen).
    """
    if start == target:
        return JoinPath(from_entity=start, to_entity=target, steps=[], fanout_safe=True)
    graph = _edges(model)
    queue: deque[tuple[str, list[JoinStep]]] = deque([(start, [])])
    best: list[list[JoinStep]] = []
    visited_depth: dict[str, int] = {start: 0}
    while queue:
        node, path = queue.popleft()
        if best and len(path) >= len(best[0]):
            break
        for step in graph.get(node, []):
            if safe_only and not step.fanout_safe:
                continue
            nxt = step.right_entity
            if any(s.left_entity == nxt for s in path) or nxt == start:
                continue
            new_path = [*path, step]
            if nxt == target:
                best.append(new_path)
                continue
            depth = len(new_path)
            if nxt in visited_depth and visited_depth[nxt] < depth:
                continue
            visited_depth[nxt] = depth
            queue.append((nxt, new_path))
    if not best:
        return None
    shortest = min(len(p) for p in best)
    candidates = [p for p in best if len(p) == shortest]
    distinct = {tuple(s.relationship_id for s in p) for p in candidates}
    chosen = candidates[0]
    return JoinPath(
        from_entity=start,
        to_entity=target,
        steps=chosen,
        fanout_safe=all(s.fanout_safe for s in chosen),
        ambiguous=len(distinct) > 1,
    )


def explain_no_path(model: SemanticModel, a: str, b: str) -> str:
    """Human explanation of why ``a`` and ``b`` cannot be joined safely."""
    unapproved = [r for r in model.relationships if not r.approved and {r.from_entity, r.to_entity} & {a, b}]
    m2m = [
        r
        for r in model.relationships
        if r.cardinality == "many_to_many" and {r.from_entity, r.to_entity} == {a, b}
    ]
    parts = [f"no approved relationship path between {a!r} and {b!r}"]
    if m2m:
        parts.append(
            "the only relationship is many-to-many ("
            + ", ".join(r.id for r in m2m)
            + "), which would double count; model a bridge entity with two many-to-one relationships"
        )
    if unapproved:
        parts.append(
            "unapproved relationships exist ("
            + ", ".join(r.id for r in unapproved)
            + "); approve one to use it"
        )
    return "; ".join(parts)


def plan_joins(model: SemanticModel, entities: list[str], base: str | None = None) -> list[JoinPath]:
    """Plan fan-out safe joins connecting ``entities``.

    ``base`` is the entity whose grain is preserved (a metric's entity). When omitted,
    the finest-grained entity that reaches every other entity along many-to-one paths
    is chosen. Raises :class:`JoinPlanningError` when no such base exists.
    """
    names = list(dict.fromkeys(entities))
    for n in names + ([base] if base else []):
        model.get_entity(n)
    candidates = [base] if base else names
    errors: list[str] = []
    for cand in candidates:
        paths: list[JoinPath] = []
        ok = True
        for other in names:
            if other == cand:
                continue
            p = find_path(model, cand, other, safe_only=True)
            if p is None:
                ok = False
                unsafe = find_path(model, cand, other, safe_only=False)
                if unsafe is not None:
                    errors.append(
                        f"joining {cand!r} to {other!r} goes through a one-to-many step ({unsafe.describe()}) "
                        f"and would repeat {cand!r} rows"
                    )
                else:
                    errors.append(explain_no_path(model, cand, other))
                break
            paths.append(p)
        if ok:
            return paths
    raise JoinPlanningError(
        "cannot join entities " + ", ".join(repr(n) for n in names) + " without fan-out: " + "; ".join(errors)
    )


def _table_of(model: SemanticModel | None, entity_or_table: str) -> str:
    if model is None:
        return entity_or_table
    try:
        return model.get_entity(entity_or_table).table
    except ValueError:
        return entity_or_table


def analyze_join(
    store: WorkspaceStore, rel: Relationship, model: SemanticModel | None = None
) -> JoinAnalysis:
    """Measure how a relationship behaves on the actual data.

    ``rel.from_entity``/``rel.to_entity`` are resolved to tables through ``model``
    when given, otherwise they are treated as table names.
    """
    from ..store import quote_ident

    lt, rt = _table_of(model, rel.from_entity), _table_of(model, rel.to_entity)
    lc, rc = rel.from_col, rel.to_col
    ql, qr = _qualified(lt), _qualified(rt)
    lcol, rcol, r2col = f"l.{quote_ident(lc)}", f"r.{quote_ident(rc)}", f"r2.{quote_ident(rc)}"
    ltype = _col_type(store, lt, lc)
    rtype = _col_type(store, rt, rc)
    warnings: list[str] = []
    if ltype != rtype:
        lcol, rcol, r2col = (f"CAST({c} AS VARCHAR)" for c in (lcol, rcol, r2col))
        warnings.append(f"key types differ ({lc}: {ltype}, {rc}: {rtype}); compared as text")
    sql = f"""
WITH l_stats AS (
  SELECT count(*) AS n, count({lcol}) AS nn, count(DISTINCT {lcol}) AS nd FROM {ql} AS l
), r_stats AS (
  SELECT count(*) AS n, count({rcol}) AS nn, count(DISTINCT {rcol}) AS nd FROM {qr} AS r
), j AS (
  SELECT count(*) AS joined,
         count(*) FILTER (WHERE r_match IS NULL) AS orphans
  FROM (
    SELECT l.*, r._aos_match AS r_match
    FROM {ql} AS l
    LEFT JOIN (SELECT {r2col} AS k, 1 AS _aos_match FROM {qr} AS r2) AS r
      ON {lcol} = r.k
  ) AS x
)
SELECT l_stats.n, l_stats.nn, l_stats.nd, r_stats.n, r_stats.nn, r_stats.nd, j.joined, j.orphans
FROM l_stats, r_stats, j
""".strip()
    res = store.execute_read(sql, limit=1)
    ln, lnn, lnd, rn, rnn, rnd, joined, orphans = (int(v or 0) for v in res.rows[0])
    left_unique = lnn == lnd
    right_unique = rnn == rnd
    if left_unique and right_unique:
        observed: Cardinality = "one_to_one"
    elif right_unique:
        observed = "many_to_one"
    elif left_unique:
        observed = "one_to_many"
    else:
        observed = "many_to_many"
    fanout = joined / ln if ln else 1.0
    orphan_pct = orphans / ln if ln else 0.0
    if observed == "many_to_many":
        warnings.append(
            f"many-to-many: neither {lt}.{lc} nor {rt}.{rc} is unique; joining would multiply rows "
            f"(fan-out {fanout:.2f}x). Do not use for metric joins."
        )
    elif fanout > 1.0 + 1e-9:
        warnings.append(
            f"join multiplies {lt} rows by {fanout:.2f}x on average; metrics on {lt} would be double counted"
        )
    if rel.cardinality and observed != rel.cardinality:
        warnings.append(f"declared cardinality {rel.cardinality} but data shows {observed}")
    if orphan_pct > 0:
        warnings.append(f"{orphans} {lt} rows ({orphan_pct:.1%}) have no matching {rt} row")
    return JoinAnalysis(
        from_table=lt,
        from_col=lc,
        to_table=rt,
        to_col=rc,
        declared_cardinality=rel.cardinality,
        observed_cardinality=observed,
        left_rows=ln,
        right_rows=rn,
        joined_rows=joined,
        fanout_factor=round(fanout, 6),
        orphan_pct=round(orphan_pct, 6),
        orphan_count=orphans,
        left_key_unique=left_unique,
        right_key_unique=right_unique,
        warnings=warnings,
        sql=res.sql,
    )


def _qualified(table: str) -> str:
    from ..store import quote_ident

    return ".".join(quote_ident(p) for p in table.split("."))


def _col_type(store: WorkspaceStore, table: str, col: str) -> str:
    name = table.split(".")[-1]
    for c in store.columns(name):
        if c.name == col:
            return c.type
    raise JoinPlanningError(f"column {col!r} not found in table {table!r}")
