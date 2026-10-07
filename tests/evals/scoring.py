"""Score an investigation tree against the benchmark's expected evidence."""

from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BENCHMARK_PATH = Path(__file__).with_name("benchmark.yaml")


def load_benchmark() -> list[dict[str, Any]]:
    return yaml.safe_load(BENCHMARK_PATH.read_text(encoding="utf-8"))["questions"]


def dig(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def row(story: dict[str, Any], list_key: str, match: str, value: Any) -> dict[str, Any]:
    """The answer-key row of a list (``unit_cost_effect_by_category``) whose ``match`` equals value."""
    return next(r for r in dig(story, list_key) if str(r[match]) == str(value))


@dataclass
class Check:
    id: str
    kind: str
    required: bool
    passed: bool
    detail: str


@dataclass
class QuestionResult:
    id: str
    question: str
    benchmark: str
    status: str
    brief: str | None
    seconds: float
    checks: list[Check] = field(default_factory=list)
    error: str | None = None

    @property
    def required_ok(self) -> bool:
        return self.error is None and all(c.passed for c in self.checks if c.required)

    @property
    def hits(self) -> int:
        return sum(c.passed for c in self.checks)


def _direction(current: float | None, baseline: float | None) -> str | None:
    if current is None or baseline is None:
        return None
    if current > baseline:
        return "up"
    if current < baseline:
        return "down"
    return "flat"


def _share(node: Any) -> float | None:
    c = node.contribution_to_parent
    return None if c is None else c.share


def _rank(node: Any, tree: Any) -> int | None:
    siblings = [n for n in tree.nodes if n.parent_id == node.parent_id and n.kind == "segment"]
    ranked = sorted(siblings, key=lambda n: (-(_share(n) if _share(n) is not None else -1e18), n.id))
    for i, n in enumerate(ranked, start=1):
        if n.id == node.id:
            return i
    return None


class Scorer:
    def __init__(
        self,
        scenarios: dict[str, Any],
        product_labels: dict[str, set[str]],
        store: Any = None,
        model: Any = None,
    ) -> None:
        self.scenarios = scenarios
        self.product_labels = product_labels  # product_id -> {name, sku}
        self.store = store
        self.model = model

    def score(self, spec: dict[str, Any], first: Any, final: Any) -> list[Check]:
        story = self.scenarios["stories"][spec["story"]]
        out = []
        for e in spec["expect"]:
            try:
                passed, detail = getattr(self, f"_{e['kind']}")(e, story, first, final)
            except Exception as exc:  # scoring must never hide a crash as a pass
                passed, detail = False, f"scoring error: {type(exc).__name__}: {exc}"
            out.append(Check(e["id"], e["kind"], bool(e.get("required", False)), passed, detail))
        return out

    # ------------------------------------------------------------------ kinds
    def _headline(self, e, story, first, final):
        inv = final.investigation
        if inv.status != "completed" or not inv.tree.root_id:
            return False, f"status {inv.status}"
        root = inv.tree.root()
        if e.get("metric") and root.metric_id != e["metric"]:
            return False, f"root metric {root.metric_id}, expected {e['metric']}"
        d = _direction(root.current, root.baseline)
        if e.get("direction") and d != e["direction"]:
            return False, f"direction {d}"
        if "pct_key" in e:
            want = dig(story, e["pct_key"])
            got = root.pct_change
            if got is None or abs(got - want) * 100 > e.get("tolerance_pp", 0.3):
                return False, f"pct_change {got} vs answer key {want}"
            return True, f"{got:+.2%} (answer key {want:+.2%})"
        return True, f"{root.metric_id} {d}"

    def _headline_values(self, e, story, first, final):
        root = final.investigation.tree.root()
        cur, base = dig(story, e["current_key"]), dig(story, e["baseline_key"])
        ok = abs(root.current - cur) < 1.0 and abs(root.baseline - base) < 1.0
        return ok, f"engine {root.baseline:,.2f} -> {root.current:,.2f}; answer key {base:,.2f} -> {cur:,.2f}"

    def _top_driver(self, e, story, first, final):
        tree = final.investigation.tree
        drivers = [n for n in tree.children_of(tree.root_id) if n.kind == "driver" and _share(n) is not None]
        if not drivers:
            return False, "no first-level drivers"
        top = max(drivers, key=lambda n: _share(n))
        return top.metric_id == e["metric"], f"top driver {top.metric_id} ({_share(top):.0%})"

    def _driver(self, e, story, first, final):
        for n in final.investigation.tree.nodes:
            if n.kind == "driver" and n.metric_id == e["metric"]:
                d = _direction(n.current, n.baseline)
                if d == e.get("direction", d):
                    return True, f"{n.metric_id} {d} ({n.pct_change:+.1%})"
        return False, f"no {e.get('direction', '')} driver node for {e['metric']}"

    def _segment(self, e, story, first, final):
        tree = final.investigation.tree
        best = None
        for n in tree.nodes:
            if n.kind != "segment" or n.segment is None:
                continue
            if n.segment.dimension != e["dimension"] or str(n.segment.value) != str(e["value"]):
                continue
            if e.get("metric") and n.metric_id != e["metric"]:
                continue
            d = _direction(n.current, n.baseline)
            if e.get("direction") and d != e["direction"]:
                continue
            share = _share(n)
            if e.get("min_share") is not None and (share is None or share < e["min_share"]):
                best = best or f"found with share {share}"
                continue
            rank = _rank(n, tree)
            if e.get("max_rank") is not None and (rank is None or rank > e["max_rank"]):
                best = best or f"found at rank {rank}"
                continue
            s = "n/a" if share is None else f"{share:.0%}"
            return (
                True,
                f"{n.metric_id} by {n.segment.dimension}: {n.segment.value} {d}, share {s}, rank {rank}",
            )
        return False, best or f"no segment {e['dimension']}={e['value']}"

    def _check(self, e, story, first, final):
        for n in final.investigation.tree.nodes:
            if n.kind == "check" and n.metric_id in e["metrics"]:
                d = _direction(n.current, n.baseline)
                if d == e["direction"]:
                    return True, n.statement
        return False, f"no {e['direction']} check on {e['metrics']}"

    def _set(self, e, story, first, final):
        dims = e["dimension"] if isinstance(e["dimension"], list) else [e["dimension"]]
        wanted = dig(story, e["values_key"])
        labels = {lab: pid for pid in wanted for lab in self.product_labels.get(pid, {pid})}
        found = {
            labels[str(n.segment.value)]
            for n in final.investigation.tree.nodes
            if n.kind == "segment"
            and n.segment
            and n.segment.dimension in dims
            and str(n.segment.value) in labels
        }
        return len(found) >= e["min_hits"], f"{len(found)} of {len(wanted)} planted items surfaced"

    def _ambiguity(self, e, story, first, final):
        inv = first.investigation
        if inv.status != "needs_disambiguation":
            return False, f"first pass status {inv.status}"
        for a in inv.interpretation.ambiguous:
            if a.term.lower() == e["term"]:
                cands = {c.metric_id for c in a.candidates}
                return set(e["candidates"]) <= cands, f"candidates {sorted(cands)}"
        return False, "term not flagged"

    def _period(self, e, story, first, final):
        """The analysis window and baseline are the answer key's periods."""
        i = final.investigation.interpretation
        cur = story["current_period"]
        base = story.get("baseline_period")
        ok = i.window is not None and i.window.start.isoformat() == cur["start"]
        ok = ok and i.window.last_day.isoformat() == cur["end"]
        if base and i.baseline is not None:
            ok = (
                ok
                and i.baseline.start.isoformat() == base["start"]
                and i.baseline.last_day.isoformat() == base["end"]
            )
        got = f"{i.window.display() if i.window else None} vs {i.baseline.display() if i.baseline else None}"
        return ok, f"{got} ({i.comparison_kind}); answer key {cur['start']}..{cur['end']} vs {base}"

    def _premise(self, e, story, first, final):
        for n in final.investigation.tree.nodes:
            if n.kind == "check" and n.step_id and n.step_id.startswith("premise:"):
                holds = "premise holds" in n.notes
                return holds == e.get("holds", True), n.statement
        return False, "no premise check node"

    def _bridge(self, e, story, first, final):
        """Margin bridge effects (percentage points) against the answer key."""
        effects = {
            n.metric_label: n.contribution_to_parent.effect
            for n in final.investigation.tree.children_of(final.investigation.tree.root_id)
            if n.step_id and n.step_id.startswith("margin_bridge") and n.contribution_to_parent is not None
        }
        want = dig(story, e["key"])
        got = effects.get(e["label"])
        if got is None:
            return False, f"no bridge node {e['label']!r} (have {sorted(effects)})"
        ok = abs(got * 100 - want) <= e.get("tolerance_pp", 0.3)
        return ok, f"{e['label']} {got * 100:+.2f} pp (answer key {want:+.2f} pp)"

    def _no_driver(self, e, story, first, final):
        tree = final.investigation.tree
        bad = [n for n in tree.children_of(tree.root_id) if n.kind == "driver" and n.metric_id == e["metric"]]
        return not bad, "no circular driver" if not bad else bad[0].statement

    def _no_overlap_shares(self, e, story, first, final):
        """No ratio node claims shares over segments that overlap (AOV by product category)."""
        bad = [
            n
            for n in final.investigation.tree.nodes
            if n.kind == "segment"
            and n.metric_id in e["metrics"]
            and n.segment is not None
            and n.segment.dimension in e["dimensions"]
            and _share(n) is not None
        ]
        return not bad, f"{len(bad)} share claims over overlapping segments" + (
            f": {bad[0].statement}" if bad else ""
        )

    def _brief_contains(self, e, story, first, final):
        brief = final.investigation.brief_answer or ""

        def fill(text: str) -> str:
            # "{share:dallas.share_of_change}" -> the answer key's share rendered as the brief does ("50%")
            return re.sub(r"\{share:([\w.]+)\}", lambda m: f"{dig(story, m.group(1)) * 100:.0f}%", text)

        missing = [fill(t) for t in e["texts"] if fill(t) not in brief]
        return not missing, brief if not missing else f"missing {missing} in: {brief}"

    def _comparison(self, e, story, first, final):
        i = final.investigation.interpretation
        ok = i.comparison_kind == e["comparison_kind"]
        if e.get("baseline_metric"):
            ok = ok and i.baseline_metric_id == e["baseline_metric"]
        if e.get("segments"):
            sc = i.segment_comparison
            ok = ok and sc is not None and [sc.current, sc.baseline] == e["segments"]
        return (
            ok,
            f"{i.comparison_kind}, baseline metric {i.baseline_metric_id}, segments {i.segment_comparison}",
        )

    def _ranked_growth(self, e, story, first, final):
        tree = final.investigation.tree
        groups = [
            n for n in tree.nodes if n.kind == "dimension" and any(x.startswith("ranked by") for x in n.notes)
        ]
        if not groups:
            return False, "no breakdown ranked by growth"
        g = groups[0]
        if e.get("dimension") and g.dimension != e["dimension"]:
            return False, f"ranked {g.dimension}, expected {e['dimension']}"
        if e.get("first"):
            kids = [n for n in tree.children_of(g.id) if n.kind == "segment"]
            top = kids[0].segment.value if kids and kids[0].segment else None
            return top == e["first"], f"fastest {top} ({g.statement})"
        return True, g.statement

    def _discount_effect(self, e, story, first, final):
        """The discount-rate check reports the rate change and its revenue effect."""
        k = story["kpis"]
        want = (
            -(k["current"]["discount_rate"] - k["baseline"]["discount_rate"]) * k["current"]["gross_revenue"]
        )
        for n in final.investigation.tree.nodes:
            if n.kind == "check" and n.metric_id == "discount_rate":
                ok = (
                    abs(
                        (n.current - n.baseline)
                        - (k["current"]["discount_rate"] - k["baseline"]["discount_rate"])
                    )
                    < 5e-4
                )
                m = re.search(r"worth ([+-])\$([\d.]+)k of Revenue", n.statement)
                got = (-1 if m and m.group(1) == "-" else 1) * float(m.group(2)) * 1000 if m else None
                ok = ok and got is not None and abs(got - want) <= e.get("tolerance", 5_000)
                return ok, f"{n.statement} (answer key effect {want:,.0f})"
        return False, "no discount_rate check"

    def _mix(self, e, story, first, final):
        """A segment's mix effect in a ratio breakdown (e.g. Entry tier dragging ASP down)."""
        for n in final.investigation.tree.nodes:
            c = n.contribution_to_parent
            if (
                n.kind == "segment"
                and n.metric_id == e["metric"]
                and n.segment is not None
                and n.segment.dimension == e["dimension"]
                and str(n.segment.value) == e["value"]
                and c is not None
                and c.mix_effect is not None
            ):
                ok = c.mix_effect < 0 if e["sign"] == "negative" else c.mix_effect > 0
                return ok, n.statement
        return False, f"no {e['metric']} mix node for {e['dimension']}={e['value']}"

    # ------------------------------------------------------------------ plan variance
    @staticmethod
    def _root_kids(final):
        tree = final.investigation.tree
        return tree, tree.root(), tree.children_of(tree.root_id)

    def _plan_premise(self, e, story, first, final):
        _tree, _root, kids = self._root_kids(final)
        for n in kids:
            if n.role == "premise" and n.metric_id == e["metric"]:
                want = dig(story, e["verdict_key"])
                tol = e.get("tolerance", 1.0)
                ok = n.verdict == want
                ok = ok and abs(n.current - dig(story, e["actual_key"])) <= tol
                ok = ok and abs(n.baseline - dig(story, e["plan_key"])) <= tol
                return ok, f"{n.verdict} (answer key {want}): {n.statement}"
        return False, f"no premise node on {e['metric']}"

    def _plan_headline(self, e, story, first, final):
        inv = final.investigation
        if inv.status != "completed" or not inv.tree.root_id:
            return False, f"status {inv.status}"
        root = inv.tree.root()
        if root.metric_id != e["metric"] or root.baseline_label != "Plan":
            return False, f"root {root.metric_id} against {root.baseline_label}"
        a, p, v = (dig(story, e[k]) for k in ("actual_key", "plan_key", "variance_key"))
        ok = abs(root.current - a) < 1.0 and abs(root.baseline - p) < 1.0 and abs(root.abs_change - v) < 1.0
        return (
            ok,
            f"engine {root.current:,.2f} vs plan {root.baseline:,.2f} ({root.abs_change:+,.2f}); key {v:+,.2f}",
        )

    def _variance_part(self, e, story, first, final):
        _tree, _root, kids = self._root_kids(final)
        want = dig(story, e["effect_key"])
        for n in kids:
            if n.kind == "driver" and n.metric_id == e["metric"] and n.root_effect is not None:
                return abs(
                    n.root_effect - want
                ) < 1.0, f"{n.title}: {n.root_effect:+,.2f} (answer key {want:+,.2f})"
        return False, f"no variance node for {e['metric']}"

    def _reconciled(self, e, story, first, final):
        _tree, root, kids = self._root_kids(final)
        node = next((n for n in kids if n.role == "reconciliation" and n.reconciliation is not None), None)
        if node is None:
            return False, "no reconciliation node"
        rec = node.reconciliation
        top = rec.identities[0]
        sums = abs(sum(c.amount for c in top.components) - root.abs_change) <= rec.tolerance
        ok = (
            rec.reconciled
            and sums
            and all(i.reconciled for i in rec.identities)
            and "reconciled" in node.notes
        )
        return ok, f"{len(rec.identities)} identities, reconciled={rec.reconciled}: {node.statement[:160]}"

    @staticmethod
    def _bridge_node(final, effect):
        tree = final.investigation.tree
        suffix = {"unit_cost": "unit_cost", "discount": "discount", "rest": "rest"}[effect]
        for n in tree.nodes:
            if (
                n.step_id
                and n.step_id.startswith("margin_bridge")
                and n.parent_id
                and n.id == _bridge_id(tree, n.parent_id, suffix)
            ):
                return n
        return None

    def _bridge_effect(self, e, story, first, final):
        n = self._bridge_node(final, e["effect"])
        if n is None or n.root_effect is None:
            return False, f"no bridge effect {e['effect']}"
        want = dig(story, e["key"])
        ok = abs(n.root_effect - want) < 1.0
        if e.get("sign") == "positive":
            ok = ok and n.root_effect > 0
        return ok, f"{n.title}: {n.root_effect:+,.2f} (answer key {want:+,.2f})"

    def _bridge_segment(self, e, story, first, final):
        tree = final.investigation.tree
        effect = self._bridge_node(final, e["effect"])
        if effect is None:
            return False, f"no bridge effect {e['effect']}"
        within = e.get("within") or {}
        want = row(story, e["list_key"], e["match"], e["value"])["effect"]
        stack = [effect.id]
        while stack:
            nid = stack.pop()
            for c in tree.children_of(nid):
                stack.append(c.id)
                if c.kind != "segment" or c.segment is None or c.segment.dimension != e["dimension"]:
                    continue
                if str(c.segment.value) != str(e["value"]):
                    continue
                path = {s.dimension: str(s.value) for s in c.segment_path[:-1]}
                if any(path.get(k) != str(v) for k, v in within.items()):
                    continue
                sibs = [
                    x
                    for x in tree.children_of(c.parent_id)
                    if x.kind == "segment" and x.root_effect is not None
                ]
                rank = sorted(sibs, key=lambda x: -abs(x.root_effect or 0.0)).index(c) + 1
                ok = abs((c.root_effect or 0.0) - want) < 1.0 and rank <= e.get("max_rank", 99)
                return (
                    ok,
                    f"{c.segment.dimension} = {c.segment.value}: {c.root_effect:+,.2f} (key {want:+,.2f}), rank {rank}",
                )
        return False, f"no segment {e['dimension']}={e['value']} inside {e['effect']}"

    def _variance_line(self, e, story, first, final):
        tree = final.investigation.tree
        want = row(story, e["list_key"], "metric", e["metric"])["effect_on_operating_profit"]
        for n in tree.nodes:
            if n.kind == "driver" and n.metric_id == e["metric"] and n.root_effect is not None:
                sibs = [
                    x
                    for x in tree.children_of(n.parent_id)
                    if x.kind == "driver" and x.root_effect is not None
                ]
                rank = sorted(sibs, key=lambda x: x.root_effect or 0.0).index(n) + 1
                ok = abs(n.root_effect - want) < 1.0 and rank <= e.get("max_rank", 99)
                return (
                    ok,
                    f"{n.title}: {n.root_effect:+,.2f} (key {want:+,.2f}), rank {rank} by adverse effect",
                )
        return False, f"no variance line {e['metric']}"

    def _variance_drill(self, e, story, first, final):
        tree = final.investigation.tree
        rows = dig(story, e["list_key"])
        top = max(rows, key=lambda r: r[e.get("field", "variance")])
        for n in tree.nodes:
            if n.kind == "dimension" and n.metric_id == e["metric"] and n.dimension == e["dimension"]:
                segs = [c for c in tree.children_of(n.id) if c.kind == "segment" and c.segment is not None]
                if not segs:
                    continue
                first_seg = max(
                    segs, key=lambda c: c.contribution_to_parent.effect if c.contribution_to_parent else 0
                )
                ok = str(first_seg.segment.value) == str(top[e["match"]])
                return ok, f"largest {first_seg.segment.value} (answer key {top[e['match']]})"
        return False, f"no {e['dimension']} drill of {e['metric']}"

    def _related_signal(self, e, story, first, final):
        inv = final.investigation
        key = dig(story, e["key"])
        tree = inv.tree
        rec = next((n.reconciliation for n in tree.nodes if n.reconciliation is not None), None)
        components = {c.node_id for i in rec.identities for c in i.components} if rec else set()
        for sig in inv.related_signals:
            path = {s.dimension: str(s.value) for s in sig.segment_path}
            if sig.metric_id != key["metric"] or path != {k: str(v) for k, v in key["filters"].items()}:
                continue
            node = tree.get(sig.node_id)
            ok = (
                abs(sig.current - key["rate_current"]) < 5e-4
                and abs(sig.baseline - key["rate_baseline"]) < 5e-4
            )
            ok = ok and sig.relationship == "not_established" and node.relationship == "not_established"
            ok = ok and node.role == "related_signal" and node.statement_type == "observation"
            ok = ok and "not established" in sig.statement and sig.node_id not in components
            support = [tree.get(i) for i in sig.supporting_node_ids]
            resp = next((n for n in support if n.metric_id == "median_first_response_hours"), None)
            ok = ok and resp is not None
            if resp is not None:
                ok = ok and abs(resp.current - key["median_first_response_hours_current"]) < 0.05
                ok = ok and abs(resp.baseline - key["median_first_response_hours_baseline"]) < 0.05
            return ok, sig.statement
        return (
            False,
            f"no related signal {key['metric']} in {key['filters']} (have {[s.title for s in inv.related_signals]})",
        )

    def _no_causal_claim(self, e, story, first, final):
        from analystos_investigator.llm.verifier import causal_leaks

        inv = final.investigation
        brief = inv.brief_answer or ""
        names = [s.metric_label for s in inv.related_signals]
        leaks = causal_leaks(brief, names)
        stated = all(f"{n}" in brief for n in names) and brief.count("not establish") >= len(names)
        explanations = [
            n for n in inv.tree.nodes if n.role == "related_signal" and n.statement_type != "observation"
        ]
        ok = bool(names) and not leaks and stated and not explanations
        return ok, f"signals {names}; causal leaks {leaks}"

    def _rerun_empty(self, e, story, first, final):
        from analystos_investigator import rerun

        again, diff = rerun(final, self.store, self.model)
        ok = not diff.has_changes and again.investigation.brief_answer == final.investigation.brief_answer
        return ok, f"{len(diff.node_changes)} node changes, {diff.unchanged_nodes} unchanged"

    def _plan_review(self, e, story, first, final):
        from analystos_investigator import variance_review

        inv = final.investigation
        explanation = [
            n.id
            for n in inv.tree.nodes
            if n.root_effect is not None or n.role in ("premise", "related_signal")
        ]
        review = variance_review(inv, explanation)
        if review is None:
            return False, "no review"
        ok = review.reconciled and bool(review.related) and all("not establish" in r for r in review.related)
        ok = ok and review.complete and review.bridge is not None and review.bridge.reconciled
        ok = ok and 3 <= len(review.causes) <= 5 and "| Step | Effect | Running total |" in review.markdown
        partial = variance_review(inv, [n for n in explanation if n != inv.tree.root_id][:3])
        ok = ok and partial is not None and not partial.reconciled
        return ok, review.markdown[:200]

    # ------------------------------------------------------------------ round 2
    def _plan_bridge(self, e, story, first, final):
        inv = final.investigation
        b = inv.bridge
        if b is None:
            return False, "no bridge"
        start, end = dig(story, e["start_key"]), dig(story, e["end_key"])
        ok = b.reconciled and abs(b.start - start) < 1.0 and abs(b.end - end) < 1.0
        ok = ok and abs(sum(st.amount for st in b.steps) - (end - start)) < 1.0
        ok = ok and abs(b.steps[-1].running_total - end) < 1.0
        by_id = inv.tree.by_id()
        details = []
        for want in e["steps"]:
            value = dig(story, want["key"]) * (-1 if want.get("negate") else 1)
            if "label" in want:
                step = next((st for st in b.steps if st.label == want["label"]), None)
            else:
                step = next(
                    (
                        st
                        for st in b.steps
                        if st.node_id in by_id and by_id[st.node_id].metric_id == want["metric"]
                    ),
                    None,
                )
            got = None if step is None else step.amount
            ok = ok and got is not None and abs(got - value) < 1.0
            details.append(f"{want.get('label') or want.get('metric')} {got} vs {value:,.2f}")
        ok = ok and len(b.steps) == len(e["steps"])
        return ok, f"{len(b.steps)} steps; " + "; ".join(details)

    def _below_grain(self, e, story, first, final):
        tree = final.investigation.tree
        effect = self._bridge_node(final, e["effect"])
        if effect is None:
            return False, f"no bridge effect {e['effect']}"
        ((dim, val),) = e["within"].items()
        holder = None
        stack = [effect.id]
        while stack:
            nid = stack.pop()
            for c in tree.children_of(nid):
                stack.append(c.id)
                if (
                    c.kind == "segment"
                    and c.segment
                    and c.segment.dimension == dim
                    and str(c.segment.value) == val
                ):
                    holder = c
        if holder is None:
            return False, f"no segment {dim}={val}"
        groups = [
            g for g in tree.children_of(holder.id) if g.kind == "dimension" and g.dimension == e["dimension"]
        ]
        if e.get("expect_none"):
            return not groups, f"{len(groups)} {e['dimension']} splits under {val}"
        if not groups:
            return False, f"no {e['dimension']} split under {val}"
        kids = tree.children_of(groups[0].id)
        rows = dig(story, e["list_key"])
        ok = abs(sum(k.root_effect or 0.0 for k in kids) - (holder.root_effect or 0.0)) < 1.0
        details = []
        for r in rows:
            name = str(r[e["match"]])
            if name == e.get("mix_row"):
                node = next(
                    (k for k in kids if k.kind == "other" and k.title and k.title.endswith("mix")), None
                )
            else:
                node = next((k for k in kids if k.segment and str(k.segment.value) == name), None)
            if node is None and abs(r["effect"]) >= 0.05 * abs(holder.root_effect or 1.0):
                ok = False
                details.append(f"{name} missing")
                continue
            if node is not None:
                ok = ok and abs((node.root_effect or 0.0) - r["effect"]) < 1.0
                details.append(f"{name} {node.root_effect:,.2f} vs {r['effect']:,.2f}")
        segs = sorted((k for k in kids if k.kind == "segment"), key=lambda k: -abs(k.root_effect or 0.0))
        ok = ok and bool(segs) and str(segs[0].segment.value) == str(rows[0][e["match"]])  # type: ignore[union-attr]
        return ok, "; ".join(details)

    def _nested_split(self, e, story, first, final):
        tree = final.investigation.tree
        parent = next((n for n in tree.nodes if n.kind == "driver" and n.metric_id == e["metric"]), None)
        if parent is None:
            return False, f"no {e['metric']} node"
        kids = {k.metric_id: k for k in tree.children_of(parent.id) if k.kind == "driver"}
        ok = True
        details = []
        for want in e["children"]:
            k = kids.get(want["metric"])
            value = -dig(story, want["key"])
            ok = ok and k is not None and abs((k.root_effect or 0.0) - value) < 1.0
            details.append(f"{want['metric']} {k.root_effect if k else None} vs {value:,.2f}")
        dom = kids.get(e["dominant"])
        ok = (
            ok
            and dom is not None
            and all(abs(dom.root_effect or 0.0) >= abs(k.root_effect or 0.0) for k in kids.values())
        )
        return ok, "; ".join(details)

    def _same_segment_offset(self, e, story, first, final):
        tree = final.investigation.tree

        def segs(metric):  # type: ignore[no-untyped-def]
            line = next((n for n in tree.nodes if n.kind == "driver" and n.metric_id == metric), None)
            if line is None:
                return {}
            out = {}
            for g in tree.children_of(line.id):
                if g.kind == "dimension" and g.dimension == e["dimension"]:
                    out.update({str(c.segment.value): c for c in tree.children_of(g.id) if c.segment})
            return out

        adverse, offset = segs(e["adverse"]), segs(e["offset"])
        rows = sorted(dig(story, e["list_key"]), key=lambda r: -r[e["adverse_field"]])[: e.get("top", 2)]
        ok = bool(rows)
        details = []
        for r in rows:
            a, o = adverse.get(str(r[e["match"]])), offset.get(str(r[e["match"]]))
            ok = ok and a is not None and o is not None and a.offset_node_id == o.id
            ok = ok and a is not None and abs((a.root_effect or 0.0) + r[e["adverse_field"]]) < 1.0
            ok = ok and o is not None and abs((o.root_effect or 0.0) + r[e["offset_field"]]) < 1.0
            ok = ok and a is not None and "partly offset in the same" in a.statement
            details.append(
                f"{r[e['match']]}: {a.root_effect if a else None} / {o.root_effect if o else None}"
            )
        return ok, "; ".join(details)

    def _other_lines(self, e, story, first, final):
        tree = final.investigation.tree
        parent = next((n for n in tree.nodes if n.kind == "driver" and n.metric_id == e["parent"]), None)
        node = next((k for k in tree.children_of(parent.id) if k.members), None) if parent else None
        if node is None:
            return False, "no Other lines, net node"
        want = set(dig(story, e["members_key"]))
        value = -dig(story, e["key"])
        ok = {m.metric_id for m in node.members} == want and abs((node.root_effect or 0.0) - value) < 1.0
        ok = ok and node.title == "Other lines, net"
        return (
            ok,
            f"{sorted(m.metric_id or '' for m in node.members)}: {node.root_effect:,.2f} vs {value:,.2f}",
        )

    def _evidence(self, e, story, first, final):
        n = next(
            (n for n in final.investigation.tree.nodes if n.kind == "driver" and n.metric_id == e["metric"]),
            None,
        )
        if n is None:
            return False, f"no {e['metric']} node"
        return n.evidence_strength == e["strength"], f"{n.evidence_strength}: {n.evidence_reasons}"

    def _no_jargon(self, e, story, first, final):
        from analystos_investigator import variance_review

        inv = final.investigation
        review = variance_review(inv, [n.id for n in inv.tree.nodes])
        texts = [inv.brief_answer or "", inv.brief_lead or "", review.markdown if review else ""]
        texts += [n.statement for n in inv.tree.nodes if n.root_effect is not None or n.role]
        hits = [(t[:80], term) for t in texts for term in e["terms"] if term in t.lower()]
        return not hits, f"{len(hits)} jargon hits" + (f": {hits[0]}" if hits else "")

    def _brief_lead(self, e, story, first, final):
        lead = final.investigation.brief_lead or ""

        def fill(text: str) -> str:
            return re.sub(r"\{supplier:([\w.]+)\}", lambda m: str(dig(story, m.group(1))), text)

        sentences = [x for x in re.split(r"(?<=\.)\s+", lead) if x]
        missing = [fill(t) for t in e["texts"] if fill(t) not in lead]
        return (lead and len(sentences) <= e["max_sentences"] and not missing), lead + (
            f" missing {missing}" if missing else ""
        )

    def _traceable(self, e, story, first, final):
        """Conclusion -> evidence -> calculation -> query: every evidence node reaches executed SQL
        through its artifacts' lineage (spec §26, §87)."""
        arts = {a.id: a for a in final.artifacts}

        def reaches_sql(aid: str, seen: set[str]) -> bool:
            art = arts.get(aid)
            if art is None or aid in seen:
                return False
            seen.add(aid)
            return bool(art.sql) or any(reaches_sql(p, seen) for p in art.parent_ids)

        missing = []
        for n in final.investigation.tree.nodes:
            if n.kind in ("hypothesis", "failed") or n.statement_type == "hypothesis":
                continue
            if not any(reaches_sql(a, set()) for a in n.artifact_ids):
                missing.append(f"{n.kind}:{n.statement[:60]}")
        detail = f"{len(missing)} evidence nodes without executed SQL in their lineage"
        return not missing, detail + (f" e.g. {missing[0]}" if missing else "")


def _bridge_id(tree: Any, parent_id: str, suffix: str) -> str:
    from analystos_investigator.ids import stable_id

    return stable_id("node", "bridge", parent_id, suffix)


def run_question(
    spec: dict[str, Any], store: Any, model: Any, value_index: Any, scorer: Scorer, today: dt.date
) -> QuestionResult:
    from analystos_investigator import investigate, resolve_ambiguity

    t0 = time.perf_counter()
    try:
        first = investigate(
            spec["question"], store, model, today=today, auto_approve=True, value_index=value_index
        )
        final = first
        if first.investigation.status == "needs_disambiguation" and spec.get("choices"):
            final = resolve_ambiguity(first, store, model, spec["choices"], auto_approve=True)
    except Exception as exc:
        return QuestionResult(
            spec["id"],
            spec["question"],
            spec["benchmark"],
            "error",
            None,
            time.perf_counter() - t0,
            error=f"{type(exc).__name__}: {exc}",
        )
    res = QuestionResult(
        spec["id"],
        spec["question"],
        spec["benchmark"],
        final.investigation.status,
        final.investigation.brief_answer,
        time.perf_counter() - t0,
    )
    res.checks = scorer.score(spec, first, final)
    return res


def format_scorecard(results: list[QuestionResult]) -> str:
    lines = ["", "AnalystOS analytical eval scorecard (Summit Supply Co., deterministic engine, no LLM)", ""]
    total = sum(len(r.checks) for r in results)
    hits = sum(r.hits for r in results)
    req = [c for r in results for c in r.checks if c.required]
    for r in results:
        mark = "PASS" if r.required_ok else "FAIL"
        lines.append(
            f"[{mark}] {r.id:<28} {r.hits}/{len(r.checks)} evidence  {r.seconds:5.1f}s  {r.question}"
        )
        if r.error:
            lines.append(f"         error: {r.error}")
        for c in r.checks:
            tag = "ok  " if c.passed else ("MISS" if not c.required else "FAIL")
            lines.append(f"         {tag} {'*' if c.required else ' '} {c.id:<28} {c.detail}")
    lines.append("")
    lines.append(
        f"Evidence surfaced: {hits}/{total} ({hits / max(total, 1):.0%}); "
        f"required: {sum(c.passed for c in req)}/{len(req)}; "
        f"questions passing: {sum(r.required_ok for r in results)}/{len(results)}"
    )
    lines.append("(* = required; MISS = tracked expectation not yet surfaced)")
    return "\n".join(lines)
