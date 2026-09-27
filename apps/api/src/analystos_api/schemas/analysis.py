"""Request/response models for the advanced-analysis endpoints (spec §39-44): forecasting, anomalies,
segmentation, statistical tests, correlation and regression."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from analystos_engine.semantic.compiler import MetricQuery
from analystos_engine.semantic.models import Filter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import TabularResult

AnalysisKind = Literal["forecast", "anomalies", "segments", "stats_test", "correlation", "regression"]
Grain = Literal["day", "week", "month", "quarter"]


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeSeriesSource(_Req):
    """A semantic metric per period over ``[start, end)`` (end exclusive)."""

    metric_id: str
    start: dt.date
    end: dt.date
    grain: Grain = "month"
    time_dimension: str | None = Field(default=None, description="Defaults to the metric's time dimension")
    filters: list[Filter] = Field(default_factory=list)

    @model_validator(mode="after")
    def _window(self) -> TimeSeriesSource:
        if self.end <= self.start:
            raise ValueError("end must be after start (the window is [start, end))")
        return self


class TabularSource(_Req):
    """Exactly one of: read-only SQL, a saved query, a semantic metric query or a workspace table."""

    sql: str | None = Field(default=None, max_length=200_000)
    params: dict[str, Any] = Field(default_factory=dict)
    saved_query_id: str | None = None
    metric_query: MetricQuery | None = None
    table: str | None = None

    @model_validator(mode="after")
    def _one(self) -> TabularSource:
        given = [
            k for k in ("sql", "saved_query_id", "metric_query", "table") if getattr(self, k) is not None
        ]
        if len(given) != 1:
            raise ValueError("give exactly one of sql, saved_query_id, metric_query or table")
        return self


class ForecastRequest(TimeSeriesSource):
    horizon: int = Field(default=6, ge=1, le=120)
    models: list[Literal["naive", "seasonal_naive", "ets", "arima"]] | None = None
    interval: float = Field(default=0.8, ge=0.5, le=0.99)
    seasonal_period: int | None = Field(
        default=None, ge=2, le=366, description="Default: inferred from the grain"
    )
    backtest_folds: int = Field(default=3, ge=1, le=10)


class AnomalyRequest(TimeSeriesSource):
    sensitivity: Literal["low", "medium", "high"] | float = "medium"
    seasonal_period: int | None = Field(default=None, ge=2, le=366)
    min_relative_deviation: float = Field(default=0.05, ge=0, le=1)
    change_points: bool = True

    @model_validator(mode="after")
    def _z(self) -> AnomalyRequest:
        if isinstance(self.sensitivity, float) and not 1.5 <= self.sensitivity <= 10:
            raise ValueError("a numeric sensitivity is a z threshold between 1.5 and 10")
        return self


class AnomalyInvestigateRequest(_Req):
    metric_id: str
    date: dt.date
    grain: Literal["day", "month"] = "day"
    filters: list[Filter] = Field(default_factory=list)
    baseline: Literal["same_weekday_last_week", "previous_day"] = "same_weekday_last_week"


class PeriodIn(_Req):
    start: dt.date
    end: dt.date

    @model_validator(mode="after")
    def _order(self) -> PeriodIn:
        if self.end <= self.start:
            raise ValueError("end must be after start (the window is [start, end))")
        return self


class SegmentRequest(_Req):
    method: Literal["rfm", "product_quadrants", "kmeans"]
    # rfm
    table: str | None = None
    entity: str | None = None
    customer_col: str | None = None
    date_col: str | None = None
    amount_col: str | None = None
    order_col: str | None = None
    as_of: dt.date | None = None
    quantiles: int = Field(default=5, ge=2, le=10)
    # product_quadrants
    dimension: str | None = None
    growth_metric_id: str | None = None
    profitability_metric_id: str | None = None
    volume_metric_id: str | None = None
    current: PeriodIn | None = None
    baseline: PeriodIn | None = None
    time_dimension: str | None = None
    filters: list[Filter] = Field(default_factory=list)
    growth_threshold: float | None = None
    profitability_threshold: float | None = None
    # kmeans
    source: TabularSource | None = None
    features: list[str] = Field(default_factory=list, max_length=30)
    id_column: str | None = None
    k: int | None = Field(default=None, ge=2, le=10)
    standardize: bool = True

    @model_validator(mode="after")
    def _required(self) -> SegmentRequest:
        need = {
            "rfm": ["customer_col", "date_col", "amount_col"],
            "product_quadrants": ["dimension", "growth_metric_id", "profitability_metric_id", "current"],
            "kmeans": ["source"],
        }[self.method]
        missing = [f for f in need if getattr(self, f) in (None, "")]
        if self.method == "rfm" and not (self.table or self.entity or self.source):
            missing.append("table, entity or source")
        if self.method == "kmeans" and len(self.features) < 1:
            missing.append("features")
        if missing:
            raise ValueError(f"method {self.method} needs: {', '.join(missing)}")
        return self


class StatsTestRequest(_Req):
    test: Literal["t_test", "chi_square", "proportion", "mean_ci", "bootstrap_ci", "proportion_ci"]
    source: TabularSource
    alpha: float = Field(default=0.05, ge=0.001, le=0.2)
    confidence: float = Field(default=0.95, ge=0.5, le=0.999)
    value_column: str | None = None
    group_column: str | None = None
    group_a: Any = None
    group_b: Any = None
    equal_var: bool = False
    row_column: str | None = None
    column_column: str | None = None
    count_column: str | None = None
    success_column: str | None = None
    trials_column: str | None = None
    stat: Literal["mean", "median", "sum"] = "mean"

    @model_validator(mode="after")
    def _required(self) -> StatsTestRequest:
        need = {
            "t_test": ["value_column", "group_column", "group_a", "group_b"],
            "chi_square": ["row_column", "column_column"],
            "proportion": ["group_column", "group_a", "group_b", "success_column"],
            "mean_ci": ["value_column"],
            "bootstrap_ci": ["value_column"],
            "proportion_ci": ["success_column"],
        }[self.test]
        missing = [f for f in need if getattr(self, f) in (None, "")]
        if missing:
            raise ValueError(f"test {self.test} needs: {', '.join(missing)}")
        return self


class CorrelationRequest(_Req):
    source: TabularSource
    columns: list[str] | None = Field(default=None, max_length=30)
    method: Literal["pearson", "spearman"] = "pearson"


class RegressionRequest(_Req):
    source: TabularSource
    target: str
    features: list[str] = Field(min_length=1, max_length=20)
    model: Literal["ols", "importance"] = "ols"


class AssumptionOut(BaseModel):
    name: str
    passed: bool | None = None
    detail: str = ""


class AnalysisOut(BaseModel):
    id: str = Field(description="Artifact id (GET /artifacts/{id}, /artifacts/{id}/lineage)")
    kind: AnalysisKind
    method: str
    title: str
    label: Literal["Exploratory", "Descriptive", "Model estimate", "Statistical test"]
    exploratory: bool
    summary: str
    assumptions: list[AssumptionOut] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    sql: str | None = None
    input_row_count: int | None = None
    input_truncated: bool = False
    metric_versions: dict[str, Any] = Field(default_factory=dict)
    dataset_versions: list[dict[str, Any]] = Field(default_factory=list)
    filter_context: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] = Field(description="The engine result model (see API.md per endpoint)")
    table: TabularResult | None = None
    created_at: dt.datetime
