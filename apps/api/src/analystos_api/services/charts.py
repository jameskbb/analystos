"""Chart suggestion service: pick chart types from the *shape* of a result.

Rules (first match ranks highest; alternatives are also returned):

* one row, only measures                       -> KPI
* time column + measure(s)                     -> line (multi-series if a low-cardinality category is present,
                                                  stacked area as the alternative)
* category + measure with mixed-sign values    -> waterfall (bridge of changes) + bar
* category + measure                           -> bar, sorted descending (horizontal when labels are long or many)
* two categories + measure                     -> stacked bar (small second category) or heatmap
* two measures, no category                    -> scatter
* one measure, many rows                       -> histogram
* category + measure with many rows per group  -> box plot
* always                                       -> table

Pie charts are never suggested (spec section 34). Every suggestion includes an ECharts
``option`` using a ``dataset`` source so the web app can render it directly, and a
human-readable reason.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

_NUMERIC = re.compile(
    r"^(TINYINT|SMALLINT|INTEGER|INT|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|"
    r"DOUBLE|REAL|DECIMAL.*|NUMERIC.*|INT\d+|FLOAT\d+|UINT\d+|NUMBER.*)$",
    re.IGNORECASE,
)
_TEMPORAL = re.compile(r"^(DATE|TIMESTAMP.*|DATETIME.*|TIME)$", re.IGNORECASE)
_TIME_NAME = re.compile(r"(^|_)(date|day|week|month|quarter|year|period|time|ts)($|_)", re.IGNORECASE)
_ID_NAME = re.compile(r"(^id$|_id$|_key$|^key$)", re.IGNORECASE)

MAX_POINTS = 5000


@dataclass
class ColumnShape:
    name: str
    type: str
    role: str  # temporal | measure | category | identifier
    distinct: int
    has_negative: bool = False
    has_positive: bool = False
    max_label_len: int = 0


def _is_date_string(values: list[Any]) -> bool:
    sample = [v for v in values[:50] if v is not None]
    if not sample:
        return False
    ok = 0
    for v in sample:
        if (
            isinstance(v, dt.date | dt.datetime)
            or isinstance(v, str)
            and re.match(r"^\d{4}-\d{2}(-\d{2})?([ T].*)?$", v)
        ):
            ok += 1
    return ok == len(sample)


def classify(columns: list[dict[str, Any]], rows: list[list[Any]]) -> list[ColumnShape]:
    shapes: list[ColumnShape] = []
    for i, col in enumerate(columns):
        name, ctype = str(col.get("name")), str(col.get("type", ""))
        values = [r[i] for r in rows if i < len(r)]
        non_null = [v for v in values if v is not None]
        distinct = len({str(v) for v in non_null})
        numeric = bool(_NUMERIC.match(ctype.strip())) or (
            bool(non_null) and all(isinstance(v, int | float) and not isinstance(v, bool) for v in non_null)
        )
        if _TEMPORAL.match(ctype.strip()) or (
            _TIME_NAME.search(name)
            and (
                _is_date_string(non_null)
                or (numeric and _TIME_NAME.search(name) and name.lower().endswith("year"))
            )
        ):
            role = "temporal"
        elif numeric and _ID_NAME.search(name):
            role = "identifier"
        elif numeric:
            role = "measure"
        else:
            role = "category"
        shapes.append(
            ColumnShape(
                name=name,
                type=ctype,
                role=role,
                distinct=distinct,
                has_negative=role == "measure"
                and any(isinstance(v, int | float) and v < 0 for v in non_null),
                has_positive=role == "measure"
                and any(isinstance(v, int | float) and v > 0 for v in non_null),
                max_label_len=max((len(str(v)) for v in non_null), default=0) if role == "category" else 0,
            )
        )
    return shapes


def _base_option(columns: list[str], rows: list[list[Any]], title: str) -> dict[str, Any]:
    return {
        "title": {"text": title, "left": 0, "textStyle": {"fontSize": 13, "fontWeight": 500}},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 56, "right": 16, "top": 40, "bottom": 40, "containLabel": True},
        "dataset": {"dimensions": columns, "source": [_jsonable(r) for r in rows[:MAX_POINTS]]},
    }


def _jsonable(row: list[Any]) -> list[Any]:
    return [v.isoformat() if isinstance(v, dt.date | dt.datetime) else v for v in row]


def _series(opt: dict[str, Any]) -> list[dict[str, Any]]:
    return list(opt["series"])


def _suggestion(
    kind: str,
    title: str,
    reason: str,
    score: float,
    option: dict[str, Any],
    x: str | None = None,
    y: list[str] | None = None,
    series: str | None = None,
) -> dict[str, Any]:
    return {
        "type": kind,
        "title": title,
        "reason": reason,
        "score": round(score, 2),
        "x": x,
        "y": y or [],
        "series": series,
        "option": option,
    }


def _pivot_series(
    rows: list[list[Any]], cols: list[str], x: str, s: str, y: str
) -> tuple[list[Any], dict[str, list[Any]]]:
    xi, si, yi = cols.index(x), cols.index(s), cols.index(y)
    xs: list[Any] = []
    series: dict[str, dict[Any, Any]] = {}
    for r in rows:
        xv = r[xi].isoformat() if isinstance(r[xi], dt.date | dt.datetime) else r[xi]
        if xv not in xs:
            xs.append(xv)
        series.setdefault(str(r[si]), {})[xv] = r[yi]
    return xs, {k: [v.get(x_) for x_ in xs] for k, v in series.items()}


def suggest_charts(
    columns: list[dict[str, Any]], rows: list[list[Any]], *, title: str = ""
) -> list[dict[str, Any]]:
    shapes = classify(columns, rows)
    names = [s.name for s in shapes]
    temporal = [s for s in shapes if s.role == "temporal"]
    measures = [s for s in shapes if s.role == "measure"]
    cats = [s for s in shapes if s.role == "category"]
    n = len(rows)
    out: list[dict[str, Any]] = []
    t = title or (", ".join(m.name for m in measures[:2]) if measures else "Result")

    if n == 1 and measures and not temporal and not cats:
        opt = {
            "title": {"text": t},
            "dataset": {"dimensions": names, "source": [_jsonable(rows[0])]},
            "kpi": {m.name: rows[0][names.index(m.name)] for m in measures},
        }
        out.append(
            _suggestion(
                "kpi",
                t,
                "A single row of measures reads best as KPI values.",
                1.0,
                opt,
                y=[m.name for m in measures],
            )
        )

    if temporal and measures:
        x = temporal[0].name
        sorted_rows = sorted(rows, key=lambda r: str(r[names.index(x)]))
        small_cat = next((c for c in cats if 1 < c.distinct <= 12), None)
        if small_cat is not None and len(measures) >= 1:
            xs, series = _pivot_series(sorted_rows, names, x, small_cat.name, measures[0].name)
            opt = {
                "title": {"text": t},
                "tooltip": {"trigger": "axis"},
                "legend": {"type": "scroll", "top": 20},
                "grid": {"left": 56, "right": 16, "top": 56, "bottom": 40, "containLabel": True},
                "xAxis": {"type": "category", "data": xs},
                "yAxis": {"type": "value"},
                "series": [
                    {"type": "line", "name": k, "data": v, "showSymbol": False} for k, v in series.items()
                ],
            }
            out.append(
                _suggestion(
                    "line",
                    t,
                    f"Time ({x}) with a measure split by {small_cat.name} "
                    f"({small_cat.distinct} values): one line per series.",
                    0.95,
                    opt,
                    x=x,
                    y=[measures[0].name],
                    series=small_cat.name,
                )
            )
            area = {**opt, "series": [{**s, "stack": "total", "areaStyle": {}} for s in _series(opt)]}
            out.append(
                _suggestion(
                    "area",
                    t,
                    "Stacked area shows how each series contributes to the total over time.",
                    0.7,
                    area,
                    x=x,
                    y=[measures[0].name],
                    series=small_cat.name,
                )
            )
        else:
            opt = _base_option(names, sorted_rows, t)
            opt |= {
                "xAxis": {"type": "category"},
                "yAxis": {"type": "value"},
                "series": [
                    {"type": "line", "encode": {"x": x, "y": m.name}, "name": m.name, "showSymbol": n <= 60}
                    for m in measures[:4]
                ],
            }
            if len(measures) > 1:
                opt["legend"] = {"top": 20}
            out.append(
                _suggestion(
                    "line",
                    t,
                    f"{x} is a time axis and {', '.join(m.name for m in measures[:4])} "
                    "are measures: a line shows the trend.",
                    0.95,
                    opt,
                    x=x,
                    y=[m.name for m in measures[:4]],
                )
            )
            bar = {**opt, "series": [{**s, "type": "bar"} for s in _series(opt)]}
            out.append(
                _suggestion(
                    "bar",
                    t,
                    "Bars compare discrete periods.",
                    0.6,
                    bar,
                    x=x,
                    y=[m.name for m in measures[:4]],
                )
            )

    if cats and measures and not temporal:
        c, m = cats[0], measures[0]
        mi = names.index(m.name)
        if len(cats) >= 2:
            c2 = cats[1]
            if c2.distinct <= 8:
                xs, series = _pivot_series(rows, names, c.name, c2.name, m.name)
                opt = {
                    "title": {"text": t},
                    "tooltip": {"trigger": "axis"},
                    "legend": {"type": "scroll", "top": 20},
                    "grid": {"left": 56, "right": 16, "top": 56, "bottom": 40, "containLabel": True},
                    "xAxis": {"type": "category", "data": xs},
                    "yAxis": {"type": "value"},
                    "series": [
                        {"type": "bar", "stack": "total", "name": k, "data": v} for k, v in series.items()
                    ],
                }
                out.append(
                    _suggestion(
                        "stacked_bar",
                        t,
                        f"{m.name} by {c.name}, stacked by {c2.name} ({c2.distinct} values).",
                        0.85,
                        opt,
                        x=c.name,
                        y=[m.name],
                        series=c2.name,
                    )
                )
            xs, series = _pivot_series(rows, names, c.name, c2.name, m.name)
            data = []
            ys = list(series)
            for yi, key in enumerate(ys):
                for xi, v in enumerate(series[key]):
                    data.append([xi, yi, v])
            vals = [d[2] for d in data if isinstance(d[2], int | float)]
            opt = {
                "title": {"text": t},
                "tooltip": {"position": "top"},
                "grid": {"left": 80, "right": 16, "top": 40, "bottom": 60, "containLabel": True},
                "xAxis": {"type": "category", "data": xs},
                "yAxis": {"type": "category", "data": ys},
                "visualMap": {
                    "min": min(vals, default=0),
                    "max": max(vals, default=1),
                    "calculable": True,
                    "orient": "horizontal",
                    "left": "center",
                    "bottom": 0,
                },
                "series": [{"type": "heatmap", "data": data}],
            }
            out.append(
                _suggestion(
                    "heatmap",
                    t,
                    f"Two categories ({c.name} x {c2.name}) and one measure.",
                    0.75 if c2.distinct > 8 else 0.6,
                    opt,
                    x=c.name,
                    y=[m.name],
                    series=c2.name,
                )
            )
        if c.distinct < n:  # several rows per category -> distribution
            groups: dict[str, list[float]] = {}
            ci = names.index(c.name)
            for r in rows:
                if isinstance(r[mi], int | float):
                    groups.setdefault(str(r[ci]), []).append(float(r[mi]))
            if groups and max(len(v) for v in groups.values()) >= 5 and len(groups) <= 30:
                stats = {k: _box(v) for k, v in groups.items()}
                opt = {
                    "title": {"text": t},
                    "tooltip": {"trigger": "item"},
                    "grid": {"left": 56, "right": 16, "top": 40, "bottom": 40, "containLabel": True},
                    "xAxis": {"type": "category", "data": list(stats)},
                    "yAxis": {"type": "value"},
                    "series": [{"type": "boxplot", "data": list(stats.values())}],
                }
                out.append(
                    _suggestion(
                        "box",
                        t,
                        f"Many {m.name} values per {c.name}: a box plot shows the spread.",
                        0.8,
                        opt,
                        x=c.name,
                        y=[m.name],
                    )
                )
        if c.distinct == n:
            ordered = sorted(
                rows, key=lambda r: (r[mi] is None, -(r[mi] or 0) if isinstance(r[mi], int | float) else 0)
            )
            horizontal = c.distinct > 12 or c.max_label_len > 14
            top = ordered[:25]
            opt = _base_option(names, list(reversed(top)) if horizontal else top, t)
            if horizontal:
                opt |= {
                    "xAxis": {"type": "value"},
                    "yAxis": {"type": "category"},
                    "series": [{"type": "bar", "encode": {"y": c.name, "x": m.name}, "name": m.name}],
                }
            else:
                opt |= {
                    "xAxis": {
                        "type": "category",
                        "axisLabel": {"interval": 0, "rotate": 30 if c.distinct > 6 else 0},
                    },
                    "yAxis": {"type": "value"},
                    "series": [{"type": "bar", "encode": {"x": c.name, "y": m.name}, "name": m.name}],
                }
            reason = f"{m.name} compared across {c.distinct} {c.name} values, sorted descending"
            if c.distinct > 25:
                reason += " (top 25 shown)"
            out.append(_suggestion("bar", t, reason + ".", 0.9, opt, x=c.name, y=[m.name]))
            if m.has_negative and m.has_positive:
                out.append(_waterfall(rows, names, c.name, m.name, t))

    if not cats and not temporal and len(measures) >= 2 and n > 2:
        a, b = measures[0].name, measures[1].name
        opt = _base_option(names, rows, t)
        opt |= {
            "tooltip": {"trigger": "item"},
            "xAxis": {"type": "value", "name": a},
            "yAxis": {"type": "value", "name": b},
            "series": [{"type": "scatter", "encode": {"x": a, "y": b}, "symbolSize": 6}],
        }
        out.append(
            _suggestion(
                "scatter",
                t,
                f"Two measures ({a}, {b}): a scatter shows their relationship (exploratory, not causal).",
                0.85,
                opt,
                x=a,
                y=[b],
            )
        )

    if len(measures) >= 1 and not temporal and n >= 20 and (not cats or cats[0].distinct < n):
        m = measures[0]
        mi = names.index(m.name)
        vals = [float(r[mi]) for r in rows if isinstance(r[mi], int | float)]
        if vals:
            bins = _histogram(vals)
            opt = {
                "title": {"text": t},
                "tooltip": {"trigger": "axis"},
                "grid": {"left": 56, "right": 16, "top": 40, "bottom": 40, "containLabel": True},
                "xAxis": {"type": "category", "data": [b["label"] for b in bins]},
                "yAxis": {"type": "value"},
                "series": [{"type": "bar", "data": [b["count"] for b in bins], "barCategoryGap": "2%"}],
            }
            out.append(
                _suggestion(
                    "histogram",
                    t,
                    f"Distribution of {m.name} across {len(vals)} rows.",
                    0.7 if cats else 0.8,
                    opt,
                    x=m.name,
                    y=["count"],
                )
            )

    out.append(
        _suggestion(
            "table",
            t or "Result",
            "Tables show exact values and suit any shape.",
            0.3,
            {"dataset": {"dimensions": names, "source": [_jsonable(r) for r in rows[:MAX_POINTS]]}},
        )
    )
    out.sort(key=lambda s: -s["score"])
    return out


def _waterfall(rows: list[list[Any]], names: list[str], cat: str, measure: str, title: str) -> dict[str, Any]:
    ci, mi = names.index(cat), names.index(measure)
    labels, base, pos, neg = [], [], [], []
    running = 0.0
    items = sorted([r for r in rows if isinstance(r[mi], int | float)], key=lambda r: -abs(r[mi]))[:20]
    for r in items:
        v = float(r[mi])
        labels.append(str(r[ci]))
        start = running if v >= 0 else running + v
        base.append(round(start, 6))
        pos.append(round(v, 6) if v >= 0 else "-")
        neg.append(round(-v, 6) if v < 0 else "-")
        running += v
    labels.append("Total")
    base.append(min(0.0, running))
    pos.append(round(running, 6) if running >= 0 else "-")
    neg.append(round(-running, 6) if running < 0 else "-")
    opt = {
        "title": {"text": title},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 56, "right": 16, "top": 40, "bottom": 60, "containLabel": True},
        "xAxis": {"type": "category", "data": labels, "axisLabel": {"interval": 0, "rotate": 30}},
        "yAxis": {"type": "value"},
        "series": [
            {
                "type": "bar",
                "stack": "wf",
                "data": base,
                "itemStyle": {"color": "transparent"},
                "emphasis": {"itemStyle": {"color": "transparent"}},
                "tooltip": {"show": False},
            },
            {"type": "bar", "stack": "wf", "name": "Increase", "data": pos},
            {"type": "bar", "stack": "wf", "name": "Decrease", "data": neg},
        ],
    }
    return _suggestion(
        "waterfall",
        title,
        f"{measure} has positive and negative values by {cat}: a waterfall "
        "bridges the changes to their total.",
        0.88,
        opt,
        x=cat,
        y=[measure],
    )


def _box(values: list[float]) -> list[float]:
    v = sorted(values)

    def q(p: float) -> float:
        k = (len(v) - 1) * p
        f = int(k)
        c = min(f + 1, len(v) - 1)
        return v[f] + (v[c] - v[f]) * (k - f)

    return [v[0], q(0.25), q(0.5), q(0.75), v[-1]]


def _histogram(values: list[float], max_bins: int = 30) -> list[dict[str, Any]]:
    lo, hi = min(values), max(values)
    if lo == hi:
        return [{"label": f"{lo:g}", "count": len(values)}]
    k = min(max_bins, max(5, int(len(values) ** 0.5)))
    width = (hi - lo) / k
    counts = [0] * k
    for x in values:
        idx = min(int((x - lo) / width), k - 1)
        counts[idx] += 1
    return [
        {"label": f"{lo + i * width:,.4g}–{lo + (i + 1) * width:,.4g}", "count": c}
        for i, c in enumerate(counts)
    ]


def best_chart(columns: list[dict[str, Any]], rows: list[list[Any]], *, title: str = "") -> dict[str, Any]:
    return suggest_charts(columns, rows, title=title)[0]
