from __future__ import annotations

import datetime as dt

import pytest
from analystos_engine.semantic.models import SemanticModel
from analystos_investigator import CommandContext, parse_command
from inv_fixture import TODAY


@pytest.fixture()
def ctx(value_index: dict[str, list[str]]) -> CommandContext:
    return CommandContext(today=TODAY, value_index=value_index, node_id="n_1", investigation_id="inv_1")


def test_spec_section_30_commands(ctx: CommandContext, model: SemanticModel) -> None:
    c = parse_command("Compare this quarter with last quarter.", ctx, model)
    assert c.kind == "compare" and c.comparison_kind == "pop"
    assert c.window and c.window.start == dt.date(2026, 7, 1)
    assert c.baseline and c.baseline.start == dt.date(2026, 4, 1)

    c = parse_command("Break this down by region.", ctx, model)
    assert (c.kind, c.dimension, c.target_node_id) == ("breakdown", "region", "n_1")

    c = parse_command("Show me the customers responsible for most of the decline.", ctx, model)
    assert (c.kind, c.dimension) == ("top_contributors", "customer_name")
    assert c.params["direction"] == "decline"

    c = parse_command("Exclude new stores.", ctx, model)
    assert c.kind == "exclude" and [f.describe() for f in c.filters] == ["branch_type excludes New"]
    assert not c.needs_clarification

    c = parse_command("Use gross margin instead.", ctx, model)
    assert (c.kind, c.metric_id) == ("switch_metric", "gross_margin")

    for text, kind in [
        ("Save this as a finding.", "save_finding"),
        ("Turn this into a dashboard.", "build_dashboard"),
        ("Build me a report.", "build_report"),
        ("Show the SQL.", "show_sql"),
    ]:
        c = parse_command(text, ctx, model)
        assert c.kind == kind and c.investigation_id == "inv_1"


def test_ambiguous_metric_switch_is_not_guessed(ctx: CommandContext, model: SemanticModel) -> None:
    c = parse_command("Use margin instead", ctx, model)
    assert c.kind == "switch_metric" and c.metric_id is None
    assert c.ambiguous and c.needs_clarification


def test_unmatched_metric_words_are_flagged(ctx: CommandContext, model: SemanticModel) -> None:
    c = parse_command("Use gross revenue instead", ctx, model)
    assert c.metric_id == "revenue"
    assert c.unresolved and "gross" in c.unresolved[0]


def test_unresolvable_exclusion_is_flagged(ctx: CommandContext, model: SemanticModel) -> None:
    c = parse_command("Exclude pet rocks", ctx, model)
    assert c.kind == "exclude" and not c.filters and c.needs_clarification


def test_other_commands(ctx: CommandContext, model: SemanticModel) -> None:
    assert parse_command("Compare this to last year", ctx, model).comparison_kind == "yoy"
    assert parse_command("compare to budget", ctx, model).comparison_kind == "budget"
    only = parse_command("Only Dallas", ctx, model)
    assert only.kind == "include" and only.filters[0].describe() == "branch_name = Dallas"
    d = parse_command("Drill into Dallas", ctx, model)
    assert d.kind == "drill" and d.dimension == "branch_name"
    assert parse_command("by channel", ctx, model).dimension == "channel"
    assert parse_command("rerun", ctx, model).kind == "rerun"
    assert parse_command("reject this", ctx, model).kind == "reject_node"
    assert parse_command("confirm this", ctx, model).kind == "confirm_node"
    q = parse_command("Why did orders fall in August?", ctx, model)
    assert q.kind == "question" and q.metric_id == "orders"
    u = parse_command("blah blah", ctx, model)
    assert u.kind == "unknown" and u.needs_clarification
    bad = parse_command("Break this down by planet", ctx, model)
    assert bad.kind == "breakdown" and bad.dimension is None and bad.unresolved
