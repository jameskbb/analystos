"""Numeric-claim verifier: every number in model-written prose must come from an artifact.

``EvidenceNumbers`` collects numbers from executed artifacts and tree nodes (raw values,
changes, percentages and shares). ``verify_text`` extracts numeric claims from narrative
text and rejects any claim that cannot be reproduced from the collected numbers at the
precision it was written with. A narrative with a single unverifiable number is rejected
as a whole; the deterministic template text is used instead.

Claims about a metric's movement are also *bound to context*: in "revenue is up 50%" the
number follows a metric name and a direction word, so it must equal a measured change of
that metric with the same sign. Matching some unrelated 50% elsewhere is not enough.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel

from ..models import Artifact, TreeNode

_NUM = re.compile(
    r"(?P<sign>[-+−])?\s?(?P<cur>\$)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<suffix>%|pp\b|percentage points?\b|percent\b|[kKmMbB]\b|thousand\b|million\b|billion\b|bn\b)?"
)


@dataclass
class NumericClaim:
    text: str
    value: float
    decimals: int
    kind: str  # "percent" | "currency" | "number"


@dataclass
class VerificationResult:
    ok: bool
    claims: list[NumericClaim] = field(default_factory=list)
    unverified: list[NumericClaim] = field(default_factory=list)

    @property
    def rejected_texts(self) -> list[str]:
        return [c.text for c in self.unverified]


def extract_claims(text: str) -> list[NumericClaim]:
    claims: list[NumericClaim] = []
    for m in _NUM.finditer(text):
        raw = m.group("num")
        start = m.start("num")
        # skip digits glued to letters (ids such as "Q3", "SKU123", "n_1f2e")
        if start > 0 and (text[start - 1].isalpha() or text[start - 1] == "_"):
            continue
        end = m.end("num")
        if end < len(text) and (text[end].isalpha() and not m.group("suffix")) and text[end] not in "kKmMbB":
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        value = float(raw.replace(",", ""))
        suffix = (m.group("suffix") or "").lower()
        mult = 1.0
        kind = "number"
        if suffix in ("%", "percent", "pp", "percentage point", "percentage points"):
            kind = "percent"
        elif suffix in ("k", "thousand"):
            mult = 1e3
        elif suffix in ("m", "million"):
            mult = 1e6
        elif suffix in ("b", "bn", "billion"):
            mult = 1e9
        if m.group("cur"):
            kind = "currency"
        sign = m.group("sign")
        if sign in ("-", "−"):
            value = -value
        claims.append(NumericClaim(m.group(0).strip(), value * mult, decimals, kind))
    return claims


class EvidenceNumbers:
    """The set of numbers a narrative may cite."""

    def __init__(self) -> None:
        self.values: list[float] = []
        self.percents: list[float] = []
        self.literals: set[float] = set()
        # metric name (lowercase) -> measured signed pct changes (as fractions)
        self.metric_changes: dict[str, list[float]] = {}

    def add_metric_change(self, names: Iterable[str | None], pct_change: float | None) -> None:
        if pct_change is None:
            return
        for name in names:
            if name:
                self.metric_changes.setdefault(name.lower().replace("_", " "), []).append(float(pct_change))

    def add_value(self, v: float | None) -> None:
        if v is None or isinstance(v, bool):
            return
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        if math.isnan(f) or math.isinf(f):
            return
        self.values.append(f)

    def add_fraction(self, v: float | None) -> None:
        """A ratio stored as a fraction (0.118) that prose will render as a percent (11.8%)."""
        if v is None:
            return
        self.percents.append(float(v) * 100)
        self.add_value(v)

    def add_literal(self, v: float) -> None:
        """Exact numbers allowed verbatim (years, day numbers, counts of items shown)."""
        self.literals.add(float(v))

    @classmethod
    def from_run(
        cls,
        artifacts: Iterable[Artifact] = (),
        nodes: Iterable[TreeNode] = (),
        *,
        dates: Iterable[date] = (),
    ) -> EvidenceNumbers:
        ev = cls()
        all_dates = list(dates)
        for a in artifacts:
            if a.window is not None:
                all_dates.extend([a.window.start, a.window.last_day])
            if a.result is not None:
                for row in a.result.rows:
                    for cell in row:
                        if isinstance(cell, int | float) and not isinstance(cell, bool):
                            ev.add_value(cell)
            _walk_numbers(a.data, ev)
        for n in nodes:
            for v in (n.current, n.baseline, n.abs_change):
                ev.add_value(v)
            if n.metric_format == "percent":
                for v in (n.current, n.baseline, n.abs_change):
                    ev.add_fraction(v)
            ev.add_fraction(n.pct_change)
            if n.kind in ("root", "driver", "segment", "check") and n.metric_id:
                ev.add_metric_change([n.metric_id, n.metric_label], n.pct_change)
            if n.contribution_to_parent is not None:
                c = n.contribution_to_parent
                ev.add_value(c.effect)
                ev.add_fraction(c.share)
                for v in (c.mix_effect, c.rate_effect):
                    ev.add_value(v)
        for d in all_dates:
            ev.add_literal(d.year)
            ev.add_literal(d.day)
            ev.add_literal(d.month)
        return ev

    def matches(self, claim: NumericClaim) -> bool:
        if claim.value in self.literals or -claim.value in self.literals:
            return True
        pool = self.percents if claim.kind == "percent" else self.values
        tol = 0.5 * _scale_unit(claim.text) if claim.kind != "percent" else 0.5 * 10 ** (-claim.decimals)
        target = abs(claim.value)
        return any(abs(abs(v) - target) <= tol + 1e-9 for v in pool)


def _scale_unit(text: str) -> float:
    t = text.lower()
    decimals = 0
    m = re.search(r"\.(\d+)", t)
    if m:
        decimals = len(m.group(1))
    if t.endswith("k") or "thousand" in t:
        return 1e3 * 10 ** (-decimals)
    if t.endswith("m") or "million" in t:
        return 1e6 * 10 ** (-decimals)
    if t.endswith("b") or "billion" in t or t.endswith("bn"):
        return 1e9 * 10 ** (-decimals)
    return 10 ** (-decimals)


_FRACTION_KEYS = ("pct", "share", "rate", "ratio", "percent", "margin_pct", "fraction")


def _walk_numbers(obj: object, ev: EvidenceNumbers, depth: int = 0, key: str = "") -> None:
    """Collect numbers from artifact data. Values under keys that denote fractions
    (``pct_change``, ``share``...) may also be cited as percentages."""
    if depth > 8 or isinstance(obj, bool):
        return
    if isinstance(obj, int | float):
        ev.add_value(float(obj))
        if any(k in key.lower() for k in _FRACTION_KEYS):
            ev.add_fraction(float(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _walk_numbers(v, ev, depth + 1, str(k))
    elif isinstance(obj, list | tuple):
        for v in obj:
            _walk_numbers(v, ev, depth + 1, key)
    elif isinstance(obj, BaseModel):
        _walk_numbers(obj.model_dump(), ev, depth + 1, key)


_UP = (
    "up",
    "rose",
    "risen",
    "increased",
    "increase",
    "grew",
    "grown",
    "gained",
    "higher",
    "jumped",
    "climbed",
)
_DOWN = (
    "down",
    "fell",
    "fallen",
    "decreased",
    "decrease",
    "declined",
    "decline",
    "dropped",
    "lower",
    "lost",
    "shrank",
    "slid",
)


def _bound_claims(text: str, evidence: EvidenceNumbers) -> list[NumericClaim]:
    """Percent claims that follow '<metric> ... <direction>' and do not match that metric."""
    bad: list[NumericClaim] = []
    lowered = text.lower()
    directions = "|".join(_UP + _DOWN)
    for name, changes in evidence.metric_changes.items():
        pattern = (
            rf"\b{re.escape(name)}\b(?:\W+\w+){{0,4}}?\W+(?P<dir>{directions})\b(?:\W+by)?\W+"
            r"(?P<num>[-+\u2212]?\d+(?:\.\d+)?)\s?(?:%|percent)"
        )
        for m in re.finditer(pattern, lowered):
            value = float(m.group("num").replace("\u2212", "-"))
            decimals = len(m.group("num").split(".")[1]) if "." in m.group("num") else 0
            sign = 1.0 if m.group("dir") in _UP else -1.0
            tol = 0.5 * 10 ** (-decimals) + 1e-9
            ok = any(c * sign > 0 and abs(abs(c) * 100 - abs(value)) <= tol for c in changes)
            if not ok:
                bad.append(NumericClaim(text[m.start() : m.end()], sign * abs(value), decimals, "percent"))
    return bad


def verify_text(text: str, evidence: EvidenceNumbers) -> VerificationResult:
    claims = extract_claims(text)
    unverified = [c for c in claims if not evidence.matches(c)]
    unverified.extend(_bound_claims(text, evidence))
    return VerificationResult(ok=not unverified, claims=claims, unverified=unverified)


_TOPIC_STOP = {
    "that",
    "this",
    "with",
    "from",
    "were",
    "have",
    "been",
    "more",
    "than",
    "some",
    "there",
    "their",
    "which",
    "changed",
    "change",
}
_HEDGES = (
    "may ",
    "might ",
    "could ",
    "possibl",
    "hypothes",
    "not tested",
    "untested",
    "unconfirmed",
    "unresolved",
    "open question",
    "perhaps",
    "unclear",
    "not established",
)


def hypothesis_leaks(text: str, hypotheses: Iterable[str]) -> list[str]:
    """Hypotheses whose topic appears in ``text`` without any hedging nearby.

    A narrative may mention an untested hypothesis only as a possibility ("may be related to
    marketing"); stating it plainly ("marketing activity changed demand") is rejected.
    """
    lowered = text.lower()
    leaks: list[str] = []
    for h in hypotheses:
        body = h.lower()
        for prefix in ("hypothesis (not tested):", "hypothesis:"):
            body = body.replace(prefix, "")
        body = body.split(". no data")[0].strip(" .")
        words = [w for w in re.findall(r"[a-z]+", body) if len(w) > 3 and w not in _TOPIC_STOP][:1]
        if not words:
            continue
        m = re.search(rf"\b{re.escape(words[0])}", lowered)
        if m is None:
            continue
        start = m.start()
        window = lowered[max(0, start - 100) : start + 120]
        if not any(hd in window for hd in _HEDGES):
            leaks.append(h)
    return leaks
