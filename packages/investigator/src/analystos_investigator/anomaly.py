"""Anomaly investigation (spec §40): "Revenue unexpectedly fell on Aug 12".

``investigate_anomaly`` builds an interpretation for a single day (or any window flagged by
the engine's anomaly detection) and runs the standard investigation with the anomaly
template settings:

* baseline: the same weekday one week earlier (default) or the previous day,
* an ``anomaly`` step that measures how unusual the day is (robust z-score over the trailing
  eight weeks) before explaining it,
* driver decomposition (for example transaction counts x average value) and contribution by
  region, product, channel and customer, with drill into disproportionate segments.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from analystos_engine.semantic.models import SemanticModel
from analystos_engine.types import TimeWindow

from .hypotheses import generate as generate_hypotheses
from .investigation import run_investigation
from .models import ExecutionConfig, FilterSpec, Interpretation, Investigation, InvestigationRun
from .planner import plan as make_plan
from .templates import choose_template


def investigate_anomaly(
    store: Any,
    model: SemanticModel,
    metric_id: str,
    day: dt.date,
    *,
    baseline: Literal["same_weekday_last_week", "previous_day"] = "same_weekday_last_week",
    filters: list[FilterSpec] | None = None,
    config: ExecutionConfig | None = None,
    question: str | None = None,
) -> InvestigationRun:
    metric = model.get_metric(metric_id)
    window = TimeWindow(start=day, end=day + dt.timedelta(days=1), kind="day", label=day.isoformat())
    delta = dt.timedelta(days=7 if baseline == "same_weekday_last_week" else 1)
    base_day = day - delta
    base = TimeWindow(
        start=base_day, end=base_day + dt.timedelta(days=1), kind="day", label=base_day.isoformat()
    )
    q = question or f"Why did {metric.display_name} move unexpectedly on {day.isoformat()}?"
    interp = Interpretation(
        question=q,
        metric_ids=[metric_id],
        window=window,
        baseline=base,
        comparison_kind="pop",
        filters=list(filters or []),
        intent="anomaly",
        confidence_notes=[
            "anomaly investigation: compared with "
            + (
                "the same weekday one week earlier"
                if baseline == "same_weekday_last_week"
                else "the previous day"
            )
        ],
    )
    interp.template_id = choose_template(interp, model).id
    p = make_plan(interp, model, config=config)
    inv = Investigation(
        id="inv_" + uuid.uuid4().hex[:16],
        question=q,
        interpretation=interp,
        plan=p,
        hypotheses=generate_hypotheses(interp, model, p),
        model_snapshot_hash=model.content_hash(),
    )
    return run_investigation(inv, store, model)
