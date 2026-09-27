"""Notebooks: reproducible SQL / Python / Markdown / chart / finding cells and .ipynb export."""

from __future__ import annotations

import json
from typing import Any

from analystos_investigator import Investigation as EngineInvestigation
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..errors import ApiError, NotFound, Unprocessable
from ..models import ArtifactRecord, Finding, Notebook, NotebookCell
from ..schemas.notebooks import CellOut, NotebookOut
from ..state import AppState
from .charts import suggest_charts
from .python_sandbox import run_python
from .queries import run_sql


def get_notebook(db: Session, workspace_id: str, notebook_id: str) -> Notebook:
    nb = db.get(Notebook, notebook_id)
    if nb is None or nb.workspace_id != workspace_id:
        raise NotFound("Notebook not found")
    return nb


def cells_of(db: Session, notebook_id: str) -> list[NotebookCell]:
    return list(
        db.scalars(
            select(NotebookCell)
            .where(NotebookCell.notebook_id == notebook_id)
            .order_by(NotebookCell.position, NotebookCell.created_at)
        )
    )


def get_cell(db: Session, nb: Notebook, cell_id: str) -> NotebookCell:
    cell = db.get(NotebookCell, cell_id)
    if cell is None or cell.notebook_id != nb.id:
        raise NotFound("Cell not found")
    return cell


def notebook_out(db: Session, nb: Notebook) -> NotebookOut:
    out = NotebookOut.model_validate(nb)
    out.cells = [CellOut.model_validate(c) for c in cells_of(db, nb.id)]
    return out


def renumber(db: Session, nb: Notebook, ordered: list[NotebookCell]) -> None:
    for i, c in enumerate(ordered):
        c.position = i
    nb.updated_at = utcnow()
    db.flush()


def add_cell(
    db: Session,
    nb: Notebook,
    kind: str,
    source: str,
    config: dict[str, Any] | None = None,
    position: int | None = None,
) -> NotebookCell:
    cells = cells_of(db, nb.id)
    cell = NotebookCell(notebook_id=nb.id, position=len(cells), kind=kind, source=source, config=config or {})
    db.add(cell)
    db.flush()
    if position is not None and position < len(cells):
        cells.insert(position, cell)
        renumber(db, nb, cells)
    return cell


def run_cell(
    db: Session, state: AppState, nb: Notebook, cell: NotebookCell, user_id: str | None
) -> NotebookCell:
    try:
        cell.output = _execute(db, state, nb, cell, user_id)
        cell.status = "ok"
    except ApiError as exc:
        cell.output = {"error": exc.detail, "code": exc.code}
        cell.status = "error"
    except Exception as exc:  # noqa: BLE001 - surface the failure in the cell, never hide it
        cell.output = {"error": f"{type(exc).__name__}: {exc}", "code": "cell_failed"}
        cell.status = "error"
    cell.execution_count = cell.execution_count + 1
    cell.last_run_at = utcnow()
    nb.updated_at = utcnow()
    db.flush()
    return cell


