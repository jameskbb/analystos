"""Dataset registration, versioning, profiling and remote snapshots."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..errors import Conflict, NotFound, Unprocessable
from ..jobs import JobContext
from ..models import Dataset, DatasetVersion, DataSource, RelationshipRecord, Upload
from ..state import AppState
from . import engine_bridge as eng
from .observability import record_event, timed_event

_IDENT = re.compile(r"[^a-z0-9_]+")


def table_name_for(name: str) -> str:
    stem = Path(name).stem if "." in name else name
    ident = _IDENT.sub("_", stem.strip().lower()).strip("_") or "dataset"
    if ident[0].isdigit():
        ident = f"t_{ident}"
    if ident.startswith("_aos_"):
        ident = ident[5:] or "dataset"
    return ident[:60]


def get_dataset(db: Session, workspace_id: str, dataset_id: str) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None or ds.workspace_id != workspace_id:
        raise NotFound("Dataset not found")
    return ds


def dataset_by_table(db: Session, workspace_id: str, table: str) -> Dataset | None:
    return db.scalar(select(Dataset).where(Dataset.workspace_id == workspace_id, Dataset.table_name == table))


def _columns_json(table_info: Any) -> list[dict[str, Any]]:
    return [
        {"name": c.name, "type": c.type, "nullable": getattr(c, "nullable", True)}
        for c in getattr(table_info, "columns", [])
    ]


def register_table(
    db: Session,
    state: AppState,
    workspace_id: str,
    table: str,
    *,
    name: str,
    source_kind: str,
    source_ref: dict[str, Any],
    user_id: str | None,
    upload_id: str | None = None,
    data_source_id: str | None = None,
    ingest_options: dict[str, Any] | None = None,
    description: str = "",
) -> tuple[Dataset, DatasetVersion | None]:
    """Create or refresh the Dataset row for a store table and record a new version if content changed."""
    store = state.stores.get(workspace_id)
    info = store.describe(table)
    version = store.table_version(table)
    ds = dataset_by_table(db, workspace_id, table)
    if ds is None:
        ds = Dataset(
            workspace_id=workspace_id,
            name=name,
            table_name=table,
            source_kind=source_kind,
            source_ref=source_ref,
            data_source_id=data_source_id,
            created_by=user_id,
            description=description,
        )
        db.add(ds)
        db.flush()
    else:
        ds.source_ref = source_ref
        ds.source_kind = source_kind
    ds.columns = _columns_json(info)
    ds.row_count = int(version.row_count)
    new_version: DatasetVersion | None = None
    current = db.get(DatasetVersion, ds.current_version_id) if ds.current_version_id else None
    if current is None or current.content_hash != version.content_hash:
        last = (
            db.scalar(select(func.max(DatasetVersion.version_no)).where(DatasetVersion.dataset_id == ds.id))
            or 0
        )
        new_version = DatasetVersion(
            dataset_id=ds.id,
            version_no=last + 1,
            content_hash=version.content_hash,
            row_count=int(version.row_count),
            columns=ds.columns,
            ingest_options=ingest_options or {},
            upload_id=upload_id,
            created_by=user_id,
        )
        db.add(new_version)
        db.flush()
        ds.current_version_id = new_version.id
        ds.profile_status = "pending"
    ds.updated_at = utcnow()
    db.flush()
    return ds, new_version


def dataset_versions_for_tables(
    db: Session, state: AppState, workspace_id: str, tables: list[str]
) -> dict[str, dict[str, Any]]:
    """Current dataset version identity for each referenced table (for reproducibility records)."""
    out: dict[str, dict[str, Any]] = {}
    for table in tables:
        ds = dataset_by_table(db, workspace_id, table)
        if ds is None or ds.current_version_id is None:
            continue
        ver = db.get(DatasetVersion, ds.current_version_id)
        if ver is None:
            continue
        out[table] = {
            "dataset_id": ds.id,
            "version_id": ver.id,
            "version_no": ver.version_no,
            "content_hash": ver.content_hash,
            "row_count": ver.row_count,
        }
    return out


# ------------------------------------------------------------------ jobs


def profile_dataset(
    state: AppState, workspace_id: str, dataset_id: str, user_id: str | None
) -> dict[str, Any]:
    with state.session_factory() as db:
        ds = get_dataset(db, workspace_id, dataset_id)
        ds.profile_status = "running"
        db.commit()
        table = ds.table_name
    try:
        with timed_event(
            state.session_factory,
            category="profiling",
            name=f"profile:{table}",
            workspace_id=workspace_id,
            user_id=user_id,
            detail={"dataset_id": dataset_id},
        ) as ev:
            profile = eng.profile_table(state.stores.get(workspace_id), table)
            ev.detail["issues"] = len(profile.get("issues", []))
            ev.detail["columns"] = profile.get("column_count")
    except Exception as exc:
        with state.session_factory() as db:
            ds = get_dataset(db, workspace_id, dataset_id)
            ds.profile_status = "failed"
            ds.profile = {"error": f"{type(exc).__name__}: {exc}"}
            db.commit()
        raise
    with state.session_factory() as db:
        ds = get_dataset(db, workspace_id, dataset_id)
        ds.profile = profile
        ds.profile_status = "ready"
        ds.profiled_at = utcnow()
        db.commit()
    return {"dataset_id": dataset_id, "issues": len(profile.get("issues", []))}


def measure_relationship(state: AppState, workspace_id: str, rel: RelationshipRecord) -> None:
    """Measure the join on the data (observed cardinality, fan-out, orphans) and flag a mismatch with the
    declared cardinality, e.g. a "many_to_one" whose one side has duplicate keys (review R-07/R-48)."""
    from .semantic import normalise_cardinality

    declared = normalise_cardinality(rel.cardinality)
    try:
        analysis = eng.analyze_join(
            state.stores.get(workspace_id),
            from_table=rel.from_table,
            from_col=rel.from_col,
            to_table=rel.to_table,
            to_col=rel.to_col,
            cardinality=declared,
        )
    except Exception as exc:  # noqa: BLE001 - analysis is advisory; keep the error visible to the user
        rel.join_analysis = {"error": f"{type(exc).__name__}: {exc}", "warnings": []}
        return
    observed = analysis.get("observed_cardinality")
    warnings = list(analysis.get("warnings") or [])
    if observed and observed != declared:
        msg = f"declared {declared} but the data is {observed}" + (
            "; the 'one' side has duplicate keys, so joining on it can double count"
            if declared in {"many_to_one", "one_to_one", "one_to_many"} and observed == "many_to_many"
            else ""
        )
        if msg not in warnings:
            warnings.insert(0, msg)
    rel.join_analysis = {
        **analysis,
        "warnings": warnings,
        "declared_cardinality": declared,
        "cardinality_mismatch": bool(observed and observed != declared),
    }


def refresh_relationship_suggestions(state: AppState, workspace_id: str, user_id: str | None) -> int:
    """Run relationship discovery over all workspace tables; never overrides user decisions."""
    with state.session_factory() as db:
        tables = [
            d.table_name for d in db.scalars(select(Dataset).where(Dataset.workspace_id == workspace_id))
        ]
    if len(tables) < 2:
        return 0
    with timed_event(
        state.session_factory,
        category="profiling",
        name="relationship_discovery",
        workspace_id=workspace_id,
        user_id=user_id,
        detail={"tables": len(tables)},
    ) as ev:
        suggestions = eng.discover_relationships(state.stores.get(workspace_id), tables)
        ev.detail["suggestions"] = len(suggestions)
    created = 0
    with state.session_factory() as db:
        for s in suggestions:
            existing = db.scalar(
                select(RelationshipRecord).where(
                    RelationshipRecord.workspace_id == workspace_id,
                    RelationshipRecord.from_table == s["from_table"],
                    RelationshipRecord.from_col == s["from_col"],
                    RelationshipRecord.to_table == s["to_table"],
                    RelationshipRecord.to_col == s["to_col"],
                )
            )
            if existing is not None:
                existing.confidence = s.get("confidence", existing.confidence)
                existing.signals = s.get("signals", existing.signals)
                existing.overlap_pct = s.get("overlap_pct", existing.overlap_pct)
                if existing.status == "suggested":
                    existing.cardinality = s.get("cardinality", existing.cardinality)
                continue
            db.add(
                RelationshipRecord(
                    workspace_id=workspace_id,
                    from_table=s["from_table"],
                    from_col=s["from_col"],
                    to_table=s["to_table"],
                    to_col=s["to_col"],
                    cardinality=s.get("cardinality", "many_to_one"),
                    confidence=s.get("confidence", "medium"),
                    signals=s.get("signals", []),
                    overlap_pct=s.get("overlap_pct"),
                    status="suggested",
                    origin="discovered",
                )
            )
            created += 1
        db.commit()
    return created


def ingest_upload_job(
    state: AppState, workspace_id: str, upload_id: str, request: dict[str, Any], user_id: str | None
) -> Callable[[JobContext], dict[str, Any]]:
    def run(ctx: JobContext) -> dict[str, Any]:
        with state.session_factory() as db:
            up = db.get(Upload, upload_id)
            if up is None or up.workspace_id != workspace_id:
                raise NotFound("Upload not found")
            path = Path(up.stored_path)
            filename = up.filename
        table = request.get("table_name") or table_name_for(request.get("dataset_name") or filename)
        options = dict(request.get("options") or {})
        options.setdefault("table_name", table)
        if request.get("if_exists"):
            options.setdefault("if_exists", request["if_exists"])
        ctx.progress(0.1, "Ingesting file")
        with (
            timed_event(
                state.session_factory,
                category="import",
                name=f"ingest:{filename}",
                workspace_id=workspace_id,
                user_id=user_id,
                detail={"upload_id": upload_id, "table": table},
            ) as ev,
            state.stores.writing(workspace_id) as store,
        ):
            info = eng.ingest_file(store, path, options)
            table = getattr(info, "name", table)
            ev.detail["rows"] = getattr(info, "row_count", None)
        ctx.progress(0.5, "Recording dataset version")
        with state.session_factory() as db:
            ds, ver = register_table(
                db,
                state,
                workspace_id,
                table,
                name=request.get("dataset_name") or table,
                source_kind="upload",
                source_ref={"upload_id": upload_id, "filename": filename},
                user_id=user_id,
                upload_id=upload_id,
                ingest_options=options,
                description=request.get("description") or "",
            )
            up = db.get(Upload, upload_id)
            if up is not None:
                up.status = "ingested"
            db.commit()
            dataset_id = ds.id
            version_id = ver.id if ver else ds.current_version_id
        ctx.progress(0.6, "Profiling")
        profile_dataset(state, workspace_id, dataset_id, user_id)
        ctx.progress(0.9, "Discovering relationships")
        suggestions = refresh_relationship_suggestions(state, workspace_id, user_id)
        return {
            "dataset_id": dataset_id,
            "table_name": table,
            "version_id": version_id,
            "new_relationship_suggestions": suggestions,
        }

    return run


def profile_job(
    state: AppState, workspace_id: str, dataset_id: str, user_id: str | None
) -> Callable[[JobContext], dict[str, Any]]:
    def run(_ctx: JobContext) -> dict[str, Any]:
        return profile_dataset(state, workspace_id, dataset_id, user_id)

    return run


def snapshot_remote_table_job(
    state: AppState, workspace_id: str, source_id: str, request: dict[str, Any], user_id: str | None
) -> Callable[[JobContext], dict[str, Any]]:
    from .connectors import connector_for

    def run(ctx: JobContext) -> dict[str, Any]:
        with state.session_factory() as db:
            src = db.get(DataSource, source_id)
            if src is None or src.workspace_id != workspace_id:
                raise NotFound("Data source not found")
            db.expunge(src)
        schema, remote = request["schema_name"], request["table"]
        table = request.get("table_name") or table_name_for(remote)
        start = time.perf_counter()
        ctx.progress(0.1, f"Reading {schema}.{remote}")
        with connector_for(state.secret_box, src) as conn:
            dialect = getattr(conn, "dialect", "duckdb")
            from sqlglot import exp

            select_sql = (
                exp.select("*").from_(exp.table_(remote, db=schema, quoted=True)).sql(dialect=dialect)
            )
            limit = request.get("row_limit") or 1_000_000
            result = conn.query(select_sql, None, limit)
        ctx.progress(0.6, "Writing snapshot")
        with state.stores.writing(workspace_id) as store:
            store.write_table(table, result.to_pandas(), if_exists="replace")
        record_event(
            state.session_factory,
            category="import",
            name=f"snapshot:{src.kind}",
            duration_ms=(time.perf_counter() - start) * 1000,
            workspace_id=workspace_id,
            user_id=user_id,
            detail={"source": f"{schema}.{remote}", "rows": result.row_count, "truncated": result.truncated},
        )
        with state.session_factory() as db:
            ds, ver = register_table(
                db,
                state,
                workspace_id,
                table,
                name=request.get("dataset_name") or table,
                source_kind="connector",
                source_ref={
                    "data_source_id": source_id,
                    "schema": schema,
                    "table": remote,
                    "truncated": result.truncated,
                    "row_limit": limit,
                },
                user_id=user_id,
                data_source_id=source_id,
            )
            db.commit()
            dataset_id = ds.id
        profile_dataset(state, workspace_id, dataset_id, user_id)
        refresh_relationship_suggestions(state, workspace_id, user_id)
        return {
            "dataset_id": dataset_id,
            "table_name": table,
            "rows": result.row_count,
            "truncated": result.truncated,
        }

    return run


def drop_dataset(db: Session, state: AppState, ds: Dataset) -> None:
    from ..models import Workspace
    from .semantic import build_model

    ws = db.get(Workspace, ds.workspace_id)
    if ws is not None:
        model = build_model(db, ws).model
        users = [e.name for e in model.entities if e.table == ds.table_name]
        if users:
            raise Conflict(
                f"Dataset is used by semantic entities {users}; remove them first", code="dataset_in_use"
            )
    with state.stores.writing(ds.workspace_id) as store:
        store.drop_table(ds.table_name)
    for rel in db.scalars(
        select(RelationshipRecord).where(
            RelationshipRecord.workspace_id == ds.workspace_id,
            (RelationshipRecord.from_table == ds.table_name) | (RelationshipRecord.to_table == ds.table_name),
        )
    ):
        db.delete(rel)
    db.delete(ds)


def ensure_table_name_free(db: Session, workspace_id: str, table: str, if_exists: str) -> None:
    existing = dataset_by_table(db, workspace_id, table)
    if existing is not None and if_exists == "fail":
        raise Conflict(
            f"A dataset with table name {table!r} already exists; choose another name or "
            "set if_exists to 'replace' or 'append'",
            code="table_exists",
        )
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$", table):
        raise Unprocessable(
            "Table names must be identifiers (letters, digits, underscore)", code="invalid_table_name"
        )
