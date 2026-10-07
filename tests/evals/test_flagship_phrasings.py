"""How an executive actually asks: every phrasing of the flagship question must read as the same
investigation (subject, comparison against the subject's own plan, premise, template), through the
generic interpreter and the governed semantic model. Nothing in the engine matches these strings."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from .conftest import TODAY

FLAGSHIP = "We made our sales number last quarter, but we missed our target for net profit. What happened?"
HALF_SHORT = "We made our sales number last quarter, but we came in almost half short on operating income. What happened?"

# (question, subject, premise metric -> expectation or None, ambiguous term or None)
CASES = [
    (FLAGSHIP, "operating_profit", "meets_plan", None),
    (HALF_SHORT, "operating_profit", "meets_plan", None),
    (
        "We made our sales number last quarter but operating profit came in way under plan. What happened?",
        "operating_profit",
        "meets_plan",
        None,
    ),
    (
        "Sales came in on budget last quarter, but we're almost half short on profit. Where did the money go?",
        None,
        "meets_plan",
        "profit",
    ),
    (
        "Top line was on plan last quarter but the bottom line was way off. What happened?",
        "operating_profit",
        "meets_plan",
        None,
    ),
    (
        "Sales hit plan last quarter. Why is operating profit nearly half of what we budgeted?",
        "operating_profit",
        "meets_plan",
        None,
    ),
    (
        "We sold what we said we'd sell last quarter, so why are we $1.9 million short on operating income?",
        "operating_profit",
        None,
        None,
    ),
    (
        "Revenue was on budget last quarter but operating income came in well below budget. Why?",
        "operating_profit",
        "meets_plan",
        None,
    ),
    (
        "We hit our revenue target last quarter, but operating profit missed plan badly. What happened?",
        "operating_profit",
        "meets_plan",
        None,
    ),
]


@pytest.mark.parametrize(
    ("question", "subject", "premise", "ambiguous"), CASES, ids=[c[0][:48] for c in CASES]
)
def test_executive_phrasings(
    model: Any, question: str, subject: str | None, premise: str | None, ambiguous: str | None
) -> None:
    from analystos_investigator import interpret
    from analystos_investigator.interpret import apply_choices

    today = dt.date.fromisoformat(TODAY)
    i = interpret(question, model, today)
    assert i.comparison_kind == "budget", i.confidence_notes
    assert i.window is not None and i.window.start.isoformat() == "2026-04-01"
    assert i.intent == "why_change"
    plan_metrics = {m.id for m in model.metrics if m.plan_for}
    assert not set(i.metric_ids) & plan_metrics, "a plan metric is never the subject"
    revenue = [p for p in i.premises if p.metric_id == "revenue"]
    if premise is None:
        assert not revenue
    else:
        assert len(revenue) == 1 and revenue[0].expectation == premise
        assert model.get_metric(revenue[0].baseline_metric_id).plan_for == "revenue"
    if ambiguous:
        amb = next(a for a in i.ambiguous if a.term == ambiguous)
        assert {"gross_margin", "operating_profit"} <= {c.metric_id for c in amb.candidates}, (
            "profit stays ambiguous"
        )
        i = apply_choices(i, model, {ambiguous: "operating_profit"})
        subject = "operating_profit"
    assert not i.ambiguous
    assert i.metric_ids[0] == subject
    assert i.baseline_metric_id == "plan_operating_profit", "the subject is compared with its own plan"
    assert i.template_id == "plan_variance"


def test_net_profit_resolves_visibly_through_the_glossary(model: Any) -> None:
    from analystos_investigator import interpret

    i = interpret(FLAGSHIP, model, dt.date.fromisoformat(TODAY))
    assert i.resolved_terms["net profit"] == "operating_profit"
    assert any("net profit" in n and "Net Profit" in n for n in i.confidence_notes)
    assert not any("net profit" in [s.lower() for s in m.synonyms] for m in model.metrics), (
        "glossary, not a synonym"
    )
    missed = next(p for p in i.premises if p.metric_id == "operating_profit")
    assert missed.expectation == "misses_plan" and missed.baseline_metric_id == "plan_operating_profit"


def test_flagship_question_is_the_answer_key_question(scenarios: dict[str, Any]) -> None:
    assert scenarios["stories"]["operating_profit_miss_q2_2026"]["questions"][0] == FLAGSHIP
