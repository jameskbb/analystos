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
        cur = cur[part]
    return cur


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
    def __init__(self, scenarios: dict[str, Any], product_labels: dict[str, set[str]]) -> None:
        self.scenarios = scenarios
        self.product_labels = product_labels  # product_id -> {name, sku}

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
        missing = [t for t in e["texts"] if t not in brief]
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
