from __future__ import annotations

import datetime as dt

import pytest
from analystos_engine.semantic.models import SemanticModel
from analystos_investigator import apply_choices, interpret
from analystos_investigator.llm import ScriptedProvider
from inv_fixture import TODAY


def _i(q: str, model: SemanticModel, vi: dict[str, list[str]] | None = None, **kw: object):  # type: ignore[no-untyped-def]
    return interpret(q, model, TODAY, value_index=vi, **kw)  # type: ignore[arg-type]


def test_revenue_question_resolves_metric_period_and_previous_period(model: SemanticModel) -> None:
    i = _i("Why did revenue decline in August?", model)
    assert i.metric_ids == ["revenue"]
    assert i.window and (i.window.start, i.window.end) == (dt.date(2026, 8, 1), dt.date(2026, 9, 1))
    assert i.baseline and i.baseline.start == dt.date(2026, 7, 1)
    assert i.comparison_kind == "pop"
    assert i.intent == "why_change"
    assert i.direction == "decrease"
    assert i.template_id == "revenue_decline"


def test_year_over_year_comparison(model: SemanticModel) -> None:
    i = _i("Why was August revenue down vs last year?", model)
    assert i.comparison_kind == "yoy"
    assert i.baseline and i.baseline.start == dt.date(2025, 8, 1)
    assert i.window and i.window.start == dt.date(2026, 8, 1)


def test_explicit_baseline_period(model: SemanticModel) -> None:
    i = _i("Compare revenue for August vs June", model)
    assert i.window and i.window.start == dt.date(2026, 8, 1)
    assert i.baseline and i.baseline.start == dt.date(2026, 6, 1)
    assert i.intent == "compare"


def test_synonym_and_glossary_terms(model: SemanticModel) -> None:
    assert _i("How did sales do last month?", model).metric_ids == ["revenue"]
    top = _i("Why did the top line drop in Q3?", model)
    assert top.metric_ids == ["revenue"]
    assert any("glossary" in n for n in top.confidence_notes)
    assert top.window and top.window.kind == "quarter"


def test_ambiguous_margin_is_surfaced_not_guessed(model: SemanticModel) -> None:
    i = _i("Why did margin fall in August?", model)
    assert i.metric_ids == []
    assert len(i.ambiguous) == 1
    amb = i.ambiguous[0]
    assert amb.term == "margin"
    assert {c.metric_id for c in amb.candidates} == {"gross_margin", "contribution_margin"}
    assert amb.llm_suggestion is None


def test_exact_metric_name_is_not_ambiguous(model: SemanticModel) -> None:
    i = _i("Why did gross margin fall in August?", model)
    assert i.metric_ids == ["gross_margin"] and not i.ambiguous
    assert i.template_id == "margin_variance"


def test_choices_resolve_ambiguity(model: SemanticModel) -> None:
    i = _i("Why did margin fall in August?", model, choices={"margin": "contribution_margin"})
    assert i.metric_ids == ["contribution_margin"] and not i.ambiguous
    j = apply_choices(_i("Why did margin fall?", model), model, {"margin": "gross_margin"})
    assert j.metric_ids == ["gross_margin"] and not j.ambiguous


def test_choice_outside_candidates_is_ignored(model: SemanticModel) -> None:
    i = _i("Why did margin fall?", model, choices={"margin": "revenue"})
    assert i.ambiguous and not i.metric_ids
    assert any("not one of its candidates" in n for n in i.confidence_notes)


def test_llm_only_suggests_and_never_applies(model: SemanticModel) -> None:
    llm = ScriptedProvider({"interpret_question": {"metric_id": "gross_margin", "rationale": "most common"}})
    i = _i("Why did margin fall?", model, llm=llm)
    assert i.ambiguous[0].llm_suggestion == "gross_margin"
    assert i.metric_ids == []  # still ambiguous; the analyst decides


