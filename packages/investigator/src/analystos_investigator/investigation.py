"""Investigation lifecycle: ask -> interpret -> plan -> (approve) -> run -> drill / rerun / diff.

This is the entry point used by the API layer. Everything here is deterministic and works
without an LLM; the optional provider only adds suggestions (see ``interpret`` and
``llm.orchestrator``).
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from typing import Any

from analystos_engine.semantic.models import SemanticModel

from .executor import ExecutionError, execute, extend_with_contribution
from .formatting import fmt_share, fmt_value
from .hypotheses import generate as generate_hypotheses
from .interpret import apply_choices, build_value_index, interpret
from .llm.base import LLMProvider
from .models import (
    AnalysisPlan,
    Artifact,
    DatasetVersionChange,
    DatasetVersionRef,
    ExecutionConfig,
    Interpretation,
    Investigation,
    InvestigationDiff,
    InvestigationRun,
    InvestigationTree,
    MetricVersionChange,
    NodeChange,
    NodeStatus,
    PeriodCandidate,
    PlanBridge,
    Premise,
    RelatedSignal,
    TreeNode,
    utcnow,
)
from .planner import PlanningError, pinned_dimensions
from .planner import plan as make_plan
from .semantic_graph import dimensions_for_metric
from .templates import dimension_roles


class InvestigationError(ValueError):
    pass


def _new_id() -> str:
    return "inv_" + uuid.uuid4().hex[:16]


def investigate(
    question: str,
    store: Any,
    model: SemanticModel,
    today: dt.date | None = None,
    *,
    llm: LLMProvider | None = None,
    choices: dict[str, str] | None = None,
    value_index: dict[str, list[str]] | None = None,
    config: ExecutionConfig | None = None,
    template: str | None = None,
    auto_approve: bool = False,
) -> InvestigationRun:
    """Interpret and plan a question, and run it unless it needs approval or disambiguation.

    * Ambiguous metric terms -> ``status="needs_disambiguation"`` with candidates; call again
      with ``choices`` or use :func:`resolve_ambiguity`.
    * Expensive plans -> ``status="awaiting_approval"``; call :func:`run_investigation`.
    """
    if value_index is None:
        value_index = build_value_index(store, model)
    interp = interpret(question, model, today, llm, value_index=value_index, choices=choices)
    inv = Investigation(
        id=_new_id(), question=question, interpretation=interp, model_snapshot_hash=model.content_hash()
    )
    return _plan_and_maybe_run(inv, store, model, config=config, template=template, auto_approve=auto_approve)


def resolve_ambiguity(
    run: InvestigationRun,
    store: Any,
    model: SemanticModel,
    choices: dict[str, str],
    *,
    config: ExecutionConfig | None = None,
    auto_approve: bool = False,
) -> InvestigationRun:
    inv = run.investigation
    interp = apply_choices(inv.interpretation, model, choices)
    updated = inv.model_copy(update={"interpretation": interp})
    return _plan_and_maybe_run(updated, store, model, config=config, template=None, auto_approve=auto_approve)


def _premise_holds(pr: Premise, pct: float | None) -> bool:
    if pct is None:
        return False
    if pr.expectation == "flat":
        return abs(pct) <= pr.tolerance
    if pr.expectation == "increase":
        return pct > pr.tolerance
    return pct < -pr.tolerance


def resolve_premise_period(interp: Interpretation, store: Any, model: SemanticModel) -> Interpretation:
    """Choose the analysis period for a question that states a premise but names no period.

    "Revenue was roughly flat. Why did margin decline?" has no period; the default (last
    month vs the month before) may contradict the premise. Candidates are the last three
    complete months and the last two complete quarters, each against the previous period
    and the same period a year earlier. Among the candidates where every premise holds and
    the subject metric moved in the asked direction, the one where the premises hold most
    tightly (flattest) is chosen; ties go to the more recent, longer period. Every candidate
    is recorded in ``period_candidates`` and the choice is explained in the notes. When no
    candidate qualifies, the default period stays and the premise check reports it.
    """
    premises = [
        p
        for p in interp.premises
        if p.metric_id and p.metric_id != interp.primary_metric_id and p.baseline_metric_id is None
    ]
    subject = interp.primary_metric_id
    if (
        not premises
        or not interp.period_defaulted
        or subject is None
        or interp.comparison_kind not in ("pop", "yoy")
        or interp.segment_comparison is not None
    ):
        return interp
    from analystos_engine.calendar import CalendarError, previous_period, resolve_period
    from analystos_engine.semantic.compiler import MetricQuery, run_metric_query

    today = interp.reference_date or dt.date.today()
    try:
        month = resolve_period("last month", today, model.calendar)
        quarter = resolve_period("last quarter", today, model.calendar)
    except CalendarError:
        return interp
    periods = [month, previous_period(month, "pop"), previous_period(previous_period(month, "pop"), "pop")]
    periods += [quarter, previous_period(quarter, "pop")]
    metrics = list(dict.fromkeys([subject, *(p.metric_id or "" for p in premises)]))
    values: dict[tuple[dt.date, dt.date], dict[str, float | None]] = {}

    def measure(w: Any) -> dict[str, float | None]:
        key = (w.start, w.end)
        if key not in values:
            try:
                q = MetricQuery(metrics=metrics, time=w.model_copy(update={"dimension": None, "grain": None}))
                _c, res = run_metric_query(store, model, q, limit=10)
                row = res.to_records()[0] if res.rows else {}
                values[key] = {m: (float(row[m]) if row.get(m) is not None else None) for m in metrics}
            except Exception:
                values[key] = dict.fromkeys(metrics)
        return values[key]

    direction = interp.direction
    candidates: list[PeriodCandidate] = []
    for w in periods:
        for kind in ("pop", "yoy"):
            b = previous_period(w, kind)  # type: ignore[arg-type]
            cur, base = measure(w), measure(b)
            changes: dict[str, float | None] = {}
            for pr in premises:
                c, bv = cur.get(pr.metric_id or ""), base.get(pr.metric_id or "")
                changes[pr.metric_id or ""] = (
                    (c - bv) / abs(bv) if c is not None and bv not in (None, 0) else None
                )
            sc, sb = cur.get(subject), base.get(subject)
            subject_change = (sc - sb) if sc is not None and sb is not None else None
            ok = all(_premise_holds(pr, changes.get(pr.metric_id or "")) for pr in premises)
            if subject_change is None:
                ok = False
            elif direction == "decrease":
                ok = ok and subject_change < -1e-9 * max(abs(sb or 0.0), 1.0)
            elif direction == "increase":
                ok = ok and subject_change > 1e-9 * max(abs(sb or 0.0), 1.0)
            candidates.append(
                PeriodCandidate(
                    window=w,
                    baseline=b,
                    comparison_kind=kind,  # type: ignore[arg-type]
                    premise_changes=changes,
                    subject_change=subject_change,
                    holds=ok,
                )
            )
    notes = list(interp.confidence_notes)
    names = ", ".join(f"'{p.text}'" for p in premises)
    valid = [c for c in candidates if c.holds]
    if not valid:
        notes.append(
            f"no recent month or quarter matches {names} with {model.get_metric(subject).display_name} moving as "
            f"asked; kept {interp.window.display() if interp.window else 'the default period'} and the premise "
            "check reports what was measured"
        )
        return interp.model_copy(update={"period_candidates": candidates, "confidence_notes": notes})

    def tightness(c: PeriodCandidate) -> float:
        return sum(abs(v) for v in c.premise_changes.values() if v is not None)

    best = sorted(valid, key=lambda c: (round(tightness(c), 4), -c.window.end.toordinal(), -c.window.days))[0]
    label = f"{best.window.display()} vs {best.baseline.display()}"
    others = [f"{c.window.display()} vs {c.baseline.display()}" for c in valid if c is not best]
    notes = [n for n in notes if not n.startswith("no period in the question; defaulted")]
    notes.append(
        f"no period was named, so the period was chosen where {names} holds: {label} "
        f"({', '.join(f'{model.get_metric(k).display_name} {v * 100:+.1f}%' for k, v in best.premise_changes.items() if v is not None)})"
        + (f"; other matching periods: {'; '.join(others)}" if others else "")
    )
    return interp.model_copy(
        update={
            "window": best.window,
            "baseline": best.baseline,
            "comparison_kind": best.comparison_kind,
            "period_candidates": candidates,
            "confidence_notes": notes,
        }
    )


def _plan_and_maybe_run(
    inv: Investigation,
    store: Any,
    model: SemanticModel,
    *,
    config: ExecutionConfig | None,
    template: str | None,
    auto_approve: bool,
) -> InvestigationRun:
    interp = inv.interpretation
    if not interp.ambiguous and interp.metric_ids and store is not None:
        interp = resolve_premise_period(interp, store, model)
        inv = inv.model_copy(update={"interpretation": interp})
    if interp.ambiguous or not interp.metric_ids:
        inv.status = "needs_disambiguation"
        if not interp.metric_ids and not interp.ambiguous:
            inv.failures.append("no metric from the semantic model was recognised in the question")
        return InvestigationRun(investigation=inv, artifacts=[])
    try:
        plan = make_plan(interp, model, template=template, config=config)
    except PlanningError as exc:
        inv.status = "failed"
        inv.failures.append(str(exc))
        return InvestigationRun(investigation=inv, artifacts=[])
    inv.plan = plan
    inv.hypotheses = generate_hypotheses(interp, model, plan)
    if plan.requires_approval and not auto_approve:
        inv.status = "awaiting_approval"
        return InvestigationRun(investigation=inv, artifacts=[])
    return run_investigation(inv, store, model)


def update_plan(inv: Investigation, plan: AnalysisPlan, model: SemanticModel) -> Investigation:
    """Replace the plan of a not-yet-run (or to-be-rerun) investigation after analyst edits."""
    if plan.context is None:
        raise InvestigationError("plan has no context")
    hyps = generate_hypotheses(inv.interpretation, model, plan)
    return inv.model_copy(
        update={
            "plan": plan,
            "hypotheses": hyps,
            "status": "awaiting_approval" if inv.status != "completed" else inv.status,
        }
    )


def run_investigation(inv: Investigation, store: Any, model: SemanticModel) -> InvestigationRun:
    """Execute the investigation's plan (after approval)."""
    if inv.plan is None:
        raise InvestigationError("the investigation has no plan")
    res = execute(inv.plan, store, model)
    root = res.tree.root() if res.tree.root_id else None
    status = "failed" if root is None or root.status == "failed" else "completed"
    out = inv.model_copy(
        update={
            "tree": res.tree,
            "status": status,
            "failures": [*res.failures],
            "completed_at": utcnow(),
            "model_snapshot_hash": model.content_hash(),
            "related_signals": list(res.related_signals),
            "bridge": res.bridge,
        }
    )
    window = inv.plan.context.window.display() if inv.plan.context else None
    out.brief_answer = (
        brief_answer(res.tree, out.related_signals, window=window) if status == "completed" else None
    )
    out.brief_lead = brief_lead(res.tree, res.bridge, window=window) if status == "completed" else None
    out.followups = followups(out, model)
    return InvestigationRun(investigation=out, artifacts=res.artifacts)


