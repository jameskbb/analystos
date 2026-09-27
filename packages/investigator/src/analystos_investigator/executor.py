"""Plan execution: every test is real SQL from the engine compiler (INV-04..07; spec §16-19, §26, §50, §87).

``run(plan, store, model)`` executes the enabled steps and returns the investigation tree and
every artifact. The flow:

1. ``compare`` (root): measure the metric in the current period and in the baseline (previous
   period, prior year, or a budget/forecast metric). The root is an observation. If it fails,
   the investigation stops with a failed root: nothing can be explained without it.
2. ``decompose``: split the change across the metric-tree drivers (LMDI for multiplicative
   and ratio identities, exact differences for additive ones), recursing into drivers' own
   trees up to ``decomposition_depth``. A residual node reports any gap.
3. ``contribution``: for each dimension, measure the metric by segment in both periods and
   attribute the change (additive partitions verified on the data, ratio metrics by mix/rate,
   non-additive metrics reported without shares). Segments are ranked by |share of change|;
   the top ``top_n`` become nodes and the rest are grouped in an "other" node. Shares sum to
   100% within one dimension and are never added across dimensions.
4. Drill: after all steps, the root metric's dimension groups are ranked by explanatory
   power (the disproportionality index, see ``_disproportionality``). In the top
   ``drill_groups`` groups, segments whose share is at least ``drill_min_share`` and larger
   than their share of the baseline are re-investigated inside the segment (driver
   decomposition plus contribution by the template's drill dimensions, e.g. customers inside
   Dallas), up to ``drill_depth`` levels.
5. ``compare`` checks (related metrics such as discount rate), seasonality (same transition
   a year earlier), ``anomaly`` (robust z-score of a single day), ``segment`` (trend),
   ``custom_sql`` and ``python`` steps.
6. Evidence: a final pass finds corroborations (the same segment explaining the parent and
   a driver) and applies :mod:`analystos_investigator.evidence`.

Every query is compiled by the engine, executed read-only through the store, validated, and
recorded as an Artifact with SQL, filters, window, metric versions and dataset versions. A
query that fails or does not validate yields a ``failed`` node that states the failure; no
number from it is used anywhere.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from analystos_engine.calendar import add_months, previous_period
from analystos_engine.semantic.compiler import CompiledQuery, MetricQuery, join_key_warnings
from analystos_engine.semantic.compiler import compile as compile_query
from analystos_engine.semantic.models import SemanticModel
from analystos_engine.sqlsafety import ensure_read_only
from analystos_engine.types import TimeWindow

from . import charts
from ._engine_compat import engine_validation_checks, run_python_sandbox
from .attribution import (
    ContributionOutcome,
    SegmentRow,
    additive_contribution,
    decompose_drivers,
    ratio_contribution,
    safe_pct,
    safe_share,
)
from .evidence import (
    MODERATE_SHARE,
    RESIDUAL_TOLERANCE,
    EvidenceInput,
    assess,
    phrase_hypothesis,
    statement_type_for,
)
from .formatting import change_phrase, direction_word, fmt_abs_pct, fmt_change, fmt_pct, fmt_share, fmt_value
from .ids import canonical_json, stable_id
from .models import (
    AnalysisPlan,
    Artifact,
    Contribution,
    DatasetVersionRef,
    FilterSpec,
    InvestigationTree,
    NodeKind,
    PlanContext,
    PlanStep,
    ResultSnapshot,
    Segment,
    TreeNode,
    ValidationCheck,
    ValidationSummary,
)
from .planner import driver_edges, pinned_dimensions
from .semantic_graph import dimensions_for_metric, time_dimension_for_metric
from .templates import TEMPLATES, dimension_roles

IDENTITY_GAP_LIMIT = 0.05
"""A driver identity that misses the parent by more than 5% in either period is not used."""


class ExecutionError(RuntimeError):
    pass


class _BudgetExceeded(ExecutionError):
    pass


@dataclass
class QueryOutcome:
    artifact: Artifact
    ok: bool
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    compiled: CompiledQuery | None = None


@dataclass
class ExecutionResult:
    tree: InvestigationTree
    artifacts: list[Artifact]
    failures: list[str]
    query_count: int
    notes: list[str] = field(default_factory=list)


@dataclass
class _Side:
    metric_id: str
    window: TimeWindow
    label: str
    filters: list[FilterSpec] = field(default_factory=list)
    """Extra filters of this side (segment-versus-segment comparisons)."""


def _to_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _seg_key(v: Any) -> str | None:
    return None if v is None else str(v)


def _definitional(outcome: ContributionOutcome) -> bool:
    """A ratio breakdown whose segment rates only take the values 0 and one constant (for
    example conversion rate by lead status: Converted = 100%, Open = Lost = 0%) describes the
    metric's definition, not an explanation of its change."""
    if outcome.method != "ratio_mix_rate" or len(outcome.rows) < 2:
        return False
    rates = {round(v, 9) for r in outcome.rows for v in (r.current, r.baseline) if v is not None}
    return len(rates) <= 2 and 0.0 in rates


def _rank_by_growth(outcome: ContributionOutcome, direction: str) -> tuple[list[SegmentRow], str]:
    """Rows ordered by growth rate. Segments below 0.5% of the baseline total (or new ones) are
    ranked after the rest, because a percentage on a tiny base is not meaningful."""
    base_total = sum(abs(r.baseline or 0.0) for r in outcome.rows) or 1.0
    floor = 0.005 * base_total

    def key(r: SegmentRow) -> tuple[int, float, str]:
        pct = r.pct_change
        small = pct is None or abs(r.baseline or 0.0) < floor
        val = pct if pct is not None else 0.0
        return (1 if small else 0, val if direction == "decrease" else -val, str(r.value))

    note = (
        f"ranked by {'decline' if direction == 'decrease' else 'growth'} rate; segments under 0.5% of the "
        "baseline total are listed last because their percentage changes are unstable"
    )
    return sorted(outcome.rows, key=key), note


def _disproportionality(outcome: ContributionOutcome) -> float | None:
    """Explanatory power of a dimension: total-variation distance between the distribution
    of the change across segments (shares) and the distribution of the baseline (weights).

    0 means every segment moved in proportion to its size (the dimension explains nothing);
    values near 1 mean the change sits in segments that were small in the baseline. Only
    defined for exact attribution methods.
    """
    if not outcome.additive_valid:
        return None
    rows = [r for r in outcome.rows if r.share is not None]
    if not rows:
        return None
    return sum(abs((r.share or 0.0) - (r.base_share or 0.0)) for r in rows) / 2


