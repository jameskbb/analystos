from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from analystos_engine.analysis.anomaly import detect_anomalies, detect_change_points, detect_metric_anomalies
from analystos_engine.analysis.correlation import correlation_matrix, ols_regression, permutation_importance
from analystos_engine.analysis.forecast import ForecastError, forecast, forecast_metric
from analystos_engine.analysis.segmentation import (
    kmeans_segments,
    product_quadrants,
    rfm_from_table,
    rfm_segments,
)
from analystos_engine.analysis.stats import (
    bootstrap_ci,
    chi_square,
    mean_ci,
    proportion_ci,
    proportion_z_test,
    t_test,
)
from analystos_engine.analysis.timeseries import infer_frequency, to_series
from analystos_engine.types import TimeWindow


def daily_series(n=120, seed=1, weekly=True):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    base = 1000 + (np.array([0, 50, 80, 60, 40, -120, -150])[idx.dayofweek] if weekly else 0)
    return pd.Series(base + rng.normal(0, 15, n), index=idx)


# ----------------------------------------------------------------------------- anomaly


def test_anomaly_finds_planted_spike_and_drop_not_noise():
    s = daily_series()
    s.iloc[80] -= 400  # a planted drop
    s.iloc[100] += 350  # a planted spike
    r = detect_anomalies(s, sensitivity="medium")
    flagged = {p.timestamp for p in r.anomalies}
    assert s.index[80].date() in flagged and s.index[100].date() in flagged
    assert len(r.anomalies) <= 4  # ordinary weekly pattern and noise are not anomalies
    drop = next(p for p in r.anomalies if p.timestamp == s.index[80].date())
    assert drop.direction == "down" and drop.expected is not None and drop.lower < drop.expected < drop.upper
    assert r.seasonal_period == 7 and r.frequency == "day"


def test_sensitivity_changes_number_of_anomalies():
    s = daily_series(seed=3)
    s.iloc[90] += 70
    low = detect_anomalies(s, sensitivity="low")
    high = detect_anomalies(s, sensitivity="high")
    assert len(high.anomalies) >= len(low.anomalies)
    custom = detect_anomalies(s, sensitivity=10.0)
    assert custom.anomalies == []
    with pytest.raises(ValueError):
        detect_anomalies(s, sensitivity="extreme")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        detect_anomalies(s, sensitivity=-1.0)


def test_no_lookahead():
    s = daily_series()
    base = detect_anomalies(s.iloc[:90])
    s2 = s.copy()
    s2.iloc[95:] += 5000  # future changes must not alter past scores
    full = detect_anomalies(s2)
    for a, b in zip(base.points, full.points[:90], strict=True):
        assert a.score == b.score


def test_non_seasonal_rolling_median():
    s = daily_series(weekly=False)
    s.iloc[70] += 300
    r = detect_anomalies(s, seasonal_period=None)
    assert r.seasonal_period is None and "rolling median" in r.method
    assert s.index[70].date() in {p.timestamp for p in r.anomalies}


def test_change_point_detection():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2026-01-01", periods=100, freq="D")
    y = np.concatenate([rng.normal(100, 3, 60), rng.normal(80, 3, 40)])
    cps = detect_change_points(pd.Series(y, index=idx))
    assert len(cps) == 1
    assert abs(cps[0].index - 60) <= 2
    assert cps[0].magnitude == pytest.approx(-20, abs=2)
    assert detect_change_points(pd.Series(rng.normal(100, 3, 100), index=idx)) == []
    assert detect_change_points([(idx[0], 1.0), (idx[1], 2.0)]) == []


def test_short_series_note():
    r = detect_anomalies([(dt.date(2026, 1, i), float(i)) for i in range(1, 5)])
    assert r.anomalies == [] and any("too short" in n for n in r.notes)


