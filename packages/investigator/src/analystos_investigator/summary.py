"""Executive summary from confirmed findings only (spec §38, §65; OUT-03).

The summary has three sections: observed facts, supported explanations and unresolved
hypotheses. Each item is the finding's own statement (whose numbers come from executed
artifacts), labelled with its evidence strength. Findings that are not confirmed are
excluded and counted.

An optional LLM may reword the three sections into a short narrative. The narrative is
accepted only if every number in it can be reproduced from the artifacts behind the
included findings (:mod:`analystos_investigator.llm.verifier`); otherwise the deterministic
narrative is used and the rejection is recorded in ``notes``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .evidence import phrase_hypothesis
from .llm.base import LLMError, LLMProvider, Message
from .llm.untrusted import UNTRUSTED_DATA_POLICY, render_untrusted
from .llm.verifier import EvidenceNumbers, causal_leaks, extract_claims, hypothesis_leaks, verify_text
from .models import (
    Artifact,
    BridgeStep,
    EvidenceStrength,
    Investigation,
    InvestigationTree,
    PlanBridge,
    StatementType,
    Summary,
    SummaryItem,
    TreeNode,
)

_STRENGTH_ORDER = {"strong": 0, "moderate": 1, "weak": 2, "hypothesis_only": 3}
_STRENGTH_LABEL = {
    "strong": "strong evidence",
    "moderate": "moderate evidence",
    "weak": "weak evidence",
    "hypothesis_only": "hypothesis only",
}


class FindingInput(BaseModel):
    """The parts of a finding the summary needs (the API maps its Finding rows to this)."""

    model_config = ConfigDict(extra="ignore")

    statement: str
    statement_type: StatementType
    status: str
    evidence_strength: EvidenceStrength = "moderate"
    artifact_ids: list[str] = Field(default_factory=list)
    node_id: str | None = None
    role: str | None = None
    """The node's role; ``related_signal`` findings are summarised apart from explanations."""
    metric_label: str | None = None

    @classmethod
    def from_node(cls, node: TreeNode) -> FindingInput:
        return cls(
            statement=node.statement,
            statement_type=node.statement_type,
            status=node.status,
            evidence_strength=node.evidence_strength,
            artifact_ids=list(node.artifact_ids),
            node_id=node.id,
            role=node.role,
            metric_label=node.metric_label,
        )


class _Narrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narrative: str


_SYSTEM = (
    "You write a short executive summary (at most 120 words) for business leaders from findings "
    "that analysts have already confirmed. Keep the kinds of statements distinct: observed facts, "
    "supported explanations, related signals (observations whose relationship to the change is not "
    "established: never present them as causes) and unresolved hypotheses (always phrased as "
    "possibilities, never as facts). "
    "Use only the findings provided. Do not add explanations, causes or numbers that are not in them; "
    "copy numbers exactly as written. " + UNTRUSTED_DATA_POLICY
)


def _coerce(findings: Iterable[TreeNode | FindingInput]) -> list[FindingInput]:
    out: list[FindingInput] = []
    for f in findings:
        out.append(FindingInput.from_node(f) if isinstance(f, TreeNode) else f)
    return out