# --------------------------------------------------------------------------- narrative


def _share0(n: TreeNode) -> float:
    c = n.contribution_to_parent
    return (c.share or 0.0) if c is not None else 0.0


def brief_answer(
    tree: InvestigationTree, related: list[RelatedSignal] | None = None, *, window: str | None = None
) -> str:
    """Short answer (spec §29) assembled from the tree's own statements and numbers.

    A gap to plan with a reconciled decomposition gets the variance brief
    (:func:`variance_brief`); everything else:

    * the measured change (root);
    * a premise the data contradicts, if any;
    * the largest positive drivers, each with the change its share refers to;
    * in the most explanatory breakdown of the root metric, its largest segment (by share of
      the root change), plus the largest segment inside it when the drill found one.
    Shares are always stated against the change they are a share of.
    """
    root = tree.root()
    kids = tree.children_of(root.id)
    rec = next((k for k in kids if k.role == "reconciliation" and k.reconciliation is not None), None)
    if rec is not None:
        return variance_brief(tree, related or [], window=window)
    metric = root.metric_label or root.metric_id or "the metric"
    parts = [root.statement.split(":")[0].rstrip(".") + "."]
    provisional = next((n for n in root.notes if n.startswith("provisional: ")), None)
    if provisional:
        parts.append("Provisional: " + provisional.removeprefix("provisional: ").rstrip(".") + ".")
    for k in kids:
        if k.kind == "check" and "premise contradicted" in k.notes:
            parts.append(f"Note: {k.statement.split(': ', 1)[0]} does not hold for these periods.")
    drivers = sorted(
        (
            k
            for k in kids
            if k.kind == "driver"
            and k.contribution_to_parent is not None
            and (k.contribution_to_parent.share or 0) > 0.05
        ),
        key=lambda d: (-(d.contribution_to_parent.share or 0.0), d.id),  # type: ignore[union-attr]
    )
    if drivers:
        named = [f"{d.metric_label} ({fmt_share(d.contribution_to_parent.share)})" for d in drivers[:2]]  # type: ignore[union-attr]
        lead = (
            "The largest measurable driver was "
            if len(named) == 1
            else "The largest measurable drivers were "
        )
        parts.append(f"{lead}{' and '.join(named)} of the {metric} change.")
    for k in kids:
        if k.kind == "dimension" and k.step_id and k.step_id.startswith("health:"):
            parts.append(k.statement.rstrip(".") + ".")
        elif k.kind == "dimension" and any(n.startswith("ranked by") for n in k.notes) and ";" in k.statement:
            tail = k.statement.split(";", 1)[1].strip().rstrip(".")
            parts.append(tail[:1].upper() + tail[1:] + ".")
    groups = sorted(
        (
            g
            for g in kids
            if g.kind == "dimension" and g.metric_id == root.metric_id and g.explanatory_power is not None
        ),
        key=lambda g: (-(g.explanatory_power or 0.0), g.id),
    )

    # Segments the investigation drilled into are its focus; otherwise the most explanatory
    # breakdown. Name the largest one by share (a segment explaining more than all of the
    # change is offset by others, so it comes last).
    def explaining(g: TreeNode) -> list[TreeNode]:
        return [
            s
            for s in tree.children_of(g.id)
            if s.kind == "segment"
            and s.contribution_to_parent is not None
            and (s.contribution_to_parent.share or 0) > 0
            and s.statement_type == "supported_explanation"
        ]

    drilled = [
        s for g in groups for s in explaining(g) if any(c.kind == "dimension" for c in tree.children_of(s.id))
    ]
    segs = drilled or (explaining(groups[0]) if groups else [])
    if segs:
        top = max(
            segs,
            key=lambda s: (
                _share0(s) <= 1.0,
                _share0(s),
                s.id,
            ),  # type: ignore[union-attr]
        )
        label = top.statement.split(":")[0]
        sentence = f"By {label.split(' = ')[0]}, {label.split(' = ', 1)[-1]} accounts for {fmt_share(top.contribution_to_parent.share)} of the {metric} change"  # type: ignore[union-attr]
        inner = [
            c
            for ig in tree.children_of(top.id)
            if ig.kind == "dimension"
            for c in tree.children_of(ig.id)
            if c.kind == "segment"
            and c.contribution_to_parent is not None
            and 0.5 <= (c.contribution_to_parent.share or 0) <= 1.0
        ]
        if inner:
            c = max(inner, key=lambda c: (c.contribution_to_parent.share or 0.0, c.id))  # type: ignore[union-attr]
            sentence += (
                f"; within {label.split(' = ', 1)[-1]}, {c.statement.split(':')[0]} accounts for "
                f"{fmt_share(c.contribution_to_parent.share)} of that segment's change"  # type: ignore[union-attr]
            )
        parts.append(sentence + ".")
    return " ".join(parts)


