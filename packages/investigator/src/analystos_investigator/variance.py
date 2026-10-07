"""Plan-variance steps of the executor: the variance tree against plan, the margin bridge
against plan, drills (by plan dimensions and below plan grain), materiality grouping, the
reconciliation and the plan-to-actual bridge, related signals and premises against plan.

Everything is generic: plan metrics come from ``plan_for`` pairs frozen on the plan context,
dimensions from the semantic model, numbers from executed queries only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from analystos_engine.calendar import previous_period
from analystos_engine.types import TimeWindow

from . import charts
from .attribution import safe_pct, safe_share
from .evidence import MODERATE_SHARE, EvidenceInput
from .executor import IDENTITY_GAP_LIMIT, QueryOutcome, _Executor, _seg_key, _to_float
from .formatting import fmt_abs_pct, fmt_change, fmt_pct, fmt_share, fmt_value
from .ids import stable_id
from .models import (
    BridgeStep,
    Contribution,
    PlanBridge,
    PlanStep,
    Reconciliation,
    ReconciliationIdentity,
    ReconciliationLine,
    RelatedSignal,
    Segment,
    TreeNode,
)
from .planner import pinned_dimensions
from .semantic_graph import dimensions_for_metric, time_dimension_for_metric
from .templates import dimension_roles, rank_dimensions

MATERIALITY = 0.05
"""Parts of one identity whose gap is below 5% of the parent's gap each are grouped into a single
"Other lines, net" node (when there are at least two); the group still reconciles."""

BELOW_GRAIN_MAX_SEGMENTS = 25
"""A dimension below plan grain is only used to split an effect when it has 2 to 25 values inside
the segment; finer dimensions (products, customers) scatter the effect instead of explaining it."""


CellKey = tuple[str | None, ...]
"""A plan cell: one value per cell dimension."""

Measure = Callable[[list[CellKey]], tuple[float, float, str]]
"""Actual, counterfactual and a plain phrase ("costs 7.4% above plan") for a set of cells."""


class Executor(_Executor):
    """The complete executor: :class:`_Executor` plus the plan-variance step kinds."""

    def dim_words(self, dim: str, *, count: int = 1) -> str:
        """The dimension's label as words inside a sentence ("product category", "branch");
        acronyms ("SKU") keep their case. With ``count`` above one, the plural ("product
        categories", "branches", "SKUs")."""
        words = [w if len(w) > 1 and w.isupper() else w.lower() for w in self.dim_label(dim).split()]
        if count > 1 and words:
            last = words[-1]
            if last.endswith("y") and len(last) > 1 and last[-2] not in "aeiou":
                last = last[:-1] + "ies"
            elif last.endswith(("s", "x", "ch", "sh")):
                last += "es"
            else:
                last += "s"
            words[-1] = last
        return " ".join(words)

    def plan_variance(self, step: PlanStep) -> None:
        """Split the root's gap to plan through the metric tree: each child is compared with its
        own governed plan metric, and additive identities make the children's effects add up to
        the parent's gap exactly (checked on both the actual and the plan side)."""
        assert self.root_id is not None
        root = self.nodes[self.root_id]
        if root.current is None or root.baseline is None:
            return
        self.root_sign[root.id] = 1
        root.root_effect = root.current - root.baseline
        edges: dict[str, list[list[str]]] = step.params.get("edges", {})
        self._variance_children(root, self.ctx.metric_id, edges, 1, step)

    def _variance_children(
        self, parent: TreeNode, metric_id: str, edges: dict[str, list[list[str]]], sign: int, step: PlanStep
    ) -> None:
        kids = edges.get(metric_id)
        if not kids or parent.current is None or parent.baseline is None:
            return
        window = self.ctx.window
        measured: list[tuple[str, str, str, float, float]] = []
        ids: list[str] = []
        for child, relation, plan in kids:
            cur = self.metric_query(
                [child],
                window,
                filters=list(self.ctx.filters),
                title=f"{self.metric_label(child)}, {window.display()}",
            )
            base = self.metric_query(
                [plan],
                window,
                filters=list(self.ctx.filters),
                title=f"{self.metric_label(plan)}, {window.display()}",
            )
            ids.extend([cur.artifact.id, base.artifact.id])
            cv = _to_float(cur.rows[0].get(child)) if cur.ok else None
            bv = _to_float(base.rows[0].get(plan)) if base.ok else None
            if not cur.ok or not base.ok or cv is None or bv is None:
                self.failed_node(
                    parent.id,
                    step,
                    f"{self.metric_label(child)} vs plan in the decomposition of {self.metric_label(metric_id)}",
                    cur.error or base.error or "no value",
                    [cur.artifact.id, base.artifact.id],
                )
                return
            measured.append((child, relation, plan, cv, bv))

        def rel_sign(relation: str) -> int:
            return 1 if relation == "additive" else -1

        ident_cur = sum(rel_sign(r) * c for _m, r, _p, c, _b in measured)
        ident_plan = sum(rel_sign(r) * b for _m, r, _p, _c, b in measured)
        scale = max(abs(parent.current), abs(parent.baseline), 1.0)
        gap = max(abs(ident_cur - parent.current), abs(ident_plan - parent.baseline)) / scale
        pm = self.model.get_metric(metric_id)
        if gap > IDENTITY_GAP_LIMIT:
            self.failed_node(
                parent.id,
                step,
                f"variance decomposition of {pm.display_name}",
                f"the children do not reproduce {pm.display_name} (identity off by {gap * 100:.1f}%); check the "
                "metric tree and the plan metrics' formulas",
                ids,
            )
            return
        parent_var = parent.current - parent.baseline
        effects = [(m, r, p, c, b, rel_sign(r) * (c - b)) for m, r, p, c, b in measured]
        residual = parent_var - sum(e[5] for e in effects)
        art = self.metric_result(
            f"{pm.display_name} vs plan by component",
            {
                "method": "plan_variance",
                "parent_current": parent.current,
                "parent_baseline": parent.baseline,
                "parent_change": parent_var,
                "effects": [
                    {
                        "metric_id": m,
                        "plan_metric_id": p,
                        "relation": r,
                        "current": c,
                        "baseline": b,
                        "effect": e,
                        "share": safe_share(e, parent_var),
                        "root_effect": sign * rel_sign(r) * (c - b),
                    }
                    for m, r, p, c, b, e in effects
                ],
                "residual": residual,
                "residual_share": safe_share(residual, parent_var),
                "identity_current": ident_cur,
                "identity_baseline": ident_plan,
                "notes": ["actuals and plan both satisfy the metric tree identity"],
            },
            ids,
            chart=charts.waterfall_chart(
                f"{pm.display_name}: plan to actual",
                "Plan",
                window.display(),
                parent.baseline,
                parent.current,
                [(self.metric_label(m), e) for m, _r, _p, _c, _b, e in effects],
            ),
            filters=list(self.ctx.filters),
            window=window,
            metric_ids=[metric_id, *[m for m, *_ in effects]],
        )
        corroboration = [
            f"actuals and plan both satisfy {pm.display_name} = "
            + " ".join(
                f"{'+' if r == 'additive' else '-'} {self.metric_label(m)}" for m, r, *_ in effects
            ).lstrip("+ ")
            + f" (gap {gap * 100:.2f}%)"
        ]
        is_root = parent.id == self.root_id
        parent_noun = self.gap_noun(metric_id, parent_var, root=is_root)
        method = (
            f"Exact additive identity of the metric tree: {pm.display_name} = "
            + " ".join(
                f"{'+' if r == 'additive' else '-'} {self.metric_label(m)}" for m, r, *_ in effects
            ).lstrip("+ ")
            + ", on actuals and on the plan metrics alike (each child compared with its governed plan metric, "
            "plan_for), so the children's gaps add up to the parent's gap."
        )
        ordered = sorted(effects, key=lambda x: (-abs(x[5]), x[0]))
        small = [x for x in ordered if abs(parent_var) > 1e-9 and abs(x[5]) < MATERIALITY * abs(parent_var)]
        grouped = small if len(small) >= 2 else []
        for m, r, p, c, b, e in ordered:
            if any(x[0] == m for x in grouped):
                continue
            cm = self.model.get_metric(m)
            child_sign = sign * rel_sign(r)
            root_eff = child_sign * (c - b) + 0.0
            share = safe_share(e, parent_var)
            node = TreeNode(
                id=stable_id("node", "driver", parent.id, m),
                parent_id=parent.id,
                kind="driver",
                statement=self.plain_variance(m, c, b, share, pm.display_name, parent_noun),
                statement_type="supported_explanation",
                title=self.variance_title(m, c, b),
                metric_id=m,
                metric_label=cm.display_name,
                metric_format=cm.format,
                current=c,
                baseline=b,
                baseline_label="Plan",
                abs_change=c - b,
                pct_change=safe_pct(c, b),
                contribution_to_parent=Contribution(effect=e, share=share, method="additive_identity"),
                root_effect=root_eff,
                evidence_strength="moderate",
                artifact_ids=[art.id, *ids],
                step_id=step.id,
                method=method,
                notes=[f"compared with {self.metric_label(p)} ({p}), its governed plan metric"],
            )
            self.add_node(
                node,
                EvidenceInput(
                    executed=True,
                    exact_method=True,
                    method_label="exact additive identity of the metric tree, actuals and plan",
                    share=share,
                    residual_share=safe_share(residual, parent_var),
                    corroborations=list(corroboration),
                    pct_change=safe_pct(c, b),
                ),
            )
            self.root_sign[node.id] = child_sign
            self._variance_children(node, m, edges, child_sign, step)
        if grouped:
            self._other_lines(parent, metric_id, grouped, sign, parent_var, art.id, ids, step, rel_sign)
        if abs(residual) > max(0.005, 1e-9 * scale):
            node = TreeNode(
                id=stable_id("node", "residual", parent.id),
                parent_id=parent.id,
                kind="other",
                statement=(
                    f"{fmt_value(abs(residual), pm.format)} of the {pm.display_name} {parent_noun} is not explained by "
                    "its parts"
                ),
                statement_type="observation",
                title="Unexplained residual",
                metric_id=metric_id,
                contribution_to_parent=Contribution(
                    effect=residual, share=safe_share(residual, parent_var), method="additive_identity"
                ),
                root_effect=sign * residual,
                evidence_strength="weak",
                artifact_ids=[art.id],
                step_id=step.id,
                method=method,
            )
            self.add_node(
                node,
                EvidenceInput(
                    executed=True,
                    measurement_only=True,
                    validation_ok=False,
                    validation_failures=["identity residual"],
                ),
            )

    def _other_lines(
        self,
        parent: TreeNode,
        metric_id: str,
        grouped: list[tuple[str, str, str, float, float, float]],
        sign: int,
        parent_var: float,
        art_id: str,
        ids: list[str],
        step: PlanStep,
        rel_sign: Callable[[str], int],
    ) -> None:
        """Group the immaterial parts of one identity into a single node that still reconciles."""
        root_m = self.model.get_metric(self.ctx.metric_id)
        pm = self.model.get_metric(metric_id)
        members = [
            ReconciliationLine(
                label=self.variance_title(m, c, b),
                amount=sign * rel_sign(r) * (c - b) + 0.0,
                metric_id=m,
            )
            for m, r, _p, c, b, _e in grouped
        ]
        effect = sum(e for *_x, e in grouped)
        root_eff = sum(x.amount for x in members) + 0.0
        share = safe_share(effect, parent_var)
        count = {2: "Two", 3: "Three", 4: "Four", 5: "Five"}.get(len(members), str(len(members)))

        def member_text(x: ReconciliationLine) -> str:
            name = self.metric_label(x.metric_id or "")
            if abs(x.amount) < 0.005:
                return f"{name} on plan"
            return f"{name} {fmt_change(x.amount, root_m.format)}"

        node = TreeNode(
            id=stable_id("node", "other_lines", parent.id),
            parent_id=parent.id,
            kind="other",
            title="Other lines, net",
            statement=(
                f"{count} smaller lines net to {fmt_change(root_eff, root_m.format)} of {root_m.display_name}: "
                + ", ".join(member_text(x) for x in members)
                + "."
            ),
            statement_type="supported_explanation",
            metric_id=metric_id,
            metric_label="Other lines, net",
            metric_format=pm.format,
            contribution_to_parent=Contribution(effect=effect, share=share, method="additive_identity"),
            root_effect=root_eff,
            members=members,
            evidence_strength="weak",
            artifact_ids=[art_id, *ids],
            step_id=step.id,
            method=f"Lines whose gap to plan is below {MATERIALITY:.0%} of the {pm.display_name} gap each are "
            "grouped; their sum stays part of the reconciled identity and each is measured in the artifact.",
        )
        self.add_node(
            node,
            EvidenceInput(executed=True, share=share, method_label="grouped immaterial lines"),
        )
        self.root_sign[node.id] = sign

    def plain_variance(
        self, metric_id: str, actual: float, plan: float, share: float | None, parent: str, noun: str
    ) -> str:
        """One plain sentence for a line against plan: what it came in at, the plan, the share."""
        m = self.model.get_metric(metric_id)
        name, fmt = m.display_name, m.format
        var = actual - plan
        pct = fmt_pct(safe_pct(actual, plan))
        if abs(var) <= 0.005 * max(abs(plan), 1e-9):
            return f"{name} was on plan at {fmt_value(actual, fmt)}."
        if m.higher_is_better:
            head = (
                f"{name} came in at {fmt_value(actual, fmt)}, {fmt_value(abs(var), fmt)} "
                f"{'below' if var < 0 else 'above'} its plan of {fmt_value(plan, fmt)}"
            )
        else:
            head = (
                f"{name} ran {fmt_value(abs(var), fmt)} ({pct}) {'over' if var > 0 else 'under'} plan: "
                f"{fmt_value(actual, fmt)} against {fmt_value(plan, fmt)}"
            )
        if share is None or abs(share) < 0.005:
            return head + "."
        if share > 0:
            return f"{head}; that is {fmt_share(share)} of the {parent} {noun}."
        return f"{head}, offsetting {fmt_share(abs(share))} of the {parent} {noun}."

    def plan_bridge(self, step: PlanStep) -> None:
        """Exact bridge of a margin's gap to plan into unit cost, discounting and the rest.

        Per plan cell k (the conformed dimensions actuals and plan share) with actual units q1,
        revenue R1, cost C1 and list-price sales G1, and plan units q0, revenue R0, cost C0 and
        list-price sales G0: C'_k = q1 x C0 / q0 is the actual volume at plan unit cost and
        R'_k = G1 x R0 / G0 the actual list-price sales at the plan discount rate.
        unit cost effect = sum(C' - C1), discount effect = sum(R1 - R'), and volume, mix and list
        price = (M1 - M0) - both. Cells without plan volume (or plan list-price sales) keep their
        actual cost (or revenue), so unplanned business counts as volume and mix.
        """
        assert self.root_id is not None
        p = step.params
        mid = p["metric_id"]
        parent = self.find_metric_node(p.get("attach_to", mid)) or self.nodes[self.root_id]
        if parent.current is None or parent.baseline is None:
            return
        m = self.model.get_metric(mid)
        dims: list[str] = list(p["cell_dimensions"])
        rev, cost, vol, gross = p["revenue"], p["cost"], p["volume"], p.get("gross")
        prev, pcost, pvol, pgross = p["plan_revenue"], p["plan_cost"], p["plan_volume"], p.get("plan_gross")
        actual = [rev, cost, vol] + ([gross] if gross and pgross else [])
        planned = [prev, pcost, pvol] + ([pgross] if gross and pgross else [])
        window = self.ctx.window
        where = " x ".join(self.dim_label(d) for d in dims)
        outs = []
        for metrics, label in ((actual, window.display()), (planned, f"plan, {window.display()}")):
            out = self.metric_query(
                metrics,
                window,
                dims=dims,
                filters=list(self.ctx.filters),
                title=f"{', '.join(self.metric_label(x) for x in metrics)} by {where}, {label}",
                expect="many",
            )
            if not out.ok:
                self.failed_node(parent.id, step, step.title, out.error or "query failed", [out.artifact.id])
                return
            outs.append(out)
        ids = [o.artifact.id for o in outs]

        def cell(r: dict[str, Any]) -> tuple[str | None, ...]:
            return tuple(_seg_key(r.get(d)) for d in dims)

        cur = {cell(r): r for r in outs[0].rows}
        plan = {cell(r): r for r in outs[1].rows}

        def val(
            rows: dict[tuple[str | None, ...], dict[str, Any]], k: tuple[str | None, ...], col: str | None
        ) -> float:
            r = rows.get(k)
            return (_to_float(r.get(col)) or 0.0) if r is not None and col else 0.0

        keys = sorted(set(cur) | set(plan), key=lambda k: tuple("" if v is None else v for v in k))
        m1 = sum(val(cur, k, rev) - val(cur, k, cost) for k in keys)
        m0 = sum(val(plan, k, prev) - val(plan, k, pcost) for k in keys)
        scale = max(abs(parent.current), abs(parent.baseline), 1.0)
        off = max(abs(m1 - parent.current), abs(m0 - parent.baseline)) / scale
        if off > 0.001:
            self.failed_node(
                parent.id,
                step,
                step.title,
                f"the cells do not reproduce {m.display_name} (off by {off * 100:.2f}%), so the bridge would not be exact",
                ids,
            )
            return
        cost_k: dict[tuple[str | None, ...], float] = {}
        disc_k: dict[tuple[str | None, ...], float] = {}
        rest_k: dict[tuple[str | None, ...], float] = {}
        cost_cf: dict[tuple[str | None, ...], float] = {}
        rev_cf: dict[tuple[str | None, ...], float] = {}
        basis, pbasis = p.get("cost_basis") or vol, p.get("plan_cost_basis") or pvol
        for k in keys:
            q1, c1, r1 = val(cur, k, basis), val(cur, k, cost), val(cur, k, rev)
            q0, c0, r0 = val(plan, k, pbasis), val(plan, k, pcost), val(plan, k, prev)
            cost_cf[k] = q1 * (c0 / q0) if q0 > 0 and q1 > 0 else c1
            g1, g0 = val(cur, k, gross), val(plan, k, pgross)
            rev_cf[k] = g1 * (r0 / g0) if gross and pgross and g0 > 0 and g1 > 0 else r1
            cost_k[k] = cost_cf[k] - c1
            disc_k[k] = r1 - rev_cf[k]
            rest_k[k] = (r1 - c1) - (r0 - c0) - cost_k[k] - disc_k[k]
        cost_eff, disc_eff = sum(cost_k.values()), sum(disc_k.values())
        total = parent.current - parent.baseline
        rest = total - cost_eff - disc_eff
        c1_tot = sum(val(cur, k, cost) for k in keys)
        r1_tot = sum(val(cur, k, rev) for k in keys)
        g1_tot = sum(val(cur, k, gross) for k in keys) if gross else 0.0
        c_cf, r_cf = sum(cost_cf.values()), sum(rev_cf.values())
        sign = self.root_sign.get(parent.id, 1)
        corroborations = self._bridge_corroborations(p, c1_tot - c_cf, r1_tot - r_cf, ids)
        data = {
            "method": "counterfactual_bridge",
            "mode": "plan",
            "cell_dimensions": dims,
            "cells": len(keys),
            "revenue_current": r1_tot,
            "revenue_plan": sum(val(plan, k, prev) for k in keys),
            "cost_current": c1_tot,
            "cost_plan": sum(val(plan, k, pcost) for k in keys),
            "cost_at_plan_unit_costs": c_cf,
            "revenue_at_plan_discount_rates": r_cf if gross else None,
            "margin_current": parent.current,
            "margin_plan": parent.baseline,
            "parent_current": parent.current,
            "parent_baseline": parent.baseline,
            "parent_change": total,
            "unit_cost_effect": cost_eff,
            "discount_effect": disc_eff if gross else None,
            "mix_and_other_effect": rest,
            "total_change": total,
            "effects": [
                {"metric_id": "unit_cost", "effect": cost_eff, "share": safe_share(cost_eff, total)},
                *(
                    [{"metric_id": "discounting", "effect": disc_eff, "share": safe_share(disc_eff, total)}]
                    if gross
                    else []
                ),
                {"metric_id": "volume_mix_other", "effect": rest, "share": safe_share(rest, total)},
            ],
        }
        art = self.metric_result(
            f"{m.display_name} vs plan: unit cost, discounting, volume and mix",
            data,
            ids,
            chart=charts.waterfall_chart(
                f"{m.display_name}: plan to actual",
                "Plan",
                window.display(),
                parent.baseline,
                parent.current,
                [
                    ("unit cost", cost_eff),
                    *([("discounting", disc_eff)] if gross else []),
                    ("volume, mix and other", rest),
                ],
            ),
            filters=list(self.ctx.filters),
            window=window,
            metric_ids=[mid, *actual, *planned],
        )
        noun = self.gap_noun(mid, total, root=parent.id == self.root_id)
        method_label = f"exact counterfactual bridge per plan cell ({where}), unit costs then discount rates held at plan"

        def share_text(eff: float) -> str:
            share = safe_share(eff, total)
            if share is None:
                return "."
            return (
                f"; that is {fmt_share(share)} of the {m.display_name} {noun}."
                if share >= 0
                else f", offsetting {fmt_share(abs(share))} of the {m.display_name} {noun}."
            )

        def moved(eff: float) -> str:
            return f"{'cut' if eff < 0 else 'added'} {fmt_value(abs(eff), m.format)} {'from' if eff < 0 else 'to'} {m.display_name}"

        cost_rate = {}
        disc_rate = {}
        for k in keys:
            q1, q0 = val(cur, k, basis), val(plan, k, pbasis)
            cost_rate[k] = val(plan, k, pcost) / q0 if q0 > 0 and q1 > 0 else None
            g1, g0 = val(cur, k, gross), val(plan, k, pgross)
            disc_rate[k] = val(plan, k, prev) / g0 if gross and pgross and g0 > 0 and g1 > 0 else None
        below = [d for d in p.get("below_grain_dimensions", []) if d not in dims]
        grain_metrics = list(dict.fromkeys([x for x in (rev, cost, basis, gross) if x]))

        cm = self.model.get_metric(cost)
        per_list = p.get("cost_basis_metric")
        at_plan = (
            f"at plan {self.metric_label(per_list)} (cost per list-price dollar, robust to unlike units in a cell)"
            if per_list
            else "at plan unit costs"
        )
        cost_node = TreeNode(
            id=stable_id("node", "bridge", parent.id, "unit_cost"),
            parent_id=parent.id,
            kind="driver",
            title="Unit-cost inflation" if cost_eff < 0 else "Unit-cost savings",
            statement=(
                f"{'Higher' if cost_eff < 0 else 'Lower'} unit costs {moved(cost_eff)}: what was sold cost "
                f"{fmt_abs_pct(safe_pct(c1_tot, c_cf))} {'more' if c1_tot >= c_cf else 'less'} than the plan's cost "
                f"rates{share_text(cost_eff)}"
            ),
            method=(
                "Counterfactual per plan cell ("
                + where
                + "): each cell's actual "
                + ("list-price sales" if per_list else "units")
                + f" {at_plan}; the unit-cost effect is that cost minus the recorded cost. Unit costs are applied "
                "before discount rates; the three effects add up exactly to the gap."
            ),
            statement_type="supported_explanation",
            metric_id=cost,
            metric_label="Unit cost",
            metric_format=cm.format,
            current=c1_tot,
            baseline=c_cf,
            baseline_label="At plan cost rates",
            abs_change=c1_tot - c_cf,
            pct_change=safe_pct(c1_tot, c_cf),
            contribution_to_parent=Contribution(
                effect=cost_eff, share=safe_share(cost_eff, total), method="additive_identity"
            ),
            root_effect=sign * cost_eff,
            evidence_strength="moderate",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
            notes=[
                "counterfactual: each cell's actual "
                + ("list-price sales" if per_list else "units")
                + f" {at_plan} (plan cost / plan "
                + ("list-price sales)" if per_list else "units)")
            ],
        )
        self.add_node(
            cost_node,
            EvidenceInput(
                executed=True,
                method_label=method_label,
                share=safe_share(cost_eff, total),
                corroborations=corroborations["cost"],
            ),
        )
        self.root_sign[cost_node.id] = sign

        def cost_measure(ks: list[CellKey]) -> tuple[float, float, str]:
            a = sum(val(cur, k, cost) for k in ks)
            b = sum(cost_cf[k] for k in ks)
            return a, b, f"costs {fmt_abs_pct(safe_pct(a, b))} {'above' if a >= b else 'below'} plan"

        self._cell_breakdown(
            cost_node,
            "cost",
            cost_k,
            {k: val(cur, k, cost) for k in keys},
            dims,
            [],
            1,
            art.id,
            step,
            cost_measure,
            corroborate={"metric_id": p.get("unit_cost_metric"), "adverse_when": "up"},
            grain={
                "kind": "cost",
                "cells": dims,
                "candidates": below,
                "metrics": grain_metrics,
                "cost": cost,
                "basis": basis,
                "gross": gross,
                "rev": rev,
                "rate": cost_rate,
            },
        )
        if gross and pgross:
            d_rate = p.get("discount_rate")
            dm = self.model.get_metric(d_rate or p.get("discount") or rev)
            d1 = 1 - r1_tot / g1_tot if g1_tot else None
            d_cf = 1 - r_cf / g1_tot if g1_tot else None
            disc_node = TreeNode(
                id=stable_id("node", "bridge", parent.id, "discount"),
                parent_id=parent.id,
                kind="driver",
                title="Increased discounting" if disc_eff < 0 else "Reduced discounting",
                statement=(
                    f"{'Deeper' if disc_eff < 0 else 'Lighter'} discounts {moved(disc_eff)}: the average discount was "
                    f"{fmt_value(d1, 'percent')} against {fmt_value(d_cf, 'percent')} at the plan's rates"
                    f"{share_text(disc_eff)}"
                ),
                method=(
                    "Counterfactual per plan cell ("
                    + where
                    + "): each cell's actual list-price sales at that cell's "
                    f"plan discount rate would have brought {fmt_value(r_cf, 'currency')} instead of "
                    f"{fmt_value(r1_tot, 'currency')}; the difference is the discount effect."
                ),
                statement_type="supported_explanation",
                metric_id=dm.id,
                metric_label="Discounting",
                metric_format="percent",
                current=d1,
                baseline=d_cf,
                baseline_label="At plan discount rates",
                abs_change=(d1 - d_cf) if d1 is not None and d_cf is not None else None,
                pct_change=safe_pct(d1, d_cf),
                contribution_to_parent=Contribution(
                    effect=disc_eff, share=safe_share(disc_eff, total), method="additive_identity"
                ),
                root_effect=sign * disc_eff,
                evidence_strength="moderate",
                artifact_ids=[art.id, *ids],
                step_id=step.id,
                notes=[
                    "counterfactual: each cell's actual list-price sales at that cell's plan discount rate"
                ],
            )
            self.add_node(
                disc_node,
                EvidenceInput(
                    executed=True,
                    method_label=method_label,
                    share=safe_share(disc_eff, total),
                    corroborations=corroborations["discount"],
                ),
            )
            self.root_sign[disc_node.id] = sign

            def disc_measure(ks: list[CellKey]) -> tuple[float, float, str]:
                g = sum(val(cur, k, gross) for k in ks)
                a = 1 - sum(val(cur, k, rev) for k in ks) / g if g else 0.0
                b = 1 - sum(rev_cf[k] for k in ks) / g if g else 0.0
                return (
                    a,
                    b,
                    f"discount {fmt_value(a, 'percent')} against a planned {fmt_value(b, 'percent')}",
                )

            self._cell_breakdown(
                disc_node,
                "discount",
                disc_k,
                {k: val(cur, k, gross) for k in keys},
                dims,
                [],
                1,
                art.id,
                step,
                disc_measure,
                fmt="percent",
                corroborate={"metric_id": p.get("discount_rate"), "adverse_when": "up"},
                grain={
                    "kind": "discount",
                    "cells": dims,
                    "candidates": below,
                    "metrics": grain_metrics,
                    "cost": cost,
                    "basis": basis,
                    "gross": gross,
                    "rev": rev,
                    "rate": disc_rate,
                },
            )
        rest_node = TreeNode(
            id=stable_id("node", "bridge", parent.id, "rest"),
            parent_id=parent.id,
            kind="other",
            title="Favorable volume and mix" if rest >= 0 else "Unfavorable volume and mix",
            statement=f"Volume and mix against plan {moved(rest)}{share_text(rest)}",
            method=(
                "The remainder of the exact bridge once unit costs and discount rates are held at plan: volume, "
                "product and customer mix, and list prices. Not a single measured cause."
            ),
            statement_type="observation",
            metric_id=mid,
            metric_label="Volume, mix and other",
            metric_format=m.format,
            contribution_to_parent=Contribution(
                effect=rest, share=safe_share(rest, total), method="additive_identity"
            ),
            root_effect=sign * rest,
            evidence_strength="moderate",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
            notes=[
                "a remainder of the exact bridge (volume, product and customer mix, list prices), not a single cause"
            ],
        )
        self.add_node(
            rest_node,
            EvidenceInput(
                executed=True,
                method_label="remainder of the exact bridge",
                share=safe_share(rest, total),
                max_strength="moderate",
            ),
        )
        self.root_sign[rest_node.id] = sign

    def _bridge_corroborations(
        self, p: dict[str, Any], cost_gap: float, rev_gap: float, ids: list[str]
    ) -> dict[str, list[str]]:
        """Independent tests of the bridge's direction: recorded unit costs and the discount rate
        moved the same way against the same period a year earlier (separate queries)."""
        out: dict[str, list[str]] = {"cost": [], "discount": []}
        prior = previous_period(self.ctx.window, "yoy")
        for key, mid, gap in (
            ("cost", p.get("unit_cost_metric"), cost_gap),
            ("discount", p.get("discount_rate"), -rev_gap),
        ):
            if not mid or abs(gap) < 1e-9:
                continue
            a = self.metric_query(
                [mid],
                self.ctx.window,
                filters=list(self.ctx.filters),
                title=f"{self.metric_label(mid)}, {self.ctx.window.display()}",
            )
            b = self.metric_query(
                [mid],
                prior,
                filters=list(self.ctx.filters),
                title=f"{self.metric_label(mid)}, {prior.display()}",
            )
            if not a.ok or not b.ok:
                continue
            ids.extend([a.artifact.id, b.artifact.id])
            av, bv = _to_float(a.rows[0].get(mid)), _to_float(b.rows[0].get(mid))
            if av is None or bv is None:
                continue
            fmt = self.model.get_metric(mid).format
            if (av - bv) * gap > 0:
                out[key].append(
                    f"recorded {self.metric_label(mid)} {'rose' if av > bv else 'fell'} from {fmt_value(bv, fmt)} "
                    f"to {fmt_value(av, fmt)} between {prior.display()} and {self.ctx.window.display()} "
                    "(separate query)"
                )
        return out

    def _cell_breakdown(
        self,
        parent: TreeNode,
        what: str,
        values: dict[CellKey, float],
        weights: dict[CellKey, float],
        dims: list[str],
        fixed: list[Segment],
        depth: int,
        art_id: str,
        step: PlanStep,
        measure: Measure,
        fmt: str | None = None,
        effect_title: str | None = None,
        corroborate: dict[str, Any] | None = None,
        grain: dict[str, Any] | None = None,
    ) -> bool:
        """Split a bridge effect across the cell dimensions (the parts add up to the effect).

        The dimension shown is the one where the effect is most disproportionate to the cells'
        size (total-variation distance, as for breakdowns); its largest segments are drilled
        the same way while the split stays informative, up to ``drill_depth``. A segment that
        still carries at least ``drill_min_share`` of the effect and is not split further by a
        plan dimension is split below plan grain (see :meth:`_below_grain`). Returns whether a
        breakdown was added.
        """
        fixed_dims = {s.dimension: s.value for s in fixed}
        index = {d: i for i, d in enumerate(dims)}
        cells = [k for k in values if all(k[index[d]] == v for d, v in fixed_dims.items())]
        total = sum(values[k] for k in cells)
        if abs(total) < 1e-9 or not cells:
            return False
        best: tuple[float, str, dict[str | None, list[CellKey]]] | None = None
        corroborate = corroborate or {}
        for d in dims:
            if d in fixed_dims:
                continue
            groups: dict[str | None, list[CellKey]] = {}
            for k in cells:
                groups.setdefault(k[index[d]], []).append(k)
            if len(groups) < 2:
                continue
            wsum = sum(abs(weights.get(k, 0.0)) for k in cells) or 1.0
            power = 0.5 * sum(
                abs(sum(values[k] for k in ks) / total - sum(abs(weights.get(k, 0.0)) for k in ks) / wsum)
                for ks in groups.values()
            )
            if best is None or power > best[0] + 1e-12:
                best = (power, d, groups)
        if best is None or (depth > 1 and best[0] < self.cfg.drill_min_power):
            return False
        power, dim, groups = best
        wsum = sum(abs(weights.get(k, 0.0)) for k in cells) or 1.0
        rows = []
        for g, ks in groups.items():
            a, b, extra = measure(ks)
            rows.append(
                (
                    g,
                    sum(values[k] for k in ks),
                    sum(abs(weights.get(k, 0.0)) for k in ks) / wsum,
                    a,
                    b,
                    extra,
                    ks,
                )
            )
        effect_title = effect_title or parent.title or parent.metric_label or ""
        made = self._effect_group(
            parent,
            what,
            dim,
            rows,
            total,
            fixed,
            art_id,
            step,
            effect_title,
            fmt,
            corroborate,
            power,
            "segment effects add up exactly to the parent effect (sums over plan cells)",
        )
        for rank, (node, share, base_share, _key) in enumerate(made, start=1):
            deeper = False
            seg = node.segment
            assert seg is not None
            if (
                depth < self.cfg.drill_depth
                and share is not None
                and share >= self.cfg.drill_min_share
                and share - base_share >= 0.05
                and rank <= self.cfg.drill_top_n
            ):
                deeper = self._cell_breakdown(
                    node,
                    what,
                    values,
                    weights,
                    dims,
                    [*fixed, seg],
                    depth + 1,
                    art_id,
                    step,
                    measure,
                    fmt,
                    effect_title,
                    corroborate,
                    grain,
                )
            if not deeper and grain and share is not None and share >= self.cfg.drill_min_share:
                self._below_grain(node, grain, [*fixed, seg], art_id, step, effect_title, fmt, corroborate)
        return True

    def _effect_group(
        self,
        parent: TreeNode,
        what: str,
        dim: str,
        rows: list[tuple[Any, float, float, float, float, str, Any]],
        total: float,
        fixed: list[Segment],
        art_id: str,
        step: PlanStep,
        effect_title: str,
        fmt: str | None,
        corroborate: dict[str, Any],
        power: float,
        note: str,
    ) -> list[tuple[TreeNode, float | None, float, Any]]:
        """A breakdown of one effect by ``dim``: rows of (value, effect, size share, current,
        baseline, plain measure, key). The largest segments become nodes (the rest one "other"
        node); the parts add up to ``total``. Returns (node, share, size share, key) per segment."""
        root_m = self.model.get_metric(self.ctx.metric_id)
        sign = self.root_sign.get(parent.id, 1)
        ranked = sorted(rows, key=lambda x: (-abs(x[1]), str(x[0])))
        material = [x for x in ranked if abs(x[1]) >= MATERIALITY * abs(total)]
        top = material[: self.cfg.top_n]
        rest = [x for x in ranked if x not in top and abs(x[1]) > 1e-9]
        if not top:
            return []
        lead = top[0]
        words = self.dim_words(dim)
        effect_lower = (
            effect_title[:1].lower() + effect_title[1:] if effect_title[1:2].islower() else effect_title
        )
        if fixed:
            effect_lower += f" in {self._seg_label(fixed[-1].value)}"
        # The group sits under the segment it splits, so its title names only the dimension; the
        # statement says where ("Unit-cost inflation in Lumber by supplier").
        group = TreeNode(
            id=stable_id("node", "dim", parent.id, what, dim),
            parent_id=parent.id,
            kind="dimension",
            title=f"{effect_title} by {words}",
            statement=(
                f"{effect_title}"
                + (f" in {self._seg_label(fixed[-1].value)}" if fixed else "")
                + f" by {words}: the largest part is {self._seg_label(lead[0])} "
                f"({fmt_share(safe_share(lead[1], total))})."
            ),
            statement_type="observation",
            metric_id=parent.metric_id,
            metric_label=parent.metric_label,
            metric_format=root_m.format,
            dimension=dim,
            segment_path=list(fixed),
            evidence_strength="strong",
            artifact_ids=[art_id],
            step_id=step.id,
            explanatory_power=power,
            notes=[note],
        )
        self.add_node(group, EvidenceInput(executed=True, measurement_only=True))
        made: list[tuple[TreeNode, float | None, float, Any]] = []
        for rank, (g, eff, base_share, a, b, extra, key) in enumerate(top, start=1):
            share = safe_share(eff, total)
            seg = Segment(dimension=dim, value=g)
            of_effect = (
                f" ({fmt_share(share)} of {effect_lower})"
                if share is not None and share >= 0
                else (
                    f" (offsets {fmt_share(abs(share or 0.0))} of {effect_lower})"
                    if share is not None
                    else ""
                )
            )
            node = TreeNode(
                id=stable_id("node", "seg", group.id, g),
                parent_id=group.id,
                kind="segment",
                title=self._seg_label(g),
                statement=(
                    f"{self._seg_label(g)}: {extra}; "
                    f"{fmt_change(sign * eff, root_m.format)} of {root_m.display_name}{of_effect}."
                ),
                statement_type="supported_explanation",
                metric_id=parent.metric_id,
                metric_label=parent.metric_label,
                metric_format=fmt or parent.metric_format,
                segment=seg,
                segment_path=[*fixed, seg],
                dimension=dim,
                current=a,
                baseline=b,
                baseline_label=parent.baseline_label,
                abs_change=a - b,
                pct_change=safe_pct(a, b),
                contribution_to_parent=Contribution(effect=eff, share=share, method="additive_identity"),
                root_effect=sign * eff + 0.0,
                evidence_strength="moderate",
                artifact_ids=[art_id],
                step_id=step.id,
                rank=rank,
                method=parent.method,
            )
            self.add_node(
                node,
                EvidenceInput(
                    executed=True,
                    method_label=f"exact split of the {effect_lower} effect",
                    share=share,
                    base_share=base_share,
                    pct_change=safe_pct(a, b),
                    corroborations=self._segment_corroboration(corroborate, [*fixed, seg], eff, node)
                    if share is not None and share >= MODERATE_SHARE
                    else [],
                ),
            )
            self.root_sign[node.id] = sign
            made.append((node, share, base_share, key))
        if rest:
            eff = sum(x[1] for x in rest)
            other = TreeNode(
                id=stable_id("node", "other", group.id),
                parent_id=group.id,
                kind="other",
                title=f"{len(rest)} other {self.dim_words(dim, count=len(rest))}",
                statement=(
                    f"{len(rest)} other {self.dim_words(dim, count=len(rest))}: "
                    f"{fmt_change(sign * eff, root_m.format)} of {root_m.display_name}."
                ),
                statement_type="supported_explanation",
                metric_id=parent.metric_id,
                dimension=dim,
                segment_path=list(fixed),
                contribution_to_parent=Contribution(
                    effect=eff, share=safe_share(eff, total), method="additive_identity"
                ),
                root_effect=sign * eff + 0.0,
                evidence_strength="weak",
                artifact_ids=[art_id],
                step_id=step.id,
                rank=len(top) + 1,
            )
            self.add_node(
                other,
                EvidenceInput(executed=True, share=safe_share(eff, total), method_label="grouped remainder"),
            )
        return made

    def _below_grain(
        self,
        node: TreeNode,
        grain: dict[str, Any],
        path: list[Segment],
        art_id: str,
        step: PlanStep,
        effect_title: str,
        fmt: str | None,
        corroborate: dict[str, Any],
    ) -> None:
        """Split a segment's bridge effect by a dimension the plan does not carry, exactly.

        The plan has no rate per member of that dimension (supplier, sales rep), so each member's
        plan rate in a plan cell is its own prior-year rate (same dates a year earlier) scaled by
        the cell's planned change: P_rate_s = PY_rate_s x P_rate_cell / PY_rate_cell. The
        assumption: the plan's change of a cell's rate applies uniformly to its members. Costing
        every member at the cell's blended plan rate instead would book standing differences
        between members (one supplier is always dearer per list dollar) as cost changes.
        cost effect_s = actual basis_s x P_rate_s - actual cost_s; discount effect_s = actual
        revenue_s - actual list-price sales_s x P_rate_s; the remainder, the shift in member mix
        inside cells, is its own node so the parts add up exactly to the segment's effect. A
        member with no prior-year sales in a cell uses the cell's plan rate; when less than half of
        the segment has a prior-year base the dimension is not used.

        Candidates are the dimensions of the actual metrics below plan grain with 2 to
        ``BELOW_GRAIN_MAX_SEGMENTS`` values in the segment; the one whose split is most
        disproportionate to size (total-variation distance) is kept when that index is at least
        ``drill_min_power`` and its largest member explains at least ``drill_min_share``; ties go
        to the dimension with fewer values. Otherwise no split is shown.
        """
        if node.contribution_to_parent is None:
            return
        target = node.contribution_to_parent.effect
        if abs(target) < 1e-9:
            return
        cells: list[str] = grain["cells"]
        kind: str = grain["kind"]
        window = self.ctx.window
        prior = previous_period(window, "yoy")
        num_key, den_key = (
            (grain["cost"], grain["basis"]) if kind == "cost" else (grain["rev"], grain["gross"])
        )
        best: tuple[float, int, int, str, dict[str | None, list[float]], float, list[str]] | None = None
        skipped: list[str] = []
        for order, d in enumerate(grain["candidates"]):
            if any(s.dimension == d for s in path):
                continue

            def query(win: TimeWindow, d: str = d) -> QueryOutcome:
                return self.metric_query(
                    grain["metrics"],
                    win,
                    dims=[*cells, d],
                    filters=self.filters_for(path),
                    title=f"{', '.join(self.metric_label(x) for x in grain['metrics'])} by "
                    f"{' x '.join(self.dim_label(x) for x in [*cells, d])} "
                    f"[{', '.join(s.label() for s in path)}], {win.display()}",
                    expect="many",
                )

            out = query(window)
            if not out.ok:
                continue
            # The prior-year anchor is only worth a query for a dimension of usable cardinality.
            if not 2 <= len({_seg_key(r.get(d)) for r in out.rows}) <= BELOW_GRAIN_MAX_SEGMENTS:
                continue
            py = query(prior)
            py_rows = py.rows if py.ok else []
            py_num: dict[tuple[Any, ...], float] = {}
            py_den: dict[tuple[Any, ...], float] = {}
            for r in py_rows:
                cell = tuple(_seg_key(r.get(c)) for c in cells)
                key = (cell, _seg_key(r.get(d)))
                py_num[key] = py_num.get(key, 0.0) + (_to_float(r.get(num_key)) or 0.0)
                py_den[key] = py_den.get(key, 0.0) + (_to_float(r.get(den_key)) or 0.0)
            cell_num: dict[tuple[Any, ...], float] = {}
            cell_den: dict[tuple[Any, ...], float] = {}
            for (cell, _m), v in py_num.items():
                cell_num[cell] = cell_num.get(cell, 0.0) + v
            for (cell, _m), v in py_den.items():
                cell_den[cell] = cell_den.get(cell, 0.0) + v
            # per member: [effect, size, actual cost or discount, the same at its plan rate, list-price sales]
            members: dict[str | None, list[float]] = {}
            mix = covered = size = 0.0
            for r in out.rows:
                cell = tuple(_seg_key(r.get(c)) for c in cells)
                member = _seg_key(r.get(d))
                p_cell = grain["rate"].get(cell)
                g = (_to_float(r.get(grain["gross"])) or 0.0) if grain.get("gross") else 0.0
                den = _to_float(r.get(den_key)) or 0.0
                num = _to_float(r.get(num_key)) or 0.0
                weight = num if kind == "cost" else g
                size += abs(weight)
                p_member = p_cell
                if p_cell is not None:
                    py_m = (py_num.get((cell, member), 0.0), py_den.get((cell, member), 0.0))
                    py_c = (cell_num.get(cell, 0.0), cell_den.get(cell, 0.0))
                    if py_m[1] > 0 and py_c[1] > 0 and py_c[0] > 0:
                        p_member = (py_m[0] / py_m[1]) * p_cell / (py_c[0] / py_c[1])
                        covered += abs(weight)
                if kind == "cost":
                    actual = num
                    at_plan = den * p_member if p_member is not None else actual
                    mix += den * ((p_cell or 0.0) - (p_member or 0.0)) if p_cell is not None else 0.0
                else:
                    actual = g - num
                    at_plan = g - g * p_member if p_member is not None else actual
                    mix += g * ((p_member or 0.0) - (p_cell or 0.0)) if p_cell is not None else 0.0
                acc = members.setdefault(member, [0.0, 0.0, 0.0, 0.0, 0.0])
                acc[0] += at_plan - actual
                acc[1] += abs(weight)
                acc[2] += actual
                acc[3] += at_plan
                acc[4] += g
            if not 2 <= len(members) <= BELOW_GRAIN_MAX_SEGMENTS:
                continue
            if size and covered / size < 0.5:
                skipped.append(self.dim_label(d))
                continue
            if abs(sum(v[0] for v in members.values()) + mix - target) > max(0.01, 1e-6 * abs(target)):
                continue  # the rows do not reproduce the segment's effect: no exact split
            wsum = sum(v[1] for v in members.values()) or 1.0
            power = 0.5 * sum(abs(v[0] / target - v[1] / wsum) for v in members.values())
            lead_share = max(v[0] / target for v in members.values())
            if power < self.cfg.drill_min_power or lead_share < self.cfg.drill_min_share:
                continue
            rank_key = (-round(power, 9), len(members), order)
            if best is None or rank_key < (-round(best[0], 9), best[1], best[2]):
                best = (power, len(members), order, d, members, mix, [out.artifact.id, py.artifact.id])
        if best is None:
            if skipped:
                node.notes.append(
                    f"not split by {', '.join(skipped)}: less than half of it has a prior-year base to anchor "
                    "each member's plan rate"
                )
            return
        power, _n, _o, dim, members, mix, qids = best
        rows = []
        for value, (eff, w, cur, at_plan, g) in members.items():
            if kind == "cost":
                a, b = cur, at_plan
                extra = f"costs {fmt_abs_pct(safe_pct(a, b))} {'above' if a >= b else 'below'} plan"
            else:
                a, b = (cur / g, at_plan / g) if g else (0.0, 0.0)  # discount rates on its list-price sales
                extra = f"discount {fmt_value(a, 'percent')} against a planned {fmt_value(b, 'percent')}"
            rows.append((value, eff, w / (sum(v[1] for v in members.values()) or 1.0), a, b, extra, None))
        split = self.metric_result(
            f"{effect_title} in {', '.join(self._seg_label(s.value) for s in path)} by {self.dim_label(dim)}",
            {
                "method": "below_plan_grain_split",
                "dimension": dim,
                "segment_path": [s.model_dump() for s in path],
                "total_effect": target,
                "member_mix_effect": mix,
                "plan_rate": "prior-year rate of the member x plan rate of the cell / prior-year rate of the cell",
                "rows": [{"segment": v, "effect": e, "size_share": w} for v, e, w, *_r in rows],
            },
            [*qids, art_id],
            chart=charts.contribution_chart(
                f"{effect_title} by {self.dim_label(dim)}", [(self._seg_label(v), e) for v, e, *_r in rows]
            ),
            filters=self.filters_for(path),
            window=window,
            metric_ids=list(grain["metrics"]),
        )
        label = self.dim_words(dim)
        made = self._effect_group(
            node,
            f"{kind}_below",
            dim,
            rows,
            target,
            path,
            split.id,
            step,
            effect_title,
            fmt if kind != "cost" else node.metric_format,
            corroborate,
            power,
            f"split below plan grain by {label}: the parts and the {label} mix add up exactly to the "
            "segment's effect",
        )
        method = (
            f"The plan has no {label}, so each {label}'s plan "
            + ("cost rate" if kind == "cost" else "discount rate")
            + " in a plan cell is its own rate a year earlier scaled by the cell's planned change (the plan's change "
            f"is assumed to apply uniformly to every {label} in the cell); a {label} with no sales a "
            "year earlier takes the cell's plan rate. The shift in "
            f"{label} mix inside cells is shown on its own."
        )
        for seg_node, *_rest in made:
            seg_node.method = method
        if made and abs(mix) > 1e-9:
            group = self.nodes[made[0][0].parent_id or ""]
            root_m = self.model.get_metric(self.ctx.metric_id)
            sign = self.root_sign.get(node.id, 1)
            mix_node = TreeNode(
                id=stable_id("node", "mix", group.id),
                parent_id=group.id,
                kind="other",
                title=f"{label[:1].upper()}{label[1:]} mix",
                statement=(
                    f"Shift in {label} mix within {self._seg_label(path[-1].value)}: "
                    f"{fmt_change(sign * mix, root_m.format)} of {root_m.display_name}."
                ),
                statement_type="supported_explanation",
                metric_id=node.metric_id,
                dimension=dim,
                segment_path=list(path),
                contribution_to_parent=Contribution(
                    effect=mix, share=safe_share(mix, target), method="additive_identity"
                ),
                root_effect=sign * mix + 0.0,
                evidence_strength="weak",
                artifact_ids=[split.id],
                step_id=step.id,
                method=method,
            )
            self.add_node(
                mix_node,
                EvidenceInput(
                    executed=True, share=safe_share(mix, target), method_label="member mix remainder"
                ),
            )

    def _segment_corroboration(
        self, spec: dict[str, Any], path: list[Segment], effect: float, node: TreeNode
    ) -> list[str]:
        """A separate query on another metric that moved the same way in the same segment against
        the same period a year earlier (recorded unit costs for a cost effect, the discount rate
        for a discount effect)."""
        mid = spec.get("metric_id")
        if not mid or abs(effect) < 1e-9 or time_dimension_for_metric(self.model, mid) is None:
            return []
        usable = {d.name for d in dimensions_for_metric(self.model, mid)[0]}
        if any(s.dimension not in usable for s in path):
            return []
        prior = previous_period(self.ctx.window, "yoy")
        cur, base, ids = self._measure_in(mid, path, self.ctx.window, prior)
        if cur is None or base is None:
            return []
        # an adverse margin effect (negative) means the metric rose (costs, discount rates)
        if (cur - base) * (-effect if spec.get("adverse_when") == "up" else effect) <= 0:
            return []
        node.artifact_ids.extend(i for i in ids if i not in node.artifact_ids)
        fmt = self.model.get_metric(mid).format
        where = ", ".join(self._seg_label(s.value) for s in path)
        return [
            f"recorded {self.metric_label(mid)} {'rose' if cur > base else 'fell'} from {fmt_value(base, fmt)} to "
            f"{fmt_value(cur, fmt)} in {where} between {prior.display()} and {self.ctx.window.display()} "
            "(separate query)"
        ]

    def variance_drill(self, step: PlanStep) -> None:
        """Break the material leaf variances (fuel surcharge, overtime, a payroll saving) down by
        the conformed dimensions the metric and its plan share, keeping the most concentrated
        breakdown. Siblings reuse the dimension chosen for the larger sibling when it is informative
        for them too, so offsets in the same segment line up (overtime over plan in a branch where
        payroll came in under plan), and those segments point at each other."""
        min_share = float(step.params.get("min_share", self.cfg.drill_min_share))
        leaves = [
            n
            for n in (self.nodes[i] for i in self.order)
            if n.kind == "driver"
            and n.step_id
            and n.step_id.startswith("decompose:")
            and n.root_effect is not None
            and not n.children
            and n.contribution_to_parent is not None
            and abs(n.contribution_to_parent.share or 0.0) >= min_share
            and n.metric_id is not None
            and self.plan_of(n.metric_id) is not None
        ]
        leaves.sort(key=lambda n: (-abs(n.root_effect or 0.0), n.id))
        chosen: dict[str, str] = {}  # parent node id -> dimension used by its first drilled leaf
        kept: dict[str, TreeNode] = {}  # leaf id -> kept group
        for node in leaves[: self.cfg.drill_top_n + 2]:
            mid = node.metric_id or ""
            plan = self.plan_of(mid) or ""
            usable, _ = dimensions_for_metric(self.model, mid)
            names = {d.name for d in dimensions_for_metric(self.model, plan)[0]}
            blocked = pinned_dimensions(self.model, mid) | {
                f.dimension for f in self.ctx.filters if f.op == "eq"
            }
            cands = [
                d.name
                for d in rank_dimensions([d for d in usable if d.name in names], self.template)
                if d.name not in blocked
            ][: self.cfg.drill_dimensions]
            prefer = chosen.get(node.parent_id or "")
            order = [prefer, *(c for c in cands if c != prefer)] if prefer in cands else cands
            groups: list[TreeNode] = []
            for dim in order:
                g = self.contribution(node, mid, dim, [], step, min_segments=2)
                if g is None or g.kind != "dimension":
                    continue
                groups.append(g)
                if dim == prefer and (g.explanatory_power or 0.0) >= self.cfg.drill_min_power:
                    break
            keep = [
                g
                for g in groups
                if g.dimension == prefer and (g.explanatory_power or 0.0) >= self.cfg.drill_min_power
            ]
            keep = keep[:1] or self.top_groups(groups, 1)
            for g in groups:
                if g not in keep:
                    self.remove_subtree(g.id)
            if not keep:
                if groups:
                    node.notes.append(
                        "the gap is spread in proportion to "
                        + ", ".join(self.dim_label(g.dimension or "") for g in groups)
                        + " size; no breakdown concentrates it"
                    )
                continue
            chosen.setdefault(node.parent_id or "", keep[0].dimension or "")
            kept[node.id] = keep[0]
            self._plain_drill(node, keep[0])
        self._link_offsets(kept)

    def _plain_drill(self, line: TreeNode, group: TreeNode) -> None:
        """Rewrite a variance drill in plain words and drop segments with no share of the gap."""
        m = self.model.get_metric(line.metric_id or "")
        noun = self.gap_noun(
            m.id, (line.current or 0.0) - (line.baseline or 0.0), root=line.id == self.root_id
        )
        segs = [self.nodes[c] for c in group.children]
        root_m = self.model.get_metric(self.ctx.metric_id)
        folded: list[TreeNode] = []
        for c in segs:
            share = c.contribution_to_parent.share if c.contribution_to_parent else None
            small = share is None or abs(share) < MATERIALITY
            if (c.kind == "segment" and small) or c.kind == "other":
                if c.contribution_to_parent is not None and share is not None and abs(share) >= 0.0005:
                    folded.append(c)
                self.remove_subtree(c.id)
                continue
            if c.kind != "segment" or c.segment is None or c.current is None or c.baseline is None:
                continue
            var = c.current - c.baseline
            word = (
                ("over" if var > 0 else "under")
                if not m.higher_is_better
                else ("above" if var > 0 else "below")
            )
            tail = (
                f"{fmt_share(share)} of the {m.display_name} {noun}"
                if share is not None and share >= 0
                else f"offsetting {fmt_share(abs(share or 0.0))} of the {m.display_name} {noun}"
            )
            c.statement = (
                f"{self._seg_label(c.segment.value)}: {m.display_name} {fmt_value(abs(var), m.format)} {word} plan "
                f"({fmt_value(c.current, m.format)} against {fmt_value(c.baseline, m.format)}); {tail}."
            )
        if folded:
            eff = sum(f.contribution_to_parent.effect for f in folded if f.contribution_to_parent)
            count = sum(
                int((f.title or "0").split()[0]) if f.kind == "other" and (f.title or "")[:1].isdigit() else 1
                for f in folded
            )
            label = self.dim_words(group.dimension or "", count=count)
            total = (line.current or 0.0) - (line.baseline or 0.0)
            other = TreeNode(
                id=stable_id("node", "other", group.id),
                parent_id=group.id,
                kind="other",
                title=f"{count} other {label}",
                statement=(
                    f"{count} other {label}, each under {MATERIALITY:.0%} of the "
                    f"{m.display_name} {noun}: {fmt_change(self.root_sign.get(line.id, 1) * eff, root_m.format)} of "
                    f"{root_m.display_name}."
                ),
                statement_type="supported_explanation",
                metric_id=m.id,
                dimension=group.dimension,
                contribution_to_parent=Contribution(
                    effect=eff, share=safe_share(eff, total), method="additive"
                ),
                root_effect=self.root_sign.get(line.id, 1) * eff + 0.0,
                evidence_strength="weak",
                artifact_ids=list(group.artifact_ids[:1]),
                step_id=group.step_id,
            )
            self.add_node(
                other,
                EvidenceInput(executed=True, share=safe_share(eff, total), method_label="grouped remainder"),
            )
        lead = next((self.nodes[c] for c in group.children if self.nodes[c].kind == "segment"), None)
        words = self.dim_words(group.dimension or "")
        if lead is not None and lead.segment is not None and lead.contribution_to_parent is not None:
            group.statement = (
                f"{m.display_name} {noun} by {words}: the largest part is "
                f"{self._seg_label(lead.segment.value)} ({fmt_share(lead.contribution_to_parent.share)})."
            )
        group.title = f"{m.display_name} {noun} by {words}"

    def _link_offsets(self, kept: dict[str, TreeNode]) -> None:
        """Sibling lines drilled by the same dimension: a segment where one line hurt profit and
        the other helped (overtime over plan, payroll under plan in the same branch) names the
        offset and points at the other segment."""
        by_parent: dict[str, list[tuple[TreeNode, TreeNode]]] = {}
        for leaf_id, group in kept.items():
            leaf = self.nodes.get(leaf_id)
            if leaf is not None and group.id in self.nodes:
                by_parent.setdefault(leaf.parent_id or "", []).append((leaf, group))
        for pairs in by_parent.values():
            for a_leaf, a_group in pairs:
                for b_leaf, b_group in pairs:
                    if a_leaf.id == b_leaf.id or a_group.dimension != b_group.dimension:
                        continue
                    b_segs = {
                        self.nodes[c].segment.value: self.nodes[c]  # type: ignore[union-attr]
                        for c in b_group.children
                        if self.nodes[c].kind == "segment" and self.nodes[c].segment is not None
                    }
                    for cid in a_group.children:
                        a = self.nodes[cid]
                        if a.kind != "segment" or a.segment is None or a.root_effect is None:
                            continue
                        b = b_segs.get(a.segment.value)
                        if b is None or b.root_effect is None or a.root_effect * b.root_effect >= 0:
                            continue
                        if a.root_effect > 0:
                            continue  # the adverse segment names its offset
                        a.offset_node_id, b.offset_node_id = b.id, a.id
                        fmt = self.model.get_metric(self.ctx.metric_id).format
                        a.statement = (
                            a.statement.rstrip(".")
                            + f"; partly offset in the same {self.dim_words(a.segment.dimension)} by "
                            f"{b_leaf.title or b_leaf.metric_label} of {fmt_value(abs(b.root_effect), fmt)}."
                        )

    def reconcile(self, step: PlanStep) -> None:
        """Sum every identity of the explanation from executed numbers and say whether the parts
        add back to the root gap."""
        assert self.root_id is not None
        root = self.nodes[self.root_id]
        if root.current is None or root.baseline is None or root.root_effect is None:
            return
        for g in [self.nodes[c] for c in root.children]:
            if g.kind == "dimension" and g.metric_id == root.metric_id and g.segment_path == []:
                self._plain_drill(root, g)  # the root's own breakdowns (by region) in plain words
        root_m = self.model.get_metric(self.ctx.metric_id)
        gap = root.current - root.baseline
        tol = max(0.005, 1e-9 * max(abs(root.current), abs(root.baseline), 1.0))
        identities: list[ReconciliationIdentity] = []
        arts: list[str] = []

        def parts(node: TreeNode) -> list[TreeNode]:
            return [
                self.nodes[c]
                for c in node.children
                if self.nodes[c].kind in ("driver", "other") and self.nodes[c].root_effect is not None
            ]

        def visit(node: TreeNode) -> None:
            kids = parts(node)
            if not kids or node.root_effect is None:
                return
            total = sum(k.root_effect or 0.0 for k in kids)
            residual = node.root_effect - total
            methods = {k.contribution_to_parent.method for k in kids if k.contribution_to_parent}
            bridge = any(k.step_id and k.step_id.startswith("margin_bridge") for k in kids)
            identities.append(
                ReconciliationIdentity(
                    parent_node_id=node.id,
                    parent_label=node.title or node.metric_label or "",
                    parent_amount=node.root_effect,
                    components=[
                        ReconciliationLine(
                            node_id=k.id,
                            label=k.title or k.metric_label or "",
                            amount=k.root_effect or 0.0,
                            metric_id=k.metric_id,
                        )
                        for k in kids
                    ],
                    sum_of_components=total,
                    residual=residual,
                    reconciled=abs(residual) <= tol,
                    method="counterfactual bridge" if bridge else ", ".join(sorted(methods)) or "identity",
                )
            )
            for k in kids:
                arts.extend(a for a in k.artifact_ids[:1] if a not in arts)
                visit(k)

        visit(root)
        if not identities:
            root.notes.append("no exact decomposition to reconcile")
            return
        ok = all(i.reconciled for i in identities) and abs(root.root_effect - gap) <= tol
        rec = Reconciliation(
            metric_id=root_m.id,
            metric_label=root_m.display_name,
            metric_format=root_m.format,
            actual=root.current,
            baseline=root.baseline,
            gap=gap,
            tolerance=tol,
            reconciled=ok,
            identities=identities,
        )
        top = identities[0]
        fmt = root_m.format

        def line(i: ReconciliationIdentity) -> str:
            return " + ".join(f"{c.label} ({fmt_change(c.amount, fmt)})" for c in i.components)

        noun = self.gap_noun(root_m.id, gap, root=True)
        parts_text = " and ".join(f"{c.label} ({fmt_change(c.amount, fmt)})" for c in top.components)
        largest = fmt_value(round(max(abs(i.residual) for i in identities), 2), fmt)
        statement = (
            f"The parts add back exactly: {parts_text} make up the full {fmt_change(gap, fmt)} "
            f"{root_m.display_name} {noun}. Every level of the explanation was checked the same way "
            f"({len(identities)} checks; the largest difference is {largest})."
            if ok
            else f"The parts do not add back: {parts_text} make {fmt_change(top.sum_of_components, fmt)} against a "
            f"{root_m.display_name} {noun} of {fmt_change(gap, fmt)} (largest difference {largest})."
        )
        bridge = self._plan_bridge_steps(root, rec)
        art = self.metric_result(
            f"Reconciliation of the {root_m.display_name} {noun}",
            {
                "method": "reconciliation",
                **rec.model_dump(mode="json"),
                "bridge": [st.model_dump(mode="json") for st in bridge.steps] if bridge else [],
            },
            arts,
            chart=bridge.chart if bridge else None,
            filters=list(self.ctx.filters),
            window=self.ctx.window,
            metric_ids=[root_m.id],
        )
        if bridge is not None:
            self.bridge = bridge.model_copy(update={"artifact_id": art.id})
        node = TreeNode(
            id=stable_id("node", "check", self.root_id, step.id),
            parent_id=self.root_id,
            kind="check",
            role="reconciliation",
            title="The explanation adds back to the gap"
            if ok
            else "The explanation does not add back to the gap",
            statement=statement,
            statement_type="observation",
            metric_id=root_m.id,
            metric_label=root_m.display_name,
            metric_format=fmt,
            current=top.sum_of_components,
            baseline=gap,
            abs_change=top.residual,
            evidence_strength="strong",
            artifact_ids=[art.id, *arts],
            step_id=step.id,
            notes=["reconciled" if ok else "not reconciled", f"tolerance {fmt_value(tol, fmt)}"],
            reconciliation=rec,
            method="Each identity is summed from executed numbers: "
            + "; ".join(
                f"{i.parent_label}: {line(i)} = {fmt_change(i.parent_amount, fmt)}" for i in identities
            )
            + f". Tolerance {fmt_value(tol, fmt)}.",
        )
        self.add_node(
            node,
            EvidenceInput(
                executed=True,
                measurement_only=True,
                validation_ok=ok,
                validation_failures=[] if ok else ["an identity does not add back to its parent"],
            ),
        )

    def _plan_bridge_steps(self, root: TreeNode, rec: Reconciliation) -> PlanBridge | None:
        """Plan -> the reconciled parts of the gap -> actual: each first-level part's own parts
        (adverse first, largest first; favorable offsets after; "Other lines, net" last), or the
        part itself when it has none. The steps sum to the gap when the tree reconciles."""
        if root.current is None or root.baseline is None or not rec.identities:
            return None
        by_parent = {i.parent_node_id: i for i in rec.identities}
        steps: list[BridgeStep] = []
        running = root.baseline
        for comp in rec.identities[0].components:
            inner = by_parent.get(comp.node_id)
            group = self.nodes.get(comp.node_id or "")
            group_label = (group.metric_label if group else None) or comp.label
            lines = inner.components if inner is not None else [comp]

            def order(c: ReconciliationLine) -> tuple[int, float]:
                node = self.nodes.get(c.node_id or "")
                last = 1 if node is not None and node.members else 0
                return (last, c.amount if c.amount < 0 else 1e18 - c.amount)

            for c in sorted(lines, key=order):
                running += c.amount
                steps.append(
                    BridgeStep(
                        node_id=c.node_id,
                        label=c.label,
                        amount=c.amount,
                        group_label=group_label,
                        group_node_id=comp.node_id,
                        running_total=running,
                    )
                )
        gap = root.current - root.baseline
        root_m = self.model.get_metric(self.ctx.metric_id)
        reconciled = abs(sum(st.amount for st in steps) - gap) <= rec.tolerance
        return PlanBridge(
            metric_id=root_m.id,
            metric_label=root_m.display_name,
            metric_format=root_m.format,
            start=root.baseline,
            end=root.current,
            gap=gap,
            steps=steps,
            reconciled=reconciled,
            tolerance=rec.tolerance,
            chart=charts.waterfall_chart(
                f"{root_m.display_name}: plan to actual, {self.ctx.window.display()}",
                "Plan",
                "Actual",
                root.baseline,
                root.current,
                [(st.label, st.amount) for st in steps],
            ),
        )

    def related_signals(self, step: PlanStep) -> None:
        """Verified observations on related metrics where the explanation concentrates.

        The metrics come from the semantic model (``related_metrics`` of the metrics the root is
        built from). For each segment that explains at least ``drill_min_share`` of its parent
        effect, each related metric that can be sliced by the segment's dimension is measured
        in that segment over the analysis window and the previous period. A signal must move
        adversely (in the same sense as the root), by at least ``related_min_change``, and at
        least twice as much as the same metric across the business. The strongest signal per
        metric is kept, refined by the metric's own dimensions when one segment carries the move,
        and reported with the metric's own related metrics measured in the same segment. Signals
        are observations: they never enter the reconciled tree and their relationship to the
        root change is stated as not established.
        """
        assert self.root_id is not None
        root = self.nodes[self.root_id]
        metrics = [m for m in step.params.get("metrics", []) if self.model.has_metric(m)]
        if not metrics or root.current is None or root.baseline is None or self.cfg.related_signals == 0:
            return
        root_m = self.model.get_metric(self.ctx.metric_id)
        root_change = root.current - root.baseline
        if abs(root_change) < 1e-12:
            return
        root_bad = self._adverse(root_m.id, root_change)
        keys: dict[tuple[str, str | None], tuple[float, list[str]]] = {}
        for nid in self.order:
            n = self.nodes[nid]
            c = n.contribution_to_parent
            if (
                n.kind != "segment"
                or n.segment is None
                or n.statement_type != "supported_explanation"
                or c is None
                or c.share is None
                or c.share < self.cfg.drill_min_share
            ):
                continue
            weight = abs(n.root_effect if n.root_effect is not None else c.effect)
            k = (n.segment.dimension, n.segment.value)
            w, linked = keys.get(k, (0.0, []))
            keys[k] = (max(w, weight), [*linked, n.id])
        if not keys:
            return
        ranked_keys = sorted(keys.items(), key=lambda kv: (-kv[1][0], kv[0][0], str(kv[0][1])))[:6]
        window, base = self.ctx.window, self.period_baseline()
        found: list[
            tuple[float, str, Segment, list[str], float, float, float | None, float | None, list[str]]
        ] = []
        for mid in metrics:
            m = self.model.get_metric(mid)
            if time_dimension_for_metric(self.model, mid) is None:
                continue
            usable = {d.name for d in dimensions_for_metric(self.model, mid)[0]}
            filters = list(self.ctx.filters)
            a_cur = self.metric_query(
                [mid], window, filters=filters, title=f"{m.display_name}, {window.display()}"
            )
            a_base = self.metric_query(
                [mid], base, filters=filters, title=f"{m.display_name}, {base.display()}"
            )
            ac = _to_float(a_cur.rows[0].get(mid)) if a_cur.ok else None
            ab = _to_float(a_base.rows[0].get(mid)) if a_base.ok else None
            rel_all = safe_pct(ac, ab)
            for (dim, value), (_w, linked) in ranked_keys:
                if dim not in usable:
                    continue
                seg = Segment(dimension=dim, value=value)
                cur_v, base_v, ids = self._measure_in(mid, [seg], window, base)
                if cur_v is None or base_v is None:
                    continue
                rel = safe_pct(cur_v, base_v)
                if rel is None or abs(rel) < self.cfg.related_min_change:
                    continue
                if self._adverse(mid, cur_v - base_v) != root_bad:
                    continue
                if rel_all is not None and rel_all * rel > 0 and abs(rel) < 2 * abs(rel_all):
                    continue
                weight = keys[(dim, value)][0]
                found.append(
                    (
                        weight,
                        mid,
                        seg,
                        linked,
                        cur_v,
                        base_v,
                        ac,
                        ab,
                        [a_cur.artifact.id, a_base.artifact.id, *ids],
                    )
                )
        # The strongest explanation segment a metric moved in (by the segment's effect on the root).
        best: dict[
            str, tuple[float, str, Segment, list[str], float, float, float | None, float | None, list[str]]
        ] = {}
        for f in sorted(found, key=lambda f: (-f[0], f[1], f[2].dimension, str(f[2].value))):
            best.setdefault(f[1], f)
        chosen = sorted(best.values(), key=lambda f: (-f[0], f[1]))[: self.cfg.related_signals]
        noun = self.gap_noun(root_m.id, root_change, root=True)
        for _score, mid, seg, linked, cur_v, base_v, ac, ab, ids in chosen:
            path, cur_v, base_v, more_ids = self._refine_signal(mid, seg, cur_v, base_v, window, base)
            ids = [*ids, *more_ids]
            self._signal_node(
                root, mid, path, cur_v, base_v, ac, ab, ids, linked, window, base, step, noun, seg
            )

    def _refine_signal(
        self, metric_id: str, seg: Segment, cur_v: float, base_v: float, window: TimeWindow, base: TimeWindow
    ) -> tuple[list[Segment], float, float, list[str]]:
        """Narrow a signal to the sub-segment that carries it (e.g. one lead channel inside a
        region) when that sub-segment moved at least 25% more than the segment as a whole and
        held at least 15% of the segment's baseline volume."""
        m = self.model.get_metric(metric_id)
        weight_metric = m.denominator if m.kind == "ratio" and m.denominator else metric_id
        wm = self.model.get_metric(weight_metric)
        if wm.kind == "simple" and wm.agg not in ("sum", "count", "count_distinct"):
            return [seg], cur_v, base_v, []
        usable, _ = dimensions_for_metric(self.model, metric_id)
        blocked = (
            pinned_dimensions(self.model, metric_id)
            | {seg.dimension}
            | {f.dimension for f in self.ctx.filters}
        )
        # Every categorical dimension except outcome states (a lead's status decides conversion)
        dims = [
            d
            for d in rank_dimensions(usable, self.template)
            if d.name not in blocked and "status" not in dimension_roles(d)
        ]
        rel = safe_pct(cur_v, base_v) or 0.0
        best: tuple[float, Segment, float, float] | None = None
        ids: list[str] = []
        for d in dims:
            vc, a1, _e1 = self.by_segment(metric_id, window, d.name, [seg], window.display())
            vb, a2, _e2 = self.by_segment(metric_id, base, d.name, [seg], base.display())
            wb, a3, _e3 = self.by_segment(weight_metric, base, d.name, [seg], base.display())
            ids.extend([a1, a2, a3])
            if vc is None or vb is None or wb is None:
                continue
            wtotal = sum(abs(v) for v in wb.values()) or 1.0
            if len(vb) > 25:
                continue  # individual-level dimensions (reps, customers) are too fine for a signal
            for value, b in vb.items():
                c = vc.get(value)
                if c is None or abs(wb.get(value, 0.0)) / wtotal < 0.15:
                    continue
                r = safe_pct(c, b)
                if r is None or r * rel <= 0 or abs(r) < 1.25 * abs(rel):
                    continue
                if best is None or abs(r) > abs(safe_pct(best[2], best[3]) or 0.0) + 1e-12:
                    best = (abs(r), Segment(dimension=d.name, value=value), c, b)
        if best is None:
            return [seg], cur_v, base_v, ids
        return [seg, best[1]], best[2], best[3], ids

    def _signal_node(
        self,
        parent: TreeNode,
        metric_id: str,
        path: list[Segment],
        cur_v: float,
        base_v: float,
        all_cur: float | None,
        all_base: float | None,
        ids: list[str],
        linked: list[str],
        window: TimeWindow,
        base: TimeWindow,
        step: PlanStep,
        noun: str,
        shared: Segment,
    ) -> None:
        m = self.model.get_metric(metric_id)
        root_m = self.model.get_metric(self.ctx.metric_id)
        where = ", ".join(self._seg_label(s.value) for s in path)
        verb = "fell" if cur_v < base_v else "rose"
        overall = (
            f"; across the business {fmt_value(all_base, m.format, m.unit)} to {fmt_value(all_cur, m.format, m.unit)}"
            if all_cur is not None and all_base is not None
            else ""
        )
        not_established = f"Relationship to the {root_m.display_name} {noun}: not established."
        statement = (
            f"{where}: {m.display_name} {verb} from {fmt_value(base_v, m.format, m.unit)} "
            f"to {fmt_value(cur_v, m.format, m.unit)} ({window.display()} vs {base.display()}{overall}). {not_established}"
        )
        effects: list[str] = []
        for i in linked:
            owner = self.nodes.get(i)
            while owner is not None and owner.kind not in ("driver", "other", "root"):
                owner = self.nodes.get(owner.parent_id or "")
            if owner is not None and owner.kind != "root" and (owner.title or owner.metric_label):
                effects.append(f'"{owner.title or owner.metric_label}"')
        effects = list(dict.fromkeys(effects))
        where_exp = (
            f"where {' and '.join(effects)} {'is' if len(effects) == 1 else 'are'} concentrated"
            if effects
            else f"where the {root_m.display_name} {noun} is concentrated"
        )
        relationship_note = (
            f"A verified operating signal in {self._seg_label(shared.value)}, {where_exp}; the available data "
            f"does not establish it as a cause of the {root_m.display_name} {noun}."
        )
        art = self.metric_result(
            f"Related signal: {m.display_name} in {where}",
            {
                "method": "related_signal",
                "metric_id": metric_id,
                "segment_path": [s.model_dump() for s in path],
                "current": cur_v,
                "baseline": base_v,
                "all_current": all_cur,
                "all_baseline": all_base,
                "relationship": "not_established",
                "shared_with": shared.model_dump(),
                "linked_node_ids": linked,
            },
            ids,
            chart=charts.comparison_chart(m.display_name, base.display(), window.display(), base_v, cur_v),
            filters=self.filters_for(path),
            window=window,
            metric_ids=[metric_id],
        )
        node = TreeNode(
            id=stable_id("node", "signal", parent.id, metric_id, [s.model_dump() for s in path]),
            parent_id=parent.id,
            kind="check",
            role="related_signal",
            relationship="not_established",
            title=f"{m.display_name} {verb} in {where}",
            statement=statement,
            statement_type="observation",
            metric_id=metric_id,
            metric_label=m.display_name,
            metric_format=m.format,
            metric_unit=m.unit,
            segment=path[-1],
            segment_path=list(path),
            current=cur_v,
            baseline=base_v,
            baseline_label=base.display(),
            abs_change=cur_v - base_v,
            pct_change=safe_pct(cur_v, base_v),
            evidence_strength="strong",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
            notes=[
                relationship_note,
                "related signal: an observation, not part of the reconciled explanation",
            ],
        )
        self.add_node(node, EvidenceInput(executed=True, measurement_only=True))
        supporting: list[str] = []
        for rid in m.related_metrics:
            if not self.model.has_metric(rid) or time_dimension_for_metric(self.model, rid) is None:
                continue
            usable = {d.name for d in dimensions_for_metric(self.model, rid)[0]}
            if any(s.dimension not in usable for s in path):
                continue
            rc, rb, rids = self._measure_in(rid, path, window, base)
            rel = safe_pct(rc, rb)
            if rc is None or rb is None or rel is None or abs(rel) < self.cfg.related_min_change:
                continue
            if self._adverse(rid, rc - rb) != self._adverse(metric_id, cur_v - base_v):
                continue
            rm = self.model.get_metric(rid)
            rname = self.unit_label(rid)
            rverb = "fell" if rc < rb else "rose"
            child = TreeNode(
                id=stable_id("node", "signal", node.id, rid),
                parent_id=node.id,
                kind="check",
                role="related_signal",
                relationship="not_established",
                title=f"{rname} {rverb} in {where}",
                statement=(
                    f"{where}: {rname} {rverb} from {fmt_value(rb, rm.format, rm.unit)} "
                    f"to {fmt_value(rc, rm.format, rm.unit)} ({window.display()} vs {base.display()}). {not_established}"
                ),
                statement_type="observation",
                metric_id=rid,
                metric_label=rname,
                metric_format=rm.format,
                metric_unit=rm.unit,
                segment=path[-1],
                segment_path=list(path),
                current=rc,
                baseline=rb,
                baseline_label=base.display(),
                abs_change=rc - rb,
                pct_change=rel,
                evidence_strength="strong",
                artifact_ids=rids,
                step_id=step.id,
                notes=[
                    f"measured in the same segment as the {m.display_name} signal; relationship not established"
                ],
            )
            self.add_node(child, EvidenceInput(executed=True, measurement_only=True))
            supporting.append(child.id)
        self.related.append(
            RelatedSignal(
                node_id=node.id,
                metric_id=metric_id,
                metric_label=m.display_name,
                metric_format=m.format,
                unit=m.unit,
                title=node.title or "",
                statement=statement,
                segment_path=list(path),
                window=window,
                baseline_window=base,
                current=cur_v,
                baseline=base_v,
                abs_change=cur_v - base_v,
                pct_change=safe_pct(cur_v, base_v),
                relationship_note=relationship_note,
                shared_with=[shared],
                linked_node_ids=[i for i in linked if i in self.nodes],
                supporting_node_ids=supporting,
                artifact_ids=list(node.artifact_ids),
            )
        )

    def plan_premise(self, step: PlanStep) -> None:
        """A premise stated against a plan ("we hit our revenue target"): the actual metric vs
        the plan metric the question names, over the analysis window."""
        assert self.root_id is not None
        p = step.params
        mid, plan = p["metric_id"], p["baseline_metric_id"]
        m, pm = self.model.get_metric(mid), self.model.get_metric(plan)
        window = self.ctx.window
        filters = list(self.ctx.filters)
        c = self.metric_query([mid], window, filters=filters, title=f"{m.display_name}, {window.display()}")
        b = self.metric_query([plan], window, filters=filters, title=f"{pm.display_name}, {window.display()}")
        ids = [c.artifact.id, b.artifact.id]
        cur = _to_float(c.rows[0].get(mid)) if c.ok else None
        base = _to_float(b.rows[0].get(plan)) if b.ok else None
        if cur is None or base is None:
            self.failed_node(self.root_id, step, step.title, c.error or b.error or "no value", ids)
            return
        change = cur - base
        pct = safe_pct(cur, base)
        rel = pct if pct is not None else 0.0
        tol = float(p.get("tolerance", 0.01))
        expectation = p.get("expectation", "meets_plan")
        holds = rel >= -tol if expectation == "meets_plan" else rel < -tol
        word = "met" if rel >= 0 else ("came within tolerance of" if rel >= -tol else "missed")
        crit = (
            f"at or above {pm.display_name}" + (f", or within {tol * 100:.1f}% below it" if tol else "")
            if expectation == "meets_plan"
            else f"below {pm.display_name}" + (f" by more than {tol * 100:.1f}%" if tol else "")
        )
        verdict: Literal["supported", "contradicted"] = "supported" if holds else "contradicted"
        art = self.metric_result(
            step.title,
            {
                "current": cur,
                "baseline": base,
                "baseline_metric_id": plan,
                "abs_change": change,
                "pct_change": pct,
                "expectation": expectation,
                "tolerance": tol,
                "holds": holds,
            },
            ids,
            chart=charts.comparison_chart(m.display_name, pm.display_name, window.display(), base, cur),
            filters=filters,
            window=window,
            metric_ids=[mid, plan],
        )
        node = TreeNode(
            id=stable_id("node", "check", self.root_id, step.id),
            parent_id=self.root_id,
            kind="check",
            role="premise",
            verdict=verdict,
            title=f"Premise {verdict}: {m.display_name} {word} {pm.display_name}",
            statement=(
                f"Premise {verdict}: {m.display_name} came in at {fmt_value(cur, m.format)} against a "
                f"{pm.display_name} of {fmt_value(base, pm.format)} in {window.display()} ({fmt_pct(pct)}), so "
                f"'{p.get('text', '')}' {'holds' if holds else 'does not hold'}."
            ),
            method=f"The premise requires {m.display_name} {crit}; measured over {window.display()} "
            f"({fmt_change(change, m.format)}).",
            statement_type="observation",
            metric_id=mid,
            metric_label=m.display_name,
            metric_format=m.format,
            current=cur,
            baseline=base,
            baseline_label=pm.display_name,
            abs_change=change,
            pct_change=pct,
            evidence_strength="strong",
            artifact_ids=[art.id, *ids],
            step_id=step.id,
            notes=[f"premise {'holds' if holds else 'contradicted'}", f"tolerance {tol * 100:.1f}%"],
        )
        self.add_node(node, EvidenceInput(executed=True, measurement_only=True))
        if not holds:
            self.nodes[self.root_id].notes.append(
                f"the question assumes '{p.get('text', '')}', but {m.display_name} was {fmt_value(cur, m.format)} "
                f"against {pm.display_name} of {fmt_value(base, pm.format)}"
            )
