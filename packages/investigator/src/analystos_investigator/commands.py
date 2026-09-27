"""Chat commands as structured workspace actions (spec §29-30; INV-09).

``parse_command`` turns phrases such as "Break this down by region", "Compare this quarter
with last quarter", "Exclude new stores", "Use gross revenue instead", "Save this as a
finding", "Show the SQL" or "Build me a report" into a typed :class:`Command`. Resolution
uses the same deterministic vocabulary as question interpretation, so metric ambiguity is
surfaced (never guessed) and filters that match no dimension value are reported as
unresolved instead of being dropped silently.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from analystos_engine.semantic.models import SemanticModel
from analystos_engine.types import TimeWindow
from pydantic import BaseModel, ConfigDict, Field

from .interpret import find_time_expressions, interpret
from .models import AmbiguousTerm, ComparisonKind, FilterSpec
from .vocabulary import Vocabulary, tokenize

CommandKind = Literal[
    "breakdown",
    "top_contributors",
    "compare",
    "exclude",
    "include",
    "switch_metric",
    "drill",
    "save_finding",
    "show_sql",
    "build_dashboard",
    "build_report",
    "rerun",
    "confirm_node",
    "reject_node",
    "question",
    "unknown",
]


class CommandContext(BaseModel):
    """What the command applies to: the current investigation/node and reference date."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    today: dt.date | None = None
    investigation_id: str | None = None
    node_id: str | None = None
    metric_id: str | None = None
    window: TimeWindow | None = None
    value_index: dict[str, list[str]] = Field(default_factory=dict)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CommandKind
    text: str
    target_node_id: str | None = None
    investigation_id: str | None = None
    dimension: str | None = None
    metric_id: str | None = None
    ambiguous: list[AmbiguousTerm] = Field(default_factory=list)
    comparison_kind: ComparisonKind | None = None
    window: TimeWindow | None = None
    baseline: TimeWindow | None = None
    filters: list[FilterSpec] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.ambiguous or self.unresolved) or self.kind == "unknown"


_SIMPLE: list[tuple[CommandKind, re.Pattern[str]]] = [
    ("show_sql", re.compile(r"\b(show|see|view|display|give)\b.*\b(sql|query|queries|code)\b|^sql\b")),
    (
        "save_finding",
        re.compile(r"\bsave\b.*\bfinding\b|\bmark\b.*\bas\s+(a\s+)?finding\b|\bpromote\b.*\bfinding\b"),
    ),
    ("build_dashboard", re.compile(r"\b(dashboard)\b")),
    ("build_report", re.compile(r"\b(report|memo|write[\s-]?up|summary\s+document)\b")),
    ("rerun", re.compile(r"^\s*(re-?run|run\s+(it|this)\s+again|refresh|recompute)\b")),
    ("confirm_node", re.compile(r"^\s*(confirm|approve|accept)\s+(this|it|that|the\s+finding)\b")),
    (
        "reject_node",
        re.compile(r"^\s*(reject|dismiss|discard)\s+(this|it|that)\b|\bthis\s+is\s+(wrong|not\s+right)\b"),
    ),
]

