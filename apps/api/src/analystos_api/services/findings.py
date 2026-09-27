"""Findings: promotion from investigation nodes, manual findings and versioning."""

from __future__ import annotations

from typing import Any

from analystos_investigator import node_lineage
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import NotFound, Unprocessable
from ..models import ArtifactRecord, Finding, FindingComment, FindingVersion, Investigation
from .investigations import engine_run, filter_context

SNAPSHOT_FIELDS = (
    "statement",
    "statement_type",
    "evidence_strength",
    "evidence_reasons",
    "status",
    "notes",
    "business_impact",
    "metric_id",
    "metric_version_ids",
    "values",
    "filter_context",
    "segment",
    "artifact_ids",
    "tags",
)


def get_finding(db: Session, workspace_id: str, finding_id: str) -> Finding:
    f = db.get(Finding, finding_id)
    if f is None or f.workspace_id != workspace_id:
        raise NotFound("Finding not found")
    return f


def snapshot_finding(db: Session, f: Finding, user_id: str | None, note: str) -> FindingVersion:
    ver = FindingVersion(
        finding_id=f.id,
        version_no=f.version_no,
        snapshot={k: getattr(f, k) for k in SNAPSHOT_FIELDS},
        change_note=note,
        created_by=user_id,
    )
    db.add(ver)
    db.flush()
    return ver


def bump_version(db: Session, f: Finding, user_id: str | None, note: str) -> None:
    f.version_no = f.version_no + 1
    snapshot_finding(db, f, user_id, note)


def node_findings(db: Session, investigation_id: str) -> dict[str, str]:
    rows = db.execute(
        select(Finding.node_id, Finding.id).where(
            Finding.investigation_id == investigation_id, Finding.node_id.is_not(None)
        )
    ).all()
    return {n: fid for n, fid in rows if n}


def create_from_node(
    db: Session,
    inv: Investigation,
    node_id: str,
    *,
    user_id: str | None,
    statement: str | None = None,
    notes: str = "",
) -> tuple[Finding, bool]:
    """Promote a node to a Finding. Returns ``(finding, created)``; saving a node twice returns the
    existing finding with ``created=False`` (review R-37)."""
    erun = engine_run(db, inv)
    doc = erun.investigation
    try:
        node = doc.tree.get(node_id)
    except KeyError as exc:
        raise NotFound("Node not found") from exc
    if node.status == "failed":
        raise Unprocessable("A failed test cannot become a finding", code="node_failed")
    existing = db.scalar(
        select(Finding).where(Finding.investigation_id == inv.id, Finding.node_id == node_id)
    )
    if existing is not None:
        return existing, False
    engine_ids = [a.id for a in node_lineage(erun, node_id)]
    rows = db.scalars(
        select(ArtifactRecord).where(
            ArtifactRecord.run_id == inv.current_run_id, ArtifactRecord.engine_artifact_id.in_(engine_ids)
        )
    ).all()
    order = {eid: i for i, eid in enumerate(engine_ids)}
    artifact_ids = [r.id for r in sorted(rows, key=lambda r: order.get(r.engine_artifact_id or "", 0))]
    ctx = doc.plan.context if doc.plan else None
    metric_chips: list[dict[str, Any]] = []
    for r in rows:
        for chip in r.filter_context or []:
            if chip.get("kind") == "metric" and chip not in metric_chips:
                metric_chips.append(chip)
    c = node.contribution_to_parent
    values: dict[str, Any] = {
        "current": node.current,
        "baseline": node.baseline,
        "abs_change": node.abs_change,
        "pct_change": node.pct_change,
        "share": c.share if c else None,
        "effect": c.effect if c else None,
        "contribution_method": c.method if c else None,
        "format": node.metric_format,
        "metric_label": node.metric_label,
    }
    metric_versions = inv.metric_version_ids or {}
    if node.metric_id and node.metric_id in metric_versions:
        metric_versions = {node.metric_id: metric_versions[node.metric_id], **metric_versions}
    f = Finding(
        workspace_id=inv.workspace_id,
        investigation_id=inv.id,
        node_id=node_id,
        statement=statement or node.statement,
        statement_type=node.statement_type,
        evidence_strength=node.evidence_strength,
        evidence_reasons=list(node.evidence_reasons),
        status="confirmed" if node.status == "confirmed" else "draft",
        notes=notes,
        metric_id=node.metric_id,
        metric_version_ids=metric_versions,
        values=values,
        filter_context=filter_context(
            window=ctx.window if ctx else None,
            baseline=ctx.baseline if ctx else None,
            filters=ctx.filters if ctx else None,
            segments=node.segment_path,
        )
        + metric_chips,
        segment=node.segment.model_dump(mode="json") if node.segment else None,
        artifact_ids=artifact_ids,
        created_by=user_id,
    )
    db.add(f)
    db.flush()
    snapshot_finding(db, f, user_id, "Promoted from investigation node")
    return f, True


def comment_counts(db: Session, finding_ids: list[str]) -> dict[str, int]:
    if not finding_ids:
        return {}
    rows = db.execute(
        select(FindingComment.finding_id, func.count())
        .where(FindingComment.finding_id.in_(finding_ids))
        .group_by(FindingComment.finding_id)
    ).all()
    return {fid: int(n) for fid, n in rows}
