"""Global search across workspace objects (datasets, columns, metrics, dimensions, glossary,
investigations, findings, dashboards, reports, saved queries, notebooks)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import DbDep, ViewerCtx
from ..models import (
    Dashboard,
    Dataset,
    DimensionRecord,
    Finding,
    GlossaryTermRecord,
    Investigation,
    MetricRecord,
    MetricVersion,
    Notebook,
    Report,
    SavedQuery,
)
from ..schemas.common import ERROR_RESPONSES

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["search"], responses=ERROR_RESPONSES)

KINDS = (
    "dataset",
    "column",
    "metric",
    "dimension",
    "glossary",
    "investigation",
    "finding",
    "dashboard",
    "report",
    "saved_query",
    "notebook",
)


class SearchHit(BaseModel):
    id: str
    kind: str
    title: str
    subtitle: str | None = None
    snippet: str | None = None
    ref_id: str
    parent_id: str | None = None
    score: float


def _score(q: str, fields: list[tuple[str | None, float]]) -> tuple[float, str | None]:
    best, where = 0.0, None
    for text, weight in fields:
        if not text:
            continue
        t = text.lower()
        if t == q:
            s = 1.0
        elif t.startswith(q):
            s = 0.8
        elif f" {q}" in t or f"_{q}" in t:
            s = 0.65
        elif q in t:
            s = 0.5
        elif all(tok in t for tok in q.split()):
            s = 0.4
        else:
            continue
        if s * weight > best:
            best, where = s * weight, text
    return best, where


def _snippet(text: str | None, q: str, width: int = 90) -> str | None:
    if not text:
        return None
    i = text.lower().find(q)
    if i < 0:
        return text[:width]
    start = max(0, i - width // 3)
    return (
        ("..." if start else "") + text[start : start + width] + ("..." if start + width < len(text) else "")
    )


@router.get("/search", response_model=list[SearchHit], operation_id="search")
def search(
    ctx: ViewerCtx,
    db: DbDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    kinds: Annotated[str | None, Query(description="Comma-separated kinds")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[SearchHit]:
    needle = q.strip().lower()
    wanted = set(kinds.split(",")) & set(KINDS) if kinds else set(KINDS)
    ws = ctx.workspace_id
    hits: list[SearchHit] = []

    def add(
        kind: str,
        rid: str,
        title: str,
        fields: list[tuple[str | None, float]],
        subtitle: str | None = None,
        parent: str | None = None,
        hit_id: str | None = None,
    ) -> None:
        s, where = _score(needle, fields)
        if s > 0:
            hits.append(
                SearchHit(
                    id=hit_id or f"{kind}:{rid}",
                    kind=kind,
                    title=title,
                    subtitle=subtitle,
                    snippet=_snippet(where, needle) if where and where != title else None,
                    ref_id=rid,
                    parent_id=parent,
                    score=round(s, 3),
                )
            )

    if {"dataset", "column"} & wanted:
        for d in db.scalars(select(Dataset).where(Dataset.workspace_id == ws)):
            if "dataset" in wanted:
                add(
                    "dataset",
                    d.id,
                    d.name,
                    [(d.name, 1.0), (d.table_name, 0.95), (d.description, 0.6)],
                    subtitle=f"{d.table_name} · {d.row_count:,} rows",
                )
            if "column" in wanted:
                for c in d.columns or []:
                    add(
                        "column",
                        d.id,
                        f"{d.table_name}.{c['name']}",
                        [(c["name"], 0.85)],
                        subtitle=f"{c.get('type')} in {d.name}",
                        parent=d.id,
                        hit_id=f"column:{d.id}:{c['name']}",
                    )
    if "metric" in wanted:
        rows = db.execute(
            select(MetricRecord, MetricVersion)
            .join(MetricVersion, MetricVersion.id == MetricRecord.current_version_id)
            .where(MetricRecord.workspace_id == ws, MetricRecord.archived.is_(False))
        ).all()
        for rec, ver in rows:
            defn: dict[str, Any] = ver.definition
            add(
                "metric",
                rec.name,
                defn.get("label") or defn.get("name") or rec.name,
                [
                    (rec.name, 1.0),
                    (defn.get("name"), 1.0),
                    (defn.get("label"), 1.0),
                    (" ".join(defn.get("synonyms") or []), 0.8),
                    (defn.get("description"), 0.6),
                ],
                subtitle=f"v{ver.version_no} · {defn.get('kind')}",
            )
    if "dimension" in wanted:
        for r in db.scalars(select(DimensionRecord).where(DimensionRecord.workspace_id == ws)):
            spec = r.spec
            add(
                "dimension",
                r.name,
                spec.get("label") or r.name,
                [
                    (r.name, 0.95),
                    (spec.get("label"), 0.95),
                    (" ".join(spec.get("synonyms") or []), 0.7),
                    (spec.get("description"), 0.5),
                ],
                subtitle=f"{spec.get('type')} on {spec.get('entity')}",
            )
    if "glossary" in wanted:
        for g in db.scalars(select(GlossaryTermRecord).where(GlossaryTermRecord.workspace_id == ws)):
            add(
                "glossary",
                g.id,
                g.term,
                [
                    (g.term, 1.0),
                    (" ".join(g.spec.get("synonyms") or []), 0.8),
                    (g.spec.get("definition"), 0.6),
                ],
                subtitle="Glossary term",
            )
    if "investigation" in wanted:
        for i in db.scalars(select(Investigation).where(Investigation.workspace_id == ws)):
            add(
                "investigation",
                i.id,
                i.title,
                [(i.title, 0.9), (i.question, 0.9), ((i.document or {}).get("brief_answer"), 0.5)],
                subtitle=i.status,
            )
    if "finding" in wanted:
        for f in db.scalars(select(Finding).where(Finding.workspace_id == ws)):
            add(
                "finding",
                f.id,
                f.statement[:140],
                [(f.statement, 0.85), (f.notes, 0.4)],
                subtitle=f"{f.statement_type.replace('_', ' ')} · {f.status}",
                parent=f.investigation_id,
            )
    if "dashboard" in wanted:
        for x in db.scalars(select(Dashboard).where(Dashboard.workspace_id == ws)):
            add("dashboard", x.id, x.name, [(x.name, 0.9), (x.description, 0.5)], subtitle="Dashboard")
    if "report" in wanted:
        for rep in db.scalars(select(Report).where(Report.workspace_id == ws)):
            add("report", rep.id, rep.title, [(rep.title, 0.9)], subtitle=f"{rep.kind} · {rep.status}")
    if "saved_query" in wanted:
        for sq in db.scalars(select(SavedQuery).where(SavedQuery.workspace_id == ws)):
            add(
                "saved_query",
                sq.id,
                sq.name,
                [(sq.name, 0.9), (sq.description, 0.5), (sq.sql, 0.4)],
                subtitle="Saved query",
            )
    if "notebook" in wanted:
        for nb in db.scalars(select(Notebook).where(Notebook.workspace_id == ws)):
            add("notebook", nb.id, nb.title, [(nb.title, 0.9), (nb.description, 0.5)], subtitle="Notebook")
    hits.sort(key=lambda h: (-h.score, KINDS.index(h.kind), h.title.lower()))
    return hits[:limit]
