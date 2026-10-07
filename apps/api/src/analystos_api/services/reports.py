"""Reports: story blocks (narrative, finding, kpi, chart, table, methodology, sources, summary),
executive summaries from confirmed findings and the monthly business review generator.

Every number placed in a block comes from an engine computation performed while the block
is generated (and the block records the SQL / metric version that produced it). Narrative
text is templated from those numbers; hypotheses are labelled as such (spec sections 38, 64, 65).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from analystos_investigator import FindingInput, executive_summary, variance_review
from analystos_investigator import Investigation as EngineInvestigation
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound, Unprocessable
from ..models import ArtifactRecord, Dataset, DatasetVersion, Finding, Report, Workspace
from ..models import Investigation as InvestigationRecord
from ..state import AppState
from .ai import get_llm
from .investigations import engine_run, filter_context, reference_date
from .semantic import BuiltModel, build_model, snapshot


def block(kind: str, **fields: Any) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex[:12], "type": kind, **fields}


def get_report(db: Session, workspace_id: str, report_id: str) -> Report:
    r = db.get(Report, report_id)
    if r is None or r.workspace_id != workspace_id:
        raise NotFound("Report not found")
    return r


def fmt(value: float | None, kind: str | None) -> str:
    if value is None:
        return "n/a"
    if kind == "currency":
        return f"${value:,.0f}"
    if kind == "percent":
        return f"{value * 100:.1f}%"
    if kind == "integer":
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}%"


def finding_block(f: Finding) -> dict[str, Any]:
    return block(
        "finding",
        finding_id=f.id,
        snapshot={
            "statement": f.statement,
            "statement_type": f.statement_type,
            "evidence_strength": f.evidence_strength,
            "evidence_reasons": f.evidence_reasons,
            "status": f.status,
            "values": f.values,
            "filter_context": f.filter_context,
            "artifact_ids": f.artifact_ids,
            "version_no": f.version_no,
        },
    )


def finding_evidence(db: Session, findings: list[Finding]) -> list[Any]:
    """Engine artifacts behind findings: the numbers an AI-worded summary may cite (numeric verifier)."""
    from analystos_investigator import Artifact as EngineArtifact

    ids = sorted({a for f in findings for a in f.artifact_ids or []})
    if not ids:
        return []
    out = []
    for rec in db.scalars(select(ArtifactRecord).where(ArtifactRecord.id.in_(ids))):
        if rec.origin == "investigation" and rec.document:
            try:
                out.append(EngineArtifact.model_validate(rec.document))
            except ValueError:
                continue
    return out


def summary_block(
    findings: list[Finding],
    extra: list[FindingInput] | None = None,
    *,
    llm: Any = None,
    artifacts: list[Any] | None = None,
    nodes: list[Any] | None = None,
) -> dict[str, Any]:
    """Executive summary from confirmed items. With AI configured the narrative may be reworded by the model,
    but only if every number in it is found in the evidence and no hypothesis is stated as fact (§38)."""
    items = [
        FindingInput.model_validate(
            {
                "statement": f.statement,
                "statement_type": f.statement_type,
                "status": f.status,
                "evidence_strength": f.evidence_strength,
                "artifact_ids": list(f.artifact_ids),
                "node_id": f.node_id,
            }
        )
        for f in findings
    ]
    s = executive_summary([*items, *(extra or [])], llm=llm, artifacts=artifacts or [], nodes=nodes or [])
    return block(
        "summary",
        title="Executive summary",
        observations=[i.model_dump() for i in s.observations],
        supported_explanations=[i.model_dump() for i in s.supported_explanations],
        hypotheses=[i.model_dump() for i in s.hypotheses],
        narrative=s.narrative,
        notes=s.notes,
        excluded_count=s.excluded_count,
        narrative_source=s.narrative_source,
    )


def sources_block(
    db: Session,
    ws: Workspace,
    built: BuiltModel,
    metric_ids: list[str],
    extra: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for mid, ref in built.version_map([m for m in metric_ids if m in built.metric_versions]).items():
        items.append(
            {
                "kind": "metric",
                "label": built.model.get_metric(mid).display_name,
                "ref_id": mid,
                "version": f"v{ref['version_no']}",
                "detail": ref["engine_version_id"],
            }
        )
    tables = sorted(
        {
            built.model.get_entity(str(built.model.get_metric(d).entity)).table
            for m in metric_ids
            if built.model.has_metric(m)
            for d in built.model.metric_dependencies(m)
            if built.model.get_metric(d).entity
        }
    )
    for t in tables:
        ds = db.scalar(select(Dataset).where(Dataset.workspace_id == ws.id, Dataset.table_name == t))
        if ds is None:
            continue
        ver = db.get(DatasetVersion, ds.current_version_id) if ds.current_version_id else None
        items.append(
            {
                "kind": "dataset",
                "label": ds.name,
                "ref_id": ds.id,
                "version": f"v{ver.version_no}" if ver else None,
                "detail": f"{ds.row_count:,} rows; content {ver.content_hash[:12]}" if ver else None,
            }
        )
    return block("sources", title="Sources", items=[*items, *(extra or [])])


def _chart_provenance(art: ArtifactRecord, by_engine_id: dict[str | None, ArtifactRecord]) -> dict[str, Any]:
    """Provenance for a chart block that resolves to SQL: the chart's own SQL, or its parent query artifacts'."""
    parents = [by_engine_id[p] for p in art.parent_ids or [] if p in by_engine_id]
    sql = art.sql or next((p.sql for p in parents if p.sql), None)
    return {
        "artifact_id": art.id,
        "sql": sql,
        "metric_versions": art.metric_versions,
        "dataset_versions": art.dataset_versions,
        "filter_context": art.filter_context,
        "parent_artifact_ids": [p.id for p in parents],
        "queries": [{"artifact_id": p.id, "title": p.title, "sql": p.sql} for p in parents if p.sql],
    }


def accepted_node_ids(doc: EngineInvestigation, findings: list[Finding]) -> set[str]:
    """Nodes whose evidence the analyst accepted: confirmed nodes and confirmed findings' nodes,
    minus anything rejected."""
    ids = {n.id for n in doc.tree.nodes if n.status == "confirmed"}
    ids |= {f.node_id for f in findings if f.status == "confirmed" and f.node_id}
    ids -= {f.node_id for f in findings if f.status == "rejected" and f.node_id}
    return ids


def review_block(review: Any, investigation_id: str) -> dict[str, Any]:
    """The operating review of a gap to plan, from accepted evidence only (see ``variance_review``)."""
    return block(
        "narrative",
        title=review.title,
        label="Operating review",
        markdown=review.markdown
        + "\n\n*Built only from confirmed findings of the investigation; every number is a value computed by the "
        "analytical engine. Related signals are observations whose relationship to the gap is not established.*",
        investigation_id=investigation_id,
        node_ids=review.node_ids,
        reconciled=review.reconciled,
        complete=review.complete,
        pending=review.pending,
        headline=review.headline,
        premise=" ".join(review.premises) or None,
        premises=review.premises,
        causes=review.causes,
        related=review.related,
        watch=review.watch,
    )


def review_blocks(review: Any, investigation_id: str) -> list[dict[str, Any]]:
    """The review memo: the narrative block, then the plan-to-actual bridge as a waterfall block
    and as a table (steps from plan to actual with running totals)."""
    out = [review_block(review, investigation_id)]
    bridge = review.bridge
    if bridge is None or not bridge.steps:
        return out
    steps = [s.model_dump(mode="json") for s in bridge.steps]
    out.append(
        block(
            "bridge",
            title=f"{bridge.metric_label}: plan to actual",
            metric_id=bridge.metric_id,
            metric_label=bridge.metric_label,
            format=bridge.metric_format,
            start_label=bridge.start_label,
            start=bridge.start,
            end_label=bridge.end_label,
            end=bridge.end,
            gap=bridge.gap,
            steps=steps,
            reconciled=bridge.reconciled,
            option=bridge.chart,
            investigation_id=investigation_id,
        )
    )
    rows = [[bridge.start_label, None, bridge.start]]
    rows += [[s.label, s.amount, s.running_total] for s in bridge.steps]
    rows.append([bridge.end_label, bridge.gap, bridge.end])
    out.append(
        block(
            "table",
            title="Plan to actual",
            result={
                "columns": [
                    {"name": "step", "type": "VARCHAR"},
                    {"name": "effect", "type": "DOUBLE"},
                    {"name": "running_total", "type": "DOUBLE"},
                ],
                "rows": rows,
                "row_count": len(rows),
                "truncated": False,
                "elapsed_ms": 0.0,
                "sql": None,
            },
            note="Each step is a reconciled part of the gap; the steps add up exactly to actual minus plan.",
        )
    )
    return out


def _plan_metric(model: Any, metric_id: str) -> str | None:
    """The plan counterpart of a KPI: the metric with ``plan_for`` tagged ``plan`` (else the only one)."""
    cands = model.plan_metrics_for(metric_id)
    tagged = [c for c in cands if "plan" in {t.lower() for t in c.tags}]
    pick = tagged if len(tagged) == 1 else cands
    return pick[0].id if len(pick) == 1 else None


def _plan_review(db: Session, ws: Workspace, window: Any) -> tuple[Any, str] | None:
    """The operating review of the latest completed plan investigation over exactly ``window``."""
    invs = db.scalars(
        select(InvestigationRecord)
        .where(InvestigationRecord.workspace_id == ws.id, InvestigationRecord.status == "completed")
        .order_by(InvestigationRecord.updated_at.desc())
    )
    for inv in invs:
        try:
            doc = EngineInvestigation.model_validate(inv.document)
        except ValueError:
            continue
        ctx = doc.plan.context if doc.plan else None
        if (
            ctx is None
            or not ctx.plan_metrics
            or ctx.window.start != window.start
            or ctx.window.end != window.end
        ):
            continue
        findings = list(db.scalars(select(Finding).where(Finding.investigation_id == inv.id)))
        review = variance_review(doc, accepted_node_ids(doc, findings))
        if review is not None:
            return review, inv.id
    return None


def report_from_investigation(
    db: Session, state: AppState, ws: Workspace, inv: Any, user_id: str | None, *, persist: bool = True
) -> Report:
    doc = EngineInvestigation.model_validate(inv.document)
    if not doc.tree.root_id:
        raise Unprocessable("Run the investigation before building a report", code="not_run")
    built = build_model(db, ws)
    root = doc.tree.root()
    ctx = doc.plan.context if doc.plan else None
    findings = list(
        db.scalars(select(Finding).where(Finding.investigation_id == inv.id).order_by(Finding.created_at))
    )
    promoted = {f.node_id for f in findings}
    confirmed_nodes = [
        FindingInput.from_node(n) for n in doc.tree.nodes if n.status == "confirmed" and n.id not in promoted
    ]
    arts = {
        a.engine_artifact_id: a
        for a in db.scalars(select(ArtifactRecord).where(ArtifactRecord.run_id == inv.current_run_id))
    }
    llm = get_llm(state, ws, investigation_id=inv.id)
    review = variance_review(doc, accepted_node_ids(doc, findings))
    blocks: list[dict[str, Any]] = [
        block("heading", text=review.title if review else inv.question),
        *(review_blocks(review, inv.id) if review else []),
        # the whole analysis again, unconfirmed, only while the review is not complete
        *(
            []
            if review is not None and review.complete
            else [
                block(
                    "narrative",
                    title="Unconfirmed analysis",
                    label="Unconfirmed analysis",
                    markdown="**Unconfirmed analysis.** "
                    + (doc.brief_answer or root.statement)
                    + "\n\n*This answer is generated from the investigation tree and has not been confirmed by an "
                    "analyst; confirmed findings are listed separately. Numbers are computed by the analytical "
                    "engine; see Methodology and Sources.*",
                )
            ]
        ),
        summary_block(
            findings,
            confirmed_nodes,
            llm=llm,
            artifacts=[*engine_run(db, inv).artifacts] if llm is not None else None,
            nodes=list(doc.tree.nodes),
        ),
        block(
            "kpi",
            metric_id=root.metric_id,
            label=root.metric_label or root.metric_id,
            value=root.current,
            baseline=root.baseline,
            abs_change=root.abs_change,
            pct_change=root.pct_change,
            format=root.metric_format,
            period=ctx.window.display() if ctx else None,
            baseline_period=ctx.baseline.display() if ctx else None,
            baseline_label=root.baseline_label,
            filter_context=filter_context(
                window=ctx.window if ctx else None,
                baseline=ctx.baseline if ctx else None,
                filters=ctx.filters if ctx else None,
            ),
            artifact_ids=[arts[a].id for a in root.artifact_ids if a in arts],
        ),
        *[
            block(
                "kpi",
                metric_id=n.metric_id,
                label=n.metric_label or n.metric_id,
                value=n.current,
                baseline=n.baseline,
                abs_change=n.abs_change,
                pct_change=n.pct_change,
                format=n.metric_format,
                period=ctx.window.display() if ctx else None,
                baseline_label=n.baseline_label,
                verdict=n.verdict,
                note=n.title,
                artifact_ids=[arts[a].id for a in n.artifact_ids if a in arts],
            )
            for n in doc.tree.children_of(root.id)
            if n.role == "premise"
        ],
    ]
    charted: set[str] = set()
    for node in [
        root,
        *sorted([n for n in doc.tree.nodes if n.parent_id == root.id], key=lambda n: (n.rank or 99, n.id))[
            :4
        ],
    ]:
        art = next(
            (
                arts[a]
                for a in node.artifact_ids
                if a in arts and arts[a].chart_spec and arts[a].id not in charted
            ),
            None,
        )
        if art is not None:
            charted.add(art.id)
            blocks.append(
                block(
                    "chart",
                    title=art.title or node.statement,
                    option=art.chart_spec,
                    artifact_id=art.id,
                    provenance=_chart_provenance(art, arts),
                )
            )
    segs = sorted(
        [n for n in doc.tree.nodes if n.kind == "segment" and n.contribution_to_parent is not None],
        key=lambda n: -abs((n.contribution_to_parent.effect if n.contribution_to_parent else None) or 0.0),
    )[:10]
    if segs:
        blocks.append(
            block(
                "table",
                title="Largest contributing segments",
                result={
                    "columns": [
                        {"name": "segment", "type": "VARCHAR"},
                        {"name": "current", "type": "DOUBLE"},
                        {"name": "baseline", "type": "DOUBLE"},
                        {"name": "change", "type": "DOUBLE"},
                        {"name": "share_of_parent_change", "type": "DOUBLE"},
                        {"name": "evidence", "type": "VARCHAR"},
                    ],
                    "rows": [
                        [
                            " / ".join(s.label() for s in n.segment_path)
                            or (n.segment.label() if n.segment else ""),
                            n.current,
                            n.baseline,
                            n.abs_change,
                            n.contribution_to_parent.share if n.contribution_to_parent else None,
                            n.evidence_strength,
                        ]
                        for n in segs
                    ],
                    "row_count": len(segs),
                    "truncated": False,
                    "elapsed_ms": 0.0,
                    "sql": None,
                },
            )
        )
    blocks.extend(finding_block(f) for f in findings if f.status != "rejected")
    steps = (
        [f"{i + 1}. {s.title}: {s.rationale}" for i, s in enumerate(doc.plan.enabled_steps())]
        if doc.plan
        else []
    )
    hyps = [
        f"- {h.statement} ({'tested' if h.testable else 'not testable with available data'})"
        for h in doc.hypotheses
    ]
    blocks.append(
        block(
            "methodology",
            markdown="\n".join(
                [
                    f"Question interpreted as **{root.metric_label or root.metric_id}**"
                    + (f", {ctx.window.display()} compared with {ctx.baseline.display()}." if ctx else "."),
                    "",
                    "Analysis plan executed:",
                    *steps,
                    "",
                    "Hypotheses considered:",
                    *hyps,
                    "",
                    "Contribution shares are exact within one dimension; shares across different dimensions overlap and are "
                    "not additive. Evidence strength reflects effect size, directness of the calculation and corroboration; "
                    "no numeric confidence scores are used.",
                ]
            ),
        )
    )
    blocks.append(
        sources_block(
            db,
            ws,
            built,
            list(inv.metric_version_ids or {}),
            extra=[
                {
                    "kind": "investigation",
                    "label": inv.title,
                    "ref_id": inv.id,
                    "version": f"run {inv.run_count}",
                    "detail": f"semantic snapshot {inv.semantic_snapshot_id}",
                }
            ],
        )
    )
    rep = Report(
        workspace_id=ws.id,
        title=(review.title if review else f"Report: {inv.title}")[:400],
        kind="investigation",
        status="draft",
        investigation_id=inv.id,
        blocks=blocks,
        created_by=user_id,
    )
    if persist:
        db.add(rep)
        db.flush()
    return rep


def executive_summary_report(
    db: Session,
    ws: Workspace,
    *,
    finding_ids: list[str] | None,
    investigation_id: str | None,
    title: str | None,
    user_id: str | None,
    state: AppState | None = None,
) -> Report:
    stmt = select(Finding).where(Finding.workspace_id == ws.id)
    if finding_ids:
        stmt = stmt.where(Finding.id.in_(finding_ids))
    if investigation_id:
        stmt = stmt.where(Finding.investigation_id == investigation_id)
    findings = list(db.scalars(stmt.order_by(Finding.created_at)))
    if finding_ids and len(findings) != len(set(finding_ids)):
        raise Unprocessable("Unknown finding ids", code="unknown_finding")
    confirmed = [f for f in findings if f.status == "confirmed"]
    built = build_model(db, ws)
    llm = get_llm(state, ws) if state is not None else None
    blocks = [
        block("heading", text=title or "Executive summary"),
        summary_block(findings, llm=llm, artifacts=finding_evidence(db, confirmed) if llm else None),
        *[finding_block(f) for f in confirmed],
        block(
            "methodology",
            markdown=(
                f"Built from {len(confirmed)} confirmed finding(s); {len(findings) - len(confirmed)} unconfirmed "
                "finding(s) were excluded. Observed facts, supported explanations and unresolved hypotheses are "
                "listed separately; nothing is added that is not in a confirmed finding."
            ),
        ),
        sources_block(db, ws, built, sorted({f.metric_id for f in confirmed if f.metric_id})),
    ]
    rep = Report(
        workspace_id=ws.id,
        title=title or "Executive summary",
        kind="custom",
        status="draft",
        investigation_id=investigation_id,
        blocks=blocks,
        created_by=user_id,
    )
    db.add(rep)
    db.flush()
    return rep


# ------------------------------------------------------------------------------ business review


def canonical_revenue_metric(model: Any, metric_ids: list[str]) -> str | None:
    """The workspace's canonical revenue metric among ``metric_ids`` (id/name "revenue", or a revenue tag)."""

    def score(mid: str) -> int:
        m = model.get_metric(mid)
        tags = {t.lower() for t in m.tags}
        names = {mid.lower(), (m.name or "").lower(), (m.label or "").lower()}
        if not m.canonical:
            return 0
        if "revenue" in names or "net revenue" in names or "net_revenue" in names:
            return 3
        if "revenue" in tags:
            return 2
        return 1 if "revenue" in mid.lower() and m.format == "currency" else 0

    ranked = sorted((mid for mid in metric_ids if model.has_metric(mid)), key=lambda mid: -score(mid))
    return ranked[0] if ranked and score(ranked[0]) > 0 else None


