"""Advanced analysis: forecasting, anomalies, segmentation, statistical tests, correlation and regression
(spec §39-44). Every call persists an artifact with SQL, parameters, versions and lineage."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..deps import READ_ONLY_POST, DbDep, EditorCtx, StateDep, ViewerCtx
from ..errors import NotFound, Unprocessable
from ..models import ArtifactRecord
from ..schemas.analysis import (
    AnalysisKind,
    AnalysisOut,
    AnomalyInvestigateRequest,
    AnomalyRequest,
    CorrelationRequest,
    ForecastRequest,
    RegressionRequest,
    SegmentRequest,
    StatsTestRequest,
)
from ..schemas.common import ERROR_RESPONSES
from ..schemas.investigations import InvestigationOut
from ..services import analysis as svc
from ..services.observability import audit

router = APIRouter(prefix="/workspaces/{workspace_id}/analysis", tags=["analysis"], responses=ERROR_RESPONSES)


@router.post(
    "/forecast", response_model=AnalysisOut, operation_id="forecastMetric", openapi_extra=READ_ONLY_POST
)
def forecast(body: ForecastRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> AnalysisOut:
    """Forecast a metric with naive, seasonal naive, ETS and ARIMA models, each with a rolling-origin backtest
    (MAE, MAPE, interval coverage). The best model is the one with the lowest backtest MAE (spec §41)."""
    return svc.run_forecast(db, state, ctx.workspace, body, ctx.user_id)


@router.post(
    "/anomalies", response_model=AnalysisOut, operation_id="detectAnomalies", openapi_extra=READ_ONLY_POST
)
def anomalies(body: AnomalyRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> AnalysisOut:
    """Flag anomalous periods (robust z-score against a seasonal or rolling median baseline) and level shifts,
    with configurable sensitivity (spec §39)."""
    return svc.run_anomalies(db, state, ctx.workspace, body, ctx.user_id)


@router.post(
    "/anomalies/investigate",
    response_model=InvestigationOut,
    status_code=201,
    operation_id="investigateAnomaly",
)
def investigate_anomaly(
    body: AnomalyInvestigateRequest, ctx: EditorCtx, db: DbDep, state: StateDep
) -> InvestigationOut:
    """Open an investigation of an anomalous day (vs the same weekday a week earlier, or the previous day) or
    month (vs the previous month), broken down by the metric's dimensions (spec §40)."""
    from ..services.investigations import investigate_anomaly as start
    from .investigations import investigation_out

    inv = start(db, state, ctx.workspace, body, ctx.user_id)
    audit(
        db,
        action="investigation.create",
        resource_type="investigation",
        resource_id=inv.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"question": inv.question, "origin": "anomaly", "date": body.date.isoformat()},
    )
    return investigation_out(db, inv)


@router.post(
    "/segments", response_model=AnalysisOut, operation_id="segmentAnalysis", openapi_extra=READ_ONLY_POST
)
def segments(body: SegmentRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> AnalysisOut:
    """Rule-based customer RFM segments, product growth/profitability quadrants from semantic metrics, or
    exploratory k-means clusters (spec §42)."""
    return svc.run_segments(db, state, ctx.workspace, body, ctx.user_id)


@router.post(
    "/stats-test", response_model=AnalysisOut, operation_id="statisticalTest", openapi_extra=READ_ONLY_POST
)
def stats_test(body: StatsTestRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> AnalysisOut:
    """t-test, chi-square, two-proportion z-test or confidence intervals, with assumption checks (spec §44)."""
    return svc.run_stats_test(db, state, ctx.workspace, body, ctx.user_id)


@router.post(
    "/correlation",
    response_model=AnalysisOut,
    operation_id="correlationAnalysis",
    openapi_extra=READ_ONLY_POST,
)
def correlation(body: CorrelationRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> AnalysisOut:
    """Pearson or Spearman correlation matrix, labelled exploratory: correlation is not causation (spec §43)."""
    return svc.run_correlation(db, state, ctx.workspace, body, ctx.user_id)


@router.post(
    "/regression", response_model=AnalysisOut, operation_id="regressionAnalysis", openapi_extra=READ_ONLY_POST
)
def regression(body: RegressionRequest, ctx: ViewerCtx, db: DbDep, state: StateDep) -> AnalysisOut:
    """OLS regression (coefficients, intervals, VIF) or random-forest permutation importance, exploratory."""
    return svc.run_regression(db, state, ctx.workspace, body, ctx.user_id)


@router.get("", response_model=list[AnalysisOut], operation_id="listAnalyses")
def list_analyses(
    ctx: ViewerCtx,
    db: DbDep,
    kind: AnalysisKind | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AnalysisOut]:
    stmt = select(ArtifactRecord).where(
        ArtifactRecord.workspace_id == ctx.workspace_id, ArtifactRecord.origin == "analysis"
    )
    if kind:
        stmt = stmt.where(ArtifactRecord.kind == kind)
    rows = db.scalars(stmt.order_by(ArtifactRecord.created_at.desc()).limit(limit)).all()
    return [svc.analysis_out(r) for r in rows]


@router.get("/{artifact_id}", response_model=AnalysisOut, operation_id="getAnalysis")
def get_analysis(artifact_id: str, ctx: ViewerCtx, db: DbDep) -> AnalysisOut:
    rec = db.get(ArtifactRecord, artifact_id)
    if rec is None or rec.workspace_id != ctx.workspace_id:
        raise NotFound("Analysis not found")
    if rec.origin != "analysis":
        raise Unprocessable("This artifact is not an analysis; use /artifacts/{id}", code="not_an_analysis")
    return svc.analysis_out(rec)