def test_metric_anomalies_on_store(star_store, model):
    w = TimeWindow(dimension="order_date", start=dt.date(2026, 7, 1), end=dt.date(2026, 9, 1))
    r = detect_metric_anomalies(star_store, model, "revenue", w, grain="day")
    assert r.sql and r.metric_id == "revenue"
    # Days between the first and last order (Jul 5 .. Aug 15) are filled, missing days = 0 for an
    # additive metric; days of the window outside the data's coverage are left out, not zero.
    assert len(r.points) == 42


def test_series_helpers():
    s = to_series({dt.date(2026, 1, 1): 1, dt.date(2026, 2, 1): 2, dt.date(2026, 3, 1): 3})
    assert infer_frequency(s.index) == "month"
    assert infer_frequency(pd.DatetimeIndex(["2026-01-01"])) == "irregular"
    df = pd.DataFrame({"t": ["2026-01-01", "2026-01-08"], "v": [1, 2]})
    assert infer_frequency(to_series(df).index) == "week"
    with pytest.raises(ValueError):
        to_series(42)


# ---------------------------------------------------------------------------- forecast


def monthly_series(n=48, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="MS")
    y = 1000 + 5 * np.arange(n) + 100 * np.sin(np.arange(n) * 2 * np.pi / 12) + rng.normal(0, 10, n)
    return pd.Series(y, index=idx)


def test_forecast_models_intervals_and_backtest():
    s = monthly_series()
    r = forecast(s, 6, interval=0.8, backtest_folds=3)
    assert r.frequency == "month" and r.seasonal_period == 12
    assert {m.name for m in r.models} == {"naive", "seasonal_naive", "ets", "arima"}
    for m in r.models:
        assert m.error is None, (m.name, m.error)
        assert len(m.points) == 6
        assert all(p.lower <= p.mean <= p.upper for p in m.points)
        assert m.backtest is not None and m.backtest.mae is not None and len(m.backtest.folds) == 3
        assert m.backtest.mape is not None and 0 <= m.backtest.coverage <= 1
    assert r.model("ets").points[0].timestamp == dt.date(2026, 1, 1)
    assert r.best_model in ("seasonal_naive", "ets", "arima")  # beats naive on seasonal data
    naive_mae = r.model("naive").backtest.mae
    assert r.model(r.best_model).backtest.mae <= naive_mae
    ets = r.model("ets")
    widths = [p.upper - p.lower for p in ets.points]
    assert widths[-1] >= widths[0]  # uncertainty grows with horizon


def test_forecast_short_and_invalid():
    s = monthly_series(n=6)
    r = forecast(s, 3)
    assert r.model("naive").error is None
    assert r.model("seasonal_naive").error is not None
    assert r.model("arima").error is not None
    with pytest.raises(ForecastError):
        forecast(s.iloc[:2], 3)
    with pytest.raises(ForecastError):
        forecast(s, 0)
    with pytest.raises(ForecastError):
        forecast(s, 2, interval=1.5)
    with pytest.raises(ForecastError):
        forecast(s, 2, models=["prophet"])  # type: ignore[list-item]


def test_forecast_interpolates_missing():
    s = monthly_series(n=30)
    s.iloc[10] = np.nan
    r = forecast(s, 2, models=["naive", "ets"])
    assert any("interpolated" in n for n in r.notes)


def test_forecast_metric(star_store, model):
    w = TimeWindow(dimension="order_date", start=dt.date(2026, 7, 1), end=dt.date(2026, 9, 1))
    r = forecast_metric(star_store, model, "revenue", w, "day", 7, models=["naive"])
    assert r.sql and len(r.model("naive").points) == 7


# ------------------------------------------------------------------------------- stats