def test_llm_suggestion_outside_candidates_is_dropped(model: SemanticModel) -> None:
    llm = ScriptedProvider({"interpret_question": {"metric_id": "operating_margin", "rationale": "invented"}})
    i = _i("Why did margin fall?", model, llm=llm)
    assert i.ambiguous[0].llm_suggestion is None


def test_filters_inclusion_and_exclusion(model: SemanticModel, value_index: dict[str, list[str]]) -> None:
    i = _i("Why did sales fall in Dallas excluding new stores?", model, value_index)
    described = sorted(f.describe() for f in i.filters)
    assert described == ["branch_name = Dallas", "branch_type excludes New"]
    ops = {f.dimension: f.op for f in i.filters}
    assert ops == {"branch_name": "eq", "branch_type": "neq"}


def test_unresolvable_exclusion_is_reported_not_dropped(
    model: SemanticModel, value_index: dict[str, list[str]]
) -> None:
    i = _i("Why did revenue fall last month excluding pet rocks?", model, value_index)
    assert not i.filters
    assert i.unresolved_filters and "pet rocks" in i.unresolved_filters[0].text
    assert "NOT applied" in i.unresolved_filters[0].reason


def test_common_word_values_need_context(model: SemanticModel, value_index: dict[str, list[str]]) -> None:
    # "new" alone must not become a filter on branch_type
    i = _i("What is new with revenue in August?", model, value_index)
    assert not i.filters


def test_breakdown_dimension(model: SemanticModel) -> None:
    i = _i("Compare revenue by region for Q2", model)
    assert i.breakdown_dimensions == ["region"]
    assert i.intent == "compare"


def test_budget_comparison_resolves_baseline_metric(model: SemanticModel) -> None:
    i = _i("Revenue vs budget in August", model)
    assert i.comparison_kind == "budget"
    assert i.baseline_metric_id == "budget_revenue"
    assert i.metric_ids == ["revenue"]
    assert i.baseline == i.window


def test_single_day_anomaly(model: SemanticModel) -> None:
    i = _i("Revenue unexpectedly fell on Aug 12", model)
    assert i.intent == "anomaly"
    assert i.window and i.window.start == dt.date(2026, 8, 12) and i.window.kind == "day"
    assert i.baseline and i.baseline.start == dt.date(2026, 8, 5)


def test_month_day_without_year_uses_most_recent_past_date(model: SemanticModel) -> None:
    i = _i("Why did revenue drop on Dec 3?", model)
    assert i.window and i.window.start == dt.date(2025, 12, 3)


def test_defaults_are_noted(model: SemanticModel) -> None:
    i = _i("Why did revenue drop?", model)
    assert i.window and i.window.start == dt.date(2026, 8, 1)
    assert any("defaulted" in n for n in i.confidence_notes)
    assert any("previous period" in n for n in i.confidence_notes)


def test_may_as_verb_is_not_a_month(model: SemanticModel) -> None:
    i = _i("Revenue may have dropped last quarter", model)
    assert i.window and i.window.kind == "quarter"


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Why did orders fall in August?", "why_change"),
        ("Which customers drove the orders decline?", "why_change"),
        ("Revenue trend over time", "trend"),
        ("Forecast revenue for next quarter", "forecast"),
        ("What was revenue in July?", "lookup"),
        ("Break down revenue by channel", "breakdown"),
    ],
)
def test_intents(model: SemanticModel, question: str, intent: str) -> None:
    assert _i(question, model).intent == intent


def test_no_metric_recognised(model: SemanticModel) -> None:
    i = _i("Why is the sky blue?", model)
    assert not i.metric_ids and not i.ambiguous
    assert any("no metric" in n for n in i.confidence_notes)


def test_interpretation_is_deterministic(model: SemanticModel, value_index: dict[str, list[str]]) -> None:
    q = "Why did sales fall in Dallas excluding new stores vs last year?"
    assert _i(q, model, value_index).model_dump() == _i(q, model, value_index).model_dump()
