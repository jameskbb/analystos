"""Semantic layer persistence: assemble the engine ``SemanticModel`` from database rows.

* Metrics are versioned: ``MetricRecord`` is the stable identity and ``MetricVersion`` rows
  are immutable. Editing a metric creates a new version; nothing is overwritten.
* Every change to the model produces a content-addressed ``SemanticSnapshot`` so any
  investigation can point at the exact definitions it used.
* Relationships come from the ``relationships`` table (discovered or manual). Only rows the
  user approved are marked ``approved`` in the model, so the compiler never joins on an
  unapproved relationship.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from analystos_engine.semantic.models import (
    CalendarConfig,
    Dimension,
    Entity,
    GlossaryTerm,
    Metric,
    MetricTree,
    ModelIssue,
    Relationship,
    SemanticModel,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..errors import Conflict, NotFound, Unprocessable
from ..models import (
    DimensionRecord,
    GlossaryTermRecord,
    MetricRecord,
    MetricTreeRecord,
    MetricVersion,
    RelationshipRecord,
    SemanticSnapshot,
    Workspace,
)
from ..models import Entity as EntityRecord
from .workspaces import merged_settings

_CARDINALITY_ALIASES = {
    "1:1": "one_to_one",
    "1:n": "one_to_many",
    "n:1": "many_to_one",
    "n:m": "many_to_many",
    "m:n": "many_to_many",
}


def normalise_cardinality(value: str) -> str:
    v = (value or "").strip().lower()
    return _CARDINALITY_ALIASES.get(
        v, v if v in {"one_to_one", "one_to_many", "many_to_one", "many_to_many"} else "many_to_one"
    )


@dataclass
class MetricVersionRef:
    metric_id: str
    version_id: str
    version_no: int
    engine_version_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "version_no": self.version_no,
            "engine_version_id": self.engine_version_id,
        }


@dataclass
class BuiltModel:
    model: SemanticModel
    metric_versions: dict[str, MetricVersionRef]

    def version_map(self, metric_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
        ids = metric_ids if metric_ids is not None else list(self.metric_versions)
        out: dict[str, dict[str, Any]] = {}
        for mid in ids:
            if mid not in self.metric_versions:
                continue
            deps = self.model.metric_dependencies(mid) if self.model.has_metric(mid) else [mid]
            for dep in deps:
                if dep in self.metric_versions:
                    out[dep] = self.metric_versions[dep].as_dict()
        return out


def _definition_hash(definition: dict[str, Any]) -> str:
    payload = {k: v for k, v in definition.items() if k not in {"version"}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------------------------ building


def build_model(
    db: Session, workspace: Workspace, *, pinned_versions: dict[str, str] | None = None
) -> BuiltModel:
    """Assemble the current semantic model (optionally pinning metric versions by version id)."""
    ws_id = workspace.id
    entities = [
        Entity.model_validate(r.spec)
        for r in db.scalars(
            select(EntityRecord).where(EntityRecord.workspace_id == ws_id).order_by(EntityRecord.name)
        )
    ]
    dimensions = [
        Dimension.model_validate(r.spec)
        for r in db.scalars(
            select(DimensionRecord)
            .where(DimensionRecord.workspace_id == ws_id)
            .order_by(DimensionRecord.name)
        )
    ]
    metrics: list[Metric] = []
    refs: dict[str, MetricVersionRef] = {}
    records = db.scalars(
        select(MetricRecord)
        .where(MetricRecord.workspace_id == ws_id, MetricRecord.archived.is_(False))
        .order_by(MetricRecord.name)
    ).all()
    for rec in records:
        version_id = (pinned_versions or {}).get(rec.name) or rec.current_version_id
        if version_id is None:
            continue
        ver = db.get(MetricVersion, version_id)
        if ver is None or ver.metric_id != rec.id:
            ver = db.get(MetricVersion, rec.current_version_id) if rec.current_version_id else None
            if ver is None:
                continue
        metric = Metric.model_validate({**ver.definition, "id": rec.name, "version": ver.version_no})
        metrics.append(metric)
        refs[rec.name] = MetricVersionRef(
            metric_id=rec.name,
            version_id=ver.id,
            version_no=ver.version_no,
            engine_version_id=metric.version_id,
        )
    table_to_entity = {e.table: e.name for e in entities}
    relationships: list[Relationship] = []
    for r in db.scalars(
        select(RelationshipRecord)
        .where(RelationshipRecord.workspace_id == ws_id, RelationshipRecord.status != "rejected")
        .order_by(RelationshipRecord.created_at)
    ):
        fe, te = table_to_entity.get(r.from_table), table_to_entity.get(r.to_table)
        if fe is None or te is None:
            continue
        relationships.append(
            Relationship(
                from_entity=fe,
                from_col=r.from_col,
                to_entity=te,
                to_col=r.to_col,
                cardinality=normalise_cardinality(r.cardinality),  # type: ignore[arg-type]
                approved=r.status == "approved",
                name=r.id,
            )
        )
    metric_ids = {m.id for m in metrics}
    trees: list[MetricTree] = []
    for t in db.scalars(
        select(MetricTreeRecord)
        .where(MetricTreeRecord.workspace_id == ws_id)
        .order_by(MetricTreeRecord.root_metric)
    ):
        tree = MetricTree.model_validate(t.spec)
        if tree.root_metric not in metric_ids:
            continue
        tree = tree.model_copy(
            update={"nodes": [e for e in tree.nodes if e.parent in metric_ids and e.child in metric_ids]}
        )
        trees.append(tree)
    glossary = [
        GlossaryTerm.model_validate(r.spec)
        for r in db.scalars(
            select(GlossaryTermRecord)
            .where(GlossaryTermRecord.workspace_id == ws_id)
            .order_by(GlossaryTermRecord.term)
        )
    ]
    cal = merged_settings(workspace).get("calendar", {})
    calendar = CalendarConfig.model_validate(
        {k: v for k, v in cal.items() if k in CalendarConfig.model_fields}
    )
    model = SemanticModel(
        name=workspace.name,
        entities=entities,
        dimensions=dimensions,
        metrics=metrics,
        relationships=relationships,
        metric_trees=trees,
        glossary=glossary,
        calendar=calendar,
    )
    return BuiltModel(model=model, metric_versions=refs)


def snapshot(
    db: Session, workspace: Workspace, *, reason: str, user_id: str | None, built: BuiltModel | None = None
) -> SemanticSnapshot:
    """Record (or reuse) a content-addressed snapshot of the current model."""
    built = built or build_model(db, workspace)
    content_hash = built.model.content_hash()
    existing = db.scalar(
        select(SemanticSnapshot).where(
            SemanticSnapshot.workspace_id == workspace.id, SemanticSnapshot.content_hash == content_hash
        )
    )
    if existing is not None:
        return existing
    last = (
        db.scalar(
            select(func.max(SemanticSnapshot.version_no)).where(SemanticSnapshot.workspace_id == workspace.id)
        )
        or 0
    )
    snap = SemanticSnapshot(
        workspace_id=workspace.id,
        version_no=last + 1,
        content_hash=content_hash,
        model=built.model.model_dump(mode="json"),
        metric_version_ids=built.version_map(),
        reason=reason[:200],
        created_by=user_id,
    )
    db.add(snap)
    db.flush()
    return snap


def model_from_snapshot(snap: SemanticSnapshot) -> SemanticModel:
    return SemanticModel.model_validate(snap.model)


# ------------------------------------------------------------------------------ metrics


def get_metric_record(db: Session, workspace_id: str, metric_id: str) -> MetricRecord:
    rec = db.scalar(
        select(MetricRecord).where(MetricRecord.workspace_id == workspace_id, MetricRecord.name == metric_id)
    )
    if rec is None:
        raise NotFound(f"Metric {metric_id!r} not found")
    return rec


def validate_metric_against_model(db: Session, workspace: Workspace, metric: Metric) -> None:
    built = build_model(db, workspace)
    others = [m for m in built.model.metrics if m.id != metric.id]
    candidate = built.model.model_copy(update={"metrics": [*others, metric]})
    errors = [
        i for i in candidate.validate_model() if i.severity == "error" and i.object in {f"metric:{metric.id}"}
    ]
    if errors:
        raise Unprocessable(
            "; ".join(i.message for i in errors),
            code="invalid_metric",
            extra=[i.model_dump() for i in errors],
        )


def add_join_warnings(store: Any, model: SemanticModel, compiled: Any) -> Any:
    """Append the engine's non-unique join key warnings (review R-07) to a compiled query we execute ourselves."""
    from analystos_engine.semantic.compiler import join_key_warnings

    try:
        extra = join_key_warnings(store, model, compiled)
    except Exception:  # noqa: BLE001 - a warning check must never fail the query itself
        return compiled
    for w in extra:
        if w not in compiled.warnings:
            compiled.warnings.append(w)
    return compiled