def test_t_test():
    rng = np.random.default_rng(0)
    a, b = rng.normal(10, 2, 200), rng.normal(9, 2, 200)
    r = t_test(a, b)
    assert r.significant and r.ci_low < r.estimate < r.ci_high and r.ci_low > 0
    assert r.effect_size == pytest.approx(0.5, abs=0.2)
    assert {c.name for c in r.assumptions} >= {"normality (a)", "equal variances", "independent samples"}
    small = t_test([1, 2, 3, 4], [1.5, 2.5, 3.5, 4.5, None], equal_var=True)
    assert not small.significant and "Student" in small.test
    with pytest.raises(ValueError):
        t_test([1], [1, 2])


def test_chi_square_and_proportions():
    r = chi_square([[50, 30], [20, 60]])
    assert r.significant and r.effect_size > 0.2 and r.assumptions[0].passed
    sparse = chi_square([[1, 2], [3, 1]])
    assert sparse.assumptions[0].passed is False
    with pytest.raises(ValueError):
        chi_square([[1, 2]])
    p = proportion_z_test(120, 1000, 90, 1000)
    assert p.significant and p.estimate == pytest.approx(0.03)
    small = proportion_z_test(2, 10, 1, 10)
    assert small.assumptions[0].passed is False
    with pytest.raises(ValueError):
        proportion_z_test(5, 3, 1, 10)


def test_intervals():
    ci = mean_ci([1, 2, 3, 4, 5])
    assert ci.low < 3 < ci.high
    w = proportion_ci(0, 20)
    assert w.low == 0 and 0 < w.high < 0.2
    b1 = bootstrap_ci(list(range(100)), seed=1)
    b2 = bootstrap_ci(list(range(100)), seed=1)
    assert b1 == b2 and b1.low < 49.5 < b1.high
    assert bootstrap_ci([1, 2, 3, 100], stat="median").estimate == 2.5
    with pytest.raises(ValueError):
        mean_ci([1])


# ------------------------------------------------------------------------ segmentation


def test_rfm_segments():
    as_of = dt.date(2026, 9, 1)
    rows = []
    for i in range(50):
        rows.append(
            {
                "customer": f"C{i:02d}",
                "last": as_of - dt.timedelta(days=5 + i * 7),
                "orders": 50 - i,
                "revenue": 10000 - i * 150,
            }
        )
    r = rfm_segments(
        rows,
        id_col="customer",
        last_date_col="last",
        frequency_col="orders",
        monetary_col="revenue",
        as_of=as_of,
    )
    by = {c.customer: c for c in r.customers}
    assert by["C00"].segment == "Champions" and by["C00"].r == 5 and by["C00"].f == 5
    assert by["C49"].segment == "Lost"
    assert sum(s.count for s in r.segments) == 50
    assert sum(s.value_share for s in r.segments) == pytest.approx(1.0)


def test_rfm_from_table(star_store):
    r = rfm_from_table(
        star_store,
        "orders",
        customer_col="customer_id",
        date_col="order_date",
        amount_col="shipping_fee",
        as_of=dt.date(2026, 9, 1),
        order_col="order_id",
        quantiles=3,
    )
    assert len(r.customers) == 3 and r.sql


def test_product_quadrants():
    data = pd.DataFrame(
        {
            "sku": ["a", "b", "c", "d", "e"],
            "growth": [0.3, -0.1, 0.2, -0.2, None],
            "margin": [0.4, 0.5, 0.1, 0.05, 0.3],
            "units": [10, 20, 30, 40, 5],
        }
    )
    r = product_quadrants(
        data, id_col="sku", growth_col="growth", profitability_col="margin", volume_col="units"
    )
    q = {row.item: row.quadrant for row in r.rows}
    assert q["a"].startswith("Stars") and q["d"].startswith("Laggards") and q["e"] == "Insufficient data"
    r2 = product_quadrants(
        data,
        id_col="sku",
        growth_col="growth",
        profitability_col="margin",
        growth_threshold=0.0,
        profitability_threshold=0.2,
    )
    assert r2.growth_threshold == 0.0


