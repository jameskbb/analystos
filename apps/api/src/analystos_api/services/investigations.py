"""Persistence and orchestration around ``analystos_investigator``.

The investigator is a pure library: it interprets, plans, executes real SQL through the
engine, and returns an ``InvestigationRun`` (investigation document + artifacts). This
module stores those documents, records exactly which metric versions, dataset versions and
semantic snapshot each run used, and replays state for drills, node actions, reruns and
diffs.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any

from analystos_investigator import (
    AnalysisPlan,
    ExecutionConfig,
    InvestigationDiff,
    InvestigationError,
    PlanningError,
    annotate_node,
    build_value_index,
    diff_runs,
    drill,
    followups,
    interpret,
    investigate,
    rerun,
    resolve_ambiguity,
    resolve_premise_period,
    run_investigation,
    set_node_status,
    update_plan,
)
from analystos_investigator import Artifact as EngineArtifact
from analystos_investigator import Investigation as EngineInvestigation
from analystos_investigator import InvestigationRun as EngineRun
from analystos_investigator import plan as make_plan
from analystos_investigator.hypotheses import generate as generate_hypotheses
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..errors import Conflict, NotFound, Unprocessable
from ..jobs import JobContext
from ..models import ArtifactRecord, Dataset, DatasetVersion, Investigation, InvestigationRun, Workspace
from ..state import AppState
from .ai import get_llm
from .observability import record_event, timed_event
from .semantic import BuiltModel, build_model, snapshot
from .workspaces import merged_settings

# ------------------------------------------------------------------------------ helpers


def reference_date(ws: Workspace) -> dt.date:
    """The "today" used to resolve relative periods ("last month", "August").

    Defaults to the real date; a workspace may pin it (the demo pins its as-of date so
    questions stay reproducible when the demo is loaded later)."""
    raw = merged_settings(ws).get("investigation", {}).get("reference_date")
    if raw:
        try:
            return dt.date.fromisoformat(str(raw))
        except ValueError:
            pass
    return dt.date.today()


def execution_config(ws: Workspace) -> ExecutionConfig:
    inv = merged_settings(ws).get("investigation", {})
    return ExecutionConfig(
        drill_depth=int(inv.get("max_depth", 2)), drill_top_n=int(inv.get("top_segments", 2))
    )


def auto_approve(ws: Workspace) -> bool:
    return not bool(merged_settings(ws).get("investigation", {}).get("require_plan_approval", True))


def get_investigation(db: Session, workspace_id: str, investigation_id: str) -> Investigation:
    inv = db.get(Investigation, investigation_id)
    if inv is None or inv.workspace_id != workspace_id:
        raise NotFound("Investigation not found")
    return inv


def title_for(question: str) -> str:
    q = " ".join(question.split())
    return q if len(q) <= 120 else q[:117] + "..."


def filter_context(
    *,
    window: Any = None,
    baseline: Any = None,
    filters: list[Any] | None = None,
    segments: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Human-readable filter chips (spec section 47): period, comparison, filters and segment path."""
    out: list[dict[str, Any]] = []
    for label, w in (("Period", window), ("Compared with", baseline)):
        if w is None:
            continue
        wd = w if isinstance(w, dict) else w.model_dump(mode="json")
        last = (dt.date.fromisoformat(wd["end"]) - dt.timedelta(days=1)).isoformat()
        out.append(
            {
                "label": label,
                "value": wd.get("label") or f"{wd['start']} to {last}",
                "kind": "time",
                "dimension": wd.get("dimension"),
                "op": "between",
                "values": [wd["start"], last],
            }
        )
    for f in filters or []:
        fd = f if isinstance(f, dict) else f.model_dump(mode="json")
        values = fd.get("values", [])
        verb = {
            "eq": "=",
            "in": "in",
            "neq": "≠",
            "not_in": "not in",
            "is_null": "is empty",
            "not_null": "is not empty",
        }.get(fd.get("op", "eq"), fd.get("op", "eq"))
        out.append(
            {
                "label": fd["dimension"],
                "value": f"{verb} {', '.join(map(str, values))}".strip(),
                "kind": "filter",
                "dimension": fd["dimension"],
                "op": fd.get("op", "eq"),
                "values": values,
            }
        )
    for s in segments or []:
        sd = s if isinstance(s, dict) else s.model_dump(mode="json")
        out.append(
            {
                "label": sd["dimension"],
                "value": str(sd.get("value")),
                "kind": "segment",
                "dimension": sd["dimension"],
                "op": "eq",
                "values": [sd.get("value")],
            }
        )
    return out


