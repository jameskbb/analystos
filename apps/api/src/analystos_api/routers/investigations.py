"""Investigations: interpret -> plan -> (edit plan) -> run -> tree -> drill / node actions -> rerun -> diff."""

from __future__ import annotations

from typing import Annotated

from analystos_investigator import (
    TEMPLATES,
    FindingInput,
    Interpretation,
    build_value_index,
    executive_summary,
)
from analystos_investigator import AnalysisPlan as EnginePlan
from analystos_investigator import Investigation as EngineInvestigation
from analystos_investigator import interpret as interpret_question
from fastapi import APIRouter, Query, Response
from sqlalchemy import or_, select

from ..deps import READ_ONLY_POST, DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import Conflict
from ..models import ArtifactRecord, Finding, Investigation, InvestigationRun
from ..schemas.common import ERROR_RESPONSES, JobAccepted, OkResponse
from ..schemas.findings import FindingOut
from ..schemas.investigations import (
    ArtifactOut,
    CommandRequest,
    CommandResult,
    DiffOut,
    DisambiguateRequest,
    DrillRequest,
    InterpretRequest,
    InvestigationCreate,
    InvestigationOut,
    InvestigationRunOut,
    InvestigationSummary,
    InvestigationTreeOut,
    InvestigationUpdate,
    NodeActionRequest,
    OrchestrationOut,
    PromoteRequest,
    ReplanRequest,
    RunRequest,
    SummaryOut,
    TemplateOut,
    TreeNodeOut,
)
from ..schemas.notebooks import NotebookOut
from ..services import investigations as svc
from ..services.ai import get_llm
from ..services.commands import execute_command
from ..services.findings import create_from_node, node_findings
from ..services.jobs_view import job_accepted
from ..services.notebooks import notebook_from_investigation, notebook_out
from ..services.observability import audit
from ..services.semantic import build_model

router = APIRouter(
    prefix="/workspaces/{workspace_id}/investigations", tags=["investigations"], responses=ERROR_RESPONSES
)


def _tree_out(doc: EngineInvestigation | None, findings: dict[str, str]) -> InvestigationTreeOut | None:
    if doc is None or not doc.tree.root_id:
        return None
    nodes = [
        TreeNodeOut.model_validate({**n.model_dump(), "finding_id": findings.get(n.id)})
        for n in doc.tree.nodes
    ]
    return InvestigationTreeOut(root_id=doc.tree.root_id, nodes=nodes)


def investigation_out(db: DbDep, inv: Investigation) -> InvestigationOut:
    doc = EngineInvestigation.model_validate(inv.document) if inv.document else None
    findings = node_findings(db, inv.id)
    orch = (inv.options or {}).get("orchestration")
    return InvestigationOut(
        id=inv.id,
        question=inv.question,
        title=inv.title,
        template=inv.template,
        status=inv.status,  # type: ignore[arg-type]
        interpretation=doc.interpretation if doc else None,
        plan=doc.plan if doc else None,
        hypotheses=doc.hypotheses if doc else [],
        tree=_tree_out(doc, findings),
        brief_answer=doc.brief_answer if doc else None,
        followups=doc.followups if doc else [],
        failures=doc.failures if doc else [],
        metric_version_ids=inv.metric_version_ids or {},
        dataset_versions=inv.dataset_versions or {},
        semantic_snapshot_id=inv.semantic_snapshot_id,
        engine_version=inv.engine_version,
        run_count=inv.run_count,
        current_run_id=inv.current_run_id,
        node_findings=findings,
        orchestration=OrchestrationOut.model_validate(orch) if orch else None,
        error=inv.error,
        created_by=inv.created_by,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )


def _audit(
    db: DbDep, ctx: EditorCtx, action: str, inv_id: str, detail: dict[str, object] | None = None
) -> None:
    audit(
        db,
        action=action,
        resource_type="investigation",
        resource_id=inv_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail=detail or {},
    )


@router.get("", response_model=list[InvestigationSummary], operation_id="listInvestigations")
def list_investigations(
    ctx: ViewerCtx,
    db: DbDep,
    status: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[InvestigationSummary]:
    stmt = select(Investigation).where(Investigation.workspace_id == ctx.workspace_id)
    if status:
        stmt = stmt.where(Investigation.status == status)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Investigation.question.ilike(like), Investigation.title.ilike(like)))
    out = []
    for inv in db.scalars(stmt.order_by(Investigation.updated_at.desc()).limit(limit)):
        item = InvestigationSummary.model_validate(inv)
        item.brief_answer = (inv.document or {}).get("brief_answer")
        out.append(item)
    return out