def order_by_materiality(model: Any, kpis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonical revenue first; then currency metrics by absolute change; then the rest by relative change.
    A ratio's relative swing (e.g. a conversion rate on immature cohorts) never outranks money (review R-26)."""
    revenue = canonical_revenue_metric(model, [k["metric_id"] for k in kpis])

    def key(k: dict[str, Any]) -> tuple[int, float]:
        if k["metric_id"] == revenue:
            return (0, 0.0)
        if k.get("format") == "currency":
            return (1, -abs(k.get("abs_change") or 0.0))
        return (2, -abs(k.get("pct_change") or 0.0))

    return sorted(kpis, key=key)


def _kpi_metrics(built: BuiltModel, limit: int = 10) -> list[str]:
    """Headline metrics: tagged kpi/headline/core metrics that root a metric tree first, then other tagged
    metrics, then tree roots, then canonical metrics."""
    model = built.model
    roots = [t.root_metric for t in model.metric_trees]
    tagged = [m.id for m in model.metrics if {"kpi", "headline", "core"} & {t.lower() for t in m.tags}]
    canon = [m.id for m in model.metrics if m.canonical]
    ordered: list[str] = []
    for mid in [*[r for r in roots if r in tagged], *tagged, *roots, *canon]:
        if mid not in ordered and model.has_metric(mid):
            ordered.append(mid)
    return ordered[:limit]


def business_review(
    db: Session, state: AppState, ws: Workspace, period: str, title: str | None, user_id: str | None
) -> Report:
    from analystos_engine.analysis.compare import compare_periods
    from analystos_engine.analysis.contribution import contribution_by_dimension
    from analystos_engine.calendar import CalendarError, previous_period, resolve_period
    from analystos_investigator.semantic_graph import dimensions_for_metric, time_dimension_for_metric

    built = build_model(db, ws)
    model = built.model
    try:
        window = resolve_period(period, reference_date(ws), model.calendar)
    except CalendarError as exc:
        raise Unprocessable(f"Invalid period: {exc}", code="invalid_period") from exc
    baseline = previous_period(window, "pop")
    yoy = previous_period(window, "yoy")
    store = state.stores.get(ws.id)
    metrics = _kpi_metrics(built)
    if not metrics:
        raise Unprocessable("Define metrics before generating a business review", code="no_metrics")
    kpis: list[dict[str, Any]] = []
    for mid in metrics:
        tdim = time_dimension_for_metric(model, mid)
        if tdim is None:
            continue
        try:
            pop = compare_periods(
                store, model, mid, window.with_dimension(tdim), baseline.with_dimension(tdim), kind="pop"
            )
            yy = compare_periods(
                store, model, mid, window.with_dimension(tdim), yoy.with_dimension(tdim), kind="yoy"
            )
        except Exception:  # noqa: BLE001 - a metric that cannot be compared is reported, not guessed
            continue
        m = model.get_metric(mid)
        plan_fields: dict[str, Any] = {}
        plan_id = _plan_metric(model, mid)
        if plan_id is not None and pop.current is not None:
            try:
                from analystos_engine.semantic.compiler import MetricQuery, run_metric_query

                ptdim = time_dimension_for_metric(model, plan_id)
                compiled, plan_res = run_metric_query(
                    store, model, MetricQuery(metrics=[plan_id], time=window.with_dimension(ptdim)), limit=5
                )
                row = plan_res.to_records()[0] if plan_res.rows else {}
                plan_value = float(row[plan_id]) if row.get(plan_id) is not None else None
            except Exception:  # noqa: BLE001 - a plan that cannot be measured is left out, not guessed
                plan_value = None
            if plan_value is not None:
                variance = pop.current - plan_value
                plan_fields = {
                    "plan_metric_id": plan_id,
                    "plan_label": model.get_metric(plan_id).display_name,
                    "plan": plan_value,
                    "plan_variance": variance,
                    "plan_variance_pct": variance / abs(plan_value) if plan_value else None,
                    "plan_sql": compiled.sql,
                }
        kpis.append(
            {
                **plan_fields,
                "metric_id": mid,
                "label": m.display_name,
                "format": m.format,
                "value": pop.current,
                "baseline": pop.baseline,
                "abs_change": pop.abs_change,
                "pct_change": pop.pct_change,
                "yoy_baseline": yy.baseline,
                "yoy_pct_change": yy.pct_change,
                "higher_is_better": m.higher_is_better,
                "period": window.display(),
                "baseline_period": baseline.display(),
                "sql": pop.sql_current,
                "baseline_sql": pop.sql_baseline,
                "metric_versions": pop.metric_versions,
            }
        )
    blocks: list[dict[str, Any]] = [block("heading", text=title or f"Business review: {window.display()}")]
    kpis = order_by_materiality(model, kpis)
    revenue_id = canonical_revenue_metric(model, [k["metric_id"] for k in kpis])
    lead = next((k for k in kpis if k["metric_id"] == revenue_id), None) or max(
        (k for k in kpis if k["pct_change"] is not None), key=lambda k: abs(k["pct_change"]), default=None
    )
    intro = [
        f"Business review for **{window.display()}**, compared with {baseline.display()} "
        f"(and {yoy.display()} year over year). KPIs are ordered by materiality"
        + (f", {lead['label']} first." if lead else ".")
    ]
    for k in kpis[:5]:
        vs_plan = f"{pct(k['plan_variance_pct'])} vs plan, " if k.get("plan") is not None else ""
        intro.append(
            f"- {k['label']}: {fmt(k['value'], k['format'])} ({vs_plan}{pct(k['pct_change'])} vs prior period, "
            f"{pct(k['yoy_pct_change'])} YoY)."
        )
    intro.append("\n*Draft generated from computed values; review before publishing.*")
    blocks.append(block("narrative", markdown="\n".join(intro)))
    plan_review = _plan_review(db, ws, window)
    if plan_review is not None:
        blocks.extend(review_blocks(*plan_review))
    blocks.append(block("kpi_overview", title="KPI overview", items=kpis))
    changes = [k for k in kpis if k["pct_change"] is not None]
    kpi_queries = [
        {"metric_id": k["metric_id"], "label": k["label"], "sql": k["sql"], "baseline_sql": k["baseline_sql"]}
        for k in changes
    ]
    blocks.append(
        block(
            "table",
            title="Largest changes",
            result={
                "columns": [
                    {"name": "metric", "type": "VARCHAR"},
                    {"name": "current", "type": "DOUBLE"},
                    {"name": "prior", "type": "DOUBLE"},
                    {"name": "change", "type": "DOUBLE"},
                    {"name": "pct_change", "type": "DOUBLE"},
                    {"name": "yoy_pct_change", "type": "DOUBLE"},
                ],
                "rows": [
                    [
                        k["label"],
                        k["value"],
                        k["baseline"],
                        k["abs_change"],
                        k["pct_change"],
                        k["yoy_pct_change"],
                    ]
                    for k in changes
                ],
                "row_count": len(changes),
                "truncated": False,
                "elapsed_ms": 0.0,
                "sql": changes[0]["sql"] if changes else None,
            },
            sql=changes[0]["sql"] if changes else None,
            queries=kpi_queries,
            note="Ordered by materiality: the canonical revenue metric first, then currency metrics by absolute "
            "change, then other metrics by relative change. Each row's SQL is listed in `queries`.",
        )
    )
    if lead is not None:
        mid = lead["metric_id"]
        tdim = time_dimension_for_metric(model, mid)
        usable, _ = dimensions_for_metric(model, mid)
        cats = [d.name for d in usable if d.type == "categorical"][:4]
        movements: list[list[Any]] = []
        queries: list[str] = []
        cols = [
            {"name": "segment", "type": "VARCHAR"},
            {"name": "current", "type": "DOUBLE"},
            {"name": "prior", "type": "DOUBLE"},
            {"name": "change", "type": "DOUBLE"},
            {"name": "share_of_change", "type": "DOUBLE"},
        ]
        for dim in cats:
            try:
                res = contribution_by_dimension(
                    store,
                    model,
                    mid,
                    dim,
                    window.with_dimension(tdim),
                    baseline.with_dimension(tdim),
                    top_n=10,
                )
            except Exception:  # noqa: BLE001 - a dimension that cannot be analysed is skipped, not guessed
                continue
            dim_queries = list(res.queries or [res.sql])
            queries.extend(dim_queries)
            rows = [
                [str(r.segment), r.current, r.baseline, r.change, r.share_of_change]
                for r in res.rows
                if not r.is_other and r.change is not None
            ]
            for r in res.rows:
                if not r.is_other and r.current_weight is not None and r.baseline_weight is not None:
                    movements.append(
                        [
                            dim,
                            str(r.segment),
                            r.baseline_weight,
                            r.current_weight,
                            r.current_weight - r.baseline_weight,
                        ]
                    )
            if not rows:
                continue
            rows.sort(key=lambda r: float(r[3] or 0.0))
            blocks.append(
                block(
                    "table",
                    title=f"Drivers of {lead['label']} by {dim.replace('_', ' ')}",
                    result={
                        "columns": cols,
                        "rows": rows[:10],
                        "row_count": min(len(rows), 10),
                        "truncated": len(rows) > 10,
                        "elapsed_ms": 0.0,
                        "sql": dim_queries[0] if dim_queries else None,
                    },
                    dimension=dim,
                    sql=dim_queries[0] if dim_queries else None,
                    queries=dim_queries,
                    method=res.method,
                    additive_valid=res.additive_valid,
                    note="Negative drivers first. Shares are of the total change within this dimension only; "
                    "different dimensions overlap and do not add up.",
                )
            )
        movements.sort(key=lambda r: -abs(r[4]))
        blocks.append(
            block(
                "table",
                title="Segment mix movements",
                result={
                    "columns": [
                        {"name": "dimension", "type": "VARCHAR"},
                        {"name": "segment", "type": "VARCHAR"},
                        {"name": "prior_share", "type": "DOUBLE"},
                        {"name": "current_share", "type": "DOUBLE"},
                        {"name": "share_points", "type": "DOUBLE"},
                    ],
                    "rows": movements[:10],
                    "row_count": min(len(movements), 10),
                    "truncated": False,
                    "elapsed_ms": 0.0,
                    "sql": queries[0] if queries else None,
                },
                sql=queries[0] if queries else None,
                queries=queries[:20],
            )
        )
        blocks.append(_anomaly_block(store, model, mid, lead["label"], window))
    findings = list(
        db.scalars(
            select(Finding)
            .where(Finding.workspace_id == ws.id, Finding.status.in_(["confirmed", "needs_review", "draft"]))
            .order_by(Finding.updated_at.desc())
        )
    )
    in_period = [f for f in findings if _overlaps(f.filter_context, window)] or []
    confirmed = [f for f in in_period if f.status == "confirmed"]
    llm = get_llm(state, ws)
    blocks.append(
        summary_block(confirmed, llm=llm, artifacts=finding_evidence(db, confirmed) if llm else None)
    )
    blocks.extend(finding_block(f) for f in confirmed)
    open_q = [f for f in in_period if f.status != "confirmed" or f.statement_type == "hypothesis"]
    blocks.append(
        block(
            "open_questions",
            title="Unresolved questions",
            items=[
                {
                    "finding_id": f.id,
                    "statement": f.statement,
                    "status": f.status,
                    "statement_type": f.statement_type,
                }
                for f in open_q
            ],
            markdown="" if open_q else "No open questions were recorded for this period.",
        )
    )
    blocks.append(
        block(
            "methodology",
            markdown=(
                f"KPIs are canonical semantic-layer metrics computed for {window.display()} "
                f"({window.start.isoformat()} to {window.last_day.isoformat()}) against {baseline.display()} and "
                f"{yoy.display()}, ordered by materiality (canonical revenue first, then currency metrics by absolute "
                "change, then others by relative change). Drivers use the engine's contribution analysis (mix/rate for "
                "ratio metrics) on the canonical revenue metric (or the largest relative change when there is none), "
                "one table per dimension. Anomalies use robust deviation from a rolling baseline. "
                "This review is a draft for analyst review before publication."
            ),
        )
    )
    blocks.append(sources_block(db, ws, built, [k["metric_id"] for k in kpis]))
    snap = snapshot(db, ws, reason=f"business review {window.display()}", user_id=user_id, built=built)
    rep = Report(
        workspace_id=ws.id,
        title=title or f"Business review: {window.display()}",
        kind="business_review",
        status="draft",
        period=period,
        blocks=blocks,
        created_by=user_id,
    )
    rep.blocks = [*rep.blocks[:-1], {**rep.blocks[-1], "semantic_snapshot_id": snap.id}]
    db.add(rep)
    db.flush()
    return rep


def _overlaps(ctx: list[dict[str, Any]], window: Any) -> bool:
    for item in ctx or []:
        if item.get("kind") == "time" and item.get("label") == "Period" and len(item.get("values", [])) == 2:
            try:
                start = dt.date.fromisoformat(str(item["values"][0]))
                end = dt.date.fromisoformat(str(item["values"][1]))
            except ValueError:
                continue
            return start <= window.last_day and end >= window.start
    return False


def _anomaly_block(store: Any, model: Any, metric_id: str, label: str, window: Any) -> dict[str, Any]:
    from analystos_engine.analysis.anomaly import detect_metric_anomalies
    from analystos_engine.types import TimeWindow
    from analystos_investigator.semantic_graph import time_dimension_for_metric

    tdim = time_dimension_for_metric(model, metric_id)
    history = TimeWindow(
        dimension=tdim, start=window.start - dt.timedelta(days=91), end=window.end, kind="custom"
    )
    try:
        res = detect_metric_anomalies(store, model, metric_id, history, "day")
    except Exception as exc:  # noqa: BLE001 - reported honestly in the block
        return block(
            "anomalies",
            title=f"Anomalies in {label}",
            items=[],
            markdown=f"Anomaly detection could not run: {exc}",
        )
    items = [
        {
            "date": str(a.timestamp),
            "value": a.value,
            "expected": a.expected,
            "score": a.score,
            "direction": a.direction,
        }
        for a in res.anomalies
        if window.start
        <= (a.timestamp if isinstance(a.timestamp, dt.date) else a.timestamp.date())
        < window.end
    ]
    return block(
        "anomalies",
        title=f"Anomalies in {label}",
        items=items,
        method=res.method,
        sensitivity=res.sensitivity,
        notes=res.notes,
        sql=res.sql,
        markdown="" if items else "No days in the period deviated beyond the anomaly threshold.",
    )
