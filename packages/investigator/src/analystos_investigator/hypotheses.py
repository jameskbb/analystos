"""Hypothesis generation (spec §18; INV-03).

A generic framework, not revenue-specific. For any metric it proposes:

* driver decomposition hypotheses from the metric tree ("fewer orders", "lower AOV"),
* dimension mix hypotheses for every reachable dimension ("the change is concentrated in
  certain branches"),
* customer concentration, if a customer-like dimension exists,
* price/discount and cancellation/return hypotheses, if related metrics exist,
* a seasonality check against the prior year,
* untestable hypotheses from the template (marketing, competitors), which stay
  ``hypothesis_only`` because no data in the semantic model can test them.

Each testable hypothesis lists the plan steps that test it.
"""

from __future__ import annotations

from analystos_engine.semantic.models import SemanticModel

from .evidence import phrase_hypothesis
from .ids import stable_id
from .models import AnalysisPlan, Hypothesis, Interpretation
from .planner import driver_edges
from .planner import plan as make_plan
from .templates import dimension_roles, get_template


def generate(
    interp: Interpretation, model: SemanticModel, plan: AnalysisPlan | None = None
) -> list[Hypothesis]:
    p = plan or make_plan(interp, model)
    ctx = p.context
    if ctx is None:
        return []
    metric = model.get_metric(ctx.metric_id)
    name = metric.display_name
    lower = "lower" if interp.direction != "increase" else "higher"
    fewer = "fewer" if interp.direction != "increase" else "more"
    out: list[Hypothesis] = []
    step_ids = {s.id for s in p.steps}

    def add(statement: str, category: str, steps: list[str], testable: bool = True, reason: str = "") -> None:
        steps = list(dict.fromkeys(s for s in steps if s in step_ids))
        out.append(
            Hypothesis(
                id=stable_id("hyp", ctx.metric_id, statement),
                statement=statement if testable else phrase_hypothesis(statement),
                category=category,  # type: ignore[arg-type]
                testable=testable and bool(steps),
                test_step_ids=steps,
                reason=reason
                or ("tested by: " + ", ".join(steps) if steps else "no executable test in the plan"),
            )
        )

    bridge = next((s for s in p.steps if s.params.get("method") == "margin_bridge"), None)
    if bridge is not None:
        cost = model.get_metric(bridge.params["cost"]).display_name
        add(
            f"Higher unit costs (input cost inflation) lowered {name}"
            if interp.direction != "increase"
            else f"Lower unit costs raised {name}",
            "price_discount",
            [bridge.id, *[s.id for s in p.steps if s.id.startswith("check:") and "cost" in s.id]],
            reason=f"tested by the unit cost counterfactual ({cost} at baseline unit costs per item)",
        )
        if bridge.params.get("gross"):
            add(
                f"{'More' if interp.direction != 'increase' else 'Less'} discounting changed {name}",
                "price_discount",
                [bridge.id, *[s.id for s in p.steps if s.kind == "compare" and "discount" in s.id]],
                reason="tested by the discount counterfactual (baseline discount rates per item)",
            )
        add(
            f"A shift in product mix changed {name}",
            "dimension_mix",
            [bridge.id],
            reason="measured as the remainder of the bridge; see the product breakdowns",
        )
    edges = [] if bridge is not None else driver_edges(model, ctx.metric_id)
    for child, relation in edges:
        cm = model.get_metric(child)
        if relation == "subtractive":
            text = f"{'higher' if lower == 'lower' else 'lower'} {cm.display_name} changed {name}"
        elif relation in ("multiplicative", "ratio_numerator", "additive"):
            word = fewer if cm.format == "integer" else lower
            text = f"{word} {cm.display_name} changed {name}"
        else:
            text = f"a change in {cm.display_name} changed {name}"
        add(
            text[0].upper() + text[1:],
            "driver_decomposition",
            [f"decompose:{ctx.metric_id}", *[s.id for s in p.steps if s.params.get("metric_id") == child]],
        )

    for s in p.steps:
        if s.kind != "contribution" or s.params.get("metric_id") != ctx.metric_id:
            continue
        dim = model.get_dimension(s.params["dimension"])
        roles = dimension_roles(dim)
        label = dim.label or dim.name.replace("_", " ")
        if "customer" in roles:
            add(f"The change in {name} is concentrated in a few customers", "customer_concentration", [s.id])
        else:
            add(
                f"The change in {name} is concentrated in specific {label} segments (mix shift)",
                "dimension_mix",
                [s.id],
            )

    for s in p.steps:
        if s.kind == "compare" and s.params.get("role") == "check":
            cm = model.get_metric(s.params["metric_id"])
            cat = s.params.get("category", "price_discount")
            if cat not in ("price_discount", "cancellations_returns", "driver_decomposition"):
                cat = "price_discount"
            add(f"A change in {cm.display_name} contributed to the change in {name}", cat, [s.id])
        if s.kind == "compare" and s.params.get("role") == "seasonality":
            add(f"The change in {name} is seasonal (it also happened a year earlier)", "seasonality", [s.id])

    tpl = get_template(p.template_id) if p.template_id else None
    for text in tpl.untestable_hypotheses if tpl else []:
        add(
            text,
            "untestable",
            [],
            testable=False,
            reason="no metric or dimension in the semantic model measures this, so it cannot be tested",
        )
    return out
