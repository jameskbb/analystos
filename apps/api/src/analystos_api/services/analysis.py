"""Advanced analyses (spec §39-44) on workspace data and the semantic model.

Every endpoint in ``routers/analysis.py`` lands here. The numbers come only from the engine's
``analystos_engine.analysis.*`` functions run on data read with read-only SQL (the semantic
compiler for metric inputs, ``ensure_read_only`` for SQL inputs). Each call persists an
``ArtifactRecord`` (``origin="analysis"``) holding the input SQL, parameters, metric versions,
dataset versions, filter context and the full engine result, so the analysis has lineage and
can be re-run like any investigation artifact.

Labels follow the spec: correlation, regression and clustering are "Exploratory" (associations,
not causes, §43); forecasts are "Model estimate" with backtested accuracy (§41); rule-based
segments and anomaly scans are "Descriptive" (§39, §42); tests are "Statistical test" with their
assumption checks (§44).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.orm import Session

from ..errors import ApiError, NotFound, Unprocessable, UnsafeQuery
from ..models import ArtifactRecord, SavedQuery, Workspace
from ..schemas.analysis import (
    AnalysisKind,
    AnalysisOut,
    AnomalyRequest,
    CorrelationRequest,
    ForecastRequest,
    RegressionRequest,
    SegmentRequest,
    StatsTestRequest,
    TabularSource,
    TimeSeriesSource,
)
from ..schemas.common import TabularResult
from ..state import AppState
from .datasets import dataset_versions_for_tables
from .investigations import filter_context, metric_filter_chips, reference_date
from .observability import timed_event
from .queries import bind_parameters
from .semantic import BuiltModel, add_join_warnings, build_model

CUSTOMER_CAP = 1000


class AnalysisFailed(ApiError):
    status_code = 422
    code = "analysis_failed"


class GrainFailed(ApiError):
    status_code = 422
    code = "grain_error"


# ------------------------------------------------------------------------------ helpers


def to_json(value: Any) -> Any:
    """JSON-safe copy of engine output (numpy scalars, NaN, dates, pydantic models)."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(v) for v in value]
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if hasattr(value, "item") and callable(value.item) and type(value).__module__ == "numpy":
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def fmt(x: float | None) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.4g}"


@dataclass
class Input:
    """Rows read for an analysis, with what is needed to reproduce and trace them."""

    frame: Any
    sql: str
    row_count: int
    truncated: bool
    tables: list[str] = field(default_factory=list)
    metric_versions: dict[str, str] = field(default_factory=dict)
    filter_context: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _referenced(sql: str) -> list[str]:
    from analystos_engine.sqlsafety import referenced_tables

    try:
        return [t.split(".")[-1] for t in referenced_tables(sql)]
    except Exception:  # noqa: BLE001 - lineage is best effort; safety was enforced before execution
        return []


