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
from .llm.verifier import EvidenceNumbers, extract_claims, hypothesis_leaks, verify_text
from .models import Artifact, EvidenceStrength, StatementType, Summary, SummaryItem, TreeNode

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

    @classmethod
    def from_node(cls, node: TreeNode) -> FindingInput:
        return cls(
            statement=node.statement,
            statement_type=node.statement_type,
            status=node.status,
            evidence_strength=node.evidence_strength,
            artifact_ids=list(node.artifact_ids),
            node_id=node.id,
        )


class _Narrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narrative: str


_SYSTEM = (
    "You write a short executive summary (at most 120 words) for business leaders from findings "
    "that analysts have already confirmed. Keep three kinds of statements distinct: observed facts, "
    "supported explanations, and unresolved hypotheses (always phrased as possibilities, never as facts). "
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
        if f.statement_type == "observation":
            summary.observations.append(item)
        else:
            summary.supported_explanations.append(item)
    if excluded:
        summary.notes.append(f"{excluded} findings were not confirmed and are not included")
    summary.narrative = _template_narrative(summary)
    if llm is not None and getattr(llm, "available", False) and confirmed:
        _llm_narrative(summary, llm, artifacts, nodes)
    return summary


def _template_narrative(summary: Summary) -> str:
    parts: list[str] = []
    if summary.observations:
        parts.append(summary.observations[0].text)
    if summary.supported_explanations:
        parts.append(
            "Supported explanations: " + " ".join(i.text for i in summary.supported_explanations[:3])
        )
    if summary.hypotheses:
        parts.append("Open questions: " + " ".join(i.text for i in summary.hypotheses[:3]))
    return " ".join(parts) if parts else "No confirmed findings yet."


def _llm_narrative(
    summary: Summary, llm: LLMProvider, artifacts: Sequence[Artifact], nodes: Sequence[TreeNode]
) -> None:
    payload = {
        "observed_facts": [i.text for i in summary.observations],
        "supported_explanations": [i.text for i in summary.supported_explanations],
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
    result = verify_text(out.narrative, ev)
    hypothesis_leak = hypothesis_leaks(out.narrative, [i.text for i in summary.hypotheses])
    if result.ok and not hypothesis_leak:
        summary.narrative = out.narrative
        summary.narrative_source = "llm_verified"
        return
    if not result.ok:
        summary.notes.append(
            "LLM narrative rejected: numbers not found in any artifact: " + ", ".join(result.rejected_texts)
        )
    if hypothesis_leak:
        summary.notes.append("LLM narrative rejected: it restated an unresolved hypothesis as fact")
