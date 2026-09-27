"""Forecasting with honest uncertainty: naive, seasonal naive, ETS and ARIMA.

Each model produces point forecasts with prediction intervals and is evaluated by a
rolling-origin backtest (MAE, MAPE, interval coverage) so that users see historical
accuracy next to the forecast. The best model is the one with the lowest backtest
MAE. Prophet is intentionally not used (see the product spec).
"""

from __future__ import annotations

import datetime as dt
import math
import warnings
from collections.abc import Callable, Sequence
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats as sps

from ..semantic.models import Filter, SemanticModel
from ..store import WorkspaceStore
from ..types import TimeWindow
from .timeseries import (
    SeriesPoint,
    default_season,
    infer_frequency,
    metric_series,
    next_timestamps,
    to_series,
)

__all__ = [
    "ForecastPoint",
    "BacktestFold",
    "BacktestResult",
    "ModelForecast",
    "ForecastResult",
    "ForecastError",
    "forecast",
    "forecast_metric",
    "MODELS",
]

ModelName = Literal["naive", "seasonal_naive", "ets", "arima"]
MODELS: tuple[ModelName, ...] = ("naive", "seasonal_naive", "ets", "arima")


class ForecastError(ValueError):
    """The series cannot be forecast (too short, empty ...)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForecastPoint(_Model):
    timestamp: dt.date | dt.datetime
    mean: float
    lower: float
    upper: float


class BacktestFold(_Model):
    origin: dt.date | dt.datetime
    horizon: int
    mae: float
    mape: float | None
    coverage: float


class BacktestResult(_Model):
    folds: list[BacktestFold] = Field(default_factory=list)
    mae: float | None = None
    mape: float | None = None
    coverage: float | None = None


class ModelForecast(_Model):
    name: ModelName
    params: dict[str, Any] = Field(default_factory=dict)
    points: list[ForecastPoint] = Field(default_factory=list)
    backtest: BacktestResult | None = None
    aic: float | None = None
    error: str | None = None
    backtest_note: str | None = None
    """Why the model has no backtest (e.g. a seasonal model needs two full seasons)."""


class ForecastResult(_Model):
    history: list[SeriesPoint] = Field(default_factory=list)
    horizon: int
    frequency: str
    seasonal_period: int | None = None
    interval: float
    models: list[ModelForecast] = Field(default_factory=list)
    best_model: ModelName | None = None
    selection_metric: str = "backtest MAE"
    notes: list[str] = Field(default_factory=list)
    sql: str | None = None
    metric_id: str | None = None
    metric_versions: dict[str, str] = Field(default_factory=dict)

    def model(self, name: str) -> ModelForecast:
        for m in self.models:
            if m.name == name:
                return m
        raise KeyError(name)


Fitted = tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any], float | None]


def _z(interval: float) -> float:
    return float(sps.norm.ppf(0.5 + interval / 2))


def _naive(
    y: pd.Series, h: int, interval: float, m: int | None, fixed: dict[str, Any] | None = None
) -> Fitted:
    vals = y.to_numpy(dtype=float)
    if len(vals) < 2:
        raise ForecastError("naive forecast needs at least 2 observations")
    sigma = float(np.std(np.diff(vals), ddof=1)) if len(vals) > 2 else 0.0
    steps = np.arange(1, h + 1)
    mean = np.full(h, vals[-1])
    half = _z(interval) * sigma * np.sqrt(steps)
    return mean, mean - half, mean + half, {"method": "last value"}, None


def _seasonal_naive(
    y: pd.Series, h: int, interval: float, m: int | None, fixed: dict[str, Any] | None = None
) -> Fitted:
    vals = y.to_numpy(dtype=float)
    if not m or len(vals) < m + 2:
        raise ForecastError("seasonal naive needs a seasonal period and more than one full season")
    resid = vals[m:] - vals[:-m]
    sigma = float(np.std(resid, ddof=1)) if len(resid) > 1 else 0.0
    idx = np.arange(h)
    mean = np.array([vals[len(vals) - m + (i % m)] for i in idx])
    k = np.floor(idx / m) + 1
    half = _z(interval) * sigma * np.sqrt(k)
    return mean, mean - half, mean + half, {"seasonal_period": m}, None


def _ets(y: pd.Series, h: int, interval: float, m: int | None, fixed: dict[str, Any] | None = None) -> Fitted:
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    vals = pd.Series(y.to_numpy(dtype=float))
    n = len(vals)
    if n < 6:
        raise ForecastError("ETS needs at least 6 observations")
    configs: list[dict[str, Any]] = [{"trend": None, "damped_trend": False, "seasonal": None}]
    if n >= 10:
        configs.append({"trend": "add", "damped_trend": True, "seasonal": None})
    if m and n >= 2 * m + 2:
        configs.append({"trend": None, "damped_trend": False, "seasonal": "add"})
        if n >= 2 * m + 6:
            configs.append({"trend": "add", "damped_trend": True, "seasonal": "add"})
    if fixed is not None:
        configs = [c for c in configs if c == fixed.get("_config")] or configs[:1]
    best = None
    for cfg in configs:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                res = ETSModel(vals, error="add", seasonal_periods=m if cfg["seasonal"] else None, **cfg).fit(
                    disp=False, maxiter=500
                )
            if not math.isfinite(res.aic):
                continue
            if best is None or res.aic < best[1].aic:
                best = (cfg, res)
        except Exception:  # statsmodels raises many error types on degenerate data
            continue
    if best is None:
        raise ForecastError("ETS could not be fitted")
    cfg, res = best
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame = res.get_prediction(start=n, end=n + h - 1).summary_frame(alpha=1 - interval)
    mean = frame["mean"].to_numpy(dtype=float)
    return (
        mean,
        frame["pi_lower"].to_numpy(dtype=float),
        frame["pi_upper"].to_numpy(dtype=float),
        {
            "error": "add",
            **{k: v for k, v in cfg.items() if v},
            "seasonal_periods": m if cfg["seasonal"] else None,
            "_config": cfg,
        },
        float(res.aic),
    )


def _arima(
    y: pd.Series, h: int, interval: float, m: int | None, fixed: dict[str, Any] | None = None
) -> Fitted:
    from statsmodels.tsa.arima.model import ARIMA

    vals = pd.Series(y.to_numpy(dtype=float))
    n = len(vals)
    if n < 8:
        raise ForecastError("ARIMA needs at least 8 observations")
    orders = [(0, 1, 1), (1, 1, 0), (1, 1, 1), (0, 1, 0), (1, 0, 0), (2, 1, 1)]
    seasonal_orders: list[tuple[int, int, int, int]] = [(0, 0, 0, 0)]
    if m and n >= 3 * m:
        seasonal_orders += [(0, 1, 1, m), (1, 0, 0, m)]
    if fixed is not None:
        orders = [tuple(fixed["order"])]  # type: ignore[list-item]
        seasonal_orders = [tuple(fixed["seasonal_order"]) if fixed.get("seasonal_order") else (0, 0, 0, 0)]  # type: ignore[list-item]
    best = None
    for order in orders:
        for so in seasonal_orders:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    res = ARIMA(vals, order=order, seasonal_order=so).fit()
                if not math.isfinite(res.aic):
                    continue
                if best is None or res.aic < best[2].aic:
                    best = (order, so, res)
            except Exception:  # degenerate fits are skipped
                continue
    if best is None:
        raise ForecastError("ARIMA could not be fitted")
    order, so, res = best
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame = res.get_forecast(h).summary_frame(alpha=1 - interval)
    return (
        frame["mean"].to_numpy(dtype=float),
        frame["mean_ci_lower"].to_numpy(dtype=float),
        frame["mean_ci_upper"].to_numpy(dtype=float),
        {"order": list(order), "seasonal_order": list(so) if so[3] else None},
        float(res.aic),
    )


_FITTERS: dict[str, Callable[..., Fitted]] = {
    "naive": _naive,
    "seasonal_naive": _seasonal_naive,
    "ets": _ets,
    "arima": _arima,
}


def _backtest(
    name: str,
    y: pd.Series,
    h: int,
    folds: int,
    interval: float,
    m: int | None,
    min_train: int,
    fixed: dict[str, Any] | None = None,
) -> BacktestResult | None:
    """Rolling-origin backtest re-fitting the model specification chosen on the full series."""
    n = len(y)
    results: list[BacktestFold] = []
    for k in range(folds, 0, -1):
        origin = n - k * h
        if origin < min_train:
            continue
        train, test = y.iloc[:origin], y.iloc[origin : origin + h]
        try:
            mean, lo, hi, _, _ = _FITTERS[name](train, len(test), interval, m, fixed)
        except ForecastError:
            continue
        actual = test.to_numpy(dtype=float)
        err = np.abs(actual - mean)
        nz = actual != 0
        mape = float(np.mean(err[nz] / np.abs(actual[nz]))) if nz.any() else None
        coverage = float(np.mean((actual >= lo) & (actual <= hi)))
        ts = train.index[-1].to_pydatetime()
        results.append(
            BacktestFold(
                origin=ts.date() if ts.time() == dt.time() else ts,
                horizon=len(test),
                mae=float(np.mean(err)),
                mape=mape,
                coverage=coverage,
            )
        )
    if not results:
        return None
    mapes = [f.mape for f in results if f.mape is not None]
    return BacktestResult(
        folds=results,
        mae=float(np.mean([f.mae for f in results])),
        mape=float(np.mean(mapes)) if mapes else None,
        coverage=float(np.mean([f.coverage for f in results])),
    )


def forecast(
    series: Any,
    horizon: int,
    *,
    models: Sequence[ModelName] = MODELS,
    seasonal_period: int | None | Literal["auto"] = "auto",
    interval: float = 0.8,
    backtest_folds: int = 3,
    backtest_horizon: int | None = None,
) -> ForecastResult:
    """Forecast ``horizon`` periods ahead with each model, backtested by rolling origin."""
    if horizon < 1:
        raise ForecastError("horizon must be at least 1")
    if not 0 < interval < 1:
        raise ForecastError("interval must be between 0 and 1 (e.g. 0.8 for an 80% interval)")
    s = to_series(series)
    notes: list[str] = []
    if s.isna().any():
        missing = int(s.isna().sum())
        s = s.interpolate(limit_direction="both")
        notes.append(f"{missing} missing values were linearly interpolated before fitting")
    s = s.dropna()
    if len(s) < 3:
        raise ForecastError("need at least 3 observations to forecast")
    freq = infer_frequency(pd.DatetimeIndex(s.index))
    m = default_season(freq, len(s)) if seasonal_period == "auto" else seasonal_period
    bt_h = backtest_horizon or min(horizon, max(1, len(s) // 5))
    seasonal_min = max(2 * m + 2, 8) if m else 8
    future = next_timestamps(s.index[-1], freq, horizon)
    out_models: list[ModelForecast] = []
    for name in models:
        if name not in _FITTERS:
            raise ForecastError(f"unknown model {name!r}; choose from {MODELS}")
        mf = ModelForecast(name=name)
        try:
            mean, lo, hi, params, aic = _FITTERS[name](s, horizon, interval, m)
            fixed = dict(params)
            params.pop("_config", None)
            mf.params, mf.aic = params, aic
            mf.points = [
                ForecastPoint(
                    timestamp=t.date() if t.to_pydatetime().time() == dt.time() else t.to_pydatetime(),
                    mean=float(a),
                    lower=float(b),
                    upper=float(c),
                )
                for t, a, b, c in zip(future, mean, lo, hi, strict=True)
            ]
            # Each model is backtested with its own minimum history: a seasonal specification
            # needs two full seasons, a non-seasonal one 8 points.
            seasonal = name == "seasonal_naive" or (name in ("ets", "arima") and bool(m))
            min_train = seasonal_min if seasonal else 8
            mf.backtest = _backtest(name, s, bt_h, backtest_folds, interval, m, min_train, fixed)
            if mf.backtest is None:
                mf.backtest_note = (
                    f"not backtested: needs {min_train} points before the first backtest origin "
                    + (f"(two seasons of {m})" if seasonal else "")
                    + f"; the series has {len(s)}"
                ).replace(" ;", ";")
        except ForecastError as exc:
            mf.error = str(exc)
        out_models.append(mf)
    scored = [mf for mf in out_models if mf.error is None and mf.backtest and mf.backtest.mae is not None]
    best = min(scored, key=lambda mf: (mf.backtest.mae, MODELS.index(mf.name))).name if scored else None  # type: ignore[union-attr]
    if best is None:
        notes.append("no model could be backtested (series too short); compare models with caution")
    elif len(scored) < len([mf for mf in out_models if mf.error is None]):
        notes.append(
            "best model chosen among the backtested models only: "
            + ", ".join(mf.name for mf in out_models if mf.error is None and mf.backtest is None)
            + " could not be backtested (see backtest_note)"
        )
    notes.append(
        f"{int(interval * 100)}% prediction intervals; backtest: {backtest_folds} rolling origins, {bt_h}-step horizon"
    )
    if freq == "irregular":
        notes.append("timestamps are irregular; forecast steps are shown one day apart")
    history = [SeriesPoint(timestamp=_ts(t), value=float(v)) for t, v in s.items()]
    return ForecastResult(
        history=history,
        horizon=horizon,
        frequency=freq,
        seasonal_period=m,
        interval=interval,
        models=out_models,
        best_model=best,
        notes=notes,
    )


def _ts(t: Any) -> dt.date | dt.datetime:
    py = pd.Timestamp(t).to_pydatetime()
    return py.date() if py.time() == dt.time() else py


def forecast_metric(
    store: WorkspaceStore,
    model: SemanticModel,
    metric: str,
    window: TimeWindow,
    grain: str,
    horizon: int,
    filters: list[Filter] | None = None,
    **kwargs: Any,
) -> ForecastResult:
    """Query the metric history per ``grain`` over ``window`` and forecast ``horizon`` periods."""
    s, compiled = metric_series(store, model, metric, window, grain, filters)
    lead, trail = s.attrs.get("trimmed", (0, 0))
    res = forecast(s, horizon, **kwargs)
    notes = list(res.notes)
    if lead or trail:
        notes.insert(
            0,
            f"{lead} leading and {trail} trailing {grain} periods of the window have no source rows and were left "
            "out (unknown, not zero)",
        )
    return res.model_copy(
        update={
            "sql": compiled.sql,
            "metric_id": metric,
            "metric_versions": compiled.metric_versions,
            "notes": notes,
        }
    )