def _run(state: AppState, workspace_id: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    from analystos_engine.sqlsafety import UnsafeSQLError, ensure_read_only
    from analystos_engine.store import QueryTimeoutError, StoreError

    try:
        ensure_read_only(sql, "duckdb")
        return state.stores.get(workspace_id).execute_read(
            sql, params or None, state.settings.analysis_row_limit, state.settings.query_timeout_s
        )
    except UnsafeSQLError as exc:
        raise UnsafeQuery(f"Query rejected: {exc}") from exc
    except QueryTimeoutError as exc:
        raise ApiError(str(exc), code="query_timeout") from exc
    except StoreError as exc:
        raise ApiError(str(exc), code="query_failed") from exc


def _compile_errors(fn: Any) -> Any:
    from analystos_engine.semantic.compiler import CompileError
    from analystos_engine.semantic.compiler import GrainError as EngineGrainError

    try:
        return fn()
    except EngineGrainError as exc:
        raise GrainFailed(str(exc)) from exc
    except CompileError as exc:
        raise Unprocessable(str(exc), code="compile_error") from exc


def _require_metric(built: BuiltModel, metric_id: str) -> None:
    if not built.model.has_metric(metric_id):
        raise NotFound(f"Metric {metric_id!r} not found")


def load_tabular(
    db: Session, state: AppState, ws: Workspace, src: TabularSource, built: BuiltModel | None = None
) -> Input:
    """Read a TabularSource into a DataFrame (read-only, capped at AOS_ANALYSIS_ROW_LIMIT)."""
    from analystos_engine.semantic.compiler import compile as compile_query

    metric_versions: dict[str, str] = {}
    chips: list[dict[str, Any]] = []
    params: dict[str, Any] = {}
    if src.metric_query is not None:
        built = built or build_model(db, ws)
        for m in src.metric_query.metrics:
            _require_metric(built, m)
        compiled = _compile_errors(lambda: compile_query(built.model, src.metric_query))
        add_join_warnings(state.stores.get(ws.id), built.model, compiled)
        sql = compiled.sql
        metric_versions = dict(compiled.metric_versions)
        chips = filter_context(
            window=src.metric_query.time,
            filters=[f.model_dump(mode="json") for f in src.metric_query.filters],
        )
        chips += metric_filter_chips(built.model, src.metric_query.metrics)
    elif src.saved_query_id is not None:
        sq = db.get(SavedQuery, src.saved_query_id)
        if sq is None or sq.workspace_id != ws.id:
            raise NotFound("Saved query not found")
        if sq.data_source_id:
            raise Unprocessable(
                "Analyses run on workspace data; this saved query targets an external source",
                code="external_source",
            )
        sql = sq.sql
        params = bind_parameters(sql, list(sq.parameters or []), src.params)
    elif src.table is not None:
        known = {t.name for t in state.stores.get(ws.id).list_tables(include_row_counts=False)}
        if src.table not in known:
            raise NotFound(f"Table {src.table!r} not found")
        from analystos_engine.store import quote_ident

        sql = f"SELECT * FROM {quote_ident(src.table)}"
    else:
        sql = src.sql or ""
        params = bind_parameters(sql, [], src.params)
    res = _run(state, ws.id, sql, params)
    notes = []
    if res.truncated:
        notes.append(
            f"input capped at {state.settings.analysis_row_limit:,} rows (AOS_ANALYSIS_ROW_LIMIT); "
            "results describe the first rows only"
        )
    if src.metric_query is not None:
        notes += [f"join warning: {w}" for w in compiled.warnings]
    return Input(
        frame=res.to_pandas(),
        sql=res.sql or sql,
        row_count=res.row_count,
        truncated=bool(res.truncated),
        tables=_referenced(sql),
        metric_versions=metric_versions,
        filter_context=chips,
        notes=notes,
    )


def _window(src: TimeSeriesSource) -> Any:
    from analystos_engine.types import TimeWindow

    return TimeWindow(dimension=src.time_dimension, start=src.start, end=src.end, grain=src.grain)


def _series_input(built: BuiltModel, src: TimeSeriesSource) -> list[dict[str, Any]]:
    _require_metric(built, src.metric_id)
    if src.time_dimension and not built.model.has_dimension(src.time_dimension):
        raise NotFound(f"Dimension {src.time_dimension!r} not found")
    for f in src.filters:
        if not built.model.has_dimension(f.dimension):
            raise NotFound(f"Dimension {f.dimension!r} not found")
    chips = filter_context(window=_window(src), filters=[f.model_dump(mode="json") for f in src.filters])
    return chips + metric_filter_chips(built.model, [src.metric_id])


def _incomplete_period_note(src: TimeSeriesSource, cal: Any) -> list[str]:
    from analystos_engine.calendar import periods_between

    periods = periods_between(src.start, src.end, src.grain, cal)
    notes = []
    if periods and periods[0] < src.start:
        notes.append(f"the first {src.grain} starts before {src.start.isoformat()}, so it is incomplete")
    if periods:
        nxt = periods_between(src.end, src.end + dt.timedelta(days=400), src.grain, cal)
        if nxt and nxt[0] < src.end:
            notes.append(
                f"the last {src.grain} ends after {src.end.isoformat()} (window end is exclusive), "
                "so it is incomplete and will look low"
            )
    return notes


# ------------------------------------------------------------------------------ persistence


def persist(
    db: Session,
    ws: Workspace,
    *,
    user_id: str | None,
    kind: str,
    method: str,
    title: str,
    label: str,
    exploratory: bool,
    summary: str,
    result: Any,
    table: dict[str, Any] | None,
    params: dict[str, Any],
    sql: str | None,
    inp_row_count: int | None,
    inp_truncated: bool,
    metric_versions: dict[str, str],
    tables: list[str],
    chips: list[dict[str, Any]],
    assumptions: list[dict[str, Any]] | None = None,
    caveats: list[str] | None = None,
    notes: list[str] | None = None,
    state: AppState,
    extra_sql: list[dict[str, str]] | None = None,
) -> AnalysisOut:
    versions = dataset_versions_for_tables(db, state, ws.id, sorted(set(tables)))
    dataset_versions = [{"table": t, **v} for t, v in versions.items()]
    meta = {
        "kind": kind,
        "method": method,
        "label": label,
        "exploratory": exploratory,
        "summary": summary,
        "assumptions": assumptions or [],
        "caveats": caveats or [],
        "notes": notes or [],
        "input_row_count": inp_row_count,
        "input_truncated": inp_truncated,
        "queries": extra_sql or [],
    }
    rec = ArtifactRecord(
        workspace_id=ws.id,
        investigation_id=None,
        run_id=None,
        engine_artifact_id=None,
        kind=kind,
        title=title[:400],
        sql=sql,
        python=None,
        params=to_json(params),
        filters=[],
        filter_context=chips,
        metric_versions=metric_versions,
        dataset_versions=dataset_versions,
        result=table,
        chart_spec=None,
        validation={
            "ok": True,
            "checks": [
                {
                    "name": "input rows",
                    "passed": bool(inp_row_count),
                    "detail": f"{inp_row_count or 0} input rows",
                }
            ],
        },
        parent_ids=[],
        document={
            "data": to_json(result),
            "analysis": meta,
            "warnings": [n for n in notes or [] if "capped" in n or "incomplete" in n],
        },
        origin="analysis",
        created_by=user_id,
    )
    db.add(rec)
    db.flush()
    return analysis_out(rec)


def analysis_out(rec: ArtifactRecord) -> AnalysisOut:
    doc = rec.document or {}
    meta = doc.get("analysis") or {}
    return AnalysisOut(
        id=rec.id,
        kind=cast(AnalysisKind, rec.kind),
        method=meta.get("method", rec.kind),
        title=rec.title,
        label=meta.get("label", "Descriptive"),
        exploratory=bool(meta.get("exploratory")),
        summary=meta.get("summary", ""),
        assumptions=meta.get("assumptions", []),
        caveats=meta.get("caveats", []),
        notes=meta.get("notes", []),
        params=rec.params or {},
        sql=rec.sql,
        input_row_count=meta.get("input_row_count"),
        input_truncated=bool(meta.get("input_truncated")),
        metric_versions=rec.metric_versions or {},
        dataset_versions=rec.dataset_versions or [],
        filter_context=rec.filter_context or [],
        result=doc.get("data") or {},
        table=TabularResult.model_validate(rec.result) if rec.result else None,
        created_at=rec.created_at,
    )


def _table(columns: list[tuple[str, str]], rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "columns": [{"name": n, "type": t} for n, t in columns],
        "rows": to_json(rows),
        "row_count": len(rows),
        "truncated": False,
        "elapsed_ms": 0.0,
        "sql": None,
    }


def _event(state: AppState, ws: Workspace, user_id: str | None, name: str, detail: dict[str, Any]) -> Any:
    return timed_event(
        state.session_factory,
        category="python",
        name=f"analysis:{name}",
        workspace_id=ws.id,
        user_id=user_id,
        detail=detail,
    )


def _engine_call(fn: Any) -> Any:
    try:
        return fn()
    except (ApiError, NotFound):
        raise
    except (ValueError, KeyError, ZeroDivisionError, ArithmeticError) as exc:
        raise AnalysisFailed(str(exc).strip() or type(exc).__name__) from exc


# ------------------------------------------------------------------------------ forecast


def run_forecast(
    db: Session, state: AppState, ws: Workspace, body: ForecastRequest, user_id: str | None
) -> AnalysisOut:
    from analystos_engine.analysis.forecast import MODELS, forecast_metric

    built = build_model(db, ws)
    chips = _series_input(built, body)
    kwargs: dict[str, Any] = {
        "models": tuple(body.models or MODELS),
        "interval": body.interval,
        "backtest_folds": body.backtest_folds,
        "seasonal_period": body.seasonal_period if body.seasonal_period else "auto",
    }
    with _event(state, ws, user_id, "forecast", {"metric": body.metric_id, "horizon": body.horizon}):
        res = _compile_errors(
            lambda: _engine_call(
                lambda: forecast_metric(
                    state.stores.get(ws.id),
                    built.model,
                    body.metric_id,
                    _window(body),
                    body.grain,
                    body.horizon,
                    body.filters,
                    **kwargs,
                )
            )
        )
    metric = built.model.get_metric(body.metric_id)
    notes = [*res.notes, *_incomplete_period_note(body, built.model.calendar)]
    fitted = [m for m in res.models if not m.error and m.points]
    if not fitted:
        raise AnalysisFailed(
            "no model could be fitted: " + "; ".join(f"{m.name}: {m.error}" for m in res.models)
        )
    best = res.model(res.best_model) if res.best_model else fitted[0]
    first = best.points[0]
    bt = best.backtest
    summary = f"{best.name} has the lowest backtest error" if res.best_model else f"{best.name} is shown"
    if bt and bt.mae is not None:
        summary += f" (MAE {fmt(bt.mae)}" + (f", MAPE {bt.mape:.1%}" if bt.mape is not None else "") + ")"
    summary += (
        f"; its forecast of {metric.display_name} for {first.timestamp} is {fmt(first.mean)} "
        f"({body.interval:.0%} interval {fmt(first.lower)} to {fmt(first.upper)})."
    )
    assumptions: list[dict[str, Any]] = [
        {
            "name": "the future resembles the history",
            "passed": None,
            "detail": "statistical models extrapolate past level, trend and seasonality; they cannot anticipate "
            "new events, price changes or structural breaks",
        },
        {
            "name": "enough history",
            "passed": len(res.history) >= 2 * (res.seasonal_period or 6),
            "detail": f"{len(res.history)} {body.grain}s of history"
            + (
                f"; seasonal period {res.seasonal_period}"
                if res.seasonal_period
                else "; no seasonal period (too short or not inferred)"
            ),
        },
    ]
    if bt and bt.coverage is not None:
        assumptions.append(
            {
                "name": "interval calibration",
                "passed": abs(bt.coverage - body.interval) <= 0.2,
                "detail": f"backtest coverage {bt.coverage:.0%} for a nominal {body.interval:.0%} interval",
            }
        )
    cols = [("period", "DATE")]
    for m in fitted:
        cols += [(f"{m.name}_mean", "DOUBLE"), (f"{m.name}_lower", "DOUBLE"), (f"{m.name}_upper", "DOUBLE")]
    rows = []
    for i in range(body.horizon):
        row: list[Any] = [fitted[0].points[i].timestamp if i < len(fitted[0].points) else None]
        for m in fitted:
            p = m.points[i] if i < len(m.points) else None
            row += [p.mean, p.lower, p.upper] if p else [None, None, None]
        rows.append(row)
    params = body.model_dump(mode="json")
    return persist(
        db,
        ws,
        user_id=user_id,
        kind="forecast",
        method=f"best of {', '.join(kwargs['models'])}",
        title=f"Forecast of {metric.display_name}, next {body.horizon} {body.grain}s",
        label="Model estimate",
        exploratory=False,
        summary=summary,
        result=res,
        table=_table(cols, rows),
        params=params,
        sql=res.sql,
        inp_row_count=len(res.history),
        inp_truncated=False,
        metric_versions=dict(res.metric_versions),
        tables=_referenced(res.sql or ""),
        chips=chips,
        assumptions=assumptions,
        caveats=[
            "A forecast is an estimate, not a measurement; judge it by the backtest error and "
            "the interval width, not the point value alone."
        ],
        notes=notes,
        state=state,
    )


# ------------------------------------------------------------------------------ anomalies


def run_anomalies(
    db: Session, state: AppState, ws: Workspace, body: AnomalyRequest, user_id: str | None
) -> AnalysisOut:
    from analystos_engine.analysis.anomaly import detect_metric_anomalies

    built = build_model(db, ws)
    chips = _series_input(built, body)
    with _event(state, ws, user_id, "anomalies", {"metric": body.metric_id, "sensitivity": body.sensitivity}):
        res = _compile_errors(
            lambda: _engine_call(
                lambda: detect_metric_anomalies(
                    state.stores.get(ws.id),
                    built.model,
                    body.metric_id,
                    _window(body),
                    body.grain,
                    body.filters,
                    sensitivity=body.sensitivity,
                    min_relative_deviation=body.min_relative_deviation,
                    change_points=body.change_points,
                    seasonal_period=body.seasonal_period if body.seasonal_period else "auto",
                )
            )
        )
    metric = built.model.get_metric(body.metric_id)
    n = len(res.anomalies)
    if n:
        top = max(res.anomalies, key=lambda p: abs(p.score or 0))
        summary = (
            f"{n} of {len(res.points)} {body.grain}s of {metric.display_name} are anomalous at "
            f"{res.sensitivity} sensitivity; the largest is {top.timestamp} ({fmt(top.value)} vs expected "
            f"{fmt(top.expected)})."
        )
    else:
        summary = (
            f"No {body.grain} of {metric.display_name} is anomalous at {res.sensitivity} sensitivity "
            f"({len(res.points)} {body.grain}s scanned)."
        )
    if res.change_points:
        cp = res.change_points[0]
        summary += f" Level shift detected at {cp.timestamp} ({fmt(cp.mean_before)} to {fmt(cp.mean_after)})."
    rows = [
        [p.timestamp, p.value, p.expected, p.lower, p.upper, p.score, p.is_anomaly, p.direction]
        for p in res.points
    ]
    cols = [
        ("period", "DATE"),
        ("value", "DOUBLE"),
        ("expected", "DOUBLE"),
        ("lower", "DOUBLE"),
        ("upper", "DOUBLE"),
        ("score", "DOUBLE"),
        ("is_anomaly", "BOOLEAN"),
        ("direction", "VARCHAR"),
    ]
    assumptions = [
        {"name": "baseline", "passed": None, "detail": res.method},
        {
            "name": "materiality",
            "passed": None,
            "detail": f"a point is flagged only if it deviates by at least {body.min_relative_deviation:.0%} from "
            "its expected value and exceeds the z threshold",
        },
    ]
    return persist(
        db,
        ws,
        user_id=user_id,
        kind="anomalies",
        method=res.method,
        title=f"Anomalies in {metric.display_name} by {body.grain}",
        label="Descriptive",
        exploratory=False,
        summary=summary,
        result=res,
        table=_table(cols, rows),
        params=body.model_dump(mode="json"),
        sql=res.sql,
        inp_row_count=len(res.points),
        inp_truncated=False,
        metric_versions=dict(res.metric_versions),
        tables=_referenced(res.sql or ""),
        chips=chips,
        assumptions=assumptions,
        caveats=["An anomaly is an unusual value, not an explanation; investigate it to find drivers."],
        notes=[*res.notes, *_incomplete_period_note(body, built.model.calendar)],
        state=state,
    )


# ------------------------------------------------------------------------------ segments


def run_segments(
    db: Session, state: AppState, ws: Workspace, body: SegmentRequest, user_id: str | None
) -> AnalysisOut:
    if body.method == "rfm":
        return _rfm(db, state, ws, body, user_id)
    if body.method == "product_quadrants":
        return _quadrants(db, state, ws, body, user_id)
    return _kmeans(db, state, ws, body, user_id)


def _rfm_from_source(
    db: Session, state: AppState, ws: Workspace, body: SegmentRequest, as_of: dt.date
) -> tuple[Any, Input]:
    """RFM over any tabular source (e.g. orders joined to order lines): transactions are rolled up per customer
    (last date, distinct orders or rows, summed amount) up to ``as_of``, then scored by the engine."""
    import pandas as pd
    from analystos_engine.analysis.segmentation import rfm_segments

    assert body.source is not None
    inp = load_tabular(db, state, ws, body.source)
    f = inp.frame
    cols = [c for c in (body.customer_col, body.date_col, body.amount_col, body.order_col) if c]
    missing = [c for c in cols if c not in f.columns]
    if missing:
        raise AnalysisFailed(f"missing columns: {missing}; available: {list(f.columns)}")
    f = f.assign(
        _d=pd.to_datetime(f[body.date_col], errors="coerce"),
        _a=pd.to_numeric(f[body.amount_col], errors="coerce"),
    )
    f = f[f["_d"].notna() & f[body.customer_col].notna() & (f["_d"] <= pd.Timestamp(as_of))]
    if f.empty:
        raise AnalysisFailed(f"no transactions on or before {as_of.isoformat()}")
    g = f.groupby(body.customer_col, sort=True)
    rolled = pd.DataFrame(
        {
            "customer": list(g.groups.keys()),
            "last_date": g["_d"].max().dt.date.to_numpy(),
            "frequency": (g[body.order_col].nunique() if body.order_col else g.size()).to_numpy(),
            "monetary": g["_a"].sum().to_numpy(),
        }
    )
    res = _engine_call(
        lambda: rfm_segments(
            rolled,
            id_col="customer",
            last_date_col="last_date",
            frequency_col="frequency",
            monetary_col="monetary",
            as_of=as_of,
            quantiles=body.quantiles,
        )
    )
    return res.model_copy(update={"sql": inp.sql}), inp


def _rfm(
    db: Session, state: AppState, ws: Workspace, body: SegmentRequest, user_id: str | None
) -> AnalysisOut:
    from analystos_engine.analysis.segmentation import rfm_from_table

    if body.source is not None:
        as_of = body.as_of or reference_date(ws)
        with _event(state, ws, user_id, "rfm", {"source": "tabular"}):
            res, inp = _rfm_from_source(db, state, ws, body, as_of)
        return _rfm_out(
            db,
            state,
            ws,
            body,
            user_id,
            res,
            as_of,
            label=f"{body.customer_col}",
            tables=inp.tables,
            sql=inp.sql,
            metric_versions=inp.metric_versions,
            extra_notes=inp.notes,
            truncated=inp.truncated,
        )

    table = body.table
    if body.entity:
        built = build_model(db, ws)
        try:
            table = built.model.get_entity(body.entity).table
        except KeyError as exc:
            raise NotFound(f"Entity {body.entity!r} not found") from exc
    store = state.stores.get(ws.id)
    if table not in {t.name for t in store.list_tables(include_row_counts=False)}:
        raise NotFound(f"Table {table!r} not found")
    cols = {c.name for c in store.describe(table, include_row_count=False).columns}
    missing = [
        c for c in (body.customer_col, body.date_col, body.amount_col, body.order_col) if c and c not in cols
    ]
    if missing:
        raise AnalysisFailed(f"missing columns in {table}: {missing}")
    as_of = body.as_of or reference_date(ws)
    with _event(state, ws, user_id, "rfm", {"table": table}):
        res = _engine_call(
            lambda: rfm_from_table(
                store,
                str(table),
                customer_col=str(body.customer_col),
                date_col=str(body.date_col),
                amount_col=str(body.amount_col),
                as_of=as_of,
                order_col=body.order_col,
                quantiles=body.quantiles,
            )
        )
    return _rfm_out(
        db,
        state,
        ws,
        body,
        user_id,
        res,
        as_of,
        label=f"{table}.{body.customer_col}",
        tables=[str(table)],
        sql=res.sql,
        metric_versions={},
        extra_notes=[],
        truncated=False,
        table=str(table),
    )


def _rfm_out(
    db: Session,
    state: AppState,
    ws: Workspace,
    body: SegmentRequest,
    user_id: str | None,
    res: Any,
    as_of: dt.date,
    *,
    label: str,
    tables: list[str],
    sql: str | None,
    metric_versions: dict[str, str],
    extra_notes: list[str],
    truncated: bool,
    table: str | None = None,
) -> AnalysisOut:
    total = len(res.customers)
    notes = [*res.notes, *extra_notes]
    result = to_json(res)
    if total > CUSTOMER_CAP:
        result["customers"] = result["customers"][:CUSTOMER_CAP]
        notes.append(f"{total:,} customers scored; the first {CUSTOMER_CAP:,} are listed")
    biggest = max(res.segments, key=lambda s: s.value_share) if res.segments else None
    summary = f"{total:,} customers scored on recency, frequency and monetary value as of {as_of.isoformat()}"
    if biggest:
        summary += (
            f"; '{biggest.segment}' holds {biggest.value_share:.0%} of the value with "
            f"{biggest.share:.0%} of customers."
        )
    rows = [[s.segment, s.count, s.share, s.value, s.value_share] for s in res.segments]
    tcols = [
        ("segment", "VARCHAR"),
        ("customers", "BIGINT"),
        ("share_of_customers", "DOUBLE"),
        ("value", "DOUBLE"),
        ("share_of_value", "DOUBLE"),
    ]
    params = {**body.model_dump(mode="json", exclude_none=True), "as_of": as_of.isoformat()}
    if table:
        params["table"] = table
    return persist(
        db,
        ws,
        user_id=user_id,
        kind="segments",
        method="rfm",
        title=f"RFM segments of {label}",
        label="Descriptive",
        exploratory=False,
        summary=summary,
        result=result,
        table=_table(tcols, rows),
        params=params,
        sql=sql,
        inp_row_count=total,
        inp_truncated=truncated,
        metric_versions=metric_versions,
        tables=tables,
        chips=[
            {
                "label": "As of",
                "value": as_of.isoformat(),
                "kind": "time",
                "dimension": body.date_col,
                "op": "lte",
                "values": [as_of.isoformat()],
            }
        ],
        assumptions=[
            {
                "name": "rule-based segments",
                "passed": None,
                "detail": "scores are quantiles within this data (1 = lowest); segment names come "
                "from fixed rules on the recency and frequency scores",
            }
        ],
        notes=notes,
        state=state,
    )


def _quadrants(
    db: Session, state: AppState, ws: Workspace, body: SegmentRequest, user_id: str | None
) -> AnalysisOut:
    from analystos_engine.analysis.segmentation import product_quadrants
    from analystos_engine.semantic.compiler import MetricQuery, run_metric_query
    from analystos_engine.types import TimeWindow

    built = build_model(db, ws)
    model = built.model
    assert (
        body.current is not None and body.dimension and body.growth_metric_id and body.profitability_metric_id
    )
    for m in (body.growth_metric_id, body.profitability_metric_id, body.volume_metric_id):
        if m:
            _require_metric(built, m)
    if not model.has_dimension(body.dimension):
        raise NotFound(f"Dimension {body.dimension!r} not found")
    cur = body.current
    base = body.baseline or type(cur)(start=cur.start - (cur.end - cur.start), end=cur.start)
    store = state.stores.get(ws.id)
    metrics = [m for m in (body.growth_metric_id, body.profitability_metric_id, body.volume_metric_id) if m]
    metrics = list(dict.fromkeys(metrics))

    def query(ms: list[str], w: Any) -> tuple[Any, Any]:
        q = MetricQuery(
            metrics=ms,
            dimensions=[str(body.dimension)],
            filters=body.filters,
            time=TimeWindow(dimension=body.time_dimension, start=w.start, end=w.end),
        )
        return _compile_errors(
            lambda: run_metric_query(
                store,
                model,
                q,
                limit=state.settings.analysis_row_limit,
                timeout_s=state.settings.query_timeout_s,
            )
        )

    with _event(state, ws, user_id, "product_quadrants", {"dimension": body.dimension}):
        c_cur, r_cur = query(metrics, cur)
        c_base, r_base = query([body.growth_metric_id], base)
    dim_col = c_cur.dimension_columns[0] if c_cur.dimension_columns else body.dimension
    base_vals = {str(r[dim_col]): r[body.growth_metric_id] for r in r_base.to_records()}
    rows = []
    for r in r_cur.to_records():
        key = str(r[dim_col])
        g_cur, g_base = r.get(body.growth_metric_id), base_vals.get(key)
        growth = (
            (float(g_cur) - float(g_base)) / abs(float(g_base))
            if g_cur is not None and g_base not in (None, 0)
            else None
        )
        rows.append(
            {
                "item": r[dim_col],
                "growth": growth,
                "profitability": r.get(body.profitability_metric_id),
                **({"volume": r.get(body.volume_metric_id)} if body.volume_metric_id else {}),
            }
        )
    import pandas as pd

    frame = pd.DataFrame(
        rows or [],
        columns=["item", "growth", "profitability", *(["volume"] if body.volume_metric_id else [])],
    )
    res = _engine_call(
        lambda: product_quadrants(
            frame,
            id_col="item",
            growth_col="growth",
            profitability_col="profitability",
            volume_col="volume" if body.volume_metric_id else None,
            growth_threshold=body.growth_threshold,
            profitability_threshold=body.profitability_threshold,
        )
    )
    g_label = model.get_metric(body.growth_metric_id).display_name
    p_label = model.get_metric(body.profitability_metric_id).display_name
    stars = next((s for s in res.segments if s.segment.startswith("Stars")), None)
    summary = (
        f"{len(res.rows)} {body.dimension} values classified by {g_label} growth "
        f"({base.start.isoformat()}..{base.end.isoformat()} to {cur.start.isoformat()}..{cur.end.isoformat()}) "
        f"and {p_label}"
    )
    summary += f"; {stars.count} are growing and above the profitability threshold." if stars else "."
    tcols = [
        ("item", "VARCHAR"),
        ("growth", "DOUBLE"),
        ("profitability", "DOUBLE"),
        ("volume", "DOUBLE"),
        ("quadrant", "VARCHAR"),
    ]
    trows = [[q.item, q.growth, q.profitability, q.volume, q.quadrant] for q in res.rows]
    chips = [
        *filter_context(
            window=TimeWindow(start=cur.start, end=cur.end),
            baseline=TimeWindow(start=base.start, end=base.end),
            filters=[f.model_dump(mode="json") for f in body.filters],
        ),
        *metric_filter_chips(model, metrics),
    ]
    versions = {**c_base.metric_versions, **c_cur.metric_versions}
    return persist(
        db,
        ws,
        user_id=user_id,
        kind="segments",
        method="product_quadrants",
        title=f"{body.dimension}: {g_label} growth vs {p_label}",
        label="Descriptive",
        exploratory=False,
        summary=summary,
        result=res,
        table=_table(tcols, trows),
        params={
            **body.model_dump(mode="json", exclude_none=True),
            "baseline": {"start": base.start.isoformat(), "end": base.end.isoformat()},
        },
        sql=c_cur.sql,
        inp_row_count=len(rows),
        inp_truncated=bool(r_cur.truncated),
        metric_versions=versions,
        tables=_referenced(c_cur.sql) + _referenced(c_base.sql),
        chips=chips,
        assumptions=[
            {"name": "thresholds", "passed": None, "detail": "; ".join(res.notes)},
            {
                "name": "growth needs a baseline",
                "passed": None,
                "detail": "items with no baseline value have no growth and are 'Insufficient data'",
            },
        ],
        notes=list(res.notes),
        state=state,
        extra_sql=[
            {"label": "current period", "sql": c_cur.sql},
            {"label": "baseline period", "sql": c_base.sql},
        ],
    )


def _kmeans(
    db: Session, state: AppState, ws: Workspace, body: SegmentRequest, user_id: str | None
) -> AnalysisOut:
    from analystos_engine.analysis.segmentation import kmeans_segments

    assert body.source is not None
    inp = load_tabular(db, state, ws, body.source)
    with _event(state, ws, user_id, "kmeans", {"features": body.features}):
        res = _engine_call(
            lambda: kmeans_segments(
                inp.frame, body.features, id_col=body.id_column, k=body.k, standardize=body.standardize
            )
        )
    summary = (
        f"{res.k} clusters over {', '.join(res.features)} from {len(res.labels):,} rows"
        + (f" (silhouette {res.silhouette:.2f})" if res.silhouette is not None else "")
        + "."
    )
    tcols = [("cluster", "BIGINT"), ("size", "BIGINT"), *[(f, "DOUBLE") for f in res.features]]
    trows = [[i, res.sizes[i], *[c.get(f) for f in res.features]] for i, c in enumerate(res.centroids)]
    sil_ok = res.silhouette is not None and res.silhouette >= 0.25
    return persist(
        db,
        ws,
        user_id=user_id,
        kind="segments",
        method="kmeans",
        title=f"k-means clusters on {', '.join(body.features)}",
        label="Exploratory",
        exploratory=True,
        summary=summary,
        result=res,
        table=_table(tcols, trows),
        params=body.model_dump(mode="json", exclude_none=True),
        sql=inp.sql,
        inp_row_count=inp.row_count,
        inp_truncated=inp.truncated,
        metric_versions=inp.metric_versions,
        tables=inp.tables,
        chips=inp.filter_context,
        assumptions=[
            {
                "name": "cluster separation",
                "passed": sil_ok,
                "detail": f"silhouette {res.silhouette:.2f}"
                if res.silhouette is not None
                else "silhouette unavailable",
            },
            {
                "name": "comparable feature scales",
                "passed": body.standardize or None,
                "detail": "features standardised before clustering"
                if body.standardize
                else "features used on their own scales; large-valued features dominate",
            },
        ],
        caveats=[
            "Exploratory: clusters describe structure in the chosen features, not causes or "
            "natural groups; prefer the rule-based segments for decisions."
        ],
        notes=[*inp.notes, *res.notes],
        state=state,
    )


# ------------------------------------------------------------------------------ statistical tests


def _col(frame: Any, name: str | None) -> Any:
    if name is None or name not in frame.columns:
        raise AnalysisFailed(f"missing column {name!r}; available: {list(frame.columns)}")
    return frame[name]


def _group_mask(frame: Any, column: str, value: Any) -> Any:
    col = _col(frame, column)
    mask = col.astype(str) == str(value)
    if not mask.any():
        raise AnalysisFailed(f"no rows where {column} = {value!r}")
    return mask


def _truthy(series: Any) -> Any:
    import pandas as pd

    if pd.api.types.is_bool_dtype(series):
        return series.astype(float)
    num = pd.to_numeric(series, errors="coerce")
    if num.notna().any():
        return num
    return series.astype(str).str.lower().isin({"true", "yes", "y", "1", "t"}).astype(float)


def run_stats_test(
    db: Session, state: AppState, ws: Workspace, body: StatsTestRequest, user_id: str | None
) -> AnalysisOut:
    import pandas as pd
    from analystos_engine.analysis import stats

    inp = load_tabular(db, state, ws, body.source)
    f = inp.frame
    t = body.test
    with _event(state, ws, user_id, f"stats:{t}", {"rows": inp.row_count}):
        if t == "t_test":
            vals = pd.to_numeric(_col(f, body.value_column), errors="coerce")
            a = vals[_group_mask(f, str(body.group_column), body.group_a)]
            b = vals[_group_mask(f, str(body.group_column), body.group_b)]
            res: Any = _engine_call(
                lambda: stats.t_test(
                    a.tolist(),
                    b.tolist(),
                    equal_var=body.equal_var,
                    alpha=body.alpha,
                    confidence=body.confidence,
                )
            )
            title = f"t-test of {body.value_column}: {body.group_a} vs {body.group_b}"
        elif t == "chi_square":
            rc, cc = _col(f, body.row_column), _col(f, body.column_column)
            if body.count_column:
                counts = pd.to_numeric(_col(f, body.count_column), errors="coerce").fillna(0)
                table = pd.crosstab(rc.astype(str), cc.astype(str), values=counts, aggfunc="sum").fillna(0)
            else:
                table = pd.crosstab(rc.astype(str), cc.astype(str))
            if table.shape[0] < 2 or table.shape[1] < 2:
                raise AnalysisFailed("a chi-square test needs at least two categories in each column")
            if table.size > 2500:
                raise AnalysisFailed(
                    f"contingency table too large ({table.shape[0]} x {table.shape[1]}); "
                    "group categories first"
                )
            res = _engine_call(lambda: stats.chi_square(table.to_numpy().tolist(), alpha=body.alpha))
            title = f"Chi-square test: {body.row_column} x {body.column_column}"
        elif t == "proportion":
            succ = _truthy(_col(f, body.success_column))
            trials = (
                pd.to_numeric(_col(f, body.trials_column), errors="coerce")
                if body.trials_column
                else pd.Series(1.0, index=f.index)
            )
            ma = _group_mask(f, str(body.group_column), body.group_a)
            mb = _group_mask(f, str(body.group_column), body.group_b)
            sa, na, sb, nb = (
                int(round(float(succ[ma].sum()))),
                int(round(float(trials[ma].sum()))),
                int(round(float(succ[mb].sum()))),
                int(round(float(trials[mb].sum()))),
            )
            res = _engine_call(
                lambda: stats.proportion_z_test(sa, na, sb, nb, alpha=body.alpha, confidence=body.confidence)
            )
            title = f"Proportion test of {body.success_column}: {body.group_a} vs {body.group_b}"
        elif t == "mean_ci":
            vals = pd.to_numeric(_col(f, body.value_column), errors="coerce").dropna().tolist()
            res = _engine_call(lambda: stats.mean_ci(vals, confidence=body.confidence))
            title = f"{body.confidence:.0%} confidence interval for the mean of {body.value_column}"
        elif t == "bootstrap_ci":
            vals = pd.to_numeric(_col(f, body.value_column), errors="coerce").dropna().tolist()
            res = _engine_call(lambda: stats.bootstrap_ci(vals, stat=body.stat, confidence=body.confidence))
            title = f"Bootstrap {body.confidence:.0%} interval for the {body.stat} of {body.value_column}"
        else:
            succ = _truthy(_col(f, body.success_column))
            n = (
                float(pd.to_numeric(_col(f, body.trials_column), errors="coerce").sum())
                if body.trials_column
                else float(len(succ))
            )
            res = _engine_call(
                lambda: stats.proportion_ci(
                    int(round(float(succ.sum()))), int(round(n)), confidence=body.confidence
                )
            )
            title = f"{body.confidence:.0%} confidence interval for the rate of {body.success_column}"
    if isinstance(res, stats.TestResult):
        summary = res.interpretation
        assumptions = [a.model_dump() for a in res.assumptions]
        caveats = list(res.caveats)
        trows = [
            [
                res.test,
                res.statistic,
                res.p_value,
                res.df,
                res.estimate,
                res.ci_low,
                res.ci_high,
                res.effect_size,
                res.significant,
            ]
        ]
        tcols = [
            ("test", "VARCHAR"),
            ("statistic", "DOUBLE"),
            ("p_value", "DOUBLE"),
            ("df", "DOUBLE"),
            ("estimate", "DOUBLE"),
            ("ci_low", "DOUBLE"),
            ("ci_high", "DOUBLE"),
            ("effect_size", "DOUBLE"),
            ("significant", "BOOLEAN"),
        ]
        method = res.test
    else:
        summary = (
            f"Estimate {fmt(res.estimate)}, {res.confidence:.0%} interval {fmt(res.low)} to "
            f"{fmt(res.high)} ({res.method}, n={res.n:,})."
        )
        assumptions = [
            {
                "name": "independent observations",
                "passed": None,
                "detail": "cannot be checked from data; repeated measures of the same unit narrow the "
                "interval falsely",
            },
            {
                "name": "sample size",
                "passed": res.n >= 30,
                "detail": f"n = {res.n}" + ("" if res.n >= 30 else "; the interval is approximate"),
            },
        ]
        caveats = ["An interval describes sampling uncertainty only, not measurement or definition errors."]
        trows = [[res.method, res.estimate, res.low, res.high, res.confidence, res.n]]
        tcols = [
            ("method", "VARCHAR"),
            ("estimate", "DOUBLE"),
            ("low", "DOUBLE"),
            ("high", "DOUBLE"),
            ("confidence", "DOUBLE"),
            ("n", "BIGINT"),
        ]
        method = res.method
    return persist(
        db,
        ws,
        user_id=user_id,
        kind="stats_test",
        method=method,
        title=title,
        label="Statistical test",
        exploratory=False,
        summary=summary,
        result=res,
        table=_table(tcols, trows),
        params=body.model_dump(mode="json", exclude_none=True),
        sql=inp.sql,
        inp_row_count=inp.row_count,
        inp_truncated=inp.truncated,
        metric_versions=inp.metric_versions,
        tables=inp.tables,
        chips=inp.filter_context,
        assumptions=assumptions,
        caveats=caveats,
        notes=inp.notes,
        state=state,
    )


# ------------------------------------------------------------------------------ correlation & regression


def run_correlation(
    db: Session, state: AppState, ws: Workspace, body: CorrelationRequest, user_id: str | None
) -> AnalysisOut:
    import pandas as pd
    from analystos_engine.analysis.correlation import correlation_matrix

    inp = load_tabular(db, state, ws, body.source)
    cols = body.columns or [c for c in inp.frame.columns if pd.api.types.is_numeric_dtype(inp.frame[c])][:30]
    if len(cols) < 2:
        raise AnalysisFailed("a correlation needs at least two numeric columns")
    with _event(state, ws, user_id, "correlation", {"columns": cols}):
        res = _engine_call(lambda: correlation_matrix(inp.frame, cols, method=body.method))
    top = next((p for p in res.pairs if p.r is not None), None)
    summary = (
        f"Strongest association: {top.a} and {top.b}, {body.method} r = {top.r:.2f} ({top.strength}, "
        f"n = {top.n:,})."
        if top
        else "No pair had enough varying values to correlate."
    )
    trows = [[p.a, p.b, p.r, p.p_value, p.n, p.strength] for p in res.pairs]
    tcols = [
        ("a", "VARCHAR"),
        ("b", "VARCHAR"),
        ("r", "DOUBLE"),
        ("p_value", "DOUBLE"),
        ("n", "BIGINT"),
        ("strength", "VARCHAR"),
    ]
    small = min((p.n for p in res.pairs), default=0)
    return persist(
        db,
        ws,
        user_id=user_id,
        kind="correlation",
        method=body.method,
        title=f"{body.method.title()} correlation of {len(cols)} columns",
        label="Exploratory",
        exploratory=True,
        summary=summary,
        result=res,
        table=_table(tcols, trows),
        params=body.model_dump(mode="json", exclude_none=True),
        sql=inp.sql,
        inp_row_count=inp.row_count,
        inp_truncated=inp.truncated,
        metric_versions=inp.metric_versions,
        tables=inp.tables,
        chips=inp.filter_context,
        assumptions=[
            {
                "name": "relationship shape",
                "passed": None,
                "detail": "Pearson measures linear association; Spearman measures monotonic "
                "association and is robust to outliers",
            },
            {"name": "sample size", "passed": small >= 30, "detail": f"smallest pair n = {small}"},
        ],
        caveats=[res.caveat],
        notes=inp.notes,
        state=state,
    )


def run_regression(
    db: Session, state: AppState, ws: Workspace, body: RegressionRequest, user_id: str | None
) -> AnalysisOut:
    from analystos_engine.analysis.correlation import ols_regression, permutation_importance

    inp = load_tabular(db, state, ws, body.source)
    with _event(state, ws, user_id, f"regression:{body.model}", {"target": body.target}):
        if body.model == "ols":
            res: Any = _engine_call(lambda: ols_regression(inp.frame, body.target, body.features))
        else:
            res = _engine_call(lambda: permutation_importance(inp.frame, body.target, body.features))
    if body.model == "ols":
        sig = [c.name for c in res.coefficients if c.name != "const" and c.p_value < 0.05]
        summary = (
            f"OLS of {body.target} on {', '.join(body.features)}: R² {res.r_squared:.2f} (adjusted "
            f"{res.adj_r_squared:.2f}, n = {res.n:,}); "
            + (f"coefficients with p < 0.05: {', '.join(sig)}." if sig else "no coefficient has p < 0.05.")
        )
        trows = [[c.name, c.coef, c.std_err, c.t, c.p_value, c.ci_low, c.ci_high] for c in res.coefficients]
        tcols = [
            ("term", "VARCHAR"),
            ("coef", "DOUBLE"),
            ("std_err", "DOUBLE"),
            ("t", "DOUBLE"),
            ("p_value", "DOUBLE"),
            ("ci_low", "DOUBLE"),
            ("ci_high", "DOUBLE"),
        ]
        assumptions = [
            {
                "name": "no strong collinearity",
                "passed": not any(v > 10 for v in res.vif.values()),
                "detail": ", ".join(f"{k} VIF {v:.3g}" for k, v in res.vif.items()) or "one feature",
            },
            {
                "name": "enough rows",
                "passed": res.n >= 10 * (len(body.features) + 1),
                "detail": f"{res.n} rows for {len(body.features)} features",
            },
            {
                "name": "linear, additive relationship",
                "passed": None,
                "detail": "OLS assumes the target changes linearly with each feature",
            },
        ]
        caveats, notes, method = [res.caveat, *res.warnings], list(inp.notes), "ols"
    else:
        top = res.features[0] if res.features else None
        summary = (
            f"Random-forest permutation importance for {body.target} (held-out {res.score_name} "
            f"{res.holdout_score:.2f}, n = {res.n:,})" + (f"; most important: {top.name}." if top else ".")
        )
        trows = [[x.name, x.importance_mean, x.importance_std] for x in res.features]
        tcols = [("feature", "VARCHAR"), ("importance_mean", "DOUBLE"), ("importance_std", "DOUBLE")]
        assumptions = [
            {
                "name": "the model predicts the target",
                "passed": res.holdout_score >= 0.1,
                "detail": f"held-out {res.score_name} {res.holdout_score:.2f}",
            }
        ]
        caveats, notes, method = [res.caveat], [*inp.notes, *res.notes], "permutation_importance"
    return persist(
        db,
        ws,
        user_id=user_id,
        kind="regression",
        method=method,
        title=f"{'OLS regression' if body.model == 'ols' else 'Feature importance'} for {body.target}",
        label="Exploratory",
        exploratory=True,
        summary=summary,
        result=res,
        table=_table(tcols, trows),
        params=body.model_dump(mode="json", exclude_none=True),
        sql=inp.sql,
        inp_row_count=inp.row_count,
        inp_truncated=inp.truncated,
        metric_versions=inp.metric_versions,
        tables=inp.tables,
        chips=inp.filter_context,
        assumptions=assumptions,
        caveats=caveats,
        notes=notes,
        state=state,
    )
