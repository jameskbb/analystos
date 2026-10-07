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
StepKind = Literal[
    "decompose",
    "segment",
    "contribution",
    "compare",
    "anomaly",
    "custom_sql",
    "python",
    "reconcile",
    "related_signals",
]
NodeRole = Literal["premise", "reconciliation", "related_signal"]
"""What a ``check`` node is for, so a client can place it without parsing prose: a premise
verdict, the reconciliation of an exact decomposition, or a related signal (an observation
whose relationship to the explained change is not established, never part of the tree's
reconciled explanation)."""
PremiseExpectation = Literal["flat", "increase", "decrease", "meets_plan", "misses_plan"]
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
    expectation: PremiseExpectation
    tolerance: float = 0.02
    """Largest relative change still called flat (2% by default). For ``meets_plan`` the
    largest shortfall against plan still called meeting it (1% by default)."""
    baseline_metric_id: str | None = None
    """For ``meets_plan`` / ``misses_plan``: the plan, budget or target metric the premise names
    ("we hit our revenue target"). The premise compares ``metric_id`` with it over the analysis
    window instead of with a baseline period."""


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
    unmeasured_terms: list[str] = Field(default_factory=list)
    """Concepts the question names that the glossary declares not measured ("net income after tax")."""
    comparison_defaulted: bool = False
    """True when the question named no comparison and the previous period was assumed."""
    plan_role_word: str | None = None
    """The yardstick word of a comparison against plan ("plan", "budget", "target")."""
    named_plan_metrics: list[str] = Field(default_factory=list)
    """Plan metrics the question names ("our revenue target"); they are baselines, not subjects."""

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
    plan_metrics: dict[str, str] = Field(default_factory=dict)
    """Against a plan: each actual metric in the plan's scope -> its governed plan metric
    (``plan_for``), frozen at planning so a rerun compares with the same plan metrics."""


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
    related_signals: int = Field(default=3, ge=0, le=10)
    """Related signals reported at most (0 disables the scan)."""
    related_min_change: float = Field(default=0.25, ge=0.0)
    """A related signal must move at least this much (relative change, 0.25 = 25%) in the shared
    segment, and at least twice as much as the same metric across the whole business."""


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


class ReconciliationLine(_Model):
    """One component of a reconciled identity, signed in the root metric's units."""

    node_id: str | None = None
    label: str
    amount: float
    metric_id: str | None = None


class ReconciliationIdentity(_Model):
    """``parent_amount = sum(components) + residual``, checked on executed numbers."""

    parent_node_id: str | None = None
    parent_label: str
    parent_amount: float
    components: list[ReconciliationLine] = Field(default_factory=list)
    sum_of_components: float
    residual: float
    reconciled: bool
    method: str = ""


class Reconciliation(_Model):
    """Does the explanation add back to the change? Carried by the ``reconciliation`` node.

    ``gap`` is the root's change (actual minus plan, or current minus baseline). Every
    identity lists its components in the root metric's units, so the top identity reads
    "gross margin + operating expenses = the operating profit miss".
    """

    metric_id: str
    metric_label: str
    metric_format: str | None = None
    actual: float
    baseline: float
    gap: float
    tolerance: float
    reconciled: bool
    identities: list[ReconciliationIdentity] = Field(default_factory=list)


class BridgeStep(_Model):
    """One bar of a plan-to-actual bridge: a reconciled part of the gap, in root units."""

    node_id: str | None = None
    label: str
    amount: float
    group_label: str | None = None
    group_node_id: str | None = None
    running_total: float


class PlanBridge(_Model):
    """Plan value -> the reconciled parts of the gap -> actual value (a waterfall).

    The steps are the reconciled children of each first-level part of the explanation (or the
    part itself when it has none); they sum exactly to ``gap`` when ``reconciled``.
    """

    metric_id: str
    metric_label: str
    metric_format: str | None = None
    start_label: str = "Plan"
    start: float
    end_label: str = "Actual"
    end: float
    gap: float
    steps: list[BridgeStep] = Field(default_factory=list)
    reconciled: bool
    tolerance: float = 0.005
    chart: dict[str, Any] | None = None
    artifact_id: str | None = None


