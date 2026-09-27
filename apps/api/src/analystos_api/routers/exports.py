"""Exports: CSV, XLSX, Markdown, HTML, PDF, JSON and .ipynb; stored chart images (PNG) from the web app."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import new_id
from ..deps import DbDep, StateDep, ViewerCtx
from ..errors import NotFound, PayloadTooLarge, Unprocessable
from ..models import (
    ArtifactRecord,
    Dataset,
    QueryRun,
    SavedQuery,
    StoredFile,
)
from ..schemas.common import ERROR_RESPONSES, ApiModel
from ..services import exports as r
from ..services.dashboards import get_dashboard, tile_data, tiles_of
from ..services.findings import get_finding
from ..services.investigations import get_investigation
from ..services.notebooks import get_notebook, ipynb_bytes, to_ipynb
from ..services.observability import audit, record_event
from ..services.reports import get_report
from ..services.writer import writer_for

router = APIRouter(prefix="/workspaces/{workspace_id}/exports", tags=["exports"], responses=ERROR_RESPONSES)

Target = Literal[
    "query_run",
    "saved_query",
    "dataset",
    "artifact",
    "finding",
    "report",
    "dashboard",
    "notebook",
    "investigation",
]
Format = Literal["csv", "xlsx", "md", "html", "pdf", "ipynb", "json"]
MEDIA = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "pdf": "application/pdf",
    "ipynb": "application/x-ipynb+json",
    "json": "application/json",
}
SUPPORTED: dict[str, set[str]] = {
    "query_run": {"csv", "xlsx", "json", "md", "html"},
    "saved_query": {"csv", "xlsx", "json", "md", "html"},
    "dataset": {"csv", "xlsx", "json"},
    "artifact": {"csv", "xlsx", "json", "md", "html"},
    "finding": {"md", "html", "pdf", "json"},
    "report": {"md", "html", "pdf", "json", "xlsx"},
    "dashboard": {"html", "pdf", "json", "md"},
    "notebook": {"ipynb", "md", "html", "json"},
    "investigation": {"md", "html", "pdf", "json", "ipynb"},
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
EXPORT_ROW_LIMIT = 1_000_000


class ExportRequest(BaseModel):
    target: Target
    id: str
    format: Format
    params: dict[str, Any] = Field(default_factory=dict, description="saved_query: parameter values")


class StoredFileOut(ApiModel):
    id: str
    purpose: str
    format: str
    filename: str
    media_type: str
    size_bytes: int
    source_type: str | None
    source_id: str | None
    meta: dict[str, Any]
    created_at: datetime
    download_url: str = ""


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:80] or "export"


def _tabular(
    ctx: ViewerCtx, db: DbDep, state: StateDep, req: ExportRequest
) -> tuple[str, list[str], list[list[Any]], list[tuple[str, str]]]:
    store = state.stores.get(ctx.workspace_id)
    if req.target == "query_run":
        run = db.get(QueryRun, req.id)
        if run is None or run.workspace_id != ctx.workspace_id:
            raise NotFound("Query run not found")
        if run.data_source_id:
            cols, rows = [c["name"] for c in run.columns], run.result_snapshot
        else:
            res = store.execute_read(
                run.sql, run.params or None, EXPORT_ROW_LIMIT, state.settings.query_timeout_s * 4
            )
            cols, rows = [c.name for c in res.columns], res.model_dump(mode="json")["rows"]
        return (
            f"query_{run.id[:8]}",
            cols,
            rows,
            [
                ("SQL", run.sql),
                ("Parameters", json.dumps(run.params)),
                ("Run", f"{run.id} at {run.created_at.isoformat()}"),
            ],
        )
    if req.target == "saved_query":
        from ..services.queries import bind_parameters

        sq = db.get(SavedQuery, req.id)
        if sq is None or sq.workspace_id != ctx.workspace_id:
            raise NotFound("Saved query not found")
        bound = bind_parameters(sq.sql, sq.parameters, req.params)
        res = store.execute_read(sq.sql, bound or None, EXPORT_ROW_LIMIT, state.settings.query_timeout_s * 4)
        return (
            _slug(sq.name),
            [c.name for c in res.columns],
            res.model_dump(mode="json")["rows"],
            [
                ("SQL", sq.sql),
                ("Parameters", json.dumps(bound, default=str)),
                ("Saved query version", str(sq.version_no)),
            ],
        )
    if req.target == "dataset":
        from analystos_engine.store import quote_ident

        ds = db.get(Dataset, req.id)
        if ds is None or ds.workspace_id != ctx.workspace_id:
            raise NotFound("Dataset not found")
        res = store.execute_read(
            f"SELECT * FROM {quote_ident(ds.table_name)}",
            None,
            EXPORT_ROW_LIMIT,
            state.settings.query_timeout_s * 4,
        )
        return (
            _slug(ds.name),
            [c.name for c in res.columns],
            res.model_dump(mode="json")["rows"],
            [("Table", ds.table_name), ("Version", ds.current_version_id or "")],
        )
    a = db.get(ArtifactRecord, req.id)
    if a is None or a.workspace_id != ctx.workspace_id:
        raise NotFound("Artifact not found")
    res = a.result or {}
    return (
        _slug(a.title or a.kind),
        [c["name"] for c in res.get("columns", [])],
        res.get("rows", []),
        [
            ("SQL", a.sql or ""),
            ("Filters", "; ".join(f"{c['label']}: {c['value']}" for c in a.filter_context or [])),
            ("Metric versions", json.dumps(a.metric_versions)),
            ("Snapshot", "Stored artifact result"),
        ],
    )


def _chart_images(db: DbDep, workspace_id: str, keys: list[str]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if not keys:
        return out
    rows = db.scalars(
        select(StoredFile)
        .where(
            StoredFile.workspace_id == workspace_id,
            StoredFile.purpose == "chart_image",
            StoredFile.source_id.in_(keys),
        )
        .order_by(StoredFile.created_at)
    ).all()
    for f in rows:
        p = Path(f.stored_path)
        if p.exists() and f.source_id:
            out[f.source_id] = p.read_bytes()
    return out


def _document(
    ctx: ViewerCtx, db: DbDep, state: StateDep, req: ExportRequest
) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
    """(title, meta line, blocks, json payload) for document-like targets."""
    if req.target == "report":
        rep = get_report(db, ctx.workspace_id, req.id)
        meta = f"{rep.kind.replace('_', ' ')} · {rep.status} · version {rep.version_no}"
        return (
            rep.title,
            meta,
            rep.blocks,
            {"report": {"id": rep.id, "title": rep.title, "status": rep.status, "blocks": rep.blocks}},
        )
    if req.target == "finding":
        from ..services.reports import finding_block

        f = get_finding(db, ctx.workspace_id, req.id)
        arts = db.scalars(select(ArtifactRecord).where(ArtifactRecord.id.in_(f.artifact_ids))).all()
        blocks: list[dict[str, Any]] = [finding_block(f)]
        for a in arts:
            if a.sql:
                blocks.append(
                    {
                        "id": a.id,
                        "type": "chart" if a.chart_spec else "table",
                        "title": a.title,
                        "artifact_id": a.id,
                        "result": a.result or {},
                        "provenance": {"sql": a.sql, "filter_context": a.filter_context},
                    }
                )
        return (
            f.statement[:120],
            f"{f.status} · version {f.version_no}",
            blocks,
            {
                "finding": {
                    k: getattr(f, k)
                    for k in (
                        "id",
                        "statement",
                        "statement_type",
                        "evidence_strength",
                        "evidence_reasons",
                        "status",
                        "values",
                        "filter_context",
                        "artifact_ids",
                        "metric_version_ids",
                        "version_no",
                    )
                }
            },
        )
    if req.target == "investigation":
        from ..services.reports import report_from_investigation

        inv = get_investigation(db, ctx.workspace_id, req.id)
        blocks = report_from_investigation(db, state, ctx.workspace, inv, ctx.user_id, persist=False).blocks
        return (
            inv.title,
            f"investigation · run {inv.run_count}",
            blocks,
            {
                "investigation": inv.document,
                "metric_version_ids": inv.metric_version_ids,
                "dataset_versions": inv.dataset_versions,
            },
        )
    if req.target == "dashboard":
        d = get_dashboard(db, ctx.workspace_id, req.id)
        blocks = []
        payload_tiles = []
        for t in tiles_of(db, d.id):
            try:
                data = tile_data(db, state, ctx.workspace, d, t, None, None, ctx.user_id)
            except Exception as exc:  # noqa: BLE001 - the export states which tile failed
                blocks.append(
                    {"id": t.id, "type": "narrative", "markdown": f"**{t.title}**: could not load ({exc})"}
                )
                continue
            payload_tiles.append(data)
            if t.kind == "text":
                blocks.append({"id": t.id, "type": "narrative", "markdown": f"### {t.title}\n\n{t.text}"})
            elif data.get("kpi"):
                blocks.append(
                    {"id": t.id, "type": "kpi", **data["kpi"], "label": data["kpi"].get("label") or t.title}
                )
            elif data.get("chart"):
                blocks.append(
                    {
                        "id": t.id,
                        "type": "chart",
                        "title": t.title,
                        "provenance": {
                            "filter_context": data.get("filter_context"),
                            "sql": (data.get("provenance") or {}).get("sql"),
                        },
                    }
                )
            if t.kind in ("table",) and data.get("result"):
                blocks.append(
                    {"id": t.id + "-t", "type": "table", "title": t.title, "result": data["result"]}
                )
        return (
            d.name,
            f"dashboard · version {d.version_no}",
            blocks,
            {"dashboard": {"id": d.id, "name": d.name}, "tiles": payload_tiles},
        )
    nb = get_notebook(db, ctx.workspace_id, req.id)
    nbj = to_ipynb(db, nb)
    blocks = []
    for c in nbj["cells"]:
        text = "".join(c.get("source", []))
        if c["cell_type"] == "markdown":
            blocks.append({"type": "narrative", "markdown": text})
        else:
            blocks.append({"type": "narrative", "markdown": f"```\n{text}\n```"})
    return nb.title, "notebook", blocks, nbj


@router.post(
    "",
    operation_id="createExport",
    responses={
        200: {
            "description": "The exported file (Content-Disposition: attachment)",
            "content": {m: {} for m in MEDIA.values()},
        }
    },
)
def export(req: ExportRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> Response:
    if req.format not in SUPPORTED[req.target]:
        raise Unprocessable(
            f"{req.target} can be exported as {sorted(SUPPORTED[req.target])}", code="unsupported_export"
        )
    content: bytes
    if req.target in ("query_run", "saved_query", "dataset", "artifact"):
        name, cols, rows, about = _tabular(ctx, db, state, req)
        if req.format == "csv":
            content = r.to_csv(cols, rows)
        elif req.format == "xlsx":
            content = r.to_xlsx([(name, cols, rows)], about)
        elif req.format == "json":
            content = json.dumps({"columns": cols, "rows": rows, "about": dict(about)}, default=str).encode()
        elif req.format == "md":
            content = (
                f"# {name}\n\n"
                + r.table_md(cols, rows)
                + "\n\n"
                + "\n".join(f"**{k}:** {v}" for k, v in about)
                + "\n"
            ).encode()
        else:
            content = r.html_document(
                name,
                r.table_html(cols, rows)
                + "".join(f"<h3>{r.e(k)}</h3><pre>{r.e(v)}</pre>" for k, v in about if v),
            ).encode()
    elif req.format == "ipynb":
        if req.target == "investigation":
            from ..services.notebooks import notebook_from_investigation

            inv = get_investigation(db, ctx.workspace_id, req.id)
            nb = notebook_from_investigation(db, inv, ctx.user_id)
            content = ipynb_bytes(db, nb)
            db.rollback()  # the notebook was only needed to render the export
            name = _slug(inv.title)
        else:
            nb = get_notebook(db, ctx.workspace_id, req.id)
            content, name = ipynb_bytes(db, nb), _slug(nb.title)
    else:
        title, meta, blocks, payload = _document(ctx, db, state, req)
        name = _slug(title)
        if req.format == "json":
            content = json.dumps(payload, default=str, indent=1).encode()
        elif req.format == "md":
            content = r.blocks_to_md(title, blocks).encode()
        elif req.format == "xlsx":
            sheets = [
                (
                    b.get("title") or f"Table {i + 1}",
                    [c["name"] for c in b["result"].get("columns", [])],
                    b["result"].get("rows", []),
                )
                for i, b in enumerate(blocks)
                if b.get("type") == "table" and b.get("result")
            ]
            if not sheets:
                raise Unprocessable("This document has no tables to export as Excel", code="no_tables")
            content = r.to_xlsx(sheets, [("Title", title)])
        else:
            keys = [k for b in blocks for k in (b.get("id"), b.get("artifact_id")) if k]
            html_doc = r.html_document(
                title, r.blocks_to_html(blocks, _chart_images(db, ctx.workspace_id, keys)), r.e(meta)
            )
            content = r.html_to_pdf(html_doc) if req.format == "pdf" else html_doc.encode()
    filename = f"{name}.{req.format}"
    _store(ctx, db, state, content, filename, req)
    return Response(
        content,
        media_type=MEDIA[req.format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _store(
    ctx: ViewerCtx, db: DbDep, state: StateDep, content: bytes, filename: str, req: ExportRequest
) -> None:
    """Keep a copy of every export under DATA_DIR (listed at GET /exports) and record the event."""
    fid = new_id()
    path = state.stores.exports_dir(ctx.workspace_id) / f"{fid}_{filename}"
    path.write_bytes(content)
    path.chmod(0o600)
    row = StoredFile(
        id=fid,
        workspace_id=ctx.workspace_id,
        purpose="export",
        format=req.format,
        filename=filename,
        media_type=MEDIA[req.format],
        stored_path=str(path),
        size_bytes=len(content),
        source_type=req.target,
        source_id=req.id,
        created_by=ctx.user_id,
    )
    writer_for(state.session_factory).submit(lambda s: s.add(row))
    audit(
        db,
        action="export.create",
        resource_type=req.target,
        resource_id=req.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"format": req.format, "bytes": len(content)},
    )
    record_event(
        state.session_factory,
        category="export",
        name=f"{req.target}:{req.format}",
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        detail={"bytes": len(content)},
    )


def _file_out(ws: str, f: StoredFile) -> StoredFileOut:
    out = StoredFileOut.model_validate(f)
    out.download_url = f"/api/v1/workspaces/{ws}/exports/{f.id}/download"
    return out


@router.get("", response_model=list[StoredFileOut], operation_id="listExports")
def list_exports(
    ctx: ViewerCtx, db: DbDep, state: StateDep, purpose: str | None = None, source_id: str | None = None
) -> list[StoredFileOut]:
    writer_for(state.session_factory).flush(5)
    stmt = select(StoredFile).where(StoredFile.workspace_id == ctx.workspace_id)
    if purpose:
        stmt = stmt.where(StoredFile.purpose == purpose)
    if source_id:
        stmt = stmt.where(StoredFile.source_id == source_id)
    return [
        _file_out(ctx.workspace_id, f)
        for f in db.scalars(stmt.order_by(StoredFile.created_at.desc()).limit(200))
    ]


@router.get("/{file_id}/download", operation_id="downloadExport", response_class=FileResponse)
def download(file_id: str, ctx: ViewerCtx, db: DbDep, state: StateDep) -> FileResponse:
    writer_for(state.session_factory).flush(5)
    f = db.get(StoredFile, file_id)
    if f is None or f.workspace_id != ctx.workspace_id or not Path(f.stored_path).exists():
        raise NotFound("File not found")
    return FileResponse(f.stored_path, media_type=f.media_type, filename=f.filename)


@router.post("/images", response_model=StoredFileOut, status_code=201, operation_id="uploadChartImage")
def upload_image(
    ctx: ViewerCtx,
    db: DbDep,
    state: StateDep,
    file: Annotated[UploadFile, File()],
    source_type: Annotated[str, Form()] = "chart",
    source_id: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
) -> StoredFileOut:
    """Store a PNG rendered by the web app (ECharts ``getDataURL``). Reports embed it on HTML/PDF export when
    ``source_id`` is the report block id or artifact id."""
    data = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise PayloadTooLarge("Chart images are limited to 10 MB")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise Unprocessable("Only PNG images are accepted", code="content_mismatch")
    fid = new_id()
    name = f"{_slug(title or source_id or 'chart')}.png"
    path = state.stores.exports_dir(ctx.workspace_id) / f"{fid}_{name}"
    path.write_bytes(data)
    path.chmod(0o600)
    f = StoredFile(
        id=fid,
        workspace_id=ctx.workspace_id,
        purpose="chart_image",
        format="png",
        filename=name,
        media_type="image/png",
        stored_path=str(path),
        size_bytes=len(data),
        source_type=source_type[:32] or None,
        source_id=source_id[:32] or None,
        meta={"title": title},
        created_by=ctx.user_id,
    )
    db.add(f)
    db.flush()
    return _file_out(ctx.workspace_id, f)
