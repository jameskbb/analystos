"""Typed data model of an investigation.

Everything the investigator produces is a Pydantic v2 model so that the API layer can
persist it as JSON and the web client can render it without re-deriving anything.

Design notes
------------
* The tree is stored flat (``InvestigationTree.nodes``) with ``parent_id`` / ``children``
  holding node ids. Flat storage keeps JSON small, makes node lookups O(1) and lets the
  diff engine compare nodes by their stable ids.
* Node and artifact ids are content-derived (see :mod:`analystos_investigator.ids`), so the
  same data and definitions always yield the same tree, including ids.
* Numbers in statements are always rendered from the numeric fields on the same node, which
  in turn come from executed query artifacts (``artifact_ids``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from analystos_engine.semantic.models import Filter
from analystos_engine.types import TimeWindow
from pydantic import BaseModel, ConfigDict, Field

ENGINE_VERSION = "analystos-investigator/1"

Intent = Literal["why_change", "compare", "breakdown", "trend", "lookup", "forecast", "anomaly"]
ComparisonKind = Literal["pop", "yoy", "budget", "forecast", "segment", "none"]
FilterOp = Literal["eq", "in", "not_in", "neq", "is_null", "not_null"]
StatementType = Literal["observation", "supported_explanation", "hypothesis"]
EvidenceStrength = Literal["strong", "moderate", "weak", "hypothesis_only"]
NodeStatus = Literal["proposed", "confirmed", "rejected", "needs_review", "failed"]
NodeKind = Literal["root", "driver", "dimension", "segment", "other", "check", "hypothesis", "failed"]
StepKind = Literal["decompose", "segment", "contribution", "compare", "anomaly", "custom_sql", "python"]
ArtifactKind = Literal[
    "query", "dataframe", "metric_result", "chart", "statistical_test", "finding", "hypothesis", "note"
]
InvestigationStatus = Literal[
    "needs_disambiguation", "awaiting_approval", "ready", "running", "completed", "failed"
]


def utcnow() -> datetime:
    return datetime.now(UTC)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- interpretation


class FilterSpec(_Model):
    """A resolved filter on a semantic-model dimension."""

    dimension: str
    op: FilterOp
    values: list[str] = Field(default_factory=list)
    source_text: str = ""

    def describe(self) -> str:
        if self.op == "is_null":
            return f"{self.dimension} is empty"
        if self.op == "not_null":
            return f"{self.dimension} is not empty"
        joined = ", ".join(self.values)
        if self.op in ("eq", "in"):
            return f"{self.dimension} = {joined}"
        return f"{self.dimension} excludes {joined}"

    def to_engine(self) -> Filter:
        if self.op in ("is_null", "not_null"):
            return Filter(dimension=self.dimension, op=self.op, values=[])
        op = self.op
        if op == "eq" and len(self.values) > 1:
            op = "in"
        if op == "neq" and len(self.values) > 1:
            op = "not_in"
        return Filter(dimension=self.dimension, op=op, values=list(self.values))


class MetricCandidate(_Model):
    metric_id: str
    label: str
    description: str = ""
    matched_by: str = ""


class AmbiguousTerm(_Model):
    """A question term that maps to more than one canonical metric (spec §15).

    The investigator never picks one of the candidates by itself. ``llm_suggestion`` holds
    an optional model suggestion, which is shown to the user but never applied.
    """

    term: str
    candidates: list[MetricCandidate]
    reason: str
    llm_suggestion: str | None = None


class UnresolvedFilter(_Model):
    text: str
    reason: str


class Premise(_Model):
    """A fact the question takes for granted ("revenue was roughly flat").

    The investigator measures every premise over the analysis periods and reports whether
    it holds; when the question names no period, the period is chosen so that it holds.
    """

    text: str
    term: str
    metric_id: str | None = None
    expectation: Literal["flat", "increase", "decrease"]
    tolerance: float = 0.02
    """Largest relative change still called flat (2% by default)."""


class SegmentComparison(_Model):
    """Segment-versus-segment comparison ("Dallas vs Houston"): the current side is
    ``dimension = current``, the baseline side ``dimension = baseline``, same period."""

    dimension: str
    current: str
    baseline: str


class PeriodCandidate(_Model):
    """A period pair considered when the question states a premise but no period."""

    window: TimeWindow
    baseline: TimeWindow
    comparison_kind: ComparisonKind
    premise_changes: dict[str, float | None] = Field(default_factory=dict)
    subject_change: float | None = None
    holds: bool = False


class Interpretation(_Model):
    question: str
    metric_ids: list[str] = Field(default_factory=list)
    ambiguous: list[AmbiguousTerm] = Field(default_factory=list)
    window: TimeWindow | None = None
    baseline: TimeWindow | None = None
    comparison_kind: ComparisonKind = "pop"
    baseline_metric_id: str | None = None
    filters: list[FilterSpec] = Field(default_factory=list)
    unresolved_filters: list[UnresolvedFilter] = Field(default_factory=list)
    breakdown_dimensions: list[str] = Field(default_factory=list)
    intent: Intent = "lookup"
    direction: Literal["decrease", "increase", "unspecified"] = "unspecified"
    template_id: str | None = None
    resolved_terms: dict[str, str] = Field(default_factory=dict)
    term_positions: dict[str, int] = Field(default_factory=dict)
    subject_position: int | None = None
    confidence_notes: list[str] = Field(default_factory=list)
    premises: list[Premise] = Field(default_factory=list)
    period_defaulted: bool = False
    """True when the question named no period and the window is a default."""
    reference_date: date | None = None
    segment_comparison: SegmentComparison | None = None
    ranking: Literal["growth", "decline"] | None = None
    """Rank segments by growth rate ("which products grew fastest") instead of by share."""
    period_candidates: list[PeriodCandidate] = Field(default_factory=list)

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.ambiguous)

    @property
    def primary_metric_id(self) -> str | None:
        return self.metric_ids[0] if self.metric_ids else None


# --------------------------------------------------------------------------- plan


class PlanContext(_Model):
    """Everything the executor needs besides the steps, frozen at planning time so that a
    rerun re-executes exactly the same analysis (same absolute periods and filters)."""

    question: str
    metric_id: str
    window: TimeWindow
    baseline: TimeWindow
    comparison_kind: ComparisonKind = "pop"
    baseline_metric_id: str | None = None
    filters: list[FilterSpec] = Field(default_factory=list)
    intent: Intent = "why_change"
    direction: Literal["decrease", "increase", "unspecified"] = "unspecified"
    premises: list[Premise] = Field(default_factory=list)
    segment_comparison: SegmentComparison | None = None
    ranking: Literal["growth", "decline"] | None = None
    reference_date: date | None = None
    """Date the data is complete to; cohort metrics younger than their maturity are flagged."""


class ExecutionConfig(_Model):
    """Limits that bound the investigation. Stored on the plan for reproducibility."""

    top_n: int = Field(default=5, ge=1, le=50)
    drill_depth: int = Field(default=2, ge=0, le=4)
    drill_top_n: int = Field(default=2, ge=0, le=10)
    drill_min_share: float = Field(default=0.2, ge=0.0, le=1.0)
    drill_dimensions: int = Field(default=3, ge=0, le=10)
    drill_groups: int = Field(default=2, ge=0, le=10)
    drill_min_power: float = Field(default=0.1, ge=0.0, le=1.0)
    decomposition_depth: int = Field(default=2, ge=0, le=4)
    driver_dimensions: int = Field(default=3, ge=0, le=20)
    max_dimensions: int | None = Field(default=None, ge=1, le=30)
    """Dimensions broken down in a full investigation; None lets the template decide (6-8)."""
    max_queries: int = Field(default=300, ge=1)
    approval_query_threshold: int = Field(default=40, ge=0)
    row_limit: int = Field(default=10_000, ge=1)
    snapshot_rows: int = Field(default=200, ge=0)
    timeout_s: float = Field(default=30.0, gt=0)


class PlanStep(_Model):
    id: str
    kind: StepKind
    title: str
    rationale: str
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    estimated_queries: int = 0
    origin: Literal["template", "metric_tree", "dimension", "user", "llm"] = "dimension"


class AnalysisPlan(_Model):
    steps: list[PlanStep] = Field(default_factory=list)
    requires_approval: bool = False
    approval_reasons: list[str] = Field(default_factory=list)
    template_id: str | None = None
    estimated_queries: int = 0
    notes: list[str] = Field(default_factory=list)
    context: PlanContext | None = None
    config: ExecutionConfig = Field(default_factory=ExecutionConfig)

    def enabled_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.enabled]

    def step(self, step_id: str) -> PlanStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(f"no plan step {step_id!r}")


class Hypothesis(_Model):
    id: str
    statement: str
    category: Literal[
        "driver_decomposition",
        "dimension_mix",
        "customer_concentration",
        "price_discount",
        "cancellations_returns",
        "seasonality",
        "untestable",
    ]
    testable: bool
    test_step_ids: list[str] = Field(default_factory=list)
    reason: str = ""


# --------------------------------------------------------------------------- artifacts


class ValidationCheck(_Model):
    name: str
    passed: bool
    detail: str = ""


class ValidationSummary(_Model):
    ok: bool
    checks: list[ValidationCheck] = Field(default_factory=list)

    def failures(self) -> list[ValidationCheck]:
        return [c for c in self.checks if not c.passed]


class DatasetVersionRef(_Model):
    table: str
    content_hash: str
    row_count: int
    captured_at: datetime | None = None


class ResultSnapshot(_Model):
    columns: list[dict[str, str]] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: float | None = None


class Artifact(_Model):
    """Reproducible record of one analytical test (spec §26, §60)."""

    id: str
    kind: ArtifactKind
    title: str = ""
    sql: str | None = None
    python: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    filters: list[FilterSpec] = Field(default_factory=list)
    window: TimeWindow | None = None
    metric_ids: list[str] = Field(default_factory=list)
    metric_versions: dict[str, str] = Field(default_factory=dict)
    dataset_versions: list[DatasetVersionRef] = Field(default_factory=list)
    result: ResultSnapshot | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    chart_spec: dict[str, Any] | None = None
    validation: ValidationSummary | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    parent_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- tree


class Segment(_Model):
    dimension: str
    value: str | None

    def label(self) -> str:
        return f"{self.dimension} = {self.value if self.value is not None else '(null)'}"


class Contribution(_Model):
    """Signed share of the parent's change explained by this node.

    ``share`` is ``effect / parent_change``; shares of siblings within one dimension (or one
    exact identity) sum to 1.0. Shares of nodes in different dimensions are never additive.
    """

    effect: float
    share: float | None
    method: Literal["additive", "ratio_mix_rate", "lmdi", "additive_identity", "non_additive"]
    mix_effect: float | None = None
    rate_effect: float | None = None


class TreeNode(_Model):
    id: str
    parent_id: str | None
    kind: NodeKind
    statement: str
    statement_type: StatementType
    metric_id: str | None = None
    metric_label: str | None = None
    metric_format: str | None = None
    segment: Segment | None = None
    segment_path: list[Segment] = Field(default_factory=list)
    dimension: str | None = None
    current: float | None = None
    baseline: float | None = None
    abs_change: float | None = None
    pct_change: float | None = None
    contribution_to_parent: Contribution | None = None
    evidence_strength: EvidenceStrength
    evidence_reasons: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    step_id: str | None = None
    status: NodeStatus = "proposed"
    children: list[str] = Field(default_factory=list)
    annotations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    depth: int = 0
    rank: int | None = None
    explanatory_power: float | None = None
    finding_id: str | None = None
    """Finding saved from this node (set by the API; carried forward on rerun)."""
    decision_history: list[dict[str, Any]] = Field(default_factory=list)
    """Analyst decisions carried over from earlier runs: ``{run, status, note}``."""


class InvestigationTree(_Model):
    root_id: str | None = None
    nodes: list[TreeNode] = Field(default_factory=list)

    def by_id(self) -> dict[str, TreeNode]:
        return {n.id: n for n in self.nodes}

    def get(self, node_id: str) -> TreeNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"no tree node {node_id!r}")

    def root(self) -> TreeNode:
        if self.root_id is None:
            raise KeyError("tree has no root")
        return self.get(self.root_id)

    def children_of(self, node_id: str) -> list[TreeNode]:
        index = self.by_id()
        return [index[c] for c in index[node_id].children if c in index]

    def walk(self) -> list[TreeNode]:
        """Depth-first pre-order traversal starting at the root."""
        if self.root_id is None:
            return []
        index = self.by_id()
        out: list[TreeNode] = []
        stack = [self.root_id]
        while stack:
            nid = stack.pop()
            node = index.get(nid)
            if node is None:
                continue
            out.append(node)
            stack.extend(reversed(node.children))
        return out

    def render_text(self) -> str:
        """ASCII rendering used by logs, docs and tests (spec §1 layout)."""
        index = self.by_id()
        lines: list[str] = []

        def rec(nid: str, prefix: str, is_last: bool, top: bool) -> None:
            node = index[nid]
            if top:
                lines.append(node.statement)
                child_prefix = ""
            else:
                lines.append(f"{prefix}{'`-- ' if is_last else '+-- '}{node.statement}")
                child_prefix = prefix + ("    " if is_last else "|   ")
            kids = [c for c in node.children if c in index]
            for i, c in enumerate(kids):
                rec(c, child_prefix, i == len(kids) - 1, False)

        if self.root_id is not None:
            rec(self.root_id, "", True, True)
        return "\n".join(lines)


class Investigation(_Model):
    id: str
    question: str
    interpretation: Interpretation
    plan: AnalysisPlan | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    tree: InvestigationTree = Field(default_factory=InvestigationTree)
    status: InvestigationStatus = "ready"
    brief_answer: str | None = None
    followups: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    engine_version: str = ENGINE_VERSION
    model_snapshot_hash: str | None = None
    run_config: dict[str, Any] = Field(default_factory=dict)


class InvestigationRun(_Model):
    """Result of running an investigation: the investigation plus every artifact it produced."""

    investigation: Investigation
    artifacts: list[Artifact] = Field(default_factory=list)

    def artifact(self, artifact_id: str) -> Artifact:
        for a in self.artifacts:
            if a.id == artifact_id:
                return a
        raise KeyError(f"no artifact {artifact_id!r}")


# --------------------------------------------------------------------------- diff


class NodeChange(_Model):
    node_id: str
    change: Literal["added", "removed", "changed"]
    statement_before: str | None = None
    statement_after: str | None = None
    fields: dict[str, tuple[Any, Any]] = Field(default_factory=dict)


class DatasetVersionChange(_Model):
    table: str
    before: DatasetVersionRef | None
    after: DatasetVersionRef | None


class MetricVersionChange(_Model):
    metric_id: str
    before: str | None
    after: str | None


class InvestigationDiff(_Model):
    previous_investigation_id: str
    node_changes: list[NodeChange] = Field(default_factory=list)
    dataset_version_changes: list[DatasetVersionChange] = Field(default_factory=list)
    metric_version_changes: list[MetricVersionChange] = Field(default_factory=list)
    unchanged_nodes: int = 0
    summary: list[str] = Field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.node_changes or self.dataset_version_changes or self.metric_version_changes)


# --------------------------------------------------------------------------- summary


class SummaryItem(_Model):
    text: str
    node_id: str | None = None
    evidence_strength: EvidenceStrength | None = None
    artifact_ids: list[str] = Field(default_factory=list)


class Summary(_Model):
    """Executive summary built only from confirmed findings (spec §38)."""

    observations: list[SummaryItem] = Field(default_factory=list)
    supported_explanations: list[SummaryItem] = Field(default_factory=list)
    hypotheses: list[SummaryItem] = Field(default_factory=list)
    excluded_count: int = 0
    narrative: str | None = None
    narrative_source: Literal["template", "llm_verified"] = "template"
    notes: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        parts: list[str] = []
        for title, items in (
            ("Observed facts", self.observations),
            ("Supported explanations", self.supported_explanations),
            ("Unresolved hypotheses", self.hypotheses),
        ):
            parts.append(f"### {title}")
            if items:
                parts.extend(f"- {i.text}" for i in items)
            else:
                parts.append("- None confirmed.")
            parts.append("")
        return "\n".join(parts).rstrip() + "\n"