def metric_filter_chips(model: Any, metric_ids: list[str]) -> list[dict[str, Any]]:
    """Filters that live inside metric definitions (e.g. ``status != cancelled`` in Revenue), as
    ``kind: "metric"`` chips so no filter that shaped a number is hidden (spec §47, review R-31)."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for mid in metric_ids:
        if model is None or not model.has_metric(mid):
            continue
        try:
            deps = model.metric_dependencies(mid)
        except Exception:  # noqa: BLE001 - a broken definition surfaces elsewhere; chips are informational
            deps = [mid]
        for dep in deps:
            m = model.get_metric(dep)
            for f in m.filters:
                key = (f.dimension, f.op, repr(f.values))
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "label": f"{m.display_name} definition",
                        "value": f.describe(),
                        "kind": "metric",
                        "dimension": f.dimension,
                        "op": f.op,
                        "values": list(f.values),
                        "metric_id": dep,
                    }
                )
    return out


def _context_of(doc: EngineInvestigation) -> Any:
    return doc.plan.context if doc.plan and doc.plan.context else None


# ------------------------------------------------------------------------------ loading / saving


def engine_run(db: Session, inv: Investigation, run_id: str | None = None) -> EngineRun:
    """Rebuild the investigator's ``InvestigationRun`` for the current (or a given) run."""
    rid = run_id or inv.current_run_id
    if run_id:
        run = db.get(InvestigationRun, run_id)
        if run is None or run.investigation_id != inv.id:
            raise NotFound("Run not found")
        doc = EngineInvestigation.model_validate(run.document)
    else:
        doc = EngineInvestigation.model_validate(inv.document)
    arts: list[EngineArtifact] = []
    if rid:
        rows = db.scalars(
            select(ArtifactRecord)
            .where(ArtifactRecord.run_id == rid)
            .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
        ).all()
        arts = [EngineArtifact.model_validate(r.document) for r in rows]
    return EngineRun(investigation=doc, artifacts=arts)


def _artifact_row(
    a: EngineArtifact,
    *,
    workspace_id: str,
    investigation_id: str,
    run_id: str,
    user_id: str | None,
    model: Any = None,
) -> ArtifactRecord:
    chips = filter_context(window=a.window, filters=a.filters)
    chips += metric_filter_chips(model, list(dict.fromkeys([*a.metric_ids, *a.metric_versions])))
    return ArtifactRecord(
        workspace_id=workspace_id,
        investigation_id=investigation_id,
        run_id=run_id,
        engine_artifact_id=a.id,
        kind=a.kind,
        title=a.title[:400],
        sql=a.sql,
        python=a.python,
        params=a.params,
        filters=[f.model_dump(mode="json") for f in a.filters],
        filter_context=chips,
        metric_versions=dict(a.metric_versions),
        dataset_versions=[d.model_dump(mode="json") for d in a.dataset_versions],
        result=a.result.model_dump(mode="json") if a.result else None,
        chart_spec=a.chart_spec,
        validation=a.validation.model_dump(mode="json") if a.validation else None,
        parent_ids=list(a.parent_ids),
        document=a.model_dump(mode="json"),
        origin="investigation",
        created_by=user_id,
    )


def _metric_ids_used(erun: EngineRun) -> list[str]:
    ids: list[str] = []
    ctx = _context_of(erun.investigation)
    if ctx is not None:
        ids.append(ctx.metric_id)
    for a in erun.artifacts:
        for mid in [*a.metric_ids, *a.metric_versions.keys()]:
            if mid not in ids:
                ids.append(mid)
    return ids


