"""Statistical tests and confidence intervals with explicit assumption checks.

Tests are tools, not verdicts: every result lists its assumptions, whether they appear
to hold, and a plain-language interpretation that avoids over-claiming.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats as sps

__all__ = [
    "AssumptionCheck",
    "TestResult",
    "ConfidenceInterval",
    "t_test",
    "chi_square",
    "proportion_z_test",
    "mean_ci",
    "proportion_ci",
    "bootstrap_ci",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssumptionCheck(_Model):
    name: str
    passed: bool | None
    detail: str


class TestResult(_Model):
    test: str
    statistic: float | None
    p_value: float | None
    df: float | None = None
    alpha: float = 0.05
    significant: bool | None = None
    effect_size: float | None = None
    effect_size_name: str | None = None
    estimate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    confidence: float = 0.95
    n: dict[str, int] = Field(default_factory=dict)
    assumptions: list[AssumptionCheck] = Field(default_factory=list)
    interpretation: str = ""
    caveats: list[str] = Field(default_factory=list)


class ConfidenceInterval(_Model):
    estimate: float
    low: float
    high: float
    confidence: float
    method: str
    n: int


def _clean(values: Sequence[Any]) -> np.ndarray:
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    return arr[~np.isnan(arr)]


def _normality(x: np.ndarray, label: str) -> AssumptionCheck:
    n = len(x)
    if n < 3:
        return AssumptionCheck(
            name=f"normality ({label})", passed=None, detail=f"n={n}: too few values to check"
        )
    if n > 5000 or n >= 30:
        return AssumptionCheck(
            name=f"normality ({label})",
            passed=True,
            detail=f"n={n}: the mean is approximately normal by the central limit theorem",
        )
    if np.ptp(x) == 0:
        return AssumptionCheck(name=f"normality ({label})", passed=None, detail="all values identical")
    p = float(sps.shapiro(x).pvalue)
    return AssumptionCheck(
        name=f"normality ({label})",
        passed=p >= 0.05,
        detail=f"Shapiro-Wilk p={p:.3g}"
        + ("" if p >= 0.05 else "; small non-normal sample, consider a bootstrap CI"),
    )


def t_test(
    a: Sequence[Any],
    b: Sequence[Any],
    *,
    equal_var: bool = False,
    alpha: float = 0.05,
    confidence: float = 0.95,
) -> TestResult:
    """Two-sample t-test (Welch by default) for a difference in means ``mean(a) - mean(b)``."""
    x, y = _clean(a), _clean(b)
    if len(x) < 2 or len(y) < 2:
        raise ValueError("each group needs at least two values")
    res = sps.ttest_ind(x, y, equal_var=equal_var)
    diff = float(np.mean(x) - np.mean(y))
    vx, vy = float(np.var(x, ddof=1)), float(np.var(y, ddof=1))
    nx, ny = len(x), len(y)
    if equal_var:
        df: float = nx + ny - 2
        sp2 = ((nx - 1) * vx + (ny - 1) * vy) / df
        se = math.sqrt(sp2 * (1 / nx + 1 / ny))
    else:
        se = math.sqrt(vx / nx + vy / ny)
        num = (vx / nx + vy / ny) ** 2
        den = (vx / nx) ** 2 / (nx - 1) + (vy / ny) ** 2 / (ny - 1)
        df = num / den if den > 0 else float(nx + ny - 2)
    tcrit = float(sps.t.ppf(0.5 + confidence / 2, df))
    pooled_sd = math.sqrt(((nx - 1) * vx + (ny - 1) * vy) / (nx + ny - 2))
    d = diff / pooled_sd if pooled_sd > 0 else None
    checks = [_normality(x, "a"), _normality(y, "b")]
    lev = sps.levene(x, y)
    ratio = max(vx, vy) / min(vx, vy) if min(vx, vy) > 0 else float("inf")
    checks.append(
        AssumptionCheck(
            name="equal variances",
            passed=bool(lev.pvalue >= 0.05),
            detail=f"Levene p={float(lev.pvalue):.3g}, variance ratio {ratio:.2g}"
            + ("; Welch's test does not assume equal variances" if not equal_var else ""),
        )
    )
    checks.append(
        AssumptionCheck(
            name="independent samples",
            passed=None,
            detail="cannot be checked from data; the two groups must not share units",
        )
    )
    p = float(res.pvalue)
    sig = p < alpha
    return TestResult(
        test="Welch two-sample t-test" if not equal_var else "Student two-sample t-test",
        statistic=float(res.statistic),
        p_value=p,
        df=float(df),
        alpha=alpha,
        significant=sig,
        effect_size=d,
        effect_size_name="Cohen's d",
        estimate=diff,
        ci_low=diff - tcrit * se,
        ci_high=diff + tcrit * se,
        confidence=confidence,
        n={"a": nx, "b": ny},
        assumptions=checks,
        interpretation=(
            f"The difference in means ({diff:.4g}) is {'statistically significant' if sig else 'not statistically significant'} "
            f"at alpha={alpha} (p={p:.3g}). "
            + (
                "A significant difference does not by itself show why the groups differ."
                if sig
                else "This does not prove the means are equal."
            )
        ),
        caveats=["statistical significance is not business significance; check the effect size and interval"],
    )


def chi_square(table: Sequence[Sequence[float]], *, alpha: float = 0.05) -> TestResult:
    """Chi-square test of independence on a contingency table (rows x columns of counts)."""
    obs = np.asarray(table, dtype=float)
    if obs.ndim != 2 or obs.shape[0] < 2 or obs.shape[1] < 2:
        raise ValueError("chi-square needs at least a 2x2 table of counts")
    if (obs < 0).any():
        raise ValueError("counts must be non-negative")
    res = sps.chi2_contingency(obs, correction=obs.shape == (2, 2))
    expected = np.asarray(res.expected_freq)
    low = int((expected < 5).sum())
    n = int(obs.sum())
    k = min(obs.shape) - 1
    cramers_v = math.sqrt(float(res.statistic) / (n * k)) if n and k else None
    checks = [
        AssumptionCheck(
            name="expected counts >= 5",
            passed=low == 0,
            detail=f"{low} of {expected.size} cells have expected count below 5"
            + ("; consider Fisher's exact test or merging categories" if low else ""),
        ),
        AssumptionCheck(
            name="independent observations",
            passed=None,
            detail="each unit must be counted in exactly one cell",
        ),
    ]
    p = float(res.pvalue)
    return TestResult(
        test="Chi-square test of independence",
        statistic=float(res.statistic),
        p_value=p,
        df=float(res.dof),
        alpha=alpha,
        significant=p < alpha,
        effect_size=cramers_v,
        effect_size_name="Cramér's V",
        n={"total": n},
        assumptions=checks,
        interpretation=(
            f"The association between rows and columns is {'statistically significant' if p < alpha else 'not statistically significant'} "
            f"(p={p:.3g}, Cramér's V={cramers_v:.3g})."
            if cramers_v is not None
            else f"p={p:.3g}"
        ),
        caveats=["association is not causation"],
    )


def proportion_z_test(
    successes_a: int, n_a: int, successes_b: int, n_b: int, *, alpha: float = 0.05, confidence: float = 0.95
) -> TestResult:
    """Two-proportion z-test for ``p_a - p_b`` (pooled standard error for the test, Wald CI)."""
    if n_a <= 0 or n_b <= 0:
        raise ValueError("sample sizes must be positive")
    if not (0 <= successes_a <= n_a and 0 <= successes_b <= n_b):
        raise ValueError("successes must be between 0 and n")
    pa, pb = successes_a / n_a, successes_b / n_b
    pooled = (successes_a + successes_b) / (n_a + n_b)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    z = (pa - pb) / se_pooled if se_pooled > 0 else 0.0
    p = float(2 * sps.norm.sf(abs(z))) if se_pooled > 0 else 1.0
    se = math.sqrt(pa * (1 - pa) / n_a + pb * (1 - pb) / n_b)
    zc = float(sps.norm.ppf(0.5 + confidence / 2))
    cond = min(successes_a, n_a - successes_a, successes_b, n_b - successes_b)
    checks = [
        AssumptionCheck(
            name="large-sample condition",
            passed=cond >= 10,
            detail=f"smallest of successes/failures per group is {cond} (needs at least 10 for the normal approximation)",
        ),
        AssumptionCheck(
            name="independent observations",
            passed=None,
            detail="each unit counted once, groups do not overlap",
        ),
    ]
    h = 2 * math.asin(math.sqrt(pa)) - 2 * math.asin(math.sqrt(pb))
    return TestResult(
        test="Two-proportion z-test",
        statistic=z,
        p_value=p,
        alpha=alpha,
        significant=p < alpha,
        effect_size=h,
        effect_size_name="Cohen's h",
        estimate=pa - pb,
        ci_low=pa - pb - zc * se,
        ci_high=pa - pb + zc * se,
        confidence=confidence,
        n={"a": n_a, "b": n_b},
        assumptions=checks,
        interpretation=(
            f"{pa:.2%} vs {pb:.2%}: the difference of {pa - pb:+.2%} is "
            f"{'statistically significant' if p < alpha else 'not statistically significant'} (p={p:.3g})."
        ),
    )


def mean_ci(values: Sequence[Any], *, confidence: float = 0.95) -> ConfidenceInterval:
    """t-based confidence interval for a mean."""
    x = _clean(values)
    n = len(x)
    if n < 2:
        raise ValueError("need at least two values")
    m = float(np.mean(x))
    se = float(np.std(x, ddof=1)) / math.sqrt(n)
    t = float(sps.t.ppf(0.5 + confidence / 2, n - 1))
    return ConfidenceInterval(
        estimate=m, low=m - t * se, high=m + t * se, confidence=confidence, method="t interval", n=n
    )


def proportion_ci(successes: int, n: int, *, confidence: float = 0.95) -> ConfidenceInterval:
    """Wilson score interval for a proportion (well behaved near 0 and 1)."""
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("need 0 <= successes <= n and n > 0")
    z = float(sps.norm.ppf(0.5 + confidence / 2))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return ConfidenceInterval(
        estimate=p,
        low=max(0.0, centre - half),
        high=min(1.0, centre + half),
        confidence=confidence,
        method="Wilson score",
        n=n,
    )


def bootstrap_ci(
    values: Sequence[Any],
    *,
    stat: Literal["mean", "median", "sum"] = "mean",
    n_boot: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
) -> ConfidenceInterval:
    """Percentile bootstrap interval (reproducible with ``seed``)."""
    x = _clean(values)
    n = len(x)
    if n < 2:
        raise ValueError("need at least two values")
    fn: Any = {"mean": np.mean, "median": np.median, "sum": np.sum}[stat]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    boots = fn(x[idx], axis=1)
    lo, hi = np.quantile(boots, [(1 - confidence) / 2, 1 - (1 - confidence) / 2])
    return ConfidenceInterval(
        estimate=float(fn(x)),
        low=float(lo),
        high=float(hi),
        confidence=confidence,
        method=f"percentile bootstrap ({n_boot} resamples, {stat})",
        n=n,
    )
