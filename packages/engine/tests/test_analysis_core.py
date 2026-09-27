from __future__ import annotations

import datetime as dt
import math

import pytest
from analystos_engine.analysis.compare import (
    compare_metrics,
    compare_periods,
    compare_segments,
    compare_to_previous,
    pct_change,
)
from analystos_engine.analysis.contribution import (
    contribution_across_dimensions,
    contribution_by_dimension,
    metric_additivity,
)
from analystos_engine.analysis.decomposition import (
    DecompositionError,
    decompose,
    decompose_metric,
    logmean,
    shapley_product,
)
from analystos_engine.semantic.compiler import MetricQuery, compile, run_metric_query
from analystos_engine.semantic.models import DriverEdge, Filter, Metric, MetricTree
from analystos_engine.types import QueryColumn, QueryResult, TimeWindow
from analystos_engine.validation import QueryExpectations, expectations_for, validate_result

JUL = TimeWindow(
    dimension="order_date",
    start=dt.date(2026, 7, 1),
    end=dt.date(2026, 8, 1),
    kind="month",
    label="July 2026",
)
AUG = TimeWindow(
    dimension="order_date",
    start=dt.date(2026, 8, 1),
    end=dt.date(2026, 9, 1),
    kind="month",
    label="August 2026",
)


# ----------------------------------------------------------------------------- compare


def test_compare_periods(star_store, model):
    c = compare_periods(star_store, model, "revenue", AUG, JUL, kind="pop")
    assert (c.current, c.baseline, c.abs_change) == (460.0, 250.0, 210.0)
    assert c.pct_change == pytest.approx(0.84)
    assert c.current_label == "August 2026" and c.baseline_label == "July 2026"
    assert "SELECT" in c.sql_current and c.sql_current != c.sql_baseline
    assert "revenue" in c.metric_versions


def test_compare_to_previous_and_yoy(star_store, model):
    c = compare_to_previous(star_store, model, "orders", AUG, "pop")
    assert (c.current, c.baseline) == (3.0, 2.0)
    y = compare_to_previous(star_store, model, "orders", AUG, "yoy")
    assert y.baseline == 0.0 and y.pct_change is None
    assert any("undefined" in n for n in y.notes)


def test_compare_with_filters_and_segments(star_store, model):
    c = compare_periods(
        star_store, model, "revenue", AUG, JUL, [Filter(dimension="region", op="eq", values=["Dallas"])]
    )
    assert (c.current, c.baseline) == (380.0, 150.0)
    s = compare_segments(star_store, model, "revenue", AUG, "region", "Dallas", "Houston")
    assert (s.current, s.baseline, s.abs_change) == (380.0, 30.0, 350.0)
    assert s.kind == "segment" and s.current_label == "region = Dallas"


def test_compare_metrics_actual_vs_target(star_store, model):
    c = compare_metrics(star_store, model, "completed_revenue", "revenue", AUG)
    assert c.kind == "target" and c.baseline_metric_id == "revenue"
    assert c.abs_change == -30.0


def test_pct_change():
    assert pct_change(110, 100) == pytest.approx(0.1)
    assert pct_change(-50, -100) == pytest.approx(0.5)
    assert pct_change(1, 0) is None and pct_change(None, 1) is None


def test_window_length_note(star_store, model):
    w = TimeWindow(dimension="order_date", start=dt.date(2026, 8, 1), end=dt.date(2026, 8, 11))
    c = compare_periods(star_store, model, "revenue", w, JUL)
    assert any("differ in length" in n for n in c.notes)


# ------------------------------------------------------------------------ contribution


def test_additive_contribution_sums_exactly(star_store, model):
    r = contribution_by_dimension(star_store, model, "revenue", "region", AUG, JUL)
    assert r.method == "additive" and r.additive_valid
    by = {row.segment: row for row in r.rows}
    assert by["Dallas"].change == 230.0 and by["Houston"].change == -70.0 and by["Austin"].change == 50.0
    assert r.total_change == 210.0
    assert sum(row.contribution for row in r.rows) == pytest.approx(r.total_change)
    assert sum(row.share_of_change for row in r.rows) == pytest.approx(1.0)
    assert r.rows[0].segment == "Dallas"  # sorted by absolute contribution
    assert len(r.queries) == 4 and r.sql