class RelatedSignal(_Model):
    """A verified observation on another governed metric in a segment where the explanation
    concentrates. Its relationship to the explained change is not established: it is shown
    next to the tree, never inside the reconciled explanation. ``node_id`` is the tree node
    (``role="related_signal"``) that carries status, evidence and lineage."""

    node_id: str
    metric_id: str
    metric_label: str
    metric_format: str | None = None
    unit: str | None = None
    title: str
    statement: str
    segment_path: list[Segment] = Field(default_factory=list)
    window: TimeWindow
    baseline_window: TimeWindow
    current: float | None = None
    baseline: float | None = None
    abs_change: float | None = None
    pct_change: float | None = None
    relationship: Literal["not_established"] = "not_established"
    relationship_note: str
    shared_with: list[Segment] = Field(default_factory=list)
    """The explanation segments this signal shares (e.g. region = Gulf Coast)."""
    linked_node_ids: list[str] = Field(default_factory=list)
    supporting_node_ids: list[str] = Field(default_factory=list)
    """Child nodes measuring related metrics in the same segment (e.g. response time)."""
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_strength: EvidenceStrength = "strong"
    provisional: bool = False


class TreeNode(_Model):
    id: str
    parent_id: str | None
    kind: NodeKind
    statement: str
    statement_type: StatementType
    metric_id: str | None = None
    metric_label: str | None = None
    metric_format: str | None = None
    metric_unit: str | None = None
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
    title: str | None = None
    """Short executive label ("Unit costs above plan", "Freight Cost over plan"). The statement
    carries the numbers; the title is what a reader scans."""
    role: NodeRole | None = None
    verdict: Literal["supported", "contradicted"] | None = None
    """Premise nodes: whether the data supports what the question takes for granted."""
    root_effect: float | None = None
    """Signed effect of this node on the root metric (root metric units), for nodes inside an
    exact decomposition of the root change: children sum to their parent's root effect."""
    baseline_label: str | None = None
    """What ``baseline`` is ("Plan", "Q1 2026", "Houston"), so the UI labels the columns."""
    relationship: Literal["not_established"] | None = None
    """Related signals only: their relationship to the explained change is not established."""
    reconciliation: Reconciliation | None = None
    """Reconciliation nodes only: the identities checked and whether they add back."""
    method: str | None = None
    """How the node was measured, in technical terms (identity, counterfactual, rates held at
    plan). The statement stays in plain language; this is the one-click-away methodology."""
    members: list[ReconciliationLine] = Field(default_factory=list)
    """"Other lines, net" nodes: the immaterial lines grouped into this node."""
    offset_node_id: str | None = None
    """A drill segment offset by a sibling line in the same segment (overtime over plan in a
    branch where payroll came in under plan): that sibling segment's node."""


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
    related_signals: list[RelatedSignal] = Field(default_factory=list)
    """Verified observations next to the explanation, relationship not established (see
    :class:`RelatedSignal`). Each one is also a tree node with ``role="related_signal"``."""
    bridge: PlanBridge | None = None
    """Plan-to-actual bridge of a gap to plan (see :class:`PlanBridge`)."""
    brief_lead: str | None = None
    """Two or three short sentences leading the brief: the gap, its split, the largest causes."""


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
    related_signals: list[SummaryItem] = Field(default_factory=list)
    """Confirmed related signals: observations whose relationship to the explained change is not
    established. Listed apart from explanations, and never worded as causes."""
    excluded_count: int = 0
    narrative: str | None = None
    narrative_source: Literal["template", "llm_verified"] = "template"
    notes: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        parts: list[str] = []
        sections = [
            ("Observed facts", self.observations),
            ("Supported explanations", self.supported_explanations),
            ("Unresolved hypotheses", self.hypotheses),
        ]
        if self.related_signals:
            sections.insert(2, ("Related signals (relationship not established)", self.related_signals))
        for title, items in sections:
            parts.append(f"### {title}")
            if items:
                parts.extend(f"- {i.text}" for i in items)
            else:
                parts.append("- None confirmed.")
            parts.append("")
        return "\n".join(parts).rstrip() + "\n"
