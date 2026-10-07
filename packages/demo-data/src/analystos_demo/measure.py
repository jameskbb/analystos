"""Measure the planted stories from the generated files with DuckDB (the answer key).

`connect(data_dir)` exposes the raw files as DuckDB views plus a handful of clearly named
ground-truth helper views. The evals reuse it so hand-written ground-truth SQL and the
answer key share one definition of Revenue, COGS, etc.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from . import stories as st

REVENUE_DEFINITION = (
    "SUM(order_lines.net_amount) over lines whose order status is not 'cancelled', dated by orders.order_date"
)


def _q(path: Path) -> str:
    return str(path).replace("'", "''")


def load_budget_sheets(xlsx: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the messy budget workbook the way an analyst would: explicit header rows, drop blanks/notes."""
    branch = pd.read_excel(xlsx, sheet_name="Branch Budget", header=4)
    branch = branch.dropna(subset=["Branch ID", "Budget Month"])
    branch = branch[branch["Budget Month"].map(lambda v: isinstance(v, pd.Timestamp))]
    branch = branch.rename(
        columns={
            "Branch ID": "branch_id",
            "Branch": "branch_name",
            "Budget Month": "budget_month",
            "Revenue Budget": "revenue_budget",
            "Gross Margin Budget": "gross_margin_budget",
            "Opex Budget": "opex_budget",
        }
    )
    category = pd.read_excel(xlsx, sheet_name="Category Budget", header=3)
    category = category.dropna(subset=["Category", "Budget Month"])
    category = category[category["Budget Month"].map(lambda v: isinstance(v, pd.Timestamp))]
    category = category.rename(
        columns={
            "Category": "category",
            "Budget Month": "budget_month",
            "Revenue Budget": "revenue_budget",
            "Units Budget": "units_budget",
        }
    )
    for df in (branch, category):
        df["budget_month"] = pd.to_datetime(df["budget_month"]).dt.date
    return branch.reset_index(drop=True), category.reset_index(drop=True)