def test_ratio_mix_rate_sums_exactly(star_store, model):
    r = contribution_by_dimension(star_store, model, "aov", "region", AUG, JUL)
    assert r.method == "ratio_mix_rate" and r.additive_valid
    assert r.total_baseline == pytest.approx(125.0)
    assert r.total_current == pytest.approx(460 / 3)
    assert sum(row.mix_effect + row.rate_effect for row in r.rows) == pytest.approx(r.total_change)
    austin = next(row for row in r.rows if row.segment == "Austin")
    assert austin.rate_effect == 0.0 and austin.mix_effect == pytest.approx((1 / 3) * (50 - 125))
    assert sum(row.current_weight for row in r.rows) == pytest.approx(1.0)
    assert sum(row.share_of_change for row in r.rows) == pytest.approx(1.0)


def test_volume_mix_rate(star_store, model):
    r = contribution_by_dimension(star_store, model, "revenue", "category", AUG, JUL, volume_metric="units")
    assert r.method == "volume_mix_rate" and r.additive_valid
    for row in r.rows:
        assert row.volume_effect + row.mix_effect + row.rate_effect == pytest.approx(row.change)
    # total volume effect = (V1 - V0) * baseline revenue per unit = (11 - 5) * 250 / 5
    assert sum(row.volume_effect for row in r.rows) == pytest.approx(300.0)
    assert sum(row.contribution for row in r.rows) == pytest.approx(r.total_change)


def test_non_additive_count_distinct_overlap(star_store, model):
    # a customer ordering in two statuses is counted in both status segments
    r = contribution_by_dimension(star_store, model, "active_customers", "status", AUG, JUL)
    assert r.method == "non_additive" and not r.additive_valid
    assert all(row.share_of_change is None for row in r.rows)
    assert any("not additive" in n for n in r.notes)


def test_avg_metric_non_additive(star_store, model):
    r = contribution_by_dimension(star_store, model, "avg_line", "region", AUG, JUL)
    assert r.method == "non_additive" and not r.additive_valid


def test_top_n_other_bucket_keeps_sum(star_store, model):
    r = contribution_by_dimension(star_store, model, "revenue", "region", AUG, JUL, top_n=1)
    assert len(r.rows) == 2 and r.rows[1].is_other and r.rows[1].segment_count == 2
    assert sum(row.contribution for row in r.rows) == pytest.approx(210.0)


def test_metric_additivity(model):
    model.metrics.append(Metric(id="net", name="Net", kind="derived", formula="revenue - shipping"))
    model.metrics.append(Metric(id="scaled", name="Scaled", kind="derived", formula="2 * revenue"))
    model.metrics.append(Metric(id="prod", name="Prod", kind="derived", formula="revenue * units"))
    assert metric_additivity(model, "revenue") == "additive"
    assert metric_additivity(model, "net") == "additive"
    assert metric_additivity(model, "scaled") == "additive"
    assert metric_additivity(model, "prod") == "non_additive"
    assert metric_additivity(model, "aov") == "ratio"
    assert metric_additivity(model, "asp") == "non_additive"
    assert metric_additivity(model, "active_customers") == "non_additive"


def test_across_dimensions_not_additive(star_store, model):
    m = contribution_across_dimensions(
        star_store, model, "revenue", ["region", "category", "status"], AUG, JUL
    )
    assert not m.additive_valid and "must not be added" in m.notes[0]
    assert {r.dimension for r in m.results} == {"region", "category", "status"}
    assert m.ranking[0].top_share is not None
    shares = [abs(r.top_share) for r in m.ranking if r.top_share is not None]
    assert shares == sorted(shares, reverse=True)
    bad = contribution_across_dimensions(star_store, model, "orders", ["category", "region"], AUG, JUL)
    assert "category" in bad.errors and len(bad.results) == 1


# ----------------------------------------------------------------------- decomposition


def test_logmean_and_shapley():
    assert logmean(2, 2) == 2
    assert logmean(1, math.e) == pytest.approx((math.e - 1) / 1)
    with pytest.raises(DecompositionError):
        logmean(0, 1)
    eff = shapley_product([2.0, 3.0, 4.0], [3.0, -1.0, 5.0])
    assert sum(eff) == pytest.approx(3 * -1 * 5 - 2 * 3 * 4)