def metric_execution_issues(
    state: Any, workspace: Workspace, model: SemanticModel, metric_id: str
) -> list[ModelIssue]:
    """Compile the metric and run it with ``LIMIT 0`` on the workspace data (read-only), so a bad ``expr``,
    filter or join is caught when the metric is saved rather than at first use (review R-52)."""
    from analystos_engine.semantic.compiler import CompileError, MetricQuery, compile

    try:
        compiled = compile(model, MetricQuery(metrics=[metric_id]))
    except (CompileError, ValueError, KeyError) as exc:
        return [
            ModelIssue(severity="error", object=f"metric:{metric_id}", message=f"does not compile: {exc}")
        ]
    try:
        state.stores.get(workspace.id).execute_read(
            f"SELECT * FROM ({compiled.sql}) AS _check LIMIT 0", None, 0, state.settings.query_timeout_s
        )
    except Exception as exc:  # noqa: BLE001 - reported to the user as a validation issue
        return [
            ModelIssue(
                severity="error",
                object=f"metric:{metric_id}",
                message=f"does not run on the data: {str(exc).splitlines()[0][:400]}",
            )
        ]
    return []


def check_metric_executes(state: Any, db: Session, workspace: Workspace, metric: Metric) -> None:
    built = build_model(db, workspace)
    others = [m for m in built.model.metrics if m.id != metric.id]
    candidate = built.model.model_copy(update={"metrics": [*others, metric]})
    issues = metric_execution_issues(state, workspace, candidate, metric.id)
    if issues:
        raise Unprocessable(
            "; ".join(i.message for i in issues),
            code="invalid_metric",
            extra=[i.model_dump() for i in issues],
        )


