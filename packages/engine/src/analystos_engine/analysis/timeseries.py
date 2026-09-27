"""Shared time-series helpers: series normalisation, frequency inference and metric series."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from ..calendar import add_months, periods_between
from ..semantic.compiler import CompiledQuery, MetricQuery, run_metric_query
from ..semantic.models import Filter, SemanticModel
from ..store import WorkspaceStore
from ..types import TimeWindow

__all__ = [
    "SeriesPoint",
    "to_series",
    "infer_frequency",
    "next_timestamps",
    "default_season",
    "metric_series",
    "Frequency",
]

Frequency = Literal["day", "week", "month", "quarter", "year", "irregular"]


class SeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: dt.date | dt.datetime
    value: float | None


def to_series(data: Any) -> pd.Series:
    """Normalise input to a float Series indexed by sorted timestamps.

    Accepts a pandas Series (datetime-like index), a DataFrame with two columns
    (timestamp, value), a mapping ``{timestamp: value}``, an iterable of
    ``(timestamp, value)`` pairs or of :class:`SeriesPoint`.
    """
    if isinstance(data, pd.Series):
        s = data.copy()
    elif isinstance(data, pd.DataFrame):
        if data.shape[1] < 2:
            raise ValueError("a DataFrame series needs a timestamp column and a value column")
        s = pd.Series(data.iloc[:, 1].values, index=data.iloc[:, 0].values)
    elif isinstance(data, Mapping):
        s = pd.Series(list(data.values()), index=list(data.keys()))
    elif isinstance(data, Iterable):
        pairs = [(p.timestamp, p.value) if isinstance(p, SeriesPoint) else (p[0], p[1]) for p in data]
        s = pd.Series([v for _, v in pairs], index=[t for t, _ in pairs])
    else:
        raise ValueError(f"cannot build a time series from {type(data).__name__}")
    s.index = pd.to_datetime(s.index)
    s = pd.to_numeric(s, errors="coerce").astype(float)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def infer_frequency(index: pd.DatetimeIndex) -> Frequency:
    if len(index) < 2:
        return "irregular"
    diffs = pd.Series(index[1:] - index[:-1]).dt.days
    med = float(diffs.median())
    if med == 1:
        return "day"
    if med == 7:
        return "week"
    if 28 <= med <= 31:
        return "month"
    if 89 <= med <= 92:
        return "quarter"
    if 365 <= med <= 366:
        return "year"
    return "irregular"


def default_season(freq: Frequency, n: int) -> int | None:
    m = {"day": 7, "week": 52, "month": 12, "quarter": 4}.get(freq)
    if m is None or n < 2 * m:
        return None
    return m


def next_timestamps(last: pd.Timestamp, freq: Frequency, horizon: int) -> list[pd.Timestamp]:
    out = []
    cur = last
    for _ in range(horizon):
        if freq == "day":
            cur = cur + pd.Timedelta(days=1)
        elif freq == "week":
            cur = cur + pd.Timedelta(days=7)
        elif freq in ("month", "quarter", "year"):
            step = {"month": 1, "quarter": 3, "year": 12}[freq]
            cur = pd.Timestamp(add_months(cur.date(), step))
        else:
            cur = cur + pd.Timedelta(days=1)
        out.append(cur)
    return out


def metric_series(
    store: WorkspaceStore,
    model: SemanticModel,
    metric: str,
    window: TimeWindow,
    grain: str = "day",
    filters: list[Filter] | None = None,
    *,
    fill_missing: bool | None = None,
) -> tuple[pd.Series, CompiledQuery]:
    """``metric`` per ``grain`` period over ``window`` as a Series (one compiled query).

    Missing periods are filled with 0 for additive metrics (no rows means nothing
    happened) and left as NaN otherwise, unless ``fill_missing`` says otherwise. Only gaps
    *between* periods that have source rows are filled: periods before the first and after
    the last period with data are dropped (the data does not cover them, so they are unknown,
    not zero). ``series.attrs["trimmed"] = (leading, trailing)`` counts the dropped periods.
    """
    from .contribution import metric_additivity

    w = window.model_copy(update={"grain": grain})
    compiled, res = run_metric_query(
        store, model, MetricQuery(metrics=[metric], filters=filters or [], time=w), limit=None
    )
    period_col = compiled.grain[0]
    values = {
        pd.Timestamp(r[period_col]): (None if r[metric] is None else float(r[metric]))
        for r in res.to_records()
    }
    periods = [pd.Timestamp(p) for p in periods_between(window.start, window.end, grain, model.calendar)]
    fill = metric_additivity(model, metric) == "additive" if fill_missing is None else fill_missing
    # Data coverage: the first and last period with any row for this metric (whole history,
    # not just the window). Periods outside it are unknown; periods inside it without rows are
    # real zeros for additive metrics.
    _c, span = run_metric_query(
        store,
        model,
        MetricQuery(metrics=[metric], filters=filters or [], dimensions=[period_col]),
        limit=None,
    )
    covered = sorted(pd.Timestamp(r[period_col]) for r in span.to_records() if r[period_col] is not None)
    inside = [i for i, p in enumerate(periods) if covered and covered[0] <= p <= covered[-1]]
    lead = inside[0] if inside else len(periods)
    trail = (len(periods) - 1 - inside[-1]) if inside else 0
    kept = periods[lead : len(periods) - trail] if inside else []
    data = [values.get(p, 0.0 if fill else None) for p in kept]
    series = pd.Series(data, index=pd.DatetimeIndex(kept), dtype=float)
    series.attrs["trimmed"] = (lead, trail)
    return series, compiled
