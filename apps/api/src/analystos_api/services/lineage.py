"""Lineage graphs: Finding -> Chart -> Query -> Metric (version) -> Entity -> Dataset -> Source (spec 26, 62)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from analystos_engine.semantic.models import Metric
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ArtifactRecord, Dataset, DatasetVersion, MetricRecord, MetricVersion, Workspace
from .semantic import build_model


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str], dict[str, str]] = {}

    def node(
        self,
        nid: str,
        kind: str,
        label: str,
        *,
        detail: str | None = None,
        ref_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        if nid not in self.nodes:
            self.nodes[nid] = {
                "id": nid,
                "kind": kind,
                "label": label,
                "detail": detail,
                "ref_id": ref_id,
                "meta": meta or {},
            }
        return nid

    def edge(self, src: str, dst: str, label: str) -> None:
        self.edges[(src, dst, label)] = {"from": src, "to": dst, "label": label}

    def graph(self) -> dict[str, Any]:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())}


_VERSION_RE = re.compile(r"^(?P<id>[^@]+)@v(?P<no>\d+)(?::(?P<hash>[0-9a-f]+))?$")


@dataclass
class ResolvedMetric:
    """A metric definition as it was when an artifact was computed."""

    metric: Metric | None
    version_no: int | None
    version_id: str | None
    engine_version_id: str | None
    hash_matches: bool | None = None


@dataclass
class LineageContext:
    """Pinned versions for one artifact: metric id -> engine version string, table -> content hash."""

    metric_versions: dict[str, Any] = field(default_factory=dict)
    dataset_hashes: dict[str, tuple[str, int | None]] = field(default_factory=dict)


def _engine_version(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("engine_version_id")
    return None


def resolve_metric_version(
    db: Session, ws: Workspace, metric_id: str, pinned: Any, cache: dict[str, Any]
) -> ResolvedMetric:
    """The definition recorded on an artifact (``revenue@v1:<hash>``), from the immutable MetricVersion rows.

    Falls back to the current definition only when nothing is pinned (review R-10)."""
    key = f"{metric_id}|{pinned!r}"
    if key in cache:
        return cache[key]
    rec = db.scalar(
        select(MetricRecord).where(MetricRecord.workspace_id == ws.id, MetricRecord.name == metric_id)
    )
    ver: MetricVersion | None = None
    engine_id = _engine_version(pinned)
    wanted_hash: str | None = None
    if rec is not None:
        if isinstance(pinned, dict) and pinned.get("version_id"):
            ver = db.get(MetricVersion, pinned["version_id"])
        m = _VERSION_RE.match(engine_id or "")
        if ver is None and m and m.group("id") == metric_id:
            wanted_hash = m.group("hash")
            ver = db.scalar(
                select(MetricVersion).where(
                    MetricVersion.metric_id == rec.id, MetricVersion.version_no == int(m.group("no"))
                )
            )
        if ver is None and pinned is None and rec.current_version_id:
            ver = db.get(MetricVersion, rec.current_version_id)
    metric = None
    matches = None
    if ver is not None:
        try:
            metric = Metric.model_validate({**ver.definition, "id": metric_id, "version": ver.version_no})
        except ValueError:
            metric = None
        if metric is not None and wanted_hash:
            matches = metric.definition_hash() == wanted_hash
    out = ResolvedMetric(
        metric=metric,
        version_no=ver.version_no if ver else None,
        version_id=ver.id if ver else None,
        engine_version_id=engine_id or (metric.version_id if metric else None),
        hash_matches=matches,
    )
    cache[key] = out
    return out


def _metric_chain(
    g: GraphBuilder,
    db: Session,
    ws: Workspace,
    metric_id: str,
    version: Any,
    cache: dict[str, Any],
    ctx: LineageContext | None = None,
) -> str:
    """Metric (pinned version) -> (input metrics, at their pinned versions) -> entity -> dataset(version) -> source."""
    from analystos_engine.semantic.models import formula_metric_refs

    ctx = ctx or LineageContext()
    built = cache.setdefault("built", build_model(db, ws))
    model = built.model
    res = resolve_metric_version(db, ws, metric_id, version, cache)
    m = res.metric or (model.get_metric(metric_id) if model.has_metric(metric_id) else None)
    label = m.display_name if m else metric_id
    nid = f"metric:{metric_id}@v{res.version_no}" if res.version_no is not None else f"metric:{metric_id}"
    current = built.metric_versions.get(metric_id)
    mid = g.node(
        nid,
        "metric",
        label,
        detail=res.engine_version_id,
        ref_id=metric_id,
        meta={
            "version_no": res.version_no,
            "version_id": res.version_id,
            "engine_version_id": res.engine_version_id,
            "is_current": bool(current and res.version_id == current.version_id),
            "current_version_no": current.version_no if current else None,
            **({"hash_matches": res.hash_matches} if res.hash_matches is not None else {}),
        },
    )
    if m is None:
        return mid
    children: list[str] = []
    if m.kind == "ratio":
        children = [c for c in (m.numerator, m.denominator) if c]
    elif m.kind == "derived":
        children = formula_metric_refs(m.formula or "", model)
    for child in children:
        g.edge(_metric_chain(g, db, ws, child, ctx.metric_versions.get(child), cache, ctx), mid, "input_to")
    ent = None
    if m.kind == "simple" and m.entity:
        try:
            ent = model.get_entity(m.entity)
        except KeyError:
            ent = None
    if ent is not None:
        eid = g.node(
            f"entity:{ent.name}",
            "entity",
            ent.label or ent.name,
            detail=ent.grain_description or None,
            ref_id=ent.name,
            meta={"table": ent.table},
        )
        g.edge(eid, mid, "aggregated_by")
        pinned_ds = ctx.dataset_hashes.get(ent.table)
        g.edge(
            _dataset_chain(
                g,
                db,
                ws.id,
                ent.table,
                pinned_ds[0] if pinned_ds else None,
                pinned_ds[1] if pinned_ds else None,
            ),
            eid,
            "backs",
        )
    return mid


def _dataset_chain(
    g: GraphBuilder,
    db: Session,
    workspace_id: str,
    table: str,
    content_hash: str | None,
    pinned_rows: int | None = None,
) -> str:
    """Dataset node at the version identified by ``content_hash`` (the current version only when none is pinned)."""
    ds = db.scalar(select(Dataset).where(Dataset.workspace_id == workspace_id, Dataset.table_name == table))
    if ds is None:
        return g.node(f"table:{table}", "dataset", table, ref_id=None)
    ver = None
    if content_hash:
        ver = db.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == ds.id, DatasetVersion.content_hash == content_hash)
            .order_by(DatasetVersion.version_no.desc())
            .limit(1)
        )
    pinned_unknown = bool(content_hash) and ver is None
    if ver is None and not content_hash:
        ver = db.get(DatasetVersion, ds.current_version_id) if ds.current_version_id else None
    nid = (
        f"dataset:{ds.id}:{ver.id}"
        if ver
        else (f"dataset:{ds.id}:{content_hash[:16]}" if content_hash else f"dataset:{ds.id}")
    )
    detail = (
        f"{table} v{ver.version_no}"
        if ver
        else (f"{table} (unrecorded version)" if pinned_unknown else table)
    )
    did = g.node(
        nid,
        "dataset",
        ds.name,
        detail=detail,
        ref_id=ds.id,
        meta={
            "table": table,
            "version_id": ver.id if ver else None,
            "version_no": ver.version_no if ver else None,
            "content_hash": ver.content_hash if ver else content_hash,
            "row_count": ver.row_count if ver else (pinned_rows if pinned_unknown else ds.row_count),
            "is_current": bool(ver and ver.id == ds.current_version_id),
        },
    )
    src = ds.source_ref or {}
    if src.get("filename"):
        sid = g.node(f"source:{ds.id}", "source_file", str(src["filename"]), ref_id=src.get("upload_id"))
        g.edge(sid, did, "ingested_into")
    elif src.get("table"):
        sid = g.node(
            f"source:{ds.id}",
            "source_table",
            f"{src.get('schema')}.{src.get('table')}",
            ref_id=ds.data_source_id,
        )
        g.edge(sid, did, "snapshot_of")
    elif ds.source_kind == "demo":
        sid = g.node(f"source:{ds.id}", "source_file", str(src.get("source_file") or f"{table} (demo)"))
        g.edge(sid, did, "ingested_into")
    return did


def _referenced_tables(sql: str) -> list[str]:
    from analystos_engine.sqlsafety import referenced_tables

    try:
        return [t.split(".")[-1] for t in referenced_tables(sql)]
    except Exception:  # noqa: BLE001 - lineage is best effort
        return []


def artifact_lineage(
    db: Session,
    ws: Workspace,
    artifacts: list[ArtifactRecord],
    g: GraphBuilder | None = None,
    attach_to: str | None = None,
) -> GraphBuilder:
    """Add artifacts (and their parents within the same run) with their metric/dataset chains."""
    g = g or GraphBuilder()
    cache: dict[str, Any] = {}
    seen: set[str] = set()
    top = {a.id for a in artifacts}
    queue = list(artifacts)
    by_run: dict[str | None, dict[str, ArtifactRecord]] = {}
    while queue:
        a = queue.pop(0)
        if a.id in seen:
            continue
        seen.add(a.id)
        kind = "chart" if a.kind == "chart" else ("query" if a.sql else "artifact")
        aid = g.node(
            f"artifact:{a.id}",
            kind,
            a.title or a.kind,
            detail=a.kind,
            ref_id=a.id,
            meta={"engine_id": a.engine_artifact_id, "sql": a.sql, "run_id": a.run_id},
        )
        if attach_to and a.id in top:
            g.edge(aid, attach_to, "supports")
        ctx = LineageContext(
            metric_versions=dict(a.metric_versions or {}),
            dataset_hashes={
                dv["table"]: (dv["content_hash"], dv.get("row_count"))
                for dv in a.dataset_versions or []
                if isinstance(dv, dict) and dv.get("table") and dv.get("content_hash")
            },
        )
        for metric_id, version in (a.metric_versions or {}).items():
            g.edge(_metric_chain(g, db, ws, metric_id, version, cache, ctx), aid, "computed_from")
        for dv in a.dataset_versions or []:
            if isinstance(dv, dict) and dv.get("table"):
                g.edge(
                    _dataset_chain(g, db, ws.id, dv["table"], dv.get("content_hash"), dv.get("row_count")),
                    aid,
                    "reads",
                )
        if a.parent_ids and a.run_id:
            index = by_run.get(a.run_id)
            if index is None:
                index = {
                    r.engine_artifact_id or "": r
                    for r in db.scalars(select(ArtifactRecord).where(ArtifactRecord.run_id == a.run_id))
                }
                by_run[a.run_id] = index
            for pid in a.parent_ids:
                parent = index.get(pid)
                if parent is not None:
                    g.edge(
                        g.node(
                            f"artifact:{parent.id}",
                            "chart" if parent.kind == "chart" else ("query" if parent.sql else "artifact"),
                            parent.title or parent.kind,
                            detail=parent.kind,
                            ref_id=parent.id,
                            meta={
                                "engine_id": parent.engine_artifact_id,
                                "sql": parent.sql,
                                "run_id": parent.run_id,
                            },
                        ),
                        aid,
                        "input_to",
                    )
                    queue.append(parent)
    return g