def upsert_metric(
    db: Session,
    workspace: Workspace,
    metric: Metric,
    *,
    user_id: str | None,
    change_note: str = "",
    create_only: bool = False,
    validate: bool = True,
) -> tuple[MetricRecord, MetricVersion, bool]:
    """Create a metric or add a new immutable version. Returns (record, version, changed)."""
    if validate:
        validate_metric_against_model(db, workspace, metric)
    definition = metric.model_dump(mode="json", exclude={"version"})
    digest = _definition_hash(definition)
    rec = db.scalar(
        select(MetricRecord).where(MetricRecord.workspace_id == workspace.id, MetricRecord.name == metric.id)
    )
    if rec is not None and create_only:
        raise Conflict(f"Metric {metric.id!r} already exists", code="metric_exists")
    if rec is None:
        rec = MetricRecord(workspace_id=workspace.id, name=metric.id, created_by=user_id)
        db.add(rec)
        db.flush()
    elif rec.current_version_id:
        current = db.get(MetricVersion, rec.current_version_id)
        if current is not None and current.definition_hash == digest:
            if rec.archived:
                rec.archived = False
            return rec, current, False
    version_no = rec.current_version_no + 1
    ver = MetricVersion(
        metric_id=rec.id,
        workspace_id=workspace.id,
        version_no=version_no,
        definition=definition,
        definition_hash=digest,
        change_note=change_note or ("Created" if version_no == 1 else ""),
        created_by=user_id,
    )
    db.add(ver)
    db.flush()
    rec.current_version_id = ver.id
    rec.current_version_no = version_no
    rec.archived = False
    rec.updated_at = utcnow()
    db.flush()
    return rec, ver, True


# ------------------------------------------------------------------------------ other objects


def upsert_entity(db: Session, workspace_id: str, entity: Entity) -> EntityRecord:
    rec = db.scalar(
        select(EntityRecord).where(
            EntityRecord.workspace_id == workspace_id, EntityRecord.name == entity.name
        )
    )
    if rec is None:
        rec = EntityRecord(workspace_id=workspace_id, name=entity.name)
        db.add(rec)
    rec.spec = entity.model_dump(mode="json")
    db.flush()
    return rec


def upsert_dimension(db: Session, workspace_id: str, dim: Dimension) -> DimensionRecord:
    rec = db.scalar(
        select(DimensionRecord).where(
            DimensionRecord.workspace_id == workspace_id, DimensionRecord.name == dim.name
        )
    )
    if rec is None:
        rec = DimensionRecord(workspace_id=workspace_id, name=dim.name)
        db.add(rec)
    rec.spec = dim.model_dump(mode="json")
    db.flush()
    return rec


def upsert_tree(db: Session, workspace_id: str, tree: MetricTree, user_id: str | None) -> MetricTreeRecord:
    rec = db.scalar(
        select(MetricTreeRecord).where(
            MetricTreeRecord.workspace_id == workspace_id, MetricTreeRecord.root_metric == tree.root_metric
        )
    )
    if rec is None:
        rec = MetricTreeRecord(
            workspace_id=workspace_id, root_metric=tree.root_metric, created_by=user_id, version_no=1
        )
        db.add(rec)
    else:
        rec.version_no = rec.version_no + 1
    rec.spec = tree.model_dump(mode="json")
    db.flush()
    return rec


