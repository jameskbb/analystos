"""Actual-versus-plan fixture for the plan-variance tests.

A distributor with two regions (North, Gulf), two customer segments (Contractor, Enterprise)
and two product categories (Lumber, Tools), planned per region x segment x category cell for
Q2 2026 and per branch for operating expenses. Every cell sells at the planned list price;
what differs from plan:

* Lumber unit cost is 66 instead of the planned 60 in every cell (unit-cost inflation). Lumber is
  bought from two suppliers (below plan grain): Pine Co (two products, half the units) costs 72,
  Oak Co (one product, half the units) stays at 60. A year earlier (Q2 2025, sold at plan
  volumes and discounts) Pine Co cost 58 and Oak Co 62 (a standing difference), so anchored to
  their own prior-year rates Pine Co carries more than the whole increase and Oak Co is favorable.
* Gulf Contractor discounts at 20% instead of the planned 10% (discounting), and sells 10%
  fewer units than planned; Enterprise sells 10% more Lumber (and Gulf Enterprise 20% more Tools)
  than planned (volume and mix, favorable).
* Freight = base freight + fuel surcharge. Base freight is 950 per cell as planned; the fuel
  surcharge is 550 in Gulf against a planned 50, so the surcharge carries the freight overrun.
* Labor = payroll + overtime. Gulf Hub overtime is 4,000 against 1,000 planned, partly offset by
  a payroll saving of 1,500 in the same branch; occupancy is 200 per branch under plan; two small
  lines (other opex +100, supplies -40) are immaterial.
* Gulf Paid Search lead conversion falls from 50% (Q1 2026 cohorts) to 10% (Q2 2026) and the
  median first response rises from 4 to 50 hours; North and referral leads do not change.

The plan metrics declare ``plan_for`` and the derived plan metrics mirror the actual tree, so
every node of ``operating_profit`` has a governed plan counterpart.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
from analystos_engine.semantic.models import SemanticModel
from analystos_engine.store import WorkspaceStore

TODAY = dt.date(2026, 9, 18)
FORMAL_QUESTION = (
    "We hit our revenue target last quarter, but operating profit missed plan badly. What happened?"
)
QUESTION = "We made our sales number last quarter, but we missed our target for net profit. What happened?"

# (region, segment, category): (plan_units, list_price, plan_discount, plan_unit_cost,
#                               actual_units, actual_discount, actual_unit_cost)
CELLS: dict[tuple[str, str, str], tuple[int, float, float, float, int, float, float]] = {
    ("North", "Contractor", "Lumber"): (1000, 100.0, 0.10, 60.0, 1000, 0.10, 66.0),
    ("North", "Contractor", "Tools"): (500, 200.0, 0.10, 120.0, 500, 0.10, 120.0),
    ("North", "Enterprise", "Lumber"): (1000, 100.0, 0.05, 60.0, 1100, 0.05, 66.0),
    ("North", "Enterprise", "Tools"): (500, 200.0, 0.05, 120.0, 500, 0.05, 120.0),
    ("Gulf", "Contractor", "Lumber"): (1000, 100.0, 0.10, 60.0, 900, 0.20, 66.0),
    ("Gulf", "Contractor", "Tools"): (500, 200.0, 0.10, 120.0, 450, 0.20, 120.0),
    ("Gulf", "Enterprise", "Lumber"): (1000, 100.0, 0.05, 60.0, 1100, 0.05, 66.0),
    ("Gulf", "Enterprise", "Tools"): (500, 200.0, 0.05, 120.0, 600, 0.05, 120.0),
}
PLAN_BASE_FREIGHT, PLAN_SURCHARGE = 950.0, 50.0
PLAN_FREIGHT = PLAN_BASE_FREIGHT + PLAN_SURCHARGE
ACTUAL_SURCHARGE = {"North": 50.0, "Gulf": 550.0}
ACTUAL_FREIGHT = {r: PLAN_BASE_FREIGHT + v for r, v in ACTUAL_SURCHARGE.items()}
BRANCHES = [(1, "North Hub", "North"), (2, "Gulf Hub", "Gulf")]
# branch_id: (payroll, overtime, occupancy, other_opex, supplies) actual and plan
OPEX_ACTUAL = {
    1: (40_000.0, 1_000.0, 4_800.0, 1_000.0, 500.0),
    2: (38_500.0, 4_000.0, 4_800.0, 1_100.0, 460.0),
}
OPEX_PLAN = {1: (40_000.0, 1_000.0, 5_000.0, 1_000.0, 500.0), 2: (40_000.0, 1_000.0, 5_000.0, 1_000.0, 500.0)}
# Lumber products: (product_id, name, supplier_id, share of the cell's units, actual unit cost)
LUMBER = [(1, "2x4 Stud", 1, 0.25, 72.0), (3, "Beam", 1, 0.25, 72.0), (4, "Plank", 2, 0.5, 60.0)]
LUMBER_PRIOR_COST = {1: 58.0, 3: 58.0, 4: 62.0}  # Q2 2025 unit cost by product
SUPPLIERS = [(1, "Pine Co"), (2, "Oak Co"), (3, "Tool Co")]
# (branch_id, channel): ((q1 leads, q1 converted, q1 response h), (q2 ...))
LEADS = {
    (2, "Paid Search"): ((10, 5, 4.0), (10, 1, 50.0)),
    (2, "Referral"): ((10, 3, 6.0), (10, 3, 6.0)),
    (1, "Paid Search"): ((30, 15, 4.0), (30, 15, 4.0)),
    (1, "Referral"): ((30, 9, 6.0), (30, 9, 6.0)),
}


def expected() -> dict[str, float]:
    """The answer, computed with plain arithmetic from the cell table (no SQL)."""
    rev1 = rev0 = cogs1 = cogs0 = cost_eff = disc_eff = 0.0
    for (_r, _s, _c), (q0, p, d0, c0, q1, d1, c1) in CELLS.items():
        rev0 += q0 * p * (1 - d0)
        cogs0 += q0 * c0
        rev1 += q1 * p * (1 - d1)
        cogs1 += q1 * c1
        cost_eff += q1 * c0 - q1 * c1
        disc_eff += q1 * p * (1 - d1) - q1 * p * (1 - d0)
    gm1, gm0 = rev1 - cogs1, rev0 - cogs0
    freight1 = sum(ACTUAL_FREIGHT[r] for (r, _s, _c) in CELLS)
    freight0 = PLAN_FREIGHT * len(CELLS)
    opex1 = freight1 + sum(sum(v) for v in OPEX_ACTUAL.values())
    opex0 = freight0 + sum(sum(v) for v in OPEX_PLAN.values())
    return {
        "revenue": rev1,
        "plan_revenue": rev0,
        "gross_margin": gm1,
        "plan_gross_margin": gm0,
        "operating_expenses": opex1,
        "plan_operating_expenses": opex0,
        "operating_profit": gm1 - opex1,
        "plan_operating_profit": gm0 - opex0,
        "unit_cost_effect": cost_eff,
        "discount_effect": disc_eff,
        "mix_effect": (gm1 - gm0) - cost_eff - disc_eff,
        "freight_variance": freight1 - freight0,
        "surcharge_variance": sum(ACTUAL_SURCHARGE[r] - PLAN_SURCHARGE for (r, _s, _c) in CELLS),
        "overtime_variance": sum(v[1] for v in OPEX_ACTUAL.values()) - sum(v[1] for v in OPEX_PLAN.values()),
        "payroll_variance": sum(v[0] for v in OPEX_ACTUAL.values()) - sum(v[0] for v in OPEX_PLAN.values()),
        "other_lines_variance": sum(v[3] + v[4] for v in OPEX_ACTUAL.values())
        - sum(v[3] + v[4] for v in OPEX_PLAN.values()),
        "pine_cost_effect_blended": sum(
            q1 * (c0 - 72.0) * 0.5
            for (_r, _s, c), (_q0, _p, _d0, c0, q1, _d1, _c1) in CELLS.items()
            if c == "Lumber"
        ),
        "pine_cost_effect": sum(
            q1 * 0.5 * (58.0 * c0 / 60.0 - 72.0)
            for (_r, _s, c), (_q0, _p, _d0, c0, q1, _d1, _c1) in CELLS.items()
            if c == "Lumber"
        ),
    }


def build_database(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE regions (region VARCHAR)")
        con.executemany("INSERT INTO regions VALUES (?)", [("North",), ("Gulf",)])
        con.execute("CREATE TABLE customer_segments (segment VARCHAR)")
        con.executemany("INSERT INTO customer_segments VALUES (?)", [("Contractor",), ("Enterprise",)])
        con.execute("CREATE TABLE product_categories (category VARCHAR)")
        con.executemany("INSERT INTO product_categories VALUES (?)", [("Lumber",), ("Tools",)])
        con.execute("CREATE TABLE branches (branch_id INTEGER, branch_name VARCHAR, region VARCHAR)")
        con.executemany("INSERT INTO branches VALUES (?, ?, ?)", BRANCHES)
        con.execute("CREATE TABLE customers (customer_id INTEGER, customer_name VARCHAR, segment VARCHAR)")
        con.executemany(
            "INSERT INTO customers VALUES (?, ?, ?)",
            [(1, "Acme Builders", "Contractor"), (2, "MegaCorp", "Enterprise")],
        )
        con.execute("CREATE TABLE suppliers (supplier_id INTEGER, supplier_name VARCHAR)")
        con.executemany("INSERT INTO suppliers VALUES (?, ?)", SUPPLIERS)
        con.execute(
            "CREATE TABLE products (product_id INTEGER, product_name VARCHAR, category VARCHAR, supplier_id INTEGER)"
        )
        con.executemany(
            "INSERT INTO products VALUES (?, ?, ?, ?)",
            [(pid, name, "Lumber", sid) for pid, name, sid, _sh, _c in LUMBER] + [(2, "Drill", "Tools", 3)],
        )
        con.execute(
            "CREATE TABLE orders (order_id INTEGER, order_date DATE, customer_id INTEGER, branch_id INTEGER, "
            "freight_cost DOUBLE, base_freight DOUBLE, fuel_surcharge DOUBLE)"
        )
        con.execute(
            "CREATE TABLE order_lines (line_id INTEGER, order_id INTEGER, product_id INTEGER, quantity INTEGER, "
            "gross_amount DOUBLE, net_amount DOUBLE, cost_amount DOUBLE)"
        )
        con.execute(
            "CREATE TABLE sales_plan (plan_month DATE, region VARCHAR, segment VARCHAR, category VARCHAR, "
            "plan_units INTEGER, plan_gross_revenue DOUBLE, plan_revenue DOUBLE, plan_cogs DOUBLE, "
            "plan_base_freight DOUBLE, plan_fuel_surcharge DOUBLE)"
        )
        con.execute(
            "CREATE TABLE operating_expenses (expense_month DATE, branch_id INTEGER, payroll DOUBLE, "
            "overtime DOUBLE, occupancy DOUBLE, other_opex DOUBLE, supplies DOUBLE)"
        )
        con.execute(
            "CREATE TABLE opex_plan (plan_month DATE, branch_id INTEGER, plan_payroll DOUBLE, plan_overtime DOUBLE, "
            "plan_occupancy DOUBLE, plan_other_opex DOUBLE, plan_supplies DOUBLE)"
        )
        con.execute(
            "CREATE TABLE leads (lead_id INTEGER, created_date DATE, branch_id INTEGER, channel VARCHAR, "
            "is_converted INTEGER, first_response_hours DOUBLE)"
        )
        branch_of = {r: b for b, _n, r in BRANCHES}
        customer_of = {"Contractor": 1, "Enterprise": 2}
        product_of = {"Lumber": 1, "Tools": 2}
        orders, lines, plan = [], [], []
        oid = 0
        lid = 0
        for (region, segment, category), (q0, p, d0, c0, q1, d1, c1) in sorted(CELLS.items()):
            oid += 1
            orders.append(
                (
                    oid,
                    dt.date(2026, 5, 15),
                    customer_of[segment],
                    branch_of[region],
                    ACTUAL_FREIGHT[region],
                    PLAN_BASE_FREIGHT,
                    ACTUAL_SURCHARGE[region],
                )
            )
            parts = (
                [(pid, share, cost) for pid, _n, _sid, share, cost in LUMBER]
                if category == "Lumber"
                else [(product_of[category], 1.0, c1)]
            )
            for pid, share, cost in parts:
                q = round(q1 * share)
                lid += 1
                lines.append((lid, oid, pid, q, q * p, q * p * (1 - d1), q * cost))
            plan.append(
                (
                    dt.date(2026, 4, 1),
                    region,
                    segment,
                    category,
                    q0,
                    q0 * p,
                    q0 * p * (1 - d0),
                    q0 * c0,
                    PLAN_BASE_FREIGHT,
                    PLAN_SURCHARGE,
                )
            )
            # the same cell a quarter earlier, sold exactly at plan (a prior-period baseline)
            oid += 1
            orders.append(
                (
                    oid,
                    dt.date(2026, 2, 15),
                    customer_of[segment],
                    branch_of[region],
                    PLAN_FREIGHT,
                    PLAN_BASE_FREIGHT,
                    PLAN_SURCHARGE,
                )
            )
            lid += 1
            lines.append((lid, oid, product_of[category], q0, q0 * p, q0 * p * (1 - d0), q0 * c0))
            # a year earlier, at plan volumes and discounts; lumber suppliers at their prior-year costs
            oid += 1
            orders.append(
                (
                    oid,
                    dt.date(2025, 5, 15),
                    customer_of[segment],
                    branch_of[region],
                    PLAN_FREIGHT,
                    PLAN_BASE_FREIGHT,
                    PLAN_SURCHARGE,
                )
            )
            prior = (
                [(pid, share, LUMBER_PRIOR_COST[pid]) for pid, _n, _sid, share, _c in LUMBER]
                if category == "Lumber"
                else [(product_of[category], 1.0, c0)]
            )
            for pid, share, cost in prior:
                q = round(q0 * share)
                lid += 1
                lines.append((lid, oid, pid, q, q * p, q * p * (1 - d0), q * cost))
        con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", orders)
        con.executemany("INSERT INTO order_lines VALUES (?, ?, ?, ?, ?, ?, ?)", lines)
        con.executemany("INSERT INTO sales_plan VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", plan)
        con.executemany(
            "INSERT INTO operating_expenses VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(dt.date(2026, 4, 1), b, *v) for b, v in OPEX_ACTUAL.items()],
        )
        con.executemany(
            "INSERT INTO opex_plan VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(dt.date(2026, 4, 1), b, *v) for b, v in OPEX_PLAN.items()],
        )
        leads = []
        lid = 0
        for (branch, channel), quarters in sorted(LEADS.items()):
            for day, (n, conv, hours) in zip(
                (dt.date(2026, 2, 10), dt.date(2026, 5, 10)), quarters, strict=True
            ):
                for k in range(n):
                    lid += 1
                    leads.append((lid, day, branch, channel, 1 if k < conv else 0, hours))
        con.executemany("INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?)", leads)
    finally:
        con.close()


MODEL_YAML = """
name: plan_fixture
entities:
  - {name: order_lines, table: order_lines, primary_key: line_id}
  - {name: orders, table: orders, primary_key: order_id, default_time_dimension: order_date}
  - {name: customers, table: customers, primary_key: customer_id}
  - {name: customer_segments, table: customer_segments, primary_key: segment}
  - {name: branches, table: branches, primary_key: branch_id}
  - {name: regions, table: regions, primary_key: region}
  - {name: products, table: products, primary_key: product_id}
  - {name: suppliers, table: suppliers, primary_key: supplier_id}
  - {name: product_categories, table: product_categories, primary_key: category}
  - {name: operating_expenses, table: operating_expenses, primary_key: [expense_month, branch_id],
     default_time_dimension: expense_month}
  - {name: sales_plan, table: sales_plan, primary_key: [plan_month, region, segment, category],
     default_time_dimension: plan_month}
  - {name: opex_plan, table: opex_plan, primary_key: [plan_month, branch_id], default_time_dimension: opex_plan_month}
  - {name: leads, table: leads, primary_key: lead_id, default_time_dimension: lead_created_date}
