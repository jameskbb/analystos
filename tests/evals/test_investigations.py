"""Investigation evals: run the investigator end to end on every benchmark question (spec §81, §83)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from .conftest import TODAY
from .scoring import QuestionResult, Scorer, format_scorecard, load_benchmark, run_question

BENCHMARK = load_benchmark()


@pytest.fixture(scope="session")
def results(store: Any, model: Any, scenarios: dict[str, Any], gt: Any) -> dict[str, QuestionResult]:
    from analystos_investigator import build_value_index

    labels: dict[str, set[str]] = {}
    for pid, name, sku in gt.execute("SELECT product_id, product_name, sku FROM products").fetchall():
        labels[pid] = {name, sku, pid}
    scorer = Scorer(scenarios, labels)
    value_index = build_value_index(store, model)
    today = dt.date.fromisoformat(TODAY)
    return {q["id"]: run_question(q, store, model, value_index, scorer, today) for q in BENCHMARK}


@pytest.mark.parametrize("spec", BENCHMARK, ids=[q["id"] for q in BENCHMARK])
def test_benchmark_question(spec: dict[str, Any], results: dict[str, QuestionResult]) -> None:
    r = results[spec["id"]]
    assert r.error is None, r.error
    failed = [f"{c.id}: {c.detail}" for c in r.checks if c.required and not c.passed]
    assert not failed, f"{spec['question']} -> missing required evidence: {failed}"


def test_investigations_are_fast(results: dict[str, QuestionResult]) -> None:
    slow = {k: round(r.seconds, 1) for k, r in results.items() if r.seconds > 30}
    assert not slow, f"investigations over 30s: {slow}"


def test_rerun_is_reproducible(store: Any, model: Any) -> None:
    """Same data + same definitions -> the same tree (spec §60)."""
    from analystos_investigator import investigate, rerun

    first = investigate(
        "Why was August revenue down?", store, model, today=dt.date.fromisoformat(TODAY), auto_approve=True
    )
    again, diff = rerun(first, store, model)
    assert again.investigation.brief_answer == first.investigation.brief_answer
    assert not diff.node_changes
    assert not diff.dataset_version_changes
    assert not diff.metric_version_changes


def test_scorecard(results: dict[str, QuestionResult]) -> None:
    """Prints the scorecard (run with -s). Fails only if a required expectation failed."""
    ordered = [results[q["id"]] for q in BENCHMARK]
    print(format_scorecard(ordered))
    assert all(r.required_ok for r in ordered)
