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
from .vocabulary import STOPWORDS, TermHit, Vocabulary, phrase_key, singular, tokenize

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
    ("period_year", rf"\b(?:q[1-4]|{_MONTHS})\.?\s+(?:of\s+)?(?:this|last|next)\s+year\b"),
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
    ("year_start", r"\b(?:the\s+)?(?:start|beginning)\s+of\s+(?:the|this)\s+year\b"),
    ("rolling", r"\b(?:rolling|last|past|trailing)\s+\d+\s+(?:days?|weeks?|months?)\b"),
    ("day_word", r"\b(?:yesterday|today)\b"),
    ("year", r"\b20\d{2}\b"),
]

_CMP = r"(?:vs\.?|versus|compared?\s+(?:to|with)|against|relative\s+to)"
_COMPARISON_PATTERNS: list[tuple[ComparisonKind, str]] = [
    (
        "budget",
        rf"\b{_CMP}\s+(?:the\s+)?(?:budget|plan|target)s?\b|\b(?:below|above|under|over|missed|miss)\s+(?:the\s+)?(?:budget|plan|target)\b|\b(?:budget|plan|target)\s+(?:miss|shortfall|gap|variance)\b|\bbudget\s+(?:vs\.?|versus)\s+actuals?\b|\bactuals?\s+(?:vs\.?|versus)\s+(?:budget|plan)\b|\b(?:of|than|to|from|versus|vs\.?)\s+what\s+we\s+(?:budgeted|planned|targeted)\b"
        rf"|\b{_CMP}\s+(?:the\s+|our\s+)?(?:[\w-]+\s+){{1,3}}?(?:budget|plan|target|aop)s?\b"
        r"|\b(?:actuals?|variance|gap|bridge|shortfall)\s+(?:\w+\s+){0,2}?to\s+(?:the\s+)?(?:budget|plan|target)\b"
        r"|\bto\s+the\s+(?:budget|plan|target)\b"
        r"|\b(?:ahead\s+of|behind|over|above|under|below)\s+(?:the\s+|our\s+)?(?:budget|plan|target|quota)\b"
        r"|\boverrun\b|\bblew\s+(?:through|past)\s+(?:the\s+|our\s+)?(?:budget|plan)\b"
        r"|\b(?:hitting|making|meeting)\s+(?:their\s+|our\s+)?(?:target|plan)s?\b",
    ),
    (
        "forecast",
        rf"\b{_CMP}\s+(?:the\s+)?forecasts?\b|\b(?:below|above|under|missed|miss)\s+(?:the\s+)?forecast\b|\bforecast\s+(?:miss|shortfall|gap|variance|error)\b|\bactuals?\s+(?:vs\.?|versus)\s+forecast\b",
    ),
    (
        "yoy",
        rf"\byear[\s-](?:over|on)[\s-]year\b|\byoy\b|\by/y\b|\ba\s+year\s+(?:ago|earlier)\b|\bthan\s+(?:it\s+was\s+)?(?:a\s+year\s+ago|last\s+year)\b|\b(?:{_CMP}|from)\s+(?:the\s+)?(?:same\s+(?:period|month|quarter|week|day)\s+)?(?:last|previous|prior)\s+year\b|\bsame\s+(?:period|month|quarter)\s+last\s+year\b",
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
    r"\b(why|what\s+(?:drove|caused|explains|happened|is\s+driving|drives)|where\s+did\s+(?:the\s+)?\w+\s+go|"
    r"what'?s\s+going\s+on|what\s+is\s+going\s+on|explain|reasons?|drivers?|"
    r"root\s+cause|behind|responsible|contribut\w*|account(?:s|ed)?\s+for|attribut\w*|drove|driv(?:e|es|ing))\b"
)
_ANOMALY = re.compile(
    r"\b(unexpected(?:ly)?|anomal\w*|spike[sd]?|outliers?|sudden(?:ly)?|abnormal\w*|unusual\w*)\b"
)
_FORECAST = re.compile(r"\b(forecast|predict\w*|projection|project(?:ed)?|expect(?:ed)?\s+next|outlook)\b")
_TREND = re.compile(
    r"\b(trend\w*|over\s+time|trajectory|history|historical|monthly|weekly|seasonal\w*|by\s+(?:month|week|quarter))\b"
)
_BREAKDOWN = re.compile(r"\b(break\s*(?:it|this)?\s*down|breakdown|split|by|per|across|each|which)\b")
_COMPARE = re.compile(r"\b(compare|comparison|vs\.?|versus|against|relative\s+to)\b")
_DECREASE = re.compile(
    r"\b(declin\w*|drop\w*|fell|fall\w*|decreas\w*|down|lower|shrink\w*|shrank|miss\w*|short\w*|behind|los[st]\w*|worse|compress\w*|contract\w*|weak\w*|dip\w*|slump\w*)\b"
)
_INCREASE = re.compile(
    r"\b(increas\w*|grow\w*|grew|ris[ei]\w*|rose|up|higher|jump\w*|spik\w*|surg\w*|improv\w*|gain\w*|expand\w*|beat|doubl\w*|tripl\w*|balloon\w*|soar\w*|climb\w*)\b"
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
    if kind == "period_year":
        # "Q2 last year" / "June this year": the period in that calendar year
        year = (
            today.year + {"this": 0, "last": -1, "next": 1}[re.findall(r"(this|last|next)\s+year", expr)[0]]
        )
        expr = re.sub(r"\s+(?:of\s+)?(?:this|last|next)\s+year", f" {year}", expr)
        kind = "quarter" if expr.startswith("q") else "month"
    if kind == "year_start":
        expr = f"q1 {today.year}"  # "the start of the year": its first quarter
        kind = "quarter"
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
                after = masked[m.end() : m.end() + 10]
                if not (
                    re.search(r"\b(in|for|during|of|since|through|vs\.?|versus|to|and)\s+$", before)
                    or not before.strip()
                    or re.match(r"\s+(vs\.?|versus|to|and)\b", after)
                ):
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
    role_word: str | None = None
    comparison_spans: list[tuple[int, int]] = []
    for kind, pattern in _COMPARISON_PATTERNS:
        m = re.search(pattern, text)
        if m:
            comparison_spans.append((m.start(), m.end()))
            if comparison is None:
                comparison = kind
                word = re.search(r"\b(budget|plan|target|forecast)", m.group(0))
                role_word = word.group(1) if word else None
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
            if explicit_baseline.start > window.start and explicit_baseline.kind == window.kind:
                # "July vs August": the later period is the one being explained
                window, explicit_baseline = explicit_baseline, window
        elif (
            len(time_hits) >= 2
            and re.search(r"\bfrom\s*$", text[max(0, time_hits[0].start - 6) : time_hits[0].start])
            and re.search(r"^\s*(?:to|through|until)\s*$", text[time_hits[0].end : time_hits[1].start])
        ):
            # "fell from July to August": August against July
            window, explicit_baseline = time_hits[1].window, time_hits[0].window
            comparison = comparison or "pop"
        elif len(time_hits) >= 1 and re.search(
            r"\bthan\s+(?:we\s+were\s+|it\s+was\s+|they\s+were\s+)?(?:at|in|during)?\s*$",
            text[max(0, time_hits[0].start - 30) : time_hits[0].start],
        ):
            # "fewer than we were at the start of the year": that period is the baseline
            explicit_baseline = time_hits[0].window
            window = time_hits[1].window if len(time_hits) > 1 else None
        else:
            window = time_hits[0].window
            anchored = [h for h in time_hits if any(0 <= a - h.end <= 3 for a, _b in comparison_spans)]
            if len(time_hits) > 1 and anchored:
                window = anchored[0].window  # "in Q2 vs last year": the period the comparison is about
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
    unmeasured: list[str] = []
    head_only: set[str] = set()
    for hit in hits:
        if hit.kind == "unmeasured":
            unmeasured.append(hit.span.text)
            notes.append(
                f"'{hit.span.text}' is not measured in this data ({hit.source}); it is not substituted"
            )
            continue
        if hit.kind not in ("value", "dimension"):
            term_positions.setdefault(hit.span.text, hit.span.start)
        if hit.kind == "dimension":
            dims_named.extend((hit, d) for d in hit.targets)
            continue
        if hit.kind == "value":
            value_hits.append(hit)
            continue
        cands = [c for c in hit.targets if model.has_metric(c)]
        if cands:
            plans = [c for c in cands if _is_any_plan(model.get_metric(c))]
            actual = [c for c in cands if c not in plans]
            if plans and not actual:
                # A plan, budget or target named by itself ("our revenue target", "what we budgeted",
                # "on plan") names a baseline or a premise, never the metric to explain.
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
            else:
                top = _tree_ancestor(model, cands)
                if top is not None:
                    chosen = top
                    reason = f"{hit.source}; {top!r} is the parent of the others in its metric tree"
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
            if hit.kind == "head":
                head_only.add(chosen)
        elif hit.kind != "head":
            head_only.discard(chosen)
        if reason and hit.kind in ("glossary", "head"):
            notes.append(f"'{term}' resolved to {chosen!r} ({reason})")

    if unmeasured:
        # "net income after tax": a named but unmeasured concept is not replaced by a neighbour that only
        # shares a word with it
        metric_ids = [m for m in metric_ids if m not in head_only]
        ambiguous = [
            a for a in ambiguous if not any(c.matched_by.startswith("metrics named") for c in a.candidates)
        ]

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
        named_metrics = metric_ids or [c.metric_id for a in ambiguous for c in a.candidates]
        if len(dims) > 1 and named_metrics:
            usable = _usable_dimensions(model, named_metrics)
            narrowed = [d for d in dims if d in usable]
            if narrowed:
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

    matched_tokens = {t for h in hits for t in tokens[h.span.start : h.span.end]}
    for m in re.finditer(r"\b[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)+", question):
        # A proper name that matches no dimension value ("the Summit Select Signature line") is
        # reported, never silently dropped or guessed.
        words = tokenize(m.group(0))
        loose = [
            w
            for w in words
            if w not in matched_tokens
            and w not in STOPWORDS
            and not re.fullmatch(rf"q[1-4]|fy\d+|\d+|{_MONTHS}", w)
        ]
        if loose and len(words) >= 2 and not any(u.text == m.group(0) for u in unresolved):
            unresolved.append(
                UnresolvedFilter(
                    text=m.group(0),
                    reason=f"'{m.group(0)}' does not match a value of any dimension in the semantic model; "
                    "no filter was applied",
                )
            )
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
    usable_dims = _usable_dimensions(model, metric_ids) if metric_ids else set()
    for h, d in dims_named:
        if h.span.start in used_dim_hits:
            continue
        if segment_cmp is not None and d == segment_cmp.dimension:
            continue
        if usable_dims and d not in usable_dims:
            # "by channel" on a lead metric means its lead channel: a usable dimension named the same way
            alt = [
                x
                for x in sorted(usable_dims)
                if phrase_key(d)[-1:] == phrase_key(x)[-1:]
                or tokenize(model.get_dimension(x).label or "")[-1:]
                == tokenize(model.get_dimension(d).label or d)[-1:]
            ]
            if len(alt) == 1:
                d = alt[0]
        if d not in breakdown:
            breakdown.append(d)
    if usable_dims:
        named_words = {t for h, _d in dims_named for t in tokenize(h.span.text)}
        # "by region", and "which SKUs ..." (a ranking over that dimension's values)
        for word in re.findall(r"\b(?:by|which|what)\s+([a-z]+)", question.lower()):
            w = singular(word)
            if w in named_words:
                continue
            # singular() keeps "-us" words ("status"); a plural of a short label ("SKUs") still names it
            forms = {w, word[:-1]} if word.endswith("s") else {w}
            alt = [x for x in sorted(usable_dims) if forms & set(tokenize(model.get_dimension(x).label or x))]
            if len(alt) == 1 and alt[0] not in breakdown:
                breakdown.append(alt[0])

    q = question.lower()
    subject_pos = _subject_position(tokens)
    if subject_pos is not None and len(metric_ids) > 1:
        metric_ids = _order_by_subject(metric_ids, metric_positions, subject_pos)
        notes.append(f"'{metric_ids[0]}' is the subject of the question; other metrics are context")
    premises = _find_premises(question, model, resolved_terms, vocab, today)
    if subject_pos is None and len(metric_ids) > 1:
        # "We hit the revenue target, but profit missed plan": a metric the question takes as
        # given (a premise) is context; the metric left to explain is the subject.
        ordered = _order_by_premises(metric_ids, premises)
        if ordered != metric_ids:
            metric_ids = ordered
            notes.append(
                f"'{metric_ids[0]}' is the subject of the question; the others are stated as premises"
            )
    if subject_pos is None and len(metric_ids) > 1 and metric_ids == _order_by_premises(metric_ids, premises):
        # a metric and its own parts named together: the parent is the subject ("leads not converting")
        top = _tree_ancestor(model, metric_ids)
        if top is not None and top != metric_ids[0]:
            metric_ids = [top, *(m for m in metric_ids if m != top)]
    if len(metric_ids) >= 2 and re.search(r"\bor\b", q):
        # "was the decline orders or AOV?": alternatives within one identity ask about their parent
        parent = _common_parent(model, metric_ids)
        if parent is not None and parent not in metric_ids:
            metric_ids = [parent, *metric_ids]
            notes.append(f"{', '.join(metric_ids[1:])} are parts of {parent!r}; it is the subject")
    if not metric_ids and not ambiguous and named_plan and not unmeasured:
        # "quota attainment by rep": the plan metric names the yardstick; its actual is the subject
        planned = _plan_actual(model, named_plan[0])
        if planned is not None:
            metric_ids = [planned]
            comparison = comparison or "budget"
            role_word = role_word or (
                "target" if metric_matches_role(model.get_metric(named_plan[0]), "target") else None
            )
            notes.append(f"'{named_plan[0]}' is the yardstick; compared {planned!r} with it")
    plan_clauses = [
        c for c in _CLAUSE_SPLIT.split(q.replace("\u2019", "'")) if c.strip() and _plan_premise(c)
    ]
    if metric_ids and ambiguous:
        # a term only used inside "we sold what we said we'd sell" states the premise, not the subject
        kept = [a for a in ambiguous if not _only_in(a.term, q, plan_clauses)]
        for a in ambiguous:
            if a not in kept:
                notes.append(f"'{a.term}' is only used in a stated premise; not asked about")
        ambiguous = kept
    if metric_ids and ambiguous:
        sentences = [x for x in re.split(r"[.?!]", q) if x.strip()]
        subject_terms = [t for t, mid in resolved_terms.items() if mid == metric_ids[0]]

        def sentence_of(term: str) -> int:
            return next(
                (k for k, x in enumerate(sentences) if re.search(rf"(?<!\w){re.escape(term)}", x)), -1
            )

        home = {sentence_of(t) for t in subject_terms}
        keep = []
        for a in ambiguous:
            generic = all(c.matched_by.startswith("metrics named") for c in a.candidates)
            if generic and sentence_of(a.term) not in home and sentence_of(a.term) >= 0:
                notes.append(
                    f"'{a.term}' is only background to the question about {metric_ids[0]!r}; not asked about"
                )
                continue
            keep.append(a)
        ambiguous = keep
    if comparison is None and explicit_baseline is None:
        # "We made our sales number, but came in half short on operating income": the shortfall is
        # against plan when the question sets plan as the yardstick (a plan premise or "what we
        # budgeted") and the rest of the question says short, under, below or behind.
        rest = q
        for c in plan_clauses:
            rest = rest.replace(c, " ")
        missed = any(
            p.expectation == "misses_plan" and metric_ids and p.metric_id == metric_ids[0] for p in premises
        )
        if missed or ((plan_clauses or _PLAN_PHRASE.search(q)) and _SHORTFALL.search(rest)):
            comparison = "budget"
            for c in plan_clauses:
                found = _plan_premise(c)
                if role_word is None and found is not None:
                    role_word = found[1]
            notes.append(
                "the question measures the shortfall against plan (it states the plan as the yardstick)"
            )
    if metric_ids and not any(p.metric_id == metric_ids[0] for p in premises):
        stated = _subject_premise(question, model, metric_ids[0], comparison, premises)
        if stated is not None:
            premises.append(stated)
    if (
        comparison is None
        and explicit_baseline is None
        and metric_ids
        and any(p.metric_id == metric_ids[0] and p.baseline_metric_id for p in premises)
    ):
        comparison = "budget"  # "revenue landed on plan": the subject is measured against its plan
    if premises and period_defaulted:
        notes.append(
            "the question states "
            + ", ".join(f"'{p.text}'" for p in premises)
            + " but names no period; the period is chosen where the premise holds (see the premise check)"
        )

    # 5. intent and direction (metric names are not intent words: "contribution margin" is no "contributed")
    for term in resolved_terms:
        q = re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", " ", q)
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
    comparison_defaulted = comparison is None
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
        paired = _plan_baseline(model, metric_ids[0], role_word, named_plan) if metric_ids else None
        if paired is not None:
            baseline_metric_id = paired
            notes.append(
                f"compared {model.get_metric(metric_ids[0]).display_name} with its governed plan metric "
                f"{model.get_metric(paired).display_name} ({paired!r}, plan_for)"
            )
        elif len(named_plan) == 1:
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
    if not metric_ids and not ambiguous and not unmeasured and (ranking or breakdown or _WHY.search(q)):
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
    if not metric_ids and not ambiguous and not unmeasured:
        notes.append("no metric from the semantic model was recognised in the question")

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
        plan_role_word=role_word,
        comparison_defaulted=comparison_defaulted,
        unmeasured_terms=unmeasured,
        named_plan_metrics=named_plan,
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
    r"the\s+same|consistent|fine|on\s+track)\b"
)
_CLAUSE_SPLIT = re.compile(
    r"[.;!?]|,|\b(?:but|while|whereas|although|though|yet|despite|even\s+though|however|and\s+yet|when|so)\b"
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
            compare_and = "and" in between and bool(set(tokens[: a.span.start]) & {"compare", "comparing"})
            if not (between & _SEGMENT_CONNECTORS or compare_and) or b.span.start - a.span.end > 3:
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
    question: str, model: SemanticModel, resolved: dict[str, str], vocab: Vocabulary, today: dt.date
) -> list[Premise]:
    """Statements the question takes as given: "revenue was roughly flat", "despite flat revenue".

    ``today`` is the reference date periods resolve with; here it only masks time expressions, so
    a premise never depends on the wall clock."""
    out: list[Premise] = []
    q = question.lower().replace("’", "'")
    for clause in _CLAUSE_SPLIT.split(q):
        clause = clause.strip()
        if not clause:
            continue
        plan_premise = _plan_premise(clause)
        if plan_premise is None and (
            _WHY.search(clause) or clause.startswith(("how ", "what ", "which ", "who "))
        ):
            continue
        if plan_premise is not None:
            out.extend(
                p
                for p in _plan_premises(clause, plan_premise, model, resolved, vocab, today)
                if p.term not in {x.term for x in out}
            )
            continue
        if _FLAT.search(clause):
            expectation = "flat"
        elif _DECREASE.search(clause):
            expectation = "decrease"
        elif _INCREASE.search(clause):
            expectation = "increase"
        else:
            continue
        masked = find_time_expressions(" " + clause + " ", today, model)[1]
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


_PLAN_WORD = r"(?:targets?|plans?|budgets?|quotas?|forecasts?|goals?|numbers?)"
_SAID_WE_WOULD = r"what\s+we\s+(?:said|planned|budgeted|promised|committed|expected)"
_MEETS_PLAN = re.compile(
    rf"\b(?:hit|hits|met|meet|meets|made|make|makes|achieved|achieve|achieves|reached|reach|reaches|beat|beats|"
    rf"exceeded|exceed|exceeds|surpassed|delivered|delivers)\b(?:\s+\S+){{0,4}}?\s+{_PLAN_WORD}\b|"
    rf"\b(?:on|ahead\s+of|above|over|in\s+line\s+with)\s+(?:the\s+|our\s+)?{_PLAN_WORD}\b|"
    rf"\b(?:blew|went)\s+(?:through|past|over)\s+(?:the\s+|our\s+)?{_PLAN_WORD}\b|"
    rf"\b{_PLAN_WORD}\s+(?:was|were)\s+(?:met|hit|achieved|reached|beaten|exceeded)\b|"
    rf"\b(?:sold|sell|shipped|booked|delivered|did|made)\b(?:\s+\S+){{0,2}}?\s+{_SAID_WE_WOULD}"
)
_MISSES_PLAN = re.compile(
    rf"\b(?:missed|miss|misses|fell\s+short\s+of|falls\s+short\s+of|short\s+of|below|under|behind)\b"
    rf"(?:\s+\S+){{0,4}}?\s+{_PLAN_WORD}\b"
)
_ROLE_OF_WORD = {
    "target": "target",
    "plan": "plan",
    "budget": "budget",
    "quota": "target",
    "number": "target",
    "forecast": "forecast",
}
_PLAN_PHRASE = re.compile(
    rf"\b(?:plan|planned|budget|budgeted|target|targeted|quota|forecast|forecasted)\b|\b{_SAID_WE_WOULD}\b"
)
_SHORTFALL = re.compile(
    r"\b(?:short|shortfall|under|below|behind|missed|miss|misses|less\s+than|lower\s+than|way\s+off|well\s+off|"
    r"off\s+(?:the\s+)?(?:plan|target|budget|pace)|half\s+of\s+what)\b"
)
_BARE_ROLE_TERMS = {
    "plan",
    "plans",
    "budget",
    "budgets",
    "target",
    "targets",
    "quota",
    "number",
    "numbers",
    "goal",
    "forecast",
    "operating plan",
}


def _is_any_plan(metric: object) -> bool:
    """A plan, budget, target or forecast metric (``plan_for`` or the role in its tags or name)."""
    return bool(getattr(metric, "plan_for", None)) or any(
        _is_plan_metric(metric, r) for r in ("budget", "forecast")
    )


def _tree_ancestor(model: SemanticModel, metric_ids: list[str]) -> str | None:
    """The one metric among ``metric_ids`` that every other one descends from in a metric tree
    ("freight" -> freight cost over base freight; "conversion" -> the rate over its numerator)."""
    parents: dict[str, set[str]] = {}
    for t in model.metric_trees:
        for e in t.nodes:
            if e.approved:
                parents.setdefault(e.child, set()).add(e.parent)

    def ancestors(m: str, seen: frozenset[str] = frozenset()) -> set[str]:
        out: set[str] = set()
        for p in parents.get(m, ()):
            if p not in seen:
                out |= {p, *ancestors(p, seen | {m})}
        return out

    for cand in metric_ids:
        others = [m for m in metric_ids if m != cand]
        if others and all(cand in ancestors(m) for m in others):
            return cand
    return None


def _common_parent(model: SemanticModel, metric_ids: list[str]) -> str | None:
    """The metric every one of ``metric_ids`` is a direct part of, within one metric tree."""
    for t in model.metric_trees:
        parents = {e.parent for e in t.nodes if e.approved and e.child == metric_ids[0]}
        for p in sorted(parents):
            kids = {e.child for e in t.nodes if e.approved and e.parent == p}
            if set(metric_ids) <= kids:
                return p
    return None


def _usable_dimensions(model: SemanticModel, metric_ids: list[str]) -> set[str]:
    from .semantic_graph import dimensions_for_metric

    out: set[str] = set()
    for m in metric_ids:
        out |= {d.name for d in dimensions_for_metric(model, m)[0]}
    return out


def _plan_actual(model: SemanticModel, plan_id: str) -> str | None:
    """The actual a plan metric plans (``plan_for``, or its name without the role word)."""
    pm = model.get_metric(plan_id)
    return pm.plan_for or next(iter(_actuals_named_like(model, pm)), None)


_UP_WORDS = re.compile(
    r"\b(over|above|overrun\w*|blew\s+(?:through|past)|increas\w*|grew|grow\w*|rose|ris\w*|up|higher|jump\w*|"
    r"spik\w*|doubl\w*|tripl\w*|balloon\w*|soar\w*|climb\w*|more)\b"
)
_DOWN_WORDS = re.compile(
    r"\b(under|below|short\w*|miss\w*|declin\w*|drop\w*|fell|fall\w*|decreas\w*|down|lower|shrink\w*|"
    r"shrank|los[st]\w*|worse|compress\w*|slump\w*|dip\w*|fewer|less|anymore|no\s+longer|stopped|leak\w*)\b"
)


def _subject_premise(
    question: str, model: SemanticModel, subject: str, comparison: str | None, premises: list[Premise]
) -> Premise | None:
    """What the question states about its own subject ("why was August revenue *down*", "freight
    blew through budget"): down or up, or, against a plan, missed or met (a cost over plan misses)."""
    q = question.lower().replace("\u2019", "'")
    clauses = [c for c in _CLAUSE_SPLIT.split(q) if c.strip()]
    stated = {
        p.text
        for p in premises
        if p.metric_id != subject
        and (p.baseline_metric_id or _tree_ancestor(model, [subject, p.metric_id or subject]) != subject)
    }
    m = model.get_metric(subject)
    for clause in clauses:
        if clause.strip() in stated:
            continue
        clause = re.sub(r"\b\w+[\s-](?:over|on)[\s-]\w+\b|\bover\s+time\b", " ", clause)
        up, down = _UP_WORDS.search(clause), _DOWN_WORDS.search(clause)
        if not (up or down) or (up and down):
            continue
        if comparison in ("budget", "forecast"):
            adverse = bool(down) if m.higher_is_better else bool(up)
            expectation = "misses_plan" if adverse else "meets_plan"
            plan = _plan_baseline(model, subject, None, [])
        else:
            expectation, plan = ("decrease" if down else "increase"), None
        return Premise(
            text=clause.strip(),
            term=m.display_name.lower(),
            metric_id=subject,
            expectation=expectation,  # type: ignore[arg-type]
            tolerance=0.0 if plan else 0.02,
            baseline_metric_id=plan,
        )
    return None


def _only_in(term: str, text: str, clauses: list[str]) -> bool:
    """True when every occurrence of ``term`` in ``text`` lies inside one of ``clauses``."""
    total = len(re.findall(rf"\b{re.escape(term)}\b", text))
    inside = sum(len(re.findall(rf"\b{re.escape(term)}\b", c)) for c in clauses)
    return total > 0 and inside >= total


def _order_by_premises(metric_ids: list[str], premises: list[Premise]) -> list[str]:
    """Metrics the question states as met, flat or up are context; the metric left to explain
    (or stated as short, missed or down) comes first."""
    asked = {p.metric_id for p in premises if p.expectation in ("misses_plan", "decrease")}
    stated = {p.metric_id for p in premises if p.expectation in ("meets_plan", "flat", "increase")} - asked
    rest = [m for m in metric_ids if m not in stated]
    return [*rest, *(m for m in metric_ids if m in stated)] if rest else list(metric_ids)


def _plan_premise(clause: str) -> tuple[str, str] | None:
    """(expectation, role word) when a clause asserts a plan outcome ("we hit our revenue target")."""
    for expectation, pattern in (("misses_plan", _MISSES_PLAN), ("meets_plan", _MEETS_PLAN)):
        m = pattern.search(clause)
        if m:
            word = re.search(r"(target|plan|budget|quota|forecast|goal|number)", m.group(0).split()[-1])
            return expectation, _ROLE_OF_WORD.get(word.group(1) if word else "plan", "plan")
    return None


def _plan_premises(
    clause: str,
    found: tuple[str, str],
    model: SemanticModel,
    resolved: dict[str, str],
    vocab: Vocabulary,
    today: dt.date,
) -> list[Premise]:
    """Premises of the form "<metric> hit / missed <target>", measured against a plan metric.

    The term may name the plan metric itself ("our revenue target"): the premise then
    compares the actual it plans (``plan_for``, or the same name without the role word) with it.
    Otherwise the plan metric is the actual's governed counterpart for the role word used.
    """
    expectation, role = found
    out: list[Premise] = []
    masked = find_time_expressions(" " + clause + " ", today, model)[1]
    hits = [h for h in vocab.match(tokenize(masked)) if h.kind in ("metric", "glossary", "head")]

    def is_plan_hit(h: TermHit) -> bool:
        cands = [
            c
            for c in ([resolved[h.span.text]] if h.span.text in resolved else h.targets)
            if model.has_metric(c)
        ]
        return bool(cands) and all(_is_any_plan(model.get_metric(c)) for c in cands)

    # One premise per clause: an actual metric wins ("sales hit plan" is about sales), then a plan
    # metric named in full ("our revenue target"); a bare role word ("plan") never makes one.
    phrase = _MISSES_PLAN.search(masked) or _MEETS_PLAN.search(masked)
    at = len(tokenize(masked[: phrase.start()])) if phrase else len(tokenize(masked))
    # the metric the plan phrase is said of: the closest one before it ("the reason *freight* was over plan")
    hits.sort(
        key=lambda h: (
            is_plan_hit(h),
            0 if h.span.end <= at else 1,
            at - h.span.end if h.span.end <= at else h.span.start,
        )
    )
    hits = [h for h in hits if not (is_plan_hit(h) and h.span.text in _BARE_ROLE_TERMS)]
    for hit in hits:
        if out:
            break
        term = hit.span.text
        cands = [c for c in ([resolved[term]] if term in resolved else hit.targets) if model.has_metric(c)]
        if len(cands) > 1:
            cands = [c for c in cands if not _is_any_plan(model.get_metric(c))]
        if len(cands) != 1:
            continue
        metric = model.get_metric(cands[0])
        if metric.plan_for or any(
            metric_matches_role(metric, r) for r in ("plan", "budget", "target", "forecast")
        ):
            actual = metric.plan_for or next(iter(_actuals_named_like(model, metric)), None)
            plan_id: str | None = metric.id
        else:
            actual = metric.id
            plan_id = _plan_baseline(model, metric.id, role, [])
        if actual is None or plan_id is None or any(p.metric_id == actual for p in out):
            continue
        # over / under plan read by what the metric is: a cost over plan misses it, under plan meets it
        hib = model.get_metric(actual).higher_is_better
        over = re.search(r"\b(over|above|exceed\w*|ahead|blew|went\s+past)\b", clause)
        under = re.search(r"\b(under|below)\b", clause)
        if not re.search(r"\b(miss\w*|short|fell)\b", clause):
            if over and not under:
                expectation = "meets_plan" if hib else "misses_plan"
            elif under and not over:
                expectation = "misses_plan" if hib else "meets_plan"
        out.append(
            Premise(
                text=clause,
                term=term,
                metric_id=actual,
                expectation=expectation,  # type: ignore[arg-type]
                tolerance=0.01 if expectation == "meets_plan" else 0.0,
                baseline_metric_id=plan_id,
            )
        )
    return out


def _plan_baseline(
    model: SemanticModel, actual_id: str, role_word: str | None, named: list[str]
) -> str | None:
    """The governed plan metric (``plan_for``) of ``actual_id``: the one the question named, else
    the only one, else the only one matching the role word ("plan", "target", "budget")."""
    cands = model.plan_metrics_for(actual_id)
    named_cands = [c for c in cands if c.id in named]
    if named_cands:
        cands = named_cands
    if len(cands) > 1 and role_word:
        roles = {"target": ("target", "quota"), "plan": ("plan",), "budget": ("budget",)}.get(
            role_word, (role_word,)
        )
        exact = [c for c in cands if any(metric_matches_role(c, r) for r in roles)]
        if exact and len(exact) < len(cands):
            cands = exact
        if len(cands) > 1:
            # "plan" names the operating plan, not a separately tagged budget or target
            others = {"budget", "target", "targets", "quota", "forecast"} - set(roles)
            plain = [c for c in cands if not others & {t.lower() for t in c.tags}]
            cands = plain or cands
    return cands[0].id if len(cands) == 1 else None


def _actuals_named_like(model: SemanticModel, plan_metric: object) -> list[str]:
    """Actual metrics named like a plan metric without its role words (revenue_target -> revenue)."""
    pm = plan_metric
    out: list[str] = []
    for words in ({*tokenize(pm.id)} - _ROLE_WORDS, {*tokenize(pm.name)} - _ROLE_WORDS):  # type: ignore[attr-defined]
        if not words:
            continue
        for m in sorted(model.metrics, key=lambda m: m.id):
            if m.id == pm.id or m.plan_for or not m.canonical:  # type: ignore[attr-defined]
                continue
            if any(metric_matches_role(m, r) for r in ("plan", "budget", "target", "forecast")):
                continue
            if words in ({*tokenize(m.id)}, {*tokenize(m.name)}) and m.id not in out:
                out.append(m.id)
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
_ROLE_WORDS = {
    "budget",
    "budgeted",
    "plan",
    "planned",
    "target",
    "targets",
    "quota",
    "forecast",
    "forecasted",
    "projected",
}


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
    elif len(metric_ids) > 1:
        metric_ids = _order_by_premises(metric_ids, premises)
    baseline_metric_id = interp.baseline_metric_id
    if interp.comparison_kind in ("budget", "forecast") and metric_ids:
        # the subject is known only now: compare it with its own plan counterpart
        paired = _plan_baseline(model, metric_ids[0], interp.plan_role_word, interp.named_plan_metrics)
        if paired is not None:
            baseline_metric_id = paired
    out = interp.model_copy(
        update={
            "metric_ids": metric_ids,
            "ambiguous": remaining,
            "resolved_terms": resolved,
            "confidence_notes": notes,
            "template_id": interp.template_id if interp.template_id == "inventory_health" else None,
            "premises": premises,
            "baseline_metric_id": baseline_metric_id,
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
