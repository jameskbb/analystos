from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from analystos_engine.semantic.models import (
    CalendarConfig,
    Dimension,
    DriverEdge,
    Entity,
    GlossaryTerm,
    Metric,
    MetricTree,
    ModelIssue,
    SemanticModel,
)
from analystos_engine.types import TimeWindow
from pydantic import BaseModel, ConfigDict, Field

from .common import ApiModel, TabularResult

__all__ = [
    "CalendarConfig",
    "Dimension",
    "DriverEdge",
    "Entity",
    "GlossaryTerm",
    "Metric",
    "MetricTree",
    "ModelIssue",
    "SemanticModel",
    "TimeWindow",
]


class SemanticModelOut(BaseModel):
    model: SemanticModel
    content_hash: str
    issues: list[ModelIssue]
    snapshot_id: str
    snapshot_version: int
    metric_versions: dict[str, dict[str, Any]] = Field(
        description="metric id -> {version_id, version_no, engine_version_id}"
    )


class SemanticImport(BaseModel):
    yaml: str = Field(description="Semantic model YAML (engine SemanticModel.to_yaml format)")
    change_note: str = "Imported from YAML"


class SemanticImportResult(BaseModel):
    counts: dict[str, int]
    issues: list[ModelIssue]
    snapshot_id: str


class SnapshotOut(ApiModel):
    id: str
    version_no: int
    content_hash: str
    reason: str
    metric_version_ids: dict[str, Any]
    created_by: str | None
    created_at: datetime


class SnapshotDetail(SnapshotOut):
    model: dict[str, Any]


class MetricOut(Metric):
    """Metric definition fields (flattened) plus version metadata. ``version`` equals ``version_no``."""

    record_id: str
    version_no: int
    current_version_id: str = Field(description="Id of the immutable metric version shown (the current one)")
    engine_version_id: str = Field(description="{id}@v{version}:{definition hash}")
    archived: bool
    change_note: str = ""
    created_at: datetime
    updated_at: datetime


class MetricCreate(Metric):
    change_note: str = "Created"

    def to_metric(self) -> Metric:
        return Metric.model_validate(self.model_dump(exclude={"change_note"}))


class MetricUpdate(BaseModel):
    """Partial or full metric definition; omitted fields keep their current values."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    label: str | None = None
    description: str | None = None
    kind: Literal["simple", "ratio", "derived"] | None = None
    expr: str | None = None
    entity: str | None = None
    agg: Literal["sum", "count", "count_distinct", "avg", "min", "max"] | None = None
    numerator: str | None = None
    denominator: str | None = None
    formula: str | None = None
    filters: list[dict[str, Any]] | None = None
    format: Literal["currency", "number", "percent", "integer"] | None = None
    owner: str | None = None
    tags: list[str] | None = None
    canonical: bool | None = None
    synonyms: list[str] | None = None
    default_time_dimension: str | None = None
    higher_is_better: bool | None = None
    time_aggregation: Literal["sum", "last", "first", "avg"] | None = Field(
        default=None, description="How values combine over time: sum for flows, last/first/avg for balances"
    )
    change_note: str = Field(default="", max_length=2000)


class MetricVersionOut(BaseModel):
    version_id: str
    version_no: int
    definition: dict[str, Any]
    definition_hash: str
    change_note: str
    created_by: str | None
    created_at: datetime
    is_current: bool


class MetricValuePoint(BaseModel):
    period: Any
    value: float | None


class MetricExamples(BaseModel):
    metric_id: str
    version_no: int
    time_dimension: str | None
    dimension: str | None = None
    grain: str
    points: list[MetricValuePoint]
    compiled: dict[str, Any] = Field(
        description="Engine CompiledQuery (sql, joins, grain, warnings, versions)"
    )
    result: TabularResult


class DimensionValues(BaseModel):
    dimension: str
    values: list[Any]
    counts: list[int]


class TreeSuggestRequest(BaseModel):
    root_metric: str


class MetricDiff(BaseModel):
    metric_id: str
    from_version: int
    to_version: int
    changes: list[dict[str, Any]]


class EntityOut(BaseModel):
    id: str
    spec: Entity
    updated_at: datetime


class DimensionOut(BaseModel):
    id: str
    spec: Dimension
    updated_at: datetime


class MetricTreeOut(BaseModel):
    id: str
    version_no: int
    spec: MetricTree
    updated_at: datetime


class TreeSuggestion(BaseModel):
    root_metric: str
    edges: list[DriverEdge]
    rationale: list[str]


class EdgeDecision(BaseModel):
    parent: str
    child: str
    decision: Literal["approve", "reject"]


class GlossaryOut(BaseModel):
    id: str
    spec: GlossaryTerm
    updated_at: datetime


class PeriodResolution(BaseModel):
    input: str
    window: TimeWindow
    previous_period: TimeWindow
    same_period_last_year: TimeWindow


class MetricInvestigationRef(BaseModel):
    id: str
    title: str
    status: str
    created_at: datetime
    metric_version: dict[str, Any] = Field(
        description="{version_id, version_no, engine_version_id} used by the run"
    )