def test_kmeans():
    rng = np.random.default_rng(0)
    pts = np.vstack(
        [rng.normal(0, 0.3, (30, 2)), rng.normal(5, 0.3, (30, 2)), rng.normal([0, 5], 0.3, (30, 2))]
    )
    df = pd.DataFrame(pts, columns=["x", "y"]).assign(id=range(90))
    r = kmeans_segments(df, ["x", "y"], id_col="id")
    assert r.k == 3 and r.silhouette > 0.7 and r.exploratory
    assert sorted(r.sizes) == [30, 30, 30]
    with pytest.raises(ValueError):
        kmeans_segments(df.head(2), ["x", "y"])


# ------------------------------------------------------------------------- correlation


def test_correlation_regression_importance():
    rng = np.random.default_rng(0)
    n = 300
    price = rng.normal(10, 2, n)
    discount = rng.uniform(0, 0.3, n)
    noise = rng.normal(0, 1, n)
    units = 100 - 3 * price + 60 * discount + rng.normal(0, 2, n)
    df = pd.DataFrame({"price": price, "discount": discount, "noise": noise, "units": units})
    c = correlation_matrix(df, method="spearman")
    assert c.exploratory and "not show" in c.caveat
    top = c.pairs[0]
    assert {top.a, top.b} == {"price", "units"} and top.strength in ("moderate", "strong")
    assert c.matrix[0][0] == 1.0
    reg = ols_regression(df, "units", ["price", "discount", "noise"])
    coef = {x.name: x for x in reg.coefficients}
    assert coef["price"].coef == pytest.approx(-3, abs=0.3)
    assert coef["discount"].ci_low < 60 < coef["discount"].ci_high
    assert reg.r_squared > 0.8 and set(reg.vif) == {"price", "discount", "noise"}
    imp = permutation_importance(df, "units", ["price", "discount", "noise"])
    assert imp.features[0].name == "price" and imp.features[-1].name == "noise"
    assert imp.task == "regression"
    df["collinear"] = df["price"] * 2 + rng.normal(0, 1e-6, n)
    reg2 = ols_regression(df, "units", ["price", "collinear"])
    assert any("collinear" in w for w in reg2.warnings)
    with pytest.raises(ValueError):
        ols_regression(df.head(2), "units", ["price", "discount"])
    df["segment"] = np.where(df["units"] > df["units"].median(), "high", "low")
    cls = permutation_importance(df, "segment", ["price", "discount"])
    assert cls.task == "classification"


def test_non_seasonal_models_backtested_when_series_is_shorter_than_two_seasons():
    # 24 monthly points with a 12-month season: seasonal models need 26 points to backtest.
    idx = pd.date_range("2024-10-01", periods=24, freq="MS")
    s = pd.Series(100 + np.arange(24) * 2.0 + 5 * np.sin(np.arange(24) / 12 * 2 * np.pi), index=idx)
    res = forecast(s, 3, seasonal_period=12)
    naive = res.model("naive")
    assert naive.backtest is not None and naive.backtest.mae is not None
    sn = res.model("seasonal_naive")
    assert sn.backtest is None and "two seasons" in (sn.backtest_note or "")
    assert res.best_model is not None
    assert any("backtested models only" in n for n in res.notes)


def test_metric_series_drops_periods_outside_the_data(star_store, model):
    from analystos_engine.analysis.timeseries import metric_series

    window = TimeWindow(start=dt.date(2026, 5, 1), end=dt.date(2026, 11, 1))
    s, _ = metric_series(star_store, model, "revenue", window, "month")
    assert list(s.index.strftime("%Y-%m")) == ["2026-07", "2026-08"]  # data covers July-August only
    assert s.attrs["trimmed"] == (2, 2)
    # Inside the data's coverage, days without orders are real zeros.
    daily, _ = metric_series(
        star_store, model, "revenue", TimeWindow(start=dt.date(2026, 7, 1), end=dt.date(2026, 9, 1)), "day"
    )
    assert len(daily) == 62 - 4 - 16 and (daily == 0).any()