def _dataset_versions_used(db: Session, workspace_id: str, erun: EngineRun) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for a in erun.artifacts:
        for d in a.dataset_versions:
            if d.table in out:
                continue
            entry: dict[str, Any] = {"content_hash": d.content_hash, "row_count": d.row_count}
            ds = db.scalar(
                select(Dataset).where(Dataset.workspace_id == workspace_id, Dataset.table_name == d.table)
            )
            if ds is not None:
                ver = db.scalar(
                    select(DatasetVersion).where(
                        DatasetVersion.dataset_id == ds.id, DatasetVersion.content_hash == d.content_hash
                    )
                )
                entry |= {
                    "dataset_id": ds.id,
                    "version_id": ver.id if ver else None,
                    "version_no": ver.version_no if ver else None,
                }
            out[d.table] = entry
    return out


def save_document(db: Session, inv: Investigation, doc: EngineInvestigation) -> None:
    inv.document = doc.model_dump(mode="json")
    inv.status = doc.status
    inv.engine_version = doc.engine_version
    inv.updated_at = utcnow()
    if inv.current_run_id:
        run = db.get(InvestigationRun, inv.current_run_id)
        if run is not None:
            run.document = inv.document


def add_artifacts(
    db: Session, inv: Investigation, erun: EngineRun, user_id: str | None, model: Any = None
) -> int:
    """Persist artifacts produced after the run was stored (e.g. by a drill)."""
    if not inv.current_run_id:
        return 0
    have = set(
        db.scalars(
            select(ArtifactRecord.engine_artifact_id).where(ArtifactRecord.run_id == inv.current_run_id)
        ).all()
    )
    added = 0
    for a in erun.artifacts:
        if a.id not in have:
            db.add(
                _artifact_row(
                    a,
                    workspace_id=inv.workspace_id,
                    investigation_id=inv.id,
                    run_id=inv.current_run_id,
                    user_id=user_id,
                    model=model,
                )
            )
            have.add(a.id)
            added += 1
    return added


def persist_run(
    db: Session,
    ws: Workspace,
    inv: Investigation,
    erun: EngineRun,
    *,
    built: BuiltModel,
    user_id: str | None,
    kind: str = "run",
    diff: InvestigationDiff | None = None,
    duration_ms: float = 0.0,
) -> InvestigationRun:
    doc = erun.investigation
    run_no = inv.run_count + 1
    snap = snapshot(db, ws, reason=f"investigation run {run_no}", user_id=user_id, built=built)
    metric_versions = built.version_map([m for m in _metric_ids_used(erun) if m in built.metric_versions])
    datasets = _dataset_versions_used(db, ws.id, erun)
    run = InvestigationRun(
        investigation_id=inv.id,
        workspace_id=ws.id,
        run_no=run_no,
        kind=kind,
        status="failed" if doc.status == "failed" else "completed",
        document=doc.model_dump(mode="json"),
        metric_version_ids=metric_versions,
        dataset_versions=datasets,
        semantic_snapshot_id=snap.id,
        diff=diff.model_dump(mode="json") if diff is not None else None,
        error="; ".join(doc.failures) if doc.status == "failed" else None,
        duration_ms=duration_ms,
        triggered_by=user_id,
        finished_at=utcnow(),
    )
    db.add(run)
    db.flush()
    for a in erun.artifacts:
        db.add(
            _artifact_row(
                a,
                workspace_id=ws.id,
                investigation_id=inv.id,
                run_id=run.id,
                user_id=user_id,
                model=built.model,
            )
        )
    inv.current_run_id = run.id
    inv.run_count = run_no
    inv.metric_version_ids = metric_versions
    inv.dataset_versions = datasets
    inv.semantic_snapshot_id = snap.id
    inv.error = run.error
    save_document(db, inv, doc)
    db.flush()
    return run


# ------------------------------------------------------------------------------ creation / planning