def _phrase(node: TreeNode) -> str:
    """A node's title for use inside a sentence."""
    title = node.title or node.metric_label or ""
    if (
        node.step_id
        and node.step_id.startswith("margin_bridge")
        and title[:1].isupper()
        and title[1:2].islower()
    ):
        return title[0].lower() + title[1:]
    return title


class VarianceSentences:
    """The sentences of a reconciled answer to a gap to plan (see :func:`variance_sentences`)."""

    def __init__(self) -> None:
        self.headline: str | None = None
        self.premises: list[str] = []
        self.explanation: list[str] = []
        self.related: list[str] = []
        self.pending: list[str] = []
        """Titles of explanation parts left out because they are not included (not confirmed)."""
        self.node_ids: list[str] = []
        self.reconciled = False

    def all(self) -> list[str]:
        return [*([self.headline] if self.headline else []), *self.premises, *self.explanation, *self.related]


def variance_sentences(
    tree: InvestigationTree,
    related: list[RelatedSignal],
    *,
    window: str | None = None,
    include: set[str] | None = None,
) -> VarianceSentences:
    """The reconciled answer to a gap to plan, in the order an executive asks: how far off,
    whether the premise holds, what the gap is made of (and that it adds back), what sits
    inside the largest parts, and which related signals were seen but not established.

    With ``include`` (for example the confirmed node ids), only those nodes are used; the
    reconciled wording is kept only when every part of the top identity is included.
    """
    out = VarianceSentences()
    root = tree.root()
    kids = tree.children_of(root.id)
    fmt = root.metric_format
    rec_node = next((k for k in kids if k.role == "reconciliation" and k.reconciliation is not None), None)
    if rec_node is None or rec_node.reconciliation is None:
        return out
    rec = rec_node.reconciliation
    gap = rec.gap
    noun = "miss" if "missed" in (root.title or "") else "gap"
    when = f" in {window}" if window else ""

    def ok(node_id: str | None) -> bool:
        return include is None or (node_id is not None and node_id in include)

    if ok(root.id):
        out.headline = (
            f"{root.title or rec.metric_label} by {fmt_value(abs(gap), fmt)}{when}: {fmt_value(rec.actual, fmt)} "
            f"against a plan of {fmt_value(rec.baseline, fmt)} ({_pct(root.pct_change)})."
        )
        out.node_ids.append(root.id)
    for k in kids:
        if k.role == "premise" and k.current is not None and k.baseline is not None and ok(k.id):
            held = "holds" if k.verdict == "supported" else "does not hold"
            out.premises.append(
                f"The premise {held}: {k.metric_label} was {fmt_value(k.current, k.metric_format)} against "
                f"{k.baseline_label or 'plan'} of {fmt_value(k.baseline, k.metric_format)} ({_pct(k.pct_change)})."
            )
            out.node_ids.append(k.id)
    by_id = tree.by_id()

    def sentence(identity: Any, lead: str | None, closing: str) -> str:
        parent_amount = identity.parent_amount
        comps = [c for c in identity.components if ok(c.node_id)]
        same = [
            c for c in comps if c.amount * parent_amount > 0 and abs(c.amount) >= 0.05 * abs(parent_amount)
        ]
        against = [
            c for c in comps if c.amount * parent_amount < 0 and abs(c.amount) >= 0.02 * abs(parent_amount)
        ]
        same.sort(key=lambda c: -abs(c.amount))
        against.sort(key=lambda c: -abs(c.amount))
        if not same:
            return ""
        items = []
        for i, c in enumerate(same[:3]):
            node = by_id.get(c.node_id or "")
            label = _phrase(node) if node else c.label
            why = cause_text(tree, node) if node is not None and lead else ""
            detail = f" ({why})" if why else ""
            items.append(
                f"{label} {'accounts for' if i == 0 else 'for'} {fmt_value(abs(c.amount), fmt)}{detail}"
            )
            out.node_ids.append(c.node_id or "")
        joined = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
        text = (lead + ", " if lead else "") + joined
        if against:
            offs = []
            for c in against[:2]:
                node = by_id.get(c.node_id or "")
                label = _phrase(node) if node else c.label
                offs.append(f"{fmt_value(abs(c.amount), fmt)} of {label}")
                out.node_ids.append(c.node_id or "")
            text += ", partially offset by " + " and ".join(offs)
        return text + closing

    top = rec.identities[0]
    missing = [c for c in top.components if not ok(c.node_id)]
    out.pending = [c.label for c in missing if abs(c.amount) >= 0.02 * abs(top.parent_amount)]
    if rec.reconciled and not missing:
        closing = f", which reconciles exactly to the {fmt_value(abs(gap), fmt)} {noun}."
        out.reconciled = True
    elif rec.reconciled:
        covered = sum(c.amount for c in top.components if ok(c.node_id))
        closing = (
            f"; the confirmed parts account for {fmt_value(abs(covered), fmt)} of the {fmt_value(abs(gap), fmt)} "
            f"{noun} ({len(missing)} part{'s' if len(missing) != 1 else ''} not yet confirmed)."
        )
    else:
        closing = f"; the parts leave {fmt_value(abs(top.residual), fmt)} unexplained."
    first = sentence(top, None, closing)
    if first:
        out.explanation.append(first[0].upper() + first[1:])
    for identity in rec.identities[1:]:
        node = by_id.get(identity.parent_node_id or "")
        if node is None or node.parent_id != root.id:
            continue
        text = sentence(identity, f"Within {node.metric_label}", ".")
        if text:
            out.explanation.append(text)
    for sig in related:
        if not ok(sig.node_id) or len(out.related) >= 2:
            continue
        where = " / ".join(str(x.value) for x in sig.segment_path)
        verb = "fell" if (sig.current or 0.0) < (sig.baseline or 0.0) else "rose"
        text = (
            f"Related signal, relationship not established: in {where}, {sig.metric_label} {verb} from "
            f"{fmt_value(sig.baseline, sig.metric_format, sig.unit)} to {fmt_value(sig.current, sig.metric_format, sig.unit)}"
        )
        out.node_ids.append(sig.node_id)
        for sid in sig.supporting_node_ids[:2]:
            n = by_id.get(sid)
            if n is not None and n.current is not None and n.baseline is not None:
                w = "fell" if n.current < n.baseline else "rose"
                text += (
                    f" and {n.metric_label} {w} from {fmt_value(n.baseline, n.metric_format, n.metric_unit)} to "
                    f"{fmt_value(n.current, n.metric_format, n.metric_unit)}"
                )
                out.node_ids.append(sid)
        text += (
            f" ({sig.window.display()} vs {sig.baseline_window.display()}); the available data does not establish "
            f"it as a cause of the {rec.metric_label} {noun}."
        )
        if sig.provisional:
            text += " The signal is provisional: recent cohorts are still maturing."
        out.related.append(text)
    out.node_ids = [i for i in dict.fromkeys(out.node_ids) if i]
    return out


