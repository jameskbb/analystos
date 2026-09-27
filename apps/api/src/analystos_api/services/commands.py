"""Chat commands as structured workspace actions (spec sections 29-30).

The investigator parses text into a typed ``Command`` with the same deterministic vocabulary as
question interpretation; this module performs the corresponding workspace action. Anything
ambiguous or unresolved is returned as a clarification, never guessed.
"""

from __future__ import annotations

from typing import Any, Literal

from analystos_investigator import (
    CommandContext,
    build_value_index,
    node_lineage,
    parse_command,
    resolve_ambiguity,
)
from analystos_investigator import Investigation as EngineInvestigation
from analystos_investigator import InvestigationRun as EngineRun
from sqlalchemy.orm import Session

from ..deps import WorkspaceContext
from ..errors import Forbidden
from ..models import Investigation
from ..schemas.investigations import CommandResult
from ..state import AppState
from . import investigations as inv_svc
from .findings import create_from_node
from .observability import audit
from .semantic import build_model


def _clarify(cmd: dict[str, Any], message: str) -> CommandResult:
    return CommandResult(command=cmd, message=message, action="clarify")


def execute_command(
    db: Session,
    state: AppState,
    ctx: WorkspaceContext,
    text: str,
    inv: Investigation | None,
    node_id: str | None,
) -> CommandResult:
    ws = ctx.workspace
    built = build_model(db, ws)
    store = state.stores.get(ws.id)
    doc = EngineInvestigation.model_validate(inv.document) if inv is not None and inv.document else None
    pctx = doc.plan.context if doc and doc.plan else None
    cctx = CommandContext(
        today=inv_svc.reference_date(ws),
        investigation_id=inv.id if inv else None,
        node_id=node_id,
        metric_id=pctx.metric_id if pctx else None,
        window=pctx.window if pctx else None,
        value_index=build_value_index(store, built.model),
    )
    cmd = parse_command(text, cctx, built.model)
    cmd_json = cmd.model_dump(mode="json")
    audit(
        db,
        action="command",
        resource_type="investigation" if inv else "workspace",
        resource_id=inv.id if inv else ws.id,
        workspace_id=ws.id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"text": text, "kind": cmd.kind},
    )
    if cmd.ambiguous:
        opts = "; ".join(f"'{a.term}': {', '.join(c.metric_id for c in a.candidates)}" for a in cmd.ambiguous)
        return _clarify(cmd_json, f"Which metric did you mean? {opts}")
    if cmd.kind == "unknown":
        return _clarify(
            cmd_json,
            "I did not recognise that command. Try 'break this down by region', "
            "'compare to last year', 'show the SQL', 'save this as a finding' or ask a question.",
        )
    if cmd.kind == "question":
        new = inv_svc.create_investigation(db, state, ws, question=text, user_id=ctx.user_id)
        return CommandResult(
            command=cmd_json,
            message=f"Started an investigation: {new.title}",
            action="created_investigation",
            investigation_id=new.id,
        )
    if cmd.unresolved:
        return _clarify(cmd_json, "; ".join(cmd.unresolved))
    if inv is None or doc is None:
        return _clarify(cmd_json, "Open an investigation first, or ask a question to start one.")
    if not doc.tree.root_id and cmd.kind not in {"rerun"}:
        return _clarify(cmd_json, "Run the investigation first.")
    target = cmd.target_node_id or node_id or doc.tree.root_id
    if target is not None:
        try:
            doc.tree.get(target)
        except KeyError:
            return _clarify(cmd_json, f"Node {target} is not in this investigation.")

    kind = cmd.kind
    if kind in {"breakdown", "drill", "top_contributors"}:
        return _breakdown(db, state, ctx, inv, target, cmd, cmd_json)
    if kind in {"compare", "exclude", "include", "switch_metric"}:
        return _branch(db, state, ctx, inv, doc, cmd, cmd_json, text)
    if kind == "save_finding":
        assert target is not None
        f, created = create_from_node(db, inv, target, user_id=ctx.user_id)
        verb = "Saved as a finding" if created else "Already saved as a finding"
        return CommandResult(
            command=cmd_json,
            message=f"{verb}: {f.statement}",
            action="saved_finding",
            investigation_id=inv.id,
            finding_id=f.id,
        )
    if kind == "show_sql":
        assert target is not None
        erun = inv_svc.engine_run(db, inv)
        rows = {a.engine_artifact_id: a for a in _artifact_rows(db, inv)}
        sql = [
            {
                "artifact_id": rows[a.id].id if a.id in rows else None,
                "engine_id": a.id,
                "title": a.title,
                "sql": a.sql,
            }
            for a in node_lineage(erun, target)
            if a.sql
        ]
        return CommandResult(
            command=cmd_json,
            message=f"{len(sql)} queries back this node.",
            action="show_sql",
            investigation_id=inv.id,
            sql=sql,
        )
    if kind == "build_report":
        if not ctx.can("editor"):
            raise Forbidden("Building a report requires the editor role", code="insufficient_role")
        from .reports import report_from_investigation

        rep = report_from_investigation(db, state, ws, inv, ctx.user_id)
        return CommandResult(
            command=cmd_json,
            message=f"Drafted report: {rep.title}",
            action="built_report",
            investigation_id=inv.id,
            report_id=rep.id,
        )
    if kind == "build_dashboard":
        from .dashboards import dashboard_from_investigation

        dash = dashboard_from_investigation(db, ws, inv, ctx.user_id)
        return CommandResult(
            command=cmd_json,
            message=f"Created dashboard: {dash.name}",
            action="built_dashboard",
            investigation_id=inv.id,
            dashboard_id=dash.id,
        )
    if kind == "rerun":
        job_id = state.jobs.submit(
            kind="investigation_rerun",
            fn=inv_svc.run_job(state, ws.id, inv.id, ctx.user_id, kind="rerun"),
            workspace_id=ws.id,
            user_id=ctx.user_id,
            resource_type="investigation",
            resource_id=inv.id,
            db=db,
        )
        return CommandResult(
            command=cmd_json,
            message="Rerunning the investigation on current data.",
            action="rerun_started",
            investigation_id=inv.id,
            job_id=job_id,
        )
    if kind in {"confirm_node", "reject_node"}:
        assert target is not None
        inv_svc.node_action(
            db, state, ws, inv, target, "confirm" if kind == "confirm_node" else "reject", None, ctx.user_id
        )
        return CommandResult(
            command=cmd_json, message="Node updated.", action="node_updated", investigation_id=inv.id
        )
    return _clarify(cmd_json, f"The command '{kind}' is not available here.")


