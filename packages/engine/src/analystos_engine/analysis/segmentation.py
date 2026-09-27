"""Segmentation: rule-based first (RFM customers, product growth/profitability
quadrants), then optional k-means clustering labelled exploratory.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from ..store import WorkspaceStore, quote_ident
from ._frames import require_columns, to_frame

__all__ = [
    "RFMCustomer",
    "SegmentSummary",
    "RFMResult",
    "QuadrantRow",
    "QuadrantResult",
    "ClusterResult",
    "rfm_segments",
    "rfm_from_table",
    "product_quadrants",
    "kmeans_segments",
    "RFM_RULES",
]

# (segment, rule on the 1-5 recency score r and frequency score f); first match wins
_RFM_RULES: list[tuple[str, str, Callable[[int, int], bool]]] = [
    ("Champions", "r >= 4 and f >= 4", lambda r, f: r >= 4 and f >= 4),
    ("Can't lose them", "r == 1 and f >= 4", lambda r, f: r == 1 and f >= 4),
    ("At risk", "r <= 2 and f >= 3", lambda r, f: r <= 2 and f >= 3),
    ("Loyal", "r >= 3 and f >= 4", lambda r, f: r >= 3 and f >= 4),
    ("New", "r == 5 and f == 1", lambda r, f: r == 5 and f == 1),
    ("Promising", "r == 4 and f == 1", lambda r, f: r == 4 and f == 1),
    ("Potential loyalists", "r >= 4 and f >= 2", lambda r, f: r >= 4 and f >= 2),
    ("Need attention", "r == 3 and f == 3", lambda r, f: r == 3 and f == 3),
    ("About to sleep", "r == 3 and f <= 2", lambda r, f: r == 3 and f <= 2),
    ("Hibernating", "r == 2 and f <= 2", lambda r, f: r == 2 and f <= 2),
    ("Lost", "r == 1 and f <= 2", lambda r, f: r == 1 and f <= 2),
]
RFM_RULES: list[tuple[str, str]] = [(name, desc) for name, desc, _ in _RFM_RULES]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RFMCustomer(_Model):
    customer: Any
    recency_days: int
    frequency: float
    monetary: float
    r: int
    f: int
    m: int
    segment: str


class SegmentSummary(_Model):
    segment: str
    count: int
    share: float
    value: float
    value_share: float


class RFMResult(_Model):
    as_of: dt.date
    customers: list[RFMCustomer] = Field(default_factory=list)
    segments: list[SegmentSummary] = Field(default_factory=list)
    quantiles: int
    rules: list[tuple[str, str]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    sql: str | None = None


class QuadrantRow(_Model):
    item: Any
    growth: float | None
    profitability: float | None
    volume: float | None = None
    quadrant: str


class QuadrantResult(_Model):
    rows: list[QuadrantRow] = Field(default_factory=list)
    growth_threshold: float
    profitability_threshold: float
    segments: list[SegmentSummary] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ClusterResult(_Model):
    k: int
    labels: list[int]
    ids: list[Any]
    centroids: list[dict[str, float]]
    sizes: list[int]
    silhouette: float | None
    features: list[str]
    exploratory: bool = True
    notes: list[str] = Field(default_factory=list)


def _score(series: pd.Series, q: int, *, ascending: bool = True) -> pd.Series:
    """Quantile scores 1..q using ranks so ties and small samples are handled."""
    ranks = series.rank(method="first", ascending=ascending)
    bins = min(q, max(1, series.notna().sum()))
    scores = pd.qcut(ranks, q=bins, labels=False, duplicates="drop") + 1
    if bins < q:
        scores = np.ceil(scores * q / bins)
    return scores.astype(int)


def _summaries(df: pd.DataFrame, seg_col: str, value_col: str) -> list[SegmentSummary]:
    total_n = len(df)
    total_v = float(df[value_col].sum()) if value_col in df else 0.0
    out = []
    for seg, g in df.groupby(seg_col, sort=False):
        v = float(g[value_col].sum()) if value_col in g else 0.0
        out.append(
            SegmentSummary(
                segment=str(seg),
                count=len(g),
                share=len(g) / total_n if total_n else 0.0,
                value=v,
                value_share=v / total_v if total_v else 0.0,
            )
        )
    return sorted(out, key=lambda s: -s.value)


def rfm_segments(
    data: Any,
    *,
    id_col: str,
    last_date_col: str,
    frequency_col: str,
    monetary_col: str,
    as_of: dt.date,
    quantiles: int = 5,
) -> RFMResult:
    """Score customers 1..``quantiles`` on recency, frequency and monetary value and name segments."""
    df = to_frame(data)
    require_columns(df, [id_col, last_date_col, frequency_col, monetary_col])
    df = df.dropna(subset=[id_col, last_date_col])
    if df.empty:
        return RFMResult(as_of=as_of, quantiles=quantiles, rules=RFM_RULES, notes=["no customers"])
    last = pd.to_datetime(df[last_date_col]).dt.date
    df = df.assign(
        _recency=[(as_of - d).days for d in last],
        _freq=pd.to_numeric(df[frequency_col], errors="coerce").fillna(0.0),
        _mon=pd.to_numeric(df[monetary_col], errors="coerce").fillna(0.0),
    )
    df["_r"] = _score(df["_recency"], quantiles, ascending=False)  # recent = high score
    df["_f"] = _score(df["_freq"], quantiles)
    df["_m"] = _score(df["_mon"], quantiles)
    q = quantiles

    def label(r: int, f: int) -> str:
        # rescale to a 1..5 scale when fewer quantiles are used
        r5 = int(np.ceil(r * 5 / q))
        f5 = int(np.ceil(f * 5 / q))
        for name, _desc, rule in _RFM_RULES:
            if rule(r5, f5):
                return name
        return "Other"

    df["_seg"] = [label(r, f) for r, f in zip(df["_r"], df["_f"], strict=True)]
    customers = [
        RFMCustomer(
            customer=row[id_col],
            recency_days=int(row["_recency"]),
            frequency=float(row["_freq"]),
            monetary=float(row["_mon"]),
            r=int(row["_r"]),
            f=int(row["_f"]),
            m=int(row["_m"]),
            segment=row["_seg"],
        )
        for _, row in df.iterrows()
    ]
    notes = [
        f"scores are {quantiles}-quantiles by rank (recency: most recent = {quantiles}); segments follow fixed rules on R and F"
    ]
    if (df["_recency"] < 0).any():
        notes.append("some last-order dates are after the as-of date")
    return RFMResult(
        as_of=as_of,
        customers=customers,
        segments=_summaries(df.rename(columns={"_seg": "segment"}), "segment", "_mon"),
        quantiles=quantiles,
        rules=RFM_RULES,
        notes=notes,
    )


def rfm_from_table(
    store: WorkspaceStore,
    table: str,
    *,
    customer_col: str,
    date_col: str,
    amount_col: str,
    as_of: dt.date,
    order_col: str | None = None,
    quantiles: int = 5,
) -> RFMResult:
    """Compute RFM inputs with read-only SQL over a transactions table, then segment."""
    t, c, d, a = (quote_ident(x) for x in (table, customer_col, date_col, amount_col))
    freq = f"count(DISTINCT {quote_ident(order_col)})" if order_col else "count(*)"
    sql = (
        f"SELECT {c} AS customer, max(CAST({d} AS DATE)) AS last_date, {freq} AS frequency, sum({a}) AS monetary "
        f"FROM {t} WHERE {c} IS NOT NULL AND CAST({d} AS DATE) <= DATE '{as_of.isoformat()}' GROUP BY 1 ORDER BY 1"
    )
    res = store.execute_read(sql, limit=None)
    out = rfm_segments(
        res,
        id_col="customer",
        last_date_col="last_date",
        frequency_col="frequency",
        monetary_col="monetary",
        as_of=as_of,
        quantiles=quantiles,
    )
    return out.model_copy(update={"sql": res.sql})


def product_quadrants(
    data: Any,
    *,
    id_col: str,
    growth_col: str,
    profitability_col: str,
    volume_col: str | None = None,
    growth_threshold: float | None = None,
    profitability_threshold: float | None = None,
) -> QuadrantResult:
    """Classify items by growth and profitability (thresholds default to the medians)."""
    df = to_frame(data)
    require_columns(df, [id_col, growth_col, profitability_col] + ([volume_col] if volume_col else []))
    g = pd.to_numeric(df[growth_col], errors="coerce")
    p = pd.to_numeric(df[profitability_col], errors="coerce")
    gt = float(g.median()) if growth_threshold is None else growth_threshold
    pt = float(p.median()) if profitability_threshold is None else profitability_threshold
    names = {
        (True, True): "Stars (growing, profitable)",
        (False, True): "Cash cows (flat or declining, profitable)",
        (True, False): "Question marks (growing, low margin)",
        (False, False): "Laggards (declining, low margin)",
    }
    rows = []
    for i in range(len(df)):
        gv, pv = g.iloc[i], p.iloc[i]
        missing = pd.isna(gv) or pd.isna(pv)
        quad = "Insufficient data" if missing else names[(bool(gv >= gt), bool(pv >= pt))]
        rows.append(
            QuadrantRow(
                item=df[id_col].iloc[i],
                growth=None if pd.isna(gv) else float(gv),
                profitability=None if pd.isna(pv) else float(pv),
                volume=None
                if not volume_col or pd.isna(df[volume_col].iloc[i])
                else float(df[volume_col].iloc[i]),
                quadrant=quad,
            )
        )
    frame = pd.DataFrame([r.model_dump() for r in rows])
    value = "volume" if volume_col else "growth"
    if not volume_col:
        frame["volume"] = 1.0
        value = "volume"
    return QuadrantResult(
        rows=rows,
        growth_threshold=gt,
        profitability_threshold=pt,
        segments=_summaries(frame, "quadrant", value),
        notes=[
            f"thresholds: growth {gt:.4g}, profitability {pt:.4g} ({'medians' if growth_threshold is None else 'given'})"
        ],
    )


def kmeans_segments(
    data: Any,
    feature_cols: list[str],
    *,
    id_col: str | None = None,
    k: int | None = None,
    k_range: tuple[int, int] = (2, 6),
    standardize: bool = True,
    seed: int = 0,
) -> ClusterResult:
    """K-means clustering (exploratory). ``k`` is chosen by silhouette when not given."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    df = to_frame(data)
    require_columns(df, feature_cols + ([id_col] if id_col else []))
    X = df[feature_cols].apply(pd.to_numeric, errors="coerce")
    mask = X.notna().all(axis=1)
    X = X[mask]
    ids = df.loc[mask, id_col].tolist() if id_col else list(X.index)
    n = len(X)
    if n < 3:
        raise ValueError("need at least 3 complete rows to cluster")
    Z = StandardScaler().fit_transform(X) if standardize else X.to_numpy(dtype=float)
    notes = ["exploratory: clusters describe structure in the chosen features, not causes"]
    if (~mask).sum():
        notes.append(f"{int((~mask).sum())} rows with missing features were excluded")
    candidates = [k] if k else list(range(k_range[0], min(k_range[1], n - 1) + 1))
    best: tuple[float, int, Any] | None = None
    for kk in candidates:
        if kk < 2 or kk >= n:
            continue
        km = KMeans(n_clusters=kk, n_init=10, random_state=seed).fit(Z)
        sil = float(silhouette_score(Z, km.labels_)) if len(set(km.labels_)) > 1 else -1.0
        if best is None or sil > best[0]:
            best = (sil, kk, km)
    if best is None:
        raise ValueError("no valid number of clusters for this data")
    sil, kk, km = best
    labels = [int(x) for x in km.labels_]
    centroids = []
    for c in range(kk):
        members = X[np.array(labels) == c]
        centroids.append({f: float(members[f].mean()) for f in feature_cols})
    if not k:
        notes.append(f"k={kk} chosen by silhouette score among {candidates}")
    return ClusterResult(
        k=kk,
        labels=labels,
        ids=ids,
        centroids=centroids,
        sizes=[labels.count(c) for c in range(kk)],
        silhouette=sil,
        features=feature_cols,
        notes=notes,
    )