def upsert_glossary(
    db: Session, workspace_id: str, term: GlossaryTerm, user_id: str | None
) -> GlossaryTermRecord:
    rec = db.scalar(
        select(GlossaryTermRecord).where(
            GlossaryTermRecord.workspace_id == workspace_id,
            func.lower(GlossaryTermRecord.term) == term.term.lower(),
        )
    )
    if rec is None:
        rec = GlossaryTermRecord(workspace_id=workspace_id, term=term.term, created_by=user_id)
        db.add(rec)
    rec.term = term.term
    rec.spec = term.model_dump(mode="json")
    db.flush()
    return rec


def upsert_relationship(
    db: Session,
    workspace_id: str,
    *,
    from_table: str,
    from_col: str,
    to_table: str,
    to_col: str,
    cardinality: str,
    status: str,
    origin: str,
    user_id: str | None,
    confidence: str = "high",
    signals: list[Any] | None = None,
    overlap_pct: float | None = None,
) -> RelationshipRecord:
    rec = db.scalar(
        select(RelationshipRecord).where(
            RelationshipRecord.workspace_id == workspace_id,
            RelationshipRecord.from_table == from_table,
            RelationshipRecord.from_col == from_col,
            RelationshipRecord.to_table == to_table,
            RelationshipRecord.to_col == to_col,
        )
    )
    if rec is None:
        rec = RelationshipRecord(
            workspace_id=workspace_id,
            from_table=from_table,
            from_col=from_col,
            to_table=to_table,
            to_col=to_col,
            cardinality=normalise_cardinality(cardinality),
            confidence=confidence,
            signals=signals or [],
            overlap_pct=overlap_pct,
            status=status,
            origin=origin,
        )
        db.add(rec)
    else:
        rec.cardinality = normalise_cardinality(cardinality)
        if status != "suggested":
            rec.status = status
    if status in {"approved", "rejected"}:
        rec.decided_by = user_id
        rec.decided_at = utcnow()
    db.flush()
    return rec


def import_model(
    db: Session,
    workspace: Workspace,
    model: SemanticModel,
    *,
    user_id: str | None,
    change_note: str = "Imported",
) -> dict[str, int]:
    """Merge a full semantic model (e.g. YAML) into the workspace. Existing objects are updated;
    metrics get a new version only when their definition changed."""
    counts = {
        "entities": 0,
        "dimensions": 0,
        "metrics_created": 0,
        "metrics_versioned": 0,
        "relationships": 0,
        "metric_trees": 0,
        "glossary": 0,
    }
    for e in model.entities:
        upsert_entity(db, workspace.id, e)
        counts["entities"] += 1
    for d in model.dimensions:
        upsert_dimension(db, workspace.id, d)
        counts["dimensions"] += 1
    for m in model.metrics:
        existed = (
            db.scalar(
                select(MetricRecord.id).where(
                    MetricRecord.workspace_id == workspace.id, MetricRecord.name == m.id
                )
            )
            is not None
        )
        _rec, _ver, changed = upsert_metric(
            db, workspace, m, user_id=user_id, change_note=change_note, validate=False
        )
        if changed:
            counts["metrics_versioned" if existed else "metrics_created"] += 1
    entity_tables = {e.name: e.table for e in model.entities}
    for r in model.relationships:
        ft, tt = entity_tables.get(r.from_entity), entity_tables.get(r.to_entity)
        if ft is None or tt is None:
            continue
        upsert_relationship(
            db,
            workspace.id,
            from_table=ft,
            from_col=r.from_col,
            to_table=tt,
            to_col=r.to_col,
            cardinality=r.cardinality,
            status="approved" if r.approved else "suggested",
            origin="manual",
            user_id=user_id,
            signals=["declared in semantic model"],
        )
        counts["relationships"] += 1
    for t in model.metric_trees:
        upsert_tree(db, workspace.id, t, user_id)
        counts["metric_trees"] += 1
    for g in model.glossary:
        upsert_glossary(db, workspace.id, g, user_id)
        counts["glossary"] += 1
    settings = merged_settings(workspace)
    settings["calendar"] = {**settings.get("calendar", {}), **model.calendar.model_dump(mode="json")}
    workspace.settings = settings
    db.flush()
    built = build_model(db, workspace)
    built.model.raise_for_errors()
    snapshot(db, workspace, reason=change_note, user_id=user_id, built=built)
    return counts