_BREAKDOWN = re.compile(
    r"\b(?:break|split|slice|group)\s*(?:this|it|that|them)?\s*(?:down\s+)?(?:by|per|across)\s+(?P<dim>.+)$"
    r"|^\s*(?:by|per|across)\s+(?P<dim2>.+)$|\bbreakdown\s+(?:by|of)\s+(?P<dim3>.+)$"
)
_TOP = re.compile(
    r"\b(?:show|list|which|who|what)\b.*?\b(?P<noun>[a-z_ ]+?)\s+(?:are\s+|were\s+)?"
    r"(?:responsible|driving|drove|behind|caused|causing|contributing|contributed|explain)"
)
_COMPARE = re.compile(r"\bcompare\b|\bvs\.?\b|\bversus\b|\bagainst\b|\bcompared\s+(?:to|with)\b")
_EXCLUDE = re.compile(
    r"^\s*(?:exclude|excluding|without|remove|drop|leave\s+out|filter\s+out)\s+(?P<what>.+)$"
)
_INCLUDE = re.compile(
    r"^\s*(?:only|just|filter\s+(?:to|on)|focus\s+on|restrict\s+to|limit\s+to)\s+(?P<what>.+)$"
)
_SWITCH = re.compile(
    r"^\s*(?:use|switch\s+to|show|change\s+(?:the\s+)?metric\s+to)\s+(?P<what>.+?)"
    r"(?:\s+instead(?:\s+of\s+.+)?)?\s*$"
)
_DRILL = re.compile(r"^\s*(?:drill|zoom|dig)\s+(?:down\s+)?(?:into|in\s+to|on)\s+(?P<what>.+)$")


_FILLER = frozenset(
    {"the", "a", "an", "metric", "instead", "of", "please", "number", "total", "this", "that"}
)


def _clean(text: str) -> str:
    return re.sub(r"[.!?]+$", "", text.strip().lower())


def _resolve_dimension(fragment: str, model: SemanticModel) -> str | None:
    vocab = Vocabulary(model)
    for hit in vocab.match(tokenize(fragment)):
        if hit.kind == "dimension" and len(hit.targets) == 1:
            return hit.targets[0]
        if hit.kind == "dimension":
            return sorted(hit.targets)[0] if len(set(hit.targets)) == 1 else None
    return None


def _resolve_dimension_all(fragment: str, model: SemanticModel) -> list[str]:
    vocab = Vocabulary(model)
    out: list[str] = []
    for hit in vocab.match(tokenize(fragment)):
        if hit.kind == "dimension":
            out.extend(t for t in hit.targets if t not in out)
    return out


