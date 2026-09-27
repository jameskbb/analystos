"""Dashboards: tiles bound to semantic metrics, saved queries, artifacts or findings.

Metric tiles inherit their definitions from the semantic layer (compiled by the engine), and
dashboard-level filters / date ranges are applied only where the metric supports the
dimension; ignored filters are reported in the tile provenance instead of silently dropped.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from analystos_engine.semantic.compiler import MetricQuery
from analystos_engine.types import TimeWindow
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import NotFound, Unprocessable
from ..models import (
    ArtifactRecord,
    Dashboard,
    DashboardTile,
    DashboardVersion,
    Finding,
    SavedQuery,
    Workspace,
)
from ..state import AppState
from .charts import suggest_charts
from .investigations import filter_context, metric_filter_chips, reference_date
from .queries import run_sql
from .semantic import BuiltModel, add_join_warnings, build_model


def get_dashboard(db: Session, workspace_id: str, dashboard_id: str) -> Dashboard:
    d = db.get(Dashboard, dashboard_id)
    if d is None or d.workspace_id != workspace_id:
        raise NotFound("Dashboard not found")
    return d


def tiles_of(db: Session, dashboard_id: str) -> list[DashboardTile]:
    return list(
        db.scalars(
            select(DashboardTile)
            .where(DashboardTile.dashboard_id == dashboard_id)
            .order_by(DashboardTile.created_at, DashboardTile.id)
        )
    )


def snapshot_dashboard(db: Session, d: Dashboard, user_id: str | None) -> DashboardVersion:
    last = (
        db.scalar(select(func.max(DashboardVersion.version_no)).where(DashboardVersion.dashboard_id == d.id))
        or 0
    )
    d.version_no = last + 1
    snap = {
        "name": d.name,
        "description": d.description,
        "layout": d.layout,
        "filters": d.filters,
        "date_range": d.date_range,
        "tiles": [
            {"id": t.id, "kind": t.kind, "title": t.title, "binding": t.binding, "viz": t.viz, "text": t.text}
            for t in tiles_of(db, d.id)
        ],
    }
    v = DashboardVersion(dashboard_id=d.id, version_no=d.version_no, snapshot=snap, created_by=user_id)
    db.add(v)
    db.flush()
    return v


def validate_binding(db: Session, ws: Workspace, kind: str, binding: dict[str, Any]) -> None:
    if kind == "text":
        return
    keys = [k for k in ("metric_query", "saved_query_id", "artifact_id", "finding_id") if binding.get(k)]
    if len(keys) != 1:
        raise Unprocessable(
            "A tile binding needs exactly one of metric_query, saved_query_id, artifact_id, finding_id",
            code="invalid_binding",
        )
    key = keys[0]
    if key == "metric_query":
        try:
            q = MetricQuery.model_validate(binding["metric_query"])
        except ValueError as exc:
            raise Unprocessable(f"Invalid metric query: {exc}", code="invalid_binding") from exc
        model = build_model(db, ws).model
        missing = [m for m in q.metrics if not model.has_metric(m)]
        if missing:
            raise Unprocessable(f"Unknown metrics: {missing}", code="unknown_metric")
    elif key == "saved_query_id":
        sq = db.get(SavedQuery, binding["saved_query_id"])
        if sq is None or sq.workspace_id != ws.id:
            raise Unprocessable("Saved query not found", code="invalid_binding")
    elif key == "artifact_id":
        a = db.get(ArtifactRecord, binding["artifact_id"])
        if a is None or a.workspace_id != ws.id:
            raise Unprocessable("Artifact not found", code="invalid_binding")
    else:
        f = db.get(Finding, binding["finding_id"])
        if f is None or f.workspace_id != ws.id:
            raise Unprocessable("Finding not found", code="invalid_binding")


def resolve_range(ws: Workspace, date_range: dict[str, Any] | None, built: BuiltModel) -> TimeWindow | None:
    if not date_range:
        return None
    if date_range.get("text"):
        from analystos_engine.calendar import CalendarError, resolve_period

        try:
            return resolve_period(str(date_range["text"]), reference_date(ws), built.model.calendar)
        except CalendarError as exc:
            raise Unprocessable(f"Invalid date range: {exc}", code="invalid_period") from exc
    if date_range.get("start") and date_range.get("end"):
        start = dt.date.fromisoformat(str(date_range["start"]))
        end_inclusive = dt.date.fromisoformat(str(date_range["end"]))
        return TimeWindow(start=start, end=end_inclusive + dt.timedelta(days=1), kind="custom")
    return None


def _merge_filters(
    model: Any, metrics: list[str], base: list[Any], extra: list[dict[str, Any]]
) -> tuple[list[Any], list[str]]:
    from analystos_engine.semantic.models import Filter
    from analystos_investigator.semantic_graph import dimensions_for_metric

    usable: set[str] | None = None
    for m in metrics:
        names = {d.name for d in dimensions_for_metric(model, m)[0]}
        usable = names if usable is None else usable & names
    out = list(base)
    ignored: list[str] = []
    for f in extra:
        dim = f.get("dimension")
        if not dim or not f.get("values") and f.get("op", "in") not in ("is_null", "not_null"):
            continue
        if usable is not None and dim not in usable:
            ignored.append(f"{dim} (not a dimension of {', '.join(metrics)})")
            continue
        out.append(Filter(dimension=dim, op=f.get("op", "in"), values=list(f.get("values", []))))
    return out, ignored


def _ignored_chips(ignored: list[str]) -> list[dict[str, Any]]:
    """Dashboard filters a tile could not apply, shown as chips rather than silently dropped (spec §47)."""
    return [
        {
            "label": "Not applied",
            "value": item,
            "kind": "filter",
            "dimension": item.split(" ")[0],
            "op": "ignored",
            "values": [],
            "ignored": True,
        }
        for item in ignored
    ]


def tile_metric_query(
    ws: Workspace,
    d: Dashboard,
    tile: DashboardTile,
    built: BuiltModel,
    filters: list[dict[str, Any]] | None,
    date_range: dict[str, Any] | None,
) -> tuple[MetricQuery, Any, list[str], str | None]:
    """The tile's MetricQuery with the dashboard's (and request's) filters and date range applied, compiled."""
    from analystos_engine.semantic.compiler import CompileError, compile
    from analystos_investigator.semantic_graph import time_dimension_for_metric

    q = MetricQuery.model_validate((tile.binding or {})["metric_query"])
    missing = [m for m in q.metrics if not built.model.has_metric(m)]
    if missing:
        raise Unprocessable(f"Tile {tile.title or tile.id}: unknown metrics {missing}", code="unknown_metric")
    window = (
        None
        if (tile.viz or {}).get("ignore_date_range")
        else resolve_range(ws, date_range or d.date_range, built)
    )
    all_filters, ignored = _merge_filters(
        built.model, q.metrics, q.filters, [*(d.filters or []), *(filters or [])]
    )
    time_dim = time_dimension_for_metric(built.model, q.metrics[0])
    if window is not None and time_dim:
        q = q.model_copy(update={"time": window.with_dimension(time_dim)})
    elif window is not None:
        ignored.append(f"date range (no time dimension for {q.metrics[0]})")
    q = q.model_copy(update={"filters": all_filters})
    try:
        compiled = compile(built.model, q)
    except (CompileError, ValueError) as exc:
        raise Unprocessable(f"Tile {tile.title or tile.id}: {exc}", code="compile_error") from exc
    return q, compiled, ignored, time_dim


def tile_lineage(db: Session, ws: Workspace, d: Dashboard, tile: DashboardTile) -> dict[str, Any]:
    """Dashboard tile -> KPI/chart -> query -> metric (version) -> entity -> dataset (version) -> source
    (spec §62: "Executive Revenue KPI -> Revenue Metric -> Orders Dataset")."""
    from .lineage import GraphBuilder, _dataset_chain, _metric_chain, _referenced_tables, artifact_lineage

    g = GraphBuilder()
    did = g.node(f"dashboard:{d.id}", "dashboard", d.name, ref_id=d.id, meta={"version_no": d.version_no})
    tid = g.node(
        f"tile:{tile.id}",
        "kpi" if tile.kind == "kpi" else "tile",
        tile.title or tile.kind,
        detail=tile.kind,
        ref_id=tile.id,
        meta={"binding": tile.binding or {}},
    )
    g.edge(tid, did, "part_of")
    b = tile.binding or {}
    cache: dict[str, Any] = {}
    if b.get("metric_query"):
        built = build_model(db, ws)
        cache["built"] = built
        q, compiled, ignored, _ = tile_metric_query(ws, d, tile, built, None, None)
        qid = g.node(
            f"query:tile:{tile.id}",
            "query",
            f"Compiled query for {tile.title or tile.kind}",
            detail="semantic",
            meta={
                "sql": compiled.sql,
                "ignored_filters": ignored,
                "filter_context": [
                    *filter_context(window=q.time, filters=q.filters),
                    *metric_filter_chips(built.model, q.metrics),
                ],
            },
        )
        g.edge(qid, tid, "feeds")
        for m in q.metrics:
            ref = built.metric_versions.get(m)
            g.edge(
                _metric_chain(g, db, ws, m, ref.engine_version_id if ref else None, cache),
                qid,
                "computed_from",
            )
    elif b.get("saved_query_id"):
        sq = db.get(SavedQuery, b["saved_query_id"])
        if sq is None or sq.workspace_id != ws.id:
            raise NotFound("Saved query not found")
        qid = g.node(
            f"saved_query:{sq.id}",
            "query",
            sq.name,
            detail=f"saved query v{sq.version_no}",
            ref_id=sq.id,
            meta={"sql": sq.sql},
        )
        g.edge(qid, tid, "feeds")
        if not sq.data_source_id:
            for table in _referenced_tables(sq.sql):
                g.edge(_dataset_chain(g, db, ws.id, table, None), qid, "reads")
    elif b.get("artifact_id"):
        a = db.get(ArtifactRecord, b["artifact_id"])
        if a is not None and a.workspace_id == ws.id:
            artifact_lineage(db, ws, [a], g, attach_to=tid)
    elif b.get("finding_id"):
        f = db.get(Finding, b["finding_id"])
        if f is not None and f.workspace_id == ws.id:
            fid = g.node(
                f"finding:{f.id}",
                "finding",
                f.statement[:120],
                detail=f.statement_type,
                ref_id=f.id,
                meta={"status": f.status, "evidence_strength": f.evidence_strength},
            )
            g.edge(fid, tid, "shown_in")
            rows = list(db.scalars(select(ArtifactRecord).where(ArtifactRecord.id.in_(f.artifact_ids or []))))
            artifact_lineage(db, ws, rows, g, attach_to=fid)
    return g.graph()


def tile_data(
    db: Session,
    state: AppState,
    ws: Workspace,
    d: Dashboard,
    tile: DashboardTile,
    filters: list[dict[str, Any]] | None,
    date_range: dict[str, Any] | None,
    user_id: str | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "tile_id": tile.id,
        "kind": tile.kind,
        "title": tile.title,
        "result": None,
        "kpi": None,
        "chart": None,
        "text": tile.text or None,
        "filter_context": [],
        "provenance": {},
        "compiled": None,
    }
    b = tile.binding or {}
    if tile.kind == "text":
        return out
    if b.get("metric_query"):
        from analystos_engine.calendar import previous_period
        from analystos_engine.semantic.compiler import compile

        built = build_model(db, ws)
        q, compiled, ignored, time_dim = tile_metric_query(ws, d, tile, built, filters, date_range)
        add_join_warnings(state.stores.get(ws.id), built.model, compiled)
        outcome = run_sql(
            db,
            state,
            workspace_id=ws.id,
            user_id=user_id,
            sql=compiled.sql,
            origin="dashboard",
            limit=q.limit,
        )
        outcome.run.metric_versions = built.version_map(q.metrics)
        res = outcome.result.model_dump(mode="json")
        out["result"] = res
        out["compiled"] = compiled.model_dump(mode="json")
        out["filter_context"] = [
            *filter_context(window=q.time, filters=q.filters),
            *metric_filter_chips(built.model, q.metrics),
            *_ignored_chips(ignored),
        ]
        out["provenance"] = {
            "metric_versions": built.version_map(q.metrics),
            "sql": compiled.sql,
            "dataset_versions": outcome.run.dataset_versions,
            "ignored_filters": ignored,
            "query_run_id": outcome.run.id,
            "warnings": list(compiled.warnings),
        }
        metric = built.model.get_metric(q.metrics[0])
        if tile.kind == "kpi":
            names = [c["name"] for c in res["columns"]]
            value = res["rows"][0][names.index(metric.id)] if res["rows"] and not q.dimensions else None
            kpi = {
                "label": tile.title or metric.display_name,
                "metric_id": metric.id,
                "format": metric.format,
                "value": value,
                "baseline": None,
                "abs_change": None,
                "pct_change": None,
                "higher_is_better": metric.higher_is_better,
            }
            if q.time is not None and not q.dimensions:
                base_q = q.model_copy(
                    update={"time": previous_period(q.time, "pop").with_dimension(time_dim)}
                )
                base_c = compile(built.model, base_q)
                base_res = state.stores.get(ws.id).execute_read(
                    base_c.sql, None, 1, state.settings.query_timeout_s
                )
                baseline = base_res.scalar(metric.id) if base_res.rows else None
                kpi["baseline"] = baseline
                kpi["baseline_period"] = base_q.time.model_dump(mode="json") if base_q.time else None
                if isinstance(value, int | float) and isinstance(baseline, int | float):
                    kpi["abs_change"] = value - baseline
                    kpi["pct_change"] = (value - baseline) / abs(baseline) if baseline else None
                out["provenance"]["baseline_sql"] = base_c.sql
                if base_q.time is not None:
                    out["filter_context"] += filter_context(baseline=base_q.time)
            out["kpi"] = kpi
        elif tile.kind == "chart" and res["row_count"]:
            suggestions = suggest_charts(res["columns"], res["rows"], title=tile.title)
            wanted = (tile.viz or {}).get("type")
            out["chart"] = next((s for s in suggestions if s["type"] == wanted), suggestions[0])
        return out
    if b.get("saved_query_id"):
        sq = db.get(SavedQuery, b["saved_query_id"])
        if sq is None or sq.workspace_id != ws.id:
            raise NotFound("Saved query not found")
        outcome = run_sql(
            db,
            state,
            workspace_id=ws.id,
            user_id=user_id,
            sql=sq.sql,
            params=b.get("params") or {},
            param_definitions=sq.parameters,
            origin="dashboard",
            saved_query_id=sq.id,
            data_source_id=sq.data_source_id,
        )
        res = outcome.result.model_dump(mode="json")
        out["result"] = res
        ignored_sq = [
            f"{f.get('dimension')} (saved queries use their own SQL; set a parameter instead)"
            for f in [*(d.filters or []), *(filters or [])]
            if f.get("dimension")
        ]
        if date_range or d.date_range:
            ignored_sq.append("date range (saved queries use their own SQL; set a parameter instead)")
        out["filter_context"] = [
            {"label": k, "value": str(v), "kind": "filter", "dimension": k, "op": "param", "values": [v]}
            for k, v in (outcome.run.params or {}).items()
        ]
        out["filter_context"] += _ignored_chips(ignored_sq)
        out["provenance"] = {
            "saved_query_id": sq.id,
            "saved_query_version": sq.version_no,
            "sql": sq.sql,
            "dataset_versions": outcome.run.dataset_versions,
            "query_run_id": outcome.run.id,
            "ignored_filters": ignored_sq,
        }
        if tile.kind in ("chart", "kpi") and res["row_count"]:
            suggestions = suggest_charts(res["columns"], res["rows"], title=tile.title)
            wanted = (tile.viz or {}).get("type") or ("kpi" if tile.kind == "kpi" else None)
            out["chart"] = next((s for s in suggestions if s["type"] == wanted), suggestions[0])
        return out
    if b.get("artifact_id"):
        a = db.get(ArtifactRecord, b["artifact_id"])
        if a is None or a.workspace_id != ws.id:
            raise NotFound("Artifact not found")
        out["result"] = a.result
        out["filter_context"] = a.filter_context or []
        out["chart"] = (
            {
                "type": "artifact",
                "title": a.title,
                "reason": "Chart stored with the analysis artifact",
                "score": 1.0,
                "x": None,
                "y": [],
                "series": None,
                "option": a.chart_spec,
            }
            if a.chart_spec
            else None
        )
        out["provenance"] = {
            "artifact_id": a.id,
            "investigation_id": a.investigation_id,
            "run_id": a.run_id,
            "sql": a.sql,
            "metric_versions": a.metric_versions,
            "dataset_versions": a.dataset_versions,
            "snapshot": True,
        }
        return out
    if b.get("finding_id"):
        f = db.get(Finding, b["finding_id"])
        if f is None or f.workspace_id != ws.id:
            raise NotFound("Finding not found")
        out["text"] = f.statement
        out["kpi"] = {
            "label": tile.title or "Finding",
            "value": f.values.get("current"),
            "baseline": f.values.get("baseline"),
            "abs_change": f.values.get("abs_change"),
            "pct_change": f.values.get("pct_change"),
            "format": f.values.get("format"),
            "statement_type": f.statement_type,
            "evidence_strength": f.evidence_strength,
            "status": f.status,
        }
        out["filter_context"] = f.filter_context or []
        out["provenance"] = {
            "finding_id": f.id,
            "artifact_ids": f.artifact_ids,
            "metric_versions": f.metric_version_ids,
        }
        return out
    return out


def dashboard_from_investigation(db: Session, ws: Workspace, inv: Any, user_id: str | None) -> Dashboard:
    from analystos_investigator import Investigation as EngineInvestigation
    from analystos_investigator.semantic_graph import time_dimension_for_metric

    doc = EngineInvestigation.model_validate(inv.document)
    ctx = doc.plan.context if doc.plan else None
    if ctx is None or not doc.tree.root_id:
        raise Unprocessable("Run the investigation before building a dashboard", code="not_run")
    built = build_model(db, ws)
    metric = built.model.get_metric(ctx.metric_id)
    d = Dashboard(
        workspace_id=ws.id,
        name=f"{metric.display_name}: {inv.title}"[:300],
        description=f"Built from investigation {inv.id}.",
        created_by=user_id,
        date_range={"start": ctx.window.start.isoformat(), "end": ctx.window.last_day.isoformat()},
        filters=[{"dimension": f.dimension, "op": f.op, "values": f.values} for f in ctx.filters],
    )
    db.add(d)
    db.flush()
    tiles: list[tuple[DashboardTile, dict[str, int]]] = []
    kpi = DashboardTile(
        dashboard_id=d.id,
        kind="kpi",
        title=metric.display_name,
        binding={"metric_query": {"metrics": [metric.id]}},
    )
    tiles.append((kpi, {"w": 3, "h": 3}))
    time_dim = time_dimension_for_metric(built.model, metric.id)
    if time_dim:
        trend = DashboardTile(
            dashboard_id=d.id,
            kind="chart",
            title=f"{metric.display_name} by month",
            binding={"metric_query": {"metrics": [metric.id], "dimensions": [f"{time_dim}__month"]}},
            viz={"type": "line", "ignore_date_range": False},
        )
        tiles.append((trend, {"w": 9, "h": 3}))
    text = DashboardTile(dashboard_id=d.id, kind="text", title="Answer", text=doc.brief_answer or "")
    tiles.append((text, {"w": 12, "h": 2}))
    rows = {
        a.engine_artifact_id: a
        for a in db.scalars(select(ArtifactRecord).where(ArtifactRecord.run_id == inv.current_run_id))
    }
    groups = sorted(
        [n for n in doc.tree.nodes if n.parent_id == doc.tree.root_id and n.kind == "dimension"],
        key=lambda n: (n.rank or 99, n.id),
    )[:2]
    for g in groups:
        art = next((rows[a] for a in g.artifact_ids if a in rows and rows[a].chart_spec), None)
        if art is not None:
            t = DashboardTile(
                dashboard_id=d.id,
                kind="chart",
                title=art.title or g.statement[:100],
                binding={"artifact_id": art.id},
            )
            tiles.append((t, {"w": 6, "h": 4}))
    layout = []
    x = y = row_h = 0
    for t, size in tiles:
        db.add(t)
        db.flush()
        if x + size["w"] > 12:
            x, y, row_h = 0, y + row_h, 0
        layout.append({"i": t.id, "x": x, "y": y, "w": size["w"], "h": size["h"]})
        x += size["w"]
        row_h = max(row_h, size["h"])
    d.layout = layout
    db.flush()
    snapshot_dashboard(db, d, user_id)
    return d
