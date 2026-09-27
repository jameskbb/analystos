"""Deterministic metric-tree suggestions from metric definitions.

Suggestions are derived only from how metrics are *defined* (ratio numerator/denominator,
derived formulas with +, -, *, /). They are returned with ``approved=False,
suggested=True`` and never affect investigations until a user approves them.
"""

from __future__ import annotations

import sqlglot
from analystos_engine.semantic.models import DriverEdge, SemanticModel
from sqlglot import exp


def _formula_edges(parent: str, formula: str, model: SemanticModel) -> tuple[list[DriverEdge], str]:
    try:
        tree = sqlglot.parse_one(formula, read="duckdb")
    except sqlglot.errors.ParseError:
        return [], f"could not parse formula of {parent}"
    while isinstance(tree, exp.Paren):
        tree = tree.this

    def ref(node: exp.Expression) -> str | None:
        while isinstance(node, exp.Paren):
            node = node.this
        if isinstance(node, exp.Column) and not node.table and model.has_metric(node.name):
            return node.name
        return None

    edges: list[DriverEdge] = []
    if isinstance(tree, exp.Add):
        for side in (tree.this, tree.expression):
            r = ref(side)
            if r:
                edges.append(
                    DriverEdge(parent=parent, child=r, relation="additive", approved=False, suggested=True)
                )
        return edges, f"{parent} = sum of its terms"
    if isinstance(tree, exp.Sub):
        left, right = ref(tree.this), ref(tree.expression)
        if left:
            edges.append(
                DriverEdge(parent=parent, child=left, relation="additive", approved=False, suggested=True)
            )
        if right:
            edges.append(
                DriverEdge(parent=parent, child=right, relation="subtractive", approved=False, suggested=True)
            )
        return edges, f"{parent} = {left} - {right}"
    if isinstance(tree, exp.Mul):
        for side in (tree.this, tree.expression):
            r = ref(side)
            if r:
                edges.append(
                    DriverEdge(
                        parent=parent, child=r, relation="multiplicative", approved=False, suggested=True
                    )
                )
        return edges, f"{parent} = product of its factors"
    if isinstance(tree, exp.Div):
        num, den = ref(tree.this), ref(tree.expression)
        if num:
            edges.append(
                DriverEdge(
                    parent=parent, child=num, relation="ratio_numerator", approved=False, suggested=True
                )
            )
        if den:
            edges.append(
                DriverEdge(
                    parent=parent, child=den, relation="ratio_denominator", approved=False, suggested=True
                )
            )
        return edges, f"{parent} = {num} / {den}"
    return [], f"formula of {parent} is not a simple +, -, * or / of metrics"


def suggest_tree(model: SemanticModel, root: str, max_depth: int = 3) -> tuple[list[DriverEdge], list[str]]:
    edges: list[DriverEdge] = []
    notes: list[str] = []
    seen: set[str] = set()

    def visit(metric_id: str, depth: int) -> None:
        if metric_id in seen or depth > max_depth:
            return
        seen.add(metric_id)
        m = model.get_metric(metric_id)
        found: list[DriverEdge] = []
        if m.kind == "ratio" and m.numerator and m.denominator:
            found = [
                DriverEdge(
                    parent=metric_id,
                    child=m.numerator,
                    relation="ratio_numerator",
                    approved=False,
                    suggested=True,
                ),
                DriverEdge(
                    parent=metric_id,
                    child=m.denominator,
                    relation="ratio_denominator",
                    approved=False,
                    suggested=True,
                ),
            ]
            notes.append(f"{metric_id} is defined as {m.numerator} / {m.denominator}")
        elif m.kind == "derived" and m.formula:
            found, note = _formula_edges(metric_id, m.formula, model)
            notes.append(note)
        for e in found:
            edges.append(e)
            visit(e.child, depth + 1)

    visit(root, 0)
    if model.get_metric(root).kind == "simple":
        # A simple sum metric can be decomposed as volume x rate when both exist as metrics that multiply back.
        for m in model.metrics:
            if m.kind == "ratio" and m.numerator == root and m.denominator:
                edges.append(
                    DriverEdge(
                        parent=root,
                        child=m.denominator,
                        relation="multiplicative",
                        approved=False,
                        suggested=True,
                    )
                )
                edges.append(
                    DriverEdge(
                        parent=root, child=m.id, relation="multiplicative", approved=False, suggested=True
                    )
                )
                notes.append(f"{root} = {m.denominator} x {m.id} (because {m.id} = {root} / {m.denominator})")
                break
    return edges, notes
