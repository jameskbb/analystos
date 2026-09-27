"""SQL correctness: the semantic compiler against hand-written ground-truth SQL (spec §82).

Each case compiles a MetricQuery with the engine, runs it on the loaded workspace store, and
compares every cell with SQL written by hand over the raw files in an independent DuckDB
connection. Cases target the classic failure modes: join fan-out, grain, metric filters
(cancelled orders), half-open date windows, ratio-of-sums, cross-entity metrics and
conformed dimensions.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any

import pytest

D = dt.date


@dataclass
class Case:
    id: str
    metrics: list[str]
    dimensions: list[str]
    start: dt.date | None
    end: dt.date | None  # exclusive
    truth_sql: str
    filters: list[dict[str, Any]] = field(default_factory=list)
    time_dimension: str | None = None
    grain: str | None = None
    why: str = ""


SALES = """
    SELECT l.*, o.order_date, o.channel, o.branch_id, o.customer_id, o.status
    FROM order_lines l JOIN orders o USING (order_id)
    WHERE o.status <> 'cancelled'
"""

CASES: list[Case] = [
    Case(
        "revenue_excludes_cancelled_by_month",
        ["revenue"],
        [],
        D(2026, 1, 1),
        D(2026, 10, 1),
        f"""SELECT date_trunc('month', order_date)::DATE AS m, SUM(net_amount) FROM ({SALES})
            WHERE order_date >= '2026-01-01' AND order_date < '2026-10-01' GROUP BY 1""",
        grain="month",
        why="metric filter on a parent entity + month grain",
    ),
    Case(
        "august_window_is_half_open",
        ["revenue", "orders", "units"],
        [],
        D(2026, 8, 1),
        D(2026, 9, 1),
        f"""SELECT SUM(net_amount), COUNT(DISTINCT order_id), SUM(quantity) FROM ({SALES})
            WHERE order_date BETWEEN '2026-08-01' AND '2026-08-31'""",
        why="Aug 31 included, Sep 1 excluded",
    ),
    Case(
        "revenue_by_branch_two_hop_join",
        ["revenue"],
        ["branch"],
        D(2026, 8, 1),
        D(2026, 9, 1),
        f"""SELECT b.branch_name, SUM(s.net_amount) FROM ({SALES}) s JOIN branches b USING (branch_id)
            WHERE s.order_date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1""",
        why="order_lines -> orders -> branches",
    ),
    Case(
        "aov_by_segment_through_duplicate_customer_rows",
        ["revenue", "orders", "aov"],
        ["segment"],
        D(2026, 7, 1),
        D(2026, 8, 1),
        f"""SELECT c.segment, SUM(s.net_amount), COUNT(DISTINCT s.order_id),
                   SUM(s.net_amount) / COUNT(DISTINCT s.order_id)
            FROM ({SALES}) s JOIN customers_dedup c USING (customer_id)
            WHERE s.order_date BETWEEN '2026-07-01' AND '2026-07-31' GROUP BY 1""",
        why="customers.csv has duplicate rows; AOV must be a ratio of sums",
    ),
    Case(
        "revenue_by_region_and_category",
        ["revenue"],
        ["region", "category"],
        D(2026, 4, 1),
        D(2026, 7, 1),
        f"""SELECT b.region, p.category, SUM(s.net_amount)
            FROM ({SALES}) s JOIN branches b USING (branch_id) JOIN products p USING (product_id)
            WHERE s.order_date BETWEEN '2026-04-01' AND '2026-06-30' GROUP BY 1, 2""",
        why="conformed region (via branches -> regions) and product category together",
    ),
    Case(
        "gross_margin_pct_by_category_quarter",
        ["gross_margin", "gross_margin_pct", "cogs"],
        ["category"],
        D(2026, 4, 1),
        D(2026, 7, 1),
        f"""SELECT p.category, SUM(net_amount) - SUM(cost_amount),
                   (SUM(net_amount) - SUM(cost_amount)) / SUM(net_amount), SUM(cost_amount)
            FROM ({SALES}) s JOIN products p USING (product_id)
            WHERE order_date BETWEEN '2026-04-01' AND '2026-06-30' GROUP BY 1""",
        why="derived and ratio metrics over a quarter",
    ),
    Case(
        "filters_region_in_and_channel_eq",
        ["revenue", "orders"],
        ["branch"],
        D(2026, 8, 1),
        D(2026, 9, 1),
        f"""SELECT b.branch_name, SUM(s.net_amount), COUNT(DISTINCT s.order_id)
            FROM ({SALES}) s JOIN branches b USING (branch_id)
            WHERE s.order_date BETWEEN '2026-08-01' AND '2026-08-31'
              AND b.region IN ('North Texas', 'Oklahoma') AND s.channel = 'Online' GROUP BY 1""",
        filters=[
            {"dimension": "region", "op": "in", "values": ["North Texas", "Oklahoma"]},
            {"dimension": "channel", "op": "eq", "values": ["Online"]},
        ],
        why="query filters on two different joined entities",
    ),
    Case(
        "discount_rate_and_asp_by_tier",
        ["discount_rate", "asp"],
        ["product_tier"],
        D(2026, 8, 1),
        D(2026, 9, 1),
        f"""SELECT p.tier, SUM(discount_amount) / SUM(gross_amount), SUM(net_amount) / SUM(quantity)
            FROM ({SALES}) s JOIN products p USING (product_id)
            WHERE order_date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1""",
        why="ratio of sums, never an average of line ratios",
    ),
    Case(
        "orders_by_category_is_distinct_per_category",
        ["orders"],
        ["category"],
        D(2026, 8, 1),
        D(2026, 9, 1),
        f"""SELECT p.category, COUNT(DISTINCT s.order_id) FROM ({SALES}) s JOIN products p USING (product_id)
            WHERE order_date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1""",
        why="non-additive count distinct across a child-grain dimension",
    ),
    Case(
        "active_customers_and_frequency_by_region",
        ["active_customers", "orders_per_customer", "units_per_order"],
        ["region"],
        D(2026, 8, 1),
        D(2026, 9, 1),
        f"""WITH o AS (SELECT o.*, b.region FROM orders o JOIN branches b USING (branch_id)
                       WHERE status <> 'cancelled' AND order_date BETWEEN '2026-08-01' AND '2026-08-31'),
                 l AS (SELECT b.region, COUNT(DISTINCT s.order_id) AS n, SUM(quantity) AS u
                       FROM ({SALES}) s JOIN branches b USING (branch_id)
                       WHERE order_date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1)
            SELECT o.region, COUNT(DISTINCT o.customer_id), MAX(l.n) / COUNT(DISTINCT o.customer_id),
                   MAX(l.u) / MAX(l.n)
            FROM o JOIN l USING (region) GROUP BY 1""",
        why="metrics from two grains (orders, order_lines) pre-aggregated before they meet",
    ),
    Case(
        "conversion_rate_by_lead_channel",
        ["leads", "converted_leads", "conversion_rate"],
        ["lead_channel"],
        D(2026, 4, 1),
        D(2026, 7, 1),
        """SELECT channel, COUNT(*), SUM(is_converted), SUM(is_converted) / COUNT(*) FROM leads
           WHERE created_date BETWEEN '2026-04-01' AND '2026-06-30' GROUP BY 1""",
        why="cohort metric dated by lead creation",
    ),
    Case(
        "conversion_rate_by_region_through_branch",
        ["conversion_rate"],
        ["region"],
        D(2026, 4, 1),
        D(2026, 7, 1),
        """SELECT b.region, SUM(is_converted) / COUNT(*) FROM leads l JOIN branches b USING (branch_id)
           WHERE created_date BETWEEN '2026-04-01' AND '2026-06-30' GROUP BY 1""",
        why="conformed region reached from leads",
    ),
    Case(
        "inventory_value_at_one_snapshot",
        ["inventory_value", "on_hand_units", "days_of_supply"],
        ["category"],
        D(2026, 6, 30),
        D(2026, 7, 1),
        """SELECT p.category, SUM(inventory_value), SUM(on_hand_units),
                  SUM(inventory_value) / SUM(units_sold_90d * unit_cost / 90.0)
           FROM inventory_snapshots i JOIN products p USING (product_id)
           WHERE snapshot_date = '2026-06-30' GROUP BY 1""",
        why="semi-additive snapshot metric at a single date",
    ),
    Case(
        "return_rate_cross_entity_time",
        ["returns_amount", "revenue", "return_rate"],
        [],
        D(2026, 6, 1),
        D(2026, 7, 1),
        f"""SELECT r.amt, s.rev, r.amt / s.rev FROM
              (SELECT SUM(return_amount) AS amt FROM returns
               WHERE return_date BETWEEN '2026-06-01' AND '2026-06-30') r,
              (SELECT SUM(net_amount) AS rev FROM ({SALES}) WHERE order_date BETWEEN '2026-06-01' AND '2026-06-30') s""",
        why="numerator dated by return_date, denominator by order_date",
    ),
    Case(
        "operating_margin_by_branch_cross_entity",
        ["contribution_margin", "operating_expenses", "operating_margin"],
        ["branch"],
        D(2026, 6, 1),
        D(2026, 7, 1),
        f"""WITH s AS (SELECT branch_id, SUM(net_amount) - SUM(cost_amount) - SUM(commission_amount) AS gm_less_comm
                      FROM ({SALES}) WHERE order_date BETWEEN '2026-06-01' AND '2026-06-30' GROUP BY 1),
                 f AS (SELECT branch_id, SUM(freight_cost) AS freight FROM orders
                      WHERE status <> 'cancelled' AND order_date BETWEEN '2026-06-01' AND '2026-06-30' GROUP BY 1),
                 x AS (SELECT branch_id, SUM(total_opex) AS opex FROM operating_expenses
                      WHERE expense_month = '2026-06-01' GROUP BY 1)
            SELECT b.branch_name, s.gm_less_comm - f.freight, x.opex, s.gm_less_comm - f.freight - x.opex
            FROM s JOIN f USING (branch_id) JOIN x USING (branch_id) JOIN branches b USING (branch_id)""",
        why="three entities, two date columns, one conformed branch dimension",
    ),
    Case(
        "actual_and_forecast_by_segment",
        ["revenue", "forecast_revenue"],
        ["segment"],
        D(2026, 4, 1),
        D(2026, 7, 1),
        f"""WITH a AS (SELECT c.segment, SUM(s.net_amount) AS rev FROM ({SALES}) s
                      JOIN customers_dedup c USING (customer_id)
                      WHERE order_date BETWEEN '2026-04-01' AND '2026-06-30' GROUP BY 1),
                 f AS (SELECT segment, SUM(forecast_revenue) AS fc FROM revenue_forecast
                      WHERE forecast_month BETWEEN '2026-04-01' AND '2026-06-30' GROUP BY 1)
            SELECT segment, a.rev, f.fc FROM a JOIN f USING (segment)""",
        why="actual vs plan on a conformed dimension",
    ),
    Case(
        "budget_from_excel_by_region",
        ["budget_revenue"],
        ["region"],
        D(2026, 8, 1),
        D(2026, 9, 1),
        """SELECT b.region, SUM(bb.revenue_budget) FROM budgets_branch bb JOIN branches b USING (branch_id)
           WHERE bb.budget_month = '2026-08-01' GROUP BY 1""",
        why="messy workbook ingested with explicit header rows",
    ),
]


def _query(case: Case) -> Any:
    from analystos_engine.semantic.compiler import MetricQuery
    from analystos_engine.semantic.models import Filter
    from analystos_engine.types import TimeWindow

    time = None
    if case.start:
        time = TimeWindow(start=case.start, end=case.end, dimension=case.time_dimension, grain=case.grain)
    return MetricQuery(
        metrics=case.metrics,
        dimensions=case.dimensions,
        filters=[Filter(**f) for f in case.filters],
        time=time,
    )


def _key(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    return v


def _close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    a, b = float(a), float(b)
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_compiled_sql_matches_ground_truth(case: Case, store: Any, model: Any, gt: Any) -> None:
    from analystos_engine.semantic.compiler import compile as compile_query
    from analystos_engine.sqlsafety import ensure_read_only

    compiled = compile_query(model, _query(case))
    ensure_read_only(compiled.sql)
    result = store.execute_read(compiled.sql, limit=100_000)
    n_dims = len(case.dimensions) + (1 if case.grain else 0)
    got = {tuple(_key(v) for v in row[:n_dims]): row[n_dims:] for row in result.rows}
    truth_rows = gt.execute(case.truth_sql).fetchall()
    want = {tuple(_key(v) for v in row[:n_dims]): row[n_dims:] for row in truth_rows}
    assert want, f"ground truth for {case.id} is empty"
    assert set(got) == set(want), f"{case.id}: segments differ: {set(got) ^ set(want)}"
    for k, vals in want.items():
        for metric, w, g in zip(case.metrics, vals, got[k], strict=True):
            assert _close(g, w), f"{case.id} {k} {metric}: engine {g} vs truth {w} ({case.why})"
    # Filters are visible, never hidden (spec §47).
    labels = " | ".join(compiled.filters_applied)
    if any(m in ("revenue", "orders", "units", "aov", "cogs") for m in case.metrics):
        assert "cancelled" in labels
    for f in case.filters:
        assert f["dimension"] in labels


def test_every_metric_compiles_for_a_month(store: Any, model: Any) -> None:
    from analystos_engine.semantic.compiler import MetricQuery, run_metric_query
    from analystos_engine.types import TimeWindow

    for m in model.metrics:
        _, res = run_metric_query(
            store, model, MetricQuery(metrics=[m.id], time=TimeWindow(start=D(2026, 6, 1), end=D(2026, 7, 1)))
        )
        assert res.row_count == 1, m.id
        assert res.rows[0][0] is not None, f"{m.id} returned NULL for June 2026"


def test_grain_violation_is_refused(model: Any) -> None:
    """Customers are counted on orders; grouping by a line-level attribute would double count."""
    from analystos_engine.semantic.compiler import GrainError, MetricQuery
    from analystos_engine.semantic.compiler import compile as compile_query
    from analystos_engine.types import TimeWindow

    with pytest.raises(GrainError):
        compile_query(
            model,
            MetricQuery(
                metrics=["active_customers"],
                dimensions=["category"],
                time=TimeWindow(start=D(2026, 8, 1), end=D(2026, 9, 1)),
            ),
        )


def test_unapproved_relationship_is_never_used(model: Any) -> None:
    """returns -> orders is unapproved (padded IDs); returns must reach branch directly."""
    from analystos_engine.semantic.compiler import MetricQuery
    from analystos_engine.semantic.compiler import compile as compile_query
    from analystos_engine.types import TimeWindow

    compiled = compile_query(
        model,
        MetricQuery(
            metrics=["returns_amount"],
            dimensions=["branch"],
            time=TimeWindow(start=D(2026, 6, 1), end=D(2026, 7, 1)),
        ),
    )
    assert '"orders"' not in compiled.sql


def test_mutating_sql_never_reaches_the_store(store: Any) -> None:
    from analystos_engine.sqlsafety import UnsafeSQLError

    for sql in (
        "DELETE FROM orders",
        "UPDATE order_lines SET net_amount = 0",
        "DROP TABLE customers",
        "SELECT 1; DELETE FROM orders",
        "COPY orders TO '/tmp/x.csv'",
        "ATTACH '/tmp/other.duckdb'",
    ):
        with pytest.raises(UnsafeSQLError):
            store.execute_read(sql)
    assert store.scalar("SELECT count(*) FROM orders") > 100_000