def _tree(*edges: tuple[str, str, str]) -> MetricTree:
    return MetricTree(
        root_metric=edges[0][0], nodes=[DriverEdge(parent=p, child=c, relation=r) for p, c, r in edges]
    )


def test_multiplicative_lmdi_exact():
    t = _tree(("rev", "vol", "multiplicative"), ("rev", "price", "multiplicative"))
    d = decompose(t, {"rev": 918.0, "vol": 91.8, "price": 10.0}, {"rev": 1000.0, "vol": 100.0, "price": 10.0})
    assert d.method == "lmdi" and d.exact
    assert sum(e.effect for e in d.effects) == pytest.approx(-82.0, abs=1e-9)
    price = next(e for e in d.effects if e.metric == "price")
    assert price.effect == pytest.approx(0.0)


def test_three_factor_lmdi_exact():
    t = _tree(
        ("rev", "cust", "multiplicative"), ("rev", "opc", "multiplicative"), ("rev", "aov", "multiplicative")
    )
    c = {"cust": 90.0, "opc": 2.2, "aov": 48.0}
    b = {"cust": 100.0, "opc": 2.0, "aov": 50.0}
    c["rev"], b["rev"] = 90 * 2.2 * 48, 100 * 2 * 50
    d = decompose(t, c, b)
    assert sum(e.effect for e in d.effects) == pytest.approx(c["rev"] - b["rev"], rel=1e-12)
    assert sum(e.share_of_change for e in d.effects) == pytest.approx(1.0)


def test_shapley_fallback_for_negative():
    t = _tree(("p", "a", "multiplicative"), ("p", "b", "multiplicative"))
    d = decompose(t, {"p": -6.0, "a": -2.0, "b": 3.0}, {"p": 4.0, "a": 2.0, "b": 2.0})
    assert d.method == "shapley" and d.exact
    assert sum(e.effect for e in d.effects) == pytest.approx(-10.0)


def test_additive_and_subtractive_with_residual():
    t = _tree(("margin", "revenue", "additive"), ("margin", "cost", "subtractive"))
    d = decompose(
        t, {"margin": 30.0, "revenue": 100.0, "cost": 70.0}, {"margin": 40.0, "revenue": 100.0, "cost": 60.0}
    )
    assert d.method == "additive" and d.exact
    assert {e.metric: e.effect for e in d.effects} == {"revenue": 0.0, "cost": -10.0}
    incomplete = decompose(
        t, {"margin": 35.0, "revenue": 100.0, "cost": 70.0}, {"margin": 40.0, "revenue": 100.0, "cost": 60.0}
    )
    assert not incomplete.exact and incomplete.residual == pytest.approx(5.0)


def test_ratio_decomposition():
    t = _tree(("conv", "wins", "ratio_numerator"), ("conv", "opps", "ratio_denominator"))
    d = decompose(t, {"conv": 0.2, "wins": 20.0, "opps": 100.0}, {"conv": 0.25, "wins": 25.0, "opps": 100.0})
    assert d.method == "ratio_lmdi" and d.exact
    assert sum(e.effect for e in d.effects) == pytest.approx(-0.05)
    assert next(e for e in d.effects if e.metric == "opps").effect == pytest.approx(0.0)
    z = decompose(t, {"conv": 0.2, "wins": 20.0, "opps": 100.0}, {"conv": 0.0, "wins": 0.0, "opps": 50.0})
    assert z.method == "ratio_shapley" and sum(e.effect for e in z.effects) == pytest.approx(0.2)
    with pytest.raises(DecompositionError):
        decompose(t, {"conv": 0.2, "wins": 20.0, "opps": 0.0}, {"conv": 0.25, "wins": 25.0, "opps": 100.0})


def test_nested_tree_effect_on_root():
    t = _tree(
        ("rev", "orders", "multiplicative"),
        ("rev", "aov", "multiplicative"),
        ("orders", "dallas_orders", "additive"),
        ("orders", "other_orders", "additive"),
    )
    cur = {"rev": 90 * 10.0, "orders": 90.0, "aov": 10.0, "dallas_orders": 30.0, "other_orders": 60.0}
    base = {"rev": 100 * 10.0, "orders": 100.0, "aov": 10.0, "dallas_orders": 40.0, "other_orders": 60.0}
    d = decompose(t, cur, base)
    orders = next(e for e in d.effects if e.metric == "orders")
    assert orders.sub is not None and orders.sub.method == "additive"
    dallas = next(e for e in orders.sub.effects if e.metric == "dallas_orders")
    assert dallas.effect_on_root == pytest.approx(orders.effect_on_root)
    assert sum(e.effect_on_root for e in orders.sub.effects) == pytest.approx(orders.effect)
    assert len(d.flat_effects()) == 4


