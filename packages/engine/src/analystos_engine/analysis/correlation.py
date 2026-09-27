"""Exploratory driver analysis: correlation, OLS regression and permutation importance.

Every result carries ``exploratory=True`` and a caveat: associations in observational
data are not causal effects.
"""

from __future__ import annotations

import math
import warnings as warnings_mod
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats as sps

from ._frames import require_columns, to_frame

__all__ = [
    "CAVEAT",
    "CorrelationPair",
    "CorrelationResult",
    "Coefficient",
    "RegressionResult",
    "FeatureImportance",
    "ImportanceResult",
    "correlation_matrix",
    "ols_regression",
    "permutation_importance",
]

CAVEAT = (
    "Exploratory: correlation and regression describe associations in this data and do not show that one "
    "variable causes another; confounders, reverse causality and selection can all produce the same pattern."
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorrelationPair(_Model):
    a: str
    b: str
    r: float | None
    p_value: float | None
    n: int
    strength: Literal["none", "weak", "moderate", "strong"] | None


class CorrelationResult(_Model):
    method: Literal["pearson", "spearman"]
    columns: list[str]
    matrix: list[list[float | None]]
    pairs: list[CorrelationPair] = Field(default_factory=list)
    exploratory: bool = True
    caveat: str = CAVEAT


class Coefficient(_Model):
    name: str
    coef: float
    std_err: float
    t: float
    p_value: float
    ci_low: float
    ci_high: float


class RegressionResult(_Model):
    target: str
    features: list[str]
    coefficients: list[Coefficient]
    r_squared: float
    adj_r_squared: float
    f_pvalue: float | None
    n: int
    vif: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    exploratory: bool = True
    caveat: str = CAVEAT


class FeatureImportance(_Model):
    name: str
    importance_mean: float
    importance_std: float


class ImportanceResult(_Model):
    target: str
    model: str
    task: Literal["regression", "classification"]
    features: list[FeatureImportance]
    score_name: str
    holdout_score: float
    n: int
    exploratory: bool = True
    caveat: str = CAVEAT
    notes: list[str] = Field(default_factory=list)


def _strength(r: float | None) -> Literal["none", "weak", "moderate", "strong"] | None:
    if r is None:
        return None
    a = abs(r)
    if a < 0.1:
        return "none"
    if a < 0.3:
        return "weak"
    if a < 0.6:
        return "moderate"
    return "strong"


def correlation_matrix(
    data: Any, columns: list[str] | None = None, *, method: Literal["pearson", "spearman"] = "pearson"
) -> CorrelationResult:
    df = to_frame(data)
    cols = columns or [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    require_columns(df, cols)
    num = df[cols].apply(pd.to_numeric, errors="coerce")
    k = len(cols)
    matrix: list[list[float | None]] = [[None] * k for _ in range(k)]
    pairs: list[CorrelationPair] = []
    for i in range(k):
        matrix[i][i] = 1.0
        for j in range(i + 1, k):
            pair = num[[cols[i], cols[j]]].dropna()
            n = len(pair)
            r = p = None
            if n >= 3 and pair.iloc[:, 0].nunique() > 1 and pair.iloc[:, 1].nunique() > 1:
                fn = sps.pearsonr if method == "pearson" else sps.spearmanr
                res = fn(pair.iloc[:, 0], pair.iloc[:, 1])
                r, p = float(res.statistic), float(res.pvalue)
            matrix[i][j] = matrix[j][i] = r
            pairs.append(CorrelationPair(a=cols[i], b=cols[j], r=r, p_value=p, n=n, strength=_strength(r)))
    pairs.sort(key=lambda x: -(abs(x.r) if x.r is not None else -1))
    return CorrelationResult(method=method, columns=cols, matrix=matrix, pairs=pairs)


def ols_regression(
    data: Any, target: str, features: list[str], *, confidence: float = 0.95
) -> RegressionResult:
    import statsmodels.api as sm
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    df = to_frame(data)
    require_columns(df, [target, *features])
    frame = df[[target, *features]].apply(pd.to_numeric, errors="coerce").dropna()
    n = len(frame)
    if n <= len(features) + 1:
        raise ValueError(f"need more rows ({n}) than parameters ({len(features) + 1})")
    X = sm.add_constant(frame[features], has_constant="add")
    res = sm.OLS(frame[target], X).fit()
    ci = res.conf_int(alpha=1 - confidence)
    coefs = [
        Coefficient(
            name=str(name),
            coef=float(res.params[name]),
            std_err=float(res.bse[name]),
            t=float(res.tvalues[name]),
            p_value=float(res.pvalues[name]),
            ci_low=float(ci.loc[name, 0]),
            ci_high=float(ci.loc[name, 1]),
        )
        for name in X.columns
    ]
    warnings: list[str] = []
    vif: dict[str, float] = {}
    if len(features) > 1:
        for i, f in enumerate(features, start=1):
            try:
                with warnings_mod.catch_warnings():
                    warnings_mod.simplefilter("ignore")  # ill-conditioning is reported through the VIF itself
                    v = float(variance_inflation_factor(X.to_numpy(dtype=float), i))
            except (ValueError, ZeroDivisionError, np.linalg.LinAlgError):
                v = float("inf")
            vif[f] = v if math.isfinite(v) else 1e9
            if vif[f] > 10:
                warnings.append(
                    f"{f} is highly collinear with other features (VIF {vif[f]:.3g}); its coefficient is unstable"
                )
    if n < 10 * (len(features) + 1):
        warnings.append(f"only {n} rows for {len(features)} features; estimates are imprecise")
    jb = sps.jarque_bera(res.resid)
    if float(jb.pvalue) < 0.01:
        warnings.append(
            "residuals are not normally distributed (Jarque-Bera p<0.01); p-values are approximate"
        )
    return RegressionResult(
        target=target,
        features=features,
        coefficients=coefs,
        r_squared=float(res.rsquared),
        adj_r_squared=float(res.rsquared_adj),
        f_pvalue=float(res.f_pvalue)
        if res.f_pvalue is not None and math.isfinite(float(res.f_pvalue))
        else None,
        n=n,
        vif=vif,
        warnings=warnings,
    )


def permutation_importance(
    data: Any,
    target: str,
    features: list[str],
    *,
    task: Literal["auto", "regression", "classification"] = "auto",
    n_repeats: int = 10,
    seed: int = 0,
) -> ImportanceResult:
    """Permutation importance of a random forest on a held-out split (exploratory)."""
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.inspection import permutation_importance as sk_perm
    from sklearn.model_selection import train_test_split

    df = to_frame(data)
    require_columns(df, [target, *features])
    frame = df[[target, *features]].dropna()
    X = pd.get_dummies(frame[features], drop_first=False)
    y = frame[target]
    if len(frame) < 20:
        raise ValueError("need at least 20 complete rows for permutation importance")
    if task == "auto":
        task = (
            "classification" if (not pd.api.types.is_numeric_dtype(y) or y.nunique() <= 10) else "regression"
        )
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=seed)
    if task == "classification":
        model = RandomForestClassifier(n_estimators=200, random_state=seed)
        score_name = "accuracy"
    else:
        model = RandomForestRegressor(n_estimators=200, random_state=seed)
        score_name = "R^2"
    model.fit(Xtr, ytr)
    score = float(model.score(Xte, yte))
    imp = sk_perm(model, Xte, yte, n_repeats=n_repeats, random_state=seed)
    by_feature: dict[str, list[float]] = {}
    for col, mean, std in zip(X.columns, imp.importances_mean, imp.importances_std, strict=True):
        base = next((f for f in features if col == f or col.startswith(f"{f}_")), col)
        by_feature.setdefault(base, [0.0, 0.0])
        by_feature[base][0] += float(mean)
        by_feature[base][1] = math.sqrt(by_feature[base][1] ** 2 + float(std) ** 2)
    feats = sorted(
        (FeatureImportance(name=k, importance_mean=v[0], importance_std=v[1]) for k, v in by_feature.items()),
        key=lambda f: -f.importance_mean,
    )
    notes = [
        f"random forest ({task}); importance = drop in held-out {score_name} when the feature is shuffled"
    ]
    if score < 0.1:
        notes.append(
            f"the model explains little (held-out {score_name} {score:.2f}); importances are not meaningful"
        )
    return ImportanceResult(
        target=target,
        model="random_forest",
        task=task,
        features=feats,
        score_name=score_name,
        holdout_score=score,
        n=len(frame),
        notes=notes,
    )