relationships:
  - {from_entity: order_lines, from_col: order_id, to_entity: orders, to_col: order_id, cardinality: many_to_one, approved: true}
  - {from_entity: order_lines, from_col: product_id, to_entity: products, to_col: product_id, cardinality: many_to_one, approved: true}
  - {from_entity: products, from_col: supplier_id, to_entity: suppliers, to_col: supplier_id, cardinality: many_to_one, approved: true}
  - {from_entity: products, from_col: category, to_entity: product_categories, to_col: category, cardinality: many_to_one, approved: true}
  - {from_entity: orders, from_col: customer_id, to_entity: customers, to_col: customer_id, cardinality: many_to_one, approved: true}
  - {from_entity: customers, from_col: segment, to_entity: customer_segments, to_col: segment, cardinality: many_to_one, approved: true}
  - {from_entity: orders, from_col: branch_id, to_entity: branches, to_col: branch_id, cardinality: many_to_one, approved: true}
  - {from_entity: branches, from_col: region, to_entity: regions, to_col: region, cardinality: many_to_one, approved: true}
  - {from_entity: operating_expenses, from_col: branch_id, to_entity: branches, to_col: branch_id, cardinality: many_to_one, approved: true}
  - {from_entity: opex_plan, from_col: branch_id, to_entity: branches, to_col: branch_id, cardinality: many_to_one, approved: true}
  - {from_entity: sales_plan, from_col: region, to_entity: regions, to_col: region, cardinality: many_to_one, approved: true}
  - {from_entity: sales_plan, from_col: segment, to_entity: customer_segments, to_col: segment, cardinality: many_to_one, approved: true}
  - {from_entity: sales_plan, from_col: category, to_entity: product_categories, to_col: category, cardinality: many_to_one, approved: true}
  - {from_entity: leads, from_col: branch_id, to_entity: branches, to_col: branch_id, cardinality: many_to_one, approved: true}
