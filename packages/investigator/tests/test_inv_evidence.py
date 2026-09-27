from __future__ import annotations

from analystos_investigator.evidence import EvidenceInput, assess, phrase_hypothesis, statement_type_for


def test_untested_is_hypothesis_only() -> None:
    strength, reasons = assess(EvidenceInput(executed=False))
    assert strength == "hypothesis_only"
    assert "not tested" in reasons[0]


def test_failed_test_is_hypothesis_only_and_says_so() -> None:
    strength, reasons = assess(EvidenceInput(executed=True, failure="query failed: no such column"))
    assert strength == "hypothesis_only"
    assert "test failed" in reasons[0] and "no conclusion" in reasons[0]


def test_measurement_nodes() -> None:
    assert assess(EvidenceInput(executed=True, measurement_only=True))[0] == "strong"
    weak, reasons = assess(
        EvidenceInput(
            executed=True, measurement_only=True, validation_ok=False, validation_failures=["non_empty"]
        )
    )
    assert weak == "weak" and "non_empty" in reasons[0]


def test_validation_failure_is_weak() -> None:
    s, r = assess(EvidenceInput(executed=True, validation_ok=False, validation_failures=["grain"], share=0.9))
    assert s == "weak" and "grain" in r[0]


def test_non_exact_method_is_weak() -> None:
    s, r = assess(
        EvidenceInput(executed=True, exact_method=False, method_label="non-additive metric", share=None)
    )
    assert s == "weak" and "not exact" in r[0]


def test_identity_residual_above_tolerance_is_weak() -> None:
    s, r = assess(EvidenceInput(executed=True, share=0.6, residual_share=0.1))
    assert s == "weak" and "residual" in r[0]


def test_small_effect_is_weak() -> None:
    s, r = assess(EvidenceInput(executed=True, share=0.05))
    assert s == "weak" and "5%" in r[0]


def test_small_base_extreme_change_is_weak() -> None:
    s, r = assess(EvidenceInput(executed=True, share=0.3, base_share=0.001, pct_change=4.0))
    assert s == "weak" and "small base" in r[0]


def test_large_uncorroborated_effect_is_moderate() -> None:
    s, r = assess(EvidenceInput(executed=True, share=0.6))
    assert s == "moderate"
    assert any("not yet corroborated" in x for x in r)


def test_large_corroborated_effect_is_strong() -> None:
    s, r = assess(EvidenceInput(executed=True, share=-0.4, corroborations=["same segment in Orders"]))
    assert s == "strong"
    assert any("large effect" in x for x in r) and any("corroborated" in x for x in r)


def test_moderate_effect_is_moderate_even_if_corroborated() -> None:
    s, _ = assess(EvidenceInput(executed=True, share=0.15, corroborations=["x"]))
    assert s == "moderate"


def test_statement_types() -> None:
    assert statement_type_for(executed=False, attribution=True, exact=True) == "hypothesis"
    assert statement_type_for(executed=True, attribution=True, exact=True) == "supported_explanation"
    assert statement_type_for(executed=True, attribution=True, exact=False) == "observation"
    assert statement_type_for(executed=True, attribution=False, exact=True) == "observation"


def test_hypothesis_phrasing_never_reads_as_fact() -> None:
    assert (
        phrase_hypothesis("Marketing activity changed demand")
        == "Hypothesis (not tested): marketing activity changed demand."
    )
    assert phrase_hypothesis("Hypothesis: x.") == "Hypothesis: x."
