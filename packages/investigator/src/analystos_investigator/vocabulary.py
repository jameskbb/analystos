"""Term index over the semantic model: metric names, synonyms, glossary terms, dimensions
and (optionally) dimension values.

Matching is purely lexical and deterministic: the question is tokenised, tokens are
singularised, and phrases are matched longest-first without overlap.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from analystos_engine.semantic.models import Metric, SemanticModel

_TOKEN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*|%|\$")

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "for",
        "to",
        "by",
        "at",
        "and",
        "or",
        "vs",
        "versus",
        "with",
        "without",
        "is",
        "was",
        "were",
        "did",
        "do",
        "does",
        "why",
        "what",
        "how",
        "which",
        "who",
        "when",
        "where",
        "our",
        "my",
        "we",
        "us",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "than",
        "from",
        "into",
        "per",
        "across",
        "show",
        "me",
        "tell",
        "give",
        "explain",
        "drove",
        "caused",
        "cause",
        "driving",
        "down",
        "up",
    ]
)


def singular(tok: str) -> str:
    if len(tok) > 3 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is", "sales")):
        return tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    return [singular(t) for t in _TOKEN.findall(text.lower().replace("_", " ").replace("-", " "))]


def phrase_key(text: str) -> tuple[str, ...]:
    return tuple(tokenize(text))


@dataclass
class Span:
    start: int
    end: int  # exclusive token index
    text: str


@dataclass
class TermHit:
    span: Span
    kind: Literal["metric", "glossary", "dimension", "value", "head"]
    targets: list[str]
    source: str
    dimension_values: list[tuple[str, str]] = field(default_factory=list)


class Vocabulary:
    """Phrase -> target maps built from a semantic model and an optional value index."""

    def __init__(self, model: SemanticModel, value_index: dict[str, list[str]] | None = None) -> None:
        self.model = model
        self.metric_phrases: dict[tuple[str, ...], set[str]] = {}
        self.glossary_phrases: dict[tuple[str, ...], list[str]] = {}
        self.glossary_terms: dict[tuple[str, ...], str] = {}
        self.dimension_phrases: dict[tuple[str, ...], set[str]] = {}
        self.value_phrases: dict[tuple[str, ...], list[tuple[str, str]]] = {}
        self.head_nouns: dict[str, set[str]] = {}

        for m in model.metrics:
            for name in _metric_names(m):
                key = phrase_key(name)
                if key:
                    self.metric_phrases.setdefault(key, set()).add(m.id)
                    self.head_nouns.setdefault(key[-1], set()).add(m.id)
        for g in model.glossary:
            # A glossary term refers to metrics only when it says so (metric_id or explicit
            # candidates). Terms such as "Region" or "Product Tier" define a dimension or a
            # concept; their ``related`` list is context, never a metric reference, so they
            # must not shadow the dimension of the same name.
            targets: list[str] = []
            if g.metric_id and model.has_metric(g.metric_id):
                targets = [g.metric_id]
            else:
                targets = [c for c in g.candidate_metric_ids if model.has_metric(c)]
            if not targets:
                continue
            for name in [g.term, *g.synonyms]:
                key = phrase_key(name)
                if key:
                    self.glossary_phrases[key] = targets
                    self.glossary_terms[key] = g.term
        for d in model.dimensions:
            for name in [d.name, d.label or "", *d.synonyms]:
                key = phrase_key(name)
                if key:
                    self.dimension_phrases.setdefault(key, set()).add(d.name)
            # "branch_name" is also referred to as "branch"
            key = phrase_key(d.name)
            if len(key) == 2 and key[1] in ("name", "id", "code", "type"):
                self.dimension_phrases.setdefault(key[:1], set()).add(d.name)
        for dim, values in (value_index or {}).items():
            for v in values:
                key = phrase_key(v)
                if key and not all(t in STOPWORDS for t in key) and not all(t.isdigit() for t in key):
                    self.value_phrases.setdefault(key, []).append((dim, v))

    def match(self, tokens: list[str], blocked: set[int] | None = None) -> list[TermHit]:
        """Longest-first, non-overlapping matches of every phrase kind."""
        blocked = set(blocked or ())
        hits: list[TermHit] = []
        max_len = max(
            [
                len(k)
                for k in (
                    *self.metric_phrases,
                    *self.glossary_phrases,
                    *self.dimension_phrases,
                    *self.value_phrases,
                )
            ]
            or [1]
        )
        i = 0
        while i < len(tokens):
            if i in blocked:
                i += 1
                continue
            found: TermHit | None = None
            for n in range(min(max_len, len(tokens) - i), 0, -1):
                if any(j in blocked for j in range(i, i + n)):
                    continue
                key = tuple(tokens[i : i + n])
                span = Span(i, i + n, " ".join(key))
                if key in self.metric_phrases:
                    found = TermHit(
                        span, "metric", sorted(self.metric_phrases[key]), "metric name or synonym"
                    )
                elif key in self.dimension_phrases:
                    # A dimension's own name, label or synonym wins over a glossary phrase.
                    found = TermHit(span, "dimension", sorted(self.dimension_phrases[key]), "dimension name")
                elif key in self.glossary_phrases:
                    found = TermHit(
                        span,
                        "glossary",
                        list(self.glossary_phrases[key]),
                        f"glossary term '{self.glossary_terms[key]}'",
                    )
                elif key in self.value_phrases:
                    found = TermHit(
                        span, "value", [], "dimension value", dimension_values=list(self.value_phrases[key])
                    )
                if found:
                    break
            if found is None and tokens[i] in self.head_nouns and tokens[i] not in STOPWORDS:
                found = TermHit(
                    Span(i, i + 1, tokens[i]),
                    "head",
                    sorted(self.head_nouns[tokens[i]]),
                    f"metrics named '... {tokens[i]}'",
                )
            if found:
                hits.append(found)
                i = found.span.end
            else:
                i += 1
        return hits


def _metric_names(m: Metric) -> Iterable[str]:
    seen: set[str] = set()
    for n in (m.id, m.name, m.label or "", *m.synonyms):
        if n and n.lower() not in seen:
            seen.add(n.lower())
            yield n