dimensions:
  - {name: order_date, entity: orders, expr: order_date, type: time}
  - {name: expense_month, entity: operating_expenses, expr: expense_month, type: time}
  - {name: plan_month, entity: sales_plan, expr: plan_month, type: time}
  - {name: opex_plan_month, entity: opex_plan, expr: plan_month, type: time}
  - {name: lead_created_date, entity: leads, expr: created_date, type: time}
  - {name: region, entity: regions, expr: region, label: Region}
  - {name: segment, entity: customer_segments, expr: segment, label: Customer Segment}
  - {name: category, entity: product_categories, expr: category, label: Product Category}
  - {name: branch, entity: branches, expr: branch_name, label: Branch}
  - {name: customer, entity: customers, expr: customer_name, label: Customer}
  - {name: product, entity: products, expr: product_name, label: Product}
  - {name: supplier, entity: suppliers, expr: supplier_name, label: Supplier}
  - {name: lead_channel, entity: leads, expr: channel, label: Lead Channel}
metrics:
  - {id: revenue, name: Revenue, entity: order_lines, agg: sum, expr: net_amount, format: currency,
     tags: [sales, core], synonyms: [sales], default_time_dimension: order_date,
     related_metrics: [conversion_rate]}
  - {id: gross_revenue, name: Gross Revenue, entity: order_lines, agg: sum, expr: gross_amount, format: currency,
     default_time_dimension: order_date}
  - {id: discount_amount, name: Discounts, entity: order_lines, agg: sum, expr: gross_amount - net_amount,
     format: currency, tags: [pricing], default_time_dimension: order_date, higher_is_better: false}
  - {id: discount_rate, name: Discount Rate, kind: ratio, numerator: discount_amount, denominator: gross_revenue,
     format: percent, higher_is_better: false}
  - {id: units, name: Units, entity: order_lines, agg: sum, expr: quantity, format: integer,
     default_time_dimension: order_date}
  - {id: cogs, name: COGS, entity: order_lines, agg: sum, expr: cost_amount, format: currency, tags: [cost],
     default_time_dimension: order_date, higher_is_better: false}
  - {id: gross_margin, name: Gross Margin, kind: derived, formula: revenue - cogs, format: currency,
     tags: [margin, core]}
  - {id: freight_cost, name: Freight Cost, label: Freight, entity: orders, agg: sum, expr: freight_cost,
     format: currency, default_time_dimension: order_date, higher_is_better: false}
  - {id: base_freight, name: Base Freight, entity: orders, agg: sum, expr: base_freight,
     format: currency, default_time_dimension: order_date, higher_is_better: false}
  - {id: fuel_surcharge, name: Fuel Surcharge, entity: orders, agg: sum, expr: fuel_surcharge,
     format: currency, default_time_dimension: order_date, higher_is_better: false}
  - {id: labor_cost, name: Labor Cost, label: Labor, kind: derived, formula: payroll + overtime, format: currency,
     higher_is_better: false}
  - {id: other_opex, name: Other Operating Expenses, entity: operating_expenses, agg: sum, expr: other_opex,
     format: currency, higher_is_better: false}
  - {id: supplies, name: Supplies, entity: operating_expenses, agg: sum, expr: supplies, format: currency,
     higher_is_better: false}
  - {id: payroll, name: Payroll, entity: operating_expenses, agg: sum, expr: payroll, format: currency,
     higher_is_better: false}
  - {id: overtime, name: Overtime, entity: operating_expenses, agg: sum, expr: overtime, format: currency,
     higher_is_better: false}
  - {id: occupancy, name: Occupancy, entity: operating_expenses, agg: sum, expr: occupancy, format: currency,
     higher_is_better: false}
  - {id: operating_expenses, name: Operating Expenses, kind: derived,
     formula: freight_cost + labor_cost + occupancy + other_opex + supplies, format: currency,
     higher_is_better: false}
  - {id: operating_profit, name: Operating Profit, kind: derived, formula: gross_margin - operating_expenses,
     format: currency, tags: [margin, core], synonyms: [operating income]}
  - {id: plan_revenue, name: Plan Revenue, entity: sales_plan, agg: sum, expr: plan_revenue, format: currency,
     tags: [plan], plan_for: revenue}
  - {id: plan_gross_revenue, name: Plan Gross Revenue, entity: sales_plan, agg: sum, expr: plan_gross_revenue,
     format: currency, tags: [plan], plan_for: gross_revenue}
  - {id: plan_units, name: Plan Units, entity: sales_plan, agg: sum, expr: plan_units, format: integer,
     tags: [plan], plan_for: units}
  - {id: plan_cogs, name: Plan COGS, entity: sales_plan, agg: sum, expr: plan_cogs, format: currency,
     tags: [plan], plan_for: cogs}
  - {id: plan_gross_margin, name: Plan Gross Margin, kind: derived, formula: plan_revenue - plan_cogs,
     format: currency, tags: [plan], plan_for: gross_margin}
  - {id: plan_base_freight, name: Plan Base Freight, entity: sales_plan, agg: sum, expr: plan_base_freight,
     format: currency, tags: [plan], plan_for: base_freight}
  - {id: plan_fuel_surcharge, name: Plan Fuel Surcharge, entity: sales_plan, agg: sum, expr: plan_fuel_surcharge,
     format: currency, tags: [plan], plan_for: fuel_surcharge}
  - {id: plan_freight_cost, name: Plan Freight, kind: derived, formula: plan_base_freight + plan_fuel_surcharge,
     format: currency, tags: [plan], plan_for: freight_cost}
  - {id: plan_labor_cost, name: Plan Labor, kind: derived, formula: plan_payroll + plan_overtime,
     format: currency, tags: [plan], plan_for: labor_cost}
  - {id: plan_other_opex, name: Plan Other Operating Expenses, entity: opex_plan, agg: sum, expr: plan_other_opex,
     format: currency, tags: [plan], plan_for: other_opex}
  - {id: plan_supplies, name: Plan Supplies, entity: opex_plan, agg: sum, expr: plan_supplies, format: currency,
     tags: [plan], plan_for: supplies}
  - {id: plan_payroll, name: Plan Payroll, entity: opex_plan, agg: sum, expr: plan_payroll, format: currency,
     tags: [plan], plan_for: payroll}
  - {id: plan_overtime, name: Plan Overtime, entity: opex_plan, agg: sum, expr: plan_overtime, format: currency,
     tags: [plan], plan_for: overtime}
  - {id: plan_occupancy, name: Plan Occupancy, entity: opex_plan, agg: sum, expr: plan_occupancy,
     format: currency, tags: [plan], plan_for: occupancy}
  - {id: plan_operating_expenses, name: Plan Operating Expenses, kind: derived,
     formula: plan_freight_cost + plan_labor_cost + plan_occupancy + plan_other_opex + plan_supplies,
     format: currency, tags: [plan],
     plan_for: operating_expenses}
  - {id: plan_operating_profit, name: Plan Operating Profit, kind: derived,
     formula: plan_gross_margin - plan_operating_expenses, format: currency, tags: [plan],
     plan_for: operating_profit}
  - {id: revenue_target, name: Revenue Target, entity: sales_plan, agg: sum, expr: plan_revenue,
     format: currency, tags: [plan, targets], synonyms: [target, quota]}
  - {id: leads, name: Leads, entity: leads, agg: count, format: integer}
  - {id: converted_leads, name: Converted Leads, entity: leads, agg: sum, expr: is_converted, format: integer}
  - {id: conversion_rate, name: Conversion Rate, kind: ratio, numerator: converted_leads, denominator: leads,
     format: percent, related_metrics: [median_first_response_hours]}
  - {id: median_first_response_hours, name: Median First Response, entity: leads, agg: median,
     expr: first_response_hours, format: number, unit: hours, higher_is_better: false}