def create_investigation(
    db: Session,
    state: AppState,
    ws: Workspace,
    *,
    question: str,
    user_id: str | None,
    template: str | None = None,
    choices: dict[str, str] | None = None,
    run_now: bool = True,
) -> Investigation:
    built = build_model(db, ws)
    if not built.model.metrics:
        raise Unprocessable("Define at least one metric before asking questions", code="no_metrics")
    store = state.stores.get(ws.id)
    inv = Investigation(
        workspace_id=ws.id,
        question=question,
        title=title_for(question),
        template=template,
        status="ready",
        created_by=user_id,
        options={"run_now": run_now},
    )
    db.add(inv)
    db.flush()
    llm = get_llm(state, ws, investigation_id=inv.id)
    start = time.perf_counter()
    with timed_event(
        state.session_factory,
        category="investigation",
        name="create",
        workspace_id=ws.id,
        user_id=user_id,
        detail={"investigation_id": inv.id},
    ) as ev:
        value_index = build_value_index(store, built.model)
        if run_now and llm is not None and template is None:
            # AI is configured: the staged orchestrator (spec §51-53) runs the same deterministic pipeline and lets
            # the model add validated plan steps, run bounded tool calls and word the answer (verified numbers).
            erun = _orchestrate(state, ws, inv, built, store, llm, question, choices, value_index)
            ev.detail["orchestrated"] = True
        elif run_now:
            erun = investigate(
                question,
                store,
                built.model,
                reference_date(ws),
                llm=llm,
                choices=choices,
                value_index=value_index,
                config=execution_config(ws),
                template=template,
                auto_approve=auto_approve(ws),
            )
        else:
            interp = interpret(
                question, built.model, reference_date(ws), llm, value_index=value_index, choices=choices
            )
            interp = resolve_premise_period(interp, store, built.model)
            doc = EngineInvestigation(
                id=f"inv_{inv.id[:16]}",
                question=question,
                interpretation=interp,
                model_snapshot_hash=built.model.content_hash(),
            )
            erun = _plan_only(doc, built, ws, template)
        ev.detail["status"] = erun.investigation.status
    doc = erun.investigation
    if doc.tree.root_id and "orchestration" not in (inv.options or {}):
        doc.followups = followups(doc, built.model)  # the orchestrator already produced (verified) follow-ups
    if doc.tree.root_id:
        persist_run(
            db, ws, inv, erun, built=built, user_id=user_id, duration_ms=(time.perf_counter() - start) * 1000
        )
    else:
        save_document(db, inv, doc)
        snap = snapshot(db, ws, reason="investigation planned", user_id=user_id, built=built)
        inv.semantic_snapshot_id = snap.id
        ctx = _context_of(doc)
        if ctx is not None:
            inv.metric_version_ids = built.version_map([ctx.metric_id])
    return inv


def _orchestrate(
    state: AppState,
    ws: Workspace,
    inv: Investigation,
    built: BuiltModel,
    store: Any,
    llm: Any,
    question: str,
    choices: dict[str, str] | None,
    value_index: Any,
) -> EngineRun:
    """Run the LLM orchestrator and record what the model contributed (and what validation refused).

    Numbers still come only from executed artifacts: the orchestrator builds the tree with the deterministic
    executor; model output can add plan steps that pass validation, request tools the registry executes, and
    word a narrative that must pass the numeric-claim verifier."""
    from analystos_investigator.llm.orchestrator import Orchestrator

    ai = merged_settings(ws).get("ai", {})
    res = Orchestrator(store, built.model, llm, config=execution_config(ws)).run(
        question,
        reference_date(ws),
        choices=choices,
        auto_approve=auto_approve(ws),
        explore=bool(ai.get("tool_loop")),
        value_index=value_index,
    )
    doc = res.run.investigation
    deterministic_brief = doc.brief_answer
    if res.narrative_source == "llm_verified" and res.narrative:
        doc.brief_answer = res.narrative
    added = [st.id for st in (doc.plan.steps if doc.plan else []) if st.origin == "llm"]
    inv.options = {
        **(inv.options or {}),
        "orchestration": {
            "stages": [
                {
                    "stage": r.stage,
                    "source": r.source,
                    "ok": r.ok,
                    "detail": r.detail[:500],
                    "duration_ms": round(r.duration_ms, 1),
                }
                for r in res.stages
            ],
            "narrative_source": res.narrative_source,
            "rejected_outputs": [r[:500] for r in res.rejected_outputs],
            "tool_calls": [
                {"tool": t.tool, "ok": t.ok, "error": t.error, "artifact_ids": list(t.artifact_ids)}
                for t in res.tool_calls
            ],
            "plan_steps_added": added,
            "deterministic_brief": deterministic_brief,
        },
    }
    return EngineRun(investigation=doc, artifacts=res.run.artifacts)