def _resolve_inputs(db: Session, nb: Notebook, inputs: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, ref in inputs.items():
        if not name.isidentifier():
            raise Unprocessable(f"Input name {name!r} must be a Python identifier", code="invalid_input")
        if isinstance(ref, str) and ref.startswith("cell:"):
            src = get_cell(db, nb, ref[5:])
            if src.kind != "sql":
                raise Unprocessable(f"Input {name} must reference a SQL cell", code="invalid_input")
            out[name] = src.source
        else:
            out[name] = str(ref)
    return out


def _execute(
    db: Session, state: AppState, nb: Notebook, cell: NotebookCell, user_id: str | None
) -> dict[str, Any]:
    cfg = cell.config or {}
    if cell.kind == "markdown":
        return {"markdown": cell.source}
    if cell.kind == "sql":
        outcome = run_sql(
            db,
            state,
            workspace_id=nb.workspace_id,
            user_id=user_id,
            sql=cell.source,
            params=cfg.get("params") or {},
            param_definitions=cfg.get("parameters") or [],
            limit=cfg.get("limit"),
            origin="notebook",
        )
        res = outcome.result.model_dump(mode="json")
        charts = suggest_charts(res["columns"], res["rows"])[:3] if res["row_count"] else []
        return {
            "result": res,
            "charts": charts,
            "query_run_id": outcome.run.id,
            "dataset_versions": outcome.run.dataset_versions,
        }
    if cell.kind == "python":
        inputs = _resolve_inputs(db, nb, cfg.get("inputs") or {})
        return run_python(
            state,
            workspace_id=nb.workspace_id,
            user_id=user_id,
            code=cell.source,
            inputs=inputs,
            timeout_s=cfg.get("timeout_s"),
        )
    if cell.kind == "chart":
        src_id = cfg.get("source_cell_id")
        if not src_id:
            raise Unprocessable("Chart cells need config.source_cell_id", code="chart_source")
        src = get_cell(db, nb, src_id)
        result = (src.output or {}).get("result")
        if not result:
            raise Unprocessable("Run the source cell first", code="chart_source_not_run")
        suggestions = suggest_charts(result["columns"], result["rows"], title=cfg.get("title") or "")
        wanted = cfg.get("type")
        chosen = (
            next((s for s in suggestions if s["type"] == wanted), suggestions[0]) if suggestions else None
        )
        return {
            "chart": chosen,
            "alternatives": [s["type"] for s in suggestions],
            "source_cell_id": src_id,
            "sql": result.get("sql"),
        }
    if cell.kind == "finding":
        fid = cfg.get("finding_id")
        f = db.get(Finding, fid) if fid else None
        if f is None or f.workspace_id != nb.workspace_id:
            raise Unprocessable("Finding cells need a valid config.finding_id", code="finding_missing")
        return {
            "finding": {
                "id": f.id,
                "statement": f.statement,
                "statement_type": f.statement_type,
                "evidence_strength": f.evidence_strength,
                "evidence_reasons": f.evidence_reasons,
                "status": f.status,
                "values": f.values,
                "filter_context": f.filter_context,
            }
        }
    raise Unprocessable(f"Unknown cell kind {cell.kind!r}", code="unknown_cell_kind")


def run_all(db: Session, state: AppState, nb: Notebook, user_id: str | None) -> tuple[int, str | None]:
    executed = 0
    for cell in cells_of(db, nb.id):
        run_cell(db, state, nb, cell, user_id)
        executed += 1
        if cell.status == "error":
            for later in cells_of(db, nb.id)[executed:]:
                if later.status == "ok":
                    later.status = "stale"
            db.flush()
            return executed, cell.id
    return executed, None


def notebook_from_investigation(db: Session, inv: Any, user_id: str | None) -> Notebook:
    doc = EngineInvestigation.model_validate(inv.document)
    nb = Notebook(
        workspace_id=inv.workspace_id,
        title=f"Investigation: {inv.title}"[:300],
        description=f"Exported from investigation {inv.id} (run {inv.run_count}).",
        investigation_id=inv.id,
        created_by=user_id,
    )
    db.add(nb)
    db.flush()
    intro = [f"# {inv.question}", ""]
    if doc.brief_answer:
        intro += [f"**Answer:** {doc.brief_answer}", ""]
    if doc.tree.root_id:
        intro += ["```", doc.tree.render_text(), "```", ""]
    versions = ", ".join(f"{m} v{v.get('version_no')}" for m, v in (inv.metric_version_ids or {}).items())
    if versions:
        intro.append(f"Metric definitions used: {versions}.")
    add_cell(db, nb, "markdown", "\n".join(intro))
    rows = (
        db.scalars(
            select(ArtifactRecord)
            .where(ArtifactRecord.run_id == inv.current_run_id)
            .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
        ).all()
        if inv.current_run_id
        else []
    )
    for a in rows:
        if not a.sql:
            continue
        ctx = "; ".join(f"{c['label']}: {c['value']}" for c in (a.filter_context or []))
        add_cell(db, nb, "markdown", f"### {a.title or a.kind}\n\n{ctx}".rstrip())
        sql_cell = add_cell(db, nb, "sql", a.sql, {"artifact_id": a.id})
        if a.chart_spec:
            add_cell(db, nb, "chart", "", {"source_cell_id": sql_cell.id, "title": a.title})
    for f in db.scalars(select(Finding).where(Finding.investigation_id == inv.id)):
        add_cell(db, nb, "finding", "", {"finding_id": f.id})
    return nb


# ------------------------------------------------------------------------------------------ ipynb


def _table_text(result: dict[str, Any], limit: int = 20) -> tuple[str, str]:
    cols = [c["name"] for c in result.get("columns", [])]
    rows = result.get("rows", [])[:limit]
    text = "\t".join(cols) + "\n" + "\n".join("\t".join("" if v is None else str(v) for v in r) for r in rows)
    import html

    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape('' if v is None else str(v))}</td>" for v in r) + "</tr>"
        for r in rows
    )
    return text, f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def to_ipynb(db: Session, nb: Notebook) -> dict[str, Any]:
    """nbformat 4 document. SQL cells become Python cells that run the same SQL read-only against a
    copy of the workspace DuckDB file, so the notebook reproduces outside AnalystOS."""
    cells: list[dict[str, Any]] = [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                f"# {nb.title}\n\n{nb.description}\n\nExported from AnalystOS. Point `WAREHOUSE` at a copy of the "
                "workspace `warehouse.duckdb` to re-run the queries (read-only)."
            ],
        },
        {
            "cell_type": "code",
            "metadata": {},
            "execution_count": None,
            "outputs": [],
            "source": [
                "import duckdb\n",
                "WAREHOUSE = 'warehouse.duckdb'\n",
                "con = duckdb.connect(WAREHOUSE, read_only=True)\n",
            ],
        },
    ]
    n = 0
    for c in cells_of(db, nb.id):
        out = c.output or {}
        if c.kind == "markdown":
            cells.append({"cell_type": "markdown", "metadata": {}, "source": [c.source]})
        elif c.kind == "sql":
            n += 1
            src = f'df_{n} = con.execute("""\n{c.source}\n""").df()\ndf_{n}'
            outputs: list[dict[str, Any]] = []
            if out.get("result"):
                text, html_table = _table_text(out["result"])
                outputs.append(
                    {
                        "output_type": "execute_result",
                        "execution_count": c.execution_count or None,
                        "metadata": {},
                        "data": {"text/plain": [text], "text/html": [html_table]},
                    }
                )
            cells.append(
                {
                    "cell_type": "code",
                    "metadata": {"analystos": {"kind": "sql", "cell_id": c.id}},
                    "execution_count": c.execution_count or None,
                    "outputs": outputs,
                    "source": [src],
                }
            )
        elif c.kind == "python":
            inputs = (c.config or {}).get("inputs") or {}
            prelude = "".join(
                f'{name} = con.execute("""{_input_sql(db, nb, ref)}""").df()\n'
                for name, ref in inputs.items()
            )
            outputs = []
            if out.get("stdout"):
                outputs.append({"output_type": "stream", "name": "stdout", "text": [out["stdout"]]})
            for fig in out.get("figures", []) or []:
                outputs.append({"output_type": "display_data", "metadata": {}, "data": {"image/png": fig}})
            cells.append(
                {
                    "cell_type": "code",
                    "metadata": {"analystos": {"kind": "python", "cell_id": c.id}},
                    "execution_count": c.execution_count or None,
                    "outputs": outputs,
                    "source": [prelude + c.source],
                }
            )
        elif c.kind == "chart":
            chart = out.get("chart") or {}
            cells.append(
                {
                    "cell_type": "markdown",
                    "metadata": {
                        "analystos": {"kind": "chart", "cell_id": c.id, "echarts_option": chart.get("option")}
                    },
                    "source": [
                        f"**Chart ({chart.get('type', 'chart')}):** {chart.get('title', '')}\n\n"
                        f"{chart.get('reason', '')}"
                    ],
                }
            )
        elif c.kind == "finding":
            f = out.get("finding") or {}
            cells.append(
                {
                    "cell_type": "markdown",
                    "metadata": {"analystos": {"kind": "finding", "cell_id": c.id}},
                    "source": [
                        f"> **Finding ({f.get('statement_type', '')}, {f.get('evidence_strength', '')}, "
                        f"{f.get('status', '')}):** {f.get('statement', '')}"
                    ],
                }
            )
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": cells,
        "metadata": {
            "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
            "language_info": {"name": "python"},
            "analystos": {
                "notebook_id": nb.id,
                "workspace_id": nb.workspace_id,
                "exported_at": utcnow().isoformat(),
            },
        },
    }


def _input_sql(db: Session, nb: Notebook, ref: str) -> str:
    if isinstance(ref, str) and ref.startswith("cell:"):
        cell = db.get(NotebookCell, ref[5:])
        return cell.source if cell is not None and cell.notebook_id == nb.id else ""
    return str(ref)


def ipynb_bytes(db: Session, nb: Notebook) -> bytes:
    return json.dumps(to_ipynb(db, nb), indent=1, default=str).encode()