class _Executor:
    def __init__(self, plan: AnalysisPlan, store: Any, model: SemanticModel) -> None:
        if plan.context is None:
            raise ExecutionError("plan has no context; build plans with planner.plan()")
        self.plan = plan
        self.ctx: PlanContext = plan.context
        self.cfg = plan.config
        self.store = store
        self.model = model
        self.nodes: dict[str, TreeNode] = {}
        self.order: list[str] = []
        self.evidence: dict[str, EvidenceInput] = {}
        self.artifacts: dict[str, Artifact] = {}
        self.failures: list[str] = []
        self.notes: list[str] = []
        self.query_count = 0
        self._cache: dict[str, QueryOutcome] = {}
        self.immature: dict[str, list[str]] = {}
        self._versions: dict[str, DatasetVersionRef] = {}
        self.root_id: str | None = None
        self.drill_candidates: list[str] = []
        tpl_id = plan.template_id or "general_change"
        self.template = TEMPLATES.get(tpl_id, TEMPLATES["general_change"])

    # ------------------------------------------------------------------ sides
    def _segment_filter(self, value: str) -> FilterSpec:
        sc = self.ctx.segment_comparison
        assert sc is not None
        return FilterSpec(dimension=sc.dimension, op="eq", values=[value], source_text="segment comparison")

    def current_side(self, metric_id: str) -> _Side:
        sc = self.ctx.segment_comparison
        if sc is not None:
            return _Side(
                metric_id,
                self.ctx.window,
                f"{self.dim_label(sc.dimension)} = {sc.current}, {self.ctx.window.display()}",
                [self._segment_filter(sc.current)],
            )
        return _Side(metric_id, self.ctx.window, self.ctx.window.display())

    def baseline_side(self, metric_id: str) -> _Side:
        sc = self.ctx.segment_comparison
        if sc is not None:
            return _Side(
                metric_id,
                self.ctx.window,
                f"{self.dim_label(sc.dimension)} = {sc.baseline}, {self.ctx.window.display()}",
                [self._segment_filter(sc.baseline)],
            )
        if (
            self.ctx.comparison_kind in ("budget", "forecast")
            and self.ctx.baseline_metric_id
            and metric_id == self.ctx.metric_id
        ):
            bm = self.model.get_metric(self.ctx.baseline_metric_id)
            return _Side(bm.id, self.ctx.window, f"{bm.display_name}, {self.ctx.window.display()}")
        return _Side(metric_id, self.ctx.baseline, self.ctx.baseline.display())

    def period_baseline(self) -> TimeWindow:
        """Baseline period for related checks. Against a budget or forecast the analysis
        window is compared with a plan metric, so period checks use the previous period."""
        if self.ctx.comparison_kind in ("budget", "forecast"):
            return previous_period(self.ctx.window, "pop")
        return self.ctx.baseline

    # ------------------------------------------------------------------ queries
    def _dataset_versions(
        self, compiled: CompiledQuery, metric_ids: list[str], dims: list[str], filters: list[FilterSpec]
    ) -> list[DatasetVersionRef]:
        entities: set[str] = set()
        for mid in metric_ids:
            for dep in self.model.metric_dependencies(mid):
                m = self.model.get_metric(dep)
                if m.entity:
                    entities.add(m.entity)
        for jp in compiled.joins:
            entities.add(jp.from_entity)
            entities.add(jp.to_entity)
            for st in jp.steps:
                entities.update((st.left_entity, st.right_entity))
        for dname in [*dims, *(f.dimension for f in filters)]:
            base = dname.split("__")[0]
            if self.model.has_dimension(base):
                entities.add(self.model.get_dimension(base).entity)
        out: list[DatasetVersionRef] = []
        for ent in sorted(entities):
            try:
                table = self.model.get_entity(ent).table
            except Exception:
                continue
            if table not in self._versions:
                try:
                    v = self.store.table_version(table)
                    self._versions[table] = DatasetVersionRef(
                        table=table,
                        content_hash=v.content_hash,
                        row_count=v.row_count,
                        captured_at=v.captured_at,
                    )
                except Exception as exc:
                    self.notes.append(f"could not version table {table}: {exc}")
                    continue
            out.append(self._versions[table])
        return out

    def metric_query(
        self,
        metric_ids: list[str],
        window: TimeWindow,
        *,
        dims: list[str] | None = None,
        filters: list[FilterSpec] | None = None,
        title: str,
        expect: str = "one",
    ) -> QueryOutcome:
        dims = dims or []
        filters = filters or []
        tdim = time_dimension_for_metric(self.model, metric_ids[0])
        tw = window.with_dimension(tdim) if tdim else None
        mq = MetricQuery(
            metrics=metric_ids,
            dimensions=dims,
            filters=[f.to_engine() for f in filters],
            time=tw,
            as_of=self.ctx.reference_date,
        )
        key = canonical_json(mq.model_dump(mode="json"))
        if key in self._cache:
            return self._cache[key]
        if self.query_count >= self.cfg.max_queries:
            raise _BudgetExceeded(f"query budget of {self.cfg.max_queries} reached")
        base_params = {
            "metrics": metric_ids,
            "dimensions": dims,
            "window": window.model_dump(mode="json"),
            "time_dimension": tdim,
        }
        if tdim is None:
            outcome = self._failed_artifact(
                title,
                base_params,
                filters,
                window,
                metric_ids,
                f"metric {metric_ids[0]!r} has no time dimension",
            )
            self._cache[key] = outcome
            return outcome
        try:
            compiled = compile_query(self.model, mq)
        except Exception as exc:
            outcome = self._failed_artifact(
                title,
                base_params,
                filters,
                window,
                metric_ids,
                f"compile failed: {type(exc).__name__}: {exc}",
            )
            self._cache[key] = outcome
            return outcome
        self.query_count += 1
        started = dt.datetime.now(dt.UTC)
        try:
            result = self.store.execute_read(
                compiled.sql, compiled.params or None, limit=self.cfg.row_limit, timeout_s=self.cfg.timeout_s
            )
        except Exception as exc:
            outcome = self._failed_artifact(
                title,
                base_params,
                filters,
                window,
                metric_ids,
                f"query failed: {type(exc).__name__}: {exc}",
                sql=compiled.sql,
                compiled=compiled,
            )
            self._cache[key] = outcome
            return outcome
        records = result.to_records()
        validation = self._validate(result, compiled, metric_ids, dims, filters, expect, records)
        versions = self._dataset_versions(compiled, metric_ids, dims, filters)
        snapshot = ResultSnapshot(
            columns=[{"name": c.name, "type": c.type} for c in result.columns],
            rows=[list(r) for r in result.rows[: self.cfg.snapshot_rows]],
            row_count=result.row_count,
            truncated=result.truncated or result.row_count > self.cfg.snapshot_rows,
            elapsed_ms=result.elapsed_ms,
        )
        art = Artifact(
            id=stable_id("art", "query", compiled.sql, compiled.params, canonical_json(result.rows)),
            kind="query",
            title=title,
            sql=compiled.sql,
            params={**base_params, "sql_params": compiled.params, "grain": compiled.grain},
            filters=list(filters),
            window=window,
            metric_ids=metric_ids,
            metric_versions=dict(compiled.metric_versions),
            dataset_versions=versions,
            result=snapshot,
            validation=validation,
            warnings=[*compiled.warnings, *self._key_warnings(compiled)],
            created_at=started,
        )
        self.artifacts[art.id] = art
        if compiled.immature_metrics:
            self.immature[art.id] = list(compiled.immature_metrics)
        ok = validation.ok
        err = (
            None
            if ok
            else "validation failed: "
            + "; ".join(f"{c.name} ({c.detail})" if c.detail else c.name for c in validation.failures())
        )
        outcome = QueryOutcome(art, ok, records, err, compiled)
        self._cache[key] = outcome
        return outcome

    def _key_warnings(self, compiled: CompiledQuery) -> list[str]:
        try:
            return [
                w for w in join_key_warnings(self.store, self.model, compiled) if w not in compiled.warnings
            ]
        except Exception:  # an inspection problem must not fail the analysis query
            return []

    def _failed_artifact(
        self,
        title: str,
        params: dict[str, Any],
        filters: list[FilterSpec],
        window: TimeWindow,
        metric_ids: list[str],
        error: str,
        *,
        sql: str | None = None,
        compiled: CompiledQuery | None = None,
    ) -> QueryOutcome:
        art = Artifact(
            id=stable_id("art", "query", title, params, error),
            kind="query",
            title=title,
            sql=sql,
            params=params,
            filters=list(filters),
            window=window,
            metric_ids=metric_ids,
            metric_versions=self._metric_versions(metric_ids),
            error=error,
            validation=ValidationSummary(
                ok=False, checks=[ValidationCheck(name="executed", passed=False, detail=error)]
            ),
        )
        self.artifacts[art.id] = art
        return QueryOutcome(art, False, [], error, compiled)

    def _metric_versions(self, metric_ids: list[str]) -> dict[str, str]:
        try:
            return self.model.metric_versions(metric_ids)
        except Exception:
            return {}

    def _validate(
        self,
        result: Any,
        compiled: CompiledQuery,
        metric_ids: list[str],
        dims: list[str],
        filters: list[FilterSpec],
        expect: str,
        records: list[dict[str, Any]],
    ) -> ValidationSummary:
        checks = [ValidationCheck(name="executed", passed=True, detail=f"{result.row_count} rows")]
        names = set(result.column_names)
        missing = [m for m in metric_ids if m not in names]
        checks.append(
            ValidationCheck(
                name="metric_present",
                passed=not missing,
                detail=f"missing columns: {missing}" if missing else "",
            )
        )
        checks.append(
            ValidationCheck(
                name="non_empty",
                passed=result.row_count > 0,
                detail="" if result.row_count else "the query returned no rows",
            )
        )
        if expect == "one":
            checks.append(
                ValidationCheck(
                    name="row_count_plausible",
                    passed=result.row_count == 1,
                    detail=f"expected 1 row, got {result.row_count}",
                )
            )
            if result.row_count == 1 and not missing:
                nulls = [m for m in metric_ids if records[0].get(m) is None]
                ratio = [m for m in nulls if self.model.get_metric(m).kind in ("ratio", "derived")]
                checks.append(
                    ValidationCheck(
                        name="value_not_null",
                        passed=not nulls,
                        detail=f"no data for {nulls} in the period" if nulls else "",
                    )
                )
                if ratio:
                    checks.append(
                        ValidationCheck(
                            name="denominator_nonzero",
                            passed=False,
                            detail=f"denominator is zero or missing for {ratio}",
                        )
                    )
        else:
            checks.append(
                ValidationCheck(
                    name="not_truncated",
                    passed=not result.truncated,
                    detail="result hit the row limit; the partition would be incomplete"
                    if result.truncated
                    else "",
                )
            )
        grain = list(compiled.grain)
        checks.append(
            ValidationCheck(
                name="grain",
                passed=sorted(grain) == sorted(dims),
                detail=f"grain {grain} vs requested {dims}",
            )
        )
        if filters:
            applied = " ".join(compiled.filters_applied).lower()
            missing_f = [f.dimension for f in filters if f.dimension.lower() not in applied]
            checks.append(
                ValidationCheck(
                    name="filters_applied",
                    passed=not missing_f,
                    detail=f"filters not reported as applied: {missing_f}" if missing_f else "",
                )
            )
        checks.extend(engine_validation_checks(result, compiled, expect))
        return ValidationSummary(ok=all(c.passed for c in checks), checks=checks)

    # ------------------------------------------------------------------ node helpers
    def add_node(self, node: TreeNode, ev: EvidenceInput) -> TreeNode:
        if node.id in self.nodes:
            return self.nodes[node.id]
        if node.parent_id is not None:
            parent = self.nodes[node.parent_id]
            node.depth = parent.depth + 1
            parent.children.append(node.id)
        self.nodes[node.id] = node
        self.order.append(node.id)
        self.evidence[node.id] = ev
        return node

    def metric_result(
        self,
        title: str,
        data: dict[str, Any],
        parents: list[str],
        *,
        chart: dict[str, Any] | None,
        filters: list[FilterSpec],
        window: TimeWindow | None,
        metric_ids: list[str],
    ) -> Artifact:
        versions: dict[str, str] = {}
        dsv: dict[str, DatasetVersionRef] = {}
        for pid in parents:
            p = self.artifacts.get(pid)
            if p:
                versions.update(p.metric_versions)
                for d in p.dataset_versions:
                    dsv[d.table] = d
        art = Artifact(
            id=stable_id("art", "metric_result", title, data, sorted(parents)),
            kind="metric_result",
            title=title,
            data=data,
            chart_spec=chart,
            parent_ids=parents,
            filters=filters,
            window=window,
            metric_ids=metric_ids,
            metric_versions=versions,
            dataset_versions=sorted(dsv.values(), key=lambda d: d.table),
            validation=ValidationSummary(
                ok=True,
                checks=[
                    ValidationCheck(
                        name="inputs_validated", passed=True, detail=f"{len(parents)} validated queries"
                    )
                ],
            ),
        )
        self.artifacts[art.id] = art
        return art

    def failed_node(
        self,
        parent_id: str | None,
        step: PlanStep | None,
        title: str,
        error: str,
        artifact_ids: list[str] | None = None,
    ) -> TreeNode:
        self.failures.append(f"{title}: {error}")
        node = TreeNode(
            id=stable_id("node", "failed", parent_id, step.id if step else title),
            parent_id=parent_id,
            kind="failed",
            statement=f"Test failed: {title}. {error}. No conclusion drawn.",
            statement_type="hypothesis",
            evidence_strength="hypothesis_only",
            artifact_ids=artifact_ids or [],
            step_id=step.id if step else None,
            status="failed",
        )
        return self.add_node(node, EvidenceInput(executed=False, failure=error))

    def metric_label(self, metric_id: str) -> str:
        return self.model.get_metric(metric_id).display_name

    def dim_label(self, dim: str) -> str:
        base = dim.split("__")[0]
        if self.model.has_dimension(base):
            d = self.model.get_dimension(base)
            return d.label or d.name.replace("_", " ").title()
        return dim

    def filters_for(self, path: list[Segment]) -> list[FilterSpec]:
        out = list(self.ctx.filters)
        for s in path:
            if s.value is None:
                out.append(FilterSpec(dimension=s.dimension, op="is_null", source_text="segment drill"))
            else:
                out.append(
                    FilterSpec(dimension=s.dimension, op="eq", values=[s.value], source_text="segment drill")
                )
        return out

    # ------------------------------------------------------------------ totals
    def totals(
        self, metric_id: str, path: list[Segment], *, compare_side: bool = True
    ) -> tuple[float | None, float | None, list[str], str | None]:
        """Current and baseline totals of ``metric_id`` under the segment path."""
        filters = self.filters_for(path)
        cur_side = self.current_side(metric_id)
        pb = self.period_baseline()
        if compare_side or self.ctx.segment_comparison is not None:
            base_side = self.baseline_side(metric_id)
            if not compare_side:
                base_side = _Side(metric_id, base_side.window, base_side.label, base_side.filters)
        else:
            base_side = _Side(metric_id, pb, pb.display())
        where = f" [{', '.join(s.label() for s in path)}]" if path else ""
        cur = self.metric_query(
            [cur_side.metric_id],
            cur_side.window,
            filters=[*filters, *cur_side.filters],
            title=f"{self.metric_label(metric_id)}{where}, {cur_side.label}",
        )
        base = self.metric_query(
            [base_side.metric_id],
            base_side.window,
            filters=[*filters, *base_side.filters],
            title=f"{self.metric_label(base_side.metric_id)}{where}, {base_side.label}",
        )
        ids = [cur.artifact.id, base.artifact.id]
        if not cur.ok or not base.ok:
            return None, None, ids, cur.error or base.error
        cv = _to_float(cur.rows[0].get(cur_side.metric_id))
        bv = _to_float(base.rows[0].get(base_side.metric_id))
        return cv, bv, ids, None

    def by_segment(
        self,
        metric_id: str,
        window: TimeWindow,
        dim: str,
        path: list[Segment],
        label: str,
        extra: list[FilterSpec] | None = None,
    ) -> tuple[dict[str | None, float] | None, str, str | None]:
        out = self.metric_query(
            [metric_id],
            window,
            dims=[dim],
            filters=[*self.filters_for(path), *(extra or [])],
            title=f"{self.metric_label(metric_id)} by {self.dim_label(dim)}"
            + (f" [{', '.join(s.label() for s in path)}]" if path else "")
            + f", {label}",
            expect="many",
        )
        if not out.ok:
            return None, out.artifact.id, out.error
        values: dict[str | None, float] = {}
        for r in out.rows:
            values[_seg_key(r.get(dim))] = _to_float(r.get(metric_id)) or 0.0
        return values, out.artifact.id, None

    # ------------------------------------------------------------------ steps
    def run(self) -> ExecutionResult:
        steps = self.plan.enabled_steps()
        root_step = next(
            (
                s
                for s in steps
                if s.kind == "compare"
                and s.params.get("metric_id") == self.ctx.metric_id
                and not s.params.get("role")
            ),
            None,
        )
        if root_step is None:
            raise ExecutionError("the plan has no root comparison step")
        try:
            root = self.run_root(root_step)
        except _BudgetExceeded as exc:
            root = self.failed_node(None, root_step, root_step.title, str(exc))
            self.root_id = root.id
        if root.status == "failed":
            return self.finish()
        for step in steps:
            if step is root_step:
                continue
            try:
                self.run_step(step)
            except _BudgetExceeded as exc:
                self.failed_node(self.root_id, step, step.title, str(exc))
            except Exception as exc:  # a broken step must not take the investigation down
                self.failed_node(self.root_id, step, step.title, f"{type(exc).__name__}: {exc}")
        if self.cfg.drill_depth > 0:
            groups = [self.nodes[g] for g in self.drill_candidates]
            for g in self.top_groups(groups, self.cfg.drill_groups):
                self.drill_group(g, 1)
        self.add_untestable()
        return self.finish()

    def run_step(self, step: PlanStep) -> None:
        assert self.root_id is not None
        if step.kind == "decompose" and step.params.get("method") == "margin_bridge":
            self.margin_bridge(step)
        elif step.kind == "decompose":
            mid = step.params.get("metric_id", self.ctx.metric_id)
            parent = self.find_metric_node(mid) or self.nodes[self.root_id]
            self.decompose(parent, mid, [], int(step.params.get("depth", self.cfg.decomposition_depth)), step)
        elif step.kind == "contribution":
            mid = step.params["metric_id"]
            attach = step.params.get("attach_to", mid)
            found = self.find_metric_node(attach)
            if found is None and attach != self.ctx.metric_id:
                self.nodes[self.root_id].notes.append(
                    f"{step.title} was not run: {self.metric_label(attach)} is not in the tree (its parent driver "
                    "explained too little of the change to be decomposed)"
                )
                return
            parent = found or self.nodes[self.root_id]
            pc = parent.contribution_to_parent
            if parent.kind == "driver" and (pc is None or pc.share is None or abs(pc.share) < MODERATE_SHARE):
                parent.notes.append(
                    f"{parent.metric_label} explains little of its parent's change, so it is not broken down by "
                    f"{self.dim_label(step.params['dimension'])} (shares of a near-zero change are not meaningful)"
                )
                return
            notes_before = len(parent.notes)
            group = self.contribution(
                parent,
                mid,
                step.params["dimension"],
                [],
                step,
                min_segments=2,
                rank_by=step.params.get("rank_by"),
                direction=step.params.get("direction", "increase"),
            )
            if group is None and len(parent.notes) == notes_before:
                parent.notes.append(
                    f"{self.dim_label(step.params['dimension'])} has a single value in both periods; "
                    "no breakdown shown"
                )
            if group is not None and group.kind == "dimension" and step.params.get("drill"):
                self.drill_candidates.append(group.id)
        elif step.kind == "compare" and step.params.get("role") == "seasonality":
            self.seasonality(step)
        elif step.kind == "compare" and step.params.get("role") == "premise":
            self.premise(step)
        elif step.kind == "segment" and step.params.get("role") == "inventory_health":
            self.inventory_health(step)
        elif step.kind == "compare":
            self.check(step)
        elif step.kind == "anomaly":
            self.anomaly(step)
        elif step.kind == "segment":
            self.trend(step)
        elif step.kind == "custom_sql":
            self.custom_sql(step)
        elif step.kind == "python":
            self.python(step)
        else:
            self.failed_node(self.root_id, step, step.title, f"unsupported step kind {step.kind!r}")

    def find_metric_node(self, metric_id: str) -> TreeNode | None:
        for nid in self.order:
            n = self.nodes[nid]
            if n.metric_id == metric_id and n.kind in ("root", "driver") and not n.segment_path:
                return n
        return None

    # ------------------------------------------------------------------ root
    def run_root(self, step: PlanStep) -> TreeNode:
        mid = self.ctx.metric_id
        m = self.model.get_metric(mid)
        cur, base, ids, err = self.totals(mid, [])
        base_side = self.baseline_side(mid)
        if err or cur is None or base is None:
            node = self.failed_node(None, step, step.title, err or "no value in one of the periods", ids)
            self.root_id = node.id
            return node
        change = cur - base
        pct = safe_pct(cur, base)
        budget = self.ctx.comparison_kind in ("budget", "forecast")
        chart = charts.comparison_chart(m.display_name, base_side.label, self.ctx.window.display(), base, cur)
        art = self.metric_result(
            f"{m.display_name}: {self.ctx.window.display()} vs {base_side.label}",
            {
                "current": cur,
                "baseline": base,
                "abs_change": change,
                "pct_change": pct,
                "baseline_metric_id": base_side.metric_id,
                "comparison_kind": self.ctx.comparison_kind,
            },
            ids,
            chart=chart,
            filters=list(self.ctx.filters),
            window=self.ctx.window,
            metric_ids=[mid],
        )
        ftxt = ("; filters: " + ", ".join(f.describe() for f in self.ctx.filters)) if self.ctx.filters else ""
        if budget:
            rel = "below" if change < 0 else "above"
            statement = (
                f"{m.display_name} was {fmt_abs_pct(pct)} {rel} "
                f"{self.metric_label(base_side.metric_id)} in {self.ctx.window.display()} "
                f"({fmt_value(cur, m.format)} vs {fmt_value(base, m.format)}, {fmt_change(change, m.format)}){ftxt}"
            )
        elif m.format == "percent":
            statement = (
                f"{m.display_name} {direction_word(change)} {fmt_change(abs(change), m.format)[1:]} "
                f"({fmt_value(base, m.format)} to {fmt_value(cur, m.format)}): "
                f"{self.ctx.window.display()} vs {base_side.label}{ftxt}"
            )
        else:
            statement = (
                f"{m.display_name} {change_phrase(change, pct)} "
                f"({fmt_value(base, m.format)} to {fmt_value(cur, m.format)}, {fmt_change(change, m.format)}): "
                f"{self.ctx.window.display()} vs {base_side.label}{ftxt}"
            )
        if self.ctx.segment_comparison is not None:
            sc = self.ctx.segment_comparison
            statement = (
                f"{m.display_name}, {self.ctx.window.display()}: {self.dim_label(sc.dimension)} = {sc.current} "
                f"{fmt_value(cur, m.format)} vs {sc.baseline} {fmt_value(base, m.format)} "
                f"({fmt_change(change, m.format)}, {fmt_pct(pct)}){ftxt}"
            )
        node = TreeNode(
            id=stable_id(
                "node",
                "root",
                mid,
                self.ctx.window.model_dump(mode="json"),
                base_side.window.model_dump(mode="json"),
                base_side.metric_id,
                [f.model_dump() for f in self.ctx.filters],
            ),
            parent_id=None,
            kind="root",
            statement=statement,
            statement_type="observation",
            metric_id=mid,
            metric_label=m.display_name,
            metric_format=m.format,
            current=cur,
            baseline=base,
            abs_change=change,
            pct_change=pct,
            evidence_strength="strong",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
        )
        self.add_node(node, EvidenceInput(executed=True, measurement_only=True))
        self.root_id = node.id
        if abs(change) < 1e-12:
            node.notes.append("no change between the periods; there is nothing to attribute")
        return node

    # ------------------------------------------------------------------ decomposition
    def decompose(
        self, parent: TreeNode, metric_id: str, path: list[Segment], depth: int, step: PlanStep | None
    ) -> None:
        if depth <= 0 or parent.current is None or parent.baseline is None:
            return
        edges = driver_edges(self.model, metric_id)
        if not edges:
            return
        if any(
            st.params.get("method") == "margin_bridge" and st.params.get("metric_id") == metric_id
            for st in self.plan.steps
        ):
            return  # Margin / Revenue is definitional; the bridge explains this metric instead
        mult = [(c, r) for c, r in edges if r in ("multiplicative", "ratio_numerator", "ratio_denominator")]
        add = [(c, r) for c, r in edges if r in ("additive", "subtractive")]
        use = mult if mult else add
        if mult and add:
            parent.notes.append(
                "metric tree mixes additive and multiplicative drivers; using the multiplicative identity"
            )
        drivers: list[tuple[str, str, float, float]] = []
        ids: list[str] = []
        for child, rel in use:
            cur, base, qids, err = self.totals(child, path, compare_side=False)
            ids.extend(qids)
            if err or cur is None or base is None:
                self.failed_node(
                    parent.id,
                    step,
                    f"{self.metric_label(child)} for the decomposition of {self.metric_label(metric_id)}",
                    err or "no value",
                    qids,
                )
                return
            drivers.append((child, rel, cur, base))
        outcome = decompose_drivers(parent.current, parent.baseline, drivers)
        if not outcome.valid and any(c <= 0 or b <= 0 for _, rel, c, b in drivers if rel != "additive"):
            # A driver at zero (e.g. no conversions in a segment) is a property of the data,
            # not a failed test: the log decomposition simply does not apply.
            zero = [self.metric_label(mid) for mid, _rel, c, b in drivers if c <= 0 or b <= 0]
            parent.notes.append(
                f"no driver decomposition: {', '.join(zero)} is zero in one of the periods, so the "
                "multiplicative split is undefined"
            )
            return
        if outcome.valid and outcome.identity_current is not None and outcome.identity_baseline is not None:
            scale = max(abs(parent.current), abs(parent.baseline), 1e-9)
            gap = (
                max(
                    abs(outcome.identity_current - parent.current),
                    abs(outcome.identity_baseline - parent.baseline),
                )
                / scale
            )
            if gap > IDENTITY_GAP_LIMIT:
                outcome.valid = False
                outcome.notes.append(
                    f"the drivers do not reproduce {self.metric_label(metric_id)} (identity off by {gap * 100:.1f}%); "
                    "check the metric tree definition"
                )
        if not outcome.valid:
            self.failed_node(
                parent.id,
                step,
                f"decomposition of {self.metric_label(metric_id)}",
                "; ".join(outcome.notes) or "identity undefined",
                ids,
            )
            return
        pm = self.model.get_metric(metric_id)
        steps_chart = [(self.metric_label(e.metric_id), e.effect) for e in outcome.effects]
        if abs(outcome.residual) > 1e-9:
            steps_chart.append(("residual", outcome.residual))
        art = self.metric_result(
            f"Decomposition of {pm.display_name}"
            + (f" [{', '.join(s.label() for s in path)}]" if path else ""),
            {
                "method": outcome.method,
                "parent_current": outcome.parent_current,
                "parent_baseline": outcome.parent_baseline,
                "parent_change": outcome.parent_change,
                "effects": [
                    {
                        "metric_id": e.metric_id,
                        "relation": e.relation,
                        "current": e.current,
                        "baseline": e.baseline,
                        "effect": e.effect,
                        "share": e.share,
                    }
                    for e in outcome.effects
                ],
                "residual": outcome.residual,
                "residual_share": outcome.residual_share,
                "identity_current": outcome.identity_current,
                "identity_baseline": outcome.identity_baseline,
                "notes": outcome.notes,
            },
            ids,
            chart=charts.waterfall_chart(
                f"{pm.display_name} change by driver",
                self.ctx.baseline.display(),
                self.ctx.window.display(),
                outcome.parent_baseline,
                outcome.parent_current,
                steps_chart,
            ),
            filters=self.filters_for(path),
            window=self.ctx.window,
            metric_ids=[metric_id, *[c for c, _ in use]],
        )
        scale = max(abs(outcome.parent_current), abs(outcome.parent_baseline), 1.0)
        gap = 0.0
        if outcome.identity_current is not None and outcome.identity_baseline is not None:
            gap = (
                max(
                    abs(outcome.identity_current - outcome.parent_current),
                    abs(outcome.identity_baseline - outcome.parent_baseline),
                )
                / scale
            )
        corroboration = (
            [
                f"the {' x '.join(self.metric_label(c) for c, _ in use) if outcome.method == 'lmdi' else 'driver sum'} "
                f"identity reproduces {pm.display_name} in both periods (gap {gap * 100:.2f}%)"
            ]
            if gap <= RESIDUAL_TOLERANCE
            else []
        )
        method_label = (
            "exact log-mean (LMDI) decomposition of the metric-tree identity"
            if outcome.method == "lmdi"
            else "exact additive metric-tree identity"
        )
        for e in sorted(outcome.effects, key=lambda e: (-abs(e.share or 0.0), e.metric_id)):
            cm = self.model.get_metric(e.metric_id)
            change = e.current - e.baseline
            pct = safe_pct(e.current, e.baseline)
            if e.share is None:
                tail = "the parent did not change, so no share is attributed"
            elif abs(e.share) < 0.005:
                tail = f"no effect on the {pm.display_name} change"
            elif e.share >= 0:
                tail = f"explains {fmt_share(e.share)} of the {pm.display_name} change"
            else:
                tail = f"offsets {fmt_share(abs(e.share))} of the {pm.display_name} change"
            sign_note = " (subtracted)" if e.relation == "subtractive" else ""
            statement = (
                f"{cm.display_name}{sign_note} {change_phrase(change, pct)} "
                f"({fmt_value(e.baseline, cm.format)} to {fmt_value(e.current, cm.format)}); {tail}"
            )
            node = TreeNode(
                id=stable_id("node", "driver", parent.id, e.metric_id),
                parent_id=parent.id,
                kind="driver",
                statement=statement,
                statement_type="supported_explanation",
                metric_id=e.metric_id,
                metric_label=cm.display_name,
                metric_format=cm.format,
                segment_path=list(path),
                current=e.current,
                baseline=e.baseline,
                abs_change=change,
                pct_change=pct,
                contribution_to_parent=Contribution(effect=e.effect, share=e.share, method=outcome.method),  # type: ignore[arg-type]
                evidence_strength="moderate",
                artifact_ids=[art.id, *ids],
                step_id=step.id if step else None,
            )
            self.add_node(
                node,
                EvidenceInput(
                    executed=True,
                    exact_method=True,
                    method_label=method_label,
                    share=e.share,
                    residual_share=outcome.residual_share,
                    corroborations=list(corroboration),
                    pct_change=pct,
                ),
            )
            if depth > 1 and e.share is not None and abs(e.share) >= MODERATE_SHARE:
                self.decompose(node, e.metric_id, path, depth - 1, step)
        if outcome.residual_share is not None and abs(outcome.residual_share) > RESIDUAL_TOLERANCE:
            node = TreeNode(
                id=stable_id("node", "residual", parent.id),
                parent_id=parent.id,
                kind="other",
                statement=(
                    f"Not explained by the driver identity: {fmt_change(outcome.residual, pm.format)} "
                    f"({fmt_share(abs(outcome.residual_share))} of the change); the drivers do not "
                    f"multiply or add exactly to {pm.display_name}"
                ),
                statement_type="observation",
                metric_id=metric_id,
                segment_path=list(path),
                contribution_to_parent=Contribution(
                    effect=outcome.residual,
                    share=outcome.residual_share,
                    method=outcome.method,  # type: ignore[arg-type]
                ),
                evidence_strength="weak",
                artifact_ids=[art.id],
                step_id=step.id if step else None,
            )
            self.add_node(
                node,
                EvidenceInput(
                    executed=True,
                    measurement_only=True,
                    validation_ok=False,
                    validation_failures=["identity residual above tolerance"],
                ),
            )

    # ------------------------------------------------------------------ contribution
    def contribution(
        self,
        parent: TreeNode,
        metric_id: str,
        dim: str,
        path: list[Segment],
        step: PlanStep | None,
        *,
        min_segments: int = 1,
        rank_by: str | None = None,
        direction: str = "increase",
    ) -> TreeNode | None:
        m = self.model.get_metric(metric_id)
        title = f"{m.display_name} change by {self.dim_label(dim)}"
        ids: list[str] = []
        if parent.metric_id == metric_id:
            total_cur, total_base = parent.current, parent.baseline
        else:
            total_cur, total_base, ids, err = self.totals(metric_id, path)
            if err:
                return self.failed_node(parent.id, step, title, err, ids)
        if total_cur is None or total_base is None:
            return None
        cur_side = self.current_side(metric_id)
        base_side = self.baseline_side(metric_id)
        budget_mode = base_side.metric_id != metric_id
        outcome: ContributionOutcome
        if m.kind == "ratio" and m.numerator and m.denominator and not budget_mode:
            parts = {}
            for key, mid, side in (
                ("nc", m.numerator, cur_side),
                ("dc", m.denominator, cur_side),
                ("nb", m.numerator, base_side),
                ("db", m.denominator, base_side),
            ):
                vals, aid, err = self.by_segment(mid, side.window, dim, path, side.label, side.filters)
                ids.append(aid)
                if vals is None:
                    return self.failed_node(parent.id, step, title, err or "query failed", ids)
                parts[key] = vals
            # The segments must partition the ratio's numerator and denominator (an order spanning
            # two categories is one order in total but one in each category).
            totals: dict[str, float] = {}
            for key, mid, side in (
                ("nc", m.numerator, cur_side),
                ("dc", m.denominator, cur_side),
                ("nb", m.numerator, base_side),
                ("db", m.denominator, base_side),
            ):
                q = self.metric_query(
                    [mid],
                    side.window,
                    filters=[*self.filters_for(path), *side.filters],
                    title=f"{self.metric_label(mid)}"
                    + (f" [{', '.join(s.label() for s in path)}]" if path else "")
                    + f", {side.label}",
                )
                ids.append(q.artifact.id)
                if not q.ok:
                    return self.failed_node(parent.id, step, title, q.error or "query failed", ids)
                totals[key] = _to_float(q.rows[0].get(mid)) or 0.0
            outcome = ratio_contribution(
                parts["nc"],
                parts["dc"],
                parts["nb"],
                parts["db"],
                totals=(totals["nc"], totals["dc"], totals["nb"], totals["db"]),
            )
            if _definitional(outcome):
                parent.notes.append(
                    f"{self.dim_label(dim)} determines the {m.display_name} outcome (every segment's rate is 0 or "
                    f"one constant value), so it cannot explain the change; not broken down"
                )
                return None
            if outcome.total_current is not None:
                gap = abs(outcome.total_current - total_cur) / max(abs(total_cur), 1e-9)
                if gap > 1e-6:
                    outcome.notes.append(
                        f"segment ratio totals differ from the measured {m.display_name} by {gap * 100:.3f}%"
                    )
        else:
            cur_vals, aid1, err1 = self.by_segment(
                cur_side.metric_id, cur_side.window, dim, path, cur_side.label, cur_side.filters
            )
            base_vals, aid2, err2 = self.by_segment(
                base_side.metric_id, base_side.window, dim, path, base_side.label, base_side.filters
            )
            ids.extend([aid1, aid2])
            if cur_vals is None or base_vals is None:
                return self.failed_node(parent.id, step, title, err1 or err2 or "query failed", ids)
            outcome = additive_contribution(cur_vals, base_vals, total_cur, total_base)
            if not outcome.additive_valid and m.kind == "simple" and m.agg in ("avg", "min", "max"):
                outcome.notes = [
                    f"{m.agg} is not additive across segments; segment moves are shown without shares"
                ]
        if len(outcome.rows) < min_segments:
            return None
        ranked = outcome.ranked()
        growth_note = None
        if rank_by == "growth":
            ranked, growth_note = _rank_by_growth(outcome, direction)
            outcome.notes.insert(0, growth_note)
        top = ranked[: self.cfg.top_n]
        rest = ranked[self.cfg.top_n :]
        chart = charts.contribution_chart(title, [(self._seg_label(r.value), r.effect) for r in ranked[:20]])
        art = self.metric_result(
            title + (f" [{', '.join(s.label() for s in path)}]" if path else ""),
            {
                "method": outcome.method,
                "additive_valid": outcome.additive_valid,
                "total_current": outcome.total_current,
                "total_baseline": outcome.total_baseline,
                "total_change": outcome.total_change,
                "notes": outcome.notes,
                "explanatory_power": _disproportionality(outcome),
                "segment_count": len(outcome.rows),
                "other_segment_count": max(0, len(outcome.rows) - self.cfg.top_n),
                "rows": [
                    {
                        "segment": r.value,
                        "baseline_share": r.base_share,
                        "current": r.current,
                        "baseline": r.baseline,
                        "effect": r.effect,
                        "share_of_change": r.share,
                        "mix_effect": r.mix_effect,
                        "rate_effect": r.rate_effect,
                        "pct_change": r.pct_change,
                    }
                    for r in ranked
                ],
            },
            ids,
            chart=chart,
            filters=self.filters_for(path),
            window=self.ctx.window,
            metric_ids=[metric_id],
        )
        exact = outcome.additive_valid
        method_text = {
            "additive": "segments partition the total (verified on the data), so shares sum to 100% within this "
            "dimension",
            "ratio_mix_rate": "ratio change split into rate and mix effects per segment; effects sum exactly to "
            "the total change",
            "non_additive": "segments overlap, so segment moves are shown without shares",
        }[outcome.method]
        topseg = top[0] if top else None
        top_text = ""
        if rank_by == "growth" and topseg is not None:
            word = "fastest decline" if direction == "decrease" else "fastest growth"
            top_text = (
                f"; {word}: {self._seg_label(topseg.value)} ({fmt_pct(topseg.pct_change)}, "
                f"{fmt_value(topseg.baseline, m.format)} to {fmt_value(topseg.current, m.format)})"
            )
        elif topseg is not None and topseg.share is not None:
            verb = "explains" if topseg.share >= 0 else "offsets"
            top_text = f"; largest: {self._seg_label(topseg.value)} ({verb} {fmt_share(abs(topseg.share))} of the change)"
        group = TreeNode(
            id=stable_id("node", "dim", parent.id, metric_id, dim),
            parent_id=parent.id,
            kind="dimension",
            statement=f"{m.display_name} change by {self.dim_label(dim)}: {len(outcome.rows)} segments{top_text}",
            statement_type="observation",
            metric_id=metric_id,
            metric_label=m.display_name,
            metric_format=m.format,
            dimension=dim,
            segment_path=list(path),
            current=outcome.total_current,
            baseline=outcome.total_baseline,
            abs_change=outcome.total_change,
            pct_change=safe_pct(outcome.total_current, outcome.total_baseline),
            evidence_strength="strong",
            artifact_ids=[art.id, *ids],
            step_id=step.id if step else None,
            notes=[method_text, *outcome.notes, "shares are never added across different dimensions"],
            explanatory_power=_disproportionality(outcome),
        )
        self.add_node(group, EvidenceInput(executed=True, measurement_only=True))
        for rank, row in enumerate(top, start=1):
            self._segment_node(group, m, dim, row, outcome, art.id, ids, path, rank, step)
        if rest:
            eff = sum(r.effect for r in rest)
            share = sum(r.share for r in rest if r.share is not None) if exact else None
            other = TreeNode(
                id=stable_id("node", "other", group.id),
                parent_id=group.id,
                kind="other",
                statement=(
                    f"{len(rest)} other {self.dim_label(dim)} segments: {fmt_change(eff, m.format)}"
                    + (f"; {fmt_share(share)} of the change combined" if share is not None else "")
                ),
                statement_type=statement_type_for(executed=True, attribution=share is not None, exact=exact),
                metric_id=metric_id,
                dimension=dim,
                segment_path=list(path),
                contribution_to_parent=Contribution(effect=eff, share=share, method=outcome.method),  # type: ignore[arg-type]
                evidence_strength="weak",
                artifact_ids=[art.id],
                step_id=step.id if step else None,
                rank=len(top) + 1,
            )
            self.add_node(
                other,
                EvidenceInput(
                    executed=True, exact_method=exact, share=share, method_label="grouped remainder"
                ),
            )
        return group

    def _seg_label(self, value: str | None) -> str:
        return "(empty)" if value is None else str(value)

    def _segment_node(
        self,
        group: TreeNode,
        m: Any,
        dim: str,
        row: SegmentRow,
        outcome: ContributionOutcome,
        art_id: str,
        ids: list[str],
        path: list[Segment],
        rank: int,
        step: PlanStep | None,
    ) -> TreeNode:
        seg = Segment(dimension=dim, value=row.value)
        label = f"{self.dim_label(dim)} = {self._seg_label(row.value)}"
        exact = outcome.additive_valid
        pct = row.pct_change
        if outcome.method == "ratio_mix_rate":
            share_txt = (
                ""
                if row.share is None
                else f"; {'explains' if row.share >= 0 else 'offsets'} {fmt_share(abs(row.share))} of the "
                f"{m.display_name} change"
            )
            statement = (
                f"{label}: {m.display_name} {fmt_value(row.baseline, m.format)} to "
                f"{fmt_value(row.current, m.format)} ({fmt_pct(pct)}); rate effect "
                f"{fmt_change(row.rate_effect, m.format)}, mix effect {fmt_change(row.mix_effect, m.format)}"
                f"{share_txt}"
            )
        elif exact:
            if row.share is None:
                share_txt = ""
            elif abs(row.share) < 0.005:
                share_txt = "; no share of the change"
            elif row.share >= 0:
                share_txt = f"; accounts for {fmt_share(row.share)} of the change"
            else:
                share_txt = f"; offsets {fmt_share(abs(row.share))} of the change"
            pct_txt = (
                fmt_pct(pct)
                if pct is not None
                else ("new" if (row.current or 0) and not row.baseline else "n/a")
            )
            statement = f"{label}: {m.display_name} {fmt_change(row.effect, m.format)} ({pct_txt}){share_txt}"
        else:
            statement = (
                f"{label}: {m.display_name} {fmt_value(row.baseline, m.format)} to "
                f"{fmt_value(row.current, m.format)} ({fmt_change(row.effect, m.format)}, {fmt_pct(pct)}); "
                "segments overlap, so no share of the total is claimed"
            )
        cur = row.current
        base = row.baseline
        node = TreeNode(
            id=stable_id("node", "seg", group.id, row.value),
            parent_id=group.id,
            kind="segment",
            statement=statement,
            statement_type=statement_type_for(executed=True, attribution=row.share is not None, exact=exact),
            metric_id=m.id,
            metric_label=m.display_name,
            metric_format=m.format,
            segment=seg,
            segment_path=[*path, seg],
            dimension=dim,
            current=cur,
            baseline=base,
            abs_change=None if cur is None and base is None else (cur or 0.0) - (base or 0.0),
            pct_change=pct,
            contribution_to_parent=Contribution(
                effect=row.effect,
                share=row.share,
                method=outcome.method,  # type: ignore[arg-type]
                mix_effect=row.mix_effect,
                rate_effect=row.rate_effect,
            ),
            evidence_strength="moderate",
            artifact_ids=[art_id, *ids],
            step_id=step.id if step else None,
            rank=rank,
        )
        method_label = {
            "additive": "direct calculation over an additive partition verified on the data",
            "ratio_mix_rate": "exact mix/rate decomposition of the ratio",
            "non_additive": "non-additive metric",
        }[outcome.method]
        return self.add_node(
            node,
            EvidenceInput(
                executed=True,
                exact_method=exact,
                method_label=method_label,
                share=row.share,
                base_share=row.base_share,
                pct_change=pct,
            ),
        )

    # ------------------------------------------------------------------ drill
    def drill_dimensions(self, exclude: set[str]) -> list[str]:
        contrib_dims = [
            s.params["dimension"]
            for s in self.plan.enabled_steps()
            if s.kind == "contribution" and s.params.get("metric_id") == self.ctx.metric_id
        ]
        usable, _ = dimensions_for_metric(self.model, self.ctx.metric_id)
        usable_names = [d.name for d in usable]
        roles_order = {r: i for i, r in enumerate(self.template.drill_roles)}

        def key(name: str) -> tuple[int, int, str]:
            roles = dimension_roles(self.model.get_dimension(name))
            pri = min((roles_order[r] for r in roles if r in roles_order), default=len(roles_order) + 1)
            in_plan = 0 if name in contrib_dims else 1
            return (pri, in_plan, name)

        blocked = set(exclude) | pinned_dimensions(self.model, self.ctx.metric_id)
        if self.ctx.comparison_kind in ("budget", "forecast") and self.ctx.baseline_metric_id:
            base_usable, _ = dimensions_for_metric(self.model, self.ctx.baseline_metric_id)
            reachable = {d.name for d in base_usable}
            blocked |= {n for n in usable_names if n not in reachable}
        cands = [
            n
            for n in usable_names
            if n not in blocked
            and (
                n in contrib_dims
                or any(r in roles_order for r in dimension_roles(self.model.get_dimension(n)))
            )
        ]
        return sorted(cands, key=key)[: self.cfg.drill_dimensions]

    def top_groups(self, groups: list[TreeNode], n: int) -> list[TreeNode]:
        ranked = sorted(
            (
                g
                for g in groups
                if g.explanatory_power is not None and g.explanatory_power >= self.cfg.drill_min_power
            ),
            key=lambda g: (-(g.explanatory_power or 0.0), g.dimension or ""),
        )
        return ranked[:n]

    def drill_group(self, group: TreeNode, depth: int) -> None:
        if depth > self.cfg.drill_depth:
            return
        kids = [self.nodes[c] for c in group.children if self.nodes[c].kind == "segment"]
        eligible = []
        for k in kids:
            c = k.contribution_to_parent
            if c is None or c.share is None or c.method not in ("additive", "ratio_mix_rate"):
                continue
            base_share = self.evidence[k.id].base_share or 0.0
            if c.share >= self.cfg.drill_min_share and c.share - base_share >= 0.05:
                eligible.append(k)
        for seg in eligible[: self.cfg.drill_top_n]:
            self.drill_segment(seg, depth)

    def drill_segment(self, seg: TreeNode, depth: int) -> None:
        mid = seg.metric_id
        if mid is None or seg.segment is None:
            return
        path = list(seg.segment_path)
        try:
            cur, base, ids, err = self.totals(mid, path)
        except _BudgetExceeded:
            seg.notes.append("drill stopped: query budget reached")
            return
        if err or cur is None or base is None:
            seg.notes.append(f"drill not possible: {err or 'no value'}")
            return
        # The segment node holds the segment's own totals, measured by a validated query.
        seg.current, seg.baseline = cur, base
        seg.artifact_ids = [*seg.artifact_ids, *[i for i in ids if i not in seg.artifact_ids]]
        groups: list[TreeNode] = []
        try:
            if self.ctx.comparison_kind not in ("budget", "forecast"):
                self.decompose(seg, mid, path, 1, None)
            for dim in self.drill_dimensions({s.dimension for s in path}):
                group = self.contribution(seg, mid, dim, path, None, min_segments=2)
                if group is not None and group.kind == "dimension":
                    groups.append(group)
        except _BudgetExceeded:
            seg.notes.append("drill stopped: query budget reached")
            return
        informative = [g for g in groups if (g.explanatory_power or 0.0) >= self.cfg.drill_min_power]
        dropped = [g for g in groups if g not in informative]
        if dropped:
            labels = ", ".join(self.dim_label(g.dimension or "") for g in dropped)
            seg.notes.append(
                f"inside this segment, {labels} moved in proportion to segment size (explanatory power below "
                f"{self.cfg.drill_min_power:.2f}); those breakdowns are kept as artifacts but not shown as findings"
            )
            for g in dropped:
                self.remove_subtree(g.id)
        if depth < self.cfg.drill_depth:
            for g in self.top_groups(informative, 1):
                self.drill_group(g, depth + 1)

    def remove_subtree(self, node_id: str) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            return
        for c in list(node.children):
            self.remove_subtree(c)
        if node.parent_id and node.parent_id in self.nodes:
            parent = self.nodes[node.parent_id]
            parent.children = [c for c in parent.children if c != node_id]
        del self.nodes[node_id]
        self.order.remove(node_id)
        self.evidence.pop(node_id, None)

    # ------------------------------------------------------------------ checks
    def check(self, step: PlanStep) -> None:
        assert self.root_id is not None
        mid = step.params["metric_id"]
        m = self.model.get_metric(mid)
        cur, base, ids, err = self.totals(mid, [], compare_side=False)
        if err or cur is None or base is None:
            self.failed_node(self.root_id, step, step.title, err or "no value", ids)
            return
        change = cur - base
        pct = safe_pct(cur, base)
        effect_txt, effect, effect_ids = self._rate_effect_on_root(m, change)
        ids = [*ids, *effect_ids]
        art = self.metric_result(
            step.title,
            {
                "current": cur,
                "baseline": base,
                "abs_change": change,
                "pct_change": pct,
                "effect_on_root": effect,
            },
            ids,
            chart=charts.comparison_chart(
                m.display_name, self.period_baseline().display(), self.ctx.window.display(), base, cur
            ),
            filters=list(self.ctx.filters),
            window=self.ctx.window,
            metric_ids=[mid],
        )
        if m.format == "percent" and abs(change) < 0.0005:
            statement = (
                f"{m.display_name} was essentially unchanged at {fmt_value(cur, m.format)}: "
                f"{self.ctx.window.display()} vs {self.period_baseline().display()}"
            )
        elif m.format == "percent":
            statement = (
                f"{m.display_name} {direction_word(change)} from {fmt_value(base, m.format)} to "
                f"{fmt_value(cur, m.format)} ({fmt_change(change, m.format)}): {self.ctx.window.display()} vs "
                f"{self.period_baseline().display()}"
            )
        else:
            statement = (
                f"{m.display_name} {change_phrase(change, pct)} "
                f"({fmt_value(base, m.format)} to {fmt_value(cur, m.format)}): {self.ctx.window.display()} vs "
                f"{self.period_baseline().display()}"
            )
        node = TreeNode(
            id=stable_id("node", "check", self.root_id, step.id),
            parent_id=self.root_id,
            kind="check",
            statement=statement + effect_txt,
            statement_type="observation",
            metric_id=mid,
            metric_label=m.display_name,
            metric_format=m.format,
            current=cur,
            baseline=base,
            abs_change=change,
            pct_change=pct,
            evidence_strength="strong",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
            notes=[
                f"related check ({step.params.get('category', 'check')}): {step.rationale} This is a measured "
                "fact; any causal link to the root change is not established by this test"
            ],
        )
        self.add_node(node, EvidenceInput(executed=True, measurement_only=True))

    def _rate_effect_on_root(self, m: Any, change: float) -> tuple[str, float | None, list[str]]:
        """Revenue effect of a rate change: for a rate R = N / D where D = root + N (discount rate
        over list-price revenue, list = revenue + discounts), the root moves by -dR x D_current
        at the current list-price volume."""
        if m.kind != "ratio" or not m.numerator or not m.denominator:
            return "", None, []
        root_id = self.ctx.metric_id
        parts = {c for c, r in driver_edges(self.model, m.denominator) if r == "additive"}
        if parts != {root_id, m.numerator}:
            return "", None, []
        dcur, _db, ids, err = self.totals(m.denominator, [], compare_side=False)
        if err or dcur is None:
            return "", None, ids
        effect = -change * dcur
        rm = self.model.get_metric(root_id)
        text = (
            f"; at the current {self.metric_label(m.denominator)} of {fmt_value(dcur, 'currency')}, that rate change "
            f"is worth {fmt_change(effect, rm.format)} of {rm.display_name}"
        )
        return text, effect, ids

    def seasonality(self, step: PlanStep) -> None:
        assert self.root_id is not None
        mid = step.params["metric_id"]
        m = self.model.get_metric(mid)
        root = self.nodes[self.root_id]
        prior_cur = previous_period(self.ctx.window, "yoy")
        prior_base = previous_period(self.ctx.baseline, "yoy")
        filters = list(self.ctx.filters)
        c = self.metric_query(
            [mid], prior_cur, filters=filters, title=f"{m.display_name}, {prior_cur.display()}"
        )
        b = self.metric_query(
            [mid], prior_base, filters=filters, title=f"{m.display_name}, {prior_base.display()}"
        )
        ids = [c.artifact.id, b.artifact.id]
        no_data = all(
            o.error is None or "value_not_null" in o.error or "non_empty" in o.error or "not_empty" in o.error
            for o in (c, b)
        ) and any(not o.rows or o.rows[0].get(mid) is None for o in (c, b))
        if no_data:
            root.notes.append(
                f"seasonality check skipped: the data has no {m.display_name} for "
                f"{prior_base.display()} and {prior_cur.display()}"
            )
            return
        if not c.ok or not b.ok:
            self.failed_node(self.root_id, step, step.title, c.error or b.error or "query failed", ids)
            return
        cv, bv = _to_float(c.rows[0].get(mid)), _to_float(b.rows[0].get(mid))
        if cv is None or bv is None:
            root.notes.append("seasonality check skipped: no prior-year values")
            return
        prior_pct = safe_pct(cv, bv)
        now_pct = root.pct_change
        art = self.metric_result(
            step.title,
            {
                "prior_current": cv,
                "prior_baseline": bv,
                "prior_pct_change": prior_pct,
                "current_pct_change": now_pct,
            },
            ids,
            chart=None,
            filters=filters,
            window=prior_cur,
            metric_ids=[mid],
        )
        similar = (
            prior_pct is not None
            and now_pct is not None
            and prior_pct * now_pct > 0
            and abs(prior_pct) >= 0.5 * abs(now_pct)
        )
        verdict = (
            "last year moved the same way by a similar amount, which is consistent with a seasonal pattern"
            if similar
            else "last year's move over the same months was different, so this change is not simply the "
            "same seasonal pattern repeating (one prior year only)"
        )
        statement = (
            f"Same months a year earlier: {m.display_name} changed {fmt_pct(prior_pct)} ({prior_base.display()} to "
            f"{prior_cur.display()}) vs {fmt_pct(now_pct)} now; {verdict}"
        )
        node = TreeNode(
            id=stable_id("node", "check", self.root_id, step.id),
            parent_id=self.root_id,
            kind="check",
            statement=statement,
            statement_type="observation",
            metric_id=mid,
            metric_label=m.display_name,
            metric_format=m.format,
            current=cv,
            baseline=bv,
            abs_change=cv - bv,
            pct_change=prior_pct,
            evidence_strength="strong",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
        )
        self.add_node(
            node,
            EvidenceInput(
                executed=True,
                measurement_only=True,
                max_strength="moderate",
                cap_reason="one prior-year comparison cannot establish or rule out seasonality",
            ),
        )

    # ------------------------------------------------------------------ margin bridge
    def margin_bridge(self, step: PlanStep) -> None:
        """Exact counterfactual bridge of a margin change into unit cost, discounting and the rest.

        Per item i (e.g. SKU) with volume q, revenue R, cost C and list-price revenue G:
        C' = sum_i q1_i * (C0_i / q0_i) is the current volume at baseline unit costs, and
        R' = sum_i G1_i * (R0_i / G0_i) the current list-price sales at baseline discount rates.
        With M(C, R) the margin at cost C and revenue R (other costs held at their current value):
        unit cost effect = M(C1, R1) - M(C', R1), discount effect = M(C', R1) - M(C', R'),
        and mix / list price / other = M(C', R') - M0. The three add up to M1 - M0 exactly.
        Items with no baseline volume (or list price) keep their current cost (or revenue), so
        new products count in the mix effect, not as cost inflation.
        """
        assert self.root_id is not None
        root = self.nodes[self.root_id]
        p = step.params
        mid = p["metric_id"]
        m = self.model.get_metric(mid)
        if root.current is None or root.baseline is None:
            return
        rate = p["form"] == "rate"
        item, group = p["item_dimension"], p.get("group_dimension")
        dims = [item] + ([group] if group else [])
        metrics = [p["revenue"], p["cost"], p["volume"]] + ([p["gross"]] if p.get("gross") else [])
        sides = (self.current_side(mid), self.baseline_side(mid))
        rows: list[list[dict[str, Any]]] = []
        ids: list[str] = []
        for side in sides:
            out = self.metric_query(
                metrics,
                side.window,
                dims=dims,
                filters=[*self.ctx.filters, *side.filters],
                title=f"{', '.join(self.metric_label(x) for x in metrics)} by {self.dim_label(item)}, {side.label}",
                expect="many",
            )
            ids.append(out.artifact.id)
            if not out.ok:
                self.failed_node(self.root_id, step, step.title, out.error or "query failed", ids)
                return
            rows.append(out.rows)
        rev, cost, vol, gross = p["revenue"], p["cost"], p["volume"], p.get("gross")

        def index(rs: list[dict[str, Any]]) -> dict[str | None, dict[str, Any]]:
            return {_seg_key(r.get(item)): r for r in rs}

        cur, base = index(rows[0]), index(rows[1])
        r1 = sum(_to_float(r.get(rev)) or 0.0 for r in cur.values())
        c1 = sum(_to_float(r.get(cost)) or 0.0 for r in cur.values())
        r0 = sum(_to_float(r.get(rev)) or 0.0 for r in base.values())
        c0 = sum(_to_float(r.get(cost)) or 0.0 for r in base.values())
        if abs(r1) < 1e-9 or abs(r0) < 1e-9:
            self.failed_node(self.root_id, step, step.title, "revenue is zero in one period", ids)
            return
        m1, m0 = root.current, root.baseline
        # other costs below the bridged cost (freight, commission ...) held at current values
        other1 = (r1 - c1 - m1 * r1) if rate else (r1 - c1 - m1)

        def margin(c: float, r: float) -> float:
            return (r - c - other1) / r if rate else r - c - other1

        gap = abs(margin(c1, r1) - m1) / max(abs(m1), 1e-9)
        if gap > 0.001:
            self.failed_node(
                self.root_id,
                step,
                step.title,
                f"per-item totals do not reproduce {m.display_name} (off by {gap * 100:.2f}%), so the bridge "
                "would not be exact",
                ids,
            )
            return
        cost_cf: dict[str | None, float] = {}
        rev_cf: dict[str | None, float] = {}
        for k, r in cur.items():
            q1, cc1 = _to_float(r.get(vol)) or 0.0, _to_float(r.get(cost)) or 0.0
            b = base.get(k)
            q0 = _to_float(b.get(vol)) if b else None
            cc0 = _to_float(b.get(cost)) if b else None
            cost_cf[k] = q1 * (cc0 / q0) if q0 and q0 > 0 and cc0 is not None and q1 > 0 else cc1
            rr1 = _to_float(r.get(rev)) or 0.0
            if gross:
                g1 = _to_float(r.get(gross)) or 0.0
                g0 = _to_float(b.get(gross)) if b else None
                rr0 = _to_float(b.get(rev)) if b else None
                rev_cf[k] = g1 * (rr0 / g0) if g0 and g0 > 0 and rr0 is not None and g1 > 0 else rr1
            else:
                rev_cf[k] = rr1
        c_cf = sum(cost_cf.values())
        r_cf = sum(rev_cf.values())
        cost_eff = margin(c1, r1) - margin(c_cf, r1)
        disc_eff = (margin(c_cf, r1) - margin(c_cf, r_cf)) if gross else 0.0
        total = m1 - m0
        rest = total - cost_eff - disc_eff

        # By group: each group's part of the cost and discount effects (they add up within each effect).
        def by_group(values: dict[str | None, float], actual_key: str) -> dict[str | None, float]:
            out: dict[str | None, float] = {}
            if not group:
                return out
            for k, r in cur.items():
                g = _seg_key(r.get(group))
                actual = _to_float(r.get(actual_key)) or 0.0
                out[g] = out.get(g, 0.0) + (values[k] - actual)
            return out

        cost_groups = by_group(cost_cf, cost)  # C'_g - C1_g, dollars
        disc_groups = by_group(rev_cf, rev)  # R'_g - R1_g, dollars
        unit_cost_growth: dict[str | None, float | None] = {}
        if group:
            for g in cost_groups:
                actual = sum(
                    _to_float(r.get(cost)) or 0.0 for r in cur.values() if _seg_key(r.get(group)) == g
                )
                cf = sum(cost_cf[k] for k, r in cur.items() if _seg_key(r.get(group)) == g)
                unit_cost_growth[g] = (actual / cf - 1) if cf else None

        corroborations: dict[str, list[str]] = {"cost": [], "discount": []}
        uc_metric = p.get("unit_cost_metric")
        if uc_metric:
            ucur, ubase, uids, uerr = self.totals(uc_metric, [], compare_side=False)
            ids.extend(uids)
            if not uerr and ucur is not None and ubase is not None and ubase:
                uc_pct = ucur / ubase - 1
                if uc_pct * (c1 - c_cf) > 0:
                    corroborations["cost"].append(
                        f"recorded {self.metric_label(uc_metric)} moved {fmt_pct(uc_pct)} over the same periods "
                        "(separate query on the cost table)"
                    )
        d_rate = p.get("discount_rate")
        if d_rate and gross:
            dcur, dbase, dids, derr = self.totals(d_rate, [], compare_side=False)
            ids.extend(dids)
            if not derr and dcur is not None and dbase is not None and (dcur - dbase) * (r1 - r_cf) < 0:
                corroborations["discount"].append(
                    f"{self.metric_label(d_rate)} moved {fmt_change(dcur - dbase, 'percent')} "
                    f"({fmt_value(dbase, 'percent')} to {fmt_value(dcur, 'percent')}; separate query)"
                )
        data = {
            "method": "counterfactual_bridge",
            "form": p["form"],
            "item_dimension": item,
            "group_dimension": group,
            "revenue_current": r1,
            "revenue_baseline": r0,
            "cost_current": c1,
            "cost_baseline": c0,
            "cost_at_baseline_unit_costs": c_cf,
            "revenue_at_baseline_discount_rates": r_cf if gross else None,
            "margin_current": m1,
            "margin_baseline": m0,
            "unit_cost_effect": cost_eff,
            "discount_effect": disc_eff if gross else None,
            "mix_and_other_effect": rest,
            "total_change": total,
            "unit_cost_growth_by_group": {str(k): v for k, v in unit_cost_growth.items()},
            "cost_effect_by_group": {str(k): v for k, v in cost_groups.items()},
            "discount_effect_by_group": {str(k): v for k, v in disc_groups.items()},
        }
        fmt = m.format
        art = self.metric_result(
            f"{m.display_name} bridge: unit cost, discounting, mix",
            data,
            ids,
            chart=charts.waterfall_chart(
                f"{m.display_name} change: unit cost, discounting, mix",
                self.baseline_side(mid).label,
                self.ctx.window.display(),
                m0,
                m1,
                [
                    ("unit cost", cost_eff),
                    *([("discounting", disc_eff)] if gross else []),
                    ("mix and other", rest),
                ],
            ),
            filters=list(self.ctx.filters),
            window=self.ctx.window,
            metric_ids=[mid, *metrics],
        )
        method_label = (
            "exact counterfactual bridge per "
            f"{self.dim_label(item)} (unit costs, then discount rates held at their baseline values)"
        )

        def effect_text(eff: float) -> str:
            share = safe_share(eff, total)
            if share is None:
                return ""
            verb = "explains" if share >= 0 else "offsets"
            return f"; {verb} {fmt_share(abs(share))} of the {m.display_name} change"

        def effect_word(eff: float) -> str:
            return (
                "lowered" if eff < 0 else "raised"
            ) + f" {m.display_name} by {fmt_change(abs(eff), fmt)[1:]}"

        cm = self.model.get_metric(cost)
        cost_node = TreeNode(
            id=stable_id("node", "bridge", self.root_id, "unit_cost"),
            parent_id=self.root_id,
            kind="driver",
            statement=(
                f"Unit cost changes {effect_word(cost_eff)}: at the same {self.dim_label(item)} and volumes, "
                f"{cm.display_name} was {fmt_value(c1, cm.format)} vs {fmt_value(c_cf, cm.format)} at baseline "
                f"unit costs ({fmt_pct(c1 / c_cf - 1 if c_cf else None)}){effect_text(cost_eff)}"
            ),
            statement_type="supported_explanation",
            metric_id=cost,
            metric_label="Unit cost",
            metric_format=cm.format,
            current=c1,
            baseline=c_cf,
            abs_change=c1 - c_cf,
            pct_change=safe_pct(c1, c_cf),
            contribution_to_parent=Contribution(
                effect=cost_eff, share=safe_share(cost_eff, total), method="additive_identity"
            ),
            evidence_strength="moderate",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
            notes=[
                "counterfactual: current volumes of each item priced at that item's baseline unit cost; items "
                "without baseline sales keep their current cost"
            ],
        )
        self.add_node(
            cost_node,
            EvidenceInput(
                executed=True,
                method_label=method_label,
                share=safe_share(cost_eff, total),
                corroborations=corroborations["cost"],
            ),
        )
        self._bridge_groups(
            cost_node, group, cost_groups, rate, r1, cost_eff, art.id, step, unit_cost_growth, "cost"
        )
        if gross:
            gm_ = self.model.get_metric(d_rate) if d_rate else self.model.get_metric(p["discount"])
            d1 = 1 - r1 / sum(_to_float(r.get(gross)) or 0.0 for r in cur.values()) if cur else None
            g0_total = sum(_to_float(r.get(gross)) or 0.0 for r in base.values())
            d0 = 1 - r0 / g0_total if g0_total else None
            disc_node = TreeNode(
                id=stable_id("node", "bridge", self.root_id, "discount"),
                parent_id=self.root_id,
                kind="driver",
                statement=(
                    f"Discounting {effect_word(disc_eff)}: at baseline discount rates the same list-price sales "
                    f"would have brought {fmt_value(r_cf, 'currency')} instead of {fmt_value(r1, 'currency')} "
                    f"(overall discount rate {fmt_value(d0, 'percent')} to {fmt_value(d1, 'percent')})"
                    f"{effect_text(disc_eff)}"
                ),
                statement_type="supported_explanation",
                metric_id=gm_.id,
                metric_label="Discounting",
                metric_format="percent",
                current=d1,
                baseline=d0,
                abs_change=(d1 - d0) if d1 is not None and d0 is not None else None,
                pct_change=safe_pct(d1, d0),
                contribution_to_parent=Contribution(
                    effect=disc_eff, share=safe_share(disc_eff, total), method="additive_identity"
                ),
                evidence_strength="moderate",
                artifact_ids=[art.id, *ids],
                step_id=step.id,
                notes=[
                    "counterfactual: each item's current list-price sales at that item's baseline discount rate"
                ],
            )
            self.add_node(
                disc_node,
                EvidenceInput(
                    executed=True,
                    method_label=method_label,
                    share=safe_share(disc_eff, total),
                    corroborations=corroborations["discount"],
                ),
            )
            self._bridge_groups(
                disc_node, group, disc_groups, rate, r1, disc_eff, art.id, step, None, "discount"
            )
        rest_node = TreeNode(
            id=stable_id("node", "bridge", self.root_id, "rest"),
            parent_id=self.root_id,
            kind="other",
            statement=(
                f"Product mix, list prices and other effects {effect_word(rest)}{effect_text(rest)} "
                "(what remains after unit cost and discounting are held at baseline)"
            ),
            statement_type="observation",
            metric_id=mid,
            metric_label="Mix and other",
            metric_format=fmt,
            contribution_to_parent=Contribution(
                effect=rest, share=safe_share(rest, total), method="additive_identity"
            ),
            evidence_strength="moderate",
            artifact_ids=[art.id],
            step_id=step.id,
            notes=[
                "a remainder, not a single measured cause; break it down by product category or tier to see mix"
            ],
        )
        self.add_node(
            rest_node,
            EvidenceInput(
                executed=True,
                method_label="remainder of the exact bridge",
                share=safe_share(rest, total),
                max_strength="moderate",
            ),
        )

    def _bridge_groups(
        self,
        parent: TreeNode,
        group: str | None,
        effects_dollars: dict[str | None, float],
        rate: bool,
        revenue: float,
        parent_effect: float,
        art_id: str,
        step: PlanStep,
        growth: dict[str | None, float | None] | None,
        what: str,
    ) -> None:
        """Split one bridge effect across product groups (the parts add up to the effect)."""
        if not group or not effects_dollars or abs(parent_effect) < 1e-12:
            return
        m = self.model.get_metric(self.ctx.metric_id)
        effects = {g: (v / revenue if rate else v) for g, v in effects_dollars.items()}
        if what == "cost":
            # C' - C1 lowers cost when negative; the margin effect of a group is (C'_g - C1_g) [/ R1].
            pass
        else:
            # R' - R1 > 0 means baseline discounts would have earned more: margin effect is -(R'_g - R1_g) adj.
            effects = {g: -v for g, v in effects.items()}
            if rate:
                # With revenue in the denominator the exact split scales each group's revenue gap by
                # the parent effect over the summed gaps, so the groups still add up exactly.
                tot = sum(effects.values())
                effects = {g: (v / tot * parent_effect if tot else 0.0) for g, v in effects.items()}
        ranked = sorted(effects.items(), key=lambda kv: (-abs(kv[1]), str(kv[0])))
        group_node = TreeNode(
            id=stable_id("node", "dim", parent.id, what, group),
            parent_id=parent.id,
            kind="dimension",
            statement=f"{parent.metric_label} by {self.dim_label(group)}: {len(ranked)} segments",
            statement_type="observation",
            metric_id=parent.metric_id,
            metric_label=parent.metric_label,
            metric_format=m.format,
            dimension=group,
            evidence_strength="strong",
            artifact_ids=[art_id],
            step_id=step.id,
            notes=["segment effects add up to the parent effect"],
        )
        self.add_node(group_node, EvidenceInput(executed=True, measurement_only=True))
        for rank, (g, eff) in enumerate(ranked[: self.cfg.top_n], start=1):
            share = safe_share(eff, parent_effect)
            grow = growth.get(g) if growth else None
            grow_txt = f"unit cost {fmt_pct(grow)} at the same items; " if grow is not None else ""
            seg = Segment(dimension=group, value=g)
            node = TreeNode(
                id=stable_id("node", "seg", group_node.id, g),
                parent_id=group_node.id,
                kind="segment",
                statement=(
                    f"{self.dim_label(group)} = {self._seg_label(g)}: {grow_txt}{fmt_change(eff, m.format)} of "
                    f"{m.display_name}"
                    + (f" ({fmt_share(abs(share))} of this effect)" if share is not None else "")
                ),
                statement_type="supported_explanation",
                metric_id=parent.metric_id,
                metric_label=parent.metric_label,
                metric_format=m.format,
                segment=seg,
                segment_path=[seg],
                dimension=group,
                current=grow,
                contribution_to_parent=Contribution(effect=eff, share=share, method="additive_identity"),
                evidence_strength="moderate",
                artifact_ids=[art_id],
                step_id=step.id,
                rank=rank,
            )
            self.add_node(
                node,
                EvidenceInput(
                    executed=True, method_label="exact split of the bridge effect by segment", share=share
                ),
            )

    # ------------------------------------------------------------------ premise
    def premise(self, step: PlanStep) -> None:
        """Measure a premise of the question ("revenue was roughly flat") over the analysis periods."""
        assert self.root_id is not None
        mid = step.params["metric_id"]
        m = self.model.get_metric(mid)
        cur, base, ids, err = self.totals(mid, [], compare_side=False)
        if err or cur is None or base is None:
            self.failed_node(self.root_id, step, step.title, err or "no value", ids)
            return
        change = cur - base
        pct = safe_pct(cur, base)
        tol = float(step.params.get("tolerance", 0.02))
        expectation = step.params.get("expectation", "flat")
        rel = pct if pct is not None else 0.0
        holds = {"flat": abs(rel) <= tol, "increase": rel > tol, "decrease": rel < -tol}[expectation]
        pb = self.period_baseline()
        verdict = (
            "the premise holds"
            if holds
            else "the premise does NOT hold for these periods; the explanation below is for the change as measured"
        )
        crit = {
            "flat": f"within ±{tol * 100:.0f}%",
            "increase": f"up more than {tol * 100:.0f}%",
            "decrease": f"down more than {tol * 100:.0f}%",
        }[expectation]
        art = self.metric_result(
            step.title,
            {
                "current": cur,
                "baseline": base,
                "abs_change": change,
                "pct_change": pct,
                "expectation": expectation,
                "tolerance": tol,
                "holds": holds,
            },
            ids,
            chart=charts.comparison_chart(m.display_name, pb.display(), self.ctx.window.display(), base, cur),
            filters=list(self.ctx.filters),
            window=self.ctx.window,
            metric_ids=[mid],
        )
        node = TreeNode(
            id=stable_id("node", "check", self.root_id, step.id),
            parent_id=self.root_id,
            kind="check",
            statement=(
                f"Premise check ('{step.params.get('text', '')}'): {m.display_name} {change_phrase(change, pct)} "
                f"({fmt_value(base, m.format)} to {fmt_value(cur, m.format)}), {self.ctx.window.display()} vs "
                f"{pb.display()}; flat means {crit}: {verdict}"
            ),
            statement_type="observation",
            metric_id=mid,
            metric_label=m.display_name,
            metric_format=m.format,
            current=cur,
            baseline=base,
            abs_change=change,
            pct_change=pct,
            evidence_strength="strong",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
            notes=[f"premise {'holds' if holds else 'contradicted'}"],
        )
        if expectation != "flat":
            node.statement = node.statement.replace(f"flat means {crit}", f"'{expectation}' means {crit}")
        self.add_node(node, EvidenceInput(executed=True, measurement_only=True))
        if not holds:
            self.nodes[self.root_id].notes.append(
                f"the question assumes '{step.params.get('text', '')}', but {m.display_name} "
                f"{change_phrase(change, pct)} over these periods"
            )

    # ------------------------------------------------------------------ inventory health
    def inventory_health(self, step: PlanStep) -> None:
        """Slow movers at the latest snapshot: items with days of supply above the threshold or
        stock with no usage, their share of inventory, and of the change since the baseline."""
        assert self.root_id is not None
        p = step.params
        inv, dos, usage, item = p["metric_id"], p["days_metric"], p["usage_metric"], p["item_dimension"]
        label_dim = p.get("label_dimension")
        threshold = float(p.get("threshold_days", 180))
        dims = [item] + ([label_dim] if label_dim else [])
        root = self.nodes[self.root_id]
        cur_side, base_side = self.current_side(inv), self.baseline_side(inv)
        outs = []
        for side in (cur_side, base_side):
            out = self.metric_query(
                [inv, usage],
                side.window,
                dims=dims,
                filters=[*self.ctx.filters, *side.filters],
                title=f"{self.metric_label(inv)} and {self.metric_label(usage)} by {self.dim_label(item)}, {side.label}",
                expect="many",
            )
            if not out.ok:
                self.failed_node(
                    self.root_id, step, step.title, out.error or "query failed", [out.artifact.id]
                )
                return
            outs.append(out)
        cur_rows = {_seg_key(r.get(item)): r for r in outs[0].rows}
        base_rows = {_seg_key(r.get(item)): r for r in outs[1].rows}
        total_cur = sum(_to_float(r.get(inv)) or 0.0 for r in cur_rows.values())
        total_base = sum(_to_float(r.get(inv)) or 0.0 for r in base_rows.values())
        flagged = []
        for k, r in cur_rows.items():
            value = _to_float(r.get(inv)) or 0.0
            use = _to_float(r.get(usage)) or 0.0
            if value <= 0:
                continue
            days = value / use if use > 0 else None
            if days is None or days > threshold:
                b = _to_float(base_rows.get(k, {}).get(inv)) or 0.0
                flagged.append((k, value, days, b, r.get(label_dim) if label_dim else None))
        flagged.sort(key=lambda x: (-x[1], str(x[0])))
        flagged_value = sum(f[1] for f in flagged)
        flagged_base = sum(f[3] for f in flagged)
        change = total_cur - total_base
        share_of_change = safe_share(flagged_value - flagged_base, change)
        ids = [o.artifact.id for o in outs]
        art = self.metric_result(
            step.title,
            {
                "threshold_days": threshold,
                "item_dimension": item,
                "total_current": total_cur,
                "total_baseline": total_base,
                "flagged_count": len(flagged),
                "flagged_value": flagged_value,
                "flagged_value_baseline": flagged_base,
                "flagged_share_of_inventory": (flagged_value / total_cur) if total_cur else None,
                "flagged_share_of_change": share_of_change,
                "items": [
                    {"item": k, "label": lbl, "inventory_value": v, "days_of_supply": d, "baseline_value": b}
                    for k, v, d, b, lbl in flagged[:200]
                ],
            },
            ids,
            chart=charts.contribution_chart(
                f"Slow-moving {self.dim_label(item)} by {self.metric_label(inv)}",
                [(str(lbl or k), v) for k, v, _d, _b, lbl in flagged[:20]],
            ),
            filters=list(self.ctx.filters),
            window=self.ctx.window,
            metric_ids=[inv, usage, dos],
        )
        im = self.model.get_metric(inv)
        no_sales = sum(1 for f in flagged if f[2] is None)
        share_txt = (
            f"; they account for {fmt_share(share_of_change)} of the {fmt_change(change, im.format)} change since "
            f"{base_side.label}"
            if share_of_change is not None
            else ""
        )
        header = TreeNode(
            id=stable_id("node", "health", self.root_id, item),
            parent_id=self.root_id,
            kind="dimension",
            statement=(
                f"{len(flagged)} {self.dim_label(item)} values are slow-moving ({no_sales} with stock but no usage, the "
                f"rest over {threshold:.0f} days of supply): {fmt_value(flagged_value, im.format)}, "
                f"{fmt_share(flagged_value / total_cur if total_cur else None)} of {im.display_name} at the latest "
                f"snapshot in {cur_side.label}{share_txt}"
            ),
            statement_type="observation",
            metric_id=inv,
            metric_label=im.display_name,
            metric_format=im.format,
            dimension=item,
            current=flagged_value,
            baseline=flagged_base,
            abs_change=flagged_value - flagged_base,
            pct_change=safe_pct(flagged_value, flagged_base),
            evidence_strength="strong",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
            notes=[
                f"slow-moving: {self.metric_label(inv)} / {self.metric_label(usage)} above {threshold:.0f} days, or "
                f"stock with zero {self.metric_label(usage)}; {self.metric_label(inv)} is read at the last snapshot "
                "of each period (semi-additive)"
            ],
        )
        self.add_node(header, EvidenceInput(executed=True, measurement_only=True))
        for rank, (k, v, days, b, lbl) in enumerate(flagged[: self.cfg.top_n], start=1):
            seg = Segment(dimension=item, value=k)
            days_txt = "no usage in the window" if days is None else f"{days:,.0f} days of supply"
            node = TreeNode(
                id=stable_id("node", "seg", header.id, k),
                parent_id=header.id,
                kind="segment",
                statement=(
                    f"{self.dim_label(item)} = {k}{f' ({lbl})' if lbl else ''}: {fmt_value(v, im.format)} on hand, "
                    f"{days_txt} (was {fmt_value(b, im.format)})"
                ),
                statement_type="observation",
                metric_id=inv,
                metric_label=im.display_name,
                metric_format=im.format,
                segment=seg,
                segment_path=[seg],
                dimension=item,
                current=v,
                baseline=b,
                abs_change=v - b,
                pct_change=safe_pct(v, b),
                evidence_strength="strong",
                artifact_ids=[art.id, *ids],
                step_id=step.id,
                rank=rank,
            )
            self.add_node(node, EvidenceInput(executed=True, measurement_only=True))
        root.notes.append(
            f"{len(flagged)} slow-moving {self.dim_label(item)} values hold {fmt_value(flagged_value, im.format)}"
        )

    def anomaly(self, step: PlanStep) -> None:
        assert self.root_id is not None
        mid = step.params["metric_id"]
        m = self.model.get_metric(mid)
        tdim = time_dimension_for_metric(self.model, mid)
        lookback = int(step.params.get("lookback_days", 56))
        day = self.ctx.window.start
        start = day - dt.timedelta(days=lookback)
        window = TimeWindow(
            start=start,
            end=self.ctx.window.end,
            kind="custom",
            label=f"{start.isoformat()} to {day.isoformat()}",
        )
        ref = f"{tdim}__day"
        out = self.metric_query(
            [mid],
            window,
            dims=[ref],
            filters=list(self.ctx.filters),
            title=f"{m.display_name} by day, {window.display()}",
            expect="many",
        )
        if not out.ok:
            self.failed_node(self.root_id, step, step.title, out.error or "query failed", [out.artifact.id])
            return
        series: dict[dt.date, float] = {}
        for r in out.rows:
            d = r.get(ref)
            if isinstance(d, dt.datetime):
                d = d.date()
            if isinstance(d, str):
                d = dt.date.fromisoformat(d[:10])
            if isinstance(d, dt.date):
                series[d] = _to_float(r.get(mid)) or 0.0
        fill_zero = m.kind == "simple" and m.agg in ("sum", "count", "count_distinct")
        history = []
        for i in range(lookback, 0, -1):
            d = day - dt.timedelta(days=i)
            if d in series:
                history.append(series[d])
            elif fill_zero:
                history.append(0.0)
        value = series.get(day, 0.0 if fill_zero else None)
        if value is None or len(history) < 14:
            self.failed_node(
                self.root_id, step, step.title, "not enough daily history to judge the day", [out.artifact.id]
            )
            return
        med = statistics.median(history)
        mad = statistics.median(abs(h - med) for h in history)
        robust_z = None if mad == 0 else 0.6745 * (value - med) / mad
        unusual = robust_z is not None and abs(robust_z) >= 3.0
        labels = [(day - dt.timedelta(days=i)).isoformat() for i in range(lookback, -1, -1)]
        vals = [series.get(dt.date.fromisoformat(x), 0.0 if fill_zero else None) for x in labels]
        art = self.metric_result(
            step.title,
            {"value": value, "median": med, "mad": mad, "robust_z": robust_z, "history_days": len(history)},
            [out.artifact.id],
            chart=charts.line_chart(f"{m.display_name} by day", labels, vals, day.isoformat()),
            filters=list(self.ctx.filters),
            window=window,
            metric_ids=[mid],
        )
        z_txt = "n/a (no variation in history)" if robust_z is None else f"{robust_z:+.1f}"
        verdict = "unusual (|robust z| >= 3)" if unusual else "within its normal range (|robust z| < 3)"
        statement = (
            f"{m.display_name} on {day.isoformat()} was {fmt_value(value, m.format)} vs a trailing "
            f"{lookback}-day median of {fmt_value(med, m.format)} (robust z {z_txt}): {verdict}"
        )
        node = TreeNode(
            id=stable_id("node", "check", self.root_id, step.id),
            parent_id=self.root_id,
            kind="check",
            statement=statement,
            statement_type="observation",
            metric_id=mid,
            metric_label=m.display_name,
            metric_format=m.format,
            current=value,
            baseline=med,
            abs_change=value - med,
            pct_change=safe_pct(value, med),
            evidence_strength="strong",
            artifact_ids=[art.id, out.artifact.id],
            step_id=step.id,
            notes=[
                "robust z = 0.6745 x (value - median) / MAD over the trailing days; missing days count as zero "
                "for additive metrics"
            ],
        )
        self.add_node(node, EvidenceInput(executed=True, measurement_only=True))

    def trend(self, step: PlanStep) -> None:
        assert self.root_id is not None
        mid = step.params["metric_id"]
        m = self.model.get_metric(mid)
        tdim = step.params.get("time_dimension") or time_dimension_for_metric(self.model, mid)
        periods = int(step.params.get("periods", 12))
        start = add_months(self.ctx.window.start.replace(day=1), -(periods - 1))
        window = TimeWindow(
            start=start,
            end=self.ctx.window.end,
            kind="custom",
            label=f"{start.isoformat()} to {self.ctx.window.last_day.isoformat()}",
        )
        ref = f"{tdim}__month"
        out = self.metric_query(
            [mid],
            window,
            dims=[ref],
            filters=list(self.ctx.filters),
            title=f"{m.display_name} by month, {window.display()}",
            expect="many",
        )
        if not out.ok:
            self.failed_node(self.root_id, step, step.title, out.error or "query failed", [out.artifact.id])
            return
        pts = sorted(((str(r.get(ref))[:10], _to_float(r.get(mid))) for r in out.rows), key=lambda x: x[0])
        vals = [v for _, v in pts if v is not None]
        if not vals:
            self.failed_node(
                self.root_id, step, step.title, "no values in the trailing months", [out.artifact.id]
            )
            return
        hi = max(pts, key=lambda p: p[1] if p[1] is not None else -math.inf)
        lo = min(pts, key=lambda p: p[1] if p[1] is not None else math.inf)
        art = self.metric_result(
            step.title,
            {"points": [{"period": p, "value": v} for p, v in pts]},
            [out.artifact.id],
            chart=charts.line_chart(f"{m.display_name} by month", [p for p, _ in pts], [v for _, v in pts]),
            filters=list(self.ctx.filters),
            window=window,
            metric_ids=[mid],
        )
        statement = (
            f"{m.display_name} by month over {len(pts)} months: high {fmt_value(hi[1], m.format)} "
            f"({hi[0][:7]}), low {fmt_value(lo[1], m.format)} ({lo[0][:7]}), latest "
            f"{fmt_value(pts[-1][1], m.format)} ({pts[-1][0][:7]})"
        )
        node = TreeNode(
            id=stable_id("node", "check", self.root_id, step.id),
            parent_id=self.root_id,
            kind="check",
            statement=statement,
            statement_type="observation",
            metric_id=mid,
            metric_label=m.display_name,
            metric_format=m.format,
            current=pts[-1][1],
            evidence_strength="strong",
            artifact_ids=[art.id, out.artifact.id],
            step_id=step.id,
        )
        self.add_node(node, EvidenceInput(executed=True, measurement_only=True))

    def custom_sql(self, step: PlanStep) -> None:
        assert self.root_id is not None
        if self.query_count >= self.cfg.max_queries:
            raise _BudgetExceeded(f"query budget of {self.cfg.max_queries} reached")
        sql = step.params.get("sql", "")
        try:
            safe = ensure_read_only(sql)
            self.query_count += 1
            res = self.store.execute_read(safe, limit=self.cfg.row_limit, timeout_s=self.cfg.timeout_s)
        except Exception as exc:
            out = self._failed_artifact(step.title, {"sql": sql}, [], self.ctx.window, [], f"{exc}", sql=sql)
            self.failed_node(self.root_id, step, step.title, str(exc), [out.artifact.id])
            return
        checks = [
            ValidationCheck(name="executed", passed=True, detail=f"{res.row_count} rows"),
            ValidationCheck(
                name="non_empty",
                passed=res.row_count > 0,
                detail="" if res.row_count else "the query returned no rows",
            ),
        ]
        art = Artifact(
            id=stable_id("art", "query", safe, canonical_json(res.rows)),
            kind="query",
            title=step.title,
            sql=safe,
            params={"custom": True},
            result=ResultSnapshot(
                columns=[{"name": c.name, "type": c.type} for c in res.columns],
                rows=[list(r) for r in res.rows[: self.cfg.snapshot_rows]],
                row_count=res.row_count,
                truncated=res.truncated or res.row_count > self.cfg.snapshot_rows,
                elapsed_ms=res.elapsed_ms,
            ),
            validation=ValidationSummary(ok=all(c.passed for c in checks), checks=checks),
            warnings=[
                "custom SQL is not governed by the semantic model; metric definitions are not enforced"
            ],
        )
        self.artifacts[art.id] = art
        node = TreeNode(
            id=stable_id("node", "check", self.root_id, step.id),
            parent_id=self.root_id,
            kind="check",
            statement=f"Custom SQL '{step.title}' returned {res.row_count} rows (see the result in the artifact)",
            statement_type="observation",
            evidence_strength="moderate" if res.row_count else "weak",
            evidence_reasons=[],
            artifact_ids=[art.id],
            step_id=step.id,
            notes=["custom SQL is not governed by the semantic model"],
        )
        self.add_node(
            node,
            EvidenceInput(
                executed=True,
                measurement_only=True,
                validation_ok=res.row_count > 0,
                validation_failures=["no rows"] if not res.row_count else [],
            ),
        )

    def python(self, step: PlanStep) -> None:
        assert self.root_id is not None
        code = step.params.get("code", "")
        try:
            result = run_python_sandbox(code, self.cfg.timeout_s, self.store)
        except Exception as exc:
            self.failed_node(self.root_id, step, step.title, str(exc))
            return
        error = getattr(result, "error", None)
        stdout = str(getattr(result, "stdout", "") or "")
        art = Artifact(
            id=stable_id("art", "dataframe", code, stdout, error),
            kind="dataframe",
            title=step.title,
            python=code,
            data={
                "stdout": stdout[:5000],
                "stderr": str(getattr(result, "stderr", "") or "")[:5000],
                "error": error,
            },
            error=error,
            validation=ValidationSummary(
                ok=error is None,
                checks=[ValidationCheck(name="executed", passed=error is None, detail=error or "")],
            ),
        )
        self.artifacts[art.id] = art
        if error:
            self.failed_node(self.root_id, step, step.title, f"Python error: {error}", [art.id])
            return
        node = TreeNode(
            id=stable_id("node", "check", self.root_id, step.id),
            parent_id=self.root_id,
            kind="check",
            statement=f"Python analysis '{step.title}' ran successfully (output in the artifact)",
            statement_type="observation",
            evidence_strength="moderate",
            artifact_ids=[art.id],
            step_id=step.id,
            notes=["Python output is not interpreted automatically; review it before citing"],
        )
        self.add_node(node, EvidenceInput(executed=True, measurement_only=True))

    def add_untestable(self) -> None:
        if self.root_id is None or self.ctx.intent not in ("why_change", "anomaly"):
            return
        for text in self.template.untestable_hypotheses:
            node = TreeNode(
                id=stable_id("node", "hyp", self.root_id, text),
                parent_id=self.root_id,
                kind="hypothesis",
                statement=phrase_hypothesis(text) + " No data in the semantic model can test this.",
                statement_type="hypothesis",
                evidence_strength="hypothesis_only",
            )
            self.add_node(node, EvidenceInput(executed=False))

    # ------------------------------------------------------------------ finish
    def corroborate(self) -> None:
        by_key: dict[tuple[str, str | None, str], list[TreeNode]] = {}
        for n in self.nodes.values():
            if n.kind != "segment" or n.segment is None or n.contribution_to_parent is None:
                continue
            ctx_key = canonical_json([s.model_dump() for s in n.segment_path[:-1]])
            by_key.setdefault((n.segment.dimension, n.segment.value, ctx_key), []).append(n)
        for nodes in by_key.values():
            for n in nodes:
                c = n.contribution_to_parent
                assert c is not None
                if c.share is None or abs(c.share) < MODERATE_SHARE:
                    continue
                for other in nodes:
                    oc = other.contribution_to_parent
                    if other.id == n.id or other.metric_id == n.metric_id or oc is None or oc.share is None:
                        continue
                    if oc.share * c.share > 0 and abs(oc.share) >= MODERATE_SHARE:
                        self.evidence[n.id].corroborations.append(
                            f"the same segment explains {fmt_share(abs(oc.share))} of the "
                            f"{other.metric_label or other.metric_id} change (separate query)"
                        )
        # The same segment with the same metric in an enclosing context (e.g. a customer inside
        # Dallas that is also a top contributor company-wide) corroborates the drilled finding.
        seen_ctx: dict[tuple[str, str | None, str | None], list[TreeNode]] = {}
        for n in self.nodes.values():
            if n.kind == "segment" and n.segment is not None and n.contribution_to_parent is not None:
                seen_ctx.setdefault((n.segment.dimension, n.segment.value, n.metric_id), []).append(n)
        for nodes in seen_ctx.values():
            for n in nodes:
                c = n.contribution_to_parent
                assert c is not None
                if c.share is None or abs(c.share) < MODERATE_SHARE:
                    continue
                for other in nodes:
                    oc = other.contribution_to_parent
                    if other.id == n.id or oc is None or oc.share is None:
                        continue
                    if len(other.segment_path) >= len(n.segment_path):
                        continue
                    if oc.share * c.share > 0 and abs(oc.share) >= MODERATE_SHARE:
                        where = ", ".join(s.label() for s in other.segment_path[:-1]) or "the whole business"
                        self.evidence[n.id].corroborations.append(
                            f"the same segment explains {fmt_share(abs(oc.share))} of the change across {where} "
                            "(separate query)"
                        )
                        break
        # A segment whose drill-down shows the parent's decomposition is corroborated by it.
        for n in self.nodes.values():
            if n.kind != "segment":
                continue
            drivers = [self.nodes[c] for c in n.children if self.nodes[c].kind == "driver"]
            if drivers and self.evidence[n.id].share is not None:
                self.evidence[n.id].corroborations.append(
                    "reproduced by a driver decomposition inside the segment"
                )

    def finish(self) -> ExecutionResult:
        self.corroborate()
        for nid in self.order:
            n = self.nodes[nid]
            hit = sorted({m for a in n.artifact_ids for m in self.immature.get(a, [])})
            if hit:
                ev = self.evidence[nid]
                ev.max_strength = "weak"
                ev.cap_reason = (
                    f"{', '.join(self.metric_label(m) for m in hit)} is still maturing for this period (recent "
                    "cohorts keep converting), so the comparison is provisional"
                )
                note = "provisional: " + ev.cap_reason
                if note not in n.notes:
                    n.notes.append(note)
        for nid in self.order:
            n = self.nodes[nid]
            ev = self.evidence[nid]
            if n.status == "failed":
                n.evidence_strength, n.evidence_reasons = assess(ev)
                continue
            strength, reasons = assess(ev)
            n.evidence_strength = strength
            n.evidence_reasons = reasons
            if (
                n.kind in ("driver", "segment")
                and n.statement_type == "supported_explanation"
                and strength == "weak"
                and not ev.exact_method
            ):
                n.statement_type = "observation"
        # rank children: drivers, dimension groups (by explanatory power), checks, failures, hypotheses
        kind_order: dict[NodeKind, int] = {
            "driver": 0,
            "other": 1,
            "dimension": 2,
            "segment": 2,
            "check": 3,
            "failed": 4,
            "hypothesis": 5,
            "root": 9,
        }
        for n in self.nodes.values():

            def power(cid: str) -> float:
                c = self.nodes[cid]
                if c.kind == "dimension":
                    return c.explanatory_power if c.explanatory_power is not None else -1.0
                if c.contribution_to_parent is not None and c.contribution_to_parent.share is not None:
                    return abs(c.contribution_to_parent.share)
                return 0.0

            def sort_key(cid: str) -> tuple[int, float, int, str]:
                c = self.nodes[cid]
                other_last = 1 if c.kind == "other" else 0
                return (kind_order.get(c.kind, 8), -power(cid), other_last, c.statement)

            if n.kind != "dimension":
                n.children.sort(key=sort_key)
                for i, cid in enumerate(n.children, start=1):
                    if self.nodes[cid].kind in ("driver", "dimension"):
                        self.nodes[cid].rank = i
        tree = InvestigationTree(root_id=self.root_id, nodes=[self.nodes[i] for i in self.order])
        # stable node order: depth-first from root
        tree.nodes = tree.walk() + [n for n in tree.nodes if n.id not in {w.id for w in tree.walk()}]
        return ExecutionResult(
            tree, list(self.artifacts.values()), self.failures, self.query_count, self.notes
        )