def _plan_only(doc: EngineInvestigation, built: BuiltModel, ws: Workspace, template: str | None) -> EngineRun:
    interp = doc.interpretation
    if interp.ambiguous or not interp.metric_ids:
        doc.status = "needs_disambiguation"
        if not interp.metric_ids and not interp.ambiguous:
            doc.failures.append("no metric from the semantic model was recognised in the question")
        return EngineRun(investigation=doc, artifacts=[])
    try:
        p = make_plan(interp, built.model, template=template, config=execution_config(ws))
    except PlanningError as exc:
        doc.status = "failed"
        doc.failures.append(str(exc))
        return EngineRun(investigation=doc, artifacts=[])
    doc.plan = p
    doc.hypotheses = generate_hypotheses(interp, built.model, p)
    doc.status = "awaiting_approval"
    return EngineRun(investigation=doc, artifacts=[])


def disambiguate(
    db: Session,
    state: AppState,
    ws: Workspace,
    inv: Investigation,
    choices: dict[str, str],
    user_id: str | None,
) -> Investigation:
    built = build_model(db, ws)
    unknown = [m for m in choices.values() if not built.model.has_metric(m)]
    if unknown:
        raise Unprocessable(f"Unknown metrics: {unknown}", code="unknown_metric")
    prev = engine_run(db, inv)
    run_now = bool(inv.options.get("run_now", True))
    start = time.perf_counter()
    erun = resolve_ambiguity(
        prev,
        state.stores.get(ws.id),
        built.model,
        choices,
        config=execution_config(ws),
        auto_approve=auto_approve(ws) and run_now,
    )
    if (
        not run_now
        and erun.investigation.status not in {"needs_disambiguation", "failed"}
        and not erun.investigation.tree.root_id
    ):
        erun.investigation.status = "awaiting_approval"
    doc = erun.investigation
    if doc.tree.root_id:
        doc.followups = followups(doc, built.model)
        persist_run(
            db, ws, inv, erun, built=built, user_id=user_id, duration_ms=(time.perf_counter() - start) * 1000
        )
    else:
        save_document(db, inv, doc)
    return inv


def replan(
    db: Session, state: AppState, ws: Workspace, inv: Investigation, template: str | None = None
) -> Investigation:
    built = build_model(db, ws)
    doc = EngineInvestigation.model_validate(inv.document)
    doc.interpretation = resolve_premise_period(doc.interpretation, state.stores.get(ws.id), built.model)
    try:
        p = make_plan(
            doc.interpretation, built.model, template=template or inv.template, config=execution_config(ws)
        )
    except PlanningError as exc:
        raise Unprocessable(str(exc), code="planning_failed") from exc
    save_document(db, inv, update_plan(doc, p, built.model))
    return inv


def set_plan(db: Session, ws: Workspace, inv: Investigation, plan: AnalysisPlan) -> Investigation:
    built = build_model(db, ws)
    doc = EngineInvestigation.model_validate(inv.document)
    if plan.context is None:
        existing = doc.plan.context if doc.plan else None
        if existing is None:
            raise Unprocessable(
                "The plan has no context (metric and periods); re-plan first", code="plan_context"
            )
        plan = plan.model_copy(update={"context": existing})
    try:
        new_doc = update_plan(doc, plan, built.model)
    except InvestigationError as exc:
        raise Unprocessable(str(exc), code="invalid_plan") from exc
    save_document(db, inv, new_doc)
    return inv


