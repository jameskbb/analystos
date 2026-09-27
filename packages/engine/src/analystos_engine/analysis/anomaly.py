"""Anomaly and change-point detection.

Point anomalies use a *trailing* robust z-score (no look-ahead): the expected value
is the median of the same season in previous cycles (seasonal baseline, e.g. the last
four same weekdays for daily data) or the rolling median when the series has no
seasonality; the scale is ``1.4826 * MAD`` of recent baseline residuals. A point is
anomalous when ``|value - expected| / scale`` exceeds the sensitivity threshold and the
deviation is also material (``min_relative_deviation`` of the expected value), so
ordinary noise is not labelled an anomaly.

Level shifts are found with binary segmentation on the mean (squared-error cost)
and a BIC-style penalty ``beta * 2 * sigma^2 * ln(n)``, with sigma estimated robustly
from first differences.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from ..semantic.models import Filter, SemanticModel
from ..store import WorkspaceStore
from ..types import TimeWindow
from .timeseries import default_season, infer_frequency, metric_series, to_series

__all__ = [
    "AnomalyPoint",
    "ChangePoint",
    "AnomalyResult",
    "detect_anomalies",
    "detect_change_points",
    "detect_metric_anomalies",
    "SENSITIVITY",
]

Sensitivity = Literal["low", "medium", "high"]
SENSITIVITY: dict[str, tuple[float, float]] = {
    # z threshold, change-point penalty multiplier
    "low": (4.0, 3.0),
    "medium": (3.0, 2.0),
    "high": (2.5, 1.0),
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnomalyPoint(_Model):
    timestamp: dt.date | dt.datetime
    value: float | None
    expected: float | None = None
    lower: float | None = None
    upper: float | None = None
    score: float | None = None
    is_anomaly: bool = False
    direction: Literal["up", "down"] | None = None


class ChangePoint(_Model):
    index: int
    timestamp: dt.date | dt.datetime
    mean_before: float
    mean_after: float
    magnitude: float
    pct_change: float | None
    cost_reduction: float


class AnomalyResult(_Model):
    points: list[AnomalyPoint] = Field(default_factory=list)
    anomalies: list[AnomalyPoint] = Field(default_factory=list)
    change_points: list[ChangePoint] = Field(default_factory=list)
    method: str
    sensitivity: str
    threshold: float
    seasonal_period: int | None = None
    window: int
    frequency: str
    notes: list[str] = Field(default_factory=list)
    sql: str | None = None
    metric_id: str | None = None
    metric_versions: dict[str, str] = Field(default_factory=dict)


def _thresholds(sensitivity: Sensitivity | float) -> tuple[float, float, str]:
    if isinstance(sensitivity, int | float) and not isinstance(sensitivity, bool):
        z = float(sensitivity)
        if z <= 0:
            raise ValueError("sensitivity threshold must be positive")
        return z, max(0.5, z - 1.0), f"z>{z:g}"
    if sensitivity not in SENSITIVITY:
        raise ValueError(f"sensitivity must be one of {sorted(SENSITIVITY)} or a positive number")
    z, beta = SENSITIVITY[sensitivity]
    return z, beta, sensitivity


def _mad_scale(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return float("nan")
    med = np.median(x)
    mad = 1.4826 * float(np.median(np.abs(x - med)))
    if mad > 0:
        return mad
    sd = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    return sd


def _ts(t: pd.Timestamp) -> dt.date | dt.datetime:
    py = t.to_pydatetime()
    return py.date() if py.time() == dt.time() else py


def detect_anomalies(
    series: Any,
    *,
    sensitivity: Sensitivity | float = "medium",
    seasonal_period: int | None | Literal["auto"] = "auto",
    window: int | None = None,
    min_history: int | None = None,
    min_relative_deviation: float = 0.05,
    change_points: bool = True,
) -> AnomalyResult:
    """Flag anomalous points (robust z vs a trailing seasonal baseline) and level shifts."""
    s = to_series(series)
    values = s.to_numpy(dtype=float)
    n = len(values)
    freq = infer_frequency(pd.DatetimeIndex(s.index))
    z_thr, beta, label = _thresholds(sensitivity)
    m = default_season(freq, n) if seasonal_period == "auto" else seasonal_period
    if m is not None and (m < 2 or n < 2 * m):
        m = None
    w = window or {"day": 28, "week": 13, "month": 12, "quarter": 8}.get(freq, 14)
    k_seasons = 4
    min_hist = min_history if min_history is not None else (2 * m if m else max(5, min(w, 8)))
    notes: list[str] = []
    method = f"seasonal median baseline (period {m})" if m else f"rolling median baseline ({w} points)"
    notes.append(f"{method}; robust z-score with MAD scale; threshold {z_thr:g}; no look-ahead")

    expected = np.full(n, np.nan)
    for t in range(n):
        if m:
            lags = [
                values[t - k * m]
                for k in range(1, k_seasons + 1)
                if t - k * m >= 0 and not math.isnan(values[t - k * m])
            ]
            if len(lags) >= 2:
                expected[t] = float(np.median(lags))
        else:
            hist = values[max(0, t - w) : t]
            hist = hist[~np.isnan(hist)]
            if len(hist) >= 3:
                expected[t] = float(np.median(hist))
    resid = values - expected
    points: list[AnomalyPoint] = []
    for t in range(n):
        v = values[t]
        pt = AnomalyPoint(timestamp=_ts(s.index[t]), value=None if math.isnan(v) else float(v))
        if t >= min_hist and not math.isnan(expected[t]) and not math.isnan(v):
            past = resid[max(0, t - w) : t]
            scale = _mad_scale(past)
            if not math.isnan(scale):
                floor = max(1e-9, 0.01 * abs(expected[t]))
                scale = max(scale, floor)
                z = (v - expected[t]) / scale
                pt.expected = float(expected[t])
                pt.lower = float(expected[t] - z_thr * scale)
                pt.upper = float(expected[t] + z_thr * scale)
                pt.score = float(z)
                material = abs(v - expected[t]) >= min_relative_deviation * abs(expected[t])
                if abs(z) > z_thr and material:
                    pt.is_anomaly = True
                    pt.direction = "up" if z > 0 else "down"
        points.append(pt)
    anomalies = [p for p in points if p.is_anomaly]
    if n < min_hist + 1:
        notes.append(f"series too short for anomaly scoring (need more than {min_hist} points)")
    cps = detect_change_points(s, sensitivity=sensitivity) if change_points else []
    return AnomalyResult(
        points=points,
        anomalies=anomalies,
        change_points=cps,
        method=method,
        sensitivity=label,
        threshold=z_thr,
        seasonal_period=m,
        window=w,
        frequency=freq,
        notes=notes,
    )


def detect_change_points(
    series: Any,
    *,
    sensitivity: Sensitivity | float = "medium",
    min_size: int | None = None,
    max_change_points: int = 10,
) -> list[ChangePoint]:
    """Mean-shift change points via binary segmentation with a BIC-style penalty."""
    s = to_series(series).dropna()
    y = s.to_numpy(dtype=float)
    n = len(y)
    _, beta, _ = _thresholds(sensitivity)
    min_seg = min_size or max(3, n // 20)
    if n < 2 * min_seg + 1:
        return []
    diffs = np.diff(y)
    sigma = 1.4826 * float(np.median(np.abs(diffs - np.median(diffs)))) / math.sqrt(2)
    if sigma <= 0:
        sigma = float(np.std(diffs, ddof=1)) / math.sqrt(2) if n > 2 else 0.0
    if sigma <= 0:
        sigma = 1e-9 + 1e-6 * float(np.mean(np.abs(y)))
    penalty = beta * 2.0 * sigma**2 * math.log(n)
    csum = np.concatenate([[0.0], np.cumsum(y)])
    csum2 = np.concatenate([[0.0], np.cumsum(y * y)])

    def cost(a: int, b: int) -> float:
        k = b - a
        if k <= 0:
            return 0.0
        s1 = csum[b] - csum[a]
        return float(csum2[b] - csum2[a] - s1 * s1 / k)

    found: list[tuple[int, float]] = []
    stack = [(0, n)]
    while stack and len(found) < max_change_points:
        a, b = stack.pop()
        if b - a < 2 * min_seg:
            continue
        base = cost(a, b)
        best_i, best_gain = -1, 0.0
        for i in range(a + min_seg, b - min_seg + 1):
            gain = base - cost(a, i) - cost(i, b)
            if gain > best_gain:
                best_i, best_gain = i, gain
        if best_i > 0 and best_gain > penalty:
            found.append((best_i, best_gain))
            stack.append((a, best_i))
            stack.append((best_i, b))
    found.sort()
    out: list[ChangePoint] = []
    bounds = [0] + [i for i, _ in found] + [n]
    for j, (i, gain) in enumerate(found):
        before = y[bounds[j] : i]
        after = y[i : bounds[j + 2]]
        mb, ma = float(np.mean(before)), float(np.mean(after))
        out.append(
            ChangePoint(
                index=i,
                timestamp=_ts(s.index[i]),
                mean_before=mb,
                mean_after=ma,
                magnitude=ma - mb,
                pct_change=(ma - mb) / abs(mb) if mb else None,
                cost_reduction=gain,
            )
        )
    return out


def detect_metric_anomalies(
    store: WorkspaceStore,
    model: SemanticModel,
    metric: str,
    window: TimeWindow,
    grain: str = "day",
    filters: list[Filter] | None = None,
    **kwargs: Any,
) -> AnomalyResult:
    """Query ``metric`` per ``grain`` over ``window`` and detect anomalies in it."""
    s, compiled = metric_series(store, model, metric, window, grain, filters)
    res = detect_anomalies(s, **kwargs)
    return res.model_copy(
        update={"sql": compiled.sql, "metric_id": metric, "metric_versions": compiled.metric_versions}
    )