def connect(data_dir: str | Path) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with one view per demo table plus ground-truth helper views."""
    d = Path(data_dir)
    con = duckdb.connect()
    csv_opts = "header=true, auto_detect=true, sample_size=-1"
    for t in (
        "branches",
        "sales_reps",
        "products",
        "product_costs",
        "orders",
        "targets",
        "leads",
        "operating_expenses",
        "product_categories",
        "suppliers",
        "sales_plan",
        "opex_plan",
    ):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_csv('{_q(d / (t + '.csv'))}', {csv_opts})")
    con.execute(
        f"CREATE VIEW customers AS SELECT * FROM read_csv('{_q(d / 'customers.csv')}', {csv_opts}, "
        "types={'account_opened': 'VARCHAR'})"
    )
    con.execute(
        f"CREATE VIEW returns AS SELECT * FROM read_csv('{_q(d / 'returns.csv')}', {csv_opts}, "
        "types={'order_id': 'VARCHAR'})"
    )
    con.execute(f"CREATE VIEW order_lines AS SELECT * FROM read_parquet('{_q(d / 'order_lines.parquet')}')")
    con.execute(
        f"CREATE VIEW inventory_snapshots AS SELECT * FROM read_parquet('{_q(d / 'inventory_snapshots.parquet')}')"
    )
    con.execute(
        f"CREATE VIEW opportunities AS SELECT * FROM read_json_auto('{_q(d / 'opportunities.json')}')"
    )
    bb, cb = load_budget_sheets(d / "budgets.xlsx")
    con.register("_budgets_branch_df", bb)
    con.register("_budgets_category_df", cb)
    con.execute("CREATE TABLE budgets_branch AS SELECT * FROM _budgets_branch_df")
    con.execute("CREATE TABLE budgets_category AS SELECT * FROM _budgets_category_df")
    con.unregister("_budgets_branch_df")
    con.unregister("_budgets_category_df")
    # Ground truth: one row per non-cancelled order line with its conformed dimensions.
    con.execute(
        """
        CREATE VIEW gt_sales AS
        WITH cust AS (
            SELECT * FROM customers QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY customer_name) = 1
        )
        SELECT l.order_line_id, l.order_id, o.order_date, o.channel, o.customer_id, c.customer_name, c.segment,
               o.branch_id, b.branch_name, b.region, l.product_id, p.category, p.tier, p.product_name,
               sup.supplier_name AS supplier,
               l.quantity, l.gross_amount, l.discount_amount, l.net_amount, l.cost_amount, l.commission_amount
        FROM order_lines l
        JOIN orders o ON o.order_id = l.order_id
        JOIN cust c ON c.customer_id = o.customer_id
        JOIN branches b ON b.branch_id = o.branch_id
        JOIN products p ON p.product_id = l.product_id
        JOIN suppliers sup ON sup.supplier_id = p.supplier_id
        WHERE o.status <> 'cancelled'
        """
    )
    return con


def _one(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> dict[str, Any]:
    cur = con.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row, strict=True)) if row else {}


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = con.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def _pct(cur: float, base: float) -> float:
    return round((cur - base) / base, 5) if base else float("nan")


def _lmdi(total_cur: float, total_base: float, x_cur: float, x_base: float) -> float:
    """Log-mean Divisia effect of factor x on a multiplicative total (effects sum exactly)."""
    if total_cur == total_base:
        return 0.0
    lmean = (total_cur - total_base) / (math.log(total_cur) - math.log(total_base))
    return lmean * math.log(x_cur / x_base)


def sales_kpis(con: duckdb.DuckDBPyConnection, start: str, end: str, where: str = "TRUE") -> dict[str, Any]:
    """Headline KPIs over [start, end]; ratio KPIs are None when their denominator is zero."""
    k = _one(
        con,
        f"""
        SELECT SUM(net_amount) AS revenue, SUM(gross_amount) AS gross_revenue,
               COUNT(DISTINCT order_id) AS orders, SUM(quantity) AS units,
               SUM(discount_amount) AS discount_amount, SUM(cost_amount) AS cogs
        FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE AND ({where})
        """,
        [start, end],
    )
    rev = float(k["revenue"] or 0)
    out = {
        "revenue": _r(rev),
        "gross_revenue": _r(k["gross_revenue"]),
        "orders": int(k["orders"] or 0),
        "units": int(k["units"] or 0),
        "cogs": _r(k["cogs"]),
        "gross_margin": _r(rev - float(k["cogs"] or 0)),
        "gross_margin_pct": _r((rev - float(k["cogs"] or 0)) / rev, 5) if rev else None,
        "discount_rate": _r(float(k["discount_amount"] or 0) / float(k["gross_revenue"]), 5)
        if k["gross_revenue"]
        else None,
        "aov": _r(rev / k["orders"]) if k["orders"] else None,
        "asp": _r(rev / k["units"], 4) if k["units"] else None,
    }
    return out


def _by(
    con, dim: str, cur: tuple[str, str], base: tuple[str, str], where: str = "TRUE"
) -> list[dict[str, Any]]:
    rows = _rows(
        con,
        f"""
        SELECT {dim} AS segment,
               SUM(net_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS current,
               SUM(net_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS baseline,
               COUNT(DISTINCT order_id) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS orders_current,
               COUNT(DISTINCT order_id) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS orders_baseline
        FROM gt_sales WHERE ({where}) GROUP BY 1
        """,
        [cur[0], cur[1], base[0], base[1], cur[0], cur[1], base[0], base[1]],
    )
    total = sum((r["current"] or 0) - (r["baseline"] or 0) for r in rows)
    out = []
    for r in rows:
        c, b = float(r["current"] or 0), float(r["baseline"] or 0)
        out.append(
            {
                "segment": r["segment"],
                "current": _r(c),
                "baseline": _r(b),
                "change": _r(c - b),
                "pct_change": _pct(c, b) if b else None,
                "share_of_change": _r((c - b) / total, 4) if total else None,
                "orders_current": int(r["orders_current"] or 0),
                "orders_baseline": int(r["orders_baseline"] or 0),
            }
        )
    out.sort(key=lambda x: x["change"])
    return out


def _month_bounds(label: str) -> tuple[str, str]:
    p = pd.Period(label, freq="M")
    return p.start_time.date().isoformat(), p.end_time.date().isoformat()


_OPEX_LINE_SOURCES = (  # metric id, plan metric id, (actual SQL, plan SQL) over [start, end]
    (
        "freight_cost",
        "plan_freight_cost",
        "SELECT SUM(freight_cost) FROM orders WHERE status <> 'cancelled' AND order_date BETWEEN ?::DATE AND ?::DATE",
        "SELECT SUM(plan_freight_cost) FROM opex_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE",
    ),
    (
        "sales_commission",
        "plan_sales_commission",
        "SELECT SUM(commission_amount) FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE",
        "SELECT SUM(plan_sales_commission) FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE",
    ),
    *(
        (
            line,
            f"plan_{line}",
            f"SELECT SUM({line}) FROM operating_expenses WHERE expense_month BETWEEN ?::DATE AND ?::DATE",
            f"SELECT SUM(plan_{line}) FROM opex_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE",
        )
        for line in ("payroll", "overtime", "occupancy", "other_opex")
    ),
)


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> float:
    row = con.execute(sql, params).fetchone()
    return float(row[0] or 0.0) if row else 0.0


def _bridge_cells(con: duckdb.DuckDBPyConnection, start: str, end: str) -> list[dict[str, Any]]:
    """Actual and plan per (category, segment, region) cell over [start, end], with the effects.

    Both effects are measured per list-price dollar (A_gross is actual quantity x list price):
    unit_cost_effect = A_gross * (P_cogs / P_gross - A_cogs / A_gross)
    discount_effect  = A_gross * (A_rev / A_gross - P_rev / P_gross)
    Units are not a common measure inside a category (a stud and a beam are one unit each), so
    cost per unit would move on product mix alone; cost per list dollar moves on cost changes.
    A cell missing on either side contributes nothing to either effect (it lands in mix).
    """
    rows = _rows(
        con,
        """
        WITH a AS (
            SELECT category, segment, region, SUM(quantity) AS a_units, SUM(gross_amount) AS a_gross,
                   SUM(net_amount) AS a_rev, SUM(cost_amount) AS a_cogs
            FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE GROUP BY ALL
        ), p AS (
            SELECT category, segment, region, SUM(plan_units) AS p_units, SUM(plan_gross_revenue) AS p_gross,
                   SUM(plan_revenue) AS p_rev, SUM(plan_cogs) AS p_cogs
            FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY ALL
        )
        SELECT category, segment, region,
               COALESCE(a_units, 0) AS a_units, COALESCE(a_gross, 0) AS a_gross,
               COALESCE(a_rev, 0) AS a_rev, COALESCE(a_cogs, 0) AS a_cogs,
               COALESCE(p_units, 0) AS p_units, COALESCE(p_gross, 0) AS p_gross,
               COALESCE(p_rev, 0) AS p_rev, COALESCE(p_cogs, 0) AS p_cogs
        FROM a FULL OUTER JOIN p USING (category, segment, region)
        ORDER BY category, segment, region
        """,
        [start, end, start, end],
    )
    for c in rows:
        for k in list(c):
            if k.startswith(("a_", "p_")):
                c[k] = float(c[k])
        both = c["a_gross"] != 0 and c["p_gross"] != 0
        c["unit_cost_effect"] = c["a_gross"] * c["p_cogs"] / c["p_gross"] - c["a_cogs"] if both else 0.0
        c["discount_effect"] = c["a_rev"] - c["a_gross"] * c["p_rev"] / c["p_gross"] if both else 0.0
        c["gm_variance"] = (c["a_rev"] - c["a_cogs"]) - (c["p_rev"] - c["p_cogs"])
    return rows


def _supplier_cost_effects(
    con: duckdb.DuckDBPyConnection, start: str, end: str, category: str
) -> list[dict[str, Any]]:
    """Split one category's unit-cost effect by supplier, below the plan grain.

    The plan has no supplier, so each supplier's plan cost per list dollar in a plan cell is its
    own prior-year rate (same months a year earlier) scaled by the cell's planned change:
        P_rate_s = PY_rate_s * P_rate_cell / PY_rate_cell
        effect_s = sum over the category's cells of (A_gross_s * P_rate_s - A_cogs_s)
    Costing every supplier at the cell's blended plan rate instead would book the suppliers'
    standing cost-ratio differences as cost changes. The remainder, sum of A_gross_s * (P_rate_cell -
    P_rate_s), is the shift in supplier mix inside cells; the rows plus `supplier_mix` add exactly to
    the category's unit-cost effect. A supplier with no prior-year sales in a cell uses the cell rate.
    """
    py = [(pd.Timestamp(d) - pd.DateOffset(years=1)).date().isoformat() for d in (start, end)]
    rows = _rows(
        con,
        """
        WITH p AS (
            SELECT category, segment, region, SUM(plan_cogs) / SUM(plan_gross_revenue) AS rate
            FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE AND category = ? GROUP BY ALL
        ), py_cell AS (
            SELECT category, segment, region, SUM(cost_amount) / SUM(gross_amount) AS rate
            FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE AND category = ? GROUP BY ALL
        ), py_sup AS (
            SELECT category, segment, region, supplier, SUM(cost_amount) / SUM(gross_amount) AS rate
            FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE AND category = ? GROUP BY ALL
        ), a AS (
            SELECT category, segment, region, supplier, SUM(gross_amount) AS g, SUM(cost_amount) AS c
            FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE AND category = ? GROUP BY ALL
        )
        SELECT a.supplier,
               SUM(a.g * p.rate * COALESCE(py_sup.rate / py_cell.rate, 1)) - SUM(a.c) AS effect,
               SUM(a.g * p.rate) - SUM(a.g * p.rate * COALESCE(py_sup.rate / py_cell.rate, 1)) AS mix,
               SUM(a.c) / SUM(a.g * p.rate * COALESCE(py_sup.rate / py_cell.rate, 1)) - 1 AS cost_vs_plan_pct,
               SUM(a.c) AS cogs
        FROM a JOIN p USING (category, segment, region)
        LEFT JOIN py_cell USING (category, segment, region)
        LEFT JOIN py_sup USING (category, segment, region, supplier)
        GROUP BY 1 ORDER BY 2
        """,
        [start, end, category, *py, category, *py, category, start, end, category],
    )
    total = sum(float(r["effect"]) for r in rows) + sum(float(r["mix"]) for r in rows)
    out: list[dict[str, Any]] = [
        {
            "supplier": r["supplier"],
            "effect": _r(r["effect"]),
            "share": _r(float(r["effect"]) / total, 4) if total else None,
            "cost_vs_plan_pct": _r(r["cost_vs_plan_pct"], 5),
            "cogs": _r(r["cogs"]),
        }
        for r in rows
    ]
    mix = sum(float(r["mix"]) for r in rows)
    out.append(
        {"supplier": "supplier_mix", "effect": _r(mix), "share": _r(mix / total, 4) if total else None}
    )
    return out


def _freight_split(con: duckdb.DuckDBPyConnection, w: list[Any]) -> dict[str, Any]:
    a = _one(
        con,
        """
        SELECT SUM(base_freight) AS base, SUM(fuel_surcharge) AS fuel, SUM(freight_cost) AS total,
               COUNT(DISTINCT order_id) FILTER (WHERE channel <> 'Counter') AS deliveries
        FROM orders WHERE status <> 'cancelled' AND order_date BETWEEN ?::DATE AND ?::DATE
        """,
        w,
    )
    p = _one(
        con,
        """
        SELECT SUM(plan_base_freight) AS base, SUM(plan_fuel_surcharge) AS fuel, SUM(plan_freight_cost) AS total,
               SUM(plan_deliveries) AS deliveries
        FROM opex_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE
        """,
        w,
    )

    def part(k: str) -> dict[str, Any]:
        av, pv = float(a[k] or 0), float(p[k] or 0)
        return {"actual": _r(av), "plan": _r(pv), "variance": _r(av - pv), "variance_pct": _pct(av, pv)}

    overrun = float(a["total"]) - float(p["total"])
    return {
        "base_freight": part("base"),
        "fuel_surcharge": part("fuel"),
        "freight_cost": part("total"),
        "deliveries": {
            "actual": int(a["deliveries"]),
            "plan": int(p["deliveries"]),
            "variance_pct": _pct(float(a["deliveries"]), float(p["deliveries"])),
        },
        "fuel_surcharge_share_of_overrun": _r((float(a["fuel"]) - float(p["fuel"])) / overrun, 4)
        if overrun
        else None,
        "fuel_surcharge_rate_actual": _r(float(a["fuel"]) / float(a["base"]), 4),
        "fuel_surcharge_rate_plan": _r(float(p["fuel"]) / float(p["base"]), 4),
    }


def _labor(con: duckdb.DuckDBPyConnection, w: list[Any]) -> dict[str, Any]:
    """Payroll and overtime vs plan, with average monthly headcount, in total and by branch."""
    rows = _rows(
        con,
        """
        WITH a AS (
            SELECT branch_id, SUM(payroll) AS payroll, SUM(overtime) AS overtime,
                   SUM(headcount) / COUNT(DISTINCT expense_month) AS headcount
            FROM operating_expenses WHERE expense_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1
        ), p AS (
            SELECT branch_id, SUM(plan_payroll) AS payroll, SUM(plan_overtime) AS overtime,
                   SUM(plan_headcount) / COUNT(DISTINCT plan_month) AS headcount
            FROM opex_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1
        )
        SELECT b.branch_name AS branch, b.region,
               a.payroll AS a_payroll, p.payroll AS p_payroll, a.overtime AS a_overtime, p.overtime AS p_overtime,
               a.headcount AS a_headcount, p.headcount AS p_headcount
        FROM p JOIN branches b USING (branch_id) LEFT JOIN a USING (branch_id)
        """,
        [*w, *w],
    )
    by_branch = []
    for r in rows:
        pay = float(r["a_payroll"] or 0) - float(r["p_payroll"])
        ot = float(r["a_overtime"] or 0) - float(r["p_overtime"])
        by_branch.append(
            {
                "branch": r["branch"],
                "region": r["region"],
                "payroll_variance": _r(pay),
                "overtime_variance": _r(ot),
                "labor_variance": _r(pay + ot),
                "headcount_actual": _r(r["a_headcount"], 2),
                "headcount_plan": _r(r["p_headcount"], 2),
                "headcount_variance": _r(float(r["a_headcount"] or 0) - float(r["p_headcount"]), 2),
            }
        )
    by_branch.sort(key=lambda x: -x["labor_variance"])

    def tot(k: str) -> float:
        return sum(float(r[k] or 0) for r in rows)

    months = 3
    return {
        "payroll": {
            "actual": _r(tot("a_payroll")),
            "plan": _r(tot("p_payroll")),
            "variance": _r(tot("a_payroll") - tot("p_payroll")),
        },
        "overtime": {
            "actual": _r(tot("a_overtime")),
            "plan": _r(tot("p_overtime")),
            "variance": _r(tot("a_overtime") - tot("p_overtime")),
        },
        "labor_cost": {
            "actual": _r(tot("a_payroll") + tot("a_overtime")),
            "plan": _r(tot("p_payroll") + tot("p_overtime")),
            "variance": _r(tot("a_payroll") + tot("a_overtime") - tot("p_payroll") - tot("p_overtime")),
        },
        "headcount": {
            "actual": _r(tot("a_headcount"), 2),
            "plan": _r(tot("p_headcount"), 2),
            "variance": _r(tot("a_headcount") - tot("p_headcount"), 2),
            "note": f"average monthly FTE over the {months} months",
        },
        "by_branch": by_branch,
    }


def _roll(cells: list[dict[str, Any]], keys: tuple[str, ...], measure: str) -> list[dict[str, Any]]:
    agg: dict[tuple[Any, ...], float] = {}
    for c in cells:
        k = tuple(c[x] for x in keys)
        agg[k] = agg.get(k, 0.0) + c[measure]
    total = sum(agg.values())
    out = [
        {**dict(zip(keys, k, strict=True)), "effect": _r(v), "share": _r(v / total, 4) if total else None}
        for k, v in agg.items()
    ]
    return sorted(out, key=lambda x: x["effect"])


def _vs_plan(
    con: duckdb.DuckDBPyConnection, actual_sql: str, plan_sql: str, params: list[Any], key: str
) -> list[dict[str, Any]]:
    """Actual vs plan by one or more keys (both queries return key columns then a value)."""
    a = {tuple(r[:-1]): float(r[-1] or 0) for r in con.execute(actual_sql, params).fetchall()}
    p = {tuple(r[:-1]): float(r[-1] or 0) for r in con.execute(plan_sql, params).fetchall()}
    names = key.split(",")
    out = []
    for k in sorted(set(a) | set(p)):
        av, pv = a.get(k, 0.0), p.get(k, 0.0)
        out.append(
            {
                **dict(zip(names, k, strict=True)),
                "actual": _r(av),
                "plan": _r(pv),
                "variance": _r(av - pv),
                "variance_pct": _pct(av, pv) if pv else None,
            }
        )
    return sorted(out, key=lambda x: x["variance"])


def _operating_profit_story(con) -> dict[str, Any]:
    start, end = st.Q2_2026
    w = [start, end]
    rev_a = _scalar(
        con, "SELECT SUM(net_amount) FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE", w
    )
    rev_p = _scalar(
        con, "SELECT SUM(plan_revenue) FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE", w
    )
    cogs_a = _scalar(
        con, "SELECT SUM(cost_amount) FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE", w
    )
    cogs_p = _scalar(
        con, "SELECT SUM(plan_cogs) FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE", w
    )
    target = _scalar(
        con, "SELECT SUM(revenue_target) FROM targets WHERE target_month BETWEEN ?::DATE AND ?::DATE", w
    )
    budget = _scalar(
        con,
        "SELECT SUM(revenue_budget) FROM budgets_branch WHERE budget_month BETWEEN ?::DATE AND ?::DATE",
        w,
    )
    gm_a, gm_p = rev_a - cogs_a, rev_p - cogs_p
    lines: list[dict[str, Any]] = []
    opex_a = opex_p = 0.0
    for metric, plan_metric, a_sql, p_sql in _OPEX_LINE_SOURCES:
        a, p = _scalar(con, a_sql, w), _scalar(con, p_sql, w)
        opex_a, opex_p = opex_a + a, opex_p + p
        lines.append(
            {
                "metric": metric,
                "plan_metric": plan_metric,
                "actual": _r(a),
                "plan": _r(p),
                "variance": _r(a - p),
                "effect_on_operating_profit": _r(p - a),
            }
        )
    lines.sort(key=lambda x: x["effect_on_operating_profit"])
    op_a, op_p = gm_a - opex_a, gm_p - opex_p
    op_gap = op_a - op_p
    gm_gap, opex_effect = gm_a - gm_p, opex_p - opex_a

    cells = _bridge_cells(con, start, end)
    cost = sum(c["unit_cost_effect"] for c in cells)
    disc = sum(c["discount_effect"] for c in cells)
    mix = gm_gap - cost - disc
    contractor = [c for c in cells if c["segment"] == "Contractor"]
    cat_cost = _roll(cells, ("category",), "unit_cost_effect")
    for row in cat_cost:
        sel = [c for c in cells if c["category"] == row["category"]]
        at_plan = sum(c["a_gross"] * c["p_cogs"] / c["p_gross"] for c in sel if c["a_gross"] and c["p_gross"])
        actual = sum(c["a_cogs"] for c in sel if c["a_gross"] and c["p_gross"])
        row["unit_cost_vs_plan_pct"] = _pct(actual, at_plan) if at_plan else None
    seg_rates = _rows(
        con,
        """
        WITH a AS (SELECT segment, SUM(discount_amount) / SUM(gross_amount) AS rate FROM gt_sales
                   WHERE order_date BETWEEN ?::DATE AND ?::DATE GROUP BY 1),
             p AS (SELECT segment, SUM(plan_discount_amount) / SUM(plan_gross_revenue) AS rate FROM sales_plan
                   WHERE plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1)
        SELECT segment, a.rate AS actual, p.rate AS plan FROM a JOIN p USING (segment) ORDER BY 1
        """,
        [*w, *w],
    )
    region_seg = (
        "SELECT region, segment, SUM(net_amount) FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE "
        "GROUP BY ALL",
        "SELECT region, segment, SUM(plan_revenue) FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE "
        "GROUP BY ALL",
    )
    by_segment = _vs_plan(
        con,
        "SELECT segment, SUM(net_amount) FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE GROUP BY 1",
        "SELECT segment, SUM(plan_revenue) FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1",
        w,
        "segment",
    )
    overtime_by_branch = _vs_plan(
        con,
        "SELECT b.branch_name, SUM(x.overtime) FROM operating_expenses x JOIN branches b USING (branch_id) "
        "WHERE x.expense_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1",
        "SELECT b.branch_name, SUM(x.plan_overtime) FROM opex_plan x JOIN branches b USING (branch_id) "
        "WHERE x.plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1",
        w,
        "branch",
    )
    overtime_by_region = _vs_plan(
        con,
        "SELECT b.region, SUM(x.overtime) FROM operating_expenses x JOIN branches b USING (branch_id) "
        "WHERE x.expense_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1",
        "SELECT b.region, SUM(x.plan_overtime) FROM opex_plan x JOIN branches b USING (branch_id) "
        "WHERE x.plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1",
        w,
        "region",
    )
    freight_by_region = _vs_plan(
        con,
        "SELECT b.region, SUM(o.freight_cost) FROM orders o JOIN branches b USING (branch_id) "
        "WHERE o.status <> 'cancelled' AND o.order_date BETWEEN ?::DATE AND ?::DATE GROUP BY 1",
        "SELECT b.region, SUM(x.plan_freight_cost) FROM opex_plan x JOIN branches b USING (branch_id) "
        "WHERE x.plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1",
        w,
        "region",
    )
    q1 = ["2026-01-01", "2026-03-31"]
    signal = _one(
        con,
        """
        SELECT SUM(is_converted) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE)
                 / COUNT(*) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS rate_cur,
               SUM(is_converted) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE)
                 / COUNT(*) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS rate_base,
               MEDIAN(first_response_hours) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS resp_cur,
               MEDIAN(first_response_hours) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS resp_base,
               COUNT(*) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS leads_cur,
               COUNT(*) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS leads_base
        FROM leads WHERE region = ? AND channel = ?
        """,
        [*w, *w, *q1, *q1, *w, *q1, *w, *q1, st.CONVERSION_STORY_REGION, st.CONVERSION_STORY_CHANNEL],
    )
    disc_regions = _roll(contractor, ("region",), "discount_effect")
    top_cost = cat_cost[0]["category"] if cat_cost else None
    return {
        "id": "operating_profit_miss_q2_2026",
        "title": "Revenue on plan, operating profit well below plan (Q2 2026 vs the FY26 Operating Plan)",
        "questions": [
            "We made our sales number last quarter, but we missed our target for net profit. What happened?",
            "We hit our revenue target last quarter, but operating profit missed plan badly. What happened?",
            "Why did operating profit miss plan in Q2 2026?",
            "Operating profit vs plan last quarter: what explains the gap?",
        ],
        "metric": "operating_profit",
        "plan_metric": "plan_operating_profit",
        "comparison": "actual_vs_plan",
        "plan_version": st.PLAN_VERSION,
        "current_period": {"start": start, "end": end},
        "headline": {
            "revenue_actual": _r(rev_a),
            "revenue_plan": _r(rev_p),
            "revenue_variance": _r(rev_a - rev_p),
            "revenue_variance_pct": _pct(rev_a, rev_p),
            "operating_profit_actual": _r(op_a),
            "operating_profit_plan": _r(op_p),
            "operating_profit_variance": _r(op_gap),
            "operating_profit_variance_pct": _pct(op_a, op_p),
            "operating_margin_pct_actual": _r(op_a / rev_a, 5),
            "operating_margin_pct_plan": _r(op_p / rev_p, 5),
        },
        "premise": {
            "statement": "We made our sales number",
            "metric": "revenue",
            "plan_metric": "plan_revenue",
            "actual": _r(rev_a),
            "plan": _r(rev_p),
            "variance_pct": _pct(rev_a, rev_p),
            "verdict": "supported" if rev_a >= rev_p * (1 - 0.01) else "contradicted",
            "rule": "supported when actual revenue is at or above plan, or within 1% below it",
            "revenue_target": _r(target),
            "budget_revenue": _r(budget),
            "note": "FY26 rep quotas (targets) and branch budgets allocate the same plan revenue",
        },
        "bridge": {
            "method": "Operating Profit = Gross Margin - Operating Expenses; variance = actual - plan",
            "gross_margin": {
                "actual": _r(gm_a),
                "plan": _r(gm_p),
                "variance": _r(gm_gap),
                "share_of_gap": _r(gm_gap / op_gap, 4),
            },
            "operating_expenses": {
                "actual": _r(opex_a),
                "plan": _r(opex_p),
                "variance": _r(opex_a - opex_p),
                "effect_on_operating_profit": _r(opex_effect),
                "share_of_gap": _r(opex_effect / op_gap, 4),
            },
            "reconciles": abs(op_gap - (gm_gap + opex_effect)) < 0.01,
            "residual": _r(op_gap - (gm_gap + opex_effect)),
        },
        "gross_margin_bridge": {
            "method": "Counterfactuals per (category, segment, region) cell over the quarter, per list-price "
            "dollar: unit cost effect = actual list revenue x (plan COGS/list - actual COGS/list); discount "
            "effect = actual list revenue x (actual net/list - plan net/list); mix, volume and other = gross "
            "margin variance minus both.",
            "grain": ["category", "segment", "region"],
            "unit_cost_effect": _r(cost),
            "discount_effect": _r(disc),
            "mix_effect": _r(mix),
            "total": _r(gm_gap),
            "reconciles": abs(gm_gap - (cost + disc + mix)) < 0.01,
        },
        "unit_cost_effect_by_category": cat_cost,
        "discount_effect_by_segment": _roll(cells, ("segment",), "discount_effect"),
        "discount_effect_by_region": _roll(cells, ("region",), "discount_effect"),
        "contractor_discount_effect_by_region": disc_regions,
        "discount_rate_by_segment": [
            {
                "segment": s["segment"],
                "actual": _r(s["actual"], 5),
                "plan": _r(s["plan"], 5),
                "change_pp": _r((s["actual"] - s["plan"]) * 100, 3),
            }
            for s in seg_rates
        ],
        "revenue_by_segment": by_segment,
        "revenue_by_region_segment": _vs_plan(con, *region_seg, w, "region,segment"),
        "operating_expense_lines": lines,
        "operating_expense_groups": {
            "freight_cost": ["base_freight", "fuel_surcharge"],
            "labor_cost": ["payroll", "overtime"],
            "other_lines": ["sales_commission", "occupancy", "other_opex"],
            "other_lines_variance": _r(
                sum(
                    x["variance"]
                    for x in lines
                    if x["metric"] in ("sales_commission", "occupancy", "other_opex")
                )
            ),
        },
        "freight": _freight_split(con, w),
        "labor": _labor(con, w),
        "lumber_unit_cost_by_supplier": _supplier_cost_effects(con, start, end, "Lumber"),
        "roofing_unit_cost_by_supplier": _supplier_cost_effects(con, start, end, "Roofing"),
        "overtime_by_branch": overtime_by_branch,
        "overtime_by_region": overtime_by_region,
        "freight_by_region": freight_by_region,
        "related_signals": [
            {
                "id": "gulf_coast_paid_search_conversion",
                "metric": "conversion_rate",
                "filters": {
                    "region": st.CONVERSION_STORY_REGION,
                    "lead_channel": st.CONVERSION_STORY_CHANNEL,
                },
                "current_period": {"start": start, "end": end},
                "baseline_period": {"start": q1[0], "end": q1[1]},
                "rate_current": _r(signal["rate_cur"], 4),
                "rate_baseline": _r(signal["rate_base"], 4),
                "leads_current": int(signal["leads_cur"]),
                "leads_baseline": int(signal["leads_base"]),
                "median_first_response_hours_current": _r(signal["resp_cur"], 1),
                "median_first_response_hours_baseline": _r(signal["resp_base"], 1),
                "shared_context": {"region": st.CONVERSION_STORY_REGION, "period": "Q2 2026"},
                "relationship_to_gap": "not_established",
                "note": "A verified operating signal in the same region and quarter as the Contractor discount "
                "concentration; the data does not establish it as a cause of the operating profit miss.",
            }
        ],
        "expected_discoveries": [
            {"id": "premise_supported", "kind": "premise", "metric": "revenue", "verdict": "supported"},
            {
                "id": "op_below_plan",
                "kind": "headline",
                "metric": "operating_profit",
                "direction": "down",
                "max_variance_pct": -0.3,
            },
            {"id": "gm_majority", "kind": "bridge", "metric": "gross_margin", "min_share_of_gap": 0.6},
            {"id": "opex_part", "kind": "bridge", "metric": "operating_expenses", "min_share_of_gap": 0.1},
            {
                "id": "unit_cost_lumber",
                "kind": "segment",
                "dimension": "category",
                "value": top_cost,
                "metric": "unit_cost_effect",
                "direction": "down",
            },
            {
                "id": "discount_contractor",
                "kind": "segment",
                "dimension": "segment",
                "value": "Contractor",
                "metric": "discount_effect",
                "direction": "down",
            },
            {
                "id": "discount_gulf_coast",
                "kind": "segment",
                "dimension": "region",
                "value": disc_regions[0]["region"] if disc_regions else None,
                "metric": "discount_effect",
                "direction": "down",
            },
            {"id": "mix_offset", "kind": "bridge", "metric": "mix_effect", "direction": "up"},
            {"id": "freight_overrun", "kind": "driver", "metric": "freight_cost", "direction": "up"},
            {"id": "overtime_overrun", "kind": "driver", "metric": "overtime", "direction": "up"},
            {
                "id": "conversion_signal_not_causal",
                "kind": "related_signal",
                "metric": "conversion_rate",
                "relationship": "not_established",
            },
        ],
    }


def _revenue_story(con) -> dict[str, Any]:
    cur, base = _month_bounds(st.AUG_2026), _month_bounds(st.JUL_2026)
    yoy = _month_bounds("2025-08")
    k1, k0, ky = sales_kpis(con, *cur), sales_kpis(con, *base), sales_kpis(con, *yoy)
    change = k1["revenue"] - k0["revenue"]
    branches = _by(con, "branch_name", cur, base)
    dallas = next(b for b in branches if b["segment"] == "Dallas")
    customers = _by(con, "customer_name", cur, base)
    major = next(c for c in customers if c["segment"] == st.MAJOR_CUSTOMER_NAME)
    categories = _by(con, "category", cur, base)
    ins = next(c for c in categories if c["segment"] == "Insulation")
    tiers = _rows(
        con,
        """
        SELECT tier,
               SUM(quantity) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) * 1.0
                 / SUM(SUM(quantity) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE)) OVER () AS unit_share_current,
               SUM(quantity) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) * 1.0
                 / SUM(SUM(quantity) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE)) OVER () AS unit_share_baseline,
               COUNT(*) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) * 1.0
                 / SUM(COUNT(*) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE)) OVER () AS line_share_current,
               COUNT(*) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) * 1.0
                 / SUM(COUNT(*) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE)) OVER () AS line_share_baseline
        FROM gt_sales GROUP BY tier ORDER BY tier
        """,
        [*cur, *cur, *base, *base, *cur, *cur, *base, *base],
    )
    entry = next(t for t in tiers if t["tier"] == "Entry")
    # Multiplicative decompositions (LMDI): Revenue = Orders x AOV = Units x ASP.
    r1, r0 = k1["revenue"], k0["revenue"]
    orders_eff = _lmdi(r1, r0, k1["orders"], k0["orders"])
    aov_eff = _lmdi(r1, r0, k1["aov"], k0["aov"])
    units_eff = _lmdi(r1, r0, k1["units"], k0["units"])
    asp_eff = _lmdi(r1, r0, k1["asp"], k0["asp"])
    # Price-only view: discount effect on revenue holding gross revenue at current level.
    discount_effect = -(k1["discount_rate"] - k0["discount_rate"]) * k1["gross_revenue"]
    dallas_k1 = sales_kpis(con, *cur, "branch_name = 'Dallas'")
    dallas_k0 = sales_kpis(con, *base, "branch_name = 'Dallas'")
    growth = _by(con, "category", cur, yoy)
    growth_sorted = sorted([g for g in growth if g["baseline"]], key=lambda g: g["pct_change"], reverse=True)
    return {
        "id": "revenue_decline_aug_2026",
        "title": "August 2026 revenue decline",
        "questions": [
            "Why was August revenue down?",
            "Why did revenue decline in August 2026 compared with July 2026?",
            "Which region drove the August decline?",
            "Which customers contributed most to the August decline?",
        ],
        "metric": "revenue",
        "current_period": {"start": cur[0], "end": cur[1]},
        "baseline_period": {"start": base[0], "end": base[1]},
        "comparison": "pop",
        "headline": {
            "current": k1["revenue"],
            "baseline": k0["revenue"],
            "abs_change": _r(change),
            "pct_change": _pct(k1["revenue"], k0["revenue"]),
            "target_pct_change": st.TARGET_AUG_VS_JUL,
        },
        "kpis": {"current": k1, "baseline": k0},
        "decomposition": {
            "method": "LMDI (log-mean Divisia); effects sum exactly to the revenue change",
            "orders_effect": _r(orders_eff),
            "aov_effect": _r(aov_eff),
            "units_effect": _r(units_eff),
            "asp_effect": _r(asp_eff),
            "orders_pct_change": _pct(k1["orders"], k0["orders"]),
            "aov_pct_change": _pct(k1["aov"], k0["aov"]),
            "units_pct_change": _pct(k1["units"], k0["units"]),
            "asp_pct_change": _pct(k1["asp"], k0["asp"]),
        },
        "dallas": {
            **dallas,
            "share_of_decline": dallas["share_of_change"],
            "orders_pct_change": _pct(dallas_k1["orders"], dallas_k0["orders"]),
            "region": "North Texas",
        },
        "major_customer": {
            "customer_id": st.MAJOR_CUSTOMER_ID,
            "customer_name": st.MAJOR_CUSTOMER_NAME,
            "branch": "Dallas",
            "segment": "Enterprise",
            **major,
            "share_of_decline": major["share_of_change"],
            "share_of_dallas_decline": _r(major["change"] / dallas["change"], 4),
            "rank_by_decline": [c["segment"] for c in customers].index(st.MAJOR_CUSTOMER_NAME) + 1,
        },
        "mix_shift": {
            "entry_unit_share_current": _r(entry["unit_share_current"], 4),
            "entry_unit_share_baseline": _r(entry["unit_share_baseline"], 4),
            "entry_line_share_current": _r(entry["line_share_current"], 4),
            "entry_line_share_baseline": _r(entry["line_share_baseline"], 4),
            "tiers": [{k: (_r(v, 4) if isinstance(v, float) else v) for k, v in t.items()} for t in tiers],
        },
        "discounting": {
            "discount_rate_current": k1["discount_rate"],
            "discount_rate_baseline": k0["discount_rate"],
            "change_pp": _r((k1["discount_rate"] - k0["discount_rate"]) * 100, 3),
            "revenue_effect_at_current_gross": _r(discount_effect),
        },
        "offsetting_category": {"category": "Insulation", **ins},
        "by_branch": branches,
        "by_category": categories,
        "top_customer_declines": customers[:5],
        "yoy": {
            "baseline_period": {"start": yoy[0], "end": yoy[1]},
            "baseline": ky["revenue"],
            "pct_change": _pct(k1["revenue"], ky["revenue"]),
            "note": "YoY is milder than MoM: Aug 2025 was lower than Jul 2026 (seasonality plus no Dallas collapse)",
        },
        "fastest_growing_categories_yoy": [
            {"category": g["segment"], "pct_change": g["pct_change"], "change": g["change"]}
            for g in growth_sorted[:3]
        ],
        "expected_discoveries": [
            {
                "id": "headline",
                "kind": "headline",
                "metric": "revenue",
                "pct_change": _pct(r1, r0),
                "tolerance_pp": 0.3,
            },
            {
                "id": "order_volume",
                "kind": "driver",
                "metric": "orders",
                "direction": "down",
                "note": "Order count falls more than AOV; orders effect is the larger multiplicative component",
            },
            {
                "id": "dallas",
                "kind": "segment",
                "dimension": "branch",
                "value": "Dallas",
                "direction": "down",
                "min_share": 0.35,
            },
            {
                "id": "north_texas",
                "kind": "segment",
                "dimension": "region",
                "value": "North Texas",
                "direction": "down",
                "min_share": 0.35,
            },
            {
                "id": "major_customer",
                "kind": "segment",
                "dimension": "customer",
                "value": st.MAJOR_CUSTOMER_NAME,
                "direction": "down",
                "min_share": 0.15,
            },
            {
                "id": "entry_mix",
                "kind": "segment",
                "dimension": "product_tier",
                "value": "Entry",
                "direction": "mix_up",
            },
            {"id": "discount_rate", "kind": "driver", "metric": "discount_rate", "direction": "up"},
            {
                "id": "insulation_offset",
                "kind": "segment",
                "dimension": "category",
                "value": "Insulation",
                "direction": "up",
            },
        ],
    }


def _margin_story(con) -> dict[str, Any]:
    cur, base = st.Q2_2026, st.Q2_2025
    k1, k0 = sales_kpis(con, *cur), sales_kpis(con, *base)
    cf = _one(
        con,
        """
        WITH prior_cost AS (
            SELECT product_id, cost_month, unit_cost FROM product_costs
        ), seg_disc AS (
            SELECT segment, SUM(discount_amount) / SUM(gross_amount) AS rate
            FROM gt_sales WHERE order_date BETWEEN ?::DATE AND ?::DATE GROUP BY segment
        )
        SELECT SUM(s.net_amount) AS revenue,
               SUM(s.cost_amount) AS cogs,
               SUM(s.quantity * pc.unit_cost) AS cogs_at_prior_year_cost,
               SUM(s.gross_amount * (1 - sd.rate)) AS revenue_at_prior_discount
        FROM gt_sales s
        JOIN prior_cost pc ON pc.product_id = s.product_id
             AND pc.cost_month = date_trunc('month', s.order_date) - INTERVAL 12 MONTH
        JOIN seg_disc sd ON sd.segment = s.segment
        WHERE s.order_date BETWEEN ?::DATE AND ?::DATE
        """,
        [base[0], base[1], cur[0], cur[1]],
    )
    rev, cogs = float(cf["revenue"]), float(cf["cogs"])
    gm_actual = (rev - cogs) / rev
    gm_prior_cost = (rev - float(cf["cogs_at_prior_year_cost"])) / rev
    rev_pd = float(cf["revenue_at_prior_discount"])
    gm_prior_disc = (rev_pd - cogs) / rev_pd
    cost_effect_pp = (gm_actual - gm_prior_cost) * 100
    discount_effect_pp = (gm_actual - gm_prior_disc) * 100
    total_pp = (k1["gross_margin_pct"] - k0["gross_margin_pct"]) * 100
    cat_rows = _rows(
        con,
        """
        SELECT category,
          SUM(net_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS rev_cur,
          SUM(net_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS rev_base,
          1 - SUM(cost_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE)
              / SUM(net_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS gm_pct_cur,
          1 - SUM(cost_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE)
              / SUM(net_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS gm_pct_base
        FROM gt_sales GROUP BY category ORDER BY category
        """,
        [*cur, *base, *cur, *cur, *base, *base],
    )
    unit_cost = _rows(
        con,
        """
        SELECT p.category,
               AVG(pc.unit_cost) FILTER (WHERE pc.cost_month BETWEEN ?::DATE AND ?::DATE)
                 / AVG(pc.unit_cost) FILTER (WHERE pc.cost_month BETWEEN ?::DATE AND ?::DATE) - 1 AS unit_cost_change
        FROM product_costs pc JOIN products p USING (product_id)
        GROUP BY 1 ORDER BY 2 DESC
        """,
        [*cur, *base],
    )
    seg_disc = _rows(
        con,
        """
        SELECT segment,
          SUM(discount_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE)
            / SUM(gross_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS disc_cur,
          SUM(discount_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE)
            / SUM(gross_amount) FILTER (WHERE order_date BETWEEN ?::DATE AND ?::DATE) AS disc_base
        FROM gt_sales GROUP BY 1 ORDER BY 1
        """,
        [*cur, *cur, *base, *base],
    )
    return {
        "id": "margin_compression_q2_2026",
        "title": "Margin compression with flat revenue (Q2 2026 vs Q2 2025)",
        "questions": [
            "Revenue was roughly flat. Why did margin decline?",
            "Why did gross margin % decline in Q2 2026 vs Q2 2025 while revenue was flat?",
            "Why did margin decline despite flat revenue?",
        ],
        "metric": "gross_margin_pct",
        "ambiguity_note": "'margin' matches Gross Margin, Gross Margin %, Contribution Margin and Operating Profit; "
        "the product must surface the choice. The answer key uses Gross Margin %.",
        "current_period": {"start": cur[0], "end": cur[1]},
        "baseline_period": {"start": base[0], "end": base[1]},
        "comparison": "yoy",
        "headline": {
            "revenue_current": k1["revenue"],
            "revenue_baseline": k0["revenue"],
            "revenue_pct_change": _pct(k1["revenue"], k0["revenue"]),
            "gross_margin_pct_current": k1["gross_margin_pct"],
            "gross_margin_pct_baseline": k0["gross_margin_pct"],
            "gross_margin_pct_change_pp": _r(total_pp, 3),
            "gross_margin_current": k1["gross_margin"],
            "gross_margin_baseline": k0["gross_margin"],
        },
        "attribution_pp": {
            "method": "Counterfactuals on Q2 2026 lines: (a) unit cost at the same product's cost 12 months "
            "earlier; (b) revenue at Q2 2025 segment discount rates. Residual = mix and other effects.",
            "unit_cost_inflation": _r(cost_effect_pp, 3),
            "discounting": _r(discount_effect_pp, 3),
            "residual_mix_and_other": _r(total_pp - cost_effect_pp - discount_effect_pp, 3),
        },
        "unit_cost_change_by_category": [
            {"category": u["category"], "unit_cost_change": _r(u["unit_cost_change"], 4)} for u in unit_cost
        ],
        "by_category": [
            {
                "category": c["category"],
                "revenue_pct_change": _pct(float(c["rev_cur"]), float(c["rev_base"])),
                "gm_pct_current": _r(c["gm_pct_cur"], 4),
                "gm_pct_baseline": _r(c["gm_pct_base"], 4),
                "gm_pct_change_pp": _r((c["gm_pct_cur"] - c["gm_pct_base"]) * 100, 3),
            }
            for c in cat_rows
        ],
        "discount_rate_by_segment": [
            {
                "segment": s["segment"],
                "current": _r(s["disc_cur"], 4),
                "baseline": _r(s["disc_base"], 4),
                "change_pp": _r((s["disc_cur"] - s["disc_base"]) * 100, 3),
            }
            for s in seg_disc
        ],
        "expected_discoveries": [
            {"id": "revenue_flat", "kind": "headline", "metric": "revenue", "max_abs_pct_change": 0.015},
            {
                "id": "gm_pct_down",
                "kind": "headline",
                "metric": "gross_margin_pct",
                "direction": "down",
                "min_drop_pp": 2.0,
            },
            {
                "id": "lumber_cost",
                "kind": "segment",
                "dimension": "category",
                "value": "Lumber",
                "direction": "down",
                "metric": "gross_margin_pct",
            },
            {
                "id": "roofing_cost",
                "kind": "segment",
                "dimension": "category",
                "value": "Roofing",
                "direction": "down",
                "metric": "gross_margin_pct",
            },
            {
                "id": "contractor_discount",
                "kind": "segment",
                "dimension": "segment",
                "value": "Contractor",
                "direction": "up",
                "metric": "discount_rate",
            },
        ],
    }


def _conversion_story(con) -> dict[str, Any]:
    cur, base = ("2026-04-01", "2026-06-30"), ("2026-01-01", "2026-03-31")
    tot = _one(
        con,
        """
        SELECT COUNT(*) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS leads_cur,
               COUNT(*) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS leads_base,
               SUM(is_converted) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS conv_cur,
               SUM(is_converted) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS conv_base
        FROM leads
        """,
        [*cur, *base, *cur, *base],
    )
    seg = _rows(
        con,
        """
        SELECT region, channel,
               COUNT(*) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS leads_cur,
               COUNT(*) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS leads_base,
               SUM(is_converted) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS conv_cur,
               SUM(is_converted) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS conv_base,
               MEDIAN(first_response_hours) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS resp_cur,
               MEDIAN(first_response_hours) FILTER (WHERE created_date BETWEEN ?::DATE AND ?::DATE) AS resp_base
        FROM leads GROUP BY 1, 2
        """,
        [*cur, *base, *cur, *base, *cur, *base],
    )
    rate_cur = tot["conv_cur"] / tot["leads_cur"]
    rate_base = tot["conv_base"] / tot["leads_base"]
    # Counterfactual: current mix, baseline rate for the story cell only.
    story = next(
        s
        for s in seg
        if s["region"] == st.CONVERSION_STORY_REGION and s["channel"] == st.CONVERSION_STORY_CHANNEL
    )
    story_rate_base = story["conv_base"] / story["leads_base"]
    cf_conv = tot["conv_cur"] - story["conv_cur"] + story_rate_base * story["leads_cur"]
    explained = (cf_conv / tot["leads_cur"] - rate_cur) / (rate_base - rate_cur)

    def rate_by(col: str) -> list[dict[str, Any]]:
        agg: dict[str, list[float]] = {}
        for s in seg:
            a = agg.setdefault(s[col], [0, 0, 0, 0])
            a[0] += s["leads_cur"]
            a[1] += s["leads_base"]
            a[2] += s["conv_cur"] or 0
            a[3] += s["conv_base"] or 0
        return sorted(
            [
                {
                    col: k,
                    "leads_current": int(v[0]),
                    "leads_baseline": int(v[1]),
                    "rate_current": _r(v[2] / v[0], 4),
                    "rate_baseline": _r(v[3] / v[1], 4),
                    "rate_change_pp": _r((v[2] / v[0] - v[3] / v[1]) * 100, 3),
                }
                for k, v in agg.items()
            ],
            key=lambda x: x["rate_change_pp"],
        )

    return {
        "id": "conversion_decline_q2_2026",
        "title": "Lead conversion decline (Q2 2026 vs Q1 2026 lead cohorts)",
        "questions": [
            "Why did conversion rate decline in Q2 2026?",
            "Lead volume was stable but conversion fell. Where?",
        ],
        "metric": "conversion_rate",
        "current_period": {"start": cur[0], "end": cur[1]},
        "baseline_period": {"start": base[0], "end": base[1]},
        "comparison": "pop",
        "cohort_note": "Leads are dated by created_date; conversion is observed through 2026-09-30, so both "
        "cohorts have had at least 90 days to convert.",
        "headline": {
            "leads_current": int(tot["leads_cur"]),
            "leads_baseline": int(tot["leads_base"]),
            "leads_pct_change": _pct(tot["leads_cur"], tot["leads_base"]),
            "conversion_rate_current": _r(rate_cur, 4),
            "conversion_rate_baseline": _r(rate_base, 4),
            "conversion_rate_change_pp": _r((rate_cur - rate_base) * 100, 3),
        },
        "concentration": {
            "region": st.CONVERSION_STORY_REGION,
            "channel": st.CONVERSION_STORY_CHANNEL,
            "rate_current": _r(story["conv_cur"] / story["leads_cur"], 4),
            "rate_baseline": _r(story_rate_base, 4),
            "share_of_decline_explained": _r(explained, 4),
            "median_first_response_hours_current": _r(story["resp_cur"], 1),
            "median_first_response_hours_baseline": _r(story["resp_base"], 1),
            "method": "Counterfactual: restore the cell's Q1 conversion rate at its Q2 lead volume",
        },
        "by_region": rate_by("region"),
        "by_channel": rate_by("channel"),
        "expected_discoveries": [
            {"id": "volume_stable", "kind": "headline", "metric": "leads", "max_abs_pct_change": 0.05},
            {
                "id": "rate_down",
                "kind": "headline",
                "metric": "conversion_rate",
                "direction": "down",
                "min_drop_pp": 2.5,
            },
            {
                "id": "region",
                "kind": "segment",
                "dimension": "region",
                "value": st.CONVERSION_STORY_REGION,
                "direction": "down",
                "metric": "conversion_rate",
            },
            {
                "id": "channel",
                "kind": "segment",
                "dimension": "lead_channel",
                "value": st.CONVERSION_STORY_CHANNEL,
                "direction": "down",
                "metric": "conversion_rate",
            },
        ],
    }


def _inventory_story(con, slow_ids: list[str]) -> dict[str, Any]:
    cur, base = "2026-06-30", "2025-06-30"
    ids = ",".join(f"'{i}'" for i in slow_ids)
    tot = _one(
        con,
        f"""
        SELECT SUM(inventory_value) FILTER (WHERE snapshot_date = ?::DATE) AS v_cur,
               SUM(inventory_value) FILTER (WHERE snapshot_date = ?::DATE) AS v_base,
               SUM(inventory_value) FILTER (WHERE snapshot_date = ?::DATE AND product_id IN ({ids})) AS s_cur,
               SUM(inventory_value) FILTER (WHERE snapshot_date = ?::DATE AND product_id IN ({ids})) AS s_base
        FROM inventory_snapshots
        """,
        [cur, base, cur, base],
    )
    skus = _rows(
        con,
        """
        SELECT i.product_id, p.sku, p.product_name, p.category,
               SUM(i.inventory_value) AS inventory_value, SUM(i.on_hand_units) AS on_hand_units,
               SUM(i.units_sold_90d) AS units_sold_90d,
               CASE WHEN SUM(i.units_sold_90d) = 0 THEN NULL
                    ELSE SUM(i.on_hand_units) / (SUM(i.units_sold_90d) / 90.0) END AS days_of_supply
        FROM inventory_snapshots i JOIN products p USING (product_id)
        WHERE i.snapshot_date = ?::DATE
        GROUP BY ALL
        ORDER BY days_of_supply DESC NULLS FIRST, inventory_value DESC
        """,
        [cur],
    )
    top_dos = [s for s in skus if s["inventory_value"] and s["inventory_value"] > 20000][:15]
    slow_sales = _one(
        con,
        f"""
        SELECT SUM(quantity) FILTER (WHERE order_date BETWEEN '2026-04-01' AND '2026-06-30') AS units_q2_26,
               SUM(quantity) FILTER (WHERE order_date BETWEEN '2026-01-01' AND '2026-03-31') AS units_q1_26,
               SUM(quantity) FILTER (WHERE order_date BETWEEN '2025-10-01' AND '2025-12-31') AS units_q4_25
        FROM gt_sales WHERE product_id IN ({ids})
        """,
    )
    q2 = sales_kpis(con, *st.Q2_2026)
    q2b = sales_kpis(con, *st.Q2_2025)
    by_cat = _rows(
        con,
        """
        SELECT p.category,
               SUM(i.inventory_value) FILTER (WHERE snapshot_date = ?::DATE) AS v_cur,
               SUM(i.inventory_value) FILTER (WHERE snapshot_date = ?::DATE) AS v_base
        FROM inventory_snapshots i JOIN products p USING (product_id) GROUP BY 1 ORDER BY 1
        """,
        [cur, base],
    )
    increase = float(tot["v_cur"]) - float(tot["v_base"])
    slow_inc = float(tot["s_cur"] or 0) - float(tot["s_base"] or 0)
    return {
        "id": "inventory_buildup_2026",
        "title": "Inventory value rising while sales are flat (2026-06-30 vs 2025-06-30)",
        "questions": [
            "Which inventory is unhealthy?",
            "Why is inventory value up while sales are flat?",
        ],
        "metric": "inventory_value",
        "current_period": {"start": cur, "end": cur},
        "baseline_period": {"start": base, "end": base},
        "comparison": "yoy",
        "semi_additive_note": "Inventory Value is a month-end snapshot: compare single snapshot dates, never sum "
        "across months.",
        "headline": {
            "inventory_value_current": _r(tot["v_cur"]),
            "inventory_value_baseline": _r(tot["v_base"]),
            "inventory_value_pct_change": _pct(float(tot["v_cur"]), float(tot["v_base"])),
            "revenue_q2_pct_change_yoy": _pct(q2["revenue"], q2b["revenue"]),
        },
        "slow_moving_skus": {
            "product_ids": slow_ids,
            "launch_date": st.SLOW_SKU_LAUNCH,
            "inventory_value_current": _r(tot["s_cur"]),
            "inventory_value_baseline": _r(tot["s_base"] or 0),
            "share_of_increase": _r(slow_inc / increase, 4),
            "units_sold_by_quarter": {k: int(v or 0) for k, v in slow_sales.items()},
        },
        "highest_days_of_supply": [
            {k: (_r(v, 1) if isinstance(v, float) else v) for k, v in s.items()} for s in top_dos
        ],
        "by_category": [
            {
                "category": c["category"],
                "value_current": _r(c["v_cur"]),
                "value_baseline": _r(c["v_base"]),
                "pct_change": _pct(float(c["v_cur"]), float(c["v_base"])),
            }
            for c in by_cat
        ],
        "expected_discoveries": [
            {
                "id": "value_up",
                "kind": "headline",
                "metric": "inventory_value",
                "direction": "up",
                "min_pct": 0.12,
            },
            {"id": "sales_flat", "kind": "headline", "metric": "revenue", "max_abs_pct_change": 0.015},
            {
                "id": "slow_skus",
                "kind": "set",
                "dimension": "product",
                "values": slow_ids,
                "min_overlap_top15_days_of_supply": 10,
            },
        ],
    }


def _dq_issues(con) -> list[dict[str, Any]]:
    def n(sql: str) -> int:
        return int(con.execute(sql).fetchone()[0])

    return [
        {
            "table": "customers",
            "column": "customer_id",
            "issue": "duplicate rows (exact duplicates, inactive accounts)",
            "count": n("SELECT COUNT(*) - COUNT(DISTINCT customer_id) FROM customers"),
            "rule": "unique",
        },
        {
            "table": "customers",
            "column": "region",
            "issue": "null billing region",
            "count": n("SELECT COUNT(*) FROM customers WHERE region IS NULL"),
            "rule": "not_null",
        },
        {
            "table": "customers",
            "column": "region",
            "issue": "inconsistent casing",
            "count": n(
                "SELECT COUNT(*) FROM customers WHERE region IS NOT NULL AND region NOT IN "
                "(SELECT DISTINCT region FROM branches)"
            ),
            "rule": "allowed_values",
        },
        {
            "table": "customers",
            "column": "account_opened",
            "issue": "malformed or non-ISO dates",
            "count": n(
                "SELECT COUNT(*) FROM customers WHERE TRY_CAST(account_opened AS DATE) IS NULL "
                "OR NOT regexp_matches(account_opened, '^\\d{4}-\\d{2}-\\d{2}$')"
            ),
            "rule": "regex",
        },
        {
            "table": "customers",
            "column": "customer_name",
            "issue": "prompt-injection text in a data value",
            "count": n(
                "SELECT COUNT(*) FROM customers WHERE customer_name ILIKE 'Ignore previous instructions%'"
            ),
            "rule": None,
        },
        {
            "table": "order_lines",
            "column": "quantity",
            "issue": "negative quantities (returns keyed as orders)",
            "count": n("SELECT COUNT(*) FROM order_lines WHERE quantity < 0"),
            "rule": "range",
        },
        {
            "table": "orders",
            "column": "status",
            "issue": "cancelled orders (excluded by the Revenue metric filter)",
            "count": n("SELECT COUNT(*) FROM orders WHERE status = 'cancelled'"),
            "rule": None,
        },
        {
            "table": "returns",
            "column": "order_id",
            "issue": "whitespace-padded IDs",
            "count": n("SELECT COUNT(*) FROM returns WHERE order_id <> trim(order_id)"),
            "rule": "regex",
        },
        {
            "table": "products",
            "column": "supplier",
            "issue": "inconsistent casing",
            "count": n("SELECT COUNT(*) FROM products WHERE supplier = upper(supplier)"),
            "rule": None,
        },
        {
            "table": "budgets.xlsx",
            "column": None,
            "issue": "title rows, blank separator row and a notes row "
            "around the data; header on row 5 (Branch Budget) and row 4 (Category Budget)",
            "count": None,
            "rule": None,
        },
    ]


def measure_scenarios(
    data_dir: Path, manifest_files: list[Any] | None = None, prod: pd.DataFrame | None = None
) -> dict[str, Any]:
    con = connect(data_dir)
    try:
        if prod is not None and "is_slow_story" in prod:
            slow_ids = sorted(prod.loc[prod["is_slow_story"], "product_id"].tolist())
        else:
            slow_ids = [
                r[0]
                for r in con.execute(
                    "SELECT product_id FROM products WHERE launch_date = ?::DATE ORDER BY 1",
                    [st.SLOW_SKU_LAUNCH],
                ).fetchall()
            ]
        overall = _rows(
            con,
            "SELECT strftime(date_trunc('month', order_date), '%Y-%m') AS month, ROUND(SUM(net_amount), 2) AS revenue, "
            "COUNT(DISTINCT order_id) AS orders FROM gt_sales GROUP BY 1 ORDER BY 1",
        )
        stories = [
            _operating_profit_story(con),
            _revenue_story(con),
            _margin_story(con),
            _conversion_story(con),
            _inventory_story(con, slow_ids),
        ]
        return {
            "company": "Summit Supply Co.",
            "definitions": {
                "revenue": REVENUE_DEFINITION,
                "gross_revenue": "SUM(order_lines.gross_amount) (list price x quantity), non-cancelled",
                "cogs": "SUM(order_lines.cost_amount), non-cancelled",
                "gross_margin_pct": "(Revenue - COGS) / Revenue",
                "discount_rate": "SUM(discount_amount) / SUM(gross_amount)",
                "conversion_rate": "SUM(leads.is_converted) / COUNT(leads.lead_id) by lead created_date",
                "inventory_value": "SUM(inventory_snapshots.inventory_value) at one snapshot_date",
                "operating_expenses": "orders.freight_cost (non-cancelled) + order_lines.commission_amount "
                "(non-cancelled) + operating_expenses payroll + overtime + occupancy + other_opex",
                "operating_profit": "Revenue - COGS - operating expenses",
                "plan": f"{st.PLAN_VERSION}: sales_plan (month x region x segment x category) and opex_plan "
                "(month x branch); every plan column is the plan for the actual of the same name",
            },
            "monthly_revenue": overall,
            "stories": {s["id"]: s for s in stories},
            "data_quality_issues": _dq_issues(con),
        }
    finally:
        con.close()