# ------------------------------------------------------------------------------ jobs


def run_job(
    state: AppState,
    workspace_id: str,
    investigation_id: str,
    user_id: str | None,
    *,
    kind: str = "run",
    pin_definitions: bool = False,
) -> Callable[[JobContext], dict[str, Any]]:
    def job(ctx: JobContext) -> dict[str, Any]:
        with state.session_factory() as db:
            ws = db.get(Workspace, workspace_id)
            inv = get_investigation(db, workspace_id, investigation_id)
            assert ws is not None
            pinned = None
            if pin_definitions and inv.metric_version_ids:
                pinned = {m: v["version_id"] for m, v in inv.metric_version_ids.items()}
            built = build_model(db, ws, pinned_versions=pinned)
            prev = engine_run(db, inv) if kind == "rerun" else None
            doc = EngineInvestigation.model_validate(inv.document)
            inv.status = "running"
            db.commit()
            store = state.stores.get(workspace_id)
            ctx.progress(0.1, "Executing analysis plan")
            start = time.perf_counter()
            try:
                with timed_event(
                    state.session_factory,
                    category="investigation",
                    name=kind,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    detail={"investigation_id": investigation_id},
                ) as ev:
                    diff = None
                    if kind == "rerun":
                        if prev is None or prev.investigation.plan is None:
                            raise InvestigationError("the investigation has no plan to rerun")
                        erun, diff = rerun(prev, store, built.model)
                    else:
                        if doc.plan is None:
                            raise InvestigationError(
                                "the investigation has no plan; resolve ambiguity or re-plan"
                            )
                        erun = run_investigation(doc, store, built.model)
                    erun.investigation.followups = followups(erun.investigation, built.model)
                    ev.detail |= {
                        "status": erun.investigation.status,
                        "artifacts": len(erun.artifacts),
                        "nodes": len(erun.investigation.tree.nodes),
                    }
            except Exception as exc:
                db.rollback()
                inv = get_investigation(db, workspace_id, investigation_id)
                inv.status = "failed"
                inv.error = f"{type(exc).__name__}: {exc}"
                db.commit()
                raise
            ctx.progress(0.9, "Saving results")
            run = persist_run(
                db,
                ws,
                inv,
                erun,
                built=built,
                user_id=user_id,
                kind=kind,
                diff=diff,
                duration_ms=(time.perf_counter() - start) * 1000,
            )
            db.commit()
            return {
                "investigation_id": investigation_id,
                "run_id": run.id,
                "run_no": run.run_no,
                "status": run.status,
                "has_changes": bool(diff and diff.has_changes),
            }

    return job


# ------------------------------------------------------------------------------ node operations


def node_action(
    db: Session,
    state: AppState,
    ws: Workspace,
    inv: Investigation,
    node_id: str,
    action: str,
    note: str | None,
    user_id: str | None,
) -> Investigation:
    erun = engine_run(db, inv)
    doc = erun.investigation
    try:
        doc.tree.get(node_id)
    except KeyError as exc:
        raise NotFound("Node not found") from exc
    try:
        if action in {"confirm", "reject", "needs_review"}:
            status = {"confirm": "confirmed", "reject": "rejected", "needs_review": "needs_review"}[action]
            doc = set_node_status(doc, node_id, status)  # type: ignore[arg-type]
            if note:
                doc = annotate_node(doc, node_id, note)
        elif action == "annotate":
            doc = annotate_node(doc, node_id, note or "")
        elif action == "rerun":
            doc = _rerun_node(state, ws, erun, node_id)
        else:
            raise Unprocessable(f"Unknown action {action!r}", code="unknown_action")
    except InvestigationError as exc:
        raise Conflict(str(exc), code="invalid_node_action") from exc
    save_document(db, inv, doc)
    return inv