@router.get("/templates", response_model=list[TemplateOut], operation_id="listInvestigationTemplates")
def templates(ctx: ViewerCtx) -> list[TemplateOut]:
    items = TEMPLATES.values() if isinstance(TEMPLATES, dict) else TEMPLATES
    return [
        TemplateOut(
            id=t.id,
            name=t.name,
            description=t.description,
            metric_roles=t.metric_roles,
            intents=t.intents,
            checks=[c.model_dump() for c in t.checks],
        )
        for t in items
    ]


@router.post(
    "/interpret",
    response_model=Interpretation,
    operation_id="interpretQuestion",
    openapi_extra=READ_ONLY_POST,
)
def interpret(body: InterpretRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> Interpretation:
    """Interpret a question against the semantic model without creating an investigation."""
    built = build_model(db, ctx.workspace)
    store = state.stores.get(ctx.workspace_id)
    return interpret_question(
        body.question,
        built.model,
        svc.reference_date(ctx.workspace),
        get_llm(state, ctx.workspace),
        value_index=build_value_index(store, built.model),
        choices=body.choices or None,
    )


@router.post("", response_model=InvestigationOut, status_code=201, operation_id="createInvestigation")
def create(body: InvestigationCreate, ctx: EditorCtx, db: DbDep, state: StateDep) -> InvestigationOut:
    """Interpret and plan a question. Cheap plans run immediately (``auto_run``); expensive plans wait for
    approval (``awaiting_approval``); ambiguous metric references stop at ``needs_disambiguation``."""
    inv = svc.create_investigation(
        db,
        state,
        ctx.workspace,
        question=body.question,
        user_id=ctx.user_id,
        template=body.template,
        choices=body.choices or None,
        run_now=body.auto_run,
    )
    _audit(db, ctx, "investigation.create", inv.id, {"question": body.question, "status": inv.status})
    return investigation_out(db, inv)


@router.get("/{investigation_id}", response_model=InvestigationOut, operation_id="getInvestigation")
def get(investigation_id: str, ctx: ViewerCtx, db: DbDep) -> InvestigationOut:
    return investigation_out(db, svc.get_investigation(db, ctx.workspace_id, investigation_id))


@router.patch("/{investigation_id}", response_model=InvestigationOut, operation_id="updateInvestigation")
def update(investigation_id: str, body: InvestigationUpdate, ctx: EditorCtx, db: DbDep) -> InvestigationOut:
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    if body.title is not None:
        inv.title = body.title
    db.flush()
    return investigation_out(db, inv)


@router.delete("/{investigation_id}", response_model=OkResponse, operation_id="deleteInvestigation")
def delete(investigation_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    if inv.status == "running":
        raise Conflict("The investigation is running", code="running")
    _audit(db, ctx, "investigation.delete", inv.id, {"question": inv.question})
    db.delete(inv)
    return OkResponse()


@router.post(
    "/{investigation_id}/disambiguate",
    response_model=InvestigationOut,
    operation_id="disambiguateInvestigation",
)
def disambiguate(
    investigation_id: str, body: DisambiguateRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> InvestigationOut:
    """Choose among canonical candidates for an ambiguous term (the engine never guesses)."""
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    svc.disambiguate(db, state, ctx.workspace, inv, body.choices, ctx.user_id)
    _audit(db, ctx, "investigation.disambiguate", inv.id, {"choices": body.choices})
    return investigation_out(db, inv)


@router.post("/{investigation_id}/plan", response_model=InvestigationOut, operation_id="replanInvestigation")
def replan(
    investigation_id: str, body: ReplanRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> InvestigationOut:
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    svc.replan(db, state, ctx.workspace, inv, body.template)
    return investigation_out(db, inv)


@router.put(
    "/{investigation_id}/plan", response_model=InvestigationOut, operation_id="updateInvestigationPlan"
)
def put_plan(investigation_id: str, plan: EnginePlan, ctx: EditorCtx, db: DbDep) -> InvestigationOut:
    """Replace the plan after analyst edits (enable/disable/remove/add steps, change parameters)."""
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    if inv.status == "running":
        raise Conflict("The investigation is running", code="running")
    svc.set_plan(db, ctx.workspace, inv, plan)
    _audit(
        db,
        ctx,
        "investigation.plan_edit",
        inv.id,
        {"steps": len(plan.steps), "enabled": len(plan.enabled_steps())},
    )
    return investigation_out(db, inv)


def _start(
    investigation_id: str, ctx: EditorCtx, db: DbDep, state: StateDep, kind: str, pin: bool = False
) -> JobAccepted:
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    if inv.status == "running":
        raise Conflict("The investigation is already running", code="running")
    if kind == "run" and inv.status == "needs_disambiguation":
        raise Conflict("Resolve the ambiguous terms first", code="needs_disambiguation")
    if kind == "rerun" and not inv.current_run_id:
        raise Conflict("The investigation has not run yet", code="not_run")
    _audit(db, ctx, f"investigation.{kind}", inv.id, {"pin_definitions": pin})
    job_id = state.jobs.submit(
        kind=f"investigation_{kind}",
        fn=svc.run_job(state, ctx.workspace_id, inv.id, ctx.user_id, kind=kind, pin_definitions=pin),
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        resource_type="investigation",
        resource_id=inv.id,
        db=db,
    )
    return job_accepted(state, ctx.workspace_id, job_id)


@router.post(
    "/{investigation_id}/run", response_model=JobAccepted, status_code=202, operation_id="runInvestigation"
)
def run(investigation_id: str, ctx: EditorCtx, db: DbDep, state: StateDep) -> JobAccepted:
    """Execute the (approved) plan. Poll the job; the result carries ``run_id``."""
    return _start(investigation_id, ctx, db, state, "run")


@router.post(
    "/{investigation_id}/rerun",
    response_model=JobAccepted,
    status_code=202,
    operation_id="rerunInvestigation",
)
def rerun(investigation_id: str, body: RunRequest, ctx: EditorCtx, db: DbDep, state: StateDep) -> JobAccepted:
    """Re-execute the same plan (same absolute periods, filters, drills) on current data; the new run
    stores a diff against the previous run."""
    return _start(investigation_id, ctx, db, state, "rerun", body.pin_definitions)


@router.get(
    "/{investigation_id}/runs", response_model=list[InvestigationRunOut], operation_id="listInvestigationRuns"
)
def runs(investigation_id: str, ctx: ViewerCtx, db: DbDep) -> list[InvestigationRunOut]:
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    rows = db.scalars(
        select(InvestigationRun)
        .where(InvestigationRun.investigation_id == inv.id)
        .order_by(InvestigationRun.run_no.desc())
    ).all()
    out = []
    for r in rows:
        item = InvestigationRunOut.model_validate(r)
        item.diff_summary = list((r.diff or {}).get("summary", []))
        out.append(item)
    return out


@router.get("/{investigation_id}/diff", response_model=DiffOut, operation_id="diffInvestigationRuns")
def diff(
    investigation_id: str, ctx: ViewerCtx, db: DbDep, from_run: str | None = None, to_run: str | None = None
) -> DiffOut:
    """What changed between two runs: node values, dataset versions and metric versions."""
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    return DiffOut.model_validate(svc.run_diff(db, inv, from_run, to_run))


@router.get(
    "/{investigation_id}/artifacts",
    response_model=list[ArtifactOut],
    operation_id="listInvestigationArtifacts",
)
def artifacts(
    investigation_id: str, ctx: ViewerCtx, db: DbDep, run_id: str | None = None
) -> list[ArtifactOut]:
    """Artifacts of the current run (or ``run_id``). ``engine_id`` matches ``TreeNode.artifact_ids``."""
    from .artifacts import artifact_out

    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    rid = run_id or inv.current_run_id
    if not rid:
        return []
    rows = db.scalars(
        select(ArtifactRecord)
        .where(ArtifactRecord.investigation_id == inv.id, ArtifactRecord.run_id == rid)
        .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
    ).all()
    return [artifact_out(r) for r in rows]


@router.post(
    "/{investigation_id}/nodes/{node_id}/drill",
    response_model=InvestigationOut,
    operation_id="drillInvestigationNode",
)
def drill(
    investigation_id: str, node_id: str, body: DrillRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> InvestigationOut:
    """Break the node's metric down by a dimension inside the node's segment (new nodes are real queries)."""
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    svc.drill_node(db, state, ctx.workspace, inv, node_id, body.dimension, ctx.user_id)
    _audit(db, ctx, "investigation.drill", inv.id, {"node_id": node_id, "dimension": body.dimension})
    return investigation_out(db, inv)


@router.post(
    "/{investigation_id}/nodes/{node_id}/actions",
    response_model=InvestigationOut,
    operation_id="investigationNodeAction",
)
def node_action(
    investigation_id: str, node_id: str, body: NodeActionRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> InvestigationOut:
    """confirm | reject | needs_review | annotate (note) | rerun (re-execute the node's queries and compare)."""
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    svc.node_action(db, state, ctx.workspace, inv, node_id, body.action, body.note, ctx.user_id)
    _audit(db, ctx, f"investigation.node_{body.action}", inv.id, {"node_id": node_id, "note": body.note})
    return investigation_out(db, inv)


@router.post(
    "/{investigation_id}/nodes/{node_id}/finding",
    response_model=FindingOut,
    status_code=201,
    operation_id="promoteNodeToFinding",
    tags=["investigations", "findings"],
    responses={200: {"model": FindingOut, "description": "The node was already saved: existing finding"}},
)
def promote(
    investigation_id: str, node_id: str, body: PromoteRequest, ctx: EditorCtx, db: DbDep, response: Response
) -> FindingOut:
    """Save a node as a Finding with its evidence, artifacts, filter context and metric versions.
    201 with the new finding; 200 with the existing one if the node was already saved."""
    from .findings import finding_out

    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    f, created = create_from_node(
        db, inv, node_id, user_id=ctx.user_id, statement=body.statement, notes=body.notes
    )
    if not created:
        response.status_code = 200
        return finding_out(db, f)
    audit(
        db,
        action="finding.create",
        resource_type="finding",
        resource_id=f.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"investigation_id": inv.id, "node_id": node_id},
    )
    return finding_out(db, f)


@router.post("/{investigation_id}/command", response_model=CommandResult, operation_id="investigationCommand")
def command(
    investigation_id: str, body: CommandRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> CommandResult:
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    return execute_command(db, state, ctx, body.text, inv, body.node_id)


@router.post("/command", response_model=CommandResult, operation_id="workspaceCommand")
def workspace_command(body: CommandRequest, ctx: EditorCtx, db: DbDep, state: StateDep) -> CommandResult:
    """A command or question with no investigation context (a question starts a new investigation)."""
    return execute_command(db, state, ctx, body.text, None, body.node_id)


@router.post(
    "/{investigation_id}/notebook",
    response_model=NotebookOut,
    status_code=201,
    operation_id="investigationToNotebook",
    tags=["investigations", "notebooks"],
)
def to_notebook(investigation_id: str, ctx: EditorCtx, db: DbDep) -> NotebookOut:
    """Export the investigation (question, answer, every query and finding) to a reproducible notebook."""
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    nb = notebook_from_investigation(db, inv, ctx.user_id)
    return notebook_out(db, nb)


@router.get("/{investigation_id}/summary", response_model=SummaryOut, operation_id="investigationSummary")
def summary(investigation_id: str, ctx: ViewerCtx, db: DbDep, state: StateDep) -> SummaryOut:
    """Executive summary from confirmed nodes and findings only: facts, supported explanations and
    unresolved hypotheses are kept separate."""
    inv = svc.get_investigation(db, ctx.workspace_id, investigation_id)
    items: list[FindingInput] = []
    promoted: set[str] = set()
    for f in db.scalars(select(Finding).where(Finding.investigation_id == inv.id)):
        items.append(
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
        )
        if f.node_id:
            promoted.add(f.node_id)
    doc = EngineInvestigation.model_validate(inv.document) if inv.document else None
    if doc and doc.tree.root_id:
        items.extend(
            FindingInput.from_node(n)
            for n in doc.tree.nodes
            if n.status == "confirmed" and n.id not in promoted
        )
    llm = get_llm(state, ctx.workspace, investigation_id=inv.id)
    artifacts = svc.engine_run(db, inv).artifacts if llm is not None and inv.current_run_id else []
    nodes = list(doc.tree.nodes) if doc else []
    return SummaryOut(
        investigation_id=inv.id, summary=executive_summary(items, llm=llm, artifacts=artifacts, nodes=nodes)
    )