def executive_summary(
    findings: Iterable[TreeNode | FindingInput],
    *,
    llm: LLMProvider | None = None,
    artifacts: Sequence[Artifact] = (),
    nodes: Sequence[TreeNode] = (),
) -> Summary:
    items = _coerce(findings)
    roles = {n.id: n.role for n in nodes}
    items = [
        f.model_copy(update={"role": roles.get(f.node_id or "")})
        if f.role is None and f.node_id in roles
        else f
        for f in items
    ]
    confirmed = [f for f in items if f.status.lower() == "confirmed"]
    excluded = len(items) - len(confirmed)
    confirmed.sort(key=lambda f: _STRENGTH_ORDER.get(f.evidence_strength, 9))
    summary = Summary(excluded_count=excluded)
    for f in confirmed:
        label = _STRENGTH_LABEL.get(f.evidence_strength, f.evidence_strength)
        if f.statement_type == "hypothesis":
            text = phrase_hypothesis(f.statement)
            summary.hypotheses.append(
                SummaryItem(
                    text=text,
                    node_id=f.node_id,
                    evidence_strength=f.evidence_strength,
                    artifact_ids=f.artifact_ids,
                )
            )
            continue
        item = SummaryItem(
            text=f"{f.statement.rstrip('.')} ({label}).",
            node_id=f.node_id,
            evidence_strength=f.evidence_strength,
            artifact_ids=f.artifact_ids,
        )
        if f.role == "related_signal":
            summary.related_signals.append(item)
        elif f.statement_type == "observation":
            summary.observations.append(item)
        else:
            summary.supported_explanations.append(item)
    if excluded:
        summary.notes.append(f"{excluded} findings were not confirmed and are not included")
    summary.narrative = _template_narrative(summary)
    if llm is not None and getattr(llm, "available", False) and confirmed:
        signals = [f.metric_label or "" for f in confirmed if f.role == "related_signal"]
        _llm_narrative(summary, llm, artifacts, nodes, signals)
    return summary


def _template_narrative(summary: Summary) -> str:
    parts: list[str] = []
    if summary.observations:
        parts.append(summary.observations[0].text)
    if summary.supported_explanations:
        parts.append(
            "Supported explanations: " + " ".join(i.text for i in summary.supported_explanations[:3])
        )
    if summary.related_signals:
        parts.append(
            "Related signals (relationship not established): "
            + " ".join(i.text for i in summary.related_signals[:2])
        )
    if summary.hypotheses:
        parts.append("Open questions: " + " ".join(i.text for i in summary.hypotheses[:3]))
    return " ".join(parts) if parts else "No confirmed findings yet."


def _llm_narrative(
    summary: Summary,
    llm: LLMProvider,
    artifacts: Sequence[Artifact],
    nodes: Sequence[TreeNode],
    signals: Sequence[str] = (),
) -> None:
    payload = {
        "observed_facts": [i.text for i in summary.observations],
        "supported_explanations": [i.text for i in summary.supported_explanations],
        "related_signals_not_established": [i.text for i in summary.related_signals],
        "unresolved_hypotheses": [i.text for i in summary.hypotheses],
    }
    messages = [
        Message(
            role="user",
            content=[
                "Write the executive summary from these confirmed findings.",
                render_untrusted("confirmed_findings", payload),
            ],
        )
    ]
    try:
        out, _usage = llm.complete_structured("synthesize_findings", _SYSTEM, messages, _Narrative, "large")
    except LLMError as exc:
        summary.notes.append(f"LLM narrative unavailable ({exc}); using the deterministic summary")
        return
    ev = EvidenceNumbers.from_run(artifacts, nodes)
    # Numbers that appear in the confirmed statements themselves are also citable.
    for i in [*summary.observations, *summary.supported_explanations, *summary.hypotheses]:
        for c in extract_claims(i.text):
            if c.kind == "percent":
                ev.percents.append(c.value)
            else:
                ev.values.append(c.value)
    for i in summary.related_signals:
        for c in extract_claims(i.text):
            (ev.percents if c.kind == "percent" else ev.values).append(c.value)
    result = verify_text(out.narrative, ev)
    hypothesis_leak = hypothesis_leaks(out.narrative, [i.text for i in summary.hypotheses])
    causal = causal_leaks(out.narrative, [s for s in signals if s])
    if result.ok and not hypothesis_leak and not causal:
        summary.narrative = out.narrative
        summary.narrative_source = "llm_verified"
        return
    if causal:
        summary.notes.append(
            "LLM narrative rejected: it stated a related signal (relationship not established) as a cause"
        )
    if not result.ok:
        summary.notes.append(
            "LLM narrative rejected: numbers not found in any artifact: " + ", ".join(result.rejected_texts)
        )
    if hypothesis_leak:
        summary.notes.append("LLM narrative rejected: it restated an unresolved hypothesis as fact")