def _rerun_node(state: AppState, ws: Workspace, erun: EngineRun, node_id: str) -> EngineInvestigation:
    """Re-execute the node's stored queries on the current data and note whether results match."""
    from analystos_investigator import node_lineage

    doc = erun.investigation
    node = doc.tree.get(node_id)
    store = state.stores.get(ws.id)
    same, differ, failed = 0, 0, 0
    for a in node_lineage(erun, node_id):
        if not a.sql or a.result is None:
            continue
        try:
            res = store.execute_read(
                a.sql, None, max(a.result.row_count, 1) + 1, state.settings.query_timeout_s
            )
        except Exception:  # noqa: BLE001 - reported in the note
            failed += 1
            continue
        if _rows_equal(res.model_dump(mode="json")["rows"][: len(a.result.rows)], a.result.rows):
            same += 1
        else:
            differ += 1
    stamp = utcnow().strftime("%Y-%m-%d %H:%M UTC")
    note = (
        f"Re-executed {same + differ + failed} stored queries at {stamp}: {same} identical, {differ} changed"
        + (f", {failed} failed" if failed else "")
        + "."
    )
    notes = [*node.notes, note]
    nodes = [n.model_copy(update={"notes": notes}) if n.id == node_id else n for n in doc.tree.nodes]
    record_event(
        state.session_factory,
        category="investigation",
        name="rerun_node",
        workspace_id=ws.id,
        detail={"node_id": node_id, "identical": same, "changed": differ, "failed": failed},
    )
    return doc.model_copy(update={"tree": doc.tree.model_copy(update={"nodes": nodes})})


def _rows_equal(a: list[list[Any]], b: list[list[Any]]) -> bool:
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b, strict=True):
        for va, vb in zip(ra, rb, strict=False):
            if isinstance(va, float) and isinstance(vb, int | float):
                if abs(va - float(vb)) > 1e-9 * max(1.0, abs(va)):
                    return False
            elif va != vb and str(va) != str(vb):
                return False
    return True


def drill_node(
    db: Session,
    state: AppState,
    ws: Workspace,
    inv: Investigation,
    node_id: str,
    dimension: str,
    user_id: str | None,
) -> Investigation:
    built = build_model(db, ws)
    if not built.model.has_dimension(dimension):
        raise Unprocessable(f"Unknown dimension {dimension!r}", code="unknown_dimension")
    erun = engine_run(db, inv)
    try:
        erun.investigation.tree.get(node_id)
    except KeyError as exc:
        raise NotFound("Node not found") from exc
    with timed_event(
        state.session_factory,
        category="investigation",
        name="drill",
        workspace_id=ws.id,
        user_id=user_id,
        detail={"node_id": node_id, "dimension": dimension},
    ):
        try:
            new = drill(erun, state.stores.get(ws.id), built.model, node_id, dimension)
        except (InvestigationError, ValueError) as exc:
            raise Unprocessable(str(exc), code="drill_failed") from exc
    add_artifacts(db, inv, new, user_id, built.model)
    save_document(db, inv, new.investigation)
    return inv


def run_diff(db: Session, inv: Investigation, from_run: str | None, to_run: str | None) -> dict[str, Any]:
    runs = db.scalars(
        select(InvestigationRun)
        .where(InvestigationRun.investigation_id == inv.id)
        .order_by(InvestigationRun.run_no)
    ).all()
    if not runs:
        raise NotFound("The investigation has not run yet")
    by_id = {r.id: r for r in runs}
    for rid in (from_run, to_run):
        if rid and rid not in by_id:
            raise NotFound(f"Run {rid} not found")
    after = by_id[to_run] if to_run else runs[-1]
    if from_run:
        before = by_id[from_run]
    else:
        earlier = [r for r in runs if r.run_no < after.run_no]
        if not earlier:
            raise Unprocessable("Only one run exists; rerun the investigation to compare", code="single_run")
        before = earlier[-1]
    if not from_run and after.diff and before.run_no == after.run_no - 1:
        diff = InvestigationDiff.model_validate(after.diff)
    else:
        diff = diff_runs(engine_run(db, inv, before.id), engine_run(db, inv, after.id))
    return {
        "from_run_id": before.id,
        "from_run_no": before.run_no,
        "to_run_id": after.id,
        "to_run_no": after.run_no,
        "diff": diff,
    }