def variance_brief(
    tree: InvestigationTree, related: list[RelatedSignal], *, window: str | None = None
) -> str:
    """The brief answer to a gap to plan (every sentence of :func:`variance_sentences`)."""
    return " ".join(variance_sentences(tree, related, window=window).all())


def _drivers(tree: InvestigationTree, node: TreeNode) -> list[TreeNode]:
    return [
        c for c in tree.children_of(node.id) if c.kind in ("driver", "other") and c.root_effect is not None
    ]


def _leaders(tree: InvestigationTree, node: TreeNode) -> list[TreeNode]:
    """Segments under a node's breakdowns that carry at least 20% of it, largest first."""
    segs = [
        s
        for g in tree.children_of(node.id)
        if g.kind == "dimension"
        for s in tree.children_of(g.id)
        if s.kind == "segment"
        and s.segment is not None
        and s.contribution_to_parent is not None
        and (s.contribution_to_parent.share or 0.0) >= 0.2
        and s.root_effect is not None
    ]
    return sorted(segs, key=lambda s: (-abs(s.root_effect or 0.0), s.id))


def cause_text(tree: InvestigationTree, node: TreeNode) -> str:
    """Where a part of the gap comes from, down to its measured operational cause:
    "Lumber $611.1k, mostly supplier Gulf States Forest Products ($520.0k), and Roofing $297.0k",
    "Fuel Surcharge overrun $250.0k", "Overtime overrun $169.4k in San Antonio and Austin, partly
    offset by Payroll saving $44.6k". Numbers are node values; nothing is inferred."""
    fmt = tree.root().metric_format
    amount = node.root_effect or 0.0

    def amt(n: TreeNode) -> str:
        return fmt_value(abs(n.root_effect or 0.0), fmt)

    kids = _drivers(tree, node)
    if kids and not node.members:
        same = sorted(
            (
                k
                for k in kids
                if (k.root_effect or 0.0) * amount > 0 and abs(k.root_effect or 0.0) >= 0.2 * abs(amount)
            ),
            key=lambda k: -abs(k.root_effect or 0.0),
        )
        against = sorted(
            (
                k
                for k in kids
                if (k.root_effect or 0.0) * amount < 0 and abs(k.root_effect or 0.0) >= 0.2 * abs(amount)
            ),
            key=lambda k: -abs(k.root_effect or 0.0),
        )
        parts = []
        for k in same[:2]:
            inner = _segment_places(tree, k)
            parts.append(f"{_phrase(k)} {amt(k)}" + (f" in {inner}" if inner else ""))
        text = " and ".join(parts)
        if against:
            k = against[0]
            inner = _segment_places(tree, k)
            if inner and inner in text:
                where = f" in the same {_plural(_leaders(tree, k)[0].segment.dimension)}"  # type: ignore[union-attr]
            else:
                where = f" in {inner}" if inner else ""
            text += f", partly offset by {_phrase(k)} {amt(k)}{where}"
        return text
    lead = _leaders(tree, node)
    if not lead:
        return ""
    parts = []
    for i, s in enumerate(lead[:2]):
        text = f"{s.segment.value} {amt(s)}"  # type: ignore[union-attr]
        below = _leaders(tree, s) if i == 0 else []
        if below and below[0].segment is not None:
            dim = (below[0].segment.dimension or "").replace("_", " ")
            share = below[0].contribution_to_parent.share if below[0].contribution_to_parent else None
            word = "mostly" if share is not None and share >= 0.5 else "largest"
            text += f", {word} {dim} {below[0].segment.value} ({amt(below[0])})"
        parts.append(text)
    return ("; " if any(", " in p for p in parts) else " and ").join(parts)


