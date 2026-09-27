from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from analystos_investigator import (
    AnalysisPlan,
    Hypothesis,
    Interpretation,
    InvestigationDiff,
    InvestigationTree,
    Summary,
    TreeNode,
)
from pydantic import BaseModel, Field

from .common import ApiModel, TabularResult

InvestigationStatus = Literal[
    "needs_disambiguation", "awaiting_approval", "ready", "running", "completed", "failed"
]


class InvestigationCreate(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    template: str | None = Field(
        default=None, description="Investigation template id (see /investigations/templates)"
    )
    choices: dict[str, str] = Field(default_factory=dict, description="Ambiguous term -> chosen metric id")
    auto_run: bool = Field(
        default=True, description="Run immediately when the plan is cheap (no approval needed)"
    )


class InvestigationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=400)


class InterpretRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    choices: dict[str, str] = Field(default_factory=dict)


class DisambiguateRequest(BaseModel):
    choices: dict[str, str] = Field(description="Ambiguous term -> chosen metric id")


class ReplanRequest(BaseModel):
    template: str | None = None


class RunRequest(BaseModel):
    pin_definitions: bool = Field(
        default=False,
        description="Rerun only: use the metric versions recorded by the last run instead of the current ones",
    )


class DrillRequest(BaseModel):
    dimension: str


class NodeActionRequest(BaseModel):
    action: Literal["confirm", "reject", "needs_review", "annotate", "rerun"]
    note: str | None = Field(default=None, max_length=5000)


class PromoteRequest(BaseModel):
    statement: str | None = Field(default=None, max_length=5000, description="Override the node statement")
    notes: str = ""


class InvestigationSummary(ApiModel):
    id: str
    question: str
    title: str
    status: str
    template: str | None
    brief_answer: str | None = None
    run_count: int
    created_at: datetime
    updated_at: datetime


class TreeNodeOut(TreeNode):
    """``TreeNode`` plus the finding saved from it (filled from the findings table on every response)."""

    finding_id: str | None = Field(default=None, description="Finding promoted from this node, if any")


class InvestigationTreeOut(InvestigationTree):
    nodes: list[TreeNodeOut] = Field(default_factory=list)  # type: ignore[assignment]


class OrchestrationOut(BaseModel):
    """What the optional AI orchestrator did for this investigation (null when it did not run)."""

    stages: list[dict[str, Any]] = Field(
        default_factory=list, description="[{stage, source, ok, detail, duration_ms}] (spec §51)"
    )
    narrative_source: str = "template"
    rejected_outputs: list[str] = Field(
        default_factory=list, description="Model output refused by validation"
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list, description="[{tool, ok, error, artifact_ids}] (spec §52)"
    )
    plan_steps_added: list[str] = Field(default_factory=list, description="Plan steps the assistant added")
    deterministic_brief: str | None = Field(
        default=None,
        description="The template answer, kept when the verified AI wording replaced it in brief_answer",
    )


class InvestigationOut(BaseModel):
    id: str
    question: str
    title: str
    template: str | None
    status: InvestigationStatus
    interpretation: Interpretation | None
    plan: AnalysisPlan | None
    hypotheses: list[Hypothesis]
    tree: InvestigationTreeOut | None
    brief_answer: str | None
    followups: list[str]
    failures: list[str]
    metric_version_ids: dict[str, Any] = Field(
        description="metric id -> {version_id, version_no, engine_version_id}"
    )
    dataset_versions: dict[str, Any] = Field(
        description="table -> {content_hash, row_count, dataset_id, version_id}"
    )
    semantic_snapshot_id: str | None
    engine_version: str | None
    run_count: int
    current_run_id: str | None
    node_findings: dict[str, str] = Field(default_factory=dict, description="tree node id -> finding id")
    orchestration: OrchestrationOut | None = None
    error: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class InvestigationRunOut(ApiModel):
    id: str
    run_no: int
    kind: str
    status: str
    metric_version_ids: dict[str, Any]
    dataset_versions: dict[str, Any]
    semantic_snapshot_id: str | None
    error: str | None
    duration_ms: float
    started_at: datetime
    finished_at: datetime | None
    diff_summary: list[str] = Field(default_factory=list)


class DiffOut(BaseModel):
    from_run_id: str
    from_run_no: int
    to_run_id: str
    to_run_no: int
    diff: InvestigationDiff


class TemplateOut(BaseModel):
    id: str
    name: str
    description: str
    metric_roles: list[str]
    intents: list[str]
    checks: list[dict[str, Any]]


class ArtifactOut(ApiModel):
    id: str
    engine_id: str | None = Field(
        description="Content-derived id referenced by tree nodes (unique within a run)"
    )
    kind: str
    title: str
    investigation_id: str | None
    run_id: str | None
    sql: str | None
    python: str | None
    params: dict[str, Any]
    filters: list[dict[str, Any]]
    filter_context: list[dict[str, Any]]
    metric_versions: dict[str, Any]
    dataset_versions: list[dict[str, Any]]
    result: dict[str, Any] | None = Field(description="Result snapshot {columns, rows, row_count, truncated}")
    data: dict[str, Any] | None = Field(
        default=None,
        description="The calculation behind the artifact (engine Artifact.data): for contributions and "
        "decompositions {method, additive_valid, total_change, rows[{segment, current, baseline, effect, "
        "share_of_change, mix_effect, rate_effect}], notes}; for analyses the engine result model",
    )
    chart_spec: dict[str, Any] | None
    validation: dict[str, Any] | None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    parent_ids: list[str]
    origin: str
    created_at: datetime


class ArtifactRerun(BaseModel):
    artifact_id: str
    identical: bool
    previous_row_count: int
    result: TabularResult
    changes: list[str]


class CommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    node_id: str | None = None


class CommandResult(BaseModel):
    command: dict[str, Any] = Field(
        description="Parsed command (kind, dimension, metric, filters, windows...)"
    )
    message: str
    action: Literal[
        "drilled",
        "branched",
        "created_investigation",
        "saved_finding",
        "show_sql",
        "built_report",
        "built_dashboard",
        "rerun_started",
        "node_updated",
        "listed_contributors",
        "clarify",
        "none",
    ]
    investigation_id: str | None = None
    finding_id: str | None = None
    report_id: str | None = None
    dashboard_id: str | None = None
    job_id: str | None = None
    sql: list[dict[str, Any]] = Field(
        default_factory=list, description="[{artifact_id, title, sql}] for show_sql"
    )
    items: list[dict[str, Any]] = Field(default_factory=list)


class SummaryOut(BaseModel):
    investigation_id: str
    summary: Summary