metric_trees:
  - root_metric: operating_profit
    nodes:
      - {parent: operating_profit, child: gross_margin, relation: additive}
      - {parent: operating_profit, child: operating_expenses, relation: subtractive}
      - {parent: gross_margin, child: revenue, relation: additive}
      - {parent: gross_margin, child: cogs, relation: subtractive}
      - {parent: operating_expenses, child: freight_cost, relation: additive}
      - {parent: operating_expenses, child: labor_cost, relation: additive}
      - {parent: operating_expenses, child: occupancy, relation: additive}
      - {parent: operating_expenses, child: other_opex, relation: additive}
      - {parent: operating_expenses, child: supplies, relation: additive}
      - {parent: freight_cost, child: base_freight, relation: additive}
      - {parent: freight_cost, child: fuel_surcharge, relation: additive}
      - {parent: labor_cost, child: payroll, relation: additive}
      - {parent: labor_cost, child: overtime, relation: additive}
  - root_metric: gross_revenue
    nodes:
      - {parent: gross_revenue, child: revenue, relation: additive}
      - {parent: gross_revenue, child: discount_amount, relation: additive}
  - root_metric: conversion_rate
    nodes:
      - {parent: conversion_rate, child: converted_leads, relation: ratio_numerator}
      - {parent: conversion_rate, child: leads, relation: ratio_denominator}
glossary:
  - term: Net Profit
    definition: The lowest governed profit line here is operating profit (no interest or tax in the data).
    metric_id: operating_profit
  - term: Margin
    definition: Ambiguous; gross margin or operating profit.
    candidate_metric_ids: [gross_margin, operating_profit]
"""


def load_model() -> SemanticModel:
    model = SemanticModel.from_yaml(MODEL_YAML)
    model.raise_for_errors()
    return model


def make_store(directory: Path) -> WorkspaceStore:
    directory.mkdir(parents=True, exist_ok=True)
    build_database(directory / WorkspaceStore.DB_FILENAME)
    return WorkspaceStore(directory)