class VarianceReview(BaseModel):
    """A one-page operating review of a gap to plan, built only from accepted evidence.

    Sections: the headline and premise, the plan-to-actual bridge (a table and a waterfall), three
    to five "what explains it" bullets that each end in a measured operational cause, the related
    signals (relationship not established) and a measured watch list. Every number is a node value
    from executed queries. A part of the gap counts once its node is confirmed (its drill-down comes
    with it; rejected nodes never do); unconfirmed parts are one "Not yet confirmed" bridge step, so
    the waterfall still closes, and are listed in ``pending``.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    period: str | None = None
    headline: str | None = None
    premises: list[str] = Field(default_factory=list)
    explanation: list[str] = Field(default_factory=list)
    """The long-form reconciled sentences (as in the brief), restricted to confirmed parts."""
    bridge: PlanBridge | None = None
    causes: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    watch: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    reconciled: bool = False
    complete: bool = False
    """Every part of the explanation, the headline and the premise are confirmed."""
    confirmed_count: int = 0
    markdown: str = ""


def variance_review(
    investigation: Investigation, confirmed: Iterable[str] | None = None
) -> VarianceReview | None:
    """Operating review of a plan investigation from its confirmed nodes (or ``confirmed`` ids).

    Returns None when the investigation has no reconciled decomposition of a gap to plan.
    """
    from . import charts
    from .formatting import fmt_change, fmt_value
    from .investigation import cause_text, variance_sentences

    tree = investigation.tree
    if not tree.root_id:
        return None
    root = tree.root()
    if not any(
        n.role == "reconciliation" and n.reconciliation is not None for n in tree.children_of(root.id)
    ):
        return None
    ids = set(confirmed) if confirmed is not None else {n.id for n in tree.nodes if n.status == "confirmed"}
    dropped = {n.id for n in tree.nodes if n.status in ("rejected", "failed")}
    ids -= dropped
    # rejected nodes and their drill-downs never feed the review
    kept_tree = tree.model_copy(update={"nodes": [n for n in tree.nodes if not _under(tree, n.id, dropped)]})
    ctx = investigation.plan.context if investigation.plan else None
    period = ctx.window.display() if ctx else None
    s = variance_sentences(tree, list(investigation.related_signals), window=period, include=ids)
    fmt = root.metric_format
    by_id = tree.by_id()
    bridge = investigation.bridge
    review_bridge: PlanBridge | None = None
    causes: list[str] = []
    watch: list[str] = []
    pending_steps: list[str] = []
    if bridge is not None:
        steps = [st for st in bridge.steps if st.node_id in ids]
        missing = [st for st in bridge.steps if st.node_id not in ids]
        pending_steps = [st.label for st in missing]
        running = bridge.start
        rebuilt = []
        for st in steps:
            running += st.amount
            rebuilt.append(st.model_copy(update={"running_total": running}))
        if missing:
            amount = sum(st.amount for st in missing)
            running += amount
            rebuilt.append(BridgeStep(label="Not yet confirmed", amount=amount, running_total=running))
        review_bridge = bridge.model_copy(
            update={
                "steps": rebuilt,
                "chart": charts.waterfall_chart(
                    f"{bridge.metric_label}: plan to actual" + (f", {period}" if period else ""),
                    bridge.start_label,
                    bridge.end_label,
                    bridge.start,
                    bridge.end,
                    [(st.label, st.amount) for st in rebuilt],
                ),
            }
        )
        material = [st for st in steps if abs(st.amount) >= 0.05 * abs(bridge.gap or 1.0)]
        for st in sorted(material, key=lambda st: -abs(st.amount))[:5]:
            node = by_id.get(st.node_id or "")
            if node is None or node.members:
                continue
            why = cause_text(kept_tree, node) if node.id in kept_tree.by_id() else ""
            causes.append(f"{st.label}, {fmt_change(st.amount, fmt)}" + (f": {why}." if why else "."))
            watch.extend(_watch_items(kept_tree, node))
    for sig in investigation.related_signals:
        if sig.node_id in ids:
            watch.append(
                sig.statement.split(" Relationship")[0].rstrip(".")
                + " (related signal; relationship not established)"
            )
    watch = list(dict.fromkeys(watch))[:6]
    complete = (
        s.headline is not None
        and all(k.id in ids for k in tree.children_of(root.id) if k.role == "premise")
        and not s.pending
        and not pending_steps
    )
    metric = root.metric_label or root.metric_id or "the metric"
    title = f"Operating review: {metric} vs plan" + (f", {period}" if period else "")
    md: list[str] = []
    if s.headline:
        md.append(f"**{s.headline}**" + (" " + " ".join(s.premises) if s.premises else ""))
    if review_bridge is not None and review_bridge.steps:
        rows = [
            f"| {review_bridge.start_label} | | {fmt_value(review_bridge.start, fmt)} |",
            *[
                f"| {st.label} | {fmt_change(st.amount, fmt)} | {fmt_value(st.running_total, fmt)} |"
                for st in review_bridge.steps
            ],
            f"| {review_bridge.end_label} | {fmt_change(review_bridge.gap, fmt)} | {fmt_value(review_bridge.end, fmt)} |",
        ]
        md.append(
            "**Plan to actual**\n\n| Step | Effect | Running total |\n|---|---:|---:|\n" + "\n".join(rows)
        )
    if causes:
        md.append("**What explains it**\n" + "\n".join(f"- {c}" for c in causes))
    if s.related:
        md.append(
            "**Related signals (not established as causes)**\n" + "\n".join(f"- {r}" for r in s.related)
        )
    if watch:
        md.append("**What to watch**\n" + "\n".join(f"- {w}" for w in watch))
    pending = list(dict.fromkeys([*s.pending, *pending_steps]))
    if pending:
        md.append("**Not yet confirmed:** " + "; ".join(pending) + ". Confirm them to include them.")
    if not (s.headline or s.premises or s.explanation or s.related):
        md.insert(
            0, "No findings of this investigation are confirmed yet; confirm findings to build the review."
        )
    return VarianceReview(
        title=title,
        period=period,
        headline=s.headline,
        premises=s.premises,
        explanation=s.explanation,
        bridge=review_bridge,
        causes=causes,
        related=s.related,
        watch=watch,
        pending=pending,
        node_ids=s.node_ids,
        reconciled=s.reconciled,
        complete=complete,
        confirmed_count=len(ids),
        markdown="\n\n".join(md),
    )


def _under(tree: InvestigationTree, node_id: str, roots: set[str]) -> bool:
    """True when ``node_id`` is one of ``roots`` or below one."""
    index = tree.by_id()
    cur = index.get(node_id)
    while cur is not None:
        if cur.id in roots:
            return True
        cur = index.get(cur.parent_id or "")
    return False


def _watch_item(node: TreeNode) -> str:
    """A segment's measurement with the segments above it for context: "Lumber / Pine Co: costs
    20.0% above plan"; a line with no segment keeps its own first clause."""
    head = node.statement.split("; ")[0].rstrip(".")
    above = [str(s.value) for s in node.segment_path[:-1] if s.value is not None]
    return " / ".join([*above, head]) if node.segment is not None and above else head


def _watch_items(tree: InvestigationTree, node: TreeNode) -> list[str]:
    """Measured items to watch: the segments where a part of the gap concentrates, deepest first."""
    from .investigation import _drivers, _leaders

    out: list[str] = []
    stack = [node]
    while stack:
        cur = stack.pop(0)
        kids = [
            k
            for k in _drivers(tree, cur)
            if (k.root_effect or 0.0) * (cur.root_effect or 0.0) > 0
            and abs(k.root_effect or 0.0) >= 0.2 * abs(cur.root_effect or 0.0)
        ]
        lead = _leaders(tree, cur)
        if kids and not cur.members:
            stack.extend(kids[:1])
        elif lead:
            deeper = _leaders(tree, lead[0])
            targets = [deeper[0]] if deeper else lead[:2]
            out.extend(_watch_item(t) for t in targets)
        elif cur is not node:
            out.append(_watch_item(cur))
    return out
