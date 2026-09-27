"""Evidence strength and statement typing rules (spec §28, §65).

AnalystOS does not produce numerical confidence scores. Every node gets one of four
categories plus the list of reasons that put it there. The rules below are the complete
definition; the UI shows the reasons verbatim.

Statement types
---------------
* ``observation``: a directly measured fact ("Revenue declined 11.8%"). Root nodes,
  dimension group headers and segment moves for metrics whose segments do not partition
  the total (so no attribution can be claimed).
* ``supported_explanation``: an attribution backed by an exact arithmetic identity
  (driver decomposition whose identity holds) or by an additive partition verified on the
  data ("Dallas accounted for 54% of the decline").
* ``hypothesis``: anything not backed by an executed, validated test: untestable ideas
  (marketing, competitor activity) and tests that failed. Always phrased conditionally.

Evidence strength rules (evaluated top to bottom, first match wins)
-------------------------------------------------------------------
H1  Not backed by an executed test, or the test failed          -> hypothesis_only
W1  Validation of the underlying query reported a failure        -> weak
W2  Attribution is not exact: a non-additive metric, or an identity residual above
    ``RESIDUAL_TOLERANCE`` of the parent change                  -> weak
W3  Effect is small: |share of parent change| < ``MODERATE_SHARE``                -> weak
W4  Unstable base: the segment's baseline is below ``SMALL_BASE_SHARE`` of the parent
    baseline and its pct change is extreme                       -> weak
S1  |share| >= ``STRONG_SHARE`` and the direct calculation is corroborated by a second
    independent test (``corroborations`` >= 1)                    -> strong
M1  Everything else that passed validation with an exact method  -> moderate

Observation nodes that are pure measurements (root, dimension headers) are ``strong``
when their query validated ("direct calculation, validated") and ``weak`` otherwise.

"Corroborated" means that a separate executed test points the same way, for example the
same segment is a same-sign top contributor to both the parent metric and one of its
drivers, or a driver identity reproduces the parent value within tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import EvidenceStrength, StatementType

STRONG_SHARE = 0.25
MODERATE_SHARE = 0.10
RESIDUAL_TOLERANCE = 0.02
SMALL_BASE_SHARE = 0.01
EXTREME_PCT = 1.0


@dataclass
class EvidenceInput:
    executed: bool
    validation_ok: bool = True
    validation_failures: list[str] = field(default_factory=list)
    exact_method: bool = True
    method_label: str = ""
    share: float | None = None
    residual_share: float | None = None
    corroborations: list[str] = field(default_factory=list)
    base_share: float | None = None
    pct_change: float | None = None
    measurement_only: bool = False
    failure: str | None = None
    max_strength: EvidenceStrength | None = None
    """Cap for evidence that is inherently limited (e.g. a single prior-year comparison)."""
    cap_reason: str = ""


_ORDER: dict[str, int] = {"hypothesis_only": 0, "weak": 1, "moderate": 2, "strong": 3}


def assess(ev: EvidenceInput) -> tuple[EvidenceStrength, list[str]]:
    """Apply the rules in the module docstring, then any ``max_strength`` cap."""
    strength, reasons = _assess(ev)
    if ev.max_strength is not None and _ORDER[strength] > _ORDER[ev.max_strength]:
        strength = ev.max_strength
        reasons = [*reasons, ev.cap_reason or f"capped at {ev.max_strength}: the test is inherently limited"]
    return strength, reasons


def _assess(ev: EvidenceInput) -> tuple[EvidenceStrength, list[str]]:
    reasons: list[str] = []

    # H1
    if not ev.executed or ev.failure:
        if ev.failure:
            reasons.append(f"test failed: {ev.failure}; no conclusion drawn")
        else:
            reasons.append("not tested: no executed query supports this statement")
        return "hypothesis_only", reasons

    if ev.measurement_only:
        if ev.validation_ok:
            return "strong", ["direct calculation from an executed query", "query validation passed"]
        return "weak", ["direct calculation, but validation reported: " + "; ".join(ev.validation_failures)]

    # W1
    if not ev.validation_ok:
        reasons.append("validation reported: " + "; ".join(ev.validation_failures))
        return "weak", reasons

    # W2
    if not ev.exact_method:
        reasons.append(
            "attribution is not exact"
            + (f" ({ev.method_label})" if ev.method_label else "")
            + "; the segment move is measured but its share of the total cannot be claimed"
        )
        return "weak", reasons
    if ev.residual_share is not None and abs(ev.residual_share) > RESIDUAL_TOLERANCE:
        reasons.append(
            f"driver identity leaves an unexplained residual of {abs(ev.residual_share) * 100:.1f}% "
            "of the parent change"
        )
        return "weak", reasons

    share = abs(ev.share) if ev.share is not None else 0.0
    # W3
    if share < MODERATE_SHARE:
        reasons.append(f"small effect: explains {share * 100:.0f}% of the parent change")
        return "weak", reasons

    # W4
    if (
        ev.base_share is not None
        and ev.base_share < SMALL_BASE_SHARE
        and ev.pct_change is not None
        and abs(ev.pct_change) >= EXTREME_PCT
    ):
        reasons.append("small base: the segment was under 1% of the baseline, so its % change is unstable")
        return "weak", reasons

    method = ev.method_label or "direct calculation"
    reasons.append(f"{method}; query validation passed")
    if share >= STRONG_SHARE:
        reasons.append(f"large effect: explains {share * 100:.0f}% of the parent change")
        # S1
        if ev.corroborations:
            reasons.extend(f"corroborated: {c}" for c in ev.corroborations)
            return "strong", reasons
        reasons.append("not yet corroborated by a second independent test")
        return "moderate", reasons
    # M1
    reasons.append(f"moderate effect: explains {share * 100:.0f}% of the parent change")
    if ev.corroborations:
        reasons.extend(f"corroborated: {c}" for c in ev.corroborations)
    return "moderate", reasons


def statement_type_for(*, executed: bool, attribution: bool, exact: bool) -> StatementType:
    """Rule for statement typing (see module docstring)."""
    if not executed:
        return "hypothesis"
    if attribution and exact:
        return "supported_explanation"
    return "observation"


def phrase_hypothesis(text: str) -> str:
    """Render an untested idea so it can never read as fact."""
    body = text.strip().rstrip(".")
    lowered = body.lower()
    if lowered.startswith("hypothesis"):
        return body + "."
    return f"Hypothesis (not tested): {body[0].lower() + body[1:] if body else body}."
