"""ECharts option builders for investigation artifacts (declarative JSON, no rendering)."""

from __future__ import annotations

from typing import Any


def _clean(v: float | None) -> float | None:
    return None if v is None else round(float(v), 6)


def comparison_chart(
    title: str, baseline_label: str, current_label: str, baseline: float | None, current: float | None
) -> dict[str, Any]:
    return {
        "title": {"text": title},
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": [baseline_label, current_label]},
        "yAxis": {"type": "value"},
        "series": [{"type": "bar", "name": title, "data": [_clean(baseline), _clean(current)]}],
    }


def waterfall_chart(
    title: str,
    baseline_label: str,
    current_label: str,
    baseline: float,
    current: float,
    steps: list[tuple[str, float]],
) -> dict[str, Any]:
    """Waterfall as a stacked bar: a transparent base series plus visible deltas."""
    cats = [baseline_label, *[s[0] for s in steps], current_label]
    base: list[float | None] = [0.0]
    up: list[float | None] = [_clean(baseline)]
    down: list[float | None] = [None]
    running = baseline
    for _, delta in steps:
        nxt = running + delta
        base.append(_clean(min(running, nxt)))
        if delta >= 0:
            up.append(_clean(delta))
            down.append(None)
        else:
            up.append(None)
            down.append(_clean(-delta))
        running = nxt
    base.append(0.0)
    up.append(_clean(current))
    down.append(None)
    return {
        "title": {"text": title},
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": cats},
        "yAxis": {"type": "value"},
        "series": [
            {
                "type": "bar",
                "stack": "wf",
                "name": "base",
                "itemStyle": {"color": "transparent"},
                "emphasis": {"disabled": True},
                "data": base,
            },
            {"type": "bar", "stack": "wf", "name": "increase", "data": up},
            {"type": "bar", "stack": "wf", "name": "decrease", "data": down},
        ],
        "meta": {"chart_type": "waterfall"},
    }


def contribution_chart(title: str, rows: list[tuple[str, float]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: r[1])
    return {
        "title": {"text": title},
        "tooltip": {"trigger": "axis"},
        "grid": {"containLabel": True},
        "xAxis": {"type": "value", "name": "effect on change"},
        "yAxis": {"type": "category", "data": [r[0] for r in ordered]},
        "series": [{"type": "bar", "name": "effect", "data": [_clean(r[1]) for r in ordered]}],
        "meta": {"chart_type": "bar_horizontal"},
    }


def line_chart(
    title: str, labels: list[str], values: list[float | None], highlight: str | None = None
) -> dict[str, Any]:
    series: dict[str, Any] = {"type": "line", "name": title, "data": [_clean(v) for v in values]}
    if highlight is not None and highlight in labels:
        idx = labels.index(highlight)
        series["markPoint"] = {"data": [{"coord": [labels[idx], _clean(values[idx])], "name": "selected"}]}
    return {
        "title": {"text": title},
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value"},
        "series": [series],
    }