def _artifact_rows(db: Session, inv: Investigation) -> list[Any]:
    from sqlalchemy import select

    from ..models import ArtifactRecord

    if not inv.current_run_id:
        return []
    return list(db.scalars(select(ArtifactRecord).where(ArtifactRecord.run_id == inv.current_run_id)))


def _breakdown(
    db: Session,
    state: AppState,
    ctx: WorkspaceContext,
    inv: Investigation,
    target: str | None,
    cmd: Any,
    cmd_json: dict[str, Any],
) -> CommandResult:
    assert target is not None and cmd.dimension
    doc = EngineInvestigation.model_validate(inv.document)

    def existing_group() -> Any:
        return next(
            (
                n
                for n in doc.tree.nodes
                if n.parent_id == target and n.dimension == cmd.dimension and n.kind == "dimension"
            ),
            None,
        )

    group = existing_group()
    if group is None:
        inv_svc.drill_node(db, state, ctx.workspace, inv, target, cmd.dimension, ctx.user_id)
        doc = EngineInvestigation.model_validate(inv.document)
        group = existing_group()
    segments = [
        n for n in doc.tree.nodes if group is not None and n.parent_id == group.id and n.kind == "segment"
    ]
    if cmd.kind == "drill" and cmd.filters:
        wanted = {str(v).lower() for f in cmd.filters for v in f.values}
        match = next((s for s in segments if s.segment and str(s.segment.value).lower() in wanted), None)
        if match is None:
            return _clarify(
                cmd_json, f"No segment matching {', '.join(sorted(wanted))} under {cmd.dimension}."
            )
        return CommandResult(
            command=cmd_json,
            message=f"Focus: {match.statement}",
            action="drilled",
            investigation_id=inv.id,
            items=[_node_item(match)],
        )
    segments.sort(
        key=lambda s: -((s.contribution_to_parent.share or 0.0) if s.contribution_to_parent else 0.0)
    )
    items = [_node_item(s) for s in segments]
    action: Literal["listed_contributors", "drilled"] = (
        "listed_contributors" if cmd.kind == "top_contributors" else "drilled"
    )
    lead = f"Top contributor: {segments[0].statement}" if segments else "No segments were found."
    return CommandResult(
        command=cmd_json,
        message=f"Broken down by {cmd.dimension}. {lead}",
        action=action,
        investigation_id=inv.id,
        items=items,
    )


