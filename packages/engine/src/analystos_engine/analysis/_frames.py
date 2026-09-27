"""Convert analysis inputs (DataFrame, QueryResult, records) into pandas DataFrames."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..types import QueryResult


def to_frame(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, QueryResult):
        return data.to_pandas()
    mod = type(data).__module__
    if mod.startswith("polars"):
        return data.to_pandas()
    if isinstance(data, list):
        return pd.DataFrame(data)
    if isinstance(data, dict):
        return pd.DataFrame(data)
    raise ValueError(f"cannot use {type(data).__name__} as tabular input")


def require_columns(df: pd.DataFrame, cols: list[str]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}; available: {list(df.columns)}")