def execute(plan: AnalysisPlan, store: Any, model: SemanticModel) -> ExecutionResult:
    """Execute ``plan`` and return the tree, artifacts, failures and query count."""
    return _Executor(plan, store, model).run()


def run(plan: AnalysisPlan, store: Any, model: SemanticModel) -> tuple[InvestigationTree, list[Artifact]]:
    """Contract entry point: ``executor.run(plan, store, model) -> (tree, artifacts)``."""
    res = execute(plan, store, model)
    return res.tree, res.artifacts


def extend_with_contribution(
    tree: InvestigationTree,
    artifacts: list[Artifact],
    plan: AnalysisPlan,
    store: Any,
    model: SemanticModel,
    node_id: str,
    dimension: str,
) -> tuple[InvestigationTree, list[Artifact], list[str]]:
    """Branch from an existing node: contribution of the node's metric by ``dimension``
    within the node's segment path (spec §17 "branch"). Returns the extended tree."""
    ex = _Executor(plan, store, model)
    for n in tree.nodes:
        ex.nodes[n.id] = n.model_copy(deep=True)
        ex.order.append(n.id)
        ex.evidence[n.id] = EvidenceInput(executed=n.status != "failed", measurement_only=True)
    ex.root_id = tree.root_id
    for a in artifacts:
        ex.artifacts[a.id] = a
    node = ex.nodes.get(node_id)
    if node is None:
        raise KeyError(f"no tree node {node_id!r}")
    if node.metric_id is None:
        raise ExecutionError("the node has no metric to break down")
    usable, _ = dimensions_for_metric(model, node.metric_id)
    if dimension not in {d.name for d in usable}:
        raise ExecutionError(f"dimension {dimension!r} cannot slice {node.metric_id!r} without fan-out")
    if node.segment_path and any(s.dimension == dimension for s in node.segment_path):
        raise ExecutionError(f"the node is already a {dimension} segment")
    before = set(ex.nodes)
    if node.kind == "segment" or node.kind == "dimension":
        target = node if node.kind == "segment" else ex.nodes[node.parent_id or ""]
        cur, base, _ids, err = ex.totals(target.metric_id or "", list(target.segment_path))
        if err or cur is None or base is None:
            ex.failed_node(target.id, None, f"drill into {target.statement}", err or "no value")
        else:
            target.current, target.baseline = cur, base
            ex.contribution(target, target.metric_id or "", dimension, list(target.segment_path), None)
    else:
        ex.contribution(node, node.metric_id, dimension, list(node.segment_path), None)
    new_ids = [i for i in ex.order if i not in before]
    # evaluate evidence only for the new nodes (existing ones keep their assessments)
    ex.corroborate()
    for nid in new_ids:
        n = ex.nodes[nid]
        n.evidence_strength, n.evidence_reasons = assess(ex.evidence[nid])
    out = InvestigationTree(root_id=tree.root_id, nodes=[ex.nodes[i] for i in ex.order])
    walk = out.walk()
    seen = {w.id for w in walk}
    out.nodes = walk + [n for n in out.nodes if n.id not in seen]
    return out, list(ex.artifacts.values()), new_ids
