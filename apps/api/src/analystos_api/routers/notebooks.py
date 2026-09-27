"""Notebooks: SQL, Python, Markdown, chart and finding cells; run cell / run all; .ipynb export."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from ..deps import DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import Unprocessable
from ..models import Notebook
from ..schemas.common import ERROR_RESPONSES, OkResponse
from ..schemas.notebooks import (
    CellCreate,
    CellOut,
    CellUpdate,
    NotebookCreate,
    NotebookOut,
    NotebookSummary,
    NotebookUpdate,
    ReorderRequest,
    RunAllResult,
)
from ..services.notebooks import (
    add_cell,
    cells_of,
    get_cell,
    get_notebook,
    ipynb_bytes,
    notebook_out,
    renumber,
    run_all,
    run_cell,
)
from ..services.observability import audit

router = APIRouter(
    prefix="/workspaces/{workspace_id}/notebooks", tags=["notebooks"], responses=ERROR_RESPONSES
)


@router.get("", response_model=list[NotebookSummary], operation_id="listNotebooks")
def list_notebooks(ctx: ViewerCtx, db: DbDep) -> list[NotebookSummary]:
    rows = db.scalars(
        select(Notebook).where(Notebook.workspace_id == ctx.workspace_id).order_by(Notebook.updated_at.desc())
    ).all()
    return [NotebookSummary.model_validate(r) for r in rows]


@router.post("", response_model=NotebookOut, status_code=201, operation_id="createNotebook")
def create(body: NotebookCreate, ctx: EditorCtx, db: DbDep) -> NotebookOut:
    nb = Notebook(
        workspace_id=ctx.workspace_id, title=body.title, description=body.description, created_by=ctx.user_id
    )
    db.add(nb)
    db.flush()
    audit(
        db,
        action="notebook.create",
        resource_type="notebook",
        resource_id=nb.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
    )
    return notebook_out(db, nb)


@router.get("/{notebook_id}", response_model=NotebookOut, operation_id="getNotebook")
def get(notebook_id: str, ctx: ViewerCtx, db: DbDep) -> NotebookOut:
    return notebook_out(db, get_notebook(db, ctx.workspace_id, notebook_id))


@router.patch("/{notebook_id}", response_model=NotebookOut, operation_id="updateNotebook")
def update(notebook_id: str, body: NotebookUpdate, ctx: EditorCtx, db: DbDep) -> NotebookOut:
    nb = get_notebook(db, ctx.workspace_id, notebook_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(nb, k, v)
    db.flush()
    return notebook_out(db, nb)


@router.delete("/{notebook_id}", response_model=OkResponse, operation_id="deleteNotebook")
def delete(notebook_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    nb = get_notebook(db, ctx.workspace_id, notebook_id)
    audit(
        db,
        action="notebook.delete",
        resource_type="notebook",
        resource_id=nb.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"title": nb.title},
    )
    db.delete(nb)
    return OkResponse()


@router.post("/{notebook_id}/cells", response_model=CellOut, status_code=201, operation_id="addNotebookCell")
def create_cell(notebook_id: str, body: CellCreate, ctx: EditorCtx, db: DbDep) -> CellOut:
    nb = get_notebook(db, ctx.workspace_id, notebook_id)
    return CellOut.model_validate(add_cell(db, nb, body.kind, body.source, body.config, body.position))


@router.patch("/{notebook_id}/cells/{cell_id}", response_model=CellOut, operation_id="updateNotebookCell")
def update_cell(notebook_id: str, cell_id: str, body: CellUpdate, ctx: EditorCtx, db: DbDep) -> CellOut:
    nb = get_notebook(db, ctx.workspace_id, notebook_id)
    cell = get_cell(db, nb, cell_id)
    changed = body.model_dump(exclude_none=True)
    for k, v in changed.items():
        setattr(cell, k, v)
    if changed and cell.status == "ok":
        cell.status = "stale"  # the stored output no longer matches the source
    db.flush()
    return CellOut.model_validate(cell)


@router.delete("/{notebook_id}/cells/{cell_id}", response_model=OkResponse, operation_id="deleteNotebookCell")
def delete_cell(notebook_id: str, cell_id: str, ctx: EditorCtx, db: DbDep) -> OkResponse:
    nb = get_notebook(db, ctx.workspace_id, notebook_id)
    cell = get_cell(db, nb, cell_id)
    db.delete(cell)
    db.flush()
    renumber(db, nb, cells_of(db, nb.id))
    return OkResponse()


@router.post("/{notebook_id}/cells/{cell_id}/run", response_model=CellOut, operation_id="runNotebookCell")
def run_one(notebook_id: str, cell_id: str, ctx: EditorCtx, db: DbDep, state: StateDep) -> CellOut:
    """Execute one cell. Errors are stored on the cell (status ``error``) rather than failing the request."""
    nb = get_notebook(db, ctx.workspace_id, notebook_id)
    return CellOut.model_validate(run_cell(db, state, nb, get_cell(db, nb, cell_id), ctx.user_id))


@router.post("/{notebook_id}/run", response_model=RunAllResult, operation_id="runNotebook")
def run_notebook(notebook_id: str, ctx: EditorCtx, db: DbDep, state: StateDep) -> RunAllResult:
    """Run every cell top to bottom; stops at the first error and marks later outputs stale."""
    nb = get_notebook(db, ctx.workspace_id, notebook_id)
    executed, stopped = run_all(db, state, nb, ctx.user_id)
    return RunAllResult(notebook=notebook_out(db, nb), executed=executed, stopped_at=stopped)


@router.post("/{notebook_id}/reorder", response_model=NotebookOut, operation_id="reorderNotebookCells")
def reorder(notebook_id: str, body: ReorderRequest, ctx: EditorCtx, db: DbDep) -> NotebookOut:
    nb = get_notebook(db, ctx.workspace_id, notebook_id)
    cells = {c.id: c for c in cells_of(db, nb.id)}
    if set(body.cell_ids) != set(cells) or len(body.cell_ids) != len(cells):
        raise Unprocessable("cell_ids must list every cell exactly once", code="invalid_order")
    renumber(db, nb, [cells[i] for i in body.cell_ids])
    return notebook_out(db, nb)


@router.get(
    "/{notebook_id}/ipynb",
    operation_id="exportNotebookIpynb",
    responses={200: {"content": {"application/x-ipynb+json": {}}, "description": "nbformat 4 notebook"}},
)
def export_ipynb(notebook_id: str, ctx: ViewerCtx, db: DbDep) -> Response:
    nb = get_notebook(db, ctx.workspace_id, notebook_id)
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in nb.title)[:80] or "notebook"
    return Response(
        ipynb_bytes(db, nb),
        media_type="application/x-ipynb+json",
        headers={"Content-Disposition": f'attachment; filename="{name}.ipynb"'},
    )
