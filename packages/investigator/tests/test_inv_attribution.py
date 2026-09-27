from __future__ import annotations

import math

import pytest
from analystos_investigator.attribution import (
    additive_contribution,
    decompose_drivers,
    log_mean,
    ratio_contribution,
)


def test_additive_partition_shares_sum_to_one() -> None:
    cur = {"Dallas": 240.0, "Houston": 220.0, "Austin": 230.0, "San Antonio": 210.0}
    base = {"Dallas": 300.0, "Houston": 250.0, "Austin": 250.0, "San Antonio": 200.0}
    out = additive_contribution(cur, base, 900.0, 1000.0)
    assert out.method == "additive" and out.additive_valid
    assert sum(r.share or 0 for r in out.rows) == pytest.approx(1.0)
    shares = {r.value: r.share for r in out.rows}
    assert shares["Dallas"] == pytest.approx(0.6)
    assert shares["San Antonio"] == pytest.approx(-0.1)
    assert [r.value for r in out.ranked()][:2] == ["Dallas", "Houston"]


def test_segments_present_in_one_period_only() -> None:
    out = additive_contribution({"A": 100.0, "New": 20.0}, {"A": 100.0, "Gone": 40.0}, 120.0, 140.0)
    assert out.additive_valid
    shares = {r.value: r.share for r in out.rows}
    assert shares["Gone"] == pytest.approx(2.0)
    assert shares["New"] == pytest.approx(-1.0)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_overlapping_segments_are_not_additive() -> None:
    # distinct customers by channel: customers use both channels, so segments overlap
    out = additive_contribution({"Online": 8.0, "Counter": 9.0}, {"Online": 9.0, "Counter": 9.0}, 9.0, 10.0)
    assert out.method == "non_additive"
    assert not out.additive_valid
    assert all(r.share is None for r in out.rows)
    assert "no share of change is claimed" in out.notes[0]


def test_zero_total_change_has_undefined_shares() -> None:
    out = additive_contribution({"A": 5.0, "B": 5.0}, {"A": 6.0, "B": 4.0}, 10.0, 10.0)
    assert out.additive_valid
    assert all(r.share is None for r in out.rows)
    assert out.notes


def test_ratio_mix_rate_effects_sum_exactly_to_total_change() -> None:
    num_c = {"Dallas": 240_000.0, "Houston": 220_000.0, "Austin": 230_000.0, "SA": 210_000.0}
    den_c = {"Dallas": 240.0, "Houston": 250.0, "Austin": 230.0, "SA": 210.0}
    num_b = {"Dallas": 300_000.0, "Houston": 250_000.0, "Austin": 250_000.0, "SA": 200_000.0}
    den_b = {"Dallas": 300.0, "Houston": 250.0, "Austin": 250.0, "SA": 200.0}
    out = ratio_contribution(num_c, den_c, num_b, den_b)
    assert out.method == "ratio_mix_rate"
    assert out.total_current == pytest.approx(900_000 / 930)
    assert out.total_baseline == pytest.approx(1000.0)
    total = sum(r.effect for r in out.rows)
    assert total == pytest.approx(out.total_change)
    assert sum(r.share or 0 for r in out.rows) == pytest.approx(1.0)
    for r in out.rows:
        assert (r.mix_effect or 0) + (r.rate_effect or 0) == pytest.approx(r.effect)
    top = out.ranked()[0]
    assert top.value == "Houston"
    assert top.rate_effect is not None and top.rate_effect < 0


def test_ratio_with_zero_denominator_is_not_decomposed() -> None:
    out = ratio_contribution({"A": 1.0}, {"A": 0.0}, {"A": 1.0}, {"A": 1.0})
    assert out.method == "non_additive" and not out.rows


def test_lmdi_multiplicative_effects_sum_exactly() -> None:
    out = decompose_drivers(
        900_000.0,
        1_000_000.0,
        [("orders", "multiplicative", 930.0, 1000.0), ("aov", "multiplicative", 900_000 / 930, 1000.0)],
    )
    assert out.method == "lmdi" and out.valid
    assert sum(e.effect for e in out.effects) == pytest.approx(-100_000.0)
    assert abs(out.residual) < 1e-6
    shares = {e.metric_id: e.share for e in out.effects}
    assert shares["orders"] == pytest.approx(math.log(0.93) / math.log(0.9), rel=1e-9)
    assert shares["orders"] == pytest.approx(0.6888, abs=1e-4)
    assert shares["aov"] == pytest.approx(0.3112, abs=1e-4)


def test_lmdi_ratio_identity_uses_negative_exponent_for_denominator() -> None:
    # AOV = revenue / orders
    out = decompose_drivers(
        900_000 / 930,
        1000.0,
        [
            ("revenue", "ratio_numerator", 900_000.0, 1_000_000.0),
            ("orders", "ratio_denominator", 930.0, 1000.0),
        ],
    )
    assert out.valid
    effects = {e.metric_id: e.effect for e in out.effects}
    assert effects["revenue"] < 0 < effects["orders"]
    assert sum(effects.values()) == pytest.approx(900_000 / 930 - 1000.0)


def test_additive_identity_with_subtraction() -> None:
    out = decompose_drivers(
        270.0, 300.0, [("revenue", "additive", 900.0, 1000.0), ("cogs", "subtractive", 630.0, 700.0)]
    )
    assert out.method == "additive_identity"
    eff = {e.metric_id: e.effect for e in out.effects}
    assert eff == {"revenue": pytest.approx(-100.0), "cogs": pytest.approx(70.0)}
    assert out.residual == pytest.approx(0.0)


def test_identity_gap_is_reported_as_residual() -> None:
    out = decompose_drivers(100.0, 80.0, [("a", "additive", 90.0, 80.0)])
    assert out.residual == pytest.approx(10.0)
    assert out.residual_share == pytest.approx(0.5)
    assert out.notes


def test_mixed_relations_and_nonpositive_values_are_refused() -> None:
    mixed = decompose_drivers(1.0, 2.0, [("a", "additive", 1.0, 1.0), ("b", "multiplicative", 1.0, 2.0)])
    assert not mixed.valid
    neg = decompose_drivers(1.0, 2.0, [("a", "multiplicative", 0.0, 2.0)])
    assert not neg.valid and "zero or negative" in neg.notes[0]


def test_log_mean() -> None:
    assert log_mean(2.0, 2.0) == 2.0
    assert log_mean(2.0, 1.0) == pytest.approx(1 / math.log(2))
    with pytest.raises(ValueError):
        log_mean(-1.0, 1.0)
