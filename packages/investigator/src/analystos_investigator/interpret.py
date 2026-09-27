"""Deterministic question interpretation (spec §14-16, §45-46; INV-01, SEM-05).

``interpret`` resolves the metric(s), period, comparison baseline, filters, breakdown
dimensions and intent of a business question using only the semantic model, the glossary,
the engine's business calendar and (optionally) an index of dimension values.

Ambiguity policy (spec §15): when a term maps to more than one canonical metric, the term
is returned in ``Interpretation.ambiguous`` with its candidates and no metric is chosen
for it. The caller resolves it by passing ``choices={term: metric_id}``. An LLM may only
add a *suggestion* (``llm_suggestion``) restricted to the known candidates; it is never
applied automatically.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Literal

from analystos_engine.calendar import CalendarError, previous_period, resolve_period
from analystos_engine.semantic.models import SemanticModel
from analystos_engine.types import TimeWindow
from pydantic import BaseModel, ConfigDict

from .llm.base import LLMError, LLMProvider, Message
from .llm.untrusted import UNTRUSTED_DATA_POLICY, render_untrusted, render_user_request
from .models import (
    AmbiguousTerm,
    ComparisonKind,
    FilterSpec,
    Intent,
    Interpretation,
    MetricCandidate,
    Premise,
    SegmentComparison,
    UnresolvedFilter,
)
from .templates import choose_template, metric_matches_role
from .vocabulary import TermHit, Vocabulary, tokenize

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_MONTH_NUM = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# Order matters: more specific patterns first. Each pattern's full match is the expression.
_TIME_PATTERNS: list[tuple[str, str]] = [
    ("range", r"\b\d{4}-\d{2}-\d{2}\s+(?:to|through|until|-)\s+\d{4}-\d{2}-\d{2}\b"),
    ("date", r"\b\d{4}-\d{2}-\d{2}\b"),
    (
        "month_day",
        rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b(?!\s*(?:days|weeks|months))",
    ),
    ("iso_month", r"\b\d{4}-\d{2}\b|\b\d{1,2}/\d{4}\b"),
    (
        "fiscal",
        r"\b(?:fiscal\s+q[1-4]|fq[1-4])(?:\s+(?:fy\s?)?\d{2,4})?\b|\bfy\s?\d{2,4}\b|"
        r"\b(?:this|last|previous|prior|current)\s+fiscal\s+(?:year|quarter)\b",
    ),
    ("quarter", r"\bq[1-4](?:\s*(?:fy\s?)?\d{4})?\b|\b\d{4}[\s-]q[1-4]\b"),
    ("month", rf"\b(?:{_MONTHS})\.?(?:\s+\d{{4}})?\b"),
    ("relative", r"\b(?:this|last|previous|prior|current)\s+(?:week|month|quarter|year)\b"),
    ("to_date", r"\b(?:ytd|mtd|qtd|fytd|year to date|month to date|quarter to date)\b"),
    ("rolling", r"\b(?:rolling|last|past|trailing)\s+\d+\s+(?:days?|weeks?|months?)\b"),
    ("day_word", r"\b(?:yesterday|today)\b"),
    ("year", r"\b20\d{2}\b"),
]

_CMP = r"(?:vs\.?|versus|compared?\s+(?:to|with)|against|relative\s+to)"
_COMPARISON_PATTERNS: list[tuple[ComparisonKind, str]] = [
    (
        "budget",
        rf"\b{_CMP}\s+(?:the\s+)?(?:budget|plan|target)s?\b|\b(?:below|above|under|over|missed|miss)\s+(?:the\s+)?(?:budget|plan|target)\b|\b(?:budget|plan|target)\s+(?:miss|shortfall|gap|variance)\b|\bbudget\s+(?:vs\.?|versus)\s+actuals?\b|\bactuals?\s+(?:vs\.?|versus)\s+(?:budget|plan)\b",
    ),
    (
        "forecast",
        rf"\b{_CMP}\s+(?:the\s+)?forecasts?\b|\b(?:below|above|under|missed|miss)\s+(?:the\s+)?forecast\b|\bforecast\s+(?:miss|shortfall|gap|variance|error)\b|\bactuals?\s+(?:vs\.?|versus)\s+forecast\b",
    ),
    (
        "yoy",
        rf"\byear[\s-]over[\s-]year\b|\byoy\b|\by/y\b|\b(?:{_CMP}|from)\s+(?:the\s+)?(?:same\s+(?:period|month|quarter|week|day)\s+)?(?:last|previous|prior)\s+year\b|\bsame\s+(?:period|month|quarter)\s+last\s+year\b",
    ),
    (
        "pop",
        rf"\b(?:month|quarter|week|period)[\s-]over[\s-](?:month|quarter|week|period)\b|\b(?:mom|qoq|wow|m/m|q/q)\b|\bsequential(?:ly)?\b|\b{_CMP}\s+(?:the\s+)?(?:last|previous|prior)\s+(?:month|quarter|week|period)\b",
    ),
]

_NEGATIONS = {
    "excluding",
    "except",
    "exclude",
    "without",
    "not",
    "ex",
    "excl",
    "excluded",
    "omitting",
    "minu",
}
_FILTER_TRIGGER = re.compile(
    r"\b(excluding|except(?:\s+for)?|exclude|without|other\s+than|omitting)\s+"
    r"(.+?)(?=$|[,.;?!]|\s(?:vs\.?|versus|compared|in|for|by|during|over|since|from|and\s+why)\b)"
)
_COMMON_VALUE_WORDS = {
    "new",
    "old",
    "other",
    "all",
    "none",
    "yes",
    "no",
    "true",
    "false",
    "open",
    "closed",
    "active",
    "inactive",
    "unknown",
    "total",
    "na",
    "null",
    "n",
    "y",
    "standard",
    "regular",
    "normal",
    "misc",
}

_WHY = re.compile(
    r"\b(why|what\s+(?:drove|caused|explains|happened|is\s+driving|drives)|explain|reasons?|drivers?|"
    r"root\s+cause|behind|responsible|contribut\w*|account(?:s|ed)?\s+for|attribut\w*|drove|driv(?:e|es|ing))\b"
)
_ANOMALY = re.compile(
    r"\b(unexpected(?:ly)?|anomal\w*|spike[sd]?|outliers?|sudden(?:ly)?|abnormal\w*|unusual\w*)\b"
)
_FORECAST = re.compile(r"\b(forecast|predict\w*|projection|project(?:ed)?|expect(?:ed)?\s+next|outlook)\b")
_TREND = re.compile(r"\b(trend\w*|over\s+time|trajectory|history|historical|monthly|weekly|seasonal\w*)\b")
_BREAKDOWN = re.compile(r"\b(break\s*(?:it|this)?\s*down|breakdown|split|by|per|across|each|which)\b")
_COMPARE = re.compile(r"\b(compare|comparison|vs\.?|versus|against|relative\s+to)\b")
_DECREASE = re.compile(
    r"\b(declin\w*|drop\w*|fell|fall\w*|decreas\w*|down|lower|shrink\w*|shrank|miss\w*|los[st]\w*|worse|compress\w*|contract\w*|weak\w*|dip\w*|slump\w*)\b"
)
_INCREASE = re.compile(
    r"\b(increas\w*|grow\w*|grew|ris[ei]\w*|rose|up|higher|jump\w*|spik\w*|surg\w*|improv\w*|gain\w*|expand\w*|beat)\b"
)


@dataclass
class _TimeHit:
    start: int
    end: int
    kind: str
    text: str
    window: TimeWindow


class _DisambiguationSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_id: str | None
    rationale: str


_DISAMBIGUATION_SYSTEM = (
    "You help an analytics application interpret a business question. A term in the question "
    "maps to several governed metric definitions. Suggest which candidate metric the analyst most "
    "likely means, or null if the question does not say. You may only answer with one of the "
    "candidate metric ids you are given. Your suggestion is shown to the analyst, who decides. "
    + UNTRUSTED_DATA_POLICY
)


def _mask(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _month_day_window(expr: str, today: dt.date) -> TimeWindow | None:
    m = re.match(rf"({_MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", expr)
    if not m:
        return None
    month = _MONTH_NUM[m.group(1)[:3]]
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else today.year
    try:
        d = dt.date(year, month, day)
    except ValueError:
        return None
    if not m.group(3) and d > today:
        d = d.replace(year=today.year - 1)
    return TimeWindow(start=d, end=d + dt.timedelta(days=1), kind="day", label=d.strftime("%b %d, %Y"))


def _resolve_expr(kind: str, expr: str, today: dt.date, model: SemanticModel) -> TimeWindow | None:
    if kind == "month_day":
        return _month_day_window(expr, today)
    if kind == "range":
        expr = re.sub(r"\s+(?:through|until|-)\s+", " to ", expr)
    try:
        return resolve_period(expr, today, model.calendar)
    except (CalendarError, ValueError):
        return None


def find_time_expressions(text: str, today: dt.date, model: SemanticModel) -> tuple[list[_TimeHit], str]:
    """All period expressions in ``text`` (lowercase), in order, plus the text with them masked."""
    hits: list[_TimeHit] = []
    masked = text
    for kind, pattern in _TIME_PATTERNS:
        for m in re.finditer(pattern, masked):
            expr = m.group(0).strip()
            if kind == "month" and expr.startswith("may") and not re.search(r"\d", expr):
                before = masked[max(0, m.start() - 8) : m.start()]
                if not re.search(r"\b(in|for|during|of|since|through)\s+$", before):
                    continue  # "may" as a verb
            if kind == "month" and expr in (
                "mar",
                "jun",
                "jul",
                "sep",
                "oct",
                "dec",
                "nov",
                "jan",
                "feb",
                "apr",
                "aug",
            ):
                before = masked[max(0, m.start() - 8) : m.start()]
                if not re.search(r"\b(in|for|during|of|since|through|vs\.?|versus|to|and)\s+$", before):
                    continue
            window = _resolve_expr(kind, expr, today, model)
            if window is None:
                continue
            hits.append(_TimeHit(m.start(), m.end(), kind, expr, window))
            masked = _mask(masked, m.start(), m.end())
    hits.sort(key=lambda h: h.start)
    return hits, masked


def build_value_index(
    store: object, model: SemanticModel, *, max_values: int = 2000, dimensions: list[str] | None = None
) -> dict[str, list[str]]:
    """Distinct values of categorical dimensions, for filter resolution.

    Queries go through the store's read-only path. Dimensions with more than ``max_values``
    distinct values are skipped (their values cannot be matched reliably).
    """
    from analystos_engine.sqlsafety import ensure_read_only

    index: dict[str, list[str]] = {}
    for d in sorted(model.dimensions, key=lambda d: d.name):
        if d.type != "categorical" or (dimensions is not None and d.name not in dimensions):
            continue
        try:
            ent = model.get_entity(d.entity)
        except Exception:
            continue
        sql = (
            f"SELECT DISTINCT CAST(({d.expr}) AS VARCHAR) AS v FROM {ent.table} "
            f"WHERE ({d.expr}) IS NOT NULL ORDER BY 1 LIMIT {max_values + 1}"
        )
        try:
            res = store.execute_read(ensure_read_only(sql), limit=max_values + 1)  # type: ignore[attr-defined]
        except Exception:
            continue
        values = [str(r[0]) for r in res.rows if r and r[0] is not None and str(r[0]).strip()]
        if len(values) <= max_values:
            index[d.name] = values
    return index


def interpret(
    question: str,
    model: SemanticModel,
    today: dt.date | None = None,
    llm: LLMProvider | None = None,
    *,
    value_index: dict[str, list[str]] | None = None,
    choices: dict[str, str] | None = None,
) -> Interpretation:
    today = today or dt.date.today()
    choices = {k.lower(): v for k, v in (choices or {}).items()}
    notes: list[str] = []
    text = " " + question.lower().strip() + " "
    text = text.replace("’", "'")

    # 1. comparison phrases (removed before period and metric matching)
    comparison: ComparisonKind | None = None
    explicit_baseline: TimeWindow | None = None
    for kind, pattern in _COMPARISON_PATTERNS:
        m = re.search(pattern, text)
        if m:
            comparison = comparison or kind
            text = _mask(text, m.start(), m.end())
    m_explicit = re.search(r"\b(?:vs\.?|versus|compared\s+(?:to|with)|against)\s+(?:the\s+)?", text)

    # 2. periods
    time_hits, text = find_time_expressions(text, today, model)
    window: TimeWindow | None = None
    if time_hits:
        if m_explicit and len(time_hits) >= 2 and any(h.start >= m_explicit.end() - 1 for h in time_hits):
            after = [h for h in time_hits if h.start >= m_explicit.end() - 1]
            before = [h for h in time_hits if h.start < m_explicit.start()]
            explicit_baseline = after[0].window
            window = (before or [h for h in time_hits if h is not after[0]])[0].window
            comparison = comparison or "pop"
            text = _mask(text, m_explicit.start(), m_explicit.end())
        else:
            window = time_hits[0].window
            if len(time_hits) > 1:
                notes.append(f"several periods mentioned; using '{time_hits[0].text}' as the analysis period")
    period_defaulted = window is None
    if window is None:
        window = resolve_period("last month", today, model.calendar)
        notes.append(f"no period in the question; defaulted to the last complete month ({window.display()})")

    # 3. vocabulary matches over the remaining text
    tokens = tokenize(text)
    vocab = Vocabulary(model, value_index)
    hits = vocab.match(tokens)

    metric_ids: list[str] = []
    ambiguous: list[AmbiguousTerm] = []
    resolved_terms: dict[str, str] = {}
    dims_named: list[tuple[TermHit, str]] = []
    value_hits: list[TermHit] = []

    term_positions: dict[str, int] = {}
    metric_positions: dict[str, int] = {}
    named_plan: list[str] = []
    for hit in hits:
        if hit.kind not in ("value", "dimension"):
            term_positions.setdefault(hit.span.text, hit.span.start)
        if hit.kind == "dimension":
            dims_named.extend((hit, d) for d in hit.targets)
            continue
        if hit.kind == "value":
            value_hits.append(hit)
            continue
        cands = [c for c in hit.targets if model.has_metric(c)]
        if comparison in ("budget", "forecast") and cands:
            actual = [c for c in cands if not _is_plan_metric(model.get_metric(c), comparison)]
            if not actual:
                # "compare to budget": the plan metric names the baseline, not a metric to explain.
                named_plan.extend(c for c in cands if c not in named_plan)
                continue
            cands = actual
        if not cands:
            continue
        term = hit.span.text
        chosen: str | None = None
        reason = ""
        if term in choices:
            if choices[term] in cands:
                chosen = choices[term]
                reason = "chosen by the analyst"
            else:
                notes.append(f"choice {choices[term]!r} for '{term}' is not one of its candidates {cands}")
        if chosen is None:
            canonical = [c for c in cands if model.get_metric(c).canonical]
            if len(cands) == 1:
                chosen = cands[0]
                reason = hit.source
                if not model.get_metric(chosen).canonical:
                    notes.append(
                        f"'{term}' maps to non-canonical metric {chosen!r}; no canonical definition exists"
                    )
            elif len(canonical) == 1:
                chosen = canonical[0]
                reason = f"{hit.source}; the only canonical definition among {cands}"
        if chosen is None:
            pool = [c for c in cands if model.get_metric(c).canonical] or cands
            if any(a.term == term for a in ambiguous):
                continue
            ambiguous.append(
                AmbiguousTerm(
                    term=term,
                    candidates=[
                        MetricCandidate(
                            metric_id=c,
                            label=model.get_metric(c).display_name,
                            description=model.get_metric(c).description,
                            matched_by=hit.source,
                        )
                        for c in pool
                    ],
                    reason=f"'{term}' matches {len(pool)} governed metric definitions ({hit.source}); "
                    "choose one",
                )
            )
            continue
        if hit.kind == "head" and chosen in metric_ids:
            continue
        resolved_terms[term] = chosen
        term_positions.setdefault(term, hit.span.start)
        if chosen not in metric_ids:
            metric_ids.append(chosen)
            metric_positions[chosen] = hit.span.start
        if reason and hit.kind in ("glossary", "head"):
            notes.append(f"'{term}' resolved to {chosen!r} ({reason})")

    # 4. filters from dimension values
    filters: list[FilterSpec] = []
    unresolved: list[UnresolvedFilter] = []
    dim_positions = {h.span.start: d for h, d in dims_named}
    used_dim_hits: set[int] = set()
    for vh in value_hits:
        dims = sorted({d for d, _ in vh.dimension_values})
        nearby = {dim_positions[p] for p in dim_positions if vh.span.start - 2 <= p <= vh.span.end + 1}
        window_tokens = set(tokens[max(0, vh.span.start - 2) : vh.span.end + 2])
        nearby |= {d for d in dims if _dimension_words(model, d) & window_tokens}
        if len(dims) > 1:
            narrowed = [d for d in dims if d in nearby]
            if len(narrowed) == 1:
                dims = narrowed
        single_common = len(vh.span.text.split()) == 1 and vh.span.text in _COMMON_VALUE_WORDS
        if single_common and not (set(dims) & nearby):
            continue
        if len(dims) != 1:
            unresolved.append(
                UnresolvedFilter(
                    text=vh.span.text,
                    reason=f"'{vh.span.text}' is a value of several dimensions ({', '.join(dims)}); "
                    "say which one, e.g. 'in branch {vh.span.text}'",
                )
            )
            continue
        dim = dims[0]
        for p, d in dim_positions.items():
            if d == dim and vh.span.start - 2 <= p <= vh.span.end + 1:
                used_dim_hits.add(p)
        values = sorted({v for d, v in vh.dimension_values if d == dim})
        look = tokens[max(0, vh.span.start - 3) : vh.span.start]
        negated = any(t in _NEGATIONS for t in look) or " ".join(look[-2:]) == "other than"
        op: Literal["in", "not_in"] = "not_in" if negated else "in"
        existing = next((f for f in filters if f.dimension == dim and f.op == op), None)
        if existing:
            existing.values = sorted(set(existing.values) | set(values))
            existing.source_text += f", {vh.span.text}"
        else:
            filters.append(FilterSpec(dimension=dim, op=op, values=values, source_text=vh.span.text))
    for f in filters:
        if f.op == "in" and len(f.values) == 1:
            f.op = "eq"
        elif f.op == "not_in" and len(f.values) == 1:
            f.op = "neq"

    for m in _FILTER_TRIGGER.finditer(question.lower()):
        phrase = m.group(2).strip()
        covered = any(
            f.op in ("neq", "not_in") and any(tok in phrase for tok in f.source_text.split(", "))
            for f in filters
        )
        if not covered:
            unresolved.append(
                UnresolvedFilter(
                    text=f"{m.group(1)} {phrase}",
                    reason=f"'{phrase}' does not match a value of any dimension in the semantic model; "
                    "the exclusion was NOT applied",
                )
            )

    segment_cmp = _segment_comparison(value_hits, tokens)
    if segment_cmp is not None:
        filters = [f for f in filters if f.dimension != segment_cmp.dimension]
        comparison = "segment"
        notes.append(
            f"compares {segment_cmp.dimension} = {segment_cmp.current} with {segment_cmp.dimension} = "
            f"{segment_cmp.baseline} over the same period"
        )

    breakdown = []
    for h, d in dims_named:
        if h.span.start in used_dim_hits:
            continue
        if segment_cmp is not None and d == segment_cmp.dimension:
            continue
        if d not in breakdown:
            breakdown.append(d)

    # 5. intent and direction
    q = question.lower()
    intent: Intent
    is_day = window.kind == "day"
    if _FORECAST.search(q) and comparison != "forecast":
        intent = "forecast"
    elif _ANOMALY.search(q) or (is_day and _WHY.search(q)):
        intent = "anomaly"
    elif (
        _WHY.search(q)
        or (_DECREASE.search(q) or _INCREASE.search(q))
        and re.search(r"\b(did|has|have|was|were)\b", q)
    ):
        intent = "why_change"
    elif _COMPARE.search(q) or comparison in ("budget", "forecast") or explicit_baseline is not None:
        intent = "compare"
    elif breakdown and _BREAKDOWN.search(q):
        intent = "breakdown"
    elif _TREND.search(q):
        intent = "trend"
    else:
        intent = "lookup"
    direction = "unspecified"
    why_clause = _why_clause(q)
    dec, inc = _DECREASE.search(why_clause), _INCREASE.search(why_clause)
    if not (dec or inc):
        dec, inc = _DECREASE.search(q), _INCREASE.search(q)
    if dec and not inc:
        direction = "decrease"
    elif inc and not dec:
        direction = "increase"
    elif dec and inc:
        direction = "decrease" if dec.start() < inc.start() else "increase"

    ranking: Literal["growth", "decline"] | None = None
    if _FASTEST.search(q) and (breakdown or intent == "breakdown"):
        ranking = "decline" if direction == "decrease" else "growth"
        if intent != "why_change":
            intent = "breakdown"

    # 6. baseline
    if (
        explicit_baseline is not None
        and comparison in (None, "pop")
        and _same_span(explicit_baseline, previous_period(window, "yoy"))
    ):
        comparison = "yoy"  # "Q2 2026 versus Q2 2025" is a year-over-year comparison
    if comparison is None:
        if intent == "anomaly" and is_day:
            comparison = "pop"
        elif intent in ("why_change", "compare", "anomaly"):
            comparison = "pop"
            notes.append("no comparison given; compared with the previous period")
        else:
            comparison = "pop"
    baseline_window: TimeWindow | None
    baseline_metric_id: str | None = None
    if explicit_baseline is not None:
        baseline_window = explicit_baseline
    elif comparison == "segment":
        baseline_window = window
    elif comparison in ("budget", "forecast"):
        baseline_window = window
        if len(named_plan) == 1:
            baseline_metric_id = named_plan[0]
        else:
            baseline_metric_id, note = _baseline_metric(model, metric_ids, comparison)
            if note:
                notes.append(note)
    elif comparison == "yoy":
        baseline_window = previous_period(window, "yoy")
    elif intent == "anomaly" and is_day:
        baseline_window = TimeWindow(
            start=window.start - dt.timedelta(days=7),
            end=window.end - dt.timedelta(days=7),
            kind="day",
            label=(window.start - dt.timedelta(days=7)).strftime("%b %d, %Y"),
        )
        notes.append("single-day question: compared with the same weekday one week earlier")
    else:
        baseline_window = previous_period(window, "pop")

    if not metric_ids and not ambiguous and comparison in ("budget", "forecast"):
        actuals = _actuals_for_plan_metrics(model, comparison)
        if len(actuals) == 1:
            metric_ids = [actuals[0]]
            notes.append(f"no actual metric named; compared {actuals[0]!r} with its {comparison} metric")
            baseline_metric_id, note = _baseline_metric(model, metric_ids, comparison)
            if note:
                notes.append(note)
        elif len(actuals) > 1:
            ambiguous.append(
                AmbiguousTerm(
                    term=comparison,
                    candidates=[
                        MetricCandidate(
                            metric_id=a,
                            label=model.get_metric(a).display_name,
                            description=model.get_metric(a).description,
                            matched_by=f"actual metric paired with a {comparison} metric",
                        )
                        for a in actuals
                    ],
                    reason=f"several actual metrics have a {comparison} counterpart; choose one",
                )
            )
    if not metric_ids and not ambiguous and (ranking or breakdown or _WHY.search(q)):
        default, candidates = _default_metric(model)
        if default is not None:
            metric_ids = [default]
            notes.append(
                f"no metric named; assumed {model.get_metric(default).display_name} ({default!r}), the canonical "
                "core sales metric; say another metric to change it"
            )
        elif candidates:
            ambiguous.append(
                AmbiguousTerm(
                    term="metric",
                    candidates=[
                        MetricCandidate(
                            metric_id=c,
                            label=model.get_metric(c).display_name,
                            description=model.get_metric(c).description,
                            matched_by="canonical core metric (no metric named in the question)",
                        )
                        for c in candidates
                    ],
                    reason="the question names no metric; choose which one to rank",
                )
            )
    if not metric_ids and not ambiguous:
        notes.append("no metric from the semantic model was recognised in the question")

    subject_pos = _subject_position(tokens)
    if subject_pos is not None and len(metric_ids) > 1:
        metric_ids = _order_by_subject(metric_ids, metric_positions, subject_pos)
        notes.append(f"'{metric_ids[0]}' is the subject of the question; other metrics are context")
    premises = _find_premises(question, model, resolved_terms, vocab)
    if premises and period_defaulted:
        notes.append(
            "the question states "
            + ", ".join(f"'{p.text}'" for p in premises)
            + " but names no period; the period is chosen where the premise holds (see the premise check)"
        )
    template_hint = None
    if (
        metric_ids
        and _HEALTH.search(q)
        and any(metric_matches_role(model.get_metric(metric_ids[0]), r) for r in ("inventory", "stock"))
    ):
        template_hint = "inventory_health"
        intent = "breakdown"
        if period_defaulted and explicit_baseline is None and comparison == "pop":
            comparison = "yoy"
            baseline_window = previous_period(window, "yoy")
            notes.append(
                "inventory health compares the latest snapshot with the same snapshot a year earlier"
            )
    interp = Interpretation(
        question=question,
        metric_ids=metric_ids,
        ambiguous=ambiguous,
        window=window,
        baseline=baseline_window,
        comparison_kind=comparison,
        baseline_metric_id=baseline_metric_id,
        filters=filters,
        unresolved_filters=unresolved,
        breakdown_dimensions=breakdown,
        intent=intent,
        direction=direction,  # type: ignore[arg-type]
        resolved_terms=resolved_terms,
        term_positions=term_positions,
        subject_position=subject_pos,
        confidence_notes=notes,
        premises=premises,
        period_defaulted=period_defaulted,
        reference_date=today,
        segment_comparison=segment_cmp,
        ranking=ranking,
    )
    if template_hint:
        interp.template_id = template_hint
    if metric_ids:
        interp.template_id = choose_template(interp, model).id
    if ambiguous and llm is not None and getattr(llm, "available", False):
        _suggest_with_llm(interp, llm)
    return interp


_FASTEST = re.compile(
    r"\b(fastest|quickest|most\s+growth|biggest\s+(?:growth|gain|increase|decline|drop)|"
    r"grew\s+(?:the\s+)?most|declin\w*\s+(?:the\s+)?most|fell\s+(?:the\s+)?most|top\s+growing|best\s+growing)\b"
)
_HEALTH = re.compile(
    r"\b(unhealthy|healthy|health|slow[\s-]?moving|slow\s+movers?|dead\s+stock|aged|aging|ageing|excess|"
    r"obsolete|overstock\w*|stale|stuck)\b"
)
_FLAT = re.compile(
    r"\b(flat|stable|steady|unchanged|level|held\s+steady|about\s+the\s+same|roughly\s+the\s+same|"
    r"the\s+same|consistent)\b"
)
_CLAUSE_SPLIT = re.compile(
    r"[.;!?]|,|\b(?:but|while|whereas|although|though|yet|despite|even\s+though|however|and\s+yet)\b"
)
_SEGMENT_CONNECTORS = {"vs", "versus", "against", "compared", "compare"}


def _why_clause(q: str) -> str:
    """The clause that asks the question ("why did margin decline"), or the whole text."""
    for part in _CLAUSE_SPLIT.split(q):
        if _WHY.search(part):
            return part
    return q


def _same_span(a: TimeWindow, b: TimeWindow) -> bool:
    return a.start == b.start and a.end == b.end


def _segment_comparison(value_hits: list[TermHit], tokens: list[str]) -> SegmentComparison | None:
    """ "Dallas vs Houston": two values of one dimension joined by a comparison word."""
    for i, a in enumerate(value_hits):
        for b in value_hits[i + 1 :]:
            dims_a = {d for d, _ in a.dimension_values}
            dims_b = {d for d, _ in b.dimension_values}
            shared = sorted(dims_a & dims_b)
            if len(shared) != 1:
                continue
            between = set(tokens[a.span.end : b.span.start])
            if not between & _SEGMENT_CONNECTORS or b.span.start - a.span.end > 3:
                continue
            dim = shared[0]
            va = next(v for d, v in a.dimension_values if d == dim)
            vb = next(v for d, v in b.dimension_values if d == dim)
            if va != vb:
                return SegmentComparison(dimension=dim, current=va, baseline=vb)
    return None


def _default_metric(model: SemanticModel) -> tuple[str | None, list[str]]:
    """The canonical core sales metric used when a ranking question names no metric."""
    core = [m for m in model.metrics if m.canonical and "core" in [t.lower() for t in m.tags]]
    sales = [
        m.id
        for m in sorted(core, key=lambda m: m.id)
        if any(metric_matches_role(m, r) for r in ("revenue", "sales"))
        and m.kind == "simple"
        and m.format == "currency"
    ]
    if len(sales) == 1:
        return sales[0], []
    return None, sorted(m.id for m in core)


def _find_premises(
    question: str, model: SemanticModel, resolved: dict[str, str], vocab: Vocabulary
) -> list[Premise]:
    """Statements the question takes as given: "revenue was roughly flat", "despite flat revenue"."""
    out: list[Premise] = []
    q = question.lower().replace("’", "'")
    for clause in _CLAUSE_SPLIT.split(q):
        clause = clause.strip()
        if not clause or _WHY.search(clause) or clause.startswith(("how ", "what ", "which ", "who ")):
            continue
        if _FLAT.search(clause):
            expectation = "flat"
        elif _DECREASE.search(clause):
            expectation = "decrease"
        elif _INCREASE.search(clause):
            expectation = "increase"
        else:
            continue
        masked = find_time_expressions(" " + clause + " ", dt.date.today(), model)[1]
        for hit in vocab.match(tokenize(masked)):
            if hit.kind not in ("metric", "glossary", "head"):
                continue
            term = hit.span.text
            metric_id = resolved.get(term)
            if metric_id is None and len(hit.targets) == 1 and model.has_metric(hit.targets[0]):
                metric_id = hit.targets[0]
            if any(p.term == term for p in out):
                continue
            out.append(Premise(text=clause, term=term, metric_id=metric_id, expectation=expectation))  # type: ignore[arg-type]
    return out


_WHY_TOKENS = {"why", "what", "explain", "reason", "cause", "caused", "drove", "driving", "driver"}


def _subject_position(tokens: list[str]) -> int | None:
    """Token index of the change word in the "why" clause ("why did margin *decline*").

    The metric mentioned closest before it is the subject of the question; metrics in other
    clauses ("revenue was roughly flat") are context.
    """
    why_idx = next((i for i, t in enumerate(tokens) if t in _WHY_TOKENS), None)
    if why_idx is None:
        return None
    for i in range(why_idx, len(tokens)):
        if _DECREASE.fullmatch(tokens[i]) or _INCREASE.fullmatch(tokens[i]):
            return i
    return None


def _order_by_subject(metric_ids: list[str], positions: dict[str, int], subject_pos: int) -> list[str]:
    before = [m for m in metric_ids if positions.get(m, -1) < subject_pos]
    if not before:
        return metric_ids
    subject = max(before, key=lambda m: positions.get(m, -1))
    return [subject, *[m for m in metric_ids if m != subject]]


def _dimension_words(model: SemanticModel, dim_name: str) -> set[str]:
    d = model.get_dimension(dim_name)
    words = set(tokenize(d.name)) | set(tokenize(d.label or ""))
    for syn in d.synonyms:
        words |= set(tokenize(syn))
    try:
        ent = model.get_entity(d.entity)
        words |= set(tokenize(ent.name)) | set(tokenize(ent.label or ""))
    except Exception:
        pass
    return {w for w in words if w not in ("name", "id", "code", "type", "flag", "status")}


_PLAN_ROLES = {"budget": ("budget", "plan", "target"), "forecast": ("forecast",)}
_ROLE_WORDS = {"budget", "budgeted", "plan", "planned", "target", "forecast", "forecasted", "projected"}


def _is_plan_metric(metric: object, role: str) -> bool:
    return any(metric_matches_role(metric, r) for r in _PLAN_ROLES.get(role, (role,)))  # type: ignore[arg-type]


def _actuals_for_plan_metrics(model: SemanticModel, role: str) -> list[str]:
    """Actual metrics whose name equals a plan metric's name without the role word
    (forecast_revenue -> revenue, "Revenue Budget" -> Revenue)."""
    out: list[str] = []
    for pm in sorted(model.metrics, key=lambda m: m.id):
        if not _is_plan_metric(pm, role):
            continue
        for words in ({*tokenize(pm.id)} - _ROLE_WORDS, {*tokenize(pm.name)} - _ROLE_WORDS):
            if not words:
                continue
            for m in model.metrics:
                if m.id == pm.id or _is_plan_metric(m, role) or not m.canonical:
                    continue
                if words in ({*tokenize(m.id)}, {*tokenize(m.name)}) and m.id not in out:
                    out.append(m.id)
    return out


def _baseline_metric(model: SemanticModel, metric_ids: list[str], role: str) -> tuple[str | None, str | None]:
    primary = metric_ids[0] if metric_ids else None
    cands = [m for m in model.metrics if _is_plan_metric(m, role) and m.id not in metric_ids]
    exact = [m for m in cands if metric_matches_role(m, role)]
    cands = exact or cands
    if primary:
        base_words = set(tokenize(model.get_metric(primary).name)) | set(tokenize(primary))
        related = [m for m in cands if (set(tokenize(m.name)) | set(tokenize(m.id))) & base_words]
        cands = related or cands
    if len(cands) == 1:
        return cands[0].id, None
    if not cands:
        return (
            None,
            f"no {role} metric is defined in the semantic model; comparison against {role} unavailable",
        )
    return None, (
        f"several {role} metrics could apply ({', '.join(sorted(m.id for m in cands))}); "
        f"pick one with choices={{'{role}': <metric_id>}}"
    )


def apply_choices(interp: Interpretation, model: SemanticModel, choices: dict[str, str]) -> Interpretation:
    """Resolve ambiguous terms with the analyst's choices, without re-parsing the question."""
    remaining: list[AmbiguousTerm] = []
    metric_ids = list(interp.metric_ids)
    resolved = dict(interp.resolved_terms)
    notes = list(interp.confidence_notes)
    lowered = {k.lower(): v for k, v in choices.items()}
    for amb in interp.ambiguous:
        pick = lowered.get(amb.term.lower())
        if pick and pick in {c.metric_id for c in amb.candidates}:
            resolved[amb.term] = pick
            if pick not in metric_ids:
                metric_ids.append(pick)
            notes.append(f"'{amb.term}' resolved to {pick!r} (chosen by the analyst)")
        else:
            remaining.append(amb)
    premises = [
        p.model_copy(update={"metric_id": resolved.get(p.term, p.metric_id)}) for p in interp.premises
    ]
    positions = {resolved[t]: pos for t, pos in interp.term_positions.items() if t in resolved}
    if interp.subject_position is not None and len(metric_ids) > 1:
        metric_ids = _order_by_subject(metric_ids, positions, interp.subject_position)
    out = interp.model_copy(
        update={
            "metric_ids": metric_ids,
            "ambiguous": remaining,
            "resolved_terms": resolved,
            "confidence_notes": notes,
            "template_id": interp.template_id if interp.template_id == "inventory_health" else None,
            "premises": premises,
        }
    )
    if metric_ids:
        out.template_id = choose_template(out, model).id
    return out


def _suggest_with_llm(interp: Interpretation, llm: LLMProvider) -> None:
    for amb in interp.ambiguous:
        allowed = [c.metric_id for c in amb.candidates]
        payload = [
            {"metric_id": c.metric_id, "label": c.label, "description": c.description} for c in amb.candidates
        ]
        messages = [
            Message(
                role="user",
                content=[
                    render_user_request(interp.question),
                    f"Ambiguous term: {amb.term!r}. Candidate metric ids: {allowed}.",
                    render_untrusted("candidate_metrics", payload),
                ],
            )
        ]
        try:
            out, _ = llm.complete_structured(
                "interpret_question", _DISAMBIGUATION_SYSTEM, messages, _DisambiguationSuggestion, "small"
            )
        except LLMError:
            continue
        if out.metric_id in allowed:
            amb.llm_suggestion = out.metric_id