def _plural(dimension: str | None) -> str:
    word = (dimension or "segment").replace("_", " ")
    return word + ("es" if word.endswith(("ch", "sh", "s", "x")) else "s")


def _segment_places(tree: InvestigationTree, node: TreeNode) -> str:
    """ "San Antonio and Austin": the segments that carry a line's gap (or '')."""
    lead = _leaders(tree, node)
    return " and ".join(str(s.segment.value) for s in lead[:2] if s.segment is not None)


def _cause_names(tree: InvestigationTree, node: TreeNode) -> str:
    """The short form of :func:`cause_text`: names only ("Lumber, Pine Co", "fuel surcharge")."""
    amount = node.root_effect or 0.0
    kids = [k for k in _drivers(tree, node) if (k.root_effect or 0.0) * amount > 0]
    if kids and not node.members:
        k = max(kids, key=lambda k: abs(k.root_effect or 0.0))
        inner = _segment_places(tree, k)
        name = (k.metric_label or k.title or "").lower()
        return name + (f" in {inner}" if inner else "")
    lead = _leaders(tree, node)
    if not lead:
        return ""
    names = " and ".join(str(s.segment.value) for s in lead[:2] if s.segment is not None)
    below = _leaders(tree, lead[0])
    if below and below[0].segment is not None:
        dim = (below[0].segment.dimension or "").replace("_", " ")
        names += f"; {dim} {below[0].segment.value}" if len(lead) > 1 else f", {below[0].segment.value}"
    return names