# ------------------------------------------------------------------------------ anomaly investigations


def investigate_anomaly(
    db: Session, state: AppState, ws: Workspace, body: Any, user_id: str | None
) -> Investigation:
    """Investigate an anomalous day or month flagged by ``/analysis/anomalies`` (spec §40).

    A day is compared with the same weekday one week earlier (or the previous day) through the
    investigator's anomaly template; a month is compared with the previous month through the standard
    change investigation. Both run immediately: the trigger is an explicit analyst action.
    """
    from analystos_engine.calendar import add_months
    from analystos_engine.types import TimeWindow
    from analystos_investigator import FilterSpec
    from analystos_investigator import Interpretation as EngineInterpretation
    from analystos_investigator import investigate_anomaly as engine_investigate_anomaly
    from analystos_investigator.templates import choose_template

    built = build_model(db, ws)
    if not built.model.has_metric(body.metric_id):
        raise NotFound(f"Metric {body.metric_id!r} not found")
    for f in body.filters:
        if not built.model.has_dimension(f.dimension):
            raise NotFound(f"Dimension {f.dimension!r} not found")
    metric = built.model.get_metric(body.metric_id)
    filters = [
        FilterSpec(dimension=f.dimension, op=f.op, values=[str(v) for v in f.values]) for f in body.filters
    ]
    store = state.stores.get(ws.id)
    if body.grain == "day":
        question = f"Why did {metric.display_name} move unexpectedly on {body.date.isoformat()}?"
    else:
        start = body.date.replace(day=1)
        question = f"Why did {metric.display_name} change in {start.strftime('%B %Y')}?"
    inv = Investigation(
        workspace_id=ws.id,
        question=question,
        title=title_for(question),
        template="anomaly",
        status="running",
        created_by=user_id,
        options={"run_now": True, "origin": "anomaly"},
    )
    db.add(inv)
    db.flush()
    t0 = time.perf_counter()
    with timed_event(
        state.session_factory,
        category="investigation",
        name="anomaly",
        workspace_id=ws.id,
        user_id=user_id,
        detail={"investigation_id": inv.id, "grain": body.grain},
    ) as ev:
        try:
            if body.grain == "day":
                erun = engine_investigate_anomaly(
                    store,
                    built.model,
                    body.metric_id,
                    body.date,
                    baseline=body.baseline,
                    filters=filters,
                    config=execution_config(ws),
                    question=question,
                )
            else:
                start = body.date.replace(day=1)
                window = TimeWindow(
                    start=start, end=add_months(start, 1), kind="month", label=start.strftime("%B %Y")
                )
                prev = add_months(start, -1)
                base = TimeWindow(start=prev, end=start, kind="month", label=prev.strftime("%B %Y"))
                interp = EngineInterpretation(
                    question=question,
                    metric_ids=[body.metric_id],
                    window=window,
                    baseline=base,
                    comparison_kind="pop",
                    filters=filters,
                    intent="why_change",
                    confidence_notes=["opened from an anomaly: month vs previous month"],
                )
                interp.template_id = choose_template(interp, built.model).id
                p = make_plan(interp, built.model, config=execution_config(ws))
                doc = EngineInvestigation(
                    id=f"inv_{inv.id[:16]}",
                    question=question,
                    interpretation=interp,
                    plan=p,
                    hypotheses=generate_hypotheses(interp, built.model, p),
                    model_snapshot_hash=built.model.content_hash(),
                )
                erun = run_investigation(doc, store, built.model)
        except (InvestigationError, PlanningError, ValueError, KeyError) as exc:
            raise Unprocessable(
                f"Could not investigate this anomaly: {exc}", code="investigation_failed"
            ) from exc
        ev.detail["status"] = erun.investigation.status
    erun.investigation.followups = followups(erun.investigation, built.model)
    persist_run(
        db, ws, inv, erun, built=built, user_id=user_id, duration_ms=(time.perf_counter() - t0) * 1000
    )
    return inv