def _node_item(n: Any) -> dict[str, Any]:
    c = n.contribution_to_parent
    return {
        "node_id": n.id,
        "statement": n.statement,
        "segment": n.segment.model_dump() if n.segment else None,
        "current": n.current,
        "baseline": n.baseline,
        "abs_change": n.abs_change,
        "pct_change": n.pct_change,
        "share": c.share if c else None,
        "evidence_strength": n.evidence_strength,
    }


def _branch(
    db: Session,
    state: AppState,
    ctx: WorkspaceContext,
    parent: Investigation,
    doc: EngineInvestigation,
    cmd: Any,
    cmd_json: dict[str, Any],
    text: str,
) -> CommandResult:
    """Follow-up branch: a new investigation with the parent's interpretation plus the requested change."""
    ws = ctx.workspace
    interp = doc.interpretation
    update: dict[str, Any] = {}
    if cmd.kind == "compare":
        if cmd.window is not None:
            update["window"] = cmd.window
        if cmd.baseline is not None:
            update["baseline"] = cmd.baseline
        if cmd.comparison_kind is not None:
            update["comparison_kind"] = cmd.comparison_kind
    elif cmd.kind in {"exclude", "include"}:
        update["filters"] = [*interp.filters, *cmd.filters]
    elif cmd.kind == "switch_metric" and cmd.metric_id:
        update["metric_ids"] = [cmd.metric_id]
        update["baseline_metric_id"] = None
    if not update:
        return _clarify(cmd_json, "Nothing to change; say which period, filter or metric to use.")
    new_interp = interp.model_copy(update={**update, "question": f"{interp.question} ({text})"})
    built = build_model(db, ws)
    child = Investigation(
        workspace_id=ws.id,
        question=new_interp.question,
        title=inv_svc.title_for(new_interp.question),
        template=parent.template,
        status="ready",
        created_by=ctx.user_id,
        options={"parent_investigation_id": parent.id, "command": text, "run_now": True},
    )
    db.add(child)
    db.flush()
    seed = EngineInvestigation(
        id=f"inv_{child.id[:16]}",
        question=new_interp.question,
        interpretation=new_interp,
        model_snapshot_hash=built.model.content_hash(),
    )
    erun = resolve_ambiguity(
        EngineRun(investigation=seed, artifacts=[]),
        state.stores.get(ws.id),
        built.model,
        {},
        config=inv_svc.execution_config(ws),
        auto_approve=inv_svc.auto_approve(ws),
    )
    if erun.investigation.tree.root_id:
        from analystos_investigator import followups

        erun.investigation.followups = followups(erun.investigation, built.model)
        inv_svc.persist_run(db, ws, child, erun, built=built, user_id=ctx.user_id)
    else:
        inv_svc.save_document(db, child, erun.investigation)
    return CommandResult(
        command=cmd_json,
        message=f"Branched into a follow-up investigation: {child.title}",
        action="branched",
        investigation_id=child.id,
    )