def brief_lead(
    tree: InvestigationTree, bridge: PlanBridge | None, *, window: str | None = None
) -> str | None:
    """Two or three short sentences for a gap to plan: how far off (and whether the premise
    holds), how the gap splits, and the largest causes."""
    if bridge is None or not tree.root_id:
        return None
    root = tree.root()
    fmt = root.metric_format
    kids = tree.children_of(root.id)
    rec = next((k.reconciliation for k in kids if k.role == "reconciliation" and k.reconciliation), None)
    if rec is None:
        return None
    first = (
        f"{root.title or rec.metric_label} by {fmt_value(abs(bridge.gap), fmt)} ({_pct(root.pct_change)})"
        + (f" in {window}" if window else "")
    )
    premise = next((k for k in kids if k.role == "premise"), None)
    if premise is not None and premise.verdict == "supported":
        first += f", while {premise.metric_label} met {premise.baseline_label or 'its plan'}"
    elif premise is not None:
        first += f"; {premise.metric_label} did not meet {premise.baseline_label or 'its plan'}"
    by_id = tree.by_id()
    comps = [c for c in rec.identities[0].components if c.amount * bridge.gap > 0]
    comps.sort(key=lambda c: -abs(c.amount))
    labels = [
        f"{(by_id[c.node_id].metric_label if c.node_id in by_id else c.label)} {fmt_value(abs(c.amount), fmt)}"
        for c in comps[:2]
    ]
    second = ""
    if labels:
        second = f"{labels[0].rsplit(' ', 1)[0]} explains {labels[0].rsplit(' ', 1)[1]} of the gap" + (
            f" and {labels[1].rsplit(' ', 1)[0]} {labels[1].rsplit(' ', 1)[1]}" if len(labels) > 1 else ""
        )
    causes = []
    for st in sorted(
        (st for st in bridge.steps if st.amount * bridge.gap > 0), key=lambda st: -abs(st.amount)
    )[:3]:
        node = by_id.get(st.node_id or "")
        if node is None or node.members:
            continue
        names = _cause_names(tree, node)
        causes.append(_phrase(node).lower() if node.title and node.title[1:2].islower() else _phrase(node))
        if names:
            causes[-1] += f" ({names})"
    third = ""
    if causes:
        third = "The largest causes: " + (
            causes[0] if len(causes) == 1 else ", ".join(causes[:-1]) + " and " + causes[-1]
        )
    return " ".join(x + "." for x in (first, second, third) if x)


def _variance_followups(inv: Investigation, model: SemanticModel) -> list[str]:
    """What to look at next after a gap to plan: each related signal as its own investigation
    (it is not established by this one), then the same period a year earlier."""
    ctx = inv.plan.context if inv.plan else None
    out: list[str] = []
    if ctx is None:
        return out
    for sig in inv.related_signals:
        m = model.get_metric(sig.metric_id) if model.has_metric(sig.metric_id) else None
        if m is None or not sig.segment_path:
            continue
        verb = "decline" if (sig.current or 0.0) < (sig.baseline or 0.0) else "increase"
        where = " ".join(str(s.value) for s in sig.segment_path[:1])
        out.append(f"Why did {m.display_name.lower()} {verb} in {where} in {sig.window.display()}?")
    root = model.get_metric(ctx.metric_id).display_name
    out.append(
        f"Why did {root.lower()} change in {ctx.window.display()} compared with the same period last year?"
    )
    for h in inv.hypotheses:
        if h.category == "untestable":
            text = h.statement.removeprefix("Hypothesis (not tested): ").rstrip(".")
            out.append(f"Add data to test whether {text}")
            break
    return out[:6]


def followups(inv: Investigation, model: SemanticModel) -> list[str]:
    """Deterministic follow-up suggestions from what was and was not explored."""
    out: list[str] = []
    ctx = inv.plan.context if inv.plan else None
    if ctx is None:
        return out
    if ctx.plan_metrics and inv.tree.root_id:
        return _variance_followups(inv, model)
    tree = inv.tree
    if tree.root_id:
        used = {n.dimension for n in tree.nodes if n.dimension}
        usable, _ = dimensions_for_metric(model, ctx.metric_id)
        top_segs = sorted(
            (
                n
                for n in tree.nodes
                if n.kind == "segment"
                and n.contribution_to_parent is not None
                and (n.contribution_to_parent.share or 0) >= 0.2
                and len(n.segment_path) == 1
            ),
            key=lambda n: (-(n.contribution_to_parent.share or 0.0), n.id),  # type: ignore[union-attr]
        )
        blocked = pinned_dimensions(model, ctx.metric_id) | {f.dimension for f in ctx.filters}
        unused = [
            d
            for d in usable
            if d.name not in used and d.name not in blocked and "status" not in dimension_roles(d)
        ]
        for seg in top_segs[:2]:
            for d in unused[:1]:
                out.append(f"Break down {seg.segment.label() if seg.segment else ''} by {d.label or d.name}")
        if unused and not top_segs:
            out.append(
                f"Break down {model.get_metric(ctx.metric_id).display_name} by {unused[0].label or unused[0].name}"
            )
    if ctx.comparison_kind == "pop":
        out.append("Compare with the same period last year")
    if ctx.comparison_kind != "budget" and any(
        "budget" in [t.lower() for t in m.tags] for m in model.metrics
    ):
        out.append("Compare against budget")
    for h in inv.hypotheses:
        if h.category == "untestable":
            text = h.statement.removeprefix("Hypothesis (not tested): ").rstrip(".")
            out.append(f"Add data to test whether {text}")
            break
    return list(dict.fromkeys(out))[:6]


# --------------------------------------------------------------------------- node actions