def parse_command(text: str, context: CommandContext, model: SemanticModel) -> Command:
    raw = text.strip()
    t = _clean(raw)
    today = context.today or dt.date.today()
    base: dict[str, Any] = {
        "text": raw,
        "target_node_id": context.node_id,
        "investigation_id": context.investigation_id,
    }

    for kind, pat in _SIMPLE:
        if pat.search(t):
            if kind == "build_report" and "dashboard" in t:
                continue
            return Command(kind=kind, **base)

    m = _TOP.search(t)
    if m:
        dims = _resolve_dimension_all(m.group("noun"), model)
        if len(dims) == 1:
            direction = "decline" if re.search(r"declin|drop|decreas|fall|fell|loss|down", t) else "change"
            return Command(
                kind="top_contributors",
                dimension=dims[0],
                params={"sort": "share_of_change", "direction": direction},
                **base,
            )

    m = _DRILL.match(t)
    if m:
        what = m.group("what")
        interp = interpret(f"in {what}", model, today, value_index=context.value_index)
        eq = [f for f in interp.filters if f.op in ("eq", "in")]
        if eq:
            return Command(kind="drill", filters=eq, dimension=eq[0].dimension, **base)
        dim = _resolve_dimension(what, model)
        if dim:
            return Command(kind="breakdown", dimension=dim, **base)
        return Command(kind="drill", unresolved=[what], **base)

    m = _BREAKDOWN.search(t)
    if m:
        frag = m.group("dim") or m.group("dim2") or m.group("dim3") or ""
        dims = _resolve_dimension_all(frag, model)
        if len(dims) == 1:
            return Command(kind="breakdown", dimension=dims[0], **base)
        if len(dims) > 1:
            return Command(
                kind="breakdown",
                unresolved=[f"'{frag}' matches several dimensions: {', '.join(dims)}"],
                **base,
            )
        return Command(kind="breakdown", unresolved=[f"no dimension named '{frag}'"], **base)

    m = _EXCLUDE.match(t)
    if m:
        what = m.group("what")
        interp = interpret(f"excluding {what}", model, today, value_index=context.value_index)
        filters = [f for f in interp.filters if f.op in ("neq", "not_in")]
        unresolved = [u.reason for u in interp.unresolved_filters]
        if not filters and not unresolved:
            unresolved = [f"'{what}' does not match a value of any dimension; nothing was excluded"]
        return Command(kind="exclude", filters=filters, unresolved=unresolved, **base)

    m = _INCLUDE.match(t)
    if m:
        what = m.group("what")
        interp = interpret(f"in {what}", model, today, value_index=context.value_index)
        filters = [f for f in interp.filters if f.op in ("eq", "in")]
        unresolved = [u.reason for u in interp.unresolved_filters]
        if not filters:
            unresolved = unresolved or [
                f"'{what}' does not match a value of any dimension; no filter applied"
            ]
        return Command(kind="include", filters=filters, unresolved=unresolved, **base)

    if _COMPARE.search(t) or re.search(r"\b(yoy|year over year|last year|prior year|budget|forecast)\b", t):
        return _compare_command(t, base, context, model, today)

    m = _SWITCH.match(t)
    if m:
        what = m.group("what")
        interp = interpret(what, model, today)
        if interp.ambiguous:
            return Command(kind="switch_metric", ambiguous=interp.ambiguous, **base)
        if interp.metric_ids:
            matched = {w for term in interp.resolved_terms for w in tokenize(term)}
            leftover = [w for w in tokenize(what) if w not in matched and w not in _FILLER]
            notes = (
                [f"'{' '.join(leftover)}' did not match any metric; using {interp.metric_ids[0]!r}"]
                if leftover
                else []
            )
            return Command(kind="switch_metric", metric_id=interp.metric_ids[0], unresolved=notes, **base)

    interp = interpret(raw, model, today, value_index=context.value_index)
    if interp.metric_ids or interp.ambiguous:
        return Command(
            kind="question",
            metric_id=interp.primary_metric_id,
            ambiguous=interp.ambiguous,
            window=interp.window,
            baseline=interp.baseline,
            comparison_kind=interp.comparison_kind,
            filters=interp.filters,
            **base,
        )
    return Command(kind="unknown", unresolved=["the command was not recognised"], **base)


def _compare_command(
    t: str, base: dict[str, Any], context: CommandContext, model: SemanticModel, today: dt.date
) -> Command:
    if re.search(r"\b(budget|plan|target)\b", t):
        return Command(kind="compare", comparison_kind="budget", window=context.window, **base)
    if re.search(r"\bforecast\b", t):
        return Command(kind="compare", comparison_kind="forecast", window=context.window, **base)
    yoy_word = re.search(r"\b(yoy|year[\s-]over[\s-]year|same\s+(?:period|month|quarter)\s+last\s+year)\b", t)
    vs_last_year = re.search(
        r"\b(?:to|with|vs\.?|versus|against)\s+(?:the\s+)?(?:last|prior|previous)\s+year\b", t
    )
    if yoy_word or (vs_last_year and not re.search(r"\bthis\s+year\b", t)):
        return Command(kind="compare", comparison_kind="yoy", window=context.window, **base)
    hits, _ = find_time_expressions(" " + t + " ", today, model)
    if len(hits) >= 2:
        return Command(
            kind="compare", comparison_kind="pop", window=hits[0].window, baseline=hits[1].window, **base
        )
    if len(hits) == 1:
        return Command(
            kind="compare", comparison_kind="pop", window=context.window, baseline=hits[0].window, **base
        )
    if re.search(r"\b(previous|prior|last)\s+(period|month|quarter|week)\b", t):
        return Command(kind="compare", comparison_kind="pop", window=context.window, **base)
    dims = _resolve_dimension_all(t, model)
    if dims:
        return Command(kind="breakdown", dimension=dims[0], params={"compare_segments": True}, **base)
    return Command(
        kind="compare", unresolved=["no period, budget or segment to compare with was found"], **base
    )