def test_decomposition_errors():
    t = _tree(("p", "a", "multiplicative"), ("p", "b", "additive"))
    with pytest.raises(DecompositionError):
        decompose(t, {"p": 1, "a": 1, "b": 1}, {"p": 1, "a": 1, "b": 1})
    t2 = _tree(("p", "a", "additive"))
    with pytest.raises(DecompositionError):
        decompose(t2, {"p": 1}, {"p": 1, "a": 1})
    with pytest.raises(DecompositionError):
        decompose(t2, {"p": 1, "a": 1}, {"p": 1, "a": 1}, root="a")
    t2.nodes[0].approved = False
    with pytest.raises(DecompositionError):
        decompose(t2, {"p": 1, "a": 1}, {"p": 1, "a": 1})


def test_decompose_metric_on_star(star_store, model):
    md = decompose_metric(star_store, model, "revenue", AUG, JUL)
    d = md.decomposition
    assert d.method == "lmdi" and d.exact
    assert d.change == pytest.approx(210.0)
    assert sum(e.effect for e in d.effects) == pytest.approx(210.0)
    assert md.values_current["orders"] == 3.0
    assert md.sql_current and md.sql_baseline
    with pytest.raises(DecompositionError):
        decompose_metric(star_store, model, "units", AUG, JUL)


# -------------------------------------------------------------------------- validation


def test_validation_from_compiled(star_store, model):
    q = MetricQuery(
        metrics=["revenue", "orders"],
        dimensions=["region"],
        time=AUG,
        filters=[Filter(dimension="status", op="eq", values=["complete"])],
    )
    compiled, res = run_metric_query(star_store, model, q)
    report = validate_result(res, expectations_for(compiled, denominators=["orders"]))
    assert report.ok, report.summary()
    names = {c.name for c in report.checks}
    assert {
        "executed",
        "not_empty",
        "metric_present",
        "grain",
        "filters_applied",
        "denominator:orders",
    } <= names


def test_validation_failures():
    res = QueryResult(
        columns=[QueryColumn(name="region", type="VARCHAR"), QueryColumn(name="orders", type="BIGINT")],
        rows=[["Dallas", 0], ["Dallas", 2]],
        row_count=2,
        truncated=True,
    )
    exp = QueryExpectations(
        required_columns=["revenue"],
        grain=["region"],
        nonzero_columns=["orders"],
        filters=[Filter(dimension="region", op="eq", values=["Houston"])],
        required_filters=["status = complete"],
        applied_filters=[],
        max_rows=1,
        non_null_columns=["region"],
        numeric_columns=["orders"],
    )
    r = validate_result(res, exp)
    assert not r.ok
    failed = {c.name for c in r.failures}
    assert {
        "metric_present",
        "grain",
        "filter:region",
        "filters_applied",
        "row_count_plausible",
        "complete",
        "denominator:orders",
    } <= failed
    assert "grain" in r.summary()


def test_validation_empty_and_error():
    empty = QueryResult(columns=[QueryColumn(name="x", type="INT")], rows=[], row_count=0)
    assert not validate_result(empty).ok
    assert validate_result(empty, QueryExpectations(allow_empty=True)).ok
    err = validate_result(None, error="boom")
    assert not err.ok and err.checks[0].name == "executed"
    total = QueryResult(columns=[QueryColumn(name="x", type="INT")], rows=[[1], [2]], row_count=2)
    assert not validate_result(total, QueryExpectations(grain=[])).ok
    assert (
        validate_result(total, QueryExpectations(nonzero_columns=["nope"])).failures[0].name
        == "denominator:nope"
    )


def test_compiled_expectations_default(model):
    c = compile(model, MetricQuery(metrics=["revenue"]))
    e = expectations_for(c)
    assert e.required_columns == ["revenue"] and e.grain == []