def _replace_node(inv: Investigation, node: TreeNode) -> Investigation:
    nodes = [node if n.id == node.id else n for n in inv.tree.nodes]
    return inv.model_copy(update={"tree": inv.tree.model_copy(update={"nodes": nodes})})


def set_node_status(inv: Investigation, node_id: str, status: NodeStatus) -> Investigation:
    """Confirm, reject or flag a node (spec §17, §90). Failed nodes stay failed."""
    node = inv.tree.get(node_id)
    if node.status == "failed" and status != "failed":
        raise InvestigationError("a failed test cannot be confirmed; rerun or edit the step instead")
    # Confirming a hypothesis node records that the analyst endorses it as an open question;
    # its statement type stays "hypothesis" and summaries list it as unresolved.
    return _replace_node(inv, node.model_copy(update={"status": status}))


def annotate_node(inv: Investigation, node_id: str, text: str) -> Investigation:
    node = inv.tree.get(node_id)
    if not text.strip():
        raise InvestigationError("annotation is empty")
    return _replace_node(inv, node.model_copy(update={"annotations": [*node.annotations, text.strip()]}))


def node_lineage(run: InvestigationRun, node_id: str) -> list[Artifact]:
    """All artifacts behind a node, including the query artifacts behind metric results."""
    node = run.investigation.tree.get(node_id)
    index = {a.id: a for a in run.artifacts}
    seen: list[str] = []
    stack = list(node.artifact_ids)
    while stack:
        aid = stack.pop(0)
        if aid in seen or aid not in index:
            continue
        seen.append(aid)
        stack.extend(index[aid].parent_ids)
    return [index[a] for a in seen]


def drill(
    run: InvestigationRun, store: Any, model: SemanticModel, node_id: str, dimension: str
) -> InvestigationRun:
    """Branch from a node: break its metric down by ``dimension`` inside the node's segment."""
    inv = run.investigation
    if inv.plan is None:
        raise InvestigationError("the investigation has no plan")
    try:
        tree, artifacts, _new = extend_with_contribution(
            inv.tree, run.artifacts, inv.plan, store, model, node_id, dimension
        )
    except ExecutionError as exc:
        raise InvestigationError(str(exc)) from exc
    # Drills are recorded so that rerun() replays them in order.
    drill_record = {"node_id": node_id, "dimension": dimension}
    out = inv.model_copy(
        update={
            "tree": tree,
            "run_config": {**inv.run_config, "drills": [*inv.run_config.get("drills", []), drill_record]},
        }
    )
    return InvestigationRun(investigation=out, artifacts=artifacts)


# --------------------------------------------------------------------------- rerun / diff


def rerun(
    run: InvestigationRun, store: Any, model: SemanticModel
) -> tuple[InvestigationRun, InvestigationDiff]:
    """Re-execute the stored plan (same absolute periods, filters and steps), replay any
    drills, and compare the result with the previous run."""
    inv = run.investigation
    if inv.plan is None:
        raise InvestigationError("the investigation has no plan to rerun")
    fresh = inv.model_copy(
        update={
            "id": _new_id(),
            "created_at": utcnow(),
            "tree": InvestigationTree(),
            "failures": [],
            "run_config": {},
        }
    )
    new_run = run_investigation(fresh, store, model)
    for d in inv.run_config.get("drills", []):
        try:
            new_run = drill(new_run, store, model, d["node_id"], d["dimension"])
        except (KeyError, ValueError) as exc:
            new_run.investigation.failures.append(f"could not replay drill {d}: {exc}")
    carried = carry_forward_decisions(inv, new_run.investigation)
    new_run = InvestigationRun(investigation=carried, artifacts=new_run.artifacts)
    return new_run, diff_runs(run, new_run)


DECISION_STATUSES = ("confirmed", "rejected", "needs_review")
MATERIAL_CHANGE = 0.05
"""A carried-forward decision is flagged for review when the node's value or share of change
moved by more than 5% (relative) between runs."""


def _match_key(n: TreeNode) -> tuple[Any, ...]:
    return (
        n.kind,
        n.step_id,
        n.metric_id,
        n.dimension,
        tuple((s.dimension, s.value) for s in n.segment_path),
    )


def _moved(old: TreeNode, new: TreeNode) -> list[str]:
    out = []

    def rel(a: float | None, b: float | None) -> bool:
        if a is None or b is None:
            return (a is None) != (b is None)
        return abs(b - a) > MATERIAL_CHANGE * max(abs(a), 1e-9)

    if rel(old.current, new.current):
        out.append("value")
    oc, nc = old.contribution_to_parent, new.contribution_to_parent
    if oc is not None and nc is not None and rel(oc.share, nc.share):
        out.append("share of change")
    if old.statement_type != new.statement_type:
        out.append("statement type")
    return out


def carry_forward_decisions(previous: Investigation, current: Investigation) -> Investigation:
    """Carry analyst decisions (spec §17, §90) from ``previous`` to the rerun ``current``.

    Nodes are matched by (kind, plan step, metric, dimension, segment path), which is stable
    across runs even if a node id changes. Status (confirmed, rejected, needs review),
    annotations and the saved finding id are copied. A confirmed node whose value, share of
    change or statement type moved materially is set to ``needs_review`` with a note saying
    what changed, so a stale confirmation never silently stands. Failed nodes keep failing.
    """
    old_by_key: dict[tuple[Any, ...], TreeNode] = {}
    for n in previous.tree.nodes:
        old_by_key.setdefault(_match_key(n), n)
    old_by_id = previous.tree.by_id()
    nodes: list[TreeNode] = []
    for n in current.tree.nodes:
        o = old_by_id.get(n.id) or old_by_key.get(_match_key(n))
        if o is None or (o.status not in DECISION_STATUSES and not o.annotations and not o.finding_id):
            nodes.append(n)
            continue
        update: dict[str, Any] = {
            "annotations": [*o.annotations, *(a for a in n.annotations if a not in o.annotations)],
            "finding_id": n.finding_id or o.finding_id,
            "decision_history": [*o.decision_history],
        }
        if o.status in DECISION_STATUSES and n.status != "failed":
            moved = _moved(o, n)
            status = o.status
            if moved and o.status == "confirmed":
                status = "needs_review"
                update["notes"] = [
                    *n.notes,
                    f"was confirmed in the previous run; its {', '.join(moved)} changed on rerun, so it needs review",
                ]
            update["status"] = status
            update["decision_history"].append(
                {
                    "from_investigation": previous.id,
                    "status": o.status,
                    "carried_as": status,
                    "changed": moved,
                }
            )
        elif o.status in DECISION_STATUSES:
            update["notes"] = [*n.notes, f"was {o.status} in the previous run; the test failed on rerun"]
        nodes.append(n.model_copy(update=update))
    return current.model_copy(update={"tree": current.tree.model_copy(update={"nodes": nodes})})


def _close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, float | int) and isinstance(b, float | int):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    return bool(a == b)


def _node_fields(n: TreeNode) -> dict[str, Any]:
    c = n.contribution_to_parent
    return {
        "current": n.current,
        "baseline": n.baseline,
        "abs_change": n.abs_change,
        "pct_change": n.pct_change,
        "share": c.share if c else None,
        "effect": c.effect if c else None,
        "evidence_strength": n.evidence_strength,
        "statement_type": n.statement_type,
        "status": n.status,
    }


def _dataset_map(artifacts: list[Artifact]) -> dict[str, DatasetVersionRef]:
    out: dict[str, DatasetVersionRef] = {}
    for a in artifacts:
        for d in a.dataset_versions:
            out[d.table] = d
    return out


def _metric_map(artifacts: list[Artifact]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in artifacts:
        out.update(a.metric_versions)
    return out


def diff_runs(before: InvestigationRun, after: InvestigationRun) -> InvestigationDiff:
    """Node-by-node comparison (by stable node id) plus data and definition changes."""
    old = before.investigation.tree.by_id()
    new = after.investigation.tree.by_id()
    changes: list[NodeChange] = []
    unchanged = 0
    summary: list[str] = []
    for nid, n in new.items():
        o = old.get(nid)
        if o is None:
            changes.append(NodeChange(node_id=nid, change="added", statement_after=n.statement))
            continue
        of, nf = _node_fields(o), _node_fields(n)
        fields = {k: (of[k], nf[k]) for k in of if not _close(of[k], nf[k]) and k != "status"}
        if fields or o.statement != n.statement:
            changes.append(
                NodeChange(
                    node_id=nid,
                    change="changed",
                    statement_before=o.statement,
                    statement_after=n.statement,
                    fields=fields,
                )
            )
            if "pct_change" in fields and n.segment is not None:
                a, b = fields["pct_change"]
                summary.append(f"{n.segment.label()}: {_pct(a)} -> {_pct(b)}")
            elif "pct_change" in fields and n.kind in ("root", "driver"):
                a, b = fields["pct_change"]
                summary.append(f"{n.metric_label}: {_pct(a)} -> {_pct(b)}")
        else:
            unchanged += 1
    for nid, o in old.items():
        if nid not in new:
            changes.append(NodeChange(node_id=nid, change="removed", statement_before=o.statement))
    ds_old, ds_new = _dataset_map(before.artifacts), _dataset_map(after.artifacts)
    ds_changes = []
    for table in sorted(set(ds_old) | set(ds_new)):
        a, b = ds_old.get(table), ds_new.get(table)
        if a is None or b is None or a.content_hash != b.content_hash or a.row_count != b.row_count:
            ds_changes.append(DatasetVersionChange(table=table, before=a, after=b))
            if a and b:
                summary.append(
                    f"table {table} changed: {a.row_count} -> {b.row_count} rows"
                    + ("" if a.content_hash != b.content_hash else " (same content hash)")
                )
    mv_old, mv_new = _metric_map(before.artifacts), _metric_map(after.artifacts)
    mv_changes = [
        MetricVersionChange(metric_id=m, before=mv_old.get(m), after=mv_new.get(m))
        for m in sorted(set(mv_old) | set(mv_new))
        if mv_old.get(m) != mv_new.get(m)
    ]
    summary.extend(f"metric {c.metric_id} definition changed: {c.before} -> {c.after}" for c in mv_changes)
    for n in after.investigation.tree.nodes:
        last = n.decision_history[-1] if n.decision_history else None
        if last and last.get("from_investigation") == before.investigation.id and last.get("changed"):
            summary.append(
                f"{last['status']} node needs review ({', '.join(last['changed'])} changed): {n.statement[:120]}"
            )
    kept = sum(
        1
        for n in after.investigation.tree.nodes
        if n.decision_history and n.decision_history[-1].get("from_investigation") == before.investigation.id
    )
    if kept:
        summary.append(f"{kept} analyst decisions carried forward")
    added = sum(1 for c in changes if c.change == "added")
    removed = sum(1 for c in changes if c.change == "removed")
    if added or removed:
        summary.append(f"{added} findings added, {removed} removed")
    return InvestigationDiff(
        previous_investigation_id=before.investigation.id,
        node_changes=changes,
        dataset_version_changes=ds_changes,
        metric_version_changes=mv_changes,
        unchanged_nodes=unchanged,
        summary=summary,
    )


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{float(v) * 100:+.1f}%"
